"""浮窗生命周期与原生契约；真实窗口仅在屏幕外离线创建。"""
from collections import OrderedDict
import ctypes
from ctypes import wintypes
from types import SimpleNamespace
import tkinter as tk
import unittest

from hots_chat.models import ChatEvent
from hots_chat.ui.viewmodel import MessageView
from hots_chat.window_settings import WindowSettings
from hots_chat.layout import DisplayMetrics

try:
    from hots_chat.ui.overlay_window import OverlayHistory, OverlayWindow
    from hots_chat.ui.native_windows import Win32Window
except ModuleNotFoundError:
    OverlayHistory = OverlayWindow = Win32Window = None


def message(key, generation=1, text='hello', translated='你好'):
    event=ChatEvent(key,generation,'队伍','队友','1:23',text)
    return MessageView(event,engine_translation=translated)


class OverlayHistoryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(OverlayHistory,'需要实现悬浮消息生命周期')
        self.history=OverlayHistory()

    def test_first_translation_gets_reading_time_without_repeated_extension(self):
        row=message('a',translated='')
        messages=OrderedDict(a=row)
        self.history.sync(messages,1,now=10,timeout=15)
        row.engine_translation='译文到达'
        self.history.sync(messages,1,now=24,timeout=15)
        self.assertEqual(self.history.entries['a'].message.engine_translation,'译文到达')
        self.assertEqual(self.history.next_expiry,27)
        self.history.expire(27)
        self.history.sync(messages,1,now=28,timeout=15)
        self.assertFalse(self.history.entries)
        self.assertIsNone(self.history.next_expiry)

    def test_generation_change_retains_history_but_only_new_generation_pops(self):
        messages=OrderedDict(a=message('a'))
        self.history.sync(messages,1,now=10,timeout=15)
        self.history.sync(messages,2,now=11,timeout=15)
        self.assertFalse(self.history.entries)
        self.assertEqual(len(messages),1)
        messages['b']=message('b',generation=2)
        self.history.sync(messages,2,now=12,timeout=15)
        self.assertEqual(list(self.history.entries),['b'])
        self.assertEqual(self.history.next_expiry,27)

    def test_only_latest_three_messages_survive_and_evicted_rows_do_not_replay(self):
        messages=OrderedDict((key,message(key,text=key)) for key in ('a','b','c','d'))
        self.history.sync(messages,1,now=10,timeout=15)
        self.assertEqual(list(self.history.entries),['b','c','d'])
        self.history.sync(messages,1,now=11,timeout=15)
        self.assertEqual(list(self.history.entries),['b','c','d'])
        self.history.expire(25)
        messages['e']=message('e')
        self.history.sync(messages,1,now=26,timeout=15)
        self.assertEqual(list(self.history.entries),['e'])

    def test_disabled_messages_are_seen_without_being_replayed_on_enable(self):
        messages=OrderedDict(a=message('a'))
        self.history.sync(messages,1,now=10,timeout=15,enabled=False)
        self.history.sync(messages,1,now=11,timeout=15,enabled=True)
        self.assertFalse(self.history.entries)
        messages['b']=message('b')
        self.history.sync(messages,1,now=12,timeout=15)
        self.assertEqual(list(self.history.entries),['b'])


class NativeFunction:
    def __init__(self,result=1):self.result=result;self.calls=[]
    def __call__(self,*args):
        self.calls.append(args)
        return self.result


class NativeUser:
    def __init__(self):
        self.GetAncestor=NativeFunction(0x100000011)
        self.GetWindowLongPtrW=NativeFunction(0x00040000|0x00080000)
        self.SetWindowLongPtrW=NativeFunction(0x00040000|0x00080000)
        self.GetWindowLongW=NativeFunction(0x00040000|0x00080000)
        self.SetWindowLongW=NativeFunction(0x00040000|0x00080000)
        self.SetWindowPos=NativeFunction()
        self.ShowWindow=NativeFunction(0)


class NativeOverlayTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Win32Window,'需要实现 Win32 不激活浮窗')
        self.user=NativeUser()
        self.native=Win32Window(user=self.user)
        self.window=SimpleNamespace(update_idletasks=lambda:None,winfo_id=lambda:0x100000001,
                                    deiconify=lambda:None)

    def test_targets_wrapper_and_uses_pointer_sized_signatures(self):
        self.assertEqual(self.native.hwnd_for(self.window),0x100000011)
        self.assertEqual(self.user.GetAncestor.calls,[(0x100000001,2)])
        self.assertIs(self.user.GetAncestor.restype,wintypes.HWND)
        self.assertEqual(self.user.SetWindowPos.argtypes[:2],[wintypes.HWND,wintypes.HWND])
        if ctypes.sizeof(ctypes.c_void_p)==8:
            self.assertIs(self.user.GetWindowLongPtrW.restype,ctypes.c_ssize_t)
            self.assertIs(self.user.SetWindowLongPtrW.argtypes[-1],ctypes.c_ssize_t)

    def test_normal_mode_is_layered_click_through_toolwindow_and_never_activates(self):
        self.native.set_overlay_mode(self.window,editing=False)
        setter=self.user.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p)==8 else self.user.SetWindowLongW
        self.assertEqual(setter.calls[-1],(0x100000011,-20,0x080800a0))
        hwnd,after,x,y,width,height,flags=self.user.SetWindowPos.calls[-1]
        self.assertEqual((hwnd,after,x,y,width,height),(0x100000011,-1,0,0,0,0))
        self.assertTrue(flags & 0x0010)
        self.assertTrue(flags & 0x0020)
        self.native.show_no_activate(self.window)
        self.assertEqual(self.user.ShowWindow.calls[-1],(0x100000011,4))

    def test_editing_removes_only_click_through_and_retains_no_activate(self):
        self.user.GetWindowLongPtrW.result=self.user.GetWindowLongW.result=0x080800a0
        self.native.set_overlay_mode(self.window,editing=True)
        setter=self.user.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p)==8 else self.user.SetWindowLongW
        self.assertEqual(setter.calls[-1],(0x100000011,-20,0x08080080))


class OverlayWindowTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(OverlayWindow,'需要实现独立浮窗')
        self.root=tk.Tk();self.root.withdraw()
        self.settings=WindowSettings(overlay_x=10000,overlay_y=10000,overlay_style='panel',
                                     overlay_font_size=14,overlay_timeout=15,overlay_width=440,
                                     overlay_placement='custom')
        self.now=10.0
        self.overlay=OverlayWindow(self.root,self.settings,clock=lambda:self.now,
                                   metrics=DisplayMetrics((10000,10000,20000,20000),1))

    def tearDown(self):
        if hasattr(self,'overlay'):self.overlay.close()
        if hasattr(self,'root'):self.root.destroy()

    def test_empty_window_is_hidden_with_no_timer(self):
        self.overlay.sync(OrderedDict(),1)
        self.root.update_idletasks()
        self.assertEqual(self.overlay.window.state(),'withdrawn')
        self.assertIsNone(self.overlay._timer)

    def test_pending_original_updates_in_place_and_expiry_hides_with_no_timer(self):
        row=message('a',text='need help',translated='')
        messages=OrderedDict(a=row)
        self.overlay.sync(messages,1)
        self.root.update_idletasks()
        self.assertIn('need help',self.overlay.rows[0]['translation'].cget('text'))
        self.assertEqual(self.overlay.window.winfo_width(),440)
        first_timer=self.overlay._timer
        self.now=20;row.engine_translation='需要支援'
        self.overlay.sync(messages,1)
        self.assertIn('需要支援',self.overlay.rows[0]['translation'].cget('text'))
        self.assertEqual(self.overlay._timer,first_timer)
        self.now=25
        self.overlay._expire()
        self.assertEqual(self.overlay.window.state(),'withdrawn')
        self.assertIsNone(self.overlay._timer)

    def test_untranslated_row_shows_actual_route_state_instead_of_pending(self):
        row=message('a',text='need help',translated='')
        messages=OrderedDict(a=row)
        for state,reason,label in (('untranslated','','未翻译'),('queued','','排队中'),
                                   ('translating','','翻译中'),('failed','网络超时','翻译失败'),
                                   ('skipped','自动翻译已暂停','自动翻译已暂停'),
                                   ('skipped','翻译服务未配置','翻译服务未配置')):
            with self.subTest(state=state,reason=reason):
                row.translation_state=state;row.translation_reason=reason
                self.overlay.sync(messages,1)
                self.assertIn(label+'：need help',self.overlay.rows[0]['translation'].cget('text'))

    def test_slow_translation_restores_three_seconds_without_timer_or_widget_churn(self):
        self.settings.overlay_timeout=6
        row=message('a',text='need help',translated='')
        messages=OrderedDict(a=row)
        self.overlay.sync(messages,1)
        self.now=16;self.overlay._expire()
        self.assertEqual(self.overlay.window.state(),'withdrawn')
        self.assertIsNone(self.overlay._timer)
        self.now=18;row.engine_translation='需要支援'
        self.overlay.sync(messages,1)
        self.assertEqual(self.overlay.history.next_expiry,21)
        self.assertEqual(self.overlay.window.state(),'normal')
        timer=self.overlay._timer
        label=self.overlay.rows[0]['translation']
        for _ in range(30):self.overlay.sync(messages,1)
        self.assertEqual(self.overlay._timer,timer)
        self.assertIs(self.overlay.rows[0]['translation'],label)
        self.assertEqual(self.root.tk.splitlist(self.root.tk.call('after','info')),(timer,))
        self.now=21;self.overlay._expire()
        self.assertEqual(self.overlay.window.state(),'withdrawn')
        self.assertIsNone(self.overlay._timer)

    def test_real_wrapper_keeps_native_style_and_fits_its_message_after_mapping(self):
        self.overlay.sync(OrderedDict(a=message('a')),1)
        self.root.update_idletasks()
        native=self.overlay._native
        wrapper=native.hwnd_for(self.overlay.window)
        self.assertNotEqual(wrapper,self.overlay.window.winfo_id())
        style=native._get_long(wrapper,-20)
        self.assertTrue(style & 0x08000000,'浮窗必须不激活')
        self.assertTrue(style & 0x00080000,'分层窗口才可靠穿透其他进程')
        self.assertTrue(style & 0x00000020,'正常展示必须鼠标穿透')
        self.assertTrue(style & 0x00000080,'浮窗不能出现在任务切换')
        label=self.overlay.rows[0]['translation']
        bottom=label.winfo_rooty()-self.overlay.window.winfo_rooty()+label.winfo_height()
        self.assertGreater(self.overlay.window.winfo_height(),20)
        self.assertLessEqual(bottom,self.overlay.window.winfo_height())
        self.overlay.set_editing(True)
        style=native._get_long(native.hwnd_for(self.overlay.window),-20)
        self.assertTrue(style & 0x08000000)
        self.assertFalse(style & 0x00000020)
        self.assertFalse(self.overlay.done.cget('takefocus'))
        self.overlay.set_editing(False)
        style=native._get_long(native.hwnd_for(self.overlay.window),-20)
        self.assertTrue(style & 0x00000020)

    def test_collapsed_original_is_only_expanded_by_setting(self):
        self.overlay.sync(OrderedDict(a=message('a')),1)
        self.root.update_idletasks()
        self.assertEqual(self.overlay.rows[0]['original'].winfo_manager(),'')
        self.settings.overlay_original=True
        self.overlay.apply_settings();self.root.update_idletasks()
        self.assertEqual(self.overlay.rows[0]['original'].winfo_manager(),'pack')

    def test_drag_requires_editing_and_clamps_position_to_work_area(self):
        start=SimpleNamespace(x_root=10010,y_root=10010)
        end=SimpleNamespace(x_root=-90,y_root=-190)
        self.overlay._start_drag(start);self.overlay._drag(end)
        self.assertEqual((self.settings.overlay_x,self.settings.overlay_y),(10000,10000))
        self.overlay.set_editing(True);self.root.update_idletasks()
        self.overlay._start_drag(start);self.overlay._drag(end);self.root.update_idletasks()
        self.assertEqual((self.settings.overlay_x,self.settings.overlay_y),(10000,10000))
        self.assertEqual((self.overlay.window.winfo_x(),self.overlay.window.winfo_y()),(10000,10000))
        self.overlay.set_editing(False)
        self.assertEqual(self.overlay.window.state(),'withdrawn')

    def test_disable_and_close_release_expiry_timer_and_do_not_replay(self):
        messages=OrderedDict(a=message('a'))
        self.overlay.sync(messages,1)
        timer=self.overlay._timer
        self.assertIsNotNone(timer)
        self.settings.overlay_enabled=False;self.overlay.apply_settings()
        self.assertIsNone(self.overlay._timer)
        self.assertEqual(self.overlay.window.state(),'withdrawn')
        self.assertNotIn(timer,self.root.tk.call('after','info'))
        self.settings.overlay_enabled=True;self.overlay.apply_settings()
        self.overlay.sync(messages,1)
        self.assertEqual(self.overlay.window.state(),'withdrawn')
        messages['b']=message('b');self.overlay.sync(messages,1)
        timer=self.overlay._timer
        self.overlay.close()
        self.assertNotIn(timer,self.root.tk.call('after','info'))
        self.assertFalse(self.overlay.window.winfo_exists())


if __name__=='__main__':unittest.main()
