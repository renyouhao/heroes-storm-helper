"""动作页配置的实际控件与主窗接线；无游戏、无真实按键。"""
import unittest
from types import SimpleNamespace

from hots_chat.ui.repeat_panel import RepeatPanel
from hots_chat.window_settings import WindowSettings
from tests import test_repeat_panel as panels
from tests import test_repeat_ui_flow as flows


class RepeatActionPanelTests(unittest.TestCase):
    setUp = panels.RepeatPanelTests.setUp
    destroy_root = panels.RepeatPanelTests.destroy_root
    def action_panel(self):
        calls = []
        def apply(action, interval_ms):
            calls.append((action, interval_ms))
            self.settings.repeat_action = action
            if action == 'S': self.settings.repeat_s_interval_ms = interval_ms
            else: self.settings.repeat_interval_ms = interval_ms
        panel = RepeatPanel(self.root, on_prepare=self.prepare, on_cancel=self.cancel,
                            on_apply=apply, settings=self.settings, metrics=self.metrics,
                            native=self.native)
        panel.place(x=0, y=0, width=540, height=380)
        self.addCleanup(panel.close)
        return panel, calls

    def test_draft_action_and_interval_only_apply_when_saved(self):
        panel, calls = self.action_panel()
        self.assertEqual(tuple(panel.action_combo['values']), ('S', 'Y+S'))
        self.assertEqual(panel.interval_var.get(), '20')
        self.assertFalse(panel.interval_entry.instate(['disabled']))
        panel.action_var.set('Y+S')
        panel.action_combo.event_generate('<<ComboboxSelected>>')
        self.assertFalse(panel.interval_entry.instate(['disabled']))
        panel.interval_var.set('350')
        self.assertEqual(calls, [])
        self.assertEqual(self.settings.repeat_action, 'S')
        panel.apply_button.invoke()
        self.assertEqual(calls, [('YS', 350)])
        self.assertIn('已保存', panel.config_message.get())
        self.assertIn('F8', panel.config_message.get())

    def test_invalid_interval_stays_visible_without_saving_or_clamping(self):
        panel, calls = self.action_panel()
        panel.action_var.set('Y+S')
        for value in ('', 'abc', '19', '5001', '200.5', '2e2'):
            with self.subTest(value=value):
                panel.interval_var.set(value)
                panel.apply_button.invoke()
                self.assertEqual(calls, [])
                self.assertEqual(panel.interval_var.get(), value)
                self.assertTrue(panel.config_message.get())
                self.assertEqual(self.settings.repeat_interval_ms, 20)

    def test_save_error_keeps_draft_and_does_not_claim_saved(self):
        panel, _ = self.action_panel()
        def fail(*args): raise OSError('fictional preferences unavailable')
        panel._on_apply = fail
        panel.action_var.set('Y+S'); panel.interval_var.set('250')
        panel.apply_button.invoke()
        self.assertIn('fictional preferences unavailable', panel.config_message.get())
        self.assertNotIn('已保存', panel.config_message.get())
        self.assertEqual((self.settings.repeat_action, self.settings.repeat_interval_ms), ('S', 20))
        self.assertEqual(panel.interval_var.get(), '250')

    def test_ys_status_and_bottom_overlay_identify_the_running_action(self):
        panel, _ = self.action_panel()
        self.settings.repeat_action, self.settings.repeat_interval_ms = 'YS', 375
        panel.apply_settings(self.settings, self.metrics)
        panel.update_status('running')
        self.assertIn('Y+S', panel._overlay.canvas.itemcget('title', 'text'))
        self.assertEqual(panel._overlay.canvas.itemcget('hint', 'text'), '按 F8 取消')
        self.assertIn('375', panel.status_detail.get())
        self.assertIn('Y', panel.status_detail.get())
        self.assertIn('S', panel.status_detail.get())
        self.assertNotIn('20 毫秒', panel.status_detail.get())

    def test_s_interval_is_editable_and_drafts_survive_action_switches(self):
        panel, calls = self.action_panel()
        panel.interval_var.set('90')
        panel.action_var.set('Y+S'); panel.action_combo.event_generate('<<ComboboxSelected>>')
        self.assertEqual(panel.interval_var.get(), '20')
        panel.interval_var.set('260')
        panel.action_var.set('S'); panel.action_combo.event_generate('<<ComboboxSelected>>')
        self.assertEqual(panel.interval_var.get(), '90')
        panel.apply_button.invoke()
        self.assertEqual(calls, [('S', 90)])
        panel.update_status('running')
        self.assertIn('90 毫秒', panel.status_detail.get())
        self.assertEqual(self.settings.repeat_interval_ms, 20)


class RepeatActionMainTests(unittest.TestCase):
    setUp = flows.RepeatUiFlowTests.setUp

    def tearDown(self):
        # 保存时会刷新控件样式，销毁离线根窗口前排空 Tk 的空闲样式事件。
        self.root.update_idletasks()
    def test_apply_cancels_current_action_saves_preferences_then_waits_for_f8(self):
        w = self.window
        calls = []
        w.repeat_helper.close()
        w.repeat_helper = SimpleNamespace(active=True, listening=True,
            cancel=lambda: calls.append(('cancel',)), close=lambda: None, drain_status=lambda: [],
            start_listening=lambda binding, **options: calls.append(('listen', binding, options)) or True)
        w._repeat_hotkey_enabled = w._repeat_listener_started = True
        w._repeat_active = True
        panel = w.repeat_panel
        panel.action_var.set('Y+S'); panel.interval_var.set('315')
        panel.apply_button.invoke()
        self.assertEqual(calls, [('cancel',), ('listen', 'F8', {'action': 'YS', 'interval_ms': 315})])
        self.assertFalse(w._repeat_active)
        self.assertEqual(panel.state, 'idle')
        saved = WindowSettings.load(w.controller.preferences_path)
        self.assertEqual((saved.repeat_action, saved.repeat_interval_ms), ('YS', 315))
        self.assertFalse(panel._overlay_visible)
        panel.action_var.set('S'); panel.action_combo.event_generate('<<ComboboxSelected>>')
        panel.apply_button.invoke()
        self.assertEqual(calls[-2:], [('cancel',), ('listen', 'F8', {})])

    def test_apply_save_failure_rolls_back_effective_config_after_cancelling(self):
        w = self.window
        calls = []
        w.repeat_helper.close()
        w.repeat_helper = SimpleNamespace(active=True, cancel=lambda: calls.append('cancel'),
            close=lambda: None, drain_status=lambda: [], start_listening=lambda *args, **kw: calls.append('listen'))
        w._repeat_hotkey_enabled = w._repeat_listener_started = True
        def fail(): raise OSError('fictional disk full')
        w.save_settings = fail
        panel = w.repeat_panel
        panel.action_var.set('Y+S'); panel.interval_var.set('250')
        panel.apply_button.invoke()
        self.assertEqual(calls, ['cancel'])
        self.assertEqual((w.settings.repeat_action, w.settings.repeat_interval_ms), ('S', 20))
        self.assertFalse(w._repeat_active)
        self.assertNotIn('已保存', panel.config_message.get())

    def test_custom_s_interval_is_saved_and_forwarded_to_listener(self):
        w = self.window
        calls = []
        w.repeat_helper.close()
        w.repeat_helper = SimpleNamespace(active=False, cancel=lambda: None, close=lambda: None,
            drain_status=lambda: [], start_listening=lambda binding, **options:
                calls.append((binding, options)) or True)
        w._repeat_hotkey_enabled = w._repeat_listener_started = True
        w.repeat_panel.interval_var.set('85')
        w.repeat_panel.apply_button.invoke()
        saved = WindowSettings.load(w.controller.preferences_path)
        self.assertEqual(saved.repeat_s_interval_ms, 85)
        self.assertEqual(saved.repeat_interval_ms, 20)
        self.assertEqual(calls, [('F8', {'action': 'S', 'interval_ms': 85})])

    def test_saving_same_config_still_stops_current_action(self):
        w=self.window
        calls=[]
        w.repeat_helper.close()
        w.repeat_helper=SimpleNamespace(active=True,listening=True,
            cancel=lambda:calls.append('cancel'),close=lambda:None,drain_status=lambda:[],
            start_listening=lambda *args,**kwargs:calls.append('listen') or True)
        w._repeat_hotkey_enabled=w._repeat_listener_started=True
        w._repeat_active=True
        w.repeat_panel.apply_button.invoke()
        self.assertEqual(calls,['cancel','listen'])
        self.assertFalse(w._repeat_active)

    def test_external_config_refresh_cancels_old_action_and_restarts_listener_with_ys(self):
        w = self.window
        calls = []
        w.repeat_helper.close()
        w.repeat_helper = SimpleNamespace(active=True, cancel=lambda: calls.append(('cancel',)),
            close=lambda: None, drain_status=lambda: [],
            start_listening=lambda binding, **options: calls.append(('listen', binding, options)) or True)
        w._repeat_hotkey_enabled = w._repeat_listener_started = True
        w._repeat_active = True
        w.settings.repeat_action, w.settings.repeat_interval_ms = 'YS', 250
        w.apply_settings()
        self.assertEqual(calls, [('cancel',), ('listen', 'F8', {'action': 'YS', 'interval_ms': 250})])
        self.assertFalse(w._repeat_active)


if __name__ == '__main__': unittest.main()
