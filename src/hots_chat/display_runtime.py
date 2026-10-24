"""Windows DPI启动策略与轻量Tk刷新；不修改操作系统显示设置。

在创建任何Tk窗口前调用 ``enable_process_dpi_awareness``。几何采用
``dpi / 96``，正数字号保留点值，由 ``tk scaling = dpi / 72`` 换算一次。
"""
import ctypes
from ctypes import wintypes
from functools import lru_cache
import math
import tkinter as tk

from .layout import DisplayMetrics
from .window_settings import screen_bounds, work_area_at


_API_ERRORS = (AttributeError, OSError, TypeError, ValueError, ctypes.ArgumentError)


@lru_cache(maxsize=2)
def _load_library(name):
    # 只缓存两个DLL句柄，避免刷新时反复装载；不在导入时改变进程状态。
    return ctypes.WinDLL(name, use_last_error=True)


def _function(library, name, args, result):
    function = getattr(library, name)
    function.argtypes = args
    function.restype = result
    return function


def _current_awareness(user, shcore=None):
    """读取manifest/先前调用锁定的有效模式，避免把拒绝误报为成功。"""
    try:
        context = _function(user, 'GetThreadDpiAwarenessContext', [], ctypes.c_void_p)()
        awareness = _function(user, 'GetAwarenessFromDpiAwarenessContext',
                              [ctypes.c_void_p], ctypes.c_int)(context)
        if awareness == 2:
            try:
                equal = _function(user, 'AreDpiAwarenessContextsEqual',
                                  [ctypes.c_void_p, ctypes.c_void_p], wintypes.BOOL)
                if equal(context, -4):
                    return 'per-monitor-v2'
            except _API_ERRORS:
                pass
            return 'per-monitor'
        if awareness in (0, 1):
            return ('unaware', 'system')[awareness]
    except _API_ERRORS:
        pass
    if shcore is None:
        try:
            shcore = _load_library('shcore')
        except _API_ERRORS:
            pass
    if shcore is not None:
        try:
            value = ctypes.c_int()
            get = _function(shcore, 'GetProcessDpiAwareness',
                            [wintypes.HANDLE, ctypes.POINTER(ctypes.c_int)], ctypes.c_long)
            if get(None, ctypes.byref(value)) == 0 and value.value in (0, 1, 2):
                return ('unaware', 'system', 'per-monitor')[value.value]
        except _API_ERRORS:
            pass
    try:
        aware = _function(user, 'IsProcessDPIAware', [], wintypes.BOOL)()
        return 'system' if aware else 'unaware'
    except _API_ERRORS:
        pass
    return None


def enable_process_dpi_awareness() -> str:
    """尽早请求PerMonitorV2，并报告实际模式；旧Windows逐级安全降级。"""
    try:
        user = _load_library('user32')
    except _API_ERRORS:
        return 'unavailable'
    try:
        request = _function(user, 'SetProcessDpiAwarenessContext',
                            [ctypes.c_void_p], wintypes.BOOL)
        if request(-4):
            return 'per-monitor-v2'
    except _API_ERRORS:
        pass
    existing = _current_awareness(user)
    if existing and existing != 'unaware':
        return existing
    shcore = None
    try:
        shcore = _load_library('shcore')
        request = _function(shcore, 'SetProcessDpiAwareness', [ctypes.c_int], ctypes.c_long)
        if request(2) == 0:
            return 'per-monitor'
    except _API_ERRORS:
        pass
    existing = _current_awareness(user, shcore)
    if existing and existing != 'unaware':
        return existing
    try:
        if _function(user, 'SetProcessDPIAware', [], wintypes.BOOL)():
            return 'system'
    except _API_ERRORS:
        pass
    return _current_awareness(user, shcore) or 'unavailable'


def _positive_dpi(value):
    try:
        number = float(value)
        if math.isfinite(number) and number > 0:
            return max(1, round(number))
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def _tk_dpi(root):
    try:
        return _positive_dpi(float(root.tk.call('tk', 'scaling')) * 72) or 96
    except (AttributeError, TypeError, ValueError, tk.TclError):
        return 96


def window_dpi(root) -> int:
    """优先读取窗口有效DPI；不使用易被虚拟化的GetDeviceCaps。"""
    try:
        user = _load_library('user32')
    except _API_ERRORS:
        return _tk_dpi(root)
    try:
        get = _function(user, 'GetDpiForWindow', [wintypes.HWND], wintypes.UINT)
        dpi = _positive_dpi(get(root.winfo_id()))
        if dpi:
            return dpi
    except _API_ERRORS + (tk.TclError,):
        pass
    try:
        dpi = _positive_dpi(_function(user, 'GetDpiForSystem', [], wintypes.UINT)())
        if dpi:
            return dpi
    except _API_ERRORS:
        pass
    return _tk_dpi(root)


def _valid_work_area(value):
    try:
        area = tuple(int(coordinate) for coordinate in value)
        if len(area) == 4 and area[2] > area[0] and area[3] > area[1]:
            return area
    except (TypeError, ValueError, OverflowError):
        pass
    return None


class TkDisplayRuntime:
    """由现有UI轮询驱动；自身不创建定时器或进行窗口布局。"""
    def __init__(self, root, *, dpi_reader=window_dpi, work_areas=screen_bounds):
        self.root = root
        self._dpi_reader = dpi_reader
        self._work_areas = work_areas
        self._dpi = None
        self._work_area = None

    def work_areas(self):
        try:
            return [valid for area in self._work_areas() if (valid:=_valid_work_area(area)) is not None]
        except _API_ERRORS + (tk.TclError,):
            return [self._work_area] if self._work_area is not None else []

    def _refresh_fonts(self):
        # Tk版本/平台对scaling变更后的命名字体缓存失效行为不同。
        # 重设原始字号使所有现有控件重算像素度量，点值与负像素值均不乘DPI。
        names = self.root.tk.call('font', 'names')
        if isinstance(names, str):
            names = self.root.tk.splitlist(names)
        for name in names:
            try:
                size = self.root.tk.call('font', 'configure', name, '-size')
                self.root.tk.call('font', 'configure', name, '-size', size)
            except tk.TclError:
                # 字体可能在另一个控件销毁回调中已被删除。
                continue

    def refresh(self) -> DisplayMetrics:
        try:
            dpi = _positive_dpi(self._dpi_reader(self.root))
        except _API_ERRORS + (tk.TclError,):
            dpi = None
        dpi = dpi or self._dpi or _tk_dpi(self.root)
        if dpi != self._dpi:
            self.root.tk.call('tk', 'scaling', dpi / 72)
            self._refresh_fonts()
            self._dpi = dpi
        try:
            areas = self.work_areas()
            try:
                x,y=self.root.winfo_x(),self.root.winfo_y()
                area=work_area_at(areas,x,y)
            except (AttributeError,tk.TclError):
                area=areas[0] if areas else None
        except _API_ERRORS + (IndexError, tk.TclError):
            area = None
        if area is None:
            area = self._work_area
        if area is None:
            try:
                area = _valid_work_area((0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()))
            except (AttributeError, tk.TclError):
                pass
        self._work_area = area or (0, 0, 1, 1)
        return DisplayMetrics(self._work_area, dpi / 96)
