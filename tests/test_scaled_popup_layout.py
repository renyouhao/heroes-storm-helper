"""仅创建屏幕外测试窗口；无截图、游戏、真实剪贴板或全局热键。"""
from collections import OrderedDict
from types import SimpleNamespace
import tkinter as tk
from tkinter import font as tkfont
import unittest

from hots_chat.models import ChatEvent
from hots_chat.ui.overlay_window import OverlayWindow
from hots_chat.ui.quick_input import QuickInput
from hots_chat.ui.viewmodel import MessageView
from hots_chat.window_settings import WindowSettings

try:
    from hots_chat.layout import DisplayMetrics
except ModuleNotFoundError:
    DisplayMetrics=None


class OfflineOverlayNative:
    def set_overlay_mode(self,window,editing):
        pass
    def show_no_activate(self,window):
        window.deiconify()


class ScaledPopupLayoutTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(DisplayMetrics,'需要有界布局接口')
        self.root=tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def assert_widget_inside(self,widget,window):
        self.assertGreaterEqual(widget.winfo_rootx(),window.winfo_rootx())
        self.assertGreaterEqual(widget.winfo_rooty(),window.winfo_rooty())
        self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),window.winfo_rootx()+window.winfo_width())
        self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),window.winfo_rooty()+window.winfo_height())
        self.assertGreaterEqual(widget.winfo_height(),widget.winfo_reqheight())

    def test_popup_widgets_fit_full_resolution_scale_matrix_at_largest_font(self):
        for scale in (1,1.25,1.5,2):
            self.root.tk.call('tk','scaling',scale*96/72)
            for width,height in ((1920,1080),(2560,1440),(3840,2160)):
                with self.subTest(scale=scale,resolution=(width,height)):
                    work=(10000,10000,10000+width,10000+height-round(40*scale))
                    metrics=DisplayMetrics(work,scale)
                    settings=WindowSettings(font_size=32,overlay_font_size=32,overlay_width=280,
                                            overlay_x=99999,overlay_y=99999,overlay_original=True,
                                            overlay_style='panel',overlay_timeout=15,
                                            overlay_placement='chat')
                    overlay=OverlayWindow(self.root,settings,native=OfflineOverlayNative(),metrics=metrics)
                    quick=QuickInput(self.root,SimpleNamespace(generation=1),settings,
                                     native=SimpleNamespace(detach_ime=lambda:None),clipboard=lambda text:None,metrics=metrics)
                    try:
                        messages=OrderedDict()
                        for index in range(3):
                            event=ChatEvent(str(index),1,'团队','队友'+str(index),'0:00','求支援abc ' * 40)
                            messages[str(index)]=MessageView(event,engine_translation='需要集合支援xyz ' * 40)
                        overlay.sync(messages,1)
                        overlay.set_editing(True)
                        quick._create_window()
                        quick.window.deiconify()
                        self.root.update()
                        for popup in (overlay.window,quick.window):
                            self.assertGreaterEqual(popup.winfo_x(),10000)
                            self.assertGreaterEqual(popup.winfo_y(),10000)
                            self.assertLessEqual(popup.winfo_x()+popup.winfo_width(),work[2])
                            self.assertLessEqual(popup.winfo_y()+popup.winfo_height()+round(40*scale),work[3]) if popup is quick.window else self.assertLessEqual(popup.winfo_y()+popup.winfo_height(),work[3])
                        self.assert_widget_inside(quick.entry,quick.window)
                        self.assert_widget_inside(overlay.done,overlay.window)
                        for row in overlay.rows:
                            for name in ('translation','original'):
                                self.assert_widget_inside(row[name],overlay.window)
                        configured=tkfont.Font(root=self.root,font=quick.entry.cget('font'))
                        self.assertEqual(configured.cget('size'),32,'字号点值不能再次乘DPI')
                    finally:
                        overlay.close()
                        quick.close()

    def test_unchanged_metrics_do_not_relayout_and_changed_work_area_recovers(self):
        large=DisplayMetrics((10000,10000,13840,12080),2)
        small=DisplayMetrics((10000,10000,11920,11000),2)
        settings=WindowSettings(overlay_x=13700,overlay_y=12000,overlay_style='panel',
                                overlay_font_size=14,overlay_timeout=15,overlay_width=440,
                                overlay_placement='chat')
        overlay=OverlayWindow(self.root,settings,native=OfflineOverlayNative(),metrics=large)
        quick=QuickInput(self.root,SimpleNamespace(generation=1),settings,
                         native=SimpleNamespace(detach_ime=lambda:None),clipboard=lambda text:None,metrics=large)
        try:
            quick._create_window()
            self.assertFalse(quick.refresh_layout(large))
            self.assertFalse(overlay.refresh_layout(large))
            self.assertTrue(quick.refresh_layout(small))
            self.assertTrue(overlay.refresh_layout(small))
            self.root.update()
            self.assertLessEqual(settings.overlay_x+overlay.window.winfo_width(),11920)
            self.assertLessEqual(settings.overlay_y+overlay.window.winfo_height(),11000)
        finally:
            overlay.close()
            quick.close()

    def test_status_text_changes_grow_input_window_without_clipping_entry(self):
        self.root.tk.call('tk','scaling',8/3)
        metrics=DisplayMetrics((10000,10000,11920,11000),2)
        quick=QuickInput(self.root,SimpleNamespace(generation=1),WindowSettings(font_size=32),
                         native=SimpleNamespace(detach_ime=lambda:None),metrics=metrics)
        try:
            quick._create_window()
            quick.window.deiconify()
            quick.status.set('翻译失败，请检查配置并重新尝试。' * 100)
            self.root.update()
            self.assert_widget_inside(quick.entry,quick.window)
            self.assert_widget_inside(quick.status_label,quick.window)
            self.assertLessEqual(quick.window.winfo_y()+quick.window.winfo_height()+80,11000)
        finally:
            quick.close()

    def test_folded_count_is_visible_and_relayout_keeps_one_expiry_timer(self):
        metrics=DisplayMetrics((10000,10000,11920,11000),1)
        settings=WindowSettings(overlay_x=10000,overlay_y=10000,overlay_style='panel',
                                overlay_font_size=14,overlay_timeout=15,overlay_width=440,
                                overlay_placement='chat')
        overlay=OverlayWindow(self.root,settings,native=OfflineOverlayNative(),metrics=metrics,clock=lambda:10)
        try:
            messages=OrderedDict()
            for key in ('a','b'):
                messages[key]=MessageView(ChatEvent(key,1,'团队','队友','0:00','help'),engine_translation='支援')
            overlay.sync(messages,1)
            self.assertIn('×2',overlay.rows[0]['translation'].cget('text'))
            first_timer=overlay._timer
            for scale in (1.25,1.5,2,1):
                overlay.refresh_layout(DisplayMetrics((10000,10000,11920,11000),scale))
                self.assertEqual(overlay._timer,first_timer,'布局不能重设消息期限')
            self.assertEqual(len(self.root.tk.call('after','info')),1)
            overlay.close()
            self.assertFalse(self.root.tk.call('after','info'))
        finally:
            overlay.close()



