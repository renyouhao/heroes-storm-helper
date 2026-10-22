"""连按页与真实主窗的隔离联动；不运行原生服务、游戏或云。"""
from types import SimpleNamespace
import unittest

from tests import test_adaptive_display_flow as adaptive
from hots_chat.ui.settings_dialog import SettingsDialog


class RepeatUiFlowTests(unittest.TestCase):
    def setUp(self):
        fixture = adaptive.AdaptiveDisplayFlowTests(methodName='runTest')
        self.context = fixture.app()
        self.root, self.window, self.settings, self.screen, _ = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def test_fourth_page_is_discoverable_and_navigation_keeps_history_and_draft(self):
        w = self.window
        self.assertIn('repeat', w.navigation)
        self.assertFalse(w.repeat_helper.active)
        self.assertIsNone(w.repeat_panel.overlay_window)
        w.input.insert('1.0', '等我集合'); w.input.edit_modified(False)
        history = list(w.vm.messages)
        w.navigation['repeat'].invoke(); self.root.update()
        self.assertEqual(w.section_title.get(), '动作连按')
        self.assertEqual(w.notebook.select(), str(w.repeat_panel))
        self.assertFalse(w.reply_panel.winfo_ismapped())
        self.assertEqual(w.notebook.tab(3, 'text'), '动作连按')
        w.navigation['chat'].invoke(); self.root.update()
        self.assertTrue(w.reply_panel.winfo_ismapped())
        self.assertEqual(w.input.get('1.0', 'end-1c'), '等我集合')
        self.assertEqual(list(w.vm.messages), history)

    def test_start_listener_and_requested_hint_confirm_saved_hotkey_without_prepare(self):
        w = self.window; self.settings.repeat_hotkey = 'F9'
        queue, calls = [], []
        def listen(binding):calls.append(('listen', binding)); return True
        def drain():
            rows = queue[:]; queue.clear(); return rows
        def confirm(generation):
            self.assertTrue(w.repeat_panel._overlay_visible)
            self.assertEqual(w.repeat_panel._overlay.canvas.itemcget('hint', 'text'), '按 F9 取消')
            calls.append(('confirmed', generation)); return True
        w.repeat_helper = SimpleNamespace(active=False, listening=True, start_listening=listen,
             cancel=lambda:calls.append(('cancel',)), close=lambda:None,
             drain_status=drain, accepts_status=lambda status:True,
             confirm_ready=confirm)
        from tests.test_repeat_panel import FakeNative
        w.repeat_panel._native = FakeNative()
        w._repeat_hotkey_enabled = True; w._start_repeat_listener()
        self.assertEqual(calls, [('listen', 'F9')])
        self.assertEqual(w.repeat_panel.state, 'idle')
        queue.append(SimpleNamespace(state='requested', detail='', generation=7)); w.pump()
        self.assertIn(('confirmed', 7), calls)
        self.assertEqual(w.repeat_panel.state, 'requested'); self.assertTrue(w._repeat_active)
        queue.append(SimpleNamespace(state='idle', detail='', generation=7)); w.pump()
        self.assertFalse(w._repeat_active)
        self.assertFalse(w.repeat_panel._overlay_visible)

    def test_changing_hotkey_ends_current_session_and_updates_hint(self):
        w = self.window; cancelled = []; bindings = []
        w.repeat_helper = SimpleNamespace(active=True, cancel=lambda:cancelled.append(True),
                                          start_listening=lambda binding:bindings.append(binding) or True,
                                          close=lambda:None, drain_status=lambda:[])
        w._repeat_hotkey_enabled = w._repeat_listener_started = True
        w._repeat_active = True
        self.settings.repeat_hotkey = 'F10'; w.apply_settings()
        self.assertEqual(cancelled, [True]); self.assertFalse(w._repeat_active)
        self.assertEqual(w._repeat_binding, 'F10')
        self.assertEqual(bindings, ['F10'])
        self.assertTrue(any('F10' in label.cget('text') for label, _ in w.repeat_panel._hotkey_labels))

    def test_repeat_and_quick_input_share_subtitle_suppression(self):
        w = self.window; seen = []
        w.overlay.set_input_active = seen.append
        w.repeat_panel.update_status = lambda *args:None
        w.repeat_helper = SimpleNamespace(confirm_ready=lambda generation:None, close=lambda:None,
                                          cancel=lambda:None, active=False, drain_status=lambda:[])
        w.update_repeat_status(SimpleNamespace(state='running', detail='', generation=1))
        self.assertTrue(seen[-1])
        w.quick_input._visible = True
        w.update_repeat_status(SimpleNamespace(state='idle', detail='', generation=1))
        self.assertTrue(seen[-1])
        w.quick_input._visible = False; w.quick_visibility_changed(False)
        self.assertFalse(seen[-1])

    def test_settings_exposes_function_key_choices_and_changes_remain_draft_until_save(self):
        dialog = SettingsDialog(self.window); self.addCleanup(lambda:dialog.cancel() if dialog.dialog.winfo_exists() else None)
        entry = dialog._entries['repeat_hotkey']
        self.assertEqual(tuple(entry['values']), tuple(f'F{n}' for n in range(1, 13)))
        dialog.values['repeat_hotkey'].set('F9')
        self.assertEqual(self.settings.repeat_hotkey, 'F8')
        dialog.cancel()
        self.assertEqual(self.settings.repeat_hotkey, 'F8')


if __name__ == '__main__':unittest.main()
