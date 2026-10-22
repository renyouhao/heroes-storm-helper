"""注入单屏指标并只创建屏幕外测试窗口；不操作系统显示设置或真实游戏。"""
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import font as tkfont
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hots_chat.app import build_controller
from hots_chat.models import AppEvent, ChatEvent
from hots_chat.paths import AppPaths
from hots_chat.ui.main_window import create_app
from hots_chat.ui.viewmodel import MessageView
from hots_chat.window_settings import WindowSettings


class OfflineOverlayNative:
    """只记录产品传出的原生显示契约，不调用原生激活或鼠标API。"""
    def __init__(self):
        self.editing_modes = []
        self.shows = 0

    def set_overlay_mode(self, window, editing):
        self.editing_modes.append(editing)

    def show_no_activate(self, window):
        self.shows += 1
        window.deiconify()


class SimulatedScreen:
    def __init__(self, root, width=1920, height=1080, scale=1):
        from hots_chat.display_runtime import TkDisplayRuntime
        self.set(width, height, scale)
        self.runtime = TkDisplayRuntime(root, dpi_reader=lambda root: self.dpi,
                                        work_areas=lambda: [self.area])

    def set(self, width, height, scale):
        self.dpi = round(96 * scale)
        self.area = (10000, 10000, 10000 + width, 10000 + height - round(40 * scale))

    def refresh(self):
        return self.runtime.refresh()


class AdaptiveDisplayFlowTests(unittest.TestCase):
    @contextmanager
    def app(self, *, mode='normal', width=1920, height=1080, scale=1, font_size=12):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            controller = build_controller(AppPaths(base / '.env', base / 'usage.db',
                                                   base / 'language.db', base / 'preferences.json'), True)
            root = tk.Tk()
            root.withdraw()
            # Tk默认wm maxsize取宿主1K屏幕；只解除本测试窗口的限幅，
            # 让注入的2K/4K尺寸能真正生效，不更改Windows显示设置。
            root.maxsize(3840, 2160)
            window = None
            native = OfflineOverlayNative()
            screen = SimulatedScreen(root, width, height, scale)
            settings = WindowSettings(mode=mode, x=10412, y=10140, overlay_x=10740,
                                      overlay_y=10200, font_size=font_size, overlay_font_size=32,
                                      overlay_width=280, overlay_original=True, hotkeys={},
                                      preferences_version=3, overlay_placement='custom',
                                      overlay_style='panel', overlay_timeout=15)
            try:
                # 即使产品意外绕过注入接口，也不会读真实屏幕工作区。
                with patch('hots_chat.ui.main_window.screen_bounds', side_effect=lambda: [screen.area]), \
                     patch('hots_chat.window_settings.screen_bounds', side_effect=lambda: [screen.area]), \
                     patch('hots_chat.ui.overlay_window.Win32Window', return_value=native):
                    window = create_app(root, controller, settings, display=screen)
                    # 只创建控件；不调用会夺取焦点的QuickInput.open。
                    window.quick_input._native = SimpleNamespace(detach_ime=lambda: None)
                    window.quick_input._clipboard = lambda text: self.fail('重排不得触发复制')
                    root.deiconify()
                    root.update()
                    window.pump()
                    yield root, window, settings, screen, native
            finally:
                if window is not None:
                    window.close()
                else:
                    controller.close()
                    root.destroy()

    def refresh_screen(self, root, window, screen, width, height, scale):
        screen.set(width, height, scale)
        # 使用真实事件泵的低频检测路径，不直接替产品调用布局方法。
        for _ in range(20):
            window.pump()
        root.update()

    def assert_inside(self, widget, parent, *, require_height=False):
        self.assertTrue(widget.winfo_ismapped(), str(widget) + ' 未显示')
        self.assertGreaterEqual(widget.winfo_rootx(), parent.winfo_rootx())
        self.assertGreaterEqual(widget.winfo_rooty(), parent.winfo_rooty())
        self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(),
                             parent.winfo_rootx() + parent.winfo_width())
        self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                             parent.winfo_rooty() + parent.winfo_height())
        if require_height:
            self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight())

    def assert_screen_fit(self, popup, screen, *, decorated=False):
        left, top, right, bottom = screen.area
        reserve_x = round(16 * screen.dpi / 96) if decorated else 0
        reserve_y = round(40 * screen.dpi / 96) if decorated else 0
        self.assertGreaterEqual(popup.winfo_x(), left)
        self.assertGreaterEqual(popup.winfo_y(), top)
        self.assertLessEqual(popup.winfo_x() + popup.winfo_width() + reserve_x, right)
        self.assertLessEqual(popup.winfo_y() + popup.winfo_height() + reserve_y, bottom)

    @staticmethod
    def multilingual_messages():
        return OrderedDict(('lang' + str(index), MessageView(
            ChatEvent('lang' + str(index), 0, '团队', '队友' + str(index), '00:01', text * 80),
            engine_translation='现在集合，留意支援 ping team 같이 가요 ' * 60))
            for index, text in enumerate(('请支援中路 ', 'Please regroup at objective ', '같이 가요 지원 부탁해요 ')))

    def test_injected_resolution_dpi_matrix_keeps_main_controls_and_popups_visible(self):
        for width, height in ((1920, 1080), (2560, 1440), (3840, 2160)):
            for scale in (1, 1.25, 1.5, 2):
                for mode in ('normal', 'compact'):
                    with self.subTest(resolution=(width, height), scale=scale, mode=mode), \
                         self.app(mode=mode, width=width, height=height, scale=scale, font_size=32) as fixture:
                        root, window, settings, screen, native = fixture
                        self.assert_screen_fit(root, screen, decorated=True)
                        for widget in (window.tree, window.input, window.preview):
                            self.assert_inside(widget, root)
                            self.assertGreater(widget.winfo_height(), 20)
                        for widget in window.action_row.winfo_children():
                            self.assert_inside(widget, root)
                            self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth())
                        actual_font = tkfont.Font(root=root, font=window.input.cget('font'))
                        self.assertEqual(actual_font.cget('size'), 32)
                        self.assertGreaterEqual(window.input.winfo_height(), actual_font.metrics('linespace'))
                        if mode == 'compact':
                            continue
                        overlay, quick = window.overlay, window.quick_input
                        overlay.sync(self.multilingual_messages(), 0)
                        overlay.set_editing(True)
                        quick._create_window()
                        quick.window.deiconify()
                        quick.text.set('请支援中路，等待队伍集合。')
                        for status in ('正在翻译', '翻译失败，请重试。' * 100, '译文已就绪'):
                            quick.status.set(status)
                            root.update()
                            self.assert_screen_fit(quick.window, screen, decorated=True)
                            self.assert_inside(quick.entry, quick.window, require_height=True)
                            self.assert_inside(quick.status_label, quick.window, require_height=True)
                            self.assertEqual(quick.text.get(), '请支援中路，等待队伍集合。')
                        self.assert_screen_fit(overlay.window, screen)
                        self.assert_inside(overlay.done, overlay.window, require_height=True)
                        for row in overlay.rows:
                            for name in ('translation', 'original'):
                                self.assert_inside(row[name], overlay.window, require_height=True)
                                self.assertTrue(row[name].cget('text'))

    def test_round_trip_restores_manual_1k_positions_after_temporary_clamping(self):
        with self.app() as (root, window, settings, screen, native):
            self.assertEqual((settings.x, settings.y), (10412, 10140), '首次1K启动保留原位置')
            window.overlay.sync(self.multilingual_messages(), 0)
            window.overlay.set_editing(True)
            root.update()
            # 在本测试窗口中模拟用户移动，和显示变化触发的自动Configure区分开。
            root.geometry('+10432+10150')
            root.update()
            root.geometry('+10412+10140')
            root.update()
            overlay = window.overlay
            overlay._start_drag(SimpleNamespace(x_root=10740, y_root=10200))
            overlay._drag(SimpleNamespace(x_root=10750, y_root=10210))
            overlay._end_drag(None)
            root.update()
            initial = (settings.x, settings.y, settings.overlay_x, settings.overlay_y)
            anchors = (settings.main_anchor, settings.overlay_anchor)
            self.assertIsNotNone(anchors[0])
            self.assertIsNotNone(anchors[1])
            for width, height, scale, expected_size in (
                    (3840, 2160, 2, (2160, 1440)),
                    (2560, 1440, 1.25, (1350, 900)),
                    (1920, 1080, 2, (1888, 920)),
                    (1920, 1080, 1, (1080, 720))):
                with self.subTest(resolution=(width, height), scale=scale):
                    self.refresh_screen(root, window, screen, width, height, scale)
                    self.assertEqual((root.winfo_width(), root.winfo_height()), expected_size,
                                     '事件泵必须实际应用新DPI及工作区尺寸')
                    self.assert_screen_fit(root, screen, decorated=True)
                    self.assert_screen_fit(overlay.window, screen)
                    self.assertEqual((settings.main_anchor, settings.overlay_anchor), anchors,
                                     '自动重排或夹紧不能覆盖用户相对位置')
            returned = (settings.x, settings.y, settings.overlay_x, settings.overlay_y)
            for actual, expected in zip(returned, initial):
                self.assertLessEqual(abs(actual - expected), 1)

    def test_display_changes_preserve_history_chinese_drafts_and_translated_preview(self):
        with self.app() as (root, window, settings, screen, native):
            window.controller._source_emit(AppEvent('chat', 0, ChatEvent('history', 0, '团队', '旧队友', '00:01', 'hello')))
            window.pump()
            window.controller._source_emit(AppEvent('generation', 1, '新连接'))
            window.controller._source_emit(AppEvent('chat', 1, ChatEvent('current', 1, '团队', '新队友', '00:02', '같이 가요')))
            window.pump()
            window.input.insert('1.0', '请等我复活后一起进攻。')
            window.input.edit_modified(False)
            root.update()
            window.vm.copy_text = '부활 후 함께 공격해요.'
            window.vm.expected_job = 'pending-manual-job'
            window.vm.manual_provenance = '离线测试'
            window.render_manual_preview()
            quick = window.quick_input
            quick._create_window()
            quick.text.set('请支援，保留这个尚未翻译的输入。')
            history = [(key, message.original, message.event.generation)
                       for key, message in window.vm.messages.items()]
            rows = [(key, window.tree.item(key, 'values')) for key in window.tree.get_children()]
            for width, height, scale in ((3840, 2160, 2), (2560, 1440, 1.5), (1920, 1080, 1)):
                self.refresh_screen(root, window, screen, width, height, scale)
                self.assertEqual([(key, message.original, message.event.generation)
                                  for key, message in window.vm.messages.items()], history)
                self.assertEqual([(key, window.tree.item(key, 'values')) for key in window.tree.get_children()], rows)
                self.assertEqual(window.input.get('1.0', 'end-1c'), '请等我复活后一起进攻。')
                self.assertEqual(window.preview.get('1.0', 'end-1c'), '부활 후 함께 공격해요.')
                self.assertEqual(window.vm.expected_job, 'pending-manual-job')
                self.assertEqual(window.vm.manual_provenance, '离线测试')
                self.assertEqual(quick.text.get(), '请支援，保留这个尚未翻译的输入。')

    def test_relayout_preserves_overlay_deadline_and_native_no_activation_contract(self):
        with self.app() as (root, window, settings, screen, native):
            overlay = window.overlay
            now = [100.]
            overlay._clock = lambda: now[0]
            overlay.sync(self.multilingual_messages(), 0)
            root.update()
            timer, expiry, shows = overlay._timer, overlay.history.next_expiry, native.shows
            self.assertEqual(expiry, 115.)
            for width, height, scale in ((3840, 2160, 2), (1920, 1080, 1.25), (2560, 1440, 1.5)):
                now[0] += 4
                self.refresh_screen(root, window, screen, width, height, scale)
                self.assertEqual(overlay.history.next_expiry, 115.)
                self.assertEqual(overlay._timer, timer, '重排不能重置浮窗TTL')
                self.assertEqual(native.shows, shows, '已显示浮窗不能重走显示或激活流程')
                self.assertTrue(all(mode is False for mode in native.editing_modes),
                                '普通展示重排不能进入接收鼠标的调整模式')
                self.assertFalse(root.tk.getboolean(overlay.window.cget('takefocus')))
                self.assertFalse(root.tk.getboolean(overlay.done.cget('takefocus')))
            now[0] = 115.
            overlay._expire()
            self.assertFalse(overlay.history.entries)
            self.assertIsNone(overlay._timer)
            self.assertEqual(overlay.window.state(), 'withdrawn')
            self.assertNotIn(timer, root.tk.call('after', 'info'))


if __name__ == '__main__':
    unittest.main()
