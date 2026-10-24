"""单一柔雾紫契约与旧偏好迁移；只使用临时配置，不访问真实用户数据。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import tkinter as tk
from tkinter import ttk

from hots_chat.ui import theme
from hots_chat.window_settings import WindowSettings


def contrast(first, second):
    def luminance(color):
        values = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        values = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
        return sum(v * weight for v, weight in zip(values, (.2126, .7152, .0722)))
    dark, light = sorted((luminance(first), luminance(second)))
    return (light + .05) / (dark + .05)


class LavenderThemeCatalogTests(unittest.TestCase):
    def test_selected_toggle_mark_is_visible_against_its_fill(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        for name in ('moonlight', 'graphite', 'mint'):
            theme.apply_theme(root, name)
            style = ttk.Style(root)
            for prefix in ('', 'Overlay.', 'Surface.'):
                controls = ('TRadiobutton',) if prefix == 'Surface.' else ('TCheckbutton', 'TRadiobutton')
                for control in controls:
                    with self.subTest(theme=name, control=prefix + control):
                        foreground = style.lookup(prefix + control, 'indicatorforeground', ('selected',))
                        background = style.lookup(prefix + control, 'indicatorbackground', ('selected',))
                        self.assertGreaterEqual(contrast(foreground, background), 3)

    def test_selected_input_text_uses_legible_colors_in_main_and_game_surfaces(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        for name in ('moonlight', 'graphite', 'mint'):
            theme.apply_theme(root, name)
            style = ttk.Style(root)
            for prefix, colors in (('', theme.theme_colors(name)), ('Overlay.', theme.overlay_colors(name))):
                for control in ('TEntry', 'TCombobox', 'TSpinbox'):
                    with self.subTest(name=name, control=prefix + control):
                        background = style.lookup(prefix + control, 'selectbackground')
                        foreground = style.lookup(prefix + control, 'selectforeground')
                        self.assertEqual(background, colors['selection'])
                        self.assertEqual(foreground, colors['text'])
                        self.assertGreaterEqual(contrast(foreground, background), 4.5)
                self.assertEqual(style.lookup(prefix + 'TCombobox', 'background', ('readonly', 'active')),
                                 colors['surface'])

    def test_only_lavender_is_exposed_and_registered(self):
        self.assertEqual(theme.PALETTE_NAMES, {'lavender': '柔雾紫'})
        self.assertEqual(set(theme.PALETTES), {'lavender'})

    def test_main_text_and_actions_are_legible_in_every_theme(self):
        self.assertEqual(set(theme.PALETTES), {'lavender'})
        for name, colors in theme.PALETTES.items():
            with self.subTest(name=name):
                for surface in ('background', 'surface', 'field', 'selection'):
                    self.assertGreaterEqual(contrast(colors['text'], colors[surface]), 4.5)
                for surface in ('background', 'surface', 'field'):
                    self.assertGreaterEqual(contrast(colors['muted'], colors[surface]), 4.5)
                self.assertGreaterEqual(contrast(colors['on_accent'], colors['accent']), 4.5)

    def test_legacy_main_and_in_game_preferences_use_lavender_without_shared_mutation(self):
        overlay_colors = getattr(theme, 'overlay_colors', None)
        self.assertTrue(callable(overlay_colors))
        for name in ('lavender', 'moonlight', 'graphite', 'mint', 'amethyst', 'ice', 'unknown'):
            colors = overlay_colors(name)
            self.assertEqual(colors, theme.PALETTES['lavender'])
            self.assertIsNot(colors, theme.PALETTES['lavender'])
            for role in ('text', 'muted'):
                self.assertGreaterEqual(contrast(colors[role], colors['background']), 4.5)
            self.assertGreaterEqual(contrast(colors['on_accent'], colors['accent']), 4.5)
            colors['accent'] = '#000000'
            self.assertEqual(theme.theme_colors(name)['accent'], '#7547F5')

    def test_all_old_choices_save_as_lavender_without_resetting_other_preferences(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            for name in ('lavender', 'moonlight', 'graphite', 'mint', 'amethyst', 'ice', 'unknown'):
                with self.subTest(name=name):
                    original = WindowSettings(color_theme=name, overlay_x=315, overlay_y=412,
                                              hotkeys={'focus': 'Ctrl+Shift+T'}, overlay_original=True)
                    original.save(path)
                    restored = WindowSettings.load(path)
                    self.assertEqual(restored.color_theme, 'lavender')
                    self.assertEqual((restored.overlay_x, restored.overlay_y), (315, 412))
                    self.assertEqual(restored.hotkeys, original.hotkeys)
                    self.assertTrue(restored.overlay_original)

    def test_legacy_theme_migration_preserves_coordinates_and_shortcuts(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            for previous in ('moonlight', 'graphite', 'mint', 'amethyst', 'ice', 'unknown', None, ['mint']):
                path.write_text(json.dumps({'color_theme': previous, 'x': 377, 'y': 205,
                                            'overlay_x': 640, 'overlay_y': 725,
                                            'font_size': 19, 'overlay_width': 650, 'overlay_font_size': 20,
                                            'hotkeys': {'focus': 'Ctrl+Shift+T'}}), encoding='utf-8')
                restored = WindowSettings.load(path)
                self.assertEqual(restored.color_theme, 'lavender')
                self.assertEqual((restored.font_size, restored.overlay_width, restored.overlay_font_size), (19, 650, 20))
                self.assertEqual((restored.x, restored.y), (377, 205))
                self.assertEqual((restored.overlay_x, restored.overlay_y), (640, 725))
                self.assertEqual(restored.hotkeys, {'focus': 'Ctrl+Shift+T'})
                restored.save(path)
                self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['color_theme'], 'lavender')

    def test_unknown_theme_falls_back_without_discarding_valid_preferences(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            path.write_text('{"color_theme":"future-theme","x":331,"font_size":16}', encoding='utf-8')
            restored = WindowSettings.load(path)
            self.assertEqual(restored.color_theme, 'lavender')
            self.assertEqual(restored.x, 331)
            self.assertEqual(restored.font_size, 16)


if __name__ == '__main__':
    unittest.main()
