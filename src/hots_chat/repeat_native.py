"""S连按的按需Win32边界；导入不加载DLL、不安装钩子、不创建线程。"""
import ctypes
import threading

from .loot_native import UINT, Win32LootBackend


class Win32RepeatBackend(Win32LootBackend):
    """10ms消息泵驱动串行调度；20ms为目标间隔，不保证硬实时。"""

    def __init__(self, *, user=None, kernel=None, winmm=None):
        super().__init__(user=user, kernel=kernel, timer_interval_ms=10)
        self._winmm = winmm
        self._repeat_run_lock = threading.Lock()
        self._period_lock = threading.Lock()
        self._resolution_active = False

    def toggle_down(self, vk):
        return self.key_down(vk)

    def send_s(self, target, *, allowed=None):
        return self.send_key(0x53, target, allowed=allowed)

    def _resolution_api(self):
        if self._winmm is None:
            self._winmm = ctypes.WinDLL('winmm', use_last_error=True)
        for name in ('timeBeginPeriod', 'timeEndPeriod'):
            function = getattr(self._winmm, name)
            function.argtypes, function.restype = [UINT], UINT
        return self._winmm

    def set_repeat_active(self, active):
        """仅连按运行期申请高精度；取消/异常即配对释放。"""
        with self._period_lock:
            if bool(active) == self._resolution_active:
                return
            winmm = self._resolution_api()
            if active:
                if winmm.timeBeginPeriod(1) != 0:
                    raise OSError('timeBeginPeriod(1) failed')
                self._resolution_active = True
            else:
                # 失败须向调用方报告，但不能重复释放同一次申请。
                self._resolution_active = False
                if winmm.timeEndPeriod(1) != 0:
                    raise OSError('timeEndPeriod(1) failed')

    def run_listening(self, on_key, on_mouse, on_tick, on_ready, stop_event):
        if stop_event.is_set():
            return
        if not self._repeat_run_lock.acquire(blocking=False):
            raise RuntimeError('Repeat message pump is already running')
        failure = None
        try:
            # 普通消息计时器不请求全局高精度；空闲tick不检查游戏或发送输入。
            super().run(on_key, on_mouse, on_tick, on_ready, stop_event)
        except BaseException as error:
            failure = error
        finally:
            try:
                self.set_repeat_active(False)
            except BaseException as error:
                if failure is None:
                    failure = error
            self._repeat_run_lock.release()
        if failure is not None:
            raise failure

    def run(self, on_key, on_mouse, on_tick, on_ready, stop_event):
        if stop_event.is_set():
            return
        if not self._repeat_run_lock.acquire(blocking=False):
            raise RuntimeError('Repeat message pump is already running')
        try:
            if stop_event.is_set():
                return
            # WinMM仅在显式准备的连按worker中加载，开箱路径不改变计时精度。
            winmm = self._winmm
            if winmm is None:
                winmm = ctypes.WinDLL('winmm', use_last_error=True)
            for name in ('timeBeginPeriod', 'timeEndPeriod'):
                function = getattr(winmm, name)
                function.argtypes, function.restype = [UINT], UINT
            if winmm.timeBeginPeriod(1) != 0:
                raise OSError('timeBeginPeriod(1) failed')
            failure = None
            try:
                super().run(on_key, on_mouse, on_tick, on_ready, stop_event)
            except BaseException as error:
                failure = error
            finally:
                try:
                    if winmm.timeEndPeriod(1) != 0:
                        raise OSError('timeEndPeriod(1) failed')
                except BaseException as error:
                    if failure is None:
                        failure = error
            if failure is not None:
                raise failure
        finally:
            self._repeat_run_lock.release()
