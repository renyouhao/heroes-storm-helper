"""底部字幕离线验证：只通过API创建屏幕外的自有Tk窗口。"""
from collections import OrderedDict
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import patch

from hots_chat.layout import DisplayMetrics
from hots_chat.models import ChatEvent
from hots_chat.ui.overlay_window import OverlayWindow
from hots_chat.ui.native_windows import Win32Window
from hots_chat.ui import theme
from hots_chat.ui.viewmodel import MessageView
from hots_chat.window_settings import WindowSettings


class OfflineNative:
    def set_overlay_mode(self, window, editing):
        pass

    def show_no_activate(self, window):
        window.deiconify()


def message(key, *, text=None, original=None, suppressed=''):
    event = ChatEvent(key, 1, '团队', '队友', '1:00', original or key)
    result = MessageView(event, engine_translation=text or '集合 ' + key)
    result.suppressed_reason = suppressed
    return result


class SubtitleOverlayTests(unittest.TestCase):
    @contextmanager
    def overlay(self, *, original=False, placement='bottom', scale=1, native=None):
        root = tk.Tk()
        root.withdraw()
        root.tk.call('tk', 'scaling', scale * 96 / 72)
        settings = WindowSettings(overlay_x=10400, overlay_y=10400, overlay_width=420,
                                  overlay_font_size=11, overlay_timeout=6,
                                  overlay_original=original, preferences_version=3)
        settings.overlay_style = 'subtitle'
        settings.overlay_placement = placement
        settings.overlay_bottom_anchor = None
        metrics = DisplayMetrics((10000, 10000, 11920, 11000), scale)
        now = [10.]
        overlay = OverlayWindow(root, settings, metrics=metrics,
                                native=native if native is not None else OfflineNative(),
                                clock=lambda: now[0])
        try:
            yield root, overlay, settings, now
        finally:
            overlay.close()
            root.destroy()

    @staticmethod
    def foreground(overlay):
        canvas = overlay.subtitle_canvas
        return [canvas.itemcget(item, 'text') for item in canvas.find_withtag('foreground')]

    @staticmethod
    def bottom(overlay):
        overlay.window.update_idletasks()
        return overlay.window.winfo_y() + overlay.window.winfo_height()

    def test_subtitle_draws_only_latest_message_without_losing_main_history(self):
        with self.overlay() as (root, overlay, settings, now):
            messages = OrderedDict((key, message(key)) for key in 'abc')
            overlay.sync(messages, 1)
            root.update_idletasks()
            visible = self.foreground(overlay)
            self.assertEqual(len(visible), 1)
            self.assertIn('集合 c', visible[0])
            self.assertEqual(len(messages), 3)
            self.assertEqual(len(overlay.history.entries), 3)
            self.assertEqual(overlay.subtitle_canvas.winfo_manager(), 'pack')
            self.assertFalse(any(row['frame'].winfo_manager() for row in overlay.rows))

    def test_slow_translation_gets_visible_priority_over_newer_original(self):
        with self.overlay() as (root, overlay, settings, now):
            a=message('a',original='A original');a.engine_translation=''
            b=message('b',original='B original');b.engine_translation=''
            messages=OrderedDict(a=a)
            overlay.sync(messages,1)
            now[0]=12;messages['b']=b;overlay.sync(messages,1)
            self.assertIn('B original',self.foreground(overlay)[0])
            now[0]=17;a.engine_translation='A 中文';overlay.sync(messages,1)
            self.assertEqual(self.foreground(overlay),['队友：A 中文'])
            now[0]=18;c=message('c',original='C original');c.engine_translation=''
            messages['c']=c;overlay.sync(messages,1)
            self.assertEqual(self.foreground(overlay),['队友：A 中文'])
            now[0]=19.9;overlay._expire()
            self.assertEqual(self.foreground(overlay),['队友：A 中文'])
            now[0]=20;overlay._expire()
            self.assertIn('C original',self.foreground(overlay)[0])

    def test_waiting_translation_gets_three_seconds_from_actual_display(self):
        with self.overlay() as (root, overlay, settings, now):
            a=message('a',original='A original');a.engine_translation=''
            b=message('b',original='B original');b.engine_translation=''
            messages=OrderedDict(a=a)
            overlay.sync(messages,1)
            now[0]=12;messages['b']=b;overlay.sync(messages,1)
            now[0]=17;a.engine_translation='A 中文';overlay.sync(messages,1)
            now[0]=18;b.engine_translation='B 中文';overlay.sync(messages,1)
            self.assertEqual(self.foreground(overlay),['队友：A 中文'])
            now[0]=20;overlay._expire()
            self.assertEqual(self.foreground(overlay),['队友：B 中文'])
            now[0]=22.9;overlay._expire()
            self.assertEqual(self.foreground(overlay),['队友：B 中文'])
            now[0]=23;overlay._expire()
            self.assertEqual(overlay.window.state(),'withdrawn')
            self.assertIsNone(overlay._timer)

    def test_translation_admitted_just_before_freshness_deadline_is_fully_readable(self):
        with self.overlay() as (root, overlay, settings, now):
            row=message('a');row.engine_translation=''
            messages=OrderedDict(a=row)
            overlay.sync(messages,1)
            now[0]=24;row.engine_translation='最后一秒到达';overlay.sync(messages,1)
            self.assertEqual(self.foreground(overlay),['队友：最后一秒到达'])
            now[0]=26.9;overlay._expire()
            self.assertEqual(self.foreground(overlay),['队友：最后一秒到达'])
            now[0]=27;overlay._expire()
            self.assertEqual(overlay.window.state(),'withdrawn')
            overlay.sync(messages,1)
            self.assertEqual(overlay.window.state(),'withdrawn')

    def test_waiting_queue_keeps_latest_three_and_does_not_replay_discarded_results(self):
        with self.overlay() as (root, overlay, settings, now):
            a=message('a');a.engine_translation=''
            messages=OrderedDict(a=a)
            overlay.sync(messages,1)
            now[0]=12
            for key in 'bcde':
                messages[key]=message(key);messages[key].engine_translation=''
            overlay.sync(messages,1)
            now[0]=17;a.engine_translation='A 中文';overlay.sync(messages,1)
            for key,arrival in zip('bcde',(17.1,17.2,17.3,17.4)):
                now[0]=arrival;messages[key].engine_translation=key.upper()+' 中文'
                overlay.sync(messages,1)
                self.assertEqual(self.foreground(overlay),['队友：A 中文'])
            queued=[entry.message for entry in overlay.history._candidates.values()
                    if entry.subtitle_state=='queued']
            self.assertEqual([row.event.message_id for row in queued],list('cde'))
            for switch,key in ((20,'C'),(23,'D'),(26,'E')):
                now[0]=switch;overlay._expire()
                self.assertEqual(self.foreground(overlay),['队友：'+key+' 中文'])
                timer=overlay._timer
                for _ in range(10):overlay.sync(messages,1)
                self.assertEqual(overlay._timer,timer)
                self.assertEqual(len(root.tk.splitlist(root.tk.call('after','info'))),1)
            now[0]=28.9;overlay._expire()
            self.assertEqual(self.foreground(overlay),['队友：E 中文'])
            now[0]=29;overlay._expire();overlay.sync(messages,1)
            self.assertEqual(overlay.window.state(),'withdrawn')
            self.assertIsNone(overlay._timer)

    def test_hide_and_settings_rollback_preserve_active_reading_deadline(self):
        with self.overlay() as (root, overlay, settings, now):
            a=message('a');a.engine_translation=''
            b=message('b');b.engine_translation=''
            messages=OrderedDict(a=a)
            overlay.sync(messages,1)
            now[0]=12;messages['b']=b;overlay.sync(messages,1)
            now[0]=17;a.engine_translation='A 中文';overlay.sync(messages,1)
            now[0]=18;b.engine_translation='B 中文';overlay.sync(messages,1)
            entry=overlay.history._candidates['a']
            self.assertEqual(entry.subtitle_read_until,20)
            overlay.set_input_active(True)
            now[0]=19;overlay.set_input_active(False)
            self.assertEqual(self.foreground(overlay),['队友：A 中文'])
            self.assertEqual(entry.subtitle_read_until,20)
            snapshot=overlay.capture_settings_state()
            settings.overlay_enabled=False;overlay.apply_settings()
            settings.overlay_enabled=True;overlay.apply_settings()
            overlay.restore_settings_state(snapshot)
            overlay.apply_settings()
            self.assertIs(overlay.history._candidates['a'],entry)
            self.assertEqual(entry.subtitle_read_until,20)
            self.assertEqual(self.foreground(overlay),['队友：A 中文'])
            now[0]=20;overlay._expire()
            self.assertEqual(self.foreground(overlay),['队友：B 中文'])
            # 用户隐藏期间取消排队补时；B仍按18秒到达时的21秒截止。
            self.assertEqual(overlay.history._candidates['b'].expires_at,21)
            now[0]=21;overlay._expire()
            self.assertEqual(overlay.window.state(),'withdrawn')

    def test_waiting_translation_that_misses_freshness_window_never_replays(self):
        with self.overlay() as (root, overlay, settings, now):
            b=message('b');b.engine_translation=''
            a=message('a');a.engine_translation=''
            messages=OrderedDict(b=b)
            overlay.sync(messages,1)
            now[0]=12;messages['a']=a;overlay.sync(messages,1)
            now[0]=24;a.engine_translation='A 中文';overlay.sync(messages,1)
            now[0]=24.5;b.engine_translation='错过展示资格';overlay.sync(messages,1)
            self.assertEqual(self.foreground(overlay),['队友：A 中文'])
            now[0]=25;overlay._expire()
            self.assertEqual(self.foreground(overlay),['队友：A 中文'])
            now[0]=27;overlay._expire();overlay.sync(messages,1)
            self.assertEqual(overlay.window.state(),'withdrawn')
            self.assertIsNone(overlay._timer)

    def test_history_eviction_cannot_interrupt_the_active_three_second_reading(self):
        with self.overlay() as (root, overlay, settings, now):
            messages=OrderedDict(a=message('a',text='正在阅读'))
            overlay.sync(messages,1)
            now[0]=11
            messages.clear()
            for index in range(500):
                row=message(str(index));row.engine_translation=''
                messages[str(index)]=row
            overlay.sync(messages,1)
            self.assertEqual(self.foreground(overlay),['队友：正在阅读'])
            self.assertLessEqual(len(overlay.history._candidates),501)
            now[0]=13;overlay._expire()
            self.assertIn('499',self.foreground(overlay)[0])
            self.assertNotIn('a',overlay.history._candidates)
            self.assertLessEqual(len(overlay.history._candidates),500)

    def test_transparency_keeps_white_text_dark_outline_and_no_theme_border(self):
        with self.overlay() as (root, overlay, settings, now):
            overlay.sync(OrderedDict(a=message('a')), 1)
            canvas = overlay.subtitle_canvas
            self.assertEqual(float(overlay.window.attributes('-alpha')), 1.)
            key = str(overlay.window.attributes('-transparentcolor'))
            self.assertTrue(key)
            self.assertEqual(overlay.window.cget('background'), key)
            self.assertEqual(overlay.body.cget('background'), key)
            self.assertEqual(canvas.cget('background'), key)
            self.assertEqual(int(overlay.window.cget('highlightthickness')), 0)
            self.assertTrue(canvas.find_withtag('outline'))
            for item in canvas.find_withtag('foreground'):
                self.assertEqual(canvas.itemcget(item, 'fill'), '#ffffff')
            for item in canvas.find_withtag('outline'):
                self.assertEqual(canvas.itemcget(item, 'fill'), '#141414')

    def test_short_subtitle_text_is_centered_inside_the_bottom_window(self):
        with self.overlay() as (root, overlay, settings, now):
            overlay.sync(OrderedDict(a=message('a')), 1)
            root.update()
            canvas = overlay.subtitle_canvas
            item, = canvas.find_withtag('foreground')
            bounds = canvas.bbox(item)
            self.assertAlmostEqual((bounds[0] + bounds[2]) / 2, canvas.winfo_width() / 2, delta=2)

    def test_mixed_language_text_is_really_limited_to_two_tk_lines(self):
        for scale in (1, 1.25, 1.5, 2):
            with self.subTest(scale=scale), self.overlay(scale=scale) as context:
                root, overlay, settings, now = context
                overlay.sync(OrderedDict(a=message('a', text='支援中路 다음 전투 help ' * 80)), 1)
                # 首次映射会产生MapNotify；处理自有窗口事件后验证真实客户区。
                root.update()
                canvas = overlay.subtitle_canvas
                item, = canvas.find_withtag('foreground')
                text = canvas.itemcget(item, 'text')
                bbox = canvas.bbox(item)
                self.assertTrue(text.endswith('…'))
                self.assertLessEqual(bbox[3] - bbox[1], overlay._translation_font.metrics('linespace') * 2)
                self.assertLessEqual(bbox[3], canvas.winfo_height())
                self.assertLessEqual(bbox[2], canvas.winfo_width())

    def test_original_switch_shares_two_lines_instead_of_growing_to_four(self):
        with self.overlay(original=True) as (root, overlay, settings, now):
            overlay.sync(OrderedDict(a=message('a', text='译文很长 ' * 80,
                                                original='도와주세요 Group up ' * 80)), 1)
            root.update_idletasks()
            canvas = overlay.subtitle_canvas
            items = canvas.find_withtag('foreground')
            self.assertEqual(len(items), 2)
            for item, font in zip(items, (overlay._translation_font, overlay._original_font)):
                bbox = canvas.bbox(item)
                self.assertTrue(canvas.itemcget(item, 'text').endswith('…'))
                self.assertLessEqual(bbox[3] - bbox[1], font.metrics('linespace'))

    def test_bottom_is_fixed_while_content_grows_upward(self):
        with self.overlay() as (root, overlay, settings, now):
            messages = OrderedDict(a=message('a'))
            overlay.sync(messages, 1)
            root.update_idletasks()
            self.assertEqual((overlay.window.winfo_x(), self.bottom(overlay)), (10750, 10860))
            short_y = overlay.window.winfo_y()
            messages['a'].engine_translation = '较长译文 ' * 100
            overlay.sync(messages, 1)
            root.update_idletasks()
            self.assertEqual(self.bottom(overlay), 10860)
            self.assertLess(overlay.window.winfo_y(), short_y)
            self.assertIsNone(settings.overlay_anchor)
            self.assertIsNone(settings.overlay_bottom_anchor)
            self.assertIsNone(overlay.input_position())

    def test_bottom_preset_clears_custom_anchors_without_restarting_expiry(self):
        with self.overlay(placement='custom') as (root, overlay, settings, now):
            overlay.sync(OrderedDict(a=message('a')), 1)
            timer = overlay._timer
            self.assertIsNotNone(settings.overlay_bottom_anchor)
            overlay.use_bottom_preset()
            self.assertEqual(settings.overlay_placement, 'bottom')
            self.assertIsNone(settings.overlay_anchor)
            self.assertIsNone(settings.overlay_bottom_anchor)
            self.assertEqual(self.bottom(overlay), 10860)
            self.assertEqual((overlay._timer, overlay.history.next_expiry), (timer, 16.))

    def test_custom_position_is_preserved_and_scale_changes_keep_bottom_anchor(self):
        with self.overlay(placement='custom') as (root, overlay, settings, now):
            overlay.sync(OrderedDict(a=message('a')), 1)
            root.update_idletasks()
            self.assertEqual((overlay.window.winfo_x(), overlay.window.winfo_y()), (10400, 10400))
            original_bottom = self.bottom(overlay)
            anchor = settings.overlay_bottom_anchor
            root.tk.call('tk', 'scaling', 2 * 96 / 72)
            overlay.refresh_layout(DisplayMetrics((10000, 10000, 13840, 12000), 2))
            root.update_idletasks()
            self.assertEqual((overlay.window.winfo_x(), self.bottom(overlay)),
                             (10800, 10000 + 2 * (original_bottom - 10000)))
            self.assertEqual(settings.overlay_bottom_anchor, anchor)

    def test_input_hides_and_restores_unexpired_subtitle_without_extending_timer(self):
        with self.overlay() as (root, overlay, settings, now):
            overlay.sync(OrderedDict(a=message('a')), 1)
            timer = overlay._timer
            entry = overlay.history.entries['a']
            overlay.set_input_active(True)
            self.assertEqual(overlay.window.state(), 'withdrawn')
            self.assertIs(overlay.history.entries['a'], entry)
            now[0] = 12
            overlay.set_input_active(False)
            self.assertEqual(overlay.window.state(), 'normal')
            self.assertEqual((overlay._timer, entry.expires_at), (timer, 16.))
            overlay.set_input_active(True)
            now[0] = 17
            overlay.set_input_active(False)
            self.assertEqual(overlay.window.state(), 'withdrawn')
            self.assertFalse(overlay.history.entries)
            self.assertIsNone(overlay._timer)

    def test_input_resume_does_not_restart_expired_waiting_translations(self):
        with self.overlay() as (root, overlay, settings, now):
            messages=OrderedDict(a=message('a'))
            overlay.sync(messages,1)
            for key in 'bcd':messages[key]=message(key)
            overlay.sync(messages,1)
            self.assertEqual(sum(entry.subtitle_state=='queued'
                                 for entry in overlay.history._candidates.values()),3)
            deadline=overlay.history.next_expiry
            overlay.set_input_active(True)
            now[0]=12;overlay.set_input_active(False)
            self.assertEqual(overlay.window.state(),'normal')
            self.assertEqual(overlay.history.next_expiry,deadline)
            overlay.set_input_active(True)
            now[0]=deadline;overlay.set_input_active(False)
            self.assertEqual(overlay.window.state(),'withdrawn')
            self.assertFalse(overlay.history.entries)
            self.assertIsNone(overlay._timer)

    def test_translation_received_while_input_hidden_keeps_arrival_deadline_on_resume(self):
        with self.overlay() as (root, overlay, settings, now):
            row=message('a');row.engine_translation=''
            messages=OrderedDict(a=row)
            overlay.sync(messages,1)
            overlay.set_input_active(True)
            now[0]=16;overlay._expire()
            now[0]=17;row.engine_translation='隐藏时到达';overlay.sync(messages,1)
            self.assertEqual(overlay.history.next_expiry,20)
            now[0]=19.5;overlay.set_input_active(False)
            self.assertEqual(self.foreground(overlay),['队友：隐藏时到达'])
            self.assertEqual(overlay.history.next_expiry,20)
            now[0]=20;overlay._expire()
            self.assertEqual(overlay.window.state(),'withdrawn')
            self.assertIsNone(overlay._timer)

    def test_calibration_has_hint_and_drag_then_returns_to_transparent_clickthrough(self):
        with self.overlay() as (root, overlay, settings, now):
            settings.overlay_width = 280
            overlay.apply_settings()
            overlay.set_editing(True)
            root.update()
            self.assertEqual(overlay.window.state(), 'normal')
            self.assertTrue(overlay.instructions.cget('text'))
            self.assertEqual(overlay.header.winfo_manager(), 'pack')
            self.assertTrue(overlay.done.winfo_ismapped())
            self.assertGreaterEqual(overlay.done.winfo_width(), overlay.done.winfo_reqwidth())
            self.assertLessEqual(overlay.done.winfo_x() + overlay.done.winfo_width(),
                                 overlay.header.winfo_width())
            self.assertFalse(str(overlay.window.attributes('-transparentcolor')))
            start = SimpleNamespace(x_root=10760, y_root=10800)
            end = SimpleNamespace(x_root=10860, y_root=10750)
            overlay._start_drag(start)
            overlay._drag(end)
            overlay._end_drag(end)
            self.assertEqual(settings.overlay_placement, 'custom')
            self.assertIsNotNone(settings.overlay_bottom_anchor)
            self.assertFalse(overlay.history.entries)
            overlay.set_editing(False)
            self.assertEqual(overlay.window.state(), 'withdrawn')
            self.assertTrue(str(overlay.window.attributes('-transparentcolor')))
            self.assertEqual(overlay.header.winfo_manager(), '')

    def test_style_and_theme_changes_keep_entries_fonts_timer_and_window(self):
        with self.overlay() as (root, overlay, settings, now):
            overlay.sync(OrderedDict(a=message('a')), 1)
            window, timer = overlay.window, overlay._timer
            entry = overlay.history.entries['a']
            fonts = overlay._translation_font, overlay._original_font
            for style, color in (('panel', 'mint'), ('subtitle', 'moonlight'), ('subtitle', 'graphite')):
                settings.overlay_style, settings.color_theme = style, color
                theme.apply_theme(root, settings)
                overlay.apply_settings()
                self.assertIs(overlay.window, window)
                self.assertIs(overlay.history.entries['a'], entry)
                self.assertEqual((overlay._timer, entry.expires_at), (timer, 16.))
                self.assertEqual((overlay._translation_font, overlay._original_font), fonts)
            self.assertEqual(self.foreground(overlay), ['队友：集合 a'])

    def test_preset_does_not_repaint_messages_after_their_deadline(self):
        with self.overlay() as (root, overlay, settings, now):
            overlay.sync(OrderedDict(a=message('a')), 1)
            now[0] = 17
            # 模拟UI回调先于已到期定时器被处理，预设操作也不得延长显示。
            overlay.use_bottom_preset()
            self.assertFalse(overlay.history.entries)
            self.assertEqual(overlay.window.state(), 'withdrawn')
            self.assertIsNone(overlay._timer)

    def test_real_native_wrapper_keeps_clickthrough_and_clears_panel_region_on_subtitle(self):
        native = Win32Window()
        with self.overlay(native=native) as (root, overlay, settings, now):
            overlay.sync(OrderedDict(a=message('a')), 1)
            root.update()
            handle = native.hwnd_for(overlay.window)
            style = native._get_long(handle, -20)
            self.assertTrue(style & native.WS_EX_NOACTIVATE)
            self.assertTrue(style & native.WS_EX_TRANSPARENT)
            self.assertTrue(style & native.WS_EX_TOOLWINDOW)
            overlay.set_editing(True)
            editing_style = native._get_long(native.hwnd_for(overlay.window), -20)
            self.assertTrue(editing_style & native.WS_EX_NOACTIVATE)
            self.assertFalse(editing_style & native.WS_EX_TRANSPARENT)
            overlay.set_editing(False)
            settings.overlay_style = 'panel'
            overlay.apply_settings()
            root.update()
            gdi = ctypes.WinDLL('gdi32', use_last_error=True)
            gdi.CreateRectRgn.argtypes = [ctypes.c_int] * 4
            gdi.CreateRectRgn.restype = wintypes.HANDLE
            gdi.DeleteObject.argtypes = [wintypes.HANDLE]
            native.user.GetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE]
            region = gdi.CreateRectRgn(0, 0, 1, 1)
            try:
                self.assertNotEqual(native.user.GetWindowRgn(handle, region), 0)
                settings.overlay_style = 'subtitle'
                overlay.apply_settings()
                root.update()
                # 没有自定义region时GetWindowRgn返回0；旧panel区域不能裁剪字幕。
                self.assertEqual(native.user.GetWindowRgn(handle, region), 0)
                final_style = native._get_long(native.hwnd_for(overlay.window), -20)
                self.assertTrue(final_style & native.WS_EX_TRANSPARENT)
                self.assertEqual(float(overlay.window.attributes('-alpha')), 1.)
            finally:
                gdi.DeleteObject(region)

    def test_failed_save_snapshot_restores_visible_entry_without_resurrecting_expired(self):
        with self.overlay() as (root, overlay, settings, now):
            overlay.sync(OrderedDict(a=message('a')), 1)
            entry = overlay.history.entries['a']
            snapshot = overlay.capture_settings_state()
            settings.overlay_enabled = False
            overlay.apply_settings()
            settings.overlay_enabled = True
            overlay.apply_settings()
            now[0] = 12
            overlay.restore_settings_state(snapshot)
            self.assertIs(overlay.history.entries['a'], entry)
            self.assertEqual(overlay.history.next_expiry, 16.)
            self.assertEqual(overlay.window.state(), 'normal')
            now[0] = 17
            overlay.restore_settings_state(snapshot)
            self.assertFalse(overlay.history.entries)
            self.assertEqual(overlay.window.state(), 'withdrawn')
            self.assertIsNone(overlay._timer)

    def test_suppressed_message_does_not_hide_latest_valid_subtitle_and_fold_keeps_deadline(self):
        with self.overlay() as (root, overlay, settings, now):
            messages = OrderedDict(a=message('a', original='help'))
            overlay.sync(messages, 1)
            now[0] = 12
            messages['b'] = message('b', original='help', suppressed='发送过快')
            messages['c'] = message('c', original='other', suppressed='发送过快')
            overlay.sync(messages, 1)
            self.assertIn('×2', self.foreground(overlay)[0])
            self.assertEqual(overlay.history.next_expiry, 16.)
            now[0] = 16
            overlay._expire()
            self.assertEqual(overlay.window.state(), 'withdrawn')
            self.assertIsNone(overlay._timer)

    def test_transparency_failure_uses_readable_opaque_fallback_and_reports_reason(self):
        original_attributes = tk.Toplevel.attributes

        def unsupported(window, *args):
            if len(args) > 1 and args[0] == '-transparentcolor' and args[1]:
                raise tk.TclError('transparent color unavailable')
            return original_attributes(window, *args)

        with patch.object(tk.Toplevel, 'attributes', unsupported), self.assertLogs(
                'hots_chat.ui.overlay_window', level='WARNING') as logs:
            with self.overlay() as (root, overlay, settings, now):
                overlay.sync(OrderedDict(a=message('a')), 1)
                self.assertEqual(float(overlay.window.attributes('-alpha')), 1.)
                self.assertFalse(str(overlay.window.attributes('-transparentcolor')))
                self.assertTrue(overlay.transparency_error)
                self.assertEqual(overlay.window.state(), 'normal')
                self.assertEqual(self.foreground(overlay), ['队友：集合 a'])
        self.assertTrue(any('transparent color unavailable' in line for line in logs.output))


if __name__ == '__main__':
    unittest.main()
