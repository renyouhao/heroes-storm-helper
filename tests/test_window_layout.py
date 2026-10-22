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
                controller=build_controller(AppPaths(p/'.env',p/'u.db',p/'l.db',p/'p.json'),True)
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
