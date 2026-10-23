"""数据面板边界使用惰性假Win32，不操作真实游戏。"""
import ctypes
import importlib
import threading
import unittest
from unittest.mock import patch

from tests.test_loot_native import FakeKernel, FakeUser, Function


class BoardNativeTests(unittest.TestCase):
    def setUp(self):
        self.native = importlib.import_module('hots_chat.board_native')
        self.user, self.kernel = FakeUser(), FakeKernel()
        self.user.GetDoubleClickTime = Function(lambda: 500)
        self.user.GetSystemMetrics = Function(lambda index: 6)
        self.user.GetClientRect = Function(self.client_rect)
        self.user.ClientToScreen = Function(self.client_to_screen)
        self.user.IsIconic = Function(lambda hwnd: 0)
        self.backend = self.native.Win32BoardBackend(user=self.user, kernel=self.kernel)

    @staticmethod
    def client_rect(hwnd, pointer):
        pointer._obj.left = pointer._obj.top = 0
        pointer._obj.right, pointer._obj.bottom = 1920, 1080
        return 1

    @staticmethod
    def client_to_screen(hwnd, pointer):
        pointer._obj.x += 100
        pointer._obj.y += 200
        return 1

    def test_import_has_no_native_or_thread_side_effects(self):
        with patch('ctypes.WinDLL', side_effect=AssertionError('DLL at import')), \
                patch('threading.Thread', side_effect=AssertionError('thread at import')):
            importlib.reload(self.native)

    def test_viewport_uses_client_origin_and_render_proportions(self):
        self.assertEqual(self.backend.viewport_converter(
            self.user.hwnd, (120., 90., 240., 180.), (1920., 1080.)),
            (220, 290, 340, 380))
        self.assertEqual(self.backend.double_click_settings(), (500, (6, 6)))

    def test_invalid_aspect_or_geometry_is_unknown(self):
        for rect, size in (((0, 0, 50, 50), (1600, 1200)),
                           ((-1, 0, 50, 50), (1920, 1080)),
                           ((0, 0, float('nan'), 50), (1920, 1080)),
                           ((0, 0, 50, 50), (0, 1080))):
            with self.subTest(rect=rect, size=size):
                self.assertIsNone(self.backend.viewport_converter(self.user.hwnd, rect, size))
        self.user.IsIconic.implementation = lambda hwnd: 1
        self.assertIsNone(self.backend.viewport_converter(self.user.hwnd, (1, 1, 5, 5), (1920, 1080)))

    def test_mouse_observer_receives_releases_and_always_passes_through(self):
        events = []
        def click(message, flags=0):
            event = self.native.MSLLHOOKSTRUCT()
            event.pt.x, event.pt.y, event.flags = 220, 290, flags
            callback = self.user.hooks[14]
            result = callback(0, message, ctypes.addressof(event))
            self.assertEqual(result, 57)
        self.user.messages = [lambda: click(0x201), lambda: click(0x202),
                              lambda: click(0x204), lambda: click(0x201, 1), (0x12, 0)]
        self.backend.run(lambda *args:False, lambda *args:events.append(args) or True,
                         lambda:True, lambda:None, threading.Event())
        self.assertEqual(events, [(220, 290, True, 'left', False, 0),
                                  (220, 290, False, 'left', False, 0),
                                  (220, 290, True, 'right', False, 0),
                                  (220, 290, True, 'left', True, 0)])
        self.assertEqual(self.user.sent, [])

    def test_mouse_observer_preserves_external_injection_marker(self):
        events = []
        event = self.native.MSLLHOOKSTRUCT()
        event.pt.x, event.pt.y, event.flags = 220, 290, 1
        event.dwExtraInfo = 0x12345
        self.backend._dispatch_mouse(lambda *args: events.append(args), 0x201,
                                     ctypes.addressof(event))
        self.assertEqual(events, [(220, 290, True, 'left', True, 0x12345)])
        self.assertEqual(self.user.sent, [])

    def test_team_chord_holds_ctrl_before_enter_and_cleans_up(self):
        target = self.backend.foreground_target()
        with patch('time.sleep'):
            self.assertTrue(self.backend.open_team_chat(target))
        downs = [key for count, size, events in self.user.sent
                 for kind, key, flags, marker in events if not flags]
        self.assertEqual(downs, [0x11, 0x0D])
        self.assertEqual(self.user.sent[-1][2], [(1, 0x0D, 2, 0x484F5453),
                                                (1, 0x11, 2, 0x484F5453)])

    def test_unicode_uses_scan_units_without_touching_clipboard(self):
        records = []
        original = self.user.SendInput.implementation
        def capture(count, inputs, size):
            records.extend((inputs[i].ki.wVk, inputs[i].ki.wScan, inputs[i].ki.dwFlags)
                           for i in range(count))
            return original(count, inputs, size)
        self.user.SendInput.implementation = capture
        self.assertTrue(self.backend.type_unicode('中A😀', self.backend.foreground_target()))
        self.assertEqual(records, [(0, 0x4e2d, 4),(0, 0x4e2d, 6),(0, 65, 4),(0, 65, 6),
                                   (0, 0xd83d, 4),(0, 0xd83d, 6),(0, 0xde00, 4),(0, 0xde00, 6)])

    def test_native_cancel_or_held_physical_key_prevents_new_input(self):
        target = self.backend.foreground_target()
        self.assertFalse(self.backend.open_team_chat(target, allowed=lambda:False))
        self.assertEqual(self.user.sent, [])
        self.user.keys.add(0x53)
        self.assertFalse(self.backend.type_unicode('中', target))
        self.assertFalse(self.backend.submit_chat(target))
        self.assertEqual(self.user.sent, [])

    def test_partial_unicode_does_not_retry_down_or_submit(self):
        records = []
        original = self.user.SendInput.implementation
        def capture(count, inputs, size):
            records.extend(inputs[i].ki.dwFlags for i in range(count))
            return original(count, inputs, size)
        self.user.SendInput.implementation = capture
        self.user.send_results = [1]
        self.assertFalse(self.backend.type_unicode('中', self.backend.foreground_target()))
        self.assertEqual(records.count(4), 1)
        self.assertTrue(all(flags == 6 for flags in records[2:]))

    def test_definite_zero_insert_never_releases_unowned_keys(self):
        target = self.backend.foreground_target()
        for operation in (self.backend.open_team_chat, self.backend.submit_chat):
            with self.subTest(operation=operation.__name__):
                self.user.sent.clear()
                self.user.send_results = [0]
                with patch('time.sleep'):self.assertFalse(operation(target))
                self.assertEqual(len(self.user.sent), 1)


if __name__ == '__main__':
    unittest.main()
