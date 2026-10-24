"""圆角容器的实际Tk几何和生命周期；仅创建隐藏的自有窗口。"""
import subprocess
import sys
import textwrap
import tkinter as tk
from tkinter import ttk
import unittest

from hots_chat.layout import DisplayMetrics
from hots_chat.ui.rounded_panel import RoundedPanel
from hots_chat.ui.theme import theme_colors


class RoundedPanelTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.geometry('540x380+10000+10000')
        self.addCleanup(self.close_root)

    def close_root(self):
        self.root.update_idletasks()
        self.root.destroy()

    def panel(self, **options):
        panel = RoundedPanel(self.root, settings='graphite', **options)
        panel.place(x=0, y=0, width=320, height=140)
        self.root.update_idletasks()
        # withdraw下Tk不派发可见窗口的Configure；使用真实布局后的尺寸重绘。
        panel._schedule()
        self.root.update_idletasks()
        return panel

    def surface(self, panel):
        items = panel.canvas.find_withtag('surface')
        self.assertEqual(len(items), 1)
        return items[0]

    def test_default_radius_is_twelve_and_preserves_surface_body(self):
        panel = self.panel()
        item = self.surface(panel)
        self.assertEqual(panel.canvas.coords(item)[:2], [13., 1.])
        self.assertIsInstance(panel.body, ttk.Frame)
        self.assertEqual(panel.body.cget('style'), 'Surface.TFrame')
        self.assertEqual(panel.canvas.itemcget(item, 'fill'), theme_colors('lavender')['surface'])
        self.assertEqual(panel.canvas.cget('background'), theme_colors('lavender')['background'])

    def test_field_shell_has_ten_pixel_corners_and_configurable_theme_roles(self):
        panel = self.panel(radius=10, surface='field', background='surface')
        item = self.surface(panel)
        self.assertEqual(panel.canvas.coords(item)[:2], [11., 1.])
        self.assertEqual(panel.body.cget('style'), 'Field.TFrame')
        self.assertEqual(panel.canvas.itemcget(item, 'fill'), theme_colors('lavender')['field'])
        self.assertEqual(panel.canvas.cget('background'), theme_colors('lavender')['surface'])
        panel.apply_settings('mint')
        self.root.update_idletasks()
        self.assertEqual(panel.canvas.find_withtag('surface'), (item,))
        self.assertEqual(panel.canvas.itemcget(item, 'fill'), theme_colors('lavender')['field'])
        self.assertEqual(panel.canvas.cget('background'), theme_colors('lavender')['surface'])

    def test_focus_border_changes_once_and_unchanged_focus_schedules_nothing(self):
        panel = self.panel(surface='field', background='surface')
        item = self.surface(panel)
        self.assertEqual(panel.canvas.itemcget(item, 'outline'), theme_colors('lavender')['border'])
        panel.set_focused(True)
        pending = panel._pending
        self.assertIsNotNone(pending)
        for _ in range(20):panel.set_focused(True)
        self.assertEqual(panel._pending, pending)
        self.root.update_idletasks()
        self.assertEqual(panel.canvas.itemcget(item, 'outline'), theme_colors('lavender')['accent'])
        for _ in range(20):panel.set_focused(True)
        self.assertIsNone(panel._pending)
        panel.set_focused(False)
        self.root.update_idletasks()
        self.assertEqual(panel.canvas.itemcget(item, 'outline'), theme_colors('lavender')['border'])
        self.assertEqual(panel.canvas.find_all(), (item,))

    def test_idle_draws_merge_and_reuse_the_single_polygon(self):
        panel = self.panel()
        item = self.surface(panel)
        for _ in range(20):panel._schedule()
        pending = panel._pending
        self.assertEqual(tuple(self.root.tk.call('after', 'info')), (pending,))
        self.root.update_idletasks()
        for _ in range(5):
            panel._schedule()
            self.root.update_idletasks()
            self.assertEqual(panel.canvas.find_all(), (item,))
            self.assertIsNone(panel._pending)
        self.assertEqual(tuple(self.root.tk.call('after', 'info')), ())

    def test_fit_height_scales_padding_and_radius_once(self):
        panel = self.panel(radius=10, padding=8, fit_height=True)
        ttk.Label(panel.body, text='第一行\n第二行').pack()
        panel.apply_settings('graphite', DisplayMetrics((10000, 10000, 11920, 11080), 2))
        self.root.update_idletasks()
        item = self.surface(panel)
        self.assertEqual(panel.canvas.coords(item)[:2], [21., 1.])
        self.assertEqual(int(panel.body.place_info()['x']), 16)
        self.assertEqual(int(panel.body.place_info()['y']), 16)
        self.assertEqual(int(panel.body.place_info()['height']), panel.body.winfo_reqheight())
        self.assertEqual(int(panel.canvas.cget('height')), panel.body.winfo_reqheight() + 32)

    def test_destroy_cancels_pending_focus_and_is_idempotent(self):
        panel = self.panel(radius=10, surface='field', background='surface')
        panel.set_focused(True)
        self.assertIsNotNone(panel._pending)
        panel.destroy()
        panel.destroy()
        panel.set_focused(False)
        self.assertIsNone(panel._pending)
        self.assertEqual(tuple(self.root.tk.call('after', 'info')), ())


class RoundedPanelResourceTests(unittest.TestCase):
    def test_default_and_field_shells_release_before_worker_gc(self):
        script = textwrap.dedent('''
            import gc
            import threading
            import tkinter as tk
            from tkinter import ttk
            import weakref
            from hots_chat.ui.rounded_panel import RoundedPanel

            def create_and_destroy(options):
                root = tk.Tk()
                root.withdraw()
                panel = RoundedPanel(root, settings='graphite', **options)
                panel.value = tk.StringVar(panel, value='offline draft')
                ttk.Entry(panel.body, textvariable=panel.value).pack()
                panel.set_focused(True)
                root.update_idletasks()
                references = weakref.ref(root), weakref.ref(panel)
                root.destroy()
                panel.destroy()
                return references

            gc.collect()
            for options in ({}, dict(radius=10, surface='field', background='surface')):
                references = create_and_destroy(options)
                assert all(reference() is None for reference in references), 'Tk references retained'
                worker = threading.Thread(target=gc.collect)
                worker.start()
                worker.join(5)
                assert not worker.is_alive(), 'worker GC did not finish'
        ''')
        result = subprocess.run([sys.executable, '-c', script], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
