import ctypes
from ctypes import wintypes
import sys


class SingleInstance:
    # 沿用原互斥锁，避免改名前后的程序同时访问同一份用户数据。
    def __init__(self,name='Local\\HotsChatHelper-v1'):
        self.name=name; self.handle=None
        if sys.platform!='win32': raise RuntimeError('需要 Windows')
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype=wintypes.HANDLE
        self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]

    def acquire(self):
        self.handle=self.kernel.CreateMutexW(None,False,self.name)
        if not self.handle: raise OSError('无法创建单实例锁')
        if ctypes.get_last_error()==183:
            self.close(); return False
        return True

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle); self.handle=None
