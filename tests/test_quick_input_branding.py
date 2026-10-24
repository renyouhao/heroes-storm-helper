"""Selected B branding: screenshot copy and explicit local window icon."""
from pathlib import Path
from types import SimpleNamespace
import ctypes
from ctypes import wintypes
import struct
import sys
import tkinter as tk
import unittest
from unittest.mock import patch

from hots_chat.layout import DisplayMetrics
from hots_chat.ui import brand_icon
from hots_chat.ui.quick_input import IDLE_HINT, KEYBOARD_HINT, QuickInput
from hots_chat.ui.theme import apply_theme
from hots_chat.window_settings import WindowSettings


class QuickInputBrandingTests(unittest.TestCase):
    def test_root_sets_the_canonical_mark_and_inheritance_default(self):
        root=tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        with patch.object(root,'iconbitmap',wraps=root.iconbitmap) as setter:
            self.assertTrue(brand_icon.apply_assistant_icon(root,default=True))
            self.assertEqual(setter.call_args_list[0].args,(str(brand_icon.assistant_icon_path()),))
            self.assertEqual(setter.call_args_list[1].kwargs,{'default':str(brand_icon.assistant_icon_path())})
        from hots_chat.ui.native_windows import Win32Window
        native=Win32Window()
        send=native.user.SendMessageW
        send.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
        send.restype=ctypes.c_ssize_t
        self.assertTrue(send(native.hwnd_for(root),0x007F,0,0))

    def test_icon_uses_bundle_data_path_when_frozen(self):
        with patch.object(sys, '_MEIPASS', str(Path('bundle-root').absolute()), create=True):
            self.assertEqual(brand_icon.assistant_icon_path(), Path(sys._MEIPASS)/'data'/'assistant.ico')

    def test_native_title_uses_the_purple_mark_and_screenshot_hint(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        settings = WindowSettings(color_theme='lavender', hotkeys={})
        apply_theme(root,settings)
        quick = QuickInput(root,SimpleNamespace(generation=0),settings,
                           metrics=DisplayMetrics((-2000,-2000,-800,-1000)),
                           native=SimpleNamespace(detach_ime=lambda:None))
        self.addCleanup(quick.close)
        quick._create_window()
        quick.status.set(IDLE_HINT)
        root.update_idletasks()
        self.assertTrue(quick.icon_loaded)
        # Tk on Windows does not return the ICO filename from iconbitmap().
        # Verify the native title bar actually received its small icon handle.
        from hots_chat.ui.native_windows import Win32Window
        native = Win32Window()
        send = native.user.SendMessageW
        send.argtypes = [wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
        send.restype = ctypes.c_ssize_t
        self.assertTrue(send(native.hwnd_for(quick.window),0x007F,0,0))
        self.assertEqual(quick.window.title(),'快捷中文输入')
        self.assertEqual(quick.status_label.cget('text'),'输入中文，译文会复制；请在游戏中粘贴发送。')
        self.assertEqual(KEYBOARD_HINT,'Enter 翻译并复制  ·  Esc 收起  ·  Ctrl+Tab 切换语言')
        hints=[str(child.cget('text')) for child in quick.body.winfo_children() if 'text' in child.keys()]
        self.assertIn(KEYBOARD_HINT,hints)
        # Keep the selected original single-row layout.
        row=quick.entry.master
        self.assertEqual(row.pack_slaves(),[quick.target_control,quick.entry,quick.submit_button])

    def test_icon_contains_small_title_bar_and_high_dpi_sizes(self):
        data=brand_icon.assistant_icon_path().read_bytes()
        reserved,kind,count=struct.unpack_from('<3H',data)
        self.assertEqual((reserved,kind),(0,1))
        sizes=[]
        for index in range(count):
            width,height,_,_,planes,depth,length,offset=struct.unpack_from('<4B2H2I',data,6+index*16)
            width,height=width or 256,height or 256
            sizes.append(width)
            self.assertEqual(width,height)
            self.assertEqual((planes,depth),(1,32))
            png=data[offset:offset+length]
            self.assertEqual(png[:8],b'\x89PNG\r\n\x1a\n')
            self.assertEqual(struct.unpack_from('!2I',png,16),(width,height))
        self.assertTrue({16,24,32,48,256}.issubset(sizes))


if __name__=='__main__':
    unittest.main()
