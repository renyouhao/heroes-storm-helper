"""Ctrl+OEM3组合键边界，全程使用不会操作系统输入的假API。"""
import ctypes
import importlib
import unittest
from unittest.mock import patch

from tests.test_loot_native import FakeKernel, FakeUser


class TalentNativeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.native = importlib.import_module('hots_chat.talent_native')
        except ImportError as error:
            self.fail('尚未提供自动天赋原生边界：' + str(error))
        self.user, self.kernel = FakeUser(), FakeKernel()
        self.backend = self.native.Win32TalentBackend(user=self.user, kernel=self.kernel)
        self.target = self.backend.foreground_target()

    def test_import_has_no_dll_thread_or_input_side_effects(self):
        with patch('ctypes.WinDLL', side_effect=AssertionError('DLL load at import')), \
                patch('threading.Thread', side_effect=AssertionError('thread at import')):
            importlib.reload(self.native)
        self.assertEqual(self.user.sent, [])

    def test_game_observes_ctrl_before_main_key_and_both_remain_down(self):
        held, sampled = set(), set()
        accepted = []
        original = self.user.SendInput.implementation
        def consume(count, inputs, size):
            for event in inputs[:count]:
                key = event.ki.wVk
                if event.ki.dwFlags & 2:
                    held.discard(key)
                else:
                    if key == 0xC0:
                        accepted.append(0x11 in sampled)
                    held.add(key)
            return original(count, inputs, size)
        def game_frame(delay):
            sampled.clear()
            sampled.update(held)
            self.user.keys = set(held)
            self.assertGreaterEqual(delay, .05)
        self.user.SendInput.implementation = consume
        with patch('time.sleep', side_effect=game_frame):
            self.assertEqual(self.backend.select_favorite(self.target), 'sent')
        self.assertEqual(accepted, [True], '游戏收到主键前必须已观察到 Ctrl 按下')
        self.assertEqual(held, set())
        self.assertEqual(self.user.sent, [
            (1, 40, [(1, 0x11, 0, 0x484F5453)]),
            (1, 40, [(1, 0xC0, 0, 0x484F5453)]),
            (2, 40, [(1, 0xC0, 2, 0x484F5453), (1, 0x11, 2, 0x484F5453)])])
        self.assertEqual(self.user.installed, [])

    def test_cancel_or_focus_change_during_ctrl_lead_releases_without_main_key(self):
        for action in ('cancel', 'focus', 'modifier', 'manual_pick'):
            with self.subTest(action=action):
                self.setUp()
                permitted = [True]
                valid = [True]
                def change(delay):
                    if action == 'cancel':permitted[0] = False
                    elif action == 'focus':self.user.hwnd += 1
                    elif action == 'modifier':self.user.keys.add(0x10)
                    else:valid[0] = False
                with patch('time.sleep', side_effect=change):
                    result = self.backend.select_favorite(self.target,
                        allowed=lambda:permitted[0], validate=lambda:valid[0])
                self.assertEqual(result, 'deferred')
                self.assertEqual(self.user.sent, [
                    (1, 40, [(1, 0x11, 0, 0x484F5453)]),
                    (1, 40, [(1, 0x11, 2, 0x484F5453)])])

    def test_cancel_after_main_key_retains_attempt_and_only_releases(self):
        permitted, waits = [True], []
        def cancel_after_main(delay):
            waits.append(delay)
            if len(waits) == 2:permitted[0] = False
        with patch('time.sleep', side_effect=cancel_after_main):
            result = self.backend.select_favorite(self.target, allowed=lambda:permitted[0])
        self.assertEqual(result, 'sent', '主键已发后不能 deferred 使状态机再次发送')
        self.assertEqual(self.user.sent[-1], (2, 40, [
            (1, 0xC0, 2, 0x484F5453), (1, 0x11, 2, 0x484F5453)]))

    def test_rejects_wrong_window_or_pid_and_never_focuses_window(self):
        self.user.hwnd += 1
        self.assertEqual(self.backend.select_favorite(self.target), 'deferred')
        self.assertEqual(self.user.sent, [])

    def test_physical_modifier_or_oem3_held_delays_chord(self):
        for key in (0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1,
                    0xA2, 0xA3, 0xA4, 0xA5, 0xC0):
            with self.subTest(key=key):
                self.user.keys = {key}
                self.assertEqual(self.backend.select_favorite(self.target), 'deferred')
        self.assertEqual(self.user.sent, [])

    def test_allowed_can_cancel_while_foreground_query_runs(self):
        active = [True]
        self.kernel.during_query = lambda: active.__setitem__(0, False)
        self.assertEqual(self.backend.select_favorite(self.target,
                         allowed=lambda: active[0]), 'deferred')
        self.assertEqual(self.user.sent, [])

    def test_second_foreground_check_catches_focus_change_in_allowed_callback(self):
        def lose_focus():
            self.user.hwnd += 1
            return True
        self.assertEqual(self.backend.select_favorite(self.target, allowed=lose_focus),
                         'deferred')
        self.assertEqual(self.user.sent, [])

    def test_second_key_check_catches_key_pressed_during_foreground_query(self):
        calls = []
        def press_later():
            calls.append(1)
            if len(calls) == 2:
                self.user.keys.add(0xC0)
        self.kernel.during_query = press_later
        self.assertEqual(self.backend.select_favorite(self.target), 'deferred')
        self.assertEqual(self.user.sent, [])

    def test_last_allowed_callback_cannot_change_focus_then_send_to_other_window(self):
        calls = []
        def lose_on_last_gate():
            calls.append(1)
            if len(calls) == 2:
                self.user.hwnd += 1
            return True
        self.assertEqual(self.backend.select_favorite(self.target, allowed=lose_on_last_gate),
                         'deferred')
        self.assertEqual(self.user.sent, [])

    def test_partial_insert_releases_only_keys_possibly_still_down(self):
        cases = (
            ([0], [0x11], []),
            ([1, 0], [0x11, 0xC0], [[0x11]]),
            ([1, OSError('main failed')], [0x11, 0xC0], [[0xC0, 0x11]]),
            ([1, 1, 0], [0x11, 0xC0], [[0xC0, 0x11], [0xC0, 0x11]]),
            ([1, 1, 1], [0x11, 0xC0], [[0xC0, 0x11], [0x11]]),
        )
        for results, downs, releases in cases:
            with self.subTest(results=results):
                self.user.sent.clear()
                self.user.send_results = list(results)
                with patch('time.sleep'):
                    self.assertEqual(self.backend.select_favorite(self.target), 'failed')
                actual_downs = [key for _, _, events in self.user.sent
                                for _, key, flags, _ in events if flags == 0]
                actual_releases = [[key for _, key, flags, _ in events] for _, _, events
                                   in self.user.sent if all(flags == 2 for _, _, flags, _ in events)]
                self.assertEqual(actual_downs, downs)
                self.assertEqual(actual_releases, releases)

    def test_wait_failure_releases_only_owned_keys(self):
        for fail_at, released in ((1, [0x11]), (2, [0xC0, 0x11])):
            with self.subTest(fail_at=fail_at):
                self.user.sent.clear()
                waits = []
                def fail(delay):
                    waits.append(delay)
                    if len(waits) == fail_at:raise OSError('wait interrupted')
                with patch('time.sleep', side_effect=fail):
                    self.assertEqual(self.backend.select_favorite(self.target), 'failed')
                self.assertEqual([event[1] for event in self.user.sent[-1][2]], released)
                self.assertTrue(all(event[2] == 2 for event in self.user.sent[-1][2]))

    def test_cancel_during_last_physical_key_query_cannot_send(self):
        gate = [True]
        calls = []
        def allowed():
            calls.append(1)
            return gate[0]
        original = self.user.GetAsyncKeyState.implementation
        def query(key):
            if len(calls) >= 2:gate[0] = False
            return original(key)
        self.user.GetAsyncKeyState.implementation = query
        self.assertEqual(self.backend.select_favorite(self.target, allowed=allowed), 'deferred')
        self.assertEqual(self.user.sent, [])

    def test_slow_validation_changes_focus_before_final_native_checks(self):
        def slow_snapshot_read():
            self.user.hwnd += 1
            return True
        self.assertEqual(self.backend.select_favorite(self.target, allowed=lambda:True,
                                                     validate=slow_snapshot_read), 'deferred')
        self.assertEqual(self.user.sent, [])

    def test_api_exception_only_sends_keyups_and_cleanup_failure_does_not_retry_down(self):
        self.user.send_results = [OSError('input failed'), OSError('cleanup failed')]
        self.assertEqual(self.backend.select_favorite(self.target), 'failed')
        self.assertEqual(self.user.sent[1:], [
            (1, 40, [(1, 0x11, 2, 0x484F5453)]),
            (1, 40, [(1, 0x11, 2, 0x484F5453)])])


if __name__ == '__main__':
    unittest.main()
