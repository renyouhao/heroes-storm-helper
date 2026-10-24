"""新主题及设置入口联动，只创建屏幕外助手窗口，数据全部临时隔离。"""
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hots_chat.app import build_controller
from hots_chat.models import AppEvent, ChatEvent
from hots_chat.paths import AppPaths
from hots_chat.layout import DisplayMetrics
from hots_chat.ui.main_window import create_app
from hots_chat.ui.settings_dialog import SettingsDialog
from hots_chat.ui.theme import theme_colors, overlay_colors
from hots_chat.window_settings import WindowSettings
from tests.test_scaled_popup_layout import OfflineOverlayNative


def contrast(foreground, background):
    def luminance(color):
        values = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
        return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))
    a, b = sorted((luminance(foreground), luminance(background)))
    return (b + .05) / (a + .05)


class NexusThemeIntegrationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(folder.name)
        controller = build_controller(AppPaths(base / '.env', base / 'language.db', base / 'settings.json'), True)
        self.root = tk.Tk()
        self.root.withdraw()
        metrics = DisplayMetrics((10000, 10000, 11920, 11020), 1)
        with patch('hots_chat.ui.main_window.screen_bounds', return_value=[metrics.work_area]), \
             patch('hots_chat.ui.overlay_window.Win32Window', return_value=OfflineOverlayNative()):
            self.window = create_app(self.root, controller,
                                     WindowSettings(x=10000, y=10000, hotkeys={},color_theme='graphite'))
        self.window._read_display_metrics = lambda: metrics
        self.addCleanup(self.window.close)

    def test_legacy_dark_preference_uses_legible_lavender_surface(self):
        style = ttk.Style(self.root)
        for widget in (self.window.input, self.window.preview, self.window.detail,
                       self.window.phrase_panel.listbox):
            bg, fg = widget.cget('background'), widget.cget('foreground')
            role = 'surface' if widget is self.window.input else 'field'
            self.assertEqual(bg.upper(), theme_colors('lavender')[role])
            self.assertGreaterEqual(contrast(fg, bg), 4.5)
        self.assertGreaterEqual(contrast(style.lookup('Treeview', 'foreground'),
                                         style.lookup('Treeview', 'background')), 4.5)

    def test_primary_action_has_contrast_and_visible_keyboard_focus(self):
        style = ttk.Style(self.root)
        self.assertGreaterEqual(contrast(style.lookup('Accent.TButton', 'foreground'),
                                         style.lookup('Accent.TButton', 'background')), 4.5)
        self.assertTrue(style.lookup('TEntry', 'bordercolor', ('focus',)))

    def test_quick_input_position_tracks_current_overlay_when_dragged(self):
        provider = self.window.quick_input._position_provider
        self.assertTrue(callable(provider))
        with patch.object(self.window.overlay, 'input_position', return_value=(10120, 10750)):
            self.assertEqual(provider(), (10120, 10750))

    def test_switching_to_light_theme_updates_views_without_dropping_drafts(self):
        w = self.window
        w.input.insert('1.0', '主窗草稿')
        w.vm.apply(AppEvent('chat', 0, ChatEvent('a', 0, '团队', '队友', '0:01', 'help')))
        w.overlay.sync(w.vm.messages, 0)
        w.quick_input._native = SimpleNamespace(detach_ime=lambda: None)
        w.quick_input._create_window()
        w.quick_input.text.set('快捷输入草稿')
        quick_window = w.quick_input.window
        font = w.quick_input.entry.cget('font')
        deadline = w.overlay.history.next_expiry
        w.settings.color_theme = 'ice'
        w.apply_settings(layout_only=True)
        self.assertEqual(w.input.cget('background'), theme_colors('lavender')['surface'])
        self.assertEqual(quick_window.cget('background'), overlay_colors('moonlight')['background'])
        style = ttk.Style(self.root)
        self.assertGreaterEqual(contrast(style.lookup('Accent.TButton', 'foreground'),
                                         style.lookup('Accent.TButton', 'background')), 4.5)
        self.assertEqual(w.input.get('1.0', 'end-1c'), '主窗草稿')
        self.assertEqual(w.quick_input.text.get(), '快捷输入草稿')
        self.assertIs(w.quick_input.window, quick_window)
        self.assertEqual(w.quick_input.entry.cget('font'), font)
        self.assertEqual(w.overlay.history.next_expiry, deadline)
        self.assertEqual(list(w.vm.messages), ['a'])


class ChatLayoutSettingTests(unittest.TestCase):
    def fixture(self):
        dialog = SettingsDialog.__new__(SettingsDialog)
        dialog.values = {'overlay_enabled': Mock()}
        dialog.window = SimpleNamespace(overlay=Mock(), quick_input=Mock(), save_settings=Mock(),
                                        _layout_metrics=DisplayMetrics((0, 0, 1920, 1020)))
        def save(before_apply=None):
            if before_apply is not None:before_apply()
            return True
        dialog.save = Mock(side_effect=save)
        return dialog

    def test_preset_commits_current_display_values_then_opens_calibration(self):
        dialog = self.fixture()
        dialog.use_chat_preset()
        dialog.values['overlay_enabled'].set.assert_called_once_with(True)
        dialog.save.assert_called_once()
        dialog.window.overlay.use_chat_preset.assert_called_once()
        dialog.window.overlay.set_editing.assert_called_once_with(True)
        dialog.window.quick_input.refresh_layout.assert_called_once()
        dialog.window.save_settings.assert_not_called()

    def test_invalid_settings_do_not_reposition_or_destroy_preferences(self):
        dialog = self.fixture()
        dialog.save.side_effect = None
        dialog.save.return_value = False
        dialog.use_chat_preset()
        dialog.window.overlay.use_chat_preset.assert_not_called()
        dialog.window.save_settings.assert_not_called()

    def test_failed_save_rolls_back_preset_and_keeps_dialog_for_retry(self):
        settings = WindowSettings(overlay_placement='custom', overlay_x=260, overlay_y=400,
                                  overlay_anchor=(.2, .4), overlay_bottom_anchor=.55)
        dialog = SettingsDialog.__new__(SettingsDialog)
        names = ('overlay_enabled', 'overlay_original', 'overlay_font_size', 'overlay_alpha',
                 'overlay_width', 'overlay_timeout', 'font_size', 'alpha', 'topmost', 'locked')
        dialog.values = {key: Mock(get=Mock(return_value=getattr(settings, key))) for key in names}
        dialog.bindings = {}
        dialog.compact = Mock(get=Mock(return_value=False))
        dialog.dialog = Mock()
        dialog.window = SimpleNamespace(settings=settings, apply_settings=Mock(),
                                        save_settings=Mock(side_effect=OSError('disk unavailable')))

        def preset():
            settings.overlay_placement = 'chat'
            settings.overlay_x = 40
            settings.overlay_y = 600

        with patch('hots_chat.ui.settings_dialog.messagebox.showerror') as error:
            self.assertFalse(dialog.save(before_apply=preset))
        self.assertEqual((settings.overlay_placement, settings.overlay_x, settings.overlay_y),
                         ('custom', 260, 400))
        self.assertEqual(settings.overlay_bottom_anchor, .55)
        dialog.dialog.destroy.assert_not_called()
        dialog.window.save_settings.assert_called_once()
        error.assert_called_once()


if __name__ == '__main__':
    unittest.main()
