"""仅验证自行创建的屏幕外离线窗口几何；不截图、不读用户界面或剪贴板。"""
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch
from hots_chat.app import build_controller
from hots_chat.paths import AppPaths
from hots_chat.ui.main_window import create_app
from hots_chat.ui.settings_dialog import SettingsDialog
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


    def test_quota_is_reachable_through_settings_at_540_and_opens_browser_only_on_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            controller=build_controller(AppPaths(p/'.env',p/'l.db',p/'p.json'),True)
            from types import SimpleNamespace
            controller.account_setup=SimpleNamespace(poll=lambda: [])
            root=tk.Tk();root.withdraw();window=None;dialogs=[]
            def open_settings(owner):
                dialog=SettingsDialog(owner)
                dialogs.append(dialog)
                return dialog
            try:
                with patch('webbrowser.open',return_value=True) as browser, \
                     patch('hots_chat.ui.main_window.screen_bounds',return_value=[(50000,50000,50540,50600)]), \
                     patch('hots_chat.ui.settings_dialog.SettingsDialog',side_effect=open_settings) as settings_factory:
                    window=create_app(root,controller,WindowSettings(x=50000,y=50000,hotkeys={},overlay_enabled=False))
                    root.geometry('540x600+50000+50000');root.deiconify();root.update()
                    browser.assert_not_called()
                    entry=window.settings_button
                    self.assertTrue(entry.winfo_ismapped())
                    self.assertGreater(entry.winfo_width(),20)
                    self.assertGreaterEqual(entry.winfo_rootx()-root.winfo_rootx(),0)
                    self.assertLessEqual(entry.winfo_rootx()-root.winfo_rootx()+entry.winfo_width(),540)
                    self.assertFalse(entry.instate(('disabled',)))
                    entry.invoke();root.update()
                    settings_factory.assert_called_once_with(window)
                    dialog,=dialogs
                    connection,=(page for page in dialog.notebook.tabs()
                                 if dialog.notebook.tab(page,'text')=='连接')
                    dialog.notebook.select(connection);root.update()
                    widgets=[root.nametowidget(connection)];buttons=[]
                    while widgets:
                        widget=widgets.pop()
                        if isinstance(widget,ttk.Button) and widget.cget('text')=='查询当前服务额度 / 余额':
                            buttons.append(widget)
                        widgets.extend(widget.winfo_children())
                    quota,=buttons
                    self.assertTrue(quota.winfo_ismapped())
                    self.assertFalse(quota.instate(('disabled',)))
                    self.assertGreater(quota.winfo_width(),20)
                    self.assertGreater(quota.winfo_height(),10)
                    canvas=dialog.scroll_canvases[connection]
                    # 可滚动页面内至少有完整可点击区域，不能只存在离屏的按钮对象。
                    left=max(quota.winfo_rootx(),canvas.winfo_rootx())
                    top=max(quota.winfo_rooty(),canvas.winfo_rooty())
                    right=min(quota.winfo_rootx()+quota.winfo_width(),canvas.winfo_rootx()+canvas.winfo_width())
                    bottom=min(quota.winfo_rooty()+quota.winfo_height(),canvas.winfo_rooty()+canvas.winfo_height())
                    self.assertGreater(right-left,20)
                    self.assertGreater(bottom-top,10)
                    browser.assert_not_called()
                    quota.invoke()
                    browser.assert_called_once_with('https://mt.console.aliyun.com/service')
            finally:
                for dialog in dialogs:
                    if dialog.dialog.winfo_exists():dialog.cancel()
                del controller.account_setup
                if window:window.close()
                else:controller.close();root.destroy()
