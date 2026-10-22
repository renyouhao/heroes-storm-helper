"""注册热键接收事件，不向游戏发送按键。默认不创建线程。"""
import ctypes
from ctypes import wintypes
import threading


def parse_function_key(text):
    """独立模式只允许功能键，避免把技能键或聊天键作为开关。"""
    key = text.strip().upper() if isinstance(text, str) else ''
    if key not in tuple(f'F{number}' for number in range(1, 13)):
        raise ValueError('连按快捷键请选择 F1–F12。')
    return 0x6f + int(key[1:])


def parse_binding(text):
    parts=text.upper().split('+')
    modifiers={'CTRL':2,'ALT':1,'SHIFT':4,'WIN':8}
    if len(parts)<2 or len(set(parts))!=len(parts):raise ValueError('快捷键须包含修饰键，例如 Ctrl+Alt+T。')
    mods=0x4000
    for part in parts[:-1]:
        if part not in modifiers:raise ValueError('快捷键修饰键无效。')
        mods|=modifiers[part]
    key=parts[-1]
    if len(key)==1 and key.isascii() and key.isalnum():vk=ord(key)
    elif key.startswith('F') and key[1:].isdigit() and 1<=int(key[1:])<=12:vk=0x6f+int(key[1:])
    else:raise ValueError('快捷键只支持字母、数字或 F1–F12。')
    return mods,vk


class Win32Hotkeys:
    def __init__(self):
        self.user=ctypes.WinDLL('user32',use_last_error=True)
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.user.RegisterHotKey.argtypes=[wintypes.HWND,ctypes.c_int,wintypes.UINT,wintypes.UINT]
        self.user.UnregisterHotKey.argtypes=[wintypes.HWND,ctypes.c_int]
        self.user.GetMessageW.argtypes=[ctypes.POINTER(wintypes.MSG),wintypes.HWND,wintypes.UINT,wintypes.UINT]
        self.user.GetMessageW.restype=wintypes.BOOL
        self.user.PeekMessageW.argtypes=[ctypes.POINTER(wintypes.MSG),wintypes.HWND,wintypes.UINT,wintypes.UINT,wintypes.UINT]
        self.user.PostThreadMessageW.argtypes=[wintypes.DWORD,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
        self.kernel.GetCurrentThreadId.restype=wintypes.DWORD

    def prepare(self):
        self.user.PeekMessageW(ctypes.byref(wintypes.MSG()),None,0,0,0)
        return self.kernel.GetCurrentThreadId()
    def register(self,i,mods,vk):return bool(self.user.RegisterHotKey(None,i,mods,vk))
    def unregister(self,i):self.user.UnregisterHotKey(None,i)
    def next(self):
        msg=wintypes.MSG()
        while True:
            result=self.user.GetMessageW(ctypes.byref(msg),None,0,0)
            if result<=0:return None
            if msg.message==0x312:return int(msg.wParam)
    def quit(self,thread_id):self.user.PostThreadMessageW(thread_id,0x12,0,0)


class HotkeyManager:
    def __init__(self,backend=None):
        self.backend=backend;self.thread=None;self.thread_id=None;self.ready=threading.Event()
    def start(self,bindings,emit):
        self.stop()
        if not bindings:return
        if set(bindings)-{'focus','translate','copy'}:raise ValueError('未知快捷键动作。')
        parsed=[(action,parse_binding(text)) for action,text in bindings.items()]
        self.ready.clear()
        def run():
            registered=[]
            try:
                backend=self.backend or Win32Hotkeys();self.backend=backend
                self.thread_id=backend.prepare()
                actions={}
                for i,(action,(mods,vk)) in enumerate(parsed,1):
                    if backend.register(i,mods,vk):registered.append(i);actions[i]=action
                    else:emit({'kind':'conflict','action':action})
                self.ready.set()
                while True:
                    key=backend.next()
                    if key is None:break
                    if key in actions:emit({'kind':'hotkey','action':actions[key]})
            except Exception:emit({'kind':'conflict','action':'热键服务不可用'})
            finally:
                for i in registered:self.backend.unregister(i)
                self.ready.set()
        self.thread=threading.Thread(target=run,name='hotkey-pump',daemon=True);self.thread.start()
        self.ready.wait(3)
    def stop(self):
        if self.thread and self.thread.is_alive():
            self.ready.wait(3)
            if self.thread_id is not None:self.backend.quit(self.thread_id)
            self.thread.join(3)
