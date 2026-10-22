"""屏幕外自产控件验证DPI字体缓存；不改变进程或系统DPI设置。"""
import gc
import tkinter as tk
from tkinter import font as tkfont, ttk
import unittest

from tests import test_adaptive_display_flow as adaptive


class DisplayFontFlowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = adaptive.AdaptiveDisplayFlowTests(methodName='runTest')

    def fresh_font(self, root, size):
        return tkfont.Font(root=root, family='Microsoft YaHei UI', size=size)

    def font_height(self, root, specification):
        return int(root.tk.call('font', 'metrics', specification, '-linespace'))

    def test_existing_main_text_and_quick_entry_match_fresh_fonts_through_dpi_round_trip(self):
        with self.fixture.app(width=3840, height=2160) as (root, window, settings, screen, native):
            quick = window.quick_input
            quick._create_window()
            quick.window.deiconify()
            window.input.insert('1.0', '中文Ag 같이')
            window.set_text(window.preview, '中文Ag 같이')
            window.set_text(window.detail, '中文Ag 같이')
            for scale in (1, 2, 1):
                with self.subTest(scale=scale):
                    self.fixture.refresh_screen(root, window, screen, 3840, 2160, scale)
                    reference = self.fresh_font(root, 12)
                    for widget in (window.input, window.preview):
                        self.assertAlmostEqual(widget.bbox('1.0')[3], reference.metrics('linespace'), delta=2,
                                               msg=str(widget))
                    self.assertAlmostEqual(self.font_height(root, window.detail.cget('font')),
                                           reference.metrics('linespace'), delta=2)
                    fresh_entry = ttk.Entry(quick.body, font=reference)
                    try:
                        root.update_idletasks()
                        self.assertAlmostEqual(quick.entry.winfo_reqheight(), fresh_entry.winfo_reqheight(), delta=2)
                    finally:
                        fresh_entry.destroy()

    def test_tree_overlay_and_phrase_fonts_match_fresh_target_dpi(self):
        with self.fixture.app(width=3840, height=2160) as (root, window, settings, screen, native):
            for scale in (1, 2, 1):
                with self.subTest(scale=scale):
                    self.fixture.refresh_screen(root, window, screen, 3840, 2160, scale)
                    cases = [(ttk.Style(root).lookup('Treeview', 'font'), settings.font_size),
                             (window.phrase_panel.listbox.cget('font'), 11),
                             (window.overlay.instructions.cget('font'), 10),
                             (window.overlay.done.cget('font'), 10),
                             (window.overlay.placeholder.cget('font'), settings.overlay_font_size)]
                    for row in window.overlay.rows:
                        cases.extend([(row['translation'].cget('font'), settings.overlay_font_size),
                                      (row['original'].cget('font'), settings.overlay_font_size - 2)])
                    for specification, size in cases:
                        reference = self.fresh_font(root, size)
                        self.assertAlmostEqual(self.font_height(root, specification), reference.metrics('linespace'), delta=2)

    def test_font_settings_update_existing_named_fonts_without_relayout_allocations(self):
        with self.fixture.app(width=3840, height=2160) as (root, window, settings, screen, native):
            quick = window.quick_input
            quick._create_window()
            def specifications():
                return [widget.cget('font') for widget in
                        (window.input, window.preview, window.detail, quick.entry,
                         window.phrase_panel.listbox, window.overlay.instructions,
                         window.overlay.done, window.overlay.placeholder,
                         *[row[key] for row in window.overlay.rows for key in ('translation', 'original')])] + \
                       [ttk.Style(root).lookup('Treeview', 'font')]
            original = specifications()
            names = root.tk.call('font', 'names')
            self.assertTrue(all(str(name) in names for name in original), '自定义字体必须是持有引用的命名字体')
            before = set(names)
            for _ in range(5):
                window.apply_settings(layout_only=True)
                root.update_idletasks()
            gc.collect()
            self.assertEqual(specifications(), original)
            self.assertEqual(set(root.tk.call('font', 'names')), before)
            settings.font_size = 18
            settings.overlay_font_size = 22
            window.apply_settings()
            root.update_idletasks()
            self.assertEqual(specifications(), original)
            for widget in (window.input, window.preview, window.detail, quick.entry):
                self.assertEqual(int(root.tk.call('font', 'configure', widget.cget('font'), '-size')), 18)
            self.assertEqual(int(root.tk.call('font', 'configure', window.overlay.rows[0]['translation'].cget('font'), '-size')), 22)
            self.assertEqual(int(root.tk.call('font', 'configure', window.overlay.rows[0]['original'].cget('font'), '-size')), 20)


if __name__ == '__main__':
    unittest.main()
