"""用户显式准备的开箱会话；状态机不接触Tk，也不默认建立系统钩子。"""
from dataclasses import dataclass, replace
import queue
import threading
from time import monotonic


INPUT_MARKER = 0x484F5453
SPACE, ESCAPE = 0x20, 0x1B


@dataclass(frozen=True)
class LootStatus:
    state: str
    detail: str = ''
    generation: int = 0


class LootSession:
    INTERVAL = .9
    PREPARE_TIMEOUT = 30.

    def __init__(self, backend, emit, *, clock=monotonic, can_start=lambda: True,
                 can_send=lambda: True):
        self.backend, self.emit, self.clock = backend, emit, clock
        self.can_start, self.can_send = can_start, can_send
        self.status = LootStatus('idle')
        self.target = None
        self._space_held = self._space_captured = False
        self._next_send = self._prepare_deadline = None

    def _status(self, state, detail=''):
        status = LootStatus(state, detail)
        if status != self.status:
            self.status = status
            self.emit(status)

    def prepare(self):
        self.target = None
        self._next_send = None
        self._space_held = self.backend.space_down()
        self._space_captured = False
        self._prepare_deadline = self.clock() + self.PREPARE_TIMEOUT
        self._status('ready', '回到游戏补给箱页，按空格开始；30秒内有效。')

    def cancel(self, detail='本次模式已结束，空格恢复原有操作。'):
        self._next_send = None
        self._status('idle', detail)

    def fail(self, failure):
        self._next_send = None
        self._status('error', '开箱助手已停止：' + str(failure))

    def key(self, vk, down, extra_info):
        if extra_info == INPUT_MARKER:
            return False
        if vk == SPACE:
            if not down:
                consumed = self._space_captured
                self._space_held = self._space_captured = False
                return consumed
            if self._space_held:
                return self._space_captured
            self._space_held = True
            if self.status.state == 'ready' and self.clock() >= self._prepare_deadline:
                self.cancel('准备已超时，请重新点准备开箱。')
            if self.status.state not in ('ready', 'running', 'paused'):
                return False
            if self.status.state == 'ready' and not self.can_start():
                return False
            try:
                if self.backend.modifiers_down():
                    self.cancel('组合键已结束本次开箱模式。')
                    return False
                target = self.backend.foreground_target()
            except OSError as failure:
                self.fail(failure)
                return False
            if target is None or (self.target is not None and self.target != target):
                if self.status.state != 'ready':
                    self.cancel('已离开目标游戏窗口，本次模式结束。')
                return False
            self.target = target
            self._space_captured = True
            if self.status.state == 'running':
                self._next_send = None
                self._status('paused', '已停止，按空格继续；Esc结束本次模式。')
            else:
                self._next_send = self.clock()
                self._status('running', '正在开箱，按空格停止；Esc结束本次模式。')
            return True
        if down and (self.status.state in ('running', 'paused')
                     or (vk == ESCAPE and self.status.state == 'ready')):
            self.cancel('键盘操作已结束本次开箱模式。')
        return False

    def mouse(self):
        # 准备后还需要切回游戏或选择补给箱页；首次空格后才启用此退出条件。
        if self.status.state in ('running', 'paused'):
            self.cancel('鼠标操作已结束本次开箱模式。')

    def tick(self):
        now = self.clock()
        if self.status.state == 'ready' and now >= self._prepare_deadline:
            self.cancel('准备已超时，请重新点准备开箱。')
        if self.status.state in ('running', 'paused'):
            try:
                if self.backend.foreground_target() != self.target:
                    self.cancel('已切出目标游戏窗口，本次模式结束。')
                elif self.status.state == 'running' and now >= self._next_send:
                    if not self.backend.send_space(self.target, allowed=self.can_send):
                        self.cancel('游戏窗口已变化，本次模式结束。')
                    else:
                        # 卡顿后不追补历史按键，每次只发送一组并从此刻重新计时。
                        self._next_send = self.clock() + self.INTERVAL
            except OSError as failure:
                self.fail(failure)
        return self.status.state in ('ready', 'running', 'paused') or self._space_captured


def _native_backend():
    from .loot_native import Win32LootBackend
    return Win32LootBackend()


class LootHelper:
    """生命周期按本次准备隔离；UI只收状态，键盘处理和发送由同一线程串行执行。"""
    def __init__(self, *, backend_factory=_native_backend, clock=monotonic):
        self.backend_factory, self.clock = backend_factory, clock
        self.thread = None
        self._backend = None
        self._closed = False
        self._lock = threading.Lock()
        self._events = queue.SimpleQueue()
        self._cancel = self._stop = None
        self._displayed = None
        self._generation = 0

    @property
    def active(self):
        return self.thread is not None and self.thread.is_alive()

    def prepare(self):
        with self._lock:
            if self._closed or self.active:
                return False
            self._cancel, self._stop = threading.Event(), threading.Event()
            self._displayed = threading.Event()
            self._generation += 1
            self._events.put(LootStatus('preparing', '正在准备开箱快捷操作…', self._generation))
            self.thread = threading.Thread(target=self._run, args=(self._generation,),
                                           name='loot-helper', daemon=True)
            self.thread.start()
            return True

    def _run(self, generation):
        session = None
        def emit(status):
            self._events.put(replace(status, generation=generation))

        def allowed():
            return not self._cancel.is_set() and not self._stop.is_set()

        try:
            backend = self.backend_factory()
            with self._lock:
                self._backend = backend
            session = LootSession(backend, emit, clock=self.clock,
                                  can_start=self._displayed.is_set, can_send=allowed)

            def ready():
                if self._cancel.is_set() or self._stop.is_set():
                    session.cancel()
                else:
                    session.prepare()

            def tick():
                if self._cancel.is_set():
                    session.cancel()
                return session.tick()

            def key(vk, down, extra_info):
                if self._cancel.is_set() or self._stop.is_set():
                    session.cancel()
                return session.key(vk, down, extra_info)

            if not self._stop.is_set():
                backend.run(key, session.mouse, tick, ready, self._stop)
            if session.status.state != 'error':
                session.cancel(session.status.detail if session.status.state == 'idle' else '')
        except Exception as failure:
            # 原生安装/回调错误必须变成显式错误状态，不能留一个假“运行中”。
            if session is not None:
                session.fail(failure)
            else:
                emit(LootStatus('error', '开箱助手未能准备：' + str(failure)))
        finally:
            with self._lock:
                self._backend = None

    def drain_status(self):
        statuses = []
        while True:
            try:
                status = self._events.get_nowait()
                if self.accepts_status(status):
                    statuses.append(status)
            except queue.Empty:
                return statuses

    def cancel(self):
        if self._cancel is not None:
            self._cancel.set()
        if self._displayed is not None:
            self._displayed.clear()

    def accepts_status(self, status):
        cancelled = self._cancel is not None and self._cancel.is_set()
        return status.generation == self._generation and (
            not cancelled or status.state in ('idle', 'error'))

    def confirm_ready(self, generation):
        if (generation == self._generation and self._displayed is not None
                and not self._cancel.is_set() and not self._stop.is_set()):
            self._displayed.set()

    def close(self):
        with self._lock:
            self._closed = True
            if self._stop is not None:
                self._stop.set()
            backend, thread = self._backend, self.thread
        try:
            if backend is not None:
                backend.stop()
        finally:
            if thread is not None and thread is not threading.current_thread():
                thread.join(2)
