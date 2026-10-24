"""圆角图元、原生交互和资源生命周期；只创建隐藏的离线 Tk。"""
import gc
import math
import subprocess
import sys
import textwrap
import tkinter as tk
from tkinter import ttk
import unittest

from hots_chat.ui.theme import apply_theme, overlay_colors, refresh_theme_scale, theme_colors


def elements(layout):
    for name, options in layout:
        yield name
        yield from elements(options.get('children', ()))


def rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def close_root(root):
    root.update_idletasks()
    root.destroy()


class RoundedControlTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.tk.call('tk', 'scaling', 96 / 72)
        self.addCleanup(close_root, self.root)

    def assets(self):
        result = getattr(self.root, '_hots_rounded_assets', None)
        self.assertIsNotNone(result, '圆角必须由真实图片图元呈现')
        return result

    def test_button_and_field_layouts_install_rounded_images_with_native_content(self):
        style = apply_theme(self.root, 'moonlight')
        for name in ('TButton', 'Accent.TButton', 'Nav.TButton', 'Sidebar.TButton',
                     'Sidebar.Selected.TButton', 'Overlay.TButton', 'Overlay.Accent.TButton',
                     'Surface.TButton', 'Surface.Accent.TButton', 'Surface.Nav.TButton'):
            with self.subTest(style=name):
                layout = list(elements(style.layout(name)))
                self.assertTrue(layout[0].startswith('Hots.Rounded.'), layout)
                self.assertNotIn('Button.border', layout)
                self.assertTrue({'Button.label', 'Button.padding', 'Button.focus'} <= set(layout))
        for prefix in ('', 'Overlay.', 'Surface.'):
            for control in ('TEntry', 'TCombobox', 'TSpinbox'):
                with self.subTest(style=prefix + control):
                    layout = list(elements(style.layout(prefix + control)))
                    self.assertTrue(layout[0].startswith('Hots.Rounded.'), layout)
                    self.assertTrue(any(name.endswith('.textarea') for name in layout))
                    arrows = [name for name in layout if name.endswith(('downarrow', 'uparrow'))]
                    self.assertTrue(all(name.startswith('Hots.Rounded.') for name in arrows))
                    self.assertEqual(len(arrows), 0 if control == 'TEntry' else 1 if control == 'TCombobox' else 2)

    def test_corner_pixels_match_parents_and_semantic_surfaces_stay_distinct(self):
        apply_theme(self.root, 'graphite')
        assets = self.assets()
        main, game = theme_colors('graphite'), overlay_colors('graphite')
        for role, expected in (
                ('button', main['surface']), ('accent', main['accent']),
                ('nav', main['background']), ('sidebar', main['sidebar']),
                ('sidebar_selected', main['sidebar_hover']),
                ('overlay_button', game['surface']), ('overlay_accent', game['accent']),
                ('surface_button', main['surface']), ('surface_accent', main['accent']),
                ('surface_nav', main['background']),
                ('helper_accent', main['accent']), ('helper_cancel', main['surface']),
                ('field', main['field']), ('surface_field', main['field']), ('overlay_field', game['field'])):
            with self.subTest(role=role):
                image = assets.images[role + ':normal']
                self.assertFalse(image.transparency_get(0, 0))
                self.assertFalse(image.transparency_get(image.width() // 2, image.height() // 2))
                self.assertEqual(image.get(image.width() // 2, image.height() // 2), rgb(expected))
                self.assertEqual(image.width(), 36 if role.endswith('field') else 32)
        arrow = assets.images['main_down:normal']
        self.assertTrue(arrow.transparency_get(0, 0))
        self.assertFalse(arrow.transparency_get(arrow.width() // 2, arrow.height() // 2))

    def test_focus_disabled_hover_and_pressed_images_follow_existing_style_colors(self):
        style = apply_theme(self.root, 'moonlight')
        assets = self.assets()
        for state in ('normal', 'disabled', 'active', 'pressed', 'focus'):
            image = assets.images['button:' + state]
            states = () if state == 'normal' else (state,)
            self.assertEqual(image.get(image.width() // 2, image.height() // 2),
                             rgb(style.lookup('TButton', 'background', states)))
        image = assets.images['field:focus']
        self.assertEqual(image.get(image.width() // 2, 0),
                         rgb(style.lookup('TEntry', 'bordercolor', ('focus',))))
        image = assets.images['field:disabled']
        self.assertEqual(image.get(image.width() // 2, image.height() // 2),
                         rgb(style.lookup('TEntry', 'fieldbackground', ('disabled',))))
        self.assertEqual(style.lookup('Field.TFrame', 'background'), theme_colors('moonlight')['field'])
        self.assertEqual(str(style.lookup('TEntry', 'padding')), '6')

    def test_opaque_corner_composition_clears_the_ttk_square_background(self):
        style = apply_theme(self.root, 'graphite')
        assets = self.assets()
        colors = theme_colors('graphite')
        for role, background in (('accent', colors['background']),
                                 ('sidebar_selected', colors['sidebar']),
                                 ('surface_button', colors['surface']),
                                 ('surface_accent', colors['surface'])):
            for state in ('normal', 'disabled', 'active', 'pressed', 'focus'):
                with self.subTest(role=role, state=state):
                    image = assets.images[role + ':' + state]
                    self.assertFalse(image.transparency_get(0, 0))
                    self.assertEqual(image.get(0, 0), rgb(background))
        self.assertEqual(style.lookup('Surface.Accent.TButton', 'background'), colors['accent'])
        self.assertEqual(style.lookup('Surface.Accent.TButton', 'foreground'), colors['on_accent'])

    def test_nine_slice_center_avoids_per_pixel_tiling_without_inflating_minimum_size(self):
        style = apply_theme(self.root, 'moonlight')
        assets = self.assets()
        # Tk 8.6 image element 使用平铺而非拉伸；800×200 控件不应退化为十几万次绘制。
        for role, radius in (('button', 8), ('field', 10)):
            image = assets.images[role + ':normal']
            tile_width = image.width() - radius * 2
            tile_height = image.height() - radius * 2
            tiles = math.ceil((800 - radius * 2) / tile_width) * math.ceil((200 - radius * 2) / tile_height)
            self.assertLessEqual(tiles, 800, (role, tile_width, tile_height, tiles))
        style.configure('Tiny.TButton', font=('Arial', 1), padding=0)
        style.configure('Tiny.TEntry', padding=0)
        button = ttk.Button(self.root, text='x', style='Tiny.TButton')
        entry = ttk.Entry(self.root, width=1, font=('Arial', 1), style='Tiny.TEntry')
        self.assertLessEqual(button.winfo_reqheight(), 20)
        self.assertLessEqual(entry.winfo_reqheight(), 22)

    def test_native_button_keyboard_and_editable_readonly_controls_keep_behavior(self):
        apply_theme(self.root, 'moonlight')
        calls = []
        button = ttk.Button(self.root, text='执行', command=lambda: calls.append(True))
        button.invoke()
        binding = self.root.tk.call('bind', 'TButton', '<Key-space>')
        self.root.tk.eval(binding.replace('%W', str(button)))
        self.assertEqual(calls, [True, True])
        button.state(['disabled'])
        button.invoke()
        self.assertEqual(calls, [True, True])
        value = tk.StringVar(master=self.root)
        entry = ttk.Entry(self.root, textvariable=value)
        entry.insert(0, '中文 한글')
        entry.selection_range(0, 2)
        self.assertEqual(value.get(), '中文 한글')
        self.assertTrue(entry.selection_present())
        entry.state(['readonly'])
        entry.insert(0, '禁止')
        self.assertEqual(value.get(), '中文 한글')
        combo = ttk.Combobox(self.root, values=('韩语', '英语'), state='readonly')
        combo.current(1)
        self.assertEqual(combo.get(), '英语')
        spin = ttk.Spinbox(self.root, from_=1, to=5)
        spin.set('2')
        binding = self.root.tk.call('bind', 'TSpinbox', '<<Increment>>')
        self.root.tk.eval(binding.replace('%W', str(spin)))
        self.assertEqual(spin.get(), '3')
        self.assertEqual((button.winfo_class(), entry.winfo_class(), combo.winfo_class(), spin.winfo_class()),
                         ('TButton', 'TEntry', 'TCombobox', 'TSpinbox'))

    def test_existing_helper_style_prefixes_share_correct_accent_surface(self):
        style = apply_theme(self.root, 'mint')
        c = theme_colors('mint')
        style.configure('Repeat123.Accent.TButton', background=c['accent'], foreground=c['on_accent'])
        button = ttk.Button(self.root, text='保存', style='Repeat123.Accent.TButton')
        refresh_theme_scale(self.root, 'mint')
        layout = list(elements(style.layout(button.cget('style'))))
        self.assertIn('helper_accent', layout[0])
        self.assertEqual(style.lookup(button.cget('style'), 'background'), c['accent'])

    def test_realized_elements_keep_native_arrow_hit_names_and_disabled_press_guard(self):
        apply_theme(self.root, 'moonlight')
        # Tk 只有在 realize 后才计算 identify 的图元几何；此测试窗口始终透明且位于屏幕外。
        self.root.overrideredirect(True)
        self.root.attributes('-alpha', 0)
        self.root.geometry('260x120+10000+10000')
        combo = ttk.Combobox(self.root, values=('韩语', '英语'), state='readonly')
        spin = ttk.Spinbox(self.root, from_=1, to=5)
        combo.place(x=0, y=0, width=220, height=34)
        spin.place(x=0, y=40, width=220, height=34)
        self.root.deiconify()
        self.root.update()
        for widget in (combo, spin):
            points = ((20, 17), (206, 6), (210, 8), (214, 17), (210, 26), (206, 28))
            hits = {name: (x, y) for x, y in points if (name := widget.identify(x, y))}
            self.assertTrue(any(name.endswith('textarea') for name in hits), hits)
            self.assertTrue(any(name.endswith('downarrow') for name in hits), hits)
            if widget is spin:
                self.assertTrue(any(name.endswith('uparrow') for name in hits), hits)
            widget.state(['disabled'])
        combo.set('英语')
        spin.set('2')
        # 原生 Press 首先检查 disabled，不进入 focus/Post/重复按键路径。
        self.root.tk.call('ttk::combobox::Press', '', str(combo), 210, 17)
        self.root.tk.call('ttk::spinbox::Press', str(spin), 210, 8)
        self.assertEqual((combo.get(), spin.get()), ('英语', '2'))
        self.root.withdraw()

    def test_theme_and_dpi_updates_keep_image_and_element_counts_fixed(self):
        style = apply_theme(self.root, 'moonlight')
        images = set(self.root.tk.call('image', 'names'))
        element_names = set(style.element_names())
        for scale, theme in ((1.25, 'graphite'), (2, 'mint'), (1.5, 'moonlight'),
                             (.75, 'mint'), (4, 'graphite'), (1, 'moonlight')):
            self.root.tk.call('tk', 'scaling', scale * 96 / 72)
            apply_theme(self.root, theme)
            refresh_theme_scale(self.root, theme)
            gc.collect()
            self.assertEqual(set(self.root.tk.call('image', 'names')), images)
            self.assertEqual(set(style.element_names()), element_names)
            image = self.assets().images['field:normal']
            self.assertAlmostEqual((image.width() - 16) / 2 / scale, 10, delta=1)


class RoundedResourceTests(unittest.TestCase):
    def test_destroy_releases_images_before_background_collection(self):
        script = textwrap.dedent('''
            import gc, threading, tkinter as tk, weakref
            from tkinter import ttk
            from hots_chat.ui.theme import apply_theme
            refs = []
            for _ in range(3):
                root = tk.Tk(); root.withdraw()
                apply_theme(root, 'moonlight')
                assets = getattr(root, '_hots_rounded_assets', None)
                assert assets is not None, 'rounded asset pool is missing'
                image = assets.images['field:normal']
                entry = ttk.Entry(root)
                refs.append(weakref.ref(root))
                root.update_idletasks(); root.destroy()
                assert not assets.images
                assert image.name is None and image.tk is None
                del root, entry, assets, image
            if any(ref() is not None for ref in refs):
                gc.collect()
                raise SystemExit('destroyed Tk root retained')
            worker = threading.Thread(target=gc.collect)
            worker.start(); worker.join()
            print('worker gc completed')
        ''')
        result = subprocess.run([sys.executable, '-X', 'faulthandler', '-c', script],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'worker gc completed')
        self.assertFalse(result.stderr)


if __name__ == '__main__':
    unittest.main()
