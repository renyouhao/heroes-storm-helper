"""柔雾紫持久化、原生开关/胶囊行为与有界主题图片资源。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import tkinter as tk
from tkinter import ttk
import unittest

from hots_chat.appearance import DEFAULT_THEME, normalize_theme
from hots_chat.ui import theme
from hots_chat.window_settings import WindowSettings
from tests.test_three_theme_catalog import contrast


def elements(layout):
    for name, options in layout:
        yield name
        yield from elements(options.get('children', ()))


class LavenderPersistenceTests(unittest.TestCase):
    def test_new_preferences_default_to_lavender_and_saved_choices_roundtrip(self):
        self.assertEqual(DEFAULT_THEME, 'lavender')
        self.assertEqual(WindowSettings().color_theme, 'lavender')
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            for name in theme.PALETTES:
                original = WindowSettings(color_theme=name, x=312, overlay_x=771,
                                          hotkeys={'focus': 'Ctrl+Shift+T'})
                original.save(path)
                restored = WindowSettings.load(path)
                self.assertEqual(restored.color_theme, name)
                self.assertEqual((restored.x, restored.overlay_x), (312, 771))
                self.assertEqual(restored.hotkeys, original.hotkeys)

    def test_legacy_aliases_and_unknown_values_preserve_other_preferences(self):
        for old in ('moonlight', 'graphite', 'mint', 'amethyst', 'ice', None, ['mint']):
            self.assertEqual(normalize_theme(old), 'lavender')
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            path.write_text(json.dumps({'color_theme': 'unknown', 'x': 351, 'font_size': 17}),
                            encoding='utf-8')
            restored = WindowSettings.load(path)
            self.assertEqual((restored.color_theme, restored.x, restored.font_size),
                             ('lavender', 351, 17))

    def test_main_text_and_primary_actions_remain_legible(self):
        c = theme.theme_colors('lavender')
        for role in ('background', 'surface', 'field', 'selection'):
            self.assertGreaterEqual(contrast(c['text'], c[role]), 4.5, role)
        for role in ('background', 'surface', 'field'):
            self.assertGreaterEqual(contrast(c['muted'], c[role]), 4.5, role)
        self.assertGreaterEqual(contrast(c['sidebar_muted'], c['sidebar']), 4.5)
        self.assertGreaterEqual(contrast(c['on_accent'], c['accent']), 4.5)
        game = theme.overlay_colors('lavender')
        self.assertEqual(game, c)
        self.assertIsNot(game, c)
        self.assertGreaterEqual(contrast(game['text'], game['background']), 4.5)


class LavenderControlTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.tk.call('tk', 'scaling', 96 / 72)
        self.addCleanup(self.root.destroy)

    def test_lavender_primary_buttons_and_sidebar_use_semantic_purple(self):
        style = theme.apply_theme(self.root, 'lavender')
        c = theme.theme_colors('lavender')
        for prefix in ('', 'Surface.'):
            name = prefix + 'Accent.TButton'
            self.assertEqual(style.lookup(name, 'background'), c['accent'])
            self.assertEqual(style.lookup(name, 'foreground'), '#FFFFFF')
            self.assertEqual(style.lookup(prefix + 'Soft.TButton', 'foreground'), c['accent'])
        for bits in ((), ('active',)):
            self.assertEqual(style.lookup('Sidebar.Selected.TButton', 'foreground', bits), c['accent'])
        for name in ('moonlight', 'graphite', 'mint'):
            theme.apply_theme(self.root, name)
            self.assertEqual(style.lookup('Surface.Accent.TButton', 'background'),
                             theme.theme_colors('lavender')['accent'])

    def test_switch_pixels_distinguish_selected_disabled_pressed_and_keyboard_focus(self):
        theme.apply_theme(self.root, 'lavender')
        images = self.root._hots_checkbox_assets['switch'].images
        self.assertEqual((images['normal'].width(), images['normal'].height()), (44, 26))
        self.assertEqual(images['normal'].get(13, 13), (255, 255, 255))
        self.assertEqual(images['selected'].get(31, 13), (255, 255, 255))
        self.assertEqual(images['selected'].get(22, 13), (117, 71, 245))
        self.assertNotEqual(images['normal'].get(22, 13), images['selected'].get(22, 13))
        for name in ('disabled_selected', 'pressed_selected'):
            self.assertNotEqual(images[name].get(22, 13), images['selected'].get(22, 13))
        self.assertTrue(images['normal'].transparency_get(13, 1))
        self.assertFalse(images['focus'].transparency_get(13, 1))

    def test_switch_native_invoke_space_and_disabled_behaviors_preserve_value_on_theme_change(self):
        style = theme.apply_theme(self.root, 'lavender')
        selected = tk.BooleanVar(master=self.root, value=False)
        switch = ttk.Checkbutton(self.root, style='Switch.TCheckbutton', variable=selected)
        self.assertIn('Hots.main.Switch.indicator', elements(style.layout('Switch.TCheckbutton')))
        switch.invoke()
        self.assertTrue(selected.get())
        switch.state(['disabled'])
        switch.invoke()
        self.assertTrue(selected.get())
        switch.state(['!disabled'])
        binding = self.root.tk.call('bind', 'TCheckbutton', '<Key-space>')
        self.root.tk.eval(binding.replace('%W', str(switch)))
        self.assertFalse(selected.get())
        switch.state(['selected', 'focus'])
        theme.apply_theme(self.root, 'graphite')
        self.assertTrue(switch.instate(['selected', 'focus']))
        self.assertFalse(selected.get())

    def test_segmented_language_choices_use_native_radio_semantics_without_dot_indicator(self):
        style = theme.apply_theme(self.root, 'lavender')
        language = tk.StringVar(master=self.root, value='en')
        english = ttk.Radiobutton(self.root, style='Surface.Segment.TRadiobutton',
                                  variable=language, value='en')
        korean = ttk.Radiobutton(self.root, style='Surface.Segment.TRadiobutton',
                                 variable=language, value='ko')
        layout = set(elements(style.layout('Surface.Segment.TRadiobutton')))
        self.assertNotIn('Radiobutton.indicator', layout)
        self.assertIn('Radiobutton.label', layout)
        korean.invoke()
        self.assertEqual(language.get(), 'ko')
        self.assertTrue(korean.instate(['selected']))
        self.assertFalse(english.instate(['selected']))
        english.state(['disabled'])
        english.invoke()
        self.assertEqual(language.get(), 'ko')
        c = theme.theme_colors('lavender')
        self.assertEqual(style.lookup('Surface.Segment.TRadiobutton', 'foreground', ('selected',)), c['accent'])
        self.assertEqual(style.lookup('Surface.Segment.TRadiobutton', 'background', ('selected',)), c['selection'])
        theme.apply_theme(self.root, 'mint')
        self.assertEqual(language.get(), 'ko')

    def test_switch_and_segment_image_pools_remain_bounded_across_theme_and_dpi_changes(self):
        style = theme.apply_theme(self.root, 'lavender')
        images = set(self.root.tk.call('image', 'names'))
        installed = set(style.element_names())
        switch = self.root._hots_checkbox_assets['switch'].images['selected']
        for scale, name in ((2, 'graphite'), (1.5, 'mint'), (.75, 'moonlight'), (4, 'lavender')):
            self.root.tk.call('tk', 'scaling', scale * 96 / 72)
            theme.apply_theme(self.root, name)
            theme.refresh_theme_scale(self.root, name)
            self.assertEqual(set(self.root.tk.call('image', 'names')), images)
            self.assertEqual(set(style.element_names()), installed)
            self.assertIs(self.root._hots_checkbox_assets['switch'].images['selected'], switch)
        self.assertEqual(switch.width(), 176)

    def test_destroy_releases_switch_and_segment_images_synchronously(self):
        other = tk.Tk()
        other.withdraw()
        theme.apply_theme(other, 'lavender')
        switch = other._hots_checkbox_assets['switch']
        rounded = other._hots_rounded_assets
        retained = [switch.images['selected'], rounded.images['surface_segment:selected']]
        other.destroy()
        self.assertFalse(switch.images)
        self.assertFalse(rounded.images)
        self.assertTrue(all(image.tk is None and image.name is None for image in retained))


if __name__ == '__main__':
    unittest.main()
