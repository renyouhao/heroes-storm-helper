"""主窗设置、发送互斥的离线接线验证。"""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from hots_chat.ui.main_window import MainWindow
from hots_chat.window_settings import WindowSettings


class TalentMainTests(unittest.TestCase):
    def window(self):
        w = MainWindow.__new__(MainWindow)
        w.closed = False
        w.settings = WindowSettings()
        w._talent_runtime_enabled = True
        w.talent_helper = SimpleNamespace(set_enabled=Mock(return_value=True), set_suspended=Mock())
        w.talent_panel = SimpleNamespace(apply_settings=Mock(), update_status=Mock())
        w._layout_metrics = None
        w.save_settings = Mock()
        w.quick_input = SimpleNamespace(_visible=False)
        w.loot_helper = SimpleNamespace(active=False)
        w._loot_active = w._repeat_active = False
        w._pending_action = None
        return w

    def test_failed_save_does_not_enable_service(self):
        w = self.window()
        w.save_settings.side_effect = OSError('只读偏好')
        with self.assertRaises(OSError):w.apply_talent_config(True)
        self.assertFalse(w.settings.talent_enabled)
        w.talent_helper.set_enabled.assert_not_called()

    def test_failed_save_cannot_prevent_turning_feature_off(self):
        w = self.window()
        w.settings.talent_enabled = True
        w.save_settings.side_effect = OSError('只读偏好')
        with self.assertRaises(OSError):w.apply_talent_config(False)
        self.assertFalse(w.settings.talent_enabled)
        w.talent_helper.set_enabled.assert_called_with(False)

    def test_saved_opt_in_enables_and_opt_out_disables_immediately(self):
        w = self.window()
        w.apply_talent_config(True)
        w.talent_helper.set_enabled.assert_called_with(True)
        self.assertTrue(w.settings.talent_enabled)
        w.apply_talent_config(False)
        w.talent_helper.set_enabled.assert_called_with(False)

    def test_quick_input_repeat_loot_and_pending_operation_suspend(self):
        w = self.window()
        w._sync_talent_gate()
        w.talent_helper.set_suspended.assert_called_with(False)
        for field in ('_loot_active', '_repeat_active', '_pending_action'):
            setattr(w, field, True)
            w._sync_talent_gate()
            w.talent_helper.set_suspended.assert_called_with(True)
            setattr(w, field, False)
        w.quick_input._visible = True
        w._sync_talent_gate()
        w.talent_helper.set_suspended.assert_called_with(True)

    def test_demo_never_enables_backend_even_if_preference_enabled(self):
        w = self.window()
        w._talent_runtime_enabled = False
        w.apply_talent_config(True)
        w.talent_helper.set_enabled.assert_called_with(False)
        self.assertIn('演示', w.talent_panel.update_status.call_args.args[1])

    def test_quick_close_callback_cannot_reopen_pending_action_gate(self):
        w = self.window()
        w.overlay = SimpleNamespace(set_input_active=Mock())
        for attr in ('pending', 'worker'):
            w._pending_action = 'loot' if attr == 'pending' else None
            w.loot_helper.active = attr == 'worker'
            w.quick_visibility_changed(False)
            w.talent_helper.set_suspended.assert_called_with(True)

    def test_close_fences_talent_before_account_cleanup(self):
        w = self.window()
        def close_account():
            w.talent_helper.set_enabled.assert_called_with(False, wait=False)
            raise RuntimeError('stop after checking close ordering')
        w.controller = SimpleNamespace(account_setup=SimpleNamespace(close=close_account))
        with self.assertRaises(RuntimeError):w.close()
