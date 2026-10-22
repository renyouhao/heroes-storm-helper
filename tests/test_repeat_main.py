"""主窗连按模式联动用可控服务，不安装钩子或发送输入。"""
from types import SimpleNamespace
import unittest

from hots_chat.ui.main_window import MainWindow
from hots_chat.window_settings import WindowSettings


class RootQueue:
    def __init__(self):self.callbacks = {}; self.serial = 0
    def after(self, delay, callback):
        self.serial += 1; self.callbacks[self.serial] = callback; return self.serial
    def after_cancel(self, token):self.callbacks.pop(token, None)
    def next(self):
        token = next(iter(self.callbacks)); self.callbacks.pop(token)()


class ActionService:
    def __init__(self, name, calls):
        self.name, self.calls = name, calls
        self.active = False; self.hold_on_cancel = False
    def prepare(self, *args):
        self.calls.append((self.name, 'prepare', args)); self.active = True; return True
    def cancel(self):
        self.calls.append((self.name, 'cancel'))
        if not self.hold_on_cancel:self.active = False


class RepeatMainTests(unittest.TestCase):
    def window(self):
        w = MainWindow.__new__(MainWindow)
        self.assertTrue(hasattr(w, 'prepare_repeat'), '需要主窗的连按准备入口')
        calls = []; w.closed = False; w.root = RootQueue(); w.settings = WindowSettings()
        w.loot_helper = ActionService('loot', calls); w.repeat_helper = ActionService('repeat', calls)
        w._loot_active = w._repeat_active = False
        w._action_revision = 0; w._action_timer = None; w._pending_action = None
        w.quick_input = SimpleNamespace(_visible=False, cancel=lambda:calls.append(('quick', 'cancel')),
                                        open=lambda:calls.append(('quick', 'open')))
        states = []
        w.update_loot_status = lambda status:states.append(('loot', status.state, status.detail))
        w.update_repeat_status = lambda status:states.append(('repeat', status.state, status.detail))
        return w, calls, states

    def test_prepare_repeat_cancels_loot_then_starts_with_saved_binding(self):
        w, calls, states = self.window(); w.settings.repeat_hotkey = 'F9'
        w.loot_helper.active = True; w.prepare_repeat()
        self.assertFalse(w.loot_helper.active)
        self.assertTrue(w.repeat_helper.active)
        self.assertIn(('repeat', 'prepare', ('F9',)), calls)
        self.assertLess(calls.index(('loot', 'cancel')), calls.index(('repeat', 'prepare', ('F9',))))

    def test_switch_waits_for_previous_native_thread_to_fully_exit(self):
        w, calls, states = self.window()
        w.loot_helper.active = w.loot_helper.hold_on_cancel = True; w.prepare_repeat()
        self.assertFalse(w.repeat_helper.active)
        self.assertEqual(states[-1][0:2], ('repeat', 'preparing'))
        self.assertEqual(len(w.root.callbacks), 1)
        w.loot_helper.active = False; w.root.next()
        self.assertTrue(w.repeat_helper.active)
        self.assertEqual(len(w.root.callbacks), 0)

    def test_cancel_during_switch_cannot_later_revive_repeat(self):
        w, calls, states = self.window()
        w.loot_helper.active = w.loot_helper.hold_on_cancel = True; w.prepare_repeat()
        pending = list(w.root.callbacks.values())[0]; w.cancel_repeat()
        w.loot_helper.active = False; pending()
        self.assertFalse(w.repeat_helper.active)
        self.assertFalse(w.root.callbacks)

    def test_newer_preparation_supersedes_the_previous_delayed_request(self):
        w, calls, states = self.window()
        w.loot_helper.active = w.loot_helper.hold_on_cancel = True; w.prepare_repeat()
        old = list(w.root.callbacks.values())[0]
        w.prepare_loot(); w.loot_helper.active = False
        old(); self.assertFalse(w.repeat_helper.active)
        w.root.next(); self.assertTrue(w.loot_helper.active)
        self.assertFalse(w.repeat_helper.active)

    def test_never_starts_if_previous_service_cannot_release(self):
        w, calls, states = self.window()
        w.loot_helper.active = w.loot_helper.hold_on_cancel = True; w.prepare_repeat()
        for _ in range(160):
            if not w.root.callbacks:break
            w.root.next()
        self.assertFalse(w.repeat_helper.active)
        self.assertEqual(states[-1][0:2], ('repeat', 'error'))
        self.assertFalse(w.root.callbacks)

    def test_preparing_loot_ends_repeat_before_starting(self):
        w, calls, states = self.window(); w.repeat_helper.active = True
        w.prepare_loot()
        self.assertFalse(w.repeat_helper.active); self.assertTrue(w.loot_helper.active)
        self.assertIn(('loot', 'prepare', ()), calls)

    def test_quick_input_cancels_both_modes_and_pending_start(self):
        w, calls, states = self.window()
        w.loot_helper.active = w.loot_helper.hold_on_cancel = True; w.prepare_repeat()
        pending = list(w.root.callbacks.values())[0]; w.open_quick_input()
        w.loot_helper.active = False; pending()
        self.assertFalse(w.repeat_helper.active)
        self.assertIn(('quick', 'open'), calls)

    def test_status_overlay_failure_cancels_repeat_and_exposes_error(self):
        w, calls, states = self.window(); w.repeat_helper.active = True
        w._repeat_active = True
        w.status_var = SimpleNamespace(set=lambda text:calls.append(('status', text)))
        w.overlay = SimpleNamespace(set_input_active=lambda value:calls.append(('overlay', value)))
        seen = []
        def update(state, detail=''):
            seen.append(state)
            if state == 'ready':raise OSError('offline overlay failure')
        w.repeat_panel = SimpleNamespace(update_status=update)
        MainWindow.update_repeat_status(w, SimpleNamespace(state='ready', detail='', generation=1))
        self.assertFalse(w.repeat_helper.active); self.assertFalse(w._repeat_active)
        self.assertEqual(seen, ['ready', 'error'])
        self.assertIn(('overlay', False), calls)

    def test_close_releases_both_services_even_if_pending_timer_cancel_fails(self):
        w = MainWindow.__new__(MainWindow); cleaned = []
        w.closed = False; w._action_timer = 'action'; w._pending_action = 'repeat'
        w._action_revision = 1; w.timer = 'pump'; w.start_timer = 'start'
        def cancel(token):
            if token == 'action':raise OSError('destroyed timer')
        w.root = SimpleNamespace(after_cancel=cancel, destroy=lambda:cleaned.append('root'))
        for name in ('loot_helper', 'loot_panel', 'repeat_helper', 'repeat_panel'):
            setattr(w, name, SimpleNamespace(close=lambda name=name:cleaned.append(name)))
        w.hotkeys = SimpleNamespace(stop=lambda:None)
        w.quick_input = SimpleNamespace(close=lambda:None)
        w.overlay = SimpleNamespace(close=lambda:None)
        w.controller = SimpleNamespace(close=lambda:cleaned.append('controller'))
        w.save_settings = lambda:None
        try:w.close()
        except OSError:pass
        self.assertEqual(cleaned, ['loot_helper', 'loot_panel', 'repeat_helper', 'repeat_panel', 'controller', 'root'])


if __name__ == '__main__':unittest.main()
