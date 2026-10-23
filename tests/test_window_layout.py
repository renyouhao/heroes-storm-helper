"""仅验证自行创建的屏幕外离线窗口几何；不截图、不读用户界面或剪贴板。"""
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from hots_chat.app import build_controller
from hots_chat.paths import AppPaths
from hots_chat.ui.main_window import create_app
from hots_chat.window_settings import WindowSettings


class WindowLayoutTests(unittest.TestCase):
    def test_input_and_preview_fit_in_normal_and_compact_default_sizes(self):
        for mode in ('normal','compact'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp)
                controller=build_controller(AppPaths(p/'.env',p/'l.db',p/'p.json'),True)
                root=tk.Tk();root.withdraw();window=None
                try:
                    with patch('hots_chat.ui.main_window.screen_bounds',return_value=[(0,0,100000,100000)]):
                        window=create_app(root,controller,WindowSettings(mode=mode,x=50000,y=50000,hotkeys={},overlay_enabled=False))
                        root.geometry('+50000+50000');root.deiconify();root.update()
                    for name in ('input','preview'):
                        widget=getattr(window,name)
                        self.assertGreater(widget.winfo_height(),20,name+' was clipped')
                        bottom=widget.winfo_rooty()-root.winfo_rooty()+widget.winfo_height()
                        self.assertLessEqual(bottom,root.winfo_height(),name+' below window')
                finally:
                    if window:window.close()
                    else:controller.close();root.destroy()


    def test_quota_button_remains_visible_at_540_without_opening_browser_on_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            controller=build_controller(AppPaths(p/'.env',p/'l.db',p/'p.json'),True)
            from types import SimpleNamespace
            controller.account_setup=SimpleNamespace(poll=lambda: [])
            root=tk.Tk();root.withdraw();window=None
            try:
                with patch('webbrowser.open',return_value=True) as browser, \
                     patch('hots_chat.ui.main_window.screen_bounds',return_value=[(50000,50000,50540,50600)]):
                    window=create_app(root,controller,WindowSettings(x=50000,y=50000,hotkeys={},overlay_enabled=False))
                    root.geometry('540x600+50000+50000');root.deiconify();root.update()
                    browser.assert_not_called()
                    self.assertIsNotNone(getattr(window,'quota_button',None))
                    for widget in (window.account_button,window.quota_button):
                        self.assertTrue(widget.winfo_ismapped())
                        self.assertGreater(widget.winfo_width(),20)
                        self.assertGreaterEqual(widget.winfo_rootx()-root.winfo_rootx(),0)
                        self.assertLessEqual(widget.winfo_rootx()-root.winfo_rootx()+widget.winfo_width(),540)
                    window.quota_button.invoke()
                    browser.assert_called_once_with('https://mt.console.aliyun.com/service')
            finally:
                del controller.account_setup
                if window:window.close()
                else:controller.close();root.destroy()
