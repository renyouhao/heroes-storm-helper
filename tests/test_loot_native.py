"""Win32 contract tests: all operating system calls use inert fake APIs."""
import ctypes
import importlib
import threading
import unittest
from unittest.mock import patch


class Function:
    def __init__(self, implementation):
        self.implementation = implementation

    def __call__(self, *args):
        return self.implementation(*args)


class FakeKernel:
    def __init__(self):
        self.path = r'C:\Games\HeroesOfTheStorm_x64.exe'
        self.opened = []
        self.closed = []
        self.query_ok = True
        self.open_ok = True
        self.during_query = None
        self.OpenProcess = Function(self.open_process)
        self.QueryFullProcessImageNameW = Function(self.query_image)
        self.CloseHandle = Function(lambda handle: self.closed.append(handle) or 1)
        self.GetCurrentThreadId = Function(lambda: 0xABCDEF01)
        self.GetModuleHandleW = Function(lambda name: 0x123456789)

    def open_process(self, access, inherit, pid):
        self.opened.append((access, inherit, pid))
        return 0x123456789 if self.open_ok else 0

    def query_image(self, handle, flags, buffer, length):
        if self.during_query:
            self.during_query()
        if not self.query_ok:
            return 0
        buffer.value = self.path
        ctypes.cast(length, ctypes.POINTER(ctypes.c_uint32))[0] = len(self.path)
        return 1


class FakeUser:
    def __init__(self):
        self.hwnd = 0x234567890
        self.pid = 789
        self.keys = set()
        self.sent = []
        self.send_results = []
        self.hooks = {}
        self.installed = []
        self.unhooked = []
        self.timer = 0x345678901
        self.timer_calls = []
        self.killed = []
        self.posted = []
        self.messages = []
        self.dispatched = []
        self.chain = []
        self.hook_failure = 0
        self.unhook_failure = 0
        self.GetForegroundWindow = Function(lambda: self.hwnd)
        self.GetWindowThreadProcessId = Function(self.window_pid)
        self.GetAsyncKeyState = Function(lambda vk: 0x8000 if vk in self.keys else 0)
        self.SendInput = Function(self.send_input)
        self.SetWindowsHookExW = Function(self.set_hook)
        self.UnhookWindowsHookEx = Function(self.unhook)
        self.CallNextHookEx = Function(self.call_next)
        self.SetTimer = Function(self.set_timer)
        self.KillTimer = Function(lambda hwnd, timer: self.killed.append(timer) or 1)
        self.PeekMessageW = Function(lambda *args: 0)
        self.GetMessageW = Function(self.get_message)
        self.TranslateMessage = Function(lambda message: 1)
        self.DispatchMessageW = Function(self.dispatch)
        self.PostThreadMessageW = Function(self.post_message)

    def window_pid(self, hwnd, pid):
        ctypes.cast(pid, ctypes.POINTER(ctypes.c_uint32))[0] = self.pid
        return 456

    def send_input(self, count, inputs, size):
        self.sent.append((count, size, [
            (inputs[i].type, inputs[i].ki.wVk, inputs[i].ki.dwFlags,
             inputs[i].ki.dwExtraInfo) for i in range(count)
        ]))
        result = self.send_results.pop(0) if self.send_results else count
        if isinstance(result, Exception):
            raise result
        return result

    def set_hook(self, kind, callback, module, thread):
        self.installed.append((kind, module, thread))
        if kind == self.hook_failure:
            return 0
        self.hooks[kind] = callback
        return 0x456789000 + kind

    def unhook(self, handle):
        self.unhooked.append(handle)
        return 0 if handle == self.unhook_failure else 1

    def call_next(self, hook, code, wparam, lparam):
        self.chain.append((code, wparam))
        return 57

    def set_timer(self, hwnd, timer, interval, callback):
        self.timer_calls.append((hwnd, timer, interval, callback))
        return self.timer

    def get_message(self, pointer, hwnd, low, high):
        if not self.messages:
            return 0
        item = self.messages.pop(0)
        if callable(item):
            item()
            return self.get_message(pointer, hwnd, low, high)
        if item == -1:
            return -1
        msg = pointer._obj
        msg.message, msg.wParam = item
        return 1

    def dispatch(self, message):
        self.dispatched.append(message._obj.message)
        return 0

    def post_message(self, thread, message, wparam, lparam):
        self.posted.append((thread, message))
        self.messages.insert(0, lambda: None)
        return 1


class LootNativeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.native = importlib.import_module('hots_chat.loot_native')
        except ModuleNotFoundError:
            self.fail('Win32 loot adapter is not implemented')
        self.user = FakeUser()
        self.kernel = FakeKernel()
        self.backend = self.native.Win32LootBackend(user=self.user, kernel=self.kernel)

    def target(self):
        return self.native.GameTarget(0x234567890, 789)

    def run_backend(self, **callbacks):
        values = dict(on_key=lambda *args: False, on_mouse=lambda: None,
                      on_tick=lambda: True, on_ready=lambda: None,
                      stop_event=threading.Event())
        values.update(callbacks)
        return self.backend.run(**values)

    def key_event(self, message, vk, extra=0, code=0):
        data = self.native.KBDLLHOOKSTRUCT(vk, 0, 0, 0, extra)
        return self.user.hooks[13](code, message, ctypes.addressof(data))

    def test_foreground_requires_real_executable_and_closes_query_handle(self):
        self.assertEqual(self.backend.foreground_target(), self.target())
        self.assertEqual(self.kernel.opened, [(0x1000, False, 789)])
        self.assertEqual(self.kernel.closed, [0x123456789])
        for path in (r'C:\Games\HeroesOfTheStorm_x64.exe.bak',
                     r'C:\Games\not-game.exe', 'HeroesOfTheStorm.exe'):
            self.kernel.path = path
            self.assertIsNone(self.backend.foreground_target())

    def test_inaccessible_process_and_failed_query_never_target_game(self):
        self.kernel.open_ok = False
        self.assertIsNone(self.backend.foreground_target())
        self.assertEqual(self.kernel.closed, [])
        self.kernel.open_ok = True
        self.kernel.query_ok = False
        self.assertIsNone(self.backend.foreground_target())
        self.assertEqual(self.kernel.closed, [0x123456789])

    def test_no_foreground_does_not_open_process(self):
        self.user.hwnd = 0
        self.assertIsNone(self.backend.foreground_target())
        self.assertEqual(self.kernel.opened, [])

    def test_foreground_change_during_query_is_rejected(self):
        self.kernel.during_query = lambda: setattr(self.user, 'hwnd', 222)
        self.assertIsNone(self.backend.foreground_target())
        self.assertEqual(self.kernel.closed, [0x123456789])

    def test_each_lookup_revalidates_reused_process_id(self):
        self.assertEqual(self.backend.foreground_target(), self.target())
        self.kernel.path = r'C:\Windows\unrelated.exe'
        self.assertIsNone(self.backend.foreground_target())
        self.assertEqual(len(self.kernel.closed), 2)

    def test_modifiers_include_control_alt_shift_and_both_windows_keys(self):
        self.assertFalse(self.backend.modifiers_down())
        for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C):
            self.user.keys = {vk}
            self.assertTrue(self.backend.modifiers_down())
        self.user.keys = {0x41}
        self.assertFalse(self.backend.modifiers_down())

    def test_space_down_reads_physical_state_for_preheld_key_guard(self):
        self.assertFalse(self.backend.space_down())
        self.user.keys = {0x20}
        self.assertTrue(self.backend.space_down())

    def test_send_space_never_treats_missing_target_as_authorization(self):
        self.user.hwnd = 0
        self.assertFalse(self.backend.send_space(None))
        self.assertEqual(self.user.sent, [])

    def test_import_never_loads_dll_or_creates_thread(self):
        # 独立命名空间执行导入，避免重载改变现有 ctypes 类型的身份。
        import runpy
        with patch('ctypes.WinDLL', side_effect=AssertionError('DLL load at import')), \
                patch('threading.Thread', side_effect=AssertionError('thread at import')):
            runpy.run_path(self.native.__file__, run_name='loot_native_import_check')

    def test_send_space_has_correct_win64_layout_and_one_marked_pair(self):
        self.assertEqual(ctypes.sizeof(self.native.INPUT), 40)
        self.assertEqual(ctypes.sizeof(self.native.KEYBDINPUT), 24)
        self.assertEqual(ctypes.sizeof(self.native.MOUSEINPUT), 32)
        self.assertTrue(self.backend.send_space(self.target()))
        self.assertEqual(self.user.sent, [(2, 40, [
            (1, 0x20, 0, 0x484F5453), (1, 0x20, 2, 0x484F5453),
        ])])

    def test_send_space_refuses_different_hwnd_or_pid(self):
        for target in (self.native.GameTarget(123, 789),
                       self.native.GameTarget(0x234567890, 999)):
            self.assertFalse(self.backend.send_space(target))
        self.assertEqual(self.user.sent, [])

    def test_partial_or_failed_send_attempts_keyup_and_raises(self):
        for failure in (0, 1, OSError('injected API failure')):
            with self.subTest(failure=failure):
                self.user.sent.clear()
                self.user.send_results = [failure, 1]
                with self.assertRaises(OSError):
                    self.backend.send_space(self.target())
                self.assertEqual(self.user.sent[1], (1, 40, [(1, 0x20, 2, 0x484F5453)]))

    def test_input_failure_is_preserved_when_cleanup_send_also_fails(self):
        self.user.send_results = [OSError('original failure'), OSError('cleanup failure')]
        with self.assertRaisesRegex(OSError, 'original failure'):
            self.backend.send_space(self.target())

    def test_run_installs_then_ready_timer_tick_and_releases_everything(self):
        observed = []
        self.user.messages = [(0x113, self.user.timer)]
        self.run_backend(on_ready=lambda: observed.append(('ready', len(self.user.hooks))),
                         on_tick=lambda: observed.append(('tick',)) or False)
        self.assertEqual(observed, [('ready', 2), ('tick',)])
        self.assertEqual(self.user.installed,
                         [(13, 0x123456789, 0), (14, 0x123456789, 0)])
        self.assertEqual(self.user.timer_calls, [(None, 0, 50, None)])
        self.assertEqual(self.user.killed, [0x345678901])
        self.assertCountEqual(self.user.unhooked, [0x45678900D, 0x45678900E])

    def test_keyboard_forwards_down_up_and_system_keys_and_only_swallows_requested(self):
        observed, results = [], []
        def events():
            for msg, vk, extra, code in (
                (0x100, 0x46, 99, 0), (0x101, 0x46, 99, 0),
                (0x104, 0x20, 0x484F5453, 0), (0x105, 0x20, 0x484F5453, 0),
                (0x100, 0x41, 0, -1), (0x999, 0x41, 0, 0)):
                results.append(self.key_event(msg, vk, extra, code))
        self.user.messages = [events]
        self.run_backend(on_key=lambda *args: observed.append(args) or args[0] == 0x46)
        self.assertEqual(observed, [(0x46, True, 99), (0x46, False, 99),
                                    (0x20, True, 0x484F5453), (0x20, False, 0x484F5453)])
        self.assertEqual(results, [1, 1, 57, 57, 57, 57])

    def test_mouse_interrupts_buttons_and_both_wheels_but_never_movement_or_up(self):
        observed, results = [], []
        def events():
            for msg in (0x200, 0x201, 0x204, 0x207, 0x20B, 0x20A, 0x20E, 0x202):
                results.append(self.user.hooks[14](0, msg, 0))
            results.append(self.user.hooks[14](-1, 0x201, 0))
        self.user.messages = [events]
        self.run_backend(on_mouse=lambda: observed.append('interrupt'))
        self.assertEqual(observed, ['interrupt'] * 6)
        self.assertEqual(results, [57] * 9)

    def test_stop_before_run_has_no_native_side_effects(self):
        stopped = threading.Event()
        stopped.set()
        self.run_backend(stop_event=stopped, on_ready=lambda: self.fail('must not be ready'))
        self.assertEqual(self.user.installed, [])
        self.assertEqual(self.user.timer_calls, [])

    def test_stop_from_ready_wakes_pump_and_skips_tick(self):
        self.user.messages = [(0x113, self.user.timer)]
        self.run_backend(on_ready=self.backend.stop,
                         on_tick=lambda: self.fail('must not tick after stop'))
        self.assertEqual(self.user.posted, [(0xABCDEF01, 0x12)])
        self.assertEqual(self.user.killed, [self.user.timer])

    def test_other_thread_can_wake_a_blocked_message_pump(self):
        waiting, awakened = threading.Event(), threading.Event()
        failures = []
        def get_message(*args):
            waiting.set()
            if not awakened.wait(2):
                raise AssertionError('stop did not wake message pump')
            return 0
        def post_message(thread, message, wparam, lparam):
            self.user.posted.append((thread, message))
            awakened.set()
            return 1
        def worker():
            try:
                self.run_backend()
            except BaseException as error:
                failures.append(error)
        self.user.GetMessageW = Function(get_message)
        self.user.PostThreadMessageW = Function(post_message)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(waiting.wait(2))
            self.backend.stop()
        finally:
            awakened.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(self.user.posted, [(0xABCDEF01, 0x12)])
        self.assertEqual(len(self.user.unhooked), 2)

    def test_stop_event_changed_while_waiting_skips_timer_work(self):
        stopped = threading.Event()
        self.user.messages = [stopped.set, (0x113, self.user.timer)]
        self.run_backend(stop_event=stopped,
                         on_tick=lambda: self.fail('must not tick after stop'))
        self.assertEqual(self.user.killed, [self.user.timer])

    def test_second_hook_failure_unhooks_first_and_never_announces_ready(self):
        self.user.hook_failure = 14
        with self.assertRaises(OSError):
            self.run_backend(on_ready=lambda: self.fail('must not be ready'))
        self.assertEqual(self.user.unhooked, [0x45678900D])
        self.assertEqual(self.user.timer_calls, [])

    def test_timer_failure_unhooks_both(self):
        self.user.timer = 0
        with self.assertRaises(OSError):
            self.run_backend(on_ready=lambda: self.fail('must not be ready'))
        self.assertCountEqual(self.user.unhooked, [0x45678900D, 0x45678900E])

    def test_ready_and_tick_failure_always_release_timer_and_hooks(self):
        def broken():
            raise RuntimeError('application callback failure')
        for callback in ('on_ready', 'on_tick'):
            with self.subTest(callback=callback):
                self.user.unhooked.clear()
                self.user.killed.clear()
                self.user.messages = [(0x113, self.user.timer)]
                with self.assertRaisesRegex(RuntimeError, 'application callback failure'):
                    self.run_backend(**{callback: broken})
                self.assertEqual(self.user.killed, [self.user.timer])
                self.assertEqual(len(self.user.unhooked), 2)

    def test_unhook_failure_preserves_callback_and_attempts_other_cleanup(self):
        self.user.unhook_failure = 0x45678900E
        with self.assertRaises(OSError):
            self.run_backend()
        self.assertEqual(self.user.killed, [self.user.timer])
        self.assertCountEqual(self.user.unhooked, [0x45678900D, 0x45678900E])
        self.assertIn(self.user.hooks[14], self.backend._callbacks)

    def test_failed_unhook_stays_inert_and_cannot_install_a_second_hook_pair(self):
        observed = []
        self.user.unhook_failure = 0x45678900E
        with self.assertRaises(OSError):
            self.run_backend(on_mouse=lambda: observed.append('mouse'))
        self.user.hooks[14](0, 0x201, 0)
        self.assertEqual(observed, [])
        with self.assertRaisesRegex(RuntimeError, 'released'):
            self.run_backend()
        self.assertEqual(len(self.user.installed), 2)

    def test_cleanup_failure_never_masks_original_application_error(self):
        self.user.unhook_failure = 0x45678900E
        def broken():
            raise RuntimeError('original application failure')
        with self.assertRaisesRegex(RuntimeError, 'original application failure'):
            self.run_backend(on_ready=broken)
        self.assertEqual(len(self.user.unhooked), 2)

    def test_getmessage_error_cleans_up_and_raises(self):
        self.user.messages = [-1]
        with self.assertRaises(OSError):
            self.run_backend()
        self.assertEqual(self.user.killed, [self.user.timer])
        self.assertEqual(len(self.user.unhooked), 2)

    def test_callback_error_escapes_message_pump_after_cleanup(self):
        self.user.messages = [lambda: self.key_event(0x100, 0x46)]
        def broken(*args):
            raise RuntimeError('callback failed')
        with self.assertRaisesRegex(RuntimeError, 'callback failed'):
            self.run_backend(on_key=broken)
        self.assertEqual(len(self.user.unhooked), 2)
        self.assertEqual(self.user.killed, [self.user.timer])

    def test_unrelated_messages_dispatch_and_other_timer_is_ignored(self):
        observed = []
        self.user.messages = [(0x113, 99), (0x8001, 0)]
        self.run_backend(on_tick=lambda: observed.append('tick') or True)
        self.assertEqual(observed, [])
        self.assertEqual(self.user.dispatched, [0x113, 0x8001])

    def test_pointer_signatures_preserve_handles_and_callback_results(self):
        self.assertEqual(self.user.GetForegroundWindow.restype, ctypes.c_void_p)
        self.assertEqual(self.kernel.OpenProcess.restype, ctypes.c_void_p)
        self.assertEqual(self.user.SetWindowsHookExW.restype, ctypes.c_void_p)
        self.assertEqual(self.user.CallNextHookEx.restype, ctypes.c_ssize_t)
        self.assertEqual(self.user.SetTimer.restype, ctypes.c_size_t)
        self.assertEqual(self.user.DispatchMessageW.restype, ctypes.c_ssize_t)
        self.assertEqual(self.user.SendInput.argtypes[2], ctypes.c_int)
        self.assertEqual(self.kernel.QueryFullProcessImageNameW.argtypes[0], ctypes.c_void_p)


if __name__ == '__main__':
    unittest.main()
