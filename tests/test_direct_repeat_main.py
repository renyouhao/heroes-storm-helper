"""F8直启与主窗的离线联动：状态浮条先显示，再开放发送门。"""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hots_chat.repeat import RepeatStatus
from hots_chat.ui.main_window import MainWindow
from tests.test_repeat_main import RootQueue, ActionService


class DirectRepeatMainTests(unittest.TestCase):
    def test_product_entry_enables_listener_and_demo_entry_keeps_it_off(self):
        from hots_chat.app import main
        from hots_chat.window_settings import WindowSettings
        for arguments, enabled in (([], True), (['--demo'], False)):
            with self.subTest(arguments=arguments):
                controller = SimpleNamespace(close=Mock())
                mutex = SimpleNamespace(acquire=lambda:True, close=Mock())
                root = SimpleNamespace(mainloop=Mock())
                with patch('hots_chat.app.SingleInstance', return_value=mutex), \
                     patch('hots_chat.app.build_controller', return_value=controller), \
                     patch('hots_chat.window_settings.WindowSettings.load', return_value=WindowSettings()), \
                     patch('hots_chat.display_runtime.enable_process_dpi_awareness'), \
                     patch('hots_chat.display_runtime.TkDisplayRuntime'), \
                     patch('tkinter.Tk', return_value=root), \
                     patch('hots_chat.ui.main_window.create_app') as create:
                    self.assertEqual(main(arguments), 0)
                self.assertEqual(create.call_args.kwargs.get('enable_repeat_hotkey'), enabled)
                root.mainloop.assert_called_once_with()

    def window(self):
        calls = []
        w = MainWindow.__new__(MainWindow)
        w.root = RootQueue(); w.closed = False
        w.settings = SimpleNamespace(repeat_hotkey='F8')
        w._repeat_hotkey_enabled = True; w._repeat_listener_started = False
        w._action_revision = 0; w._action_timer = None; w._pending_action = None
        w._loot_active = w._repeat_active = False
        w.loot_helper = ActionService('loot', calls)
        current = [RepeatStatus('requested', generation=1)]
        w.repeat_helper = SimpleNamespace(
            active=True, listening=True,
            start_listening=lambda binding:calls.append(('listen', binding)) or True,
            cancel=lambda:calls.append(('repeat', 'cancel')),
            accepts_status=lambda status:status == current[0],
            confirm_ready=lambda generation:calls.append(('confirmed', generation)) or True)
        w.repeat_panel = SimpleNamespace(update_status=lambda state, detail='':calls.append(('panel', state)))
        w.quick_input = SimpleNamespace(_visible=False)
        w.overlay = SimpleNamespace(set_input_active=lambda active:calls.append(('subtitle', active)))
        w.status_var = SimpleNamespace(set=lambda text:calls.append(('status', text)))
        w.update_loot_status = lambda status:None
        w.controller = SimpleNamespace(start_receiving=lambda:calls.append(('source', 'start')))
        return w, calls, current

    def test_formal_start_listens_with_saved_key_without_prepare_or_sending(self):
        w, calls, _ = self.window(); w.settings.repeat_hotkey = 'F10'
        w.start()
        self.assertIn(('listen', 'F10'), calls)
        self.assertIn(('source', 'start'), calls)
        self.assertFalse(w._repeat_active)
        self.assertFalse(any(call[0] == 'confirmed' for call in calls))

    def test_demo_start_does_not_install_native_repeat_listener(self):
        w, calls, _ = self.window(); w._repeat_hotkey_enabled = False
        w.start()
        self.assertEqual(calls, [('source', 'start')])

    def test_direct_request_displays_bottom_hint_before_confirming(self):
        w, calls, current = self.window()
        w.update_repeat_status(current[0])
        self.assertLess(calls.index(('panel', 'requested')), calls.index(('confirmed', 1)))
        self.assertTrue(w._repeat_active)
        self.assertIn(('subtitle', True), calls)

    def test_direct_request_cancels_loot_and_waits_for_its_thread(self):
        w, calls, current = self.window()
        w.loot_helper.active = w.loot_helper.hold_on_cancel = True
        w.update_repeat_status(current[0])
        self.assertIn(('loot', 'cancel'), calls)
        self.assertNotIn(('confirmed', 1), calls)
        self.assertEqual(w._pending_action, 'repeat')
        w.loot_helper.active = False; w.root.next()
        self.assertIn(('confirmed', 1), calls)
        self.assertIsNone(w._pending_action)

    def test_second_f8_cancelled_request_cannot_be_confirmed_by_delayed_callback(self):
        w, calls, current = self.window()
        w.loot_helper.active = w.loot_helper.hold_on_cancel = True
        w.update_repeat_status(current[0]); pending = next(iter(w.root.callbacks.values()))
        current[0] = RepeatStatus('idle', generation=1)
        w.update_repeat_status(current[0]); w.loot_helper.active = False; pending()
        self.assertNotIn(('confirmed', 1), calls)
        self.assertFalse(w._repeat_active)
        self.assertFalse(w.root.callbacks)

    def test_new_request_supersedes_old_delayed_confirmation(self):
        w, calls, current = self.window()
        w.loot_helper.active = w.loot_helper.hold_on_cancel = True
        w.update_repeat_status(current[0]); old = next(iter(w.root.callbacks.values()))
        current[0] = RepeatStatus('requested', generation=2)
        w.update_repeat_status(current[0]); w.loot_helper.active = False
        old(); w.root.next()
        self.assertNotIn(('confirmed', 1), calls)
        self.assertIn(('confirmed', 2), calls)

    def test_failed_overlay_never_confirms_or_sends(self):
        w, calls, current = self.window()
        def render(state, detail=''):
            if state == 'requested':raise OSError('fictional display failure')
            calls.append(('panel', state))
        w.repeat_panel.update_status = render
        w.update_repeat_status(current[0])
        self.assertIn(('repeat', 'cancel'), calls)
        self.assertNotIn(('confirmed', 1), calls)
        self.assertFalse(w._repeat_active)

    def test_quick_input_blocks_a_queued_start(self):
        w, calls, current = self.window(); w.quick_input._visible = True
        w.update_repeat_status(current[0])
        self.assertIn(('repeat', 'cancel'), calls)
        self.assertNotIn(('confirmed', 1), calls)
        self.assertFalse(w._repeat_active)

    def test_loot_release_timeout_cancels_request_and_exposes_error(self):
        w, calls, current = self.window()
        w.loot_helper.active = w.loot_helper.hold_on_cancel = True
        w.update_repeat_status(current[0])
        for _ in range(160):
            if not w.root.callbacks:break
            w.root.next()
        self.assertIn(('repeat', 'cancel'), calls)
        self.assertIn(('panel', 'error'), calls)
        self.assertNotIn(('confirmed', 1), calls)


if __name__ == '__main__':unittest.main()
