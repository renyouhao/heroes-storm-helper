"""输入条自己的 Win32 焦点与 IME 消息；不安装全局键盘钩子。"""
import ctypes
from ctypes import wintypes


LRESULT = ctypes.c_ssize_t
UINT_PTR = ctypes.c_size_t
SUBCLASSPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
    wintypes.LPARAM, UINT_PTR, UINT_PTR,
)


class WindowsInputNative:
    def __init__(self, *, user=None, imm=None, controls=None):
        self.user = user or ctypes.WinDLL('user32', use_last_error=True)
        self.imm = imm or ctypes.WinDLL('imm32', use_last_error=True)
        self.controls = controls or ctypes.WinDLL('comctl32', use_last_error=True)
        self.user.GetForegroundWindow.argtypes = []
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user.GetAncestor.restype = wintypes.HWND
        self.user.IsWindow.argtypes = [wintypes.HWND]
        self.user.IsWindow.restype = wintypes.BOOL
        self.user.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user.SetForegroundWindow.restype = wintypes.BOOL
        self.user.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user.GetAsyncKeyState.restype = wintypes.SHORT
        self.imm.ImmGetContext.argtypes = [wintypes.HWND]
        self.imm.ImmGetContext.restype = wintypes.HANDLE
        self.imm.ImmGetCompositionStringW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ]
        self.imm.ImmGetCompositionStringW.restype = wintypes.LONG
        self.imm.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
        self.imm.ImmReleaseContext.restype = wintypes.BOOL
        self.controls.SetWindowSubclass.argtypes = [wintypes.HWND, SUBCLASSPROC, UINT_PTR, UINT_PTR]
        self.controls.SetWindowSubclass.restype = wintypes.BOOL
        self.controls.RemoveWindowSubclass.argtypes = [wintypes.HWND, SUBCLASSPROC, UINT_PTR]
        self.controls.RemoveWindowSubclass.restype = wintypes.BOOL
        self.controls.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.controls.DefSubclassProc.restype = LRESULT
        self._hwnd = 0
        self._callback = None
        self._composing = False
        self._return_guard = False
        self._return_released = False

    def foreground_window(self):
        return self.user.GetForegroundWindow() or 0

    def window_handle(self, widget_handle):
        # Tk 的 winfo_id 可能是包装窗口的子窗口，前台句柄是 GA_ROOT。
        return self.user.GetAncestor(widget_handle, 2) or widget_handle

    def activate(self, handle):
        return bool(self.user.IsWindow(handle) and self.user.SetForegroundWindow(handle))

    def restore_foreground(self, previous, own):
        if (not previous or previous == own or self.foreground_window() != own
                or not self.user.IsWindow(previous)):
            return False
        return bool(self.user.SetForegroundWindow(previous))

    def attach_ime(self, handle):
        self.detach_ime()
        self._callback = SUBCLASSPROC(self._window_message)
        if not self.controls.SetWindowSubclass(handle, self._callback, 1, 0):
            self._callback = None
            raise ctypes.WinError(ctypes.get_last_error())
        self._hwnd = handle

    def _window_message(self, hwnd, message, wparam, lparam, subclass_id, data):
        if message == 0x010D:  # WM_IME_STARTCOMPOSITION
            self._composing = True
        elif message == 0x010E:  # WM_IME_ENDCOMPOSITION
            self._composing = False
            # IME 常在 Tk 收到 Enter 前结束组合，保留该次确认键直到释放。
            if self.user.GetAsyncKeyState(0x0D) & 0x8000:
                self._return_guard = True
                self._return_released = False
        elif message == 0x010F and lparam & 0x0800:  # WM_IME_COMPOSITION / GCS_RESULTSTR
            if self.user.GetAsyncKeyState(0x0D) & 0x8000:
                self._return_guard = True
                self._return_released = False
        elif message in (0x0101, 0x0291) and wparam == 0x0D:  # KEYUP / IME_KEYUP
            self._return_released = True
        elif message in (0x0100, 0x0290) and wparam == 0x0D:
            # 某些 IME 吞掉整次 Tk KeyRelease；新的物理 Enter 仍应可提交。
            if self._return_released and not self._composing:
                self.clear_return_guard()
        elif message == 0x0082:  # WM_NCDESTROY
            self.controls.RemoveWindowSubclass(hwnd, self._callback, 1)
            self._hwnd = 0
        return self.controls.DefSubclassProc(hwnd, message, wparam, lparam)

    def return_is_composing(self, handle):
        if self._composing or self._return_guard:
            return True
        context = self.imm.ImmGetContext(handle)
        if not context:
            return False
        try:
            size = self.imm.ImmGetCompositionStringW(context, 8, None, 0)  # GCS_COMPSTR
            return size > 0 or size == -2  # IMM_ERROR_GENERAL：不确定时避免误提交。
        finally:
            self.imm.ImmReleaseContext(handle, context)

    def clear_return_guard(self):
        self._return_guard = False
        self._return_released = False

    def detach_ime(self):
        if self._hwnd:
            if not self.controls.RemoveWindowSubclass(self._hwnd, self._callback, 1):
                raise ctypes.WinError(ctypes.get_last_error())
        self._hwnd = 0
        self._callback = None
        self._composing = False
        self.clear_return_guard()
