"""在屏幕外创建本程序窗口；不改系统分辨率、不识别用户界面。"""
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from hots_chat.app import build_controller
from hots_chat.paths import AppPaths
from hots_chat.ui.main_window import create_app
from hots_chat.window_settings import WindowSettings


class MainResolutionTests(unittest.TestCase):
    def test_normal_and_compact_controls_fit_three_resolutions_four_dpi_scales(self):
        for width,height in ((1920,1080),(2560,1440),(3840,2160)):
            for scale in (1,1.25,1.5,2):
                for mode in ('normal','compact'):
                    with self.subTest(resolution=(width,height),scale=scale,mode=mode),tempfile.TemporaryDirectory() as tmp:
                        p=Path(tmp);c=build_controller(AppPaths(p/'.env',p/'u.db',p/'l.db',p/'p.json'),True)
                        root=tk.Tk();root.withdraw();window=None
                        try:
                            root.tk.call('tk','scaling',scale*96/72)
                            with patch('hots_chat.ui.main_window.screen_bounds',return_value=[(10000,10000,10000+width,10000+height-round(40*scale))]):
                                window=create_app(root,c,WindowSettings(mode=mode,x=10000,y=10000,font_size=32,hotkeys={},overlay_enabled=False))
                                root.deiconify();root.update()
                                self.assertLessEqual(root.winfo_width(),width)
                                self.assertLessEqual(root.winfo_height(),height-round(40*scale))
                                for name in ('input','preview','tree'):
                                    widget=getattr(window,name)
                                    self.assertGreater(widget.winfo_height(),20,name+' clipped')
                                    bottom=widget.winfo_rooty()-root.winfo_rooty()+widget.winfo_height()
                                    self.assertLessEqual(bottom,root.winfo_height(),name+' below client area')
                                for widget in window.action_row.winfo_children():
                                    self.assertGreater(widget.winfo_width(),5,'hidden action button')
                                    right=widget.winfo_rootx()-root.winfo_rootx()+widget.winfo_width()
                                    self.assertLessEqual(right,root.winfo_width(),'action button outside client area')
                        finally:
                            if window:window.close()
                            else:c.close();root.destroy()
