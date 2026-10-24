"""Offline API/layout checks; no screenshots, real input or clipboard changes.

Run: .venv/Scripts/python.exe -m tools.overlay_previews.recolor_checks
"""
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hots_chat.layout import DisplayMetrics
from hots_chat.ui import overlay_window, quick_input, theme
from hots_chat.window_settings import WindowSettings

from .recolor import RecolorPreview, _widget_layout


MESSAGES = [
    {'sender': 'Raven', 'channel': '团队', 'time': '14:21', 'original': 'Take the camp.', 'translated': '先拿营地。'},
    {'sender': 'Jin', 'channel': '团队', 'time': '14:22', 'original': 'My ult in 10.', 'translated': '大招还有 10 秒。'},
    {'sender': 'Mochi', 'channel': '团队', 'time': '14:22', 'original': 'Got it.', 'translated': '收到。'},
]


class PassiveOverlayNative:
    def set_overlay_mode(self, *args):
        pass

    def show_no_activate(self, *args):
        pass

    def set_rounded_corners(self, *args):
        pass

    def clear_rounded_corners(self, *args):
        pass


class ControlledInputNative:
    def __init__(self):
        self._hwnd = 0
        self._foreground = 1

    def foreground_window(self):
        return self._foreground

    def window_handle(self, handle):
        return handle

    def attach_ime(self, handle):
        self._hwnd = handle

    def detach_ime(self):
        self._hwnd = 0

    def clear_return_guard(self):
        pass

    def return_is_composing(self, handle):
        return False

    def activate(self, handle):
        self._foreground = handle
        return True

    def restore_foreground(self, previous, own):
        return True


class RecolorChecks(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.settings = WindowSettings(color_theme='lavender', hotkeys={}, target_language='en')
        self.metrics = DisplayMetrics((0, 0, 1400, 900), 1.)
        theme.apply_theme(self.root, self.settings)
        self.original_functions = [module.overlay_colors for module in (theme, overlay_window, quick_input)]
        self.overlay_native = patch.object(overlay_window, 'Win32Window', PassiveOverlayNative)
        self.overlay_native.start()
        self.preview = None
        self.copies = []
        self.notices = []

    def tearDown(self):
        if self.preview is not None:
            self.preview.close()
        self.overlay_native.stop()
        self.root.update_idletasks()
        self.root.destroy()

    def preview_instance(self):
        def translate(text, target):
            if text == '你好':
                return 'Hello' if target == 'en' else '안녕하세요'
            raise ValueError('unknown fixture')

        self.preview = RecolorPreview(self.root, metrics=self.metrics, messages=MESSAGES,
                                      notify=self.notices.append, translate=translate)
        self.preview.quick._native = ControlledInputNative()
        self.preview.quick._clipboard = self.copies.append
        return self.preview

    def pump(self, seconds=.3):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.root.update()
            time.sleep(.01)

    def test_original_controls_order_dimensions_and_packing_identical(self):
        original = quick_input.QuickInput(self.root, SimpleNamespace(generation=0), self.settings,
                                         metrics=self.metrics)
        original._create_window()
        self.root.update_idletasks()
        original_layout = _widget_layout(original.window)
        names = ('Overlay.TEntry', 'Overlay.TCombobox', 'Overlay.Accent.TButton')
        original_styles = {name: theme.ttk.Style(self.root).layout(name) for name in names}
        preview = self.preview_instance()
        preview.quick._create_window()
        self.root.update_idletasks()
        self.assertEqual(original_layout, _widget_layout(preview.quick.window))
        self.assertEqual(original_styles, {name: preview.style.layout(name) for name in names})
        self.assertIs(type(preview.overlay), overlay_window.OverlayWindow)
        self.assertIs(type(preview.quick), quick_input.QuickInput)
        self.assertEqual(theme.overlay_colors(self.settings)['background'], '#F3F1FF')
        self.assertEqual(theme.overlay_colors(preview.settings)['background'], '#F3F1FF')
        self.assertEqual([module.overlay_colors for module in (theme, overlay_window, quick_input)],
                         self.original_functions)
        preview.close()
        self.assertEqual([module.overlay_colors for module in (theme, overlay_window, quick_input)],
                         self.original_functions)
        self.assertEqual(original_styles, {name: preview.style.layout(name) for name in names})
        original.close()

    def test_three_modes_repeated_input_show_draft_cancel_and_production_palette_lifecycle(self):
        preview = self.preview_instance()
        # An offscreen fixture area keeps these component tests out of the user's workspace.
        area = (20000, 20000, 1192, 500)
        preview.show('panel', area)
        self.root.update_idletasks()
        self.assertEqual(sum(row['frame'].winfo_manager() == 'pack' for row in preview.overlay.rows), 2)
        preview.show('subtitle', area)
        canvas = preview.overlay.subtitle_canvas
        self.assertEqual(len(canvas.find_withtag('foreground')), 1)
        self.assertEqual(canvas.itemcget(canvas.find_withtag('foreground')[0], 'fill'), '#ffffff')
        self.assertEqual(canvas.itemcget(canvas.find_withtag('outline')[0], 'fill'), '#141414')
        with patch.object(preview.quick, 'open', wraps=preview.quick.open) as opened:
            preview.show('input', area)
            self.assertEqual(preview.quick.status.get(), quick_input.IDLE_HINT)
            preview.set_draft('草稿不应丢失')
            preview.show('input', (20000, 20000, 900, 380))
            opened.assert_called_once_with()
        self.assertEqual(preview.quick.text.get(), '草稿不应丢失')
        self.assertFalse(preview.overlay._visible)
        self.assertTrue(preview.quick._visible)
        preview.set_draft('你好')
        preview.quick._submit()
        self.pump()
        self.assertEqual(self.copies, ['Hello'])
        self.assertIsNotNone(preview.quick._hide_timer)
        preview.quick.text.set('词典里没有的内容')
        preview.quick._submit()
        self.pump()
        self.assertIn('离线词典', preview.quick.status.get())
        self.assertEqual(self.copies, ['Hello'])
        preview.set_draft('你好')
        preview.quick._submit()
        self.assertEqual(len(preview.controller.timers), 1)
        preview.hide()
        self.pump()
        self.assertEqual(self.copies, ['Hello'])
        self.assertEqual(len(preview.controller.timers), 0)
        self.assertFalse(preview.quick._visible)
        preview.show('input', area)
        self.assertEqual(preview.quick.text.get(), '你好')
        preview.set_background(True)
        preview.show('panel', area)
        preview.set_messages(MESSAGES[:1])
        self.assertEqual(len(preview.overlay.history.entries), 1)
        preview.close()
        preview.close()
        self.assertEqual([module.overlay_colors for module in (theme, overlay_window, quick_input)],
                         self.original_functions)
        self.assertEqual(tuple(self.root.tk.call('after', 'info')), ())


if __name__ == '__main__':
    unittest.main(verbosity=2)
