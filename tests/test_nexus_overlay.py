"""聊天附近紧凑浮窗：只创建屏幕外 Tk 窗口，不接触游戏或用户配置。"""
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import fields
from types import SimpleNamespace
import tkinter as tk
from tkinter import font as tkfont
import unittest

from hots_chat.layout import DisplayMetrics
from hots_chat.models import ChatEvent
from hots_chat.ui import theme
from hots_chat.ui.overlay_window import OverlayWindow
from hots_chat.ui.viewmodel import MessageView
from hots_chat.window_settings import WindowSettings


class OfflineNative:
    def __init__(self):
        self.editing_modes = []
        self.rounded = []

    def set_overlay_mode(self, window, editing):
        self.editing_modes.append(editing)

    def show_no_activate(self, window):
        window.deiconify()

    def set_rounded_corners(self, window, radius):
        self.rounded.append((window.winfo_width(), window.winfo_height(), radius))


def chat(key, text='需要支援', original=None):
    event = ChatEvent(key, 1, '团队', '队友' + key, '1:00', original or key)
    return MessageView(event, engine_translation=text)


class NexusOverlayTests(unittest.TestCase):
    @contextmanager
    def overlay(self, *, placement='custom', scale=1, font_size=14, original=False):
        root = tk.Tk()
        root.withdraw()
        root.tk.call('tk', 'scaling', scale * 96 / 72)
        metrics = DisplayMetrics((10000, 10000, 11920, 11040), scale)
        settings = WindowSettings(overlay_x=10400, overlay_y=10200,
                                  overlay_font_size=font_size, overlay_width=280,
                                  overlay_original=original, preferences_version=3,
                                  overlay_style='panel', overlay_timeout=15)
        settings.overlay_placement = placement
        settings.overlay_bottom_anchor = None
        native = OfflineNative()
        now = [10.]
        overlay = OverlayWindow(root, settings, metrics=metrics, native=native,
                                clock=lambda: now[0])
        try:
            yield root, overlay, settings, native, now
        finally:
            overlay.close()
            root.destroy()

    @staticmethod
    def visible_rows(overlay):
        return [row for row in overlay.rows if row['frame'].winfo_manager()]

    @staticmethod
    def bottom(overlay):
        return overlay.window.winfo_y() + overlay.window.winfo_height()

    def test_only_latest_two_are_drawn_while_history_keeps_three(self):
        with self.overlay() as (root, overlay, settings, native, now):
            messages = OrderedDict((key, chat(key, text='消息' + key)) for key in 'abc')
            overlay.sync(messages, 1)
            root.update_idletasks()
            self.assertEqual(list(overlay.history.entries), ['a', 'b', 'c'])
            visible = self.visible_rows(overlay)
            self.assertEqual(len(visible), 2)
            self.assertIn('消息b', visible[0]['translation'].cget('text'))
            self.assertIn('消息c', visible[1]['translation'].cget('text'))

    def test_long_messages_stay_within_four_physical_lines_at_supported_scales(self):
        for scale in (1, 1.25, 1.5, 2):
            for font_size in (14, 32):
                for original in (False, True):
                    with self.subTest(scale=scale, font_size=font_size, original=original):
                        with self.overlay(scale=scale, font_size=font_size, original=original) as context:
                            root, overlay, settings, native, now = context
                            messages = OrderedDict((key, chat(key, text='支援中路 다음 전투 help ' * 40,
                                                              original='도와주세요 need help ' * 40))
                                                   for key in 'ab')
                            overlay.sync(messages, 1)
                            root.update_idletasks()
                            self.assertEqual(overlay._translation_font.cget('size'), font_size)
                            visible = self.visible_rows(overlay)
                            self.assertEqual(len(visible), 2)
                            for row in visible:
                                names = ('translation', 'original') if original else ('translation',)
                                for name in names:
                                    label = row[name]
                                    font = tkfont.Font(root=root, font=label.cget('font'))
                                    lines = 1 if original else 2
                                    self.assertLessEqual(label.winfo_reqheight(), font.metrics('linespace') * lines)
                                    self.assertTrue(label.cget('text').endswith('…'))

    def test_legacy_custom_top_is_preserved_then_content_grows_upward(self):
        with self.overlay() as (root, overlay, settings, native, now):
            messages = OrderedDict(a=chat('a'))
            overlay.sync(messages, 1)
            root.update_idletasks()
            self.assertEqual(overlay.window.winfo_y(), 10200)
            first_bottom = self.bottom(overlay)
            messages['b'] = chat('b', text='较长译文 ' * 80)
            overlay.sync(messages, 1)
            root.update_idletasks()
            self.assertEqual(self.bottom(overlay), first_bottom)
            self.assertLess(overlay.window.winfo_y(), 10200)
            messages['b'].engine_translation = '集合'
            overlay.sync(messages, 1)
            root.update_idletasks()
            self.assertEqual(self.bottom(overlay), first_bottom)

    def test_legacy_bottom_position_recovers_from_temporary_startup_clamp_before_migration(self):
        metrics = DisplayMetrics((10000, 10000, 11920, 11080), 1)
        # 旧浮窗高60、top=900：竖向锚点为900/(1080-60)。
        old_anchor = (400 / 1480, 900 / 1020)
        settings = WindowSettings(overlay_x=10400, overlay_y=10900, overlay_width=440,
                                  overlay_placement='custom', overlay_anchor=old_anchor,
                                  overlay_bottom_anchor=None, preferences_version=3,
                                  overlay_style='panel', overlay_font_size=14, overlay_timeout=15)
        settings.recover_overlay([metrics.work_area], scale=1)
        self.assertEqual(settings.overlay_y, 10780, '启动恢复暂按300高夹紧')
        self.assertEqual(settings.overlay_anchor, old_anchor)
        root = tk.Tk()
        root.withdraw()
        root.tk.call('tk', 'scaling', 96 / 72)
        self.addCleanup(root.destroy)
        overlay = OverlayWindow(root, settings, metrics=metrics, native=OfflineNative(), clock=lambda: 10.)
        self.addCleanup(overlay.close)
        self.assertIsNone(settings.overlay_bottom_anchor, '隐藏占位不得提前迁移底边')
        messages = OrderedDict(a=chat('a'))
        overlay.sync(messages, 1)
        root.update_idletasks()
        actual_height = overlay.window.winfo_height()
        expected_top = 10000 + round((1080 - actual_height) * old_anchor[1])
        self.assertEqual(overlay.window.winfo_y(), expected_top)
        first_bottom = self.bottom(overlay)
        bottom_anchor = (first_bottom - 10000) / 1080
        self.assertEqual(settings.overlay_bottom_anchor, bottom_anchor)
        messages['b'] = chat('b', text='长消息需要集合支援 ' * 50)
        overlay.sync(messages, 1)
        root.update_idletasks()
        self.assertEqual(self.bottom(overlay), first_bottom)
        root.tk.call('tk', 'scaling', 2 * 96 / 72)
        overlay.refresh_layout(DisplayMetrics((10000, 10000, 13840, 12160), 2))
        root.update_idletasks()
        self.assertEqual(self.bottom(overlay), 10000 + 2 * (first_bottom - 10000))
        self.assertEqual(settings.overlay_bottom_anchor, bottom_anchor)
        self.assertEqual(overlay.history.next_expiry, 25.)

    def test_restoring_legacy_preferences_after_preset_restores_vertical_anchor(self):
        metrics = DisplayMetrics((10000, 10000, 11920, 11080), 1)
        old_anchor = (400 / 1480, 900 / 1020)
        settings = WindowSettings(overlay_x=10400, overlay_y=10900, overlay_width=440,
                                  overlay_placement='custom', overlay_anchor=old_anchor,
                                  overlay_bottom_anchor=None, preferences_version=3,
                                  overlay_style='panel', overlay_font_size=14, overlay_timeout=15)
        settings.recover_overlay([metrics.work_area], scale=1)
        previous = {item.name: getattr(settings, item.name) for item in fields(settings)}
        root = tk.Tk()
        root.withdraw()
        root.tk.call('tk', 'scaling', 96 / 72)
        self.addCleanup(root.destroy)
        overlay = OverlayWindow(root, settings, metrics=metrics, native=OfflineNative(), clock=lambda: 10.)
        self.addCleanup(overlay.close)
        overlay.use_chat_preset()
        self.assertEqual(settings.overlay_placement, 'chat')
        # 模拟设置保存失败：恢复整个dataclass配置，再显式应用相同显示指标。
        for name, value in previous.items():
            setattr(settings, name, value)
        overlay.apply_settings(metrics)
        self.assertIsNone(settings.overlay_bottom_anchor)
        overlay.sync(OrderedDict(a=chat('a')), 1)
        root.update_idletasks()
        actual_height = overlay.window.winfo_height()
        expected_top = 10000 + round((1080 - actual_height) * old_anchor[1])
        self.assertEqual(overlay.window.winfo_y(), expected_top)
        self.assertEqual(overlay.window.winfo_x(), 10400)
        self.assertEqual(settings.overlay_bottom_anchor, (expected_top + actual_height - 10000) / 1080)
        self.assertEqual(overlay.history.next_expiry, 25.)

    def test_repeated_font_and_width_application_does_not_accumulate_position_drift(self):
        with self.overlay() as (root, overlay, settings, native, now):
            settings.overlay_y = 10400
            overlay.sync(OrderedDict(a=chat('a', text='需要集合后再进攻 ' * 40)), 1)
            root.update_idletasks()
            geometry = overlay.window.geometry()
            bottom = self.bottom(overlay)
            anchor = settings.overlay_bottom_anchor
            timer, deadline = overlay._timer, overlay.history.next_expiry
            fonts = (overlay._translation_font, overlay._original_font)
            for font_size, width in ((32, 900), (32, 900), (14, 280), (14, 280)):
                settings.overlay_font_size, settings.overlay_width = font_size, width
                overlay.apply_settings()
                root.update_idletasks()
                self.assertEqual(overlay.window.winfo_width(), width)
                self.assertEqual(overlay._translation_font.cget('size'), font_size)
                self.assertEqual(self.bottom(overlay), bottom)
                self.assertEqual(settings.overlay_bottom_anchor, anchor)
                self.assertEqual((overlay._timer, overlay.history.next_expiry), (timer, deadline))
            self.assertEqual(overlay.window.geometry(), geometry)
            self.assertIs(overlay._translation_font, fonts[0])
            self.assertIs(overlay._original_font, fonts[1])

    def test_chat_preset_estimates_position_without_waiting_for_messages(self):
        with self.overlay(placement='chat') as (root, overlay, settings, native, now):
            self.assertTrue(callable(getattr(overlay, 'input_position', None)))
            self.assertEqual(overlay.input_position(), (10040, 10729))
            overlay.sync(OrderedDict(a=chat('a')), 1)
            root.update_idletasks()
            self.assertEqual(overlay.window.winfo_x(), 10040)
            self.assertEqual(self.bottom(overlay), 10645)
            self.assertEqual(overlay.input_position(), (10040, 10729))
            now[0] = 25
            overlay._expire()
            self.assertEqual(overlay.window.state(), 'withdrawn')
            self.assertEqual(overlay.input_position(), (10040, 10729))

    def test_custom_bottom_and_horizontal_position_survive_display_round_trip(self):
        with self.overlay() as (root, overlay, settings, native, now):
            overlay.sync(OrderedDict(a=chat('a')), 1)
            root.update_idletasks()
            first_bottom = self.bottom(overlay)
            saved_anchor = settings.overlay_bottom_anchor
            root.tk.call('tk', 'scaling', 2 * 96 / 72)
            overlay.refresh_layout(DisplayMetrics((10000, 10000, 13840, 12080), 2))
            root.update_idletasks()
            self.assertEqual(overlay.window.winfo_x(), 10800)
            self.assertEqual(self.bottom(overlay), 10000 + (first_bottom - 10000) * 2)
            self.assertEqual(settings.overlay_bottom_anchor, saved_anchor)
            root.tk.call('tk', 'scaling', 96 / 72)
            overlay.refresh_layout(DisplayMetrics((10000, 10000, 11920, 11040), 1))
            root.update_idletasks()
            self.assertEqual((overlay.window.winfo_x(), self.bottom(overlay)), (10400, first_bottom))

    def test_chat_preset_can_replace_custom_position_without_changing_deadline(self):
        with self.overlay() as (root, overlay, settings, native, now):
            overlay.sync(OrderedDict(a=chat('a')), 1)
            root.update_idletasks()
            timer, deadline = overlay._timer, overlay.history.next_expiry
            self.assertTrue(callable(getattr(overlay, 'use_chat_preset', None)))
            overlay.use_chat_preset()
            root.update_idletasks()
            self.assertEqual(settings.overlay_placement, 'chat')
            self.assertEqual((overlay.window.winfo_x(), self.bottom(overlay)), (10040, 10645))
            self.assertEqual((overlay._timer, overlay.history.next_expiry), (timer, deadline))

    def test_hidden_custom_overlay_restores_input_position_before_new_chat_after_dpi_change(self):
        with self.overlay() as (root, overlay, settings, native, now):
            overlay.sync(OrderedDict(a=chat('a')), 1)
            root.update_idletasks()
            first_bottom = self.bottom(overlay)
            now[0] = 25
            overlay._expire()
            self.assertEqual(overlay.window.state(), 'withdrawn')
            root.tk.call('tk', 'scaling', 2 * 96 / 72)
            overlay.refresh_layout(DisplayMetrics((10000, 10000, 13840, 12080), 2))
            root.update_idletasks()
            expected_bottom = 10000 + (first_bottom - 10000) * 2
            self.assertEqual(overlay.input_position(), (10800, expected_bottom + 168))
            now[0] = 26
            overlay.sync(OrderedDict(b=chat('b')), 1)
            root.update_idletasks()
            self.assertEqual((overlay.window.winfo_x(), self.bottom(overlay)), (10800, expected_bottom))

    def test_empty_chat_preset_keeps_same_input_baseline_when_content_arrives(self):
        with self.overlay() as (root, overlay, settings, native, now):
            overlay.use_chat_preset()
            before = overlay.input_position()
            self.assertEqual(before, (10040, 10729))
            self.assertEqual(overlay.window.state(), 'withdrawn')
            overlay.sync(OrderedDict((key, chat(key, text='长消息 ' * 60)) for key in 'ab'), 1)
            root.update_idletasks()
            self.assertEqual(overlay.input_position(), before)
            self.assertEqual(self.bottom(overlay), 10645)

    def test_original_setting_font_and_edit_header_keep_custom_bottom(self):
        with self.overlay() as (root, overlay, settings, native, now):
            settings.overlay_y = 10400
            messages = OrderedDict((key, chat(key, text='集合进攻 ' * 50, original='Group up ' * 50))
                                   for key in 'ab')
            overlay.sync(messages, 1)
            root.update_idletasks()
            expected_bottom = self.bottom(overlay)
            settings.overlay_original = True
            overlay.apply_settings()
            root.update_idletasks()
            self.assertEqual(self.bottom(overlay), expected_bottom)
            overlay.set_editing(True)
            root.update_idletasks()
            self.assertEqual(self.bottom(overlay), expected_bottom)
            settings.overlay_font_size = 32
            overlay.apply_settings()
            root.update_idletasks()
            self.assertEqual(self.bottom(overlay), expected_bottom)
            overlay.set_editing(False)
            root.update_idletasks()
            self.assertEqual(self.bottom(overlay), expected_bottom)

    def test_empty_adjustment_previews_two_messages_and_drag_records_custom_bottom(self):
        with self.overlay(placement='chat') as (root, overlay, settings, native, now):
            overlay.set_editing(True)
            root.update_idletasks()
            rows = self.visible_rows(overlay)
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row['translation'].cget('text') for row in rows))
            self.assertFalse(overlay.history.entries)
            self.assertIsNone(overlay._timer)
            start = SimpleNamespace(x_root=10050, y_root=10500)
            end = SimpleNamespace(x_root=10150, y_root=10450)
            overlay._start_drag(start)
            overlay._drag(end)
            overlay._end_drag(end)
            root.update_idletasks()
            self.assertEqual(settings.overlay_placement, 'custom')
            self.assertIsNotNone(settings.overlay_bottom_anchor)
            dragged_bottom = self.bottom(overlay)
            overlay.set_editing(False)
            overlay.sync(OrderedDict(a=chat('a')), 1)
            root.update_idletasks()
            self.assertEqual(self.bottom(overlay), dragged_bottom)
            self.assertEqual(native.editing_modes[-1], False)

    def test_rounded_region_receives_current_dimensions_after_resize(self):
        with self.overlay(scale=1.25) as (root, overlay, settings, native, now):
            overlay.sync(OrderedDict(a=chat('a')), 1)
            root.update_idletasks()
            self.assertTrue(native.rounded)
            self.assertEqual(native.rounded[-1],
                             (overlay.window.winfo_width(), overlay.window.winfo_height(), 12))
            first_calls = len(native.rounded)
            overlay.sync(OrderedDict(a=chat('a')), 1)
            self.assertEqual(len(native.rounded), first_calls)

    def test_theme_switch_recolors_existing_widgets_without_changing_chat_state(self):
        with self.overlay(original=True) as (root, overlay, settings, native, now):
            settings.color_theme = 'amethyst'
            overlay.sync(OrderedDict((key, chat(key, text='请集合', original='Group up')) for key in 'ab'), 1)
            root.update_idletasks()
            background = overlay.window.cget('background')
            geometry = overlay.window.geometry()
            bottom = self.bottom(overlay)
            window_id = overlay.window.winfo_id()
            fonts = (overlay._header_font, overlay._translation_font, overlay._original_font)
            font_names = [str(font) for font in fonts]
            named_fonts = root.tk.call('font', 'names')
            rows = [(row['translation'].cget('text'), row['original'].cget('text'))
                    for row in self.visible_rows(overlay)]
            history = [(key, id(entry), entry.expires_at) for key, entry in overlay.history.entries.items()]
            timer, deadline = overlay._timer, overlay.history.next_expiry
            now[0] = 12
            settings.color_theme = 'ice'
            overlay.apply_settings()
            root.update_idletasks()
            self.assertEqual(overlay.window.cget('background'), background)
            palette = theme.overlay_colors(settings)
            for widget in (overlay.window, overlay.body, overlay.placeholder,
                           *[row['frame'] for row in overlay.rows]):
                self.assertEqual(widget.cget('background'), palette['surface'])
            self.assertEqual(overlay.header.cget('background'), palette['field'])
            self.assertEqual(overlay.instructions.cget('background'), palette['field'])
            self.assertEqual(overlay.instructions.cget('foreground'), palette['text'])
            self.assertEqual(overlay.done.cget('background'), palette['selection'])
            self.assertEqual(overlay.done.cget('foreground'), palette['text'])
            self.assertEqual(overlay.placeholder.cget('foreground'), palette['muted'])
            for row in overlay.rows:
                for name in ('translation', 'original'):
                    self.assertEqual(row[name].cget('background'), palette['surface'])
                self.assertEqual(row['translation'].cget('foreground'), palette['text'])
                self.assertEqual(row['original'].cget('foreground'), palette['muted'])
            self.assertEqual(overlay.window.winfo_id(), window_id)
            self.assertEqual(overlay.window.geometry(), geometry)
            self.assertEqual(self.bottom(overlay), bottom)
            for actual, existing in zip((overlay._header_font, overlay._translation_font,
                                         overlay._original_font), fonts):
                self.assertIs(actual, existing)
            self.assertEqual([str(font) for font in fonts], font_names)
            self.assertEqual(root.tk.call('font', 'names'), named_fonts)
            self.assertEqual([(row['translation'].cget('text'), row['original'].cget('text'))
                              for row in self.visible_rows(overlay)], rows)
            self.assertEqual([(key, id(entry), entry.expires_at)
                              for key, entry in overlay.history.entries.items()], history)
            self.assertEqual((overlay._timer, overlay.history.next_expiry), (timer, deadline))
            self.assertTrue(settings.overlay_original)


if __name__ == '__main__':
    unittest.main()
