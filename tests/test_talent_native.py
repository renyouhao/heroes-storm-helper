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

    def test_chord_is_single_marked_sendinput_with_correct_order(self):
        self.assertEqual(self.backend.select_favorite(self.target), 'sent')
        self.assertEqual(self.user.sent, [(4, 40, [
            (1, 0x11, 0, 0x484F5453), (1, 0xC0, 0, 0x484F5453),
            (1, 0xC0, 2, 0x484F5453), (1, 0x11, 2, 0x484F5453)])])
        self.assertEqual(self.user.installed, [])

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
        expected = {0: [], 1: [(1, 0x11, 2, 0x484F5453)],
                    2: [(1, 0xC0, 2, 0x484F5453), (1, 0x11, 2, 0x484F5453)],
                    3: [(1, 0x11, 2, 0x484F5453)]}
        for count, release in expected.items():
            with self.subTest(count=count):
                self.user.sent.clear()
                self.user.send_results = [count]
                self.assertEqual(self.backend.select_favorite(self.target), 'failed')
                self.assertEqual(len(self.user.sent), 2 if release else 1)
                if release:
                    self.assertEqual(self.user.sent[1], (len(release), 40, release))

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
        self.assertEqual(self.user.sent[1], (2, 40, [
            (1, 0xC0, 2, 0x484F5453), (1, 0x11, 2, 0x484F5453)]))
        self.assertEqual(len(self.user.sent), 2)


if __name__ == '__main__':
    unittest.main()
