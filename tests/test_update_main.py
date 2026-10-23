from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hots_chat.ui.main_window import MainWindow
from hots_chat.update_service import UpdateStatus
from hots_chat.app import main


class UpdateMainTests(unittest.TestCase):
    def window(self):
        window = MainWindow.__new__(MainWindow)
        window.closed = False; window._update_installing = False
        window.update_service = SimpleNamespace(install=Mock(return_value=True))
        window.update_dialog = SimpleNamespace(detail=SimpleNamespace(set=Mock()))
        window.cancel_loot = Mock(); window.cancel_repeat = Mock()
        window._sync_talent_gate = Mock()
        window.quick_input = SimpleNamespace(_visible=True,cancel=Mock())
        return window

    def test_install_fences_actions_before_background_prepare(self):
        window = self.window()
        def start(operation):
            self.assertTrue(window._update_installing)
            window.cancel_loot.assert_called_once()
            window.cancel_repeat.assert_called_once()
            window.quick_input.cancel.assert_called_once()
            window._sync_talent_gate.assert_called()
            return True
        window.update_service.install.side_effect = start
        with patch('sys.frozen',True,create=True):window.install_update()

    def test_source_mode_never_prepares_or_exits_application(self):
        window = self.window()
        with patch('sys.frozen',False,create=True):window.install_update()
        window.update_service.install.assert_not_called()
        self.assertFalse(window._update_installing)
        self.assertIn('源码',window.update_dialog.detail.set.call_args.args[0])

    def test_update_helper_mode_does_not_create_main_ui_or_mutex(self):
        with patch('hots_chat.update_install.apply_update',return_value=0) as apply, \
                patch('hots_chat.app.SingleInstance',side_effect=AssertionError('must not take app mutex')):
            self.assertEqual(main(['--apply-update','fake-plan.json']),0)
        self.assertEqual(str(apply.call_args.args[0]),'fake-plan.json')

    def test_pending_update_blocks_new_input_actions(self):
        window = self.window(); window._update_installing = True
        window._prepare_action('loot')
        window.open_quick_input()
        window.cancel_loot.assert_not_called()
        window.quick_input.cancel.assert_not_called()
