"""开箱助手的 Win32 边界；导入时不加载 DLL、不安装 hook、不创建线程。

调用方负责在自己的工作线程调用 run，并让 hook 回调快速返回。
所有输入只由显式的 send_space 调用产生；消息泵本身不发送输入。
"""
import ctypes
from dataclasses import dataclass
import ntpath
import threading


INPUT_MARKER = 0x484F5453
GAME_EXECUTABLE = 'HeroesOfTheStorm_x64.exe'
DWORD = ctypes.c_uint32
UINT = ctypes.c_uint32
WORD = ctypes.c_uint16
LONG = ctypes.c_int32
BOOL = ctypes.c_int32
HANDLE = ctypes.c_void_p
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t
HOOKPROC = getattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE)(
    LRESULT, ctypes.c_int, WPARAM, LPARAM,
)
# Win32 若拒绝卸载，仍可能持有函数指针；失败回调需保留到进程退出。
_UNRELEASED_CALLBACKS = []


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', WORD), ('wScan', WORD), ('dwFlags', DWORD),
                ('time', DWORD), ('dwExtraInfo', ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', LONG), ('dy', LONG), ('mouseData', DWORD),
                ('dwFlags', DWORD), ('time', DWORD), ('dwExtraInfo', ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', DWORD), ('wParamL', WORD), ('wParamH', WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ('data',)
    _fields_ = [('type', DWORD), ('data', INPUTUNION)]


class POINT(ctypes.Structure):
    _fields_ = [('x', LONG), ('y', LONG)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('vkCode', DWORD), ('scanCode', DWORD), ('flags', DWORD),
                ('time', DWORD), ('dwExtraInfo', ULONG_PTR)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('pt', POINT), ('mouseData', DWORD), ('flags', DWORD),
                ('time', DWORD), ('dwExtraInfo', ULONG_PTR)]


class MSG(ctypes.Structure):
    _fields_ = [('hwnd', HANDLE), ('message', UINT), ('wParam', WPARAM),
                ('lParam', LPARAM), ('time', DWORD), ('pt', POINT),
                ('lPrivate', DWORD)]


@dataclass(frozen=True)
class GameTarget:
    hwnd: int
    pid: int


def _win_error(operation):
    code = getattr(ctypes, 'get_last_error', lambda: 0)()
    return OSError(code, f'{operation} failed (Win32 error {code})')


class Win32LootBackend:
    """窄 Win32 适配器；可注入 API 以便完全离线验证。"""

    def __init__(self, *, user=None, kernel=None, timer_interval_ms=50):
        if not isinstance(timer_interval_ms, int) or not 10 <= timer_interval_ms <= 0x7fffffff:
            raise ValueError('Timer interval must be an integer of at least 10ms')
        self.timer_interval_ms = timer_interval_ms
        self.user = user if user is not None else ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = kernel if kernel is not None else ctypes.WinDLL('kernel32', use_last_error=True)
        self._configure_apis()
        self._lock = threading.Lock()
        self._running = False
        self._thread_id = None
        self._stop_requested = threading.Event()
        # 回调引用一直保留到对应 hook 成功卸载；卸载失败也不能让 C 回调被 GC。
        self._callbacks = []

    def _configure_apis(self):
        user_signatures = {
            'GetForegroundWindow': ([], HANDLE),
            'GetWindowThreadProcessId': ([HANDLE, ctypes.POINTER(DWORD)], DWORD),
            'GetAsyncKeyState': ([ctypes.c_int], ctypes.c_int16),
            'SendInput': ([UINT, ctypes.POINTER(INPUT), ctypes.c_int], UINT),
            'SetWindowsHookExW': ([ctypes.c_int, HOOKPROC, HANDLE, DWORD], HANDLE),
            'UnhookWindowsHookEx': ([HANDLE], BOOL),
            'CallNextHookEx': ([HANDLE, ctypes.c_int, WPARAM, LPARAM], LRESULT),
            'SetTimer': ([HANDLE, ULONG_PTR, UINT, ctypes.c_void_p], ULONG_PTR),
            'KillTimer': ([HANDLE, ULONG_PTR], BOOL),
            'PeekMessageW': ([ctypes.POINTER(MSG), HANDLE, UINT, UINT, UINT], BOOL),
            'GetMessageW': ([ctypes.POINTER(MSG), HANDLE, UINT, UINT], BOOL),
            'TranslateMessage': ([ctypes.POINTER(MSG)], BOOL),
            'DispatchMessageW': ([ctypes.POINTER(MSG)], LRESULT),
            'PostThreadMessageW': ([DWORD, UINT, WPARAM, LPARAM], BOOL),
        }
        kernel_signatures = {
            'OpenProcess': ([DWORD, BOOL, DWORD], HANDLE),
            'QueryFullProcessImageNameW': (
                [HANDLE, DWORD, ctypes.c_wchar_p, ctypes.POINTER(DWORD)], BOOL),
            'CloseHandle': ([HANDLE], BOOL),
            'GetCurrentThreadId': ([], DWORD),
            'GetModuleHandleW': ([ctypes.c_wchar_p], HANDLE),
        }
        for library, signatures in ((self.user, user_signatures),
                                    (self.kernel, kernel_signatures)):
            for name, (arguments, result) in signatures.items():
                function = getattr(library, name)
                function.argtypes = arguments
                function.restype = result

    def _foreground_identity(self):
        hwnd = self.user.GetForegroundWindow()
        if not hwnd:
            return None
        pid = DWORD()
        if not self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
            return None
        return GameTarget(int(hwnd), pid.value)

    def foreground_target(self):
        """仅认可当前前台真实进程名；从不尝试激活窗口。"""
        target = self._foreground_identity()
        if target is None:
            return None
        process = self.kernel.OpenProcess(0x1000, False, target.pid)
        if not process:
            return None
        try:
            path = ctypes.create_unicode_buffer(32768)
            length = DWORD(len(path))
            if not self.kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(length)):
                return None
            if ntpath.basename(path.value).casefold() != GAME_EXECUTABLE.casefold():
                return None
            # 查询可能跨越一次 Alt+Tab / 窗口销毁，查询结果须仍属于当前前台。
            return target if self._foreground_identity() == target else None
        finally:
            if not self.kernel.CloseHandle(process):
                raise _win_error('CloseHandle')

    def modifiers_down(self):
        return any(self.user.GetAsyncKeyState(vk) & 0x8000
                   for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C))

    def space_down(self):
        """用于准备时识别已按住的空格，避免把自动重复识别成新按下。"""
        return self.key_down(0x20)

    def key_down(self, vk):
        return bool(self.user.GetAsyncKeyState(vk) & 0x8000)

    @staticmethod
    def _space_input(*, up=False):
        return Win32LootBackend._key_input(0x20, up=up)

    @staticmethod
    def _key_input(vk, *, up=False):
        event = INPUT()
        event.type = 1  # INPUT_KEYBOARD
        event.ki = KEYBDINPUT(vk, 0, 2 if up else 0, 0, INPUT_MARKER)
        return event

    def send_space(self, target, *, allowed=None):
        """核对前台后发送单次空格；部分失败时尽力释放，不重试按下。"""
        return self.send_key(0x20, target, allowed=allowed)

    def send_key(self, vk, target, *, allowed=None):
        """同一目标的一组标记按下/释放；失败只尽力释放该键。"""
        pair = (INPUT * 2)(self._key_input(vk), self._key_input(vk, up=True))
        if not isinstance(target, GameTarget) or self.foreground_target() != target:
            return False
        if allowed is not None and not allowed():
            return False
        try:
            if self.user.SendInput(2, pair, ctypes.sizeof(INPUT)) != 2:
                raise _win_error('SendInput')
        except Exception as failure:
            try:
                release = (INPUT * 1)(self._key_input(vk, up=True))
                self.user.SendInput(1, release, ctypes.sizeof(INPUT))
            except Exception:
                pass
            if isinstance(failure, OSError):
                raise
            raise OSError('SendInput failed') from failure
        return True

    def stop(self):
        """任意线程可请求停止；定时器也会在投递失败时唤醒消息泵。"""
        with self._lock:
            self._stop_requested.set()
            if self._thread_id is not None:
                self.user.PostThreadMessageW(self._thread_id, 0x12, 0, 0)  # WM_QUIT

    def run(self, on_key, on_mouse, on_tick, on_ready, stop_event):
        if stop_event.is_set():
            return
        with self._lock:
            if self._running:
                raise RuntimeError('Loot message pump is already running')
            if self._callbacks:
                raise RuntimeError('Previous native hooks could not be released')
            self._running = True
            self._stop_requested.clear()
        hooks = []
        callbacks = []
        timer = 0
        failure = None
        callback_failure = None

        def stopped():
            return stop_event.is_set() or self._stop_requested.is_set()

        def callback_failed(error):
            nonlocal callback_failure
            if callback_failure is None:
                callback_failure = error
            self.stop()

        def keyboard(code, message, data):
            if code >= 0 and not stopped() and message in (0x100, 0x101, 0x104, 0x105):
                try:
                    event = ctypes.cast(data, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if on_key(event.vkCode, message in (0x100, 0x104), event.dwExtraInfo):
                        return 1
                except BaseException as error:
                    callback_failed(error)
            return self.user.CallNextHookEx(None, code, message, data)

        def mouse(code, message, data):
            # 任意按钮按下、垂直/水平滚轮；移动和按钮释放保持无干扰。
            if code >= 0 and not stopped() and message in (0x201, 0x204, 0x207, 0x20B, 0x20A, 0x20E):
                try:
                    on_mouse()
                except BaseException as error:
                    callback_failed(error)
            return self.user.CallNextHookEx(None, code, message, data)

        try:
            message = MSG()
            self.user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            with self._lock:
                self._thread_id = self.kernel.GetCurrentThreadId()
            if not stopped():
                module = self.kernel.GetModuleHandleW(None)
                if not module:
                    raise _win_error('GetModuleHandleW')
                for kind, handler in ((13, keyboard), (14, mouse)):
                    if stopped():
                        break
                    callback = HOOKPROC(handler)
                    callbacks.append(callback)
                    self._callbacks.append(callback)
                    hook = self.user.SetWindowsHookExW(kind, callback, module, 0)
                    if not hook:
                        raise _win_error('SetWindowsHookExW')
                    hooks.append((hook, callback))
                if not stopped():
                    timer = self.user.SetTimer(None, 0, self.timer_interval_ms, None)
                    if not timer:
                        raise _win_error('SetTimer')
                    if not stopped():
                        on_ready()
                while not stopped():
                    result = self.user.GetMessageW(ctypes.byref(message), None, 0, 0)
                    if result == -1:
                        raise _win_error('GetMessageW')
                    if result == 0 or stopped():
                        break
                    if message.message == 0x113 and message.wParam == timer:
                        if not on_tick():
                            break
                    else:
                        self.user.TranslateMessage(ctypes.byref(message))
                        self.user.DispatchMessageW(ctypes.byref(message))
            if callback_failure is not None:
                raise callback_failure
        except BaseException as error:
            failure = error
        finally:
            self._stop_requested.set()
            if timer:
                try:
                    if not self.user.KillTimer(None, timer):
                        raise _win_error('KillTimer')
                except BaseException as error:
                    if failure is None:
                        failure = error
            retained = []
            for hook, callback in reversed(hooks):
                try:
                    if not self.user.UnhookWindowsHookEx(hook):
                        raise _win_error('UnhookWindowsHookEx')
                except BaseException as error:
                    retained.append(callback)
                    _UNRELEASED_CALLBACKS.append(callback)
                    if failure is None:
                        failure = error
            for callback in callbacks:
                if callback not in retained:
                    self._callbacks.remove(callback)
            with self._lock:
                self._thread_id = None
                self._running = False
        if failure is not None:
            raise failure
