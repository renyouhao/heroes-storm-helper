import queue
import unittest
from hots_chat.hotkeys import HotkeyManager,parse_binding
from hots_chat.window_settings import WindowSettings

class Backend:
    def __init__(self):self.q=queue.Queue();self.registered=[];self.unregistered=[]
    def prepare(self):return 1
    def register(self,i,mods,vk):
        if i==2:return False
        self.registered.append(i);return True
    def unregister(self,i):self.unregistered.append(i)
    def next(self):return self.q.get()
    def quit(self,thread_id):self.q.put(None)

class HotkeyTests(unittest.TestCase):
    def test_default_input_shortcut_registers_control_without_alt(self):
        self.assertEqual(parse_binding(WindowSettings().hotkeys['focus']),(0x4002,ord('T')))
    def test_conflict_reports_and_unregisters(self):
        b=Backend();events=[]
        h=HotkeyManager(backend=b);h.start({'focus':'Ctrl+Alt+T','copy':'Ctrl+Alt+C'},events.append)
        h.stop()
        self.assertEqual(b.registered,b.unregistered)
        self.assertTrue(any(e['kind']=='conflict' for e in events))
        self.assertFalse(h.thread.is_alive())
    def test_invalid_shortcut_rejected_before_thread(self):
        h=HotkeyManager(backend=Backend())
        with self.assertRaises(ValueError):h.start({'focus':'T'},lambda e:None)
