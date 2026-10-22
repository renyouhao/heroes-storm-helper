"""仅设置助手自身窗口的 Win32 样式，不发送键鼠或改变前台窗口。"""
import ctypes
from ctypes import wintypes


class Win32Window:
    GWL_EXSTYLE=-20
    GA_ROOT=2
    WS_EX_TRANSPARENT=0x00000020
    WS_EX_TOOLWINDOW=0x00000080
    WS_EX_APPWINDOW=0x00040000
    WS_EX_LAYERED=0x00080000
    WS_EX_NOACTIVATE=0x08000000
    SW_SHOWNOACTIVATE=4
    HWND_TOPMOST=-1
    SWP_NOSIZE=0x0001
    SWP_NOMOVE=0x0002
    SWP_NOACTIVATE=0x0010
    SWP_FRAMECHANGED=0x0020
    SWP_NOOWNERZORDER=0x0200

    def __init__(self,user=None,gdi=None):
        self.user=user if user is not None else ctypes.WinDLL('user32',use_last_error=True)
        self._gdi=gdi
        # LONG_PTR 与 HWND 都必须保留指针宽度；32 位 DLL 没有 Ptr 导出。
        if ctypes.sizeof(ctypes.c_void_p)==8:
            self._get_long=self.user.GetWindowLongPtrW
            self._set_long=self.user.SetWindowLongPtrW
        else:
            self._get_long=self.user.GetWindowLongW
            self._set_long=self.user.SetWindowLongW
        self._get_long.argtypes=[wintypes.HWND,ctypes.c_int]
        self._get_long.restype=ctypes.c_ssize_t
        self._set_long.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_ssize_t]
        self._set_long.restype=ctypes.c_ssize_t
        self.user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT]
        self.user.GetAncestor.restype=wintypes.HWND
        self.user.SetWindowPos.argtypes=[wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,
                                        ctypes.c_int,ctypes.c_int,wintypes.UINT]
        self.user.SetWindowPos.restype=wintypes.BOOL
        self.user.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int]
        self.user.ShowWindow.restype=wintypes.BOOL

    def hwnd_for(self,window):
        # winfo_id 是 Tk 内容窗；修改外层包装窗口才能正确禁用激活和任务栏。
        window.update_idletasks()
        hwnd=self.user.GetAncestor(window.winfo_id(),self.GA_ROOT)
        if not hwnd:raise ctypes.WinError(ctypes.get_last_error())
        return hwnd

    def set_overlay_mode(self,window,editing=False):
        hwnd=self.hwnd_for(window)
        style=self._get_long(hwnd,self.GWL_EXSTYLE)
        style=(style|self.WS_EX_NOACTIVATE|self.WS_EX_TOOLWINDOW|self.WS_EX_LAYERED)&~self.WS_EX_APPWINDOW
        if editing:style&=~self.WS_EX_TRANSPARENT
        else:style|=self.WS_EX_TRANSPARENT
        ctypes.set_last_error(0)
        previous=self._set_long(hwnd,self.GWL_EXSTYLE,style)
        if not previous and ctypes.get_last_error():raise ctypes.WinError(ctypes.get_last_error())
        flags=self.SWP_NOSIZE|self.SWP_NOMOVE|self.SWP_NOACTIVATE|self.SWP_FRAMECHANGED|self.SWP_NOOWNERZORDER
        if not self.user.SetWindowPos(hwnd,self.HWND_TOPMOST,0,0,0,0,flags):
            raise ctypes.WinError(ctypes.get_last_error())

    def show_no_activate(self,window):
        # 无边框 Tk 的映射使用 SW_SHOWNOACTIVATE；映射前已经设置 NOACTIVATE。
        window.deiconify()
        hwnd=self.hwnd_for(window)
        self.user.ShowWindow(hwnd,self.SW_SHOWNOACTIVATE)
        flags=self.SWP_NOSIZE|self.SWP_NOMOVE|self.SWP_NOACTIVATE|self.SWP_NOOWNERZORDER
        if not self.user.SetWindowPos(hwnd,self.HWND_TOPMOST,0,0,0,0,flags):
            raise ctypes.WinError(ctypes.get_last_error())

    def set_rounded_corners(self,window,radius):
        """只裁剪助手的无边框窗口；成功后由 Windows 拥有区域句柄。"""
        if self._gdi is None:self._gdi=ctypes.WinDLL('gdi32',use_last_error=True)
        create=self._gdi.CreateRoundRectRgn
        create.argtypes=[ctypes.c_int]*6
        create.restype=wintypes.HANDLE
        remove=self._gdi.DeleteObject
        remove.argtypes=[wintypes.HANDLE]
        remove.restype=wintypes.BOOL
        assign=self.user.SetWindowRgn
        assign.argtypes=[wintypes.HWND,wintypes.HANDLE,wintypes.BOOL]
        assign.restype=ctypes.c_int
        hwnd=self.hwnd_for(window)
        width,height=window.winfo_width(),window.winfo_height()
        diameter=max(2,min(round(radius)*2,width,height))
        region=create(0,0,width+1,height+1,diameter,diameter)
        if not region:return False
        if not assign(hwnd,region,True):
            remove(region)
            return False
        return True

    def clear_rounded_corners(self,window):
        """面板切回透明字幕时移除旧尺寸区域，避免新字幕被旧区域裁掉。"""
        assign=self.user.SetWindowRgn
        assign.argtypes=[wintypes.HWND,wintypes.HANDLE,wintypes.BOOL]
        assign.restype=ctypes.c_int
        if not assign(self.hwnd_for(window),None,True):
            raise ctypes.WinError(ctypes.get_last_error())
