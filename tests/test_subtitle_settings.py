"""透明字幕的偏好、纯布局及设置事务；仅使用临时配置和屏幕外窗口。"""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hots_chat import layout
from hots_chat.layout import DisplayMetrics, quick_input_layout
from hots_chat.ui.settings_dialog import SettingsDialog
from hots_chat.ui.theme import apply_theme
from hots_chat.window_settings import WindowSettings


class SubtitlePreferenceTests(unittest.TestCase):
    def load(self, data):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'settings.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        return WindowSettings.load(path)

    def test_new_preferences_use_small_short_lived_bottom_subtitles(self):
        settings = WindowSettings()
        self.assertEqual(getattr(settings, 'overlay_style', None), 'subtitle')
        self.assertEqual((settings.overlay_font_size, settings.overlay_timeout,
                          settings.overlay_width, settings.overlay_placement),
                         (11, 6, 420, 'bottom'))
        self.assertEqual(settings.overlay_alpha, .88)

    def test_old_chat_defaults_migrate_without_resetting_other_preferences(self):
        settings = self.load({'overlay_font_size': 14, 'overlay_timeout': 15,
                              'overlay_width': 440, 'overlay_placement': 'chat',
                              'overlay_x': 70, 'overlay_y': 580, 'color_theme': 'mint',
                              'font_size': 19, 'hotkeys': {'focus': 'Ctrl+Alt+T'},
                              'preferences_version': 3})
        self.assertEqual(getattr(settings, 'overlay_style', None), 'subtitle')
        self.assertEqual((settings.overlay_font_size, settings.overlay_timeout,
                          settings.overlay_width, settings.overlay_placement),
                         (11, 6, 420, 'bottom'))
        self.assertEqual((settings.overlay_x, settings.overlay_y), (70, 580))
        self.assertEqual((settings.color_theme, settings.font_size), ('mint', 19))
        self.assertEqual(settings.hotkeys, {'focus': 'Ctrl+Alt+T'})

    def test_legacy_custom_position_and_nondefault_dimensions_survive(self):
        settings = self.load({'overlay_x': -700, 'overlay_y': 430,
                              'overlay_anchor': [.25, .6], 'overlay_bottom_anchor': .72,
                              'overlay_width': 610, 'overlay_font_size': 18,
                              'overlay_timeout': 24, 'overlay_alpha': .73,
                              'overlay_original': True,
                              'preferences_version': 3})
        self.assertEqual(settings.overlay_placement, 'custom')
        self.assertEqual((settings.overlay_x, settings.overlay_y), (-700, 430))
        self.assertEqual(settings.overlay_anchor, (.25, .6))
        self.assertEqual(settings.overlay_bottom_anchor, .72)
        self.assertTrue(settings.overlay_original)
        self.assertEqual((settings.overlay_width, settings.overlay_font_size,
                          settings.overlay_timeout, settings.overlay_alpha), (610, 18, 24, .73))

    def test_legacy_custom_default_size_migrates_but_position_stays_custom(self):
        settings = self.load({'overlay_x': 310, 'overlay_y': 650,
                              'overlay_width': 440, 'overlay_font_size': 14,
                              'overlay_timeout': 15})
        self.assertEqual((settings.overlay_x, settings.overlay_y, settings.overlay_placement),
                         (310, 650, 'custom'))
        self.assertEqual((settings.overlay_width, settings.overlay_font_size,
                          settings.overlay_timeout), (420, 11, 6))

    def test_explicit_style_roundtrips_without_repeated_default_migration(self):
        for style in ('subtitle', 'panel'):
            with self.subTest(style=style), TemporaryDirectory() as folder:
                path = Path(folder) / 'settings.json'
                settings = self.load({'overlay_style': style, 'overlay_placement': 'custom',
                                      'overlay_width': 440, 'overlay_font_size': 14,
                                      'overlay_timeout': 15})
                self.assertEqual(getattr(settings, 'overlay_style', None), style)
                settings.save(path)
                restored = WindowSettings.load(path)
                self.assertEqual(getattr(restored, 'overlay_style', None), style)
                self.assertEqual((restored.overlay_width, restored.overlay_font_size,
                                  restored.overlay_timeout), (440, 14, 15))

    def test_invalid_new_choices_preserve_valid_preferences_and_custom_position(self):
        for style in ('unknown', None, ['panel']):
            with self.subTest(style=style):
                settings = self.load({'overlay_style': style, 'overlay_placement': 'unknown',
                                      'overlay_x': -600, 'overlay_y': 500, 'color_theme': 'mint',
                                      'overlay_width': 650, 'overlay_font_size': 20,
                                      'overlay_timeout': 21, 'preferences_version': 3})
                self.assertEqual(getattr(settings, 'overlay_style', None), 'subtitle')
                self.assertEqual(settings.overlay_placement, 'custom')
                self.assertEqual((settings.overlay_x, settings.overlay_y), (-600, 500))
                self.assertEqual((settings.color_theme, settings.overlay_width,
                                  settings.overlay_font_size, settings.overlay_timeout),
                                 ('mint', 650, 20, 21))
        settings = self.load({'overlay_style': 'panel', 'overlay_placement': 'invalid',
                              'color_theme': 'moonlight'})
        self.assertEqual((settings.overlay_placement, settings.color_theme), ('bottom', 'moonlight'))


class SubtitleLayoutTests(unittest.TestCase):
    def bottom(self, metrics, **kwargs):
        helper = getattr(layout, 'bottom_overlay_layout', None)
        self.assertTrue(callable(helper), '需要可独立验证的底部字幕布局')
        return helper(metrics, **kwargs)

    def test_bottom_subtitle_centers_width_and_pins_bottom_at_86_percent(self):
        for work, scale, expected in (
                ((0, 0, 1920, 1040), 1, (420, 80, 750, 814)),
                ((0, 0, 2560, 1400), 1.25, (525, 80, 1018, 1124)),
                ((0, 0, 3840, 2080), 2, (840, 80, 1500, 1709)),
                ((-2560, -100, 0, 1300), 1.5, (630, 80, -1595, 1024))):
            with self.subTest(work=work, scale=scale):
                result = self.bottom(DisplayMetrics(work, scale), height=80)
                self.assertEqual((result.width, result.height, result.x, result.y), expected)

    def test_all_resolutions_and_scales_stay_inside_negative_work_areas(self):
        for width, height in ((1920, 1080), (2560, 1440), (3840, 2160)):
            for scale in (1, 1.25, 1.5, 2):
                with self.subTest(resolution=(width, height), scale=scale):
                    work = (-width, -height, 0, -40)
                    metrics = DisplayMetrics(work, scale)
                    result = self.bottom(metrics, width=900, height=round(110 * scale))
                    self.assertGreaterEqual(result.x, work[0])
                    self.assertGreaterEqual(result.y, work[1])
                    self.assertLessEqual(result.x + result.width, work[2])
                    self.assertLessEqual(result.y + result.height, work[3])
                    self.assertLessEqual(abs((result.x * 2 + result.width) - (work[0] + work[2])), 1)

    def test_subtitle_dimensions_clamp_to_logical_limits_and_tiny_work_area(self):
        metrics = DisplayMetrics((-300, 50, -100, 150), 2)
        result = self.bottom(metrics, width=9999, height=9999)
        self.assertEqual((result.width, result.height, result.x, result.y), (200, 100, -300, 50))
        self.assertEqual(self.bottom(DisplayMetrics((0, 0, 3840, 2080), 2), width=1).width, 560)

    def test_quick_input_default_is_bottom_centered_with_decoration_room(self):
        for work, scale, expected in (
                ((0, 0, 1920, 1020), 1, (640, 120, 632, 844)),
                ((-2560, -100, 0, 1300), 1.5, (960, 120, -1772, 1096))):
            with self.subTest(work=work, scale=scale):
                result = quick_input_layout(DisplayMetrics(work, scale), height=120)
                self.assertEqual((result.width, result.height, result.x, result.y), expected)
                self.assertLess(result.y + result.height + round(40 * scale), work[3])

    def test_quick_input_explicit_position_remains_exact_and_clamped(self):
        metrics = DisplayMetrics((-1920, -50, 0, 1030), 1.25)
        result = quick_input_layout(metrics, height=150, position=(-1800, 650))
        self.assertEqual((result.x, result.y, result.width), (-1800, 650, 800))
        result = quick_input_layout(metrics, height=150, position=(3000, 3000))
        self.assertEqual((result.x, result.y), (-820, 830))


class SettingValue:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class SubtitleSettingTransactionTests(unittest.TestCase):
    def fixture(self, choice='subtitle'):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'settings.json'
        settings = WindowSettings(overlay_placement='custom', overlay_x=-700, overlay_y=400,
                                  overlay_anchor=(.2, .4), color_theme='mint', hotkeys={})
        settings.save(path)
        dialog = SettingsDialog.__new__(SettingsDialog)
        names = ('overlay_enabled', 'overlay_original', 'overlay_font_size', 'overlay_alpha',
                 'overlay_width', 'overlay_timeout', 'font_size', 'alpha', 'topmost', 'locked',
                 'color_theme')
        dialog.values = {name: SettingValue(getattr(settings, name)) for name in names}
        dialog.values['overlay_style'] = SettingValue(choice)
        dialog.bindings = {}
        dialog.compact = SettingValue(False)
        destroyed = []
        dialog.dialog = SimpleNamespace(destroy=lambda: destroyed.append(True))
        runtime = {'visible': True, 'entries': ['latest'], 'input_draft': '保留草稿'}

        def restore(snapshot):
            runtime.clear()
            runtime.update(deepcopy(snapshot))

        dialog.window = SimpleNamespace(settings=settings, apply_settings=lambda: None,
                                        save_settings=lambda: settings.save(path),
                                        capture_settings_state=lambda: deepcopy(runtime),
                                        restore_settings_state=restore)
        return dialog, settings, path, destroyed, runtime

    def test_both_style_choices_save_canonical_values(self):
        for choice, expected in (('subtitle', 'subtitle'), ('panel', 'panel'),
                                 ('透明字幕（推荐）', 'subtitle'), ('半透明面板', 'panel')):
            with self.subTest(choice=choice):
                dialog, settings, path, destroyed, _ = self.fixture(choice)
                with patch('hots_chat.ui.settings_dialog.messagebox.showerror'):
                    self.assertTrue(dialog.save())
                self.assertEqual(getattr(settings, 'overlay_style', None), expected)
                self.assertEqual(getattr(WindowSettings.load(path), 'overlay_style', None), expected)
                self.assertEqual(destroyed, [True])

    def test_invalid_style_keeps_live_and_persisted_preferences_for_retry(self):
        dialog, settings, path, destroyed, _ = self.fixture('unknown')
        previous, stored = deepcopy(asdict(settings)), path.read_bytes()
        with patch('hots_chat.ui.settings_dialog.messagebox.showerror'):
            self.assertFalse(dialog.save())
            self.assertEqual(asdict(settings), previous)
            self.assertEqual(path.read_bytes(), stored)
            self.assertEqual(destroyed, [])
            dialog.values['overlay_style'].set('panel')
            self.assertTrue(dialog.save())
        self.assertEqual(getattr(WindowSettings.load(path), 'overlay_style', None), 'panel')

    def test_both_styles_restore_configuration_and_runtime_on_failed_save(self):
        for style in ('subtitle', 'panel'):
            with self.subTest(style=style):
                dialog, settings, path, destroyed, runtime = self.fixture(style)
                previous, stored, before_runtime = deepcopy(asdict(settings)), path.read_bytes(), deepcopy(runtime)

                def preset():
                    settings.overlay_placement = 'bottom'
                    settings.overlay_anchor = None
                    settings.overlay_x = 750
                    runtime.update(visible=False, entries=[])

                with patch('hots_chat.ui.settings_dialog.messagebox.showerror'):
                    with patch.object(Path, 'write_text', side_effect=OSError('disk unavailable')):
                        self.assertFalse(dialog.save(before_apply=preset))
                    self.assertEqual(asdict(settings), previous)
                    self.assertEqual(path.read_bytes(), stored)
                    self.assertEqual(runtime, before_runtime)
                    self.assertEqual(destroyed, [])
                    self.assertTrue(dialog.save(before_apply=preset))
                self.assertEqual(getattr(WindowSettings.load(path), 'overlay_style', None), style)
                self.assertEqual(settings.overlay_placement, 'bottom')

    def test_failed_application_restores_style_and_runtime_before_retry(self):
        dialog, settings, path, destroyed, runtime = self.fixture('panel')
        previous, stored, before_runtime = deepcopy(asdict(settings)), path.read_bytes(), deepcopy(runtime)
        attempts = []

        def apply():
            attempts.append(settings.overlay_style)
            if len(attempts) == 1:
                settings.overlay_x = 999
                runtime.update(visible=False, entries=[])
                raise ValueError('application unavailable')

        dialog.window.apply_settings = apply
        with patch('hots_chat.ui.settings_dialog.messagebox.showerror'):
            self.assertFalse(dialog.save())
            self.assertEqual(asdict(settings), previous)
            self.assertEqual(path.read_bytes(), stored)
            self.assertEqual(runtime, before_runtime)
            self.assertEqual(destroyed, [])
            self.assertTrue(dialog.save())
        self.assertEqual(WindowSettings.load(path).overlay_style, 'panel')

    def test_bottom_preset_commits_subtitle_after_failed_write_without_calibration(self):
        dialog, settings, path, destroyed, runtime = self.fixture('panel')
        settings.overlay_style = 'panel'
        settings.save(path)
        previous, stored, before_runtime = deepcopy(asdict(settings)), path.read_bytes(), deepcopy(runtime)

        def preset():
            settings.overlay_placement = 'bottom'
            settings.overlay_anchor = None
            settings.overlay_bottom_anchor = None
            settings.overlay_x, settings.overlay_y = 750, 820
            runtime['visible'] = False

        def refresh(metrics, *, force=False):
            self.assertEqual(metrics.work_area, (0, 0, 1920, 1020))
            runtime['input_bottom'] = force

        dialog.window.overlay = SimpleNamespace(use_bottom_preset=preset)
        dialog.window.quick_input = SimpleNamespace(refresh_layout=refresh)
        dialog.window._layout_metrics = DisplayMetrics((0, 0, 1920, 1020))
        with patch('hots_chat.ui.settings_dialog.messagebox.showerror'):
            with patch.object(Path, 'write_text', side_effect=OSError('disk unavailable')):
                dialog.use_bottom_preset()
            self.assertEqual(asdict(settings), previous)
            self.assertEqual(path.read_bytes(), stored)
            self.assertEqual(runtime, before_runtime)
            self.assertEqual(destroyed, [])
            dialog.use_bottom_preset()
        restored = WindowSettings.load(path)
        self.assertEqual((restored.overlay_style, restored.overlay_placement), ('subtitle', 'bottom'))
        self.assertEqual((restored.overlay_x, restored.overlay_y), (750, 820))
        self.assertIsNone(restored.overlay_anchor)
        self.assertIsNone(restored.overlay_bottom_anchor)
        self.assertTrue(runtime['input_bottom'])
        self.assertEqual(destroyed, [True])


class SubtitleSettingsPageTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.geometry('640x480+10000+10000')
        self.root.deiconify()
        self.addCleanup(self.root.destroy)
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'settings.json'
        self.settings = WindowSettings(color_theme='mint', hotkeys={})
        self.settings.save(self.path)
        noop = lambda: None
        self.window = SimpleNamespace(
            root=self.root, settings=self.settings,
            _layout_metrics=DisplayMetrics((10000, 10000, 10640, 10480), 1.5),
            controller=SimpleNamespace(source=SimpleNamespace(reconnect=noop)),
            confirm_usage=noop, stop=noop, start=noop, choose_process=noop,
            apply_settings=noop, save_settings=lambda: self.settings.save(self.path))
        apply_theme(self.root, self.settings)

    def widgets(self, parent):
        for child in parent.winfo_children():
            yield child
            yield from self.widgets(child)

    def test_style_can_be_changed_and_cancelled_without_mutating_preferences(self):
        dialog = SettingsDialog(self.window)
        choices = {str(widget.cget('text')): widget for widget in self.widgets(dialog.dialog)
                   if isinstance(widget, ttk.Radiobutton)}
        self.assertIn('透明字幕（推荐）', choices)
        self.assertIn('半透明面板', choices)
        before, stored = deepcopy(asdict(self.settings)), self.path.read_bytes()
        choices['半透明面板'].invoke()
        self.assertEqual(dialog.values['overlay_style'].get(), 'panel')
        dialog.cancel()
        self.assertEqual(asdict(self.settings), before)
        self.assertEqual(self.path.read_bytes(), stored)

    def test_game_page_scrolls_and_exposes_bottom_preset_on_small_screen(self):
        dialog = SettingsDialog(self.window)
        dialog.notebook.select(1)
        self.root.update()
        canvas = dialog.scroll_canvases[dialog.notebook.select()]
        self.assertGreater(canvas.bbox('all')[3], canvas.winfo_height())
        canvas.yview_moveto(1)
        self.assertGreater(canvas.yview()[0], 0)
        buttons = {str(widget.cget('text')) for widget in self.widgets(dialog.dialog)
                   if isinstance(widget, ttk.Button)}
        self.assertIn('保存并使用底部字幕布局', buttons)
        self.assertIn('保存并调整浮窗位置', buttons)
        dialog.cancel()


if __name__ == '__main__':
    unittest.main()
