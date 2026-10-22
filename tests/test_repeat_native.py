"""S连按原生边界：只注入惰性的Win32/WinMM假API。"""
import ctypes
import importlib
import threading
import unittest
from unittest.mock import patch

from tests.test_loot_native import FakeKernel, FakeUser, Function


class FakeWinMM:
    def __init__(self):
        self.calls = []
        self.begin_result = self.end_result = 0
        self.timeBeginPeriod = Function(self.begin)
        self.timeEndPeriod = Function(self.end)

    def begin(self, value):
        self.calls.append(('begin', value))
        return self.begin_result

    def end(self, value):
        self.calls.append(('end', value))
        return self.end_result


class RepeatNativeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.native = importlib.import_module('hots_chat.repeat_native')
        except ImportError as failure:
            self.fail('尚未提供S连按原生边界：' + str(failure))
        self.user, self.kernel, self.winmm = FakeUser(), FakeKernel(), FakeWinMM()
        self.backend = self.native.Win32RepeatBackend(
            user=self.user, kernel=self.kernel, winmm=self.winmm)

    def run_backend(self, **callbacks):
        values = dict(on_key=lambda *args: False, on_mouse=lambda: None,
                      on_tick=lambda: True, on_ready=lambda: None,
                      stop_event=threading.Event())
        values.update(callbacks)
        return self.backend.run(**values)

    def test_marked_s_pair_uses_same_foreground_identity_validation_as_loot(self):
        target = self.backend.foreground_target()
        self.assertTrue(self.backend.send_s(target))
        self.assertEqual(self.user.sent, [(2, 40, [
            (1, 0x53, 0, 0x484F5453), (1, 0x53, 2, 0x484F5453)])])
        self.user.hwnd += 1
        self.assertFalse(self.backend.send_s(target))
        self.assertEqual(len(self.user.sent), 1)

    def test_custom_toggle_down_reads_only_physical_function_key_state(self):
        self.user.keys = {0x7B}
        self.assertTrue(self.backend.toggle_down(0x7B))
        self.assertFalse(self.backend.toggle_down(0x77))

    def test_partial_or_failed_send_only_attempts_s_keyup_then_raises(self):
        for result in (0, 1, OSError('blocked')):
            with self.subTest(result=result):
                self.user.sent.clear()
                self.user.send_results = [result, 1]
                with self.assertRaises(OSError):
                    self.backend.send_s(self.backend.foreground_target())
                self.assertEqual(self.user.sent[1], (1, 40, [(1, 0x53, 2, 0x484F5453)]))

    def test_cancellation_during_foreground_query_prevents_send(self):
        target = self.backend.foreground_target()
        cancelled = threading.Event()
        self.kernel.during_query = cancelled.set
        self.assertFalse(self.backend.send_s(target, allowed=lambda: not cancelled.is_set()))
        self.assertEqual(self.user.sent, [])

    def test_ten_ms_pump_pairs_resolution_request_and_release(self):
        self.user.messages = [(0x113, self.user.timer)]
        self.run_backend(on_tick=lambda: False)
        self.assertEqual(self.user.timer_calls, [(None, 0, 10, None)])
        self.assertEqual(self.winmm.calls, [('begin', 1), ('end', 1)])
        self.assertEqual(self.user.killed, [self.user.timer])
        self.assertEqual(len(self.user.unhooked), 2)

    def test_resolution_is_released_on_hook_timer_callback_and_cleanup_failures(self):
        def broken():
            raise RuntimeError('application failed')
        for failure in ('hook', 'timer', 'callback', 'cleanup'):
            with self.subTest(failure=failure):
                self.setUp()
                if failure == 'hook':
                    self.user.hook_failure = 14
                elif failure == 'timer':
                    self.user.timer = 0
                elif failure == 'cleanup':
                    self.user.unhook_failure = 0x45678900E
                with self.assertRaises((OSError, RuntimeError)):
                    self.run_backend(on_ready=broken if failure == 'callback' else lambda: None)
                self.assertEqual(self.winmm.calls, [('begin', 1), ('end', 1)])

    def test_resolution_start_failure_installs_nothing_and_is_visible(self):
        self.winmm.begin_result = 97
        with self.assertRaisesRegex(OSError, 'timeBeginPeriod'):
            self.run_backend()
        self.assertEqual(self.winmm.calls, [('begin', 1)])
        self.assertEqual(self.user.installed, [])

    def test_resolution_release_failure_is_visible_without_masking_primary_failure(self):
        self.winmm.end_result = 97
        with self.assertRaisesRegex(OSError, 'timeEndPeriod'):
            self.run_backend()
        def broken():
            raise RuntimeError('primary failure')
        with self.assertRaisesRegex(RuntimeError, 'primary failure'):
            self.run_backend(on_ready=broken)

    def test_stop_before_run_does_not_request_resolution_or_install_hooks(self):
        stopped = threading.Event()
        stopped.set()
        self.run_backend(stop_event=stopped)
        self.assertEqual(self.winmm.calls, [])
        self.assertEqual(self.user.installed, [])

    def test_import_does_not_load_dll_create_thread_or_call_timer_api(self):
        import runpy
        with patch('ctypes.WinDLL', side_effect=AssertionError('DLL load at import')), \
                patch('threading.Thread', side_effect=AssertionError('thread at import')):
            runpy.run_path(self.native.__file__, run_name='hots_chat.repeat_native_import_check')

    def test_concurrent_native_run_cannot_request_a_second_resolution_or_hook_pair(self):
        entered, release = threading.Event(), threading.Event()
        failures = []
        def ready():
            entered.set()
            release.wait(2)
        def worker():
            try:
                self.run_backend(on_ready=ready)
            except BaseException as failure:
                failures.append(failure)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                self.run_backend()
            self.assertEqual(self.winmm.calls, [('begin', 1)])
            self.assertEqual(len(self.user.installed), 2)
        finally:
            release.set()
            thread.join(2)
        self.assertEqual(failures, [])
        self.assertEqual(self.winmm.calls, [('begin', 1), ('end', 1)])

    def test_loot_backend_keeps_fifty_ms_timer_without_loading_winmm(self):
        from hots_chat.loot_native import Win32LootBackend
        backend = Win32LootBackend(user=self.user, kernel=self.kernel)
        with patch('ctypes.WinDLL', side_effect=AssertionError('unexpected DLL load')):
            backend.run(lambda *args: False, lambda: None, lambda: False,
                        lambda: None, threading.Event())
        self.assertEqual(self.user.timer_calls, [(None, 0, 50, None)])

    def test_repeated_runs_pair_each_request_and_do_not_retain_released_callbacks(self):
        for _ in range(2):
            self.run_backend()
        self.assertEqual(self.winmm.calls,
                         [('begin', 1), ('end', 1), ('begin', 1), ('end', 1)])
        self.assertEqual(self.backend._callbacks, [])
        self.assertEqual(len(self.user.installed), 4)
        self.assertEqual(len(self.user.unhooked), 4)

    def test_real_state_machine_and_adapter_keep_mouse_and_game_keys_and_ignore_feedback(self):
        from hots_chat.repeat import RepeatSession
        from hots_chat.loot_native import KBDLLHOOKSTRUCT
        now, statuses, feedback = [10.], [], []
        session = RepeatSession(self.backend, statuses.append, clock=lambda: now[0])
        def key(vk, down, marker=0):
            event = KBDLLHOOKSTRUCT(vk, 0, 0, 0, marker)
            return self.user.hooks[13](0, 0x100 if down else 0x101, ctypes.addressof(event))
        original = self.user.SendInput
        def send(count, inputs, size):
            inserted = original(count, inputs, size)
            for index in range(count):
                event = inputs[index].ki
                feedback.append(key(event.wVk, not bool(event.dwFlags & 2), event.dwExtraInfo))
            return inserted
        self.user.SendInput = Function(send)
        def press():
            self.assertEqual(key(0x77, True), 1)
            self.assertEqual(key(0x77, False), 1)
        def gameplay():
            self.assertEqual(key(ord('Q'), True), 57)
            self.assertEqual(self.user.hooks[14](0, 0x204, 0), 57)
            now[0] += .021
        self.user.messages = [press, (0x113, self.user.timer), gameplay,
                              (0x113, self.user.timer), lambda: key(0x0D, True),
                              (0x113, self.user.timer)]
        self.run_backend(on_key=session.key, on_mouse=session.mouse,
                         on_tick=session.tick, on_ready=session.prepare)
        self.assertEqual(len(self.user.sent), 2)
        self.assertEqual(feedback, [57] * 4)
        self.assertEqual([s.state for s in statuses], ['ready', 'running', 'idle'])
        self.assertEqual(self.winmm.calls, [('begin', 1), ('end', 1)])


if __name__ == '__main__':
    unittest.main()
