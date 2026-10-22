"""开箱页面只使用隐藏的屏幕外 Tk；不映射窗口或调用真实原生接口。"""
import tkinter as tk
from tkinter import font as tkfont, ttk
import unittest
from unittest.mock import Mock, patch

from hots_chat.layout import DisplayMetrics
from hots_chat.ui.theme import overlay_colors, theme_colors
from hots_chat.window_settings import WindowSettings

try:
    from hots_chat.ui.loot_panel import LootPanel
except ModuleNotFoundError:
    LootPanel = None


class OfflineNative:
    """保留样式/显示的调用次序，窗口始终隐藏，绝不激活桌面。"""
    def __init__(self):
        self.calls = []

    def set_overlay_mode(self, window, editing=False):
        self.calls.append(('mode', window, editing))

    def show_no_activate(self, window):
        self.calls.append(('show', window))


class LootPanelTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(LootPanel, '需要独立的开箱助手页面组件')
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.geometry('540x380+10000+10000')
        self.addCleanup(self.destroy_root)
        self.settings = WindowSettings()
        self.metrics = DisplayMetrics((10000, 10000, 11920, 11000), 1)
        self.native = OfflineNative()
        self.prepare = Mock()
        self.cancel = Mock()

    def destroy_root(self):
        self.root.update_idletasks()
        self.root.destroy()

    def panel(self, *, native=None):
        panel = LootPanel(self.root, on_prepare=self.prepare, on_cancel=self.cancel,
                          settings=self.settings, metrics=self.metrics,
                          native=self.native if native is None else native)
        panel.place(x=0, y=0, width=540, height=380)
        self.addCleanup(panel.close)
        self.root.update_idletasks()
        return panel

    def test_idle_never_creates_native_or_overlay_and_prepare_is_explicit(self):
        with patch('hots_chat.ui.loot_panel.Win32Window',
                   side_effect=AssertionError('空闲页面不能访问原生窗口')):
            panel = LootPanel(self.root, on_prepare=self.prepare, on_cancel=self.cancel,
                              settings=self.settings, metrics=self.metrics)
            self.addCleanup(panel.close)
            panel.apply_settings(self.settings, self.metrics)
            panel.update_status('idle')
            self.assertIsNone(panel.overlay_window)
            self.prepare.assert_not_called()
            self.assertFalse(panel.prepare_button.instate(['disabled']))
            panel.prepare_button.invoke()
            self.prepare.assert_called_once_with()
        self.cancel.assert_not_called()

    def test_preparing_disables_duplicate_prepare_without_showing_overlay(self):
        panel = self.panel()
        panel.update_status('preparing', '正在检查游戏窗口')
        self.assertTrue(panel.prepare_button.instate(['disabled']))
        self.assertIsNone(panel.overlay_window)
        self.assertIn('正在检查游戏窗口', panel.status_detail.get())
        panel.prepare_button.invoke()
        self.prepare.assert_not_called()
        self.assertEqual(self.native.calls, [])

    def test_ready_running_paused_keep_cancel_and_space_guidance(self):
        panel = self.panel()
        for state, title, instruction in (
            ('ready', '待开始', 'Space 开始'),
            ('running', '正在开箱', 'Space 暂停'),
            ('paused', '已暂停', 'Space 继续'),
        ):
            with self.subTest(state=state):
                panel.update_status(state)
                self.root.update_idletasks()
                self.assertEqual(panel.state, state)
                self.assertIn(title, panel.status_text.get())
                self.assertIn(instruction, panel.status_detail.get())
                self.assertEqual(panel.cancel_button.winfo_manager(), 'grid')
                self.assertEqual(panel.prepare_button.winfo_manager(), '')
                self.assertFalse(panel.cancel_button.instate(['disabled']))
                panel.cancel_button.invoke()
                self.assertIsNotNone(panel.overlay_window)
                self.assertEqual(panel.overlay_window._hots_theme_scope, 'overlay')
        self.assertEqual(self.cancel.call_count, 3)
        mode = self.native.calls[0]
        self.assertEqual((mode[0], mode[2]), ('mode', False))
        self.assertEqual(self.native.calls[1][0], 'show')
        self.assertEqual(panel.overlay_window.state(), 'withdrawn', '离线测试不得映射浮窗')

    def test_idle_and_error_hide_existing_overlay_and_allow_prepare_again(self):
        panel = self.panel()
        panel.update_status('ready')
        window = panel.overlay_window
        for state, detail in (('idle', '本次模式已结束'), ('error', '未找到游戏窗口，请先启动游戏')):
            with self.subTest(state=state):
                panel.update_status(state, detail)
                self.assertIs(panel.overlay_window, window)
                self.assertEqual(window.state(), 'withdrawn')
                self.assertFalse(panel._overlay_visible)
                self.assertFalse(panel.prepare_button.instate(['disabled']))
                self.assertEqual(panel.prepare_button.winfo_manager(), 'grid')
                self.assertIn(detail, panel.status_detail.get())

    def test_theme_and_metrics_refresh_preserve_active_status_and_anchor(self):
        panel = self.panel()
        panel.update_status('paused')
        window = panel.overlay_window
        metrics = DisplayMetrics((10000, 10000, 12560, 11360), 2)
        accents = set()
        for name in ('moonlight', 'graphite', 'mint'):
            with self.subTest(theme=name):
                self.settings.color_theme = name
                # 隐藏的 Tk 窗口不会提交后续位置；验证传给窗口管理器的几何请求。
                with patch.object(window, 'geometry', wraps=window.geometry) as geometry:
                    panel.apply_settings(self.settings, metrics)
                requested = geometry.call_args.args[0]
                size, x, y = requested.split('+')
                width, height = map(int, size.split('x'))
                self.root.update_idletasks()
                self.assertIs(panel.overlay_window, window)
                self.assertIn('Space 继续', panel.status_detail.get())
                self.assertEqual(panel.scroll_canvas.cget('background'), theme_colors(name)['background'])
                self.assertEqual(panel._overlay.canvas.itemcget('surface', 'fill'), overlay_colors(name)['surface'])
                accents.add(panel._overlay.canvas.itemcget('title', 'fill'))
                self.assertAlmostEqual(int(y) + height, 11170, delta=1)
                self.assertAlmostEqual(int(x) + width / 2, 11280, delta=1)
        self.assertEqual(len(accents), 3)

    def test_large_font_narrow_view_scrolls_while_primary_action_stays_inside(self):
        self.root.tk.call('tk', 'scaling', 8 / 3)
        self.settings.font_size = 32
        self.metrics = DisplayMetrics((10000, 10000, 11920, 11000), 2)
        panel = self.panel()
        self.assertEqual(panel._body_font.cget('size'), 32, '正数字号不能再次乘 DPI')
        for state, button in (('idle', panel.prepare_button), ('paused', panel.cancel_button)):
            panel.update_status(state)
            self.root.update_idletasks()
            self.assertGreater(button.winfo_height(), 40)
            self.assertGreaterEqual(button.winfo_x(), 0)
            self.assertGreaterEqual(button.winfo_y(), 0)
            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), panel.winfo_rootx() + 540)
            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), panel.winfo_rooty() + 380)
            font = tkfont.Font(root=self.root, font=ttk.Style(self.root).lookup(button.cget('style'), 'font'))
            self.assertLess(font.measure(button.cget('text')), button.winfo_width())
        self.assertLess(panel.scroll_canvas.yview()[1], 1)
        panel.scroll_canvas.yview_moveto(1)
        self.assertGreater(panel.scroll_canvas.yview()[0], 0)

    def test_close_destroys_only_owned_overlay_and_detaches_callbacks(self):
        panel = self.panel()
        panel.update_status('ready')
        window = panel.overlay_window
        panel.close()
        panel.close()
        self.root.update_idletasks()
        self.assertTrue(self.root.winfo_exists())
        self.assertFalse(window.winfo_exists())
        panel.prepare_button.invoke()
        panel.cancel_button.invoke()
        panel.update_status('running')
        self.prepare.assert_not_called()
        self.cancel.assert_not_called()
        self.assertFalse(window.winfo_exists())

    def test_unknown_state_is_rejected_without_changing_current_status(self):
        panel = self.panel()
        panel.update_status('paused')
        before = panel.status_text.get(), panel.status_detail.get()
        with self.assertRaises(ValueError):
            panel.update_status('unknown')
        self.assertEqual((panel.status_text.get(), panel.status_detail.get()), before)

    def test_parent_destruction_cleans_overlay_without_stale_widget_callbacks(self):
        panel = self.panel()
        panel.update_status('ready')
        failures = []
        self.root.report_callback_exception = lambda *failure: failures.append(failure)
        window = panel.overlay_window
        panel.destroy()
        self.root.update_idletasks()
        self.assertEqual(failures, [])
        self.assertFalse(window.winfo_exists())

    def test_transparency_failure_propagates_without_leaking_toplevel(self):
        panel = self.panel()
        children = set(self.root.winfo_children())
        with patch.object(tk.Toplevel, 'attributes', side_effect=tk.TclError('透明背景失败')):
            with self.assertRaisesRegex(tk.TclError, '透明背景失败'):
                panel.update_status('ready')
        self.assertEqual(set(self.root.winfo_children()), children)
        self.assertIsNone(panel.overlay_window)
        self.assertFalse(panel._overlay_visible)
        self.assertTrue(self.root.winfo_exists())

    def test_native_style_or_show_failure_propagates_and_destroys_unusable_overlay(self):
        for method in ('set_overlay_mode', 'show_no_activate'):
            with self.subTest(method=method):
                panel = self.panel()
                children = set(self.root.winfo_children())
                with patch.object(self.native, method, side_effect=OSError('原生样式失败')):
                    with self.assertRaisesRegex(OSError, '原生样式失败'):
                        panel.update_status('ready')
                self.assertEqual(set(self.root.winfo_children()), children)
                self.assertIsNone(panel.overlay_window)
                self.assertFalse(panel._overlay_visible)
                panel.update_status('error', '无法显示开箱状态，本次模式已结束')
                self.assertFalse(panel.prepare_button.instate(['disabled']))
                panel.close()
                panel.destroy()


if __name__ == '__main__':
    unittest.main()
