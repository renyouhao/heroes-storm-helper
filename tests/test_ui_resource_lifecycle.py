"""闭合后的UI资源不能等待后台线程GC来销毁Tcl解释器。"""
import subprocess
import sys
import unittest


class UiResourceLifecycleTests(unittest.TestCase):
    def check(self,setup,*,closes_root=False):
        script='''
import gc, threading, tkinter as tk, weakref
from tkinter import font as tkfont
from types import SimpleNamespace as NS
from hots_chat.ui.theme import apply_theme
from hots_chat.window_settings import WindowSettings
root=tk.Tk();root.withdraw();settings=WindowSettings()
apply_theme(root,settings)
'''+setup+'''
root.update_idletasks()
watched=(weakref.ref(root),weakref.ref(component))
component.close() if hasattr(component,'close') else None
'''+('' if closes_root else 'root.destroy()')+'''
del root,component
assert not any(reference() is not None for reference in watched), 'destroyed UI still owns a Tk reference cycle'
worker=threading.Thread(target=gc.collect)
worker.start();worker.join()
print('released')
'''
        result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(result.stderr,'')

    def test_quick_input_closes_variable_traces_and_releases_its_owner(self):
        self.check('''
from hots_chat.ui.quick_input import QuickInput
component=QuickInput(root,NS(generation=0),settings)
component._create_window()
''')

    def test_phrase_panel_destroys_its_search_trace_and_child_cache(self):
        self.check('''
from hots_chat.ui.phrase_panel import PhrasePanel
component=PhrasePanel(root,NS(list=lambda **kwargs:[]),lambda text:None,lambda:None)
component.pack()
''')

    def test_chat_feed_destroys_its_owned_text_and_scrollbar(self):
        self.check('''
from hots_chat.ui.chat_feed import ChatFeed
component=ChatFeed(root,font=tkfont.Font(root=root,size=12),settings=settings)
component.pack()
''')

    def test_whole_demo_releases_its_ui_before_worker_gc(self):
        self.check('''
import tempfile
from pathlib import Path
from hots_chat.app import build_controller
from hots_chat.paths import AppPaths
from hots_chat.layout import DisplayMetrics
from hots_chat.ui.main_window import create_app
folder=tempfile.TemporaryDirectory()
base=Path(folder.name)
controller=build_controller(AppPaths(base/'.env',base/'l.db',base/'p.json'),True)
settings.hotkeys={};settings.overlay_enabled=False
component=create_app(root,controller,settings,
    display=NS(refresh=lambda:DisplayMetrics((10000,10000,11920,11080),1)))
''',closes_root=True)
