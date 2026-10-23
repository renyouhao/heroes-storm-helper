"""主题图片使用隐藏测试 Tk；不连接游戏或操作用户窗口。"""
import gc
import subprocess
import sys
import textwrap
import tkinter as tk
from tkinter import ttk
import unittest

from hots_chat.ui.rounded_panel import RoundedPanel
from hots_chat.ui import theme
from hots_chat.ui.theme import apply_theme, theme_colors


def layout_elements(layout):
    for name, options in layout:
        yield name
        yield from layout_elements(options.get('children', ()))


def close_root(root):
    root.update_idletasks()
    root.destroy()


class CheckbuttonThemeTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(close_root, self.root)
        self.root.tk.call('tk', 'scaling', 96 / 72)

    def test_every_surface_replaces_clam_cross_with_our_image_indicator(self):
        style = apply_theme(self.root, 'moonlight')
        for prefix in ('', 'Overlay.', 'Surface.', 'Panel.', 'Toolbar.'):
            with self.subTest(prefix=prefix):
                elements = set(layout_elements(style.layout(prefix + 'TCheckbutton')))
                self.assertNotIn('Checkbutton.indicator', elements)
                self.assertTrue(any(name.startswith('Hots.') and name.endswith('.indicator')
                                    for name in elements), elements)
                self.assertIn('Checkbutton.label', elements)

    def test_selected_image_has_the_two_strokes_of_a_tick_without_a_cross(self):
        apply_theme(self.root, 'moonlight')
        assets = getattr(self.root, '_hots_checkbox_assets', {})
        self.assertIn('main', assets, 'clam 默认图元没有真正的勾形图片')
        image = assets['main'].images['selected']
        mark = (255, 255, 255)
        # 20px 图片中，勾的左短笔和右长笔；X 的左上角应保持底色。
        self.assertEqual(image.width(), 20)
        for x, y in ((6, 10), (9, 13), (14, 7)):
            self.assertEqual(image.get(x, y), mark, (x, y))
        self.assertNotEqual(image.get(6, 6), mark)
        self.assertNotEqual(image.get(14, 14), mark)
        alternate = assets['main'].images['alternate']
        self.assertEqual(alternate.get(10, 10), mark)
        self.assertNotEqual(alternate.get(9, 13), mark)
        self.assertNotEqual(assets['main'].images['disabled_selected'].get(9, 13),
                            assets['main'].images['disabled_selected'].get(6, 6))
        self.assertTrue(image.transparency_get(0, 0))
        self.assertTrue(assets['main'].images['normal'].transparency_get(1, 10))
        self.assertFalse(assets['main'].images['focus'].transparency_get(1, 10))
        self.assertNotEqual(assets['main'].images['pressed_selected'].get(6, 6), image.get(6, 6))

    def test_native_invoke_disabled_and_space_binding_keep_boolean_state(self):
        apply_theme(self.root, 'graphite')
        selected = tk.BooleanVar(master=self.root, value=False)
        check = ttk.Checkbutton(self.root, text='启用', variable=selected)
        check.invoke()
        self.assertTrue(selected.get())
        check.state(['disabled'])
        check.invoke()
        self.assertTrue(selected.get())
        check.state(['!disabled'])
        # 直接调用原生 Space 类绑定；隐藏 Tk 不需要抢占用户键盘焦点。
        binding = self.root.tk.call('bind', 'TCheckbutton', '<Key-space>')
        self.assertIn('%W', binding)
        self.root.tk.eval(binding.replace('%W', str(check)))
        self.assertFalse(selected.get())
        check.state(['alternate', 'focus', 'pressed'])
        apply_theme(self.root, 'mint')
        self.assertTrue(check.instate(['alternate', 'focus', 'pressed']))
        self.assertFalse(selected.get())

    def test_switching_theme_and_dpi_reuses_images_and_elements(self):
        style = apply_theme(self.root, 'moonlight')
        image_names = set(self.root.tk.call('image', 'names'))
        elements = set(style.element_names())
        for index, name in enumerate(('graphite', 'mint', 'moonlight') * 4):
            self.root.tk.call('tk', 'scaling', (1 + index % 3 * .5) * 96 / 72)
            apply_theme(self.root, name)
            gc.collect()
            self.assertEqual(set(self.root.tk.call('image', 'names')), image_names)
            self.assertEqual(set(style.element_names()), elements)
        assets = getattr(self.root, '_hots_checkbox_assets', {})
        self.assertIn('main', assets)
        self.assertGreater(assets['main'].images['selected'].width(), 20)

    def test_image_lifetimes_are_independent_across_tk_interpreters(self):
        apply_theme(self.root, 'moonlight')
        other = tk.Tk()
        other.withdraw()
        try:
            apply_theme(other, 'graphite')
            assets = getattr(other, '_hots_checkbox_assets', {})
            self.assertIn('main', assets)
            image = assets['main'].images['selected']
            self.assertIs(image.tk, other.tk)
            first = self.root._hots_checkbox_assets['main'].images['selected']
            before = first.get(9, 13)
            apply_theme(other, 'mint')
            self.assertEqual(first.get(9, 13), before)
        finally:
            close_root(other)
        self.assertEqual(first.get(9, 13), before)

    def test_dpi_refresh_resizes_existing_assets_without_reapplying_widget_colors(self):
        apply_theme(self.root, 'moonlight')
        refresh = getattr(theme, 'refresh_theme_scale', None)
        self.assertTrue(callable(refresh), 'DPI 分支需要轻量图片刷新入口')
        self.root.configure(background='#123456')
        before = set(self.root.tk.call('image', 'names'))
        self.root.tk.call('tk', 'scaling', 8 / 3)
        refresh(self.root, 'moonlight')
        self.assertEqual(self.root._hots_checkbox_assets['main'].images['selected'].width(), 40)
        self.assertEqual(set(self.root.tk.call('image', 'names')), before)
        self.assertEqual(self.root.cget('background'), '#123456')


class RoundedPanelRenderingTests(unittest.TestCase):
    def test_cached_card_destroy_is_idempotent_after_its_owner_is_destroyed(self):
        from hots_chat.ui.talent_panel import TalentPanel
        from hots_chat.window_settings import WindowSettings
        from hots_chat.layout import DisplayMetrics
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        owner = TalentPanel(root, settings=WindowSettings(),
                            metrics=DisplayMetrics((10000, 10000, 11920, 11080), 1),
                            on_toggle=lambda enabled: None)
        card = owner.card
        owner.destroy()
        self.assertIsNone(card.master)
        card.destroy()
        card.destroy()

    def test_destroyed_panel_releases_child_caches_before_worker_gc(self):
        script = textwrap.dedent('''
            import gc
            import threading
            import tkinter as tk
            import weakref
            from hots_chat.ui.rounded_panel import RoundedPanel
            refs = []
            for _ in range(5):
                root = tk.Tk()
                root.withdraw()
                panel = RoundedPanel(root, settings='graphite')
                panel.pack()
                root.update_idletasks()
                refs.extend((weakref.ref(root), weakref.ref(panel)))
                root.destroy()
                del panel, root
            if any(ref() is not None for ref in refs):
                gc.collect()
                raise SystemExit('RoundedPanel retained destroyed widgets')
            worker = threading.Thread(target=gc.collect)
            worker.start()
            worker.join()
            print('worker gc completed')
        ''')
        result = subprocess.run([sys.executable, '-X', 'faulthandler', '-c', script],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'worker gc completed')
        self.assertFalse(result.stderr, result.stderr)

    def test_unchanged_size_does_not_replace_the_canvas_surface(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(close_root, root)
        apply_theme(root, 'moonlight')
        panel = RoundedPanel(root, settings='moonlight')
        panel.pack()
        root.update_idletasks()
        panel._draw()
        before = panel.canvas.find_withtag('surface')
        self.assertEqual(len(before), 1)
        for _ in range(10):
            panel._schedule()
            root.update_idletasks()
        self.assertEqual(panel.canvas.find_withtag('surface'), before)
        panel.apply_settings('graphite')
        root.update_idletasks()
        self.assertEqual(panel.canvas.itemcget(before[0], 'fill'), theme_colors('graphite')['surface'])


class CheckboxResourceLifetimeTests(unittest.TestCase):
    def test_destroy_releases_assets_even_when_callers_retain_image_references(self):
        root = tk.Tk()
        root.withdraw()
        apply_theme(root, 'graphite')
        assets = root._hots_checkbox_assets
        image = assets['main'].images['selected']
        root.update_idletasks()
        root.destroy()
        self.assertFalse(assets, 'Tk 销毁时应同步清空主题图片资产')
        self.assertIsNone(image.name, '已销毁图片不能留下需要异步 Tcl 调用的名称')
        self.assertIsNone(image.tk, '外部保留的图片引用不能继续持有 Tk 解释器')

    def test_destroyed_roots_are_released_before_worker_gc(self):
        script = textwrap.dedent('''
            import gc
            import threading
            import tkinter as tk
            import weakref
            from hots_chat.ui.theme import apply_theme
            roots = []
            for _ in range(5):
                root = tk.Tk()
                root.withdraw()
                apply_theme(root, 'graphite')
                root.update_idletasks()
                roots.append(weakref.ref(root))
                root.destroy()
                del root
            if any(ref() is not None for ref in roots):
                # 旧实现安全地在主线程清理后报错，避免回归时让测试进程崩溃。
                gc.collect()
                raise SystemExit('theme retained destroyed Tk roots')
            worker = threading.Thread(target=gc.collect)
            worker.start()
            worker.join()
            print('worker gc completed')
        ''')
        result = subprocess.run([sys.executable, '-X', 'faulthandler', '-c', script],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'worker gc completed')
        self.assertFalse(result.stderr, result.stderr)


if __name__ == '__main__':
    unittest.main()
