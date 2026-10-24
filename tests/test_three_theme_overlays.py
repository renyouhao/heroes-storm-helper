"""旧主题回退柔雾紫的可读性与状态保持；只创建屏幕外 Tk 窗口。"""
from collections import OrderedDict
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest

from hots_chat.layout import DisplayMetrics
from hots_chat.models import ChatEvent
from hots_chat.ui import theme
from hots_chat.ui.overlay_window import OverlayWindow
from hots_chat.ui.quick_input import QuickInput
from hots_chat.ui.viewmodel import MessageView
from hots_chat.window_settings import WindowSettings


class OfflineNative:
    def set_overlay_mode(self, window, editing):
        pass

    def show_no_activate(self, window):
        window.deiconify()

    def return_is_composing(self, handle):
        return False

    def detach_ime(self):
        pass


def brightness(color):
    return max(int(color[index:index + 2], 16) for index in (1, 3, 5))


def contrast(first, second):
    def luminance(color):
        rgb = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
                  for value in rgb]
        return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))
    lower, upper = sorted((luminance(first), luminance(second)))
    return (upper + .05) / (lower + .05)


class ThreeThemeOverlayTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.metrics = DisplayMetrics((10000, 10000, 11920, 11080), 1)
        self.settings = WindowSettings(overlay_x=10400, overlay_y=10400,
                                       overlay_original=True, color_theme='graphite',
                                       overlay_style='panel', overlay_font_size=14,
                                       overlay_timeout=15, overlay_width=440,
                                       overlay_placement='chat')

    def overlay(self):
        overlay = OverlayWindow(self.root, self.settings, metrics=self.metrics,
                                native=OfflineNative(), clock=lambda: 10.)
        self.addCleanup(overlay.close)
        messages = OrderedDict()
        for key in 'ab':
            event = ChatEvent(key, 1, '团队', '队友', '1:00', 'Group up ' + key)
            messages[key] = MessageView(event, engine_translation='请先集合 ' + key)
        overlay.sync(messages, 1)
        self.root.update_idletasks()
        return overlay

    def quick_input(self):
        controller = SimpleNamespace(generation=1, submit_manual=lambda draft, target: 'manual-1')
        quick = QuickInput(self.root, controller, self.settings, metrics=self.metrics,
                           native=OfflineNative(), clipboard=lambda text: None)
        self.addCleanup(quick.close)
        quick._create_window()
        quick.window.deiconify()
        self.root.update_idletasks()
        return quick

    def test_legacy_translation_surfaces_share_lavender_and_stay_readable(self):
        overlay = self.overlay()
        accents = set()
        for name in ('moonlight', 'graphite', 'mint'):
            with self.subTest(theme=name):
                self.settings.color_theme = name
                theme.apply_theme(self.root, self.settings)
                overlay.apply_settings()
                self.root.update_idletasks()
                surface = overlay.window.cget('background')
                self.assertEqual(surface, theme.theme_colors('lavender')['surface'])
                self.assertGreaterEqual(contrast(overlay.rows[0]['translation'].cget('foreground'), surface), 7)
                self.assertGreaterEqual(contrast(overlay.rows[0]['original'].cget('foreground'), surface), 4.5)
                self.assertGreater(int(overlay.window.cget('highlightthickness')), 0)
                accents.add(overlay.done.cget('activebackground'))
        self.assertEqual(accents, {theme.theme_colors('lavender')['accent']})

    def test_translation_theme_refresh_preserves_history_deadline_timer_and_bottom(self):
        overlay = self.overlay()
        window = overlay.window
        history = [(key, id(entry), entry.expires_at) for key, entry in overlay.history.entries.items()]
        timer = overlay._timer
        bottom = window.winfo_y() + window.winfo_height()
        for name in ('moonlight', 'mint', 'graphite'):
            self.settings.color_theme = name
            overlay.apply_settings()
            self.root.update_idletasks()
            self.assertIs(overlay.window, window)
            self.assertEqual([(key, id(entry), entry.expires_at)
                              for key, entry in overlay.history.entries.items()], history)
            self.assertEqual(overlay._timer, timer)
            self.assertEqual(window.winfo_y() + window.winfo_height(), bottom)

    def test_legacy_quick_input_preferences_use_lavender_fields_and_actions(self):
        quick = self.quick_input()
        style = ttk.Style(self.root)
        for name in ('moonlight', 'graphite', 'mint'):
            with self.subTest(theme=name):
                self.settings.color_theme = name
                theme.apply_theme(self.root, self.settings)
                quick.refresh_layout(force=True)
                self.root.update_idletasks()
                surface = quick.window.cget('background')
                entry_style = quick.entry.cget('style') or 'TEntry'
                entry_background = style.lookup(entry_style, 'fieldbackground')
                foreground = style.lookup(entry_style, 'foreground')
                self.assertEqual(surface, theme.theme_colors('lavender')['background'])
                self.assertEqual(entry_background, theme.theme_colors('lavender')['field'])
                self.assertGreaterEqual(contrast(foreground, entry_background), 7)
                self.assertEqual(style.lookup(quick.body.cget('style'), 'background'),
                                 theme.theme_colors('lavender')['background'])
                self.assertLess(quick.target_control.winfo_x(), quick.entry.winfo_x())
                accent_style = quick.submit_button.cget('style')
                accent = style.lookup(accent_style, 'background')
                self.assertGreaterEqual(contrast(style.lookup(accent_style, 'foreground'), accent), 4.5)

    def test_quick_input_theme_refresh_keeps_pending_job_draft_status_and_timer(self):
        theme.apply_theme(self.root, self.settings)
        quick = self.quick_input()
        quick._visible = True
        quick.text.set('先集合，等大家到齐')
        quick._submit()
        pending, revision = quick._pending, quick._revision
        window, text, status = quick.window, quick.text.get(), quick.status.get()
        quick._hide_timer = self.root.after(60000, lambda: None)
        timer = quick._hide_timer
        geometry = window.geometry()
        for name in ('moonlight', 'mint', 'graphite'):
            self.settings.color_theme = name
            theme.apply_theme(self.root, self.settings)
            quick.refresh_layout(force=True)
            self.root.update_idletasks()
            self.assertIs(quick.window, window)
            self.assertIs(quick._pending, pending)
            self.assertEqual(quick._revision, revision)
            self.assertEqual(quick.text.get(), text)
            self.assertEqual(quick.status.get(), status)
            self.assertEqual(quick._hide_timer, timer)
            self.assertEqual(window.geometry(), geometry)
            self.assertTrue(quick.submit_button.instate(['disabled']))

    def test_quick_input_language_popup_uses_lavender_before_and_after_legacy_theme_refresh(self):
        self.settings.color_theme = 'moonlight'
        theme.apply_theme(self.root, self.settings)
        quick = self.quick_input()
        # 只创建自己的隐藏下拉控件，不打开菜单，不与桌面或游戏交互。
        popup = self.root.tk.call('ttk::combobox::PopdownWindow', str(quick.target_control))
        listbox = popup + '.f.l'
        self.assertEqual(self.root.tk.call(listbox, 'cget', '-background'), theme.theme_colors('lavender')['field'])
        for name in ('moonlight', 'mint', 'graphite'):
            with self.subTest(theme=name):
                self.settings.color_theme = name
                theme.apply_theme(self.root, self.settings)
                quick.refresh_layout(force=True)
                background = self.root.tk.call(listbox, 'cget', '-background')
                foreground = self.root.tk.call(listbox, 'cget', '-foreground')
                self.assertEqual(background, theme.theme_colors('lavender')['field'])
                self.assertGreaterEqual(contrast(foreground, background), 7)


if __name__ == '__main__':
    unittest.main()
