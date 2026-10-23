"""动作连按会话与显式功能键请求；状态与UI线程隔离。"""
from dataclasses import dataclass, replace
import queue
import threading
from time import monotonic, monotonic_ns

from .hotkeys import parse_function_key


INPUT_MARKER = 0x484F5453
ENTER, ESCAPE = 0x0D, 0x1B
_MODIFIER_KEYS = frozenset((0x10, 0x11, 0x12, 0x5B, 0x5C,
                            0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5))
_LIVE_STATES = frozenset(('ready', 'running', 'paused'))


@dataclass(frozen=True)
class RepeatStatus:
    state: str
    detail: str = ''
    generation: int = 0


def parse_repeat_key(text):
    return parse_function_key(text)


def validate_repeat_config(action, interval_ms):
    if action not in ('S', 'YS'):
        raise ValueError('连按动作只能选择 S 或 Y+S。')
    if type(interval_ms) is not int or not 20 <= interval_ms <= 5000:
        raise ValueError('按键间隔必须是 20–5000 毫秒的整数。')


class RepeatSession:
    PREPARE_TIMEOUT = 30.

    def __init__(self, backend, emit, *, binding='F8', clock=monotonic,
                 can_start=lambda: True, can_send=lambda: True,
                 action='S', interval_ms=20):
        validate_repeat_config(action, interval_ms)
        self.backend, self.emit, self.clock = backend, emit, clock
        self.action, self.interval_ms = action, interval_ms
        self.action_label = 'Y+S' if action == 'YS' else 'S'
        self._interval_ns = interval_ms * 1_000_000
        self._step = 0
        # 默认直接使用整数单调时钟；注入的秒时钟只在边界处舍入一次。
        # 避免不同起点的浮点加法把恰好20ms的tick误判为尚未到期。
        self._clock_ns = monotonic_ns if clock is monotonic else lambda: round(clock() * 1_000_000_000)
        self.can_start, self.can_send = can_start, can_send
        self.toggle_vk = parse_repeat_key(binding)
        self.binding = f'F{self.toggle_vk - 0x6f}'
        self.status = RepeatStatus('idle')
        self.target = None
        self._toggle_held = self._toggle_captured = False
        self._next_send = self._prepare_deadline = None

    def _status(self, state, detail=''):
        status = RepeatStatus(state, detail)
        if status != self.status:
            self.status = status
            self.emit(status)

    def prepare(self):
        self.target = self._next_send = None
        self._step = 0
        self._toggle_held = self.backend.toggle_down(self.toggle_vk)
        self._toggle_captured = False
        self._prepare_deadline = self.clock() + self.PREPARE_TIMEOUT
        self._status('ready', f'回到游戏，按{self.binding}开始{self.action_label}连按；30秒内有效。')

    def cancel(self, detail=None):
        if detail is None:detail = f'本次{self.action_label}连按已结束，快捷键恢复原有操作。'
        self._next_send = None
        self._status('idle', detail)

    def fail(self, failure):
        self._next_send = None
        self._status('error', self.action_label + '连按已停止：' + str(failure))

    def key(self, vk, down, extra_info):
        if extra_info == INPUT_MARKER:
            return False
        if vk == self.toggle_vk:
            if not down:
                consumed = self._toggle_captured
                self._toggle_held = self._toggle_captured = False
                return consumed
            if self._toggle_held:
                return self._toggle_captured
            self._toggle_held = True
            if self.status.state == 'ready' and self.clock() >= self._prepare_deadline:
                self.cancel(f'准备已超时，请重新准备{self.action_label}连按。')
            if self.status.state not in _LIVE_STATES:
                return False
            try:
                if self.backend.modifiers_down():
                    self.cancel(f'组合键已结束本次{self.action_label}连按。')
                    return False
                if self.status.state == 'ready' and not self.can_start():
                    return False
                target = self.backend.foreground_target()
            except OSError as failure:
                self.fail(failure)
                return False
            if target is None or (self.target is not None and self.target != target):
                if self.status.state != 'ready':
                    self.cancel(f'已离开目标游戏窗口，本次{self.action_label}连按结束。')
                return False
            self.target = target
            self._toggle_captured = True
            if self.status.state == 'running':
                self._next_send = None
                self._status('paused', f'{self.action_label}连按已暂停，按{self.binding}继续；Esc结束。')
            else:
                self._next_send = self._clock_ns()
                self._status('running', f'正在{self.action_label}连按，按{self.binding}暂停；Esc结束。')
            return True
        if down and self.status.state in _LIVE_STATES:
            try:
                if vk in (ENTER, ESCAPE) or vk in _MODIFIER_KEYS or self.backend.modifiers_down():
                    self.cancel(f'聊天、退出或组合键已结束本次{self.action_label}连按。')
            except OSError as failure:
                self.fail(failure)
        return False

    def mouse(self):
        # 右键保持游戏原有移动操作，所有鼠标输入均透传。
        pass

    def tick(self):
        now = self.clock()
        if self.status.state == 'ready' and now >= self._prepare_deadline:
            self.cancel(f'准备已超时，请重新准备{self.action_label}连按。')
        if self.status.state in ('running', 'paused'):
            try:
                if self.backend.foreground_target() != self.target:
                    self.cancel(f'已切出目标游戏窗口，本次{self.action_label}连按结束。')
                elif self.status.state == 'running' and self._clock_ns() >= self._next_send:
                    started = self._clock_ns()
                    if not self._send_next_key():
                        self.cancel(f'游戏窗口或会话已变化，本次{self.action_label}连按结束。')
                    else:
                        completed = self._clock_ns()
                        # 快速发送从本组起点计时，避免10ms消息泵把20ms量化为30ms。
                        # 慢发送不追补；完成后等待完整间隔，也不累加过时deadline。
                        deadline = started + self._interval_ns
                        self._next_send = (completed + self._interval_ns
                                           if completed >= deadline else deadline)
            except OSError as failure:
                self.fail(failure)
        return self.status.state in _LIVE_STATES or self._toggle_captured

    def _send_next_key(self):
        if self.action == 'S':
            return self.backend.send_s(self.target, allowed=self.can_send)
        send = getattr(self.backend, 'send_repeat_key', None)
        if not callable(send):
            raise OSError('当前按键后端不支持 Y+S 动作。')
        sent = send((0x59, 0x53)[self._step], self.target, allowed=self.can_send)
        if sent:self._step = 1 - self._step
        return sent


def _native_backend():
    from .repeat_native import Win32RepeatBackend
    return Win32RepeatBackend()


class DirectRepeatSession(RepeatSession):
    """监听本身不发送输入；新按键请求经UI确认后才进入连按。"""
    def __init__(self, *args, next_generation, **kwargs):
        super().__init__(*args, **kwargs)
        self.next_generation = next_generation
        self.generation = 0
        self._enter_held = False

    def _status(self, state, detail=''):
        if (state == 'running') != (self.status.state == 'running'):
            try:
                self.backend.set_repeat_active(state == 'running')
            except OSError as failure:
                self.target = self._next_send = None
                detail = (detail + '；' if state == 'error' and detail else self.action_label + '连按已停止：') + str(failure)
                state = 'error'
        status = RepeatStatus(state, detail, self.generation)
        if status != self.status:
            self.status = status
            self.emit(status)

    def listen(self):
        self._toggle_held = self.backend.toggle_down(self.toggle_vk)
        self._toggle_captured = False
        self._status('idle', f'游戏中按{self.binding}开始{self.action_label}连按。')

    def cancel(self, detail=None):
        if self.status.state == 'error':return
        if detail is None:detail = f'本次{self.action_label}连按已取消，可在游戏中再次按功能键开始。'
        self.target = self._next_send = None
        self._status('idle', detail)

    def key(self, vk, down, extra_info):
        if extra_info == INPUT_MARKER:
            return False
        if vk == self.toggle_vk:
            if not down:
                captured = self._toggle_captured
                self._toggle_held = self._toggle_captured = False
                return captured
            if self._toggle_held:
                return self._toggle_captured
            self._toggle_held = True
            try:
                if self.backend.modifiers_down():
                    if self.status.state in ('requested', 'running'):
                        self.cancel(f'组合键已取消本次{self.action_label}连按。')
                    return False
                target = self.backend.foreground_target()
                if self.status.state in ('requested', 'running'):
                    if target is None or target != self.target:
                        self.cancel(f'已离开目标游戏窗口，本次{self.action_label}连按取消。')
                        return False
                    self._toggle_captured = True
                    self.cancel(f'已按{self.binding}取消{self.action_label}连按。')
                    return True
                if self.status.state == 'error':
                    return False
                if target is None:
                    return False
                self.target = target
                self._step = 0
                self._toggle_captured = True
                self.generation = self.next_generation()
                self._status('requested', f'正在启动{self.action_label}连按，按{self.binding}取消。')
                return True
            except OSError as failure:
                self.fail(failure)
                return False
        if vk == ENTER:
            if not down:
                self._enter_held = False
                return False
            if self._enter_held:return False
            self._enter_held = True
        if down:
            try:
                if (self.status.state in ('requested', 'running')
                        and (vk in (ENTER, ESCAPE) or vk in _MODIFIER_KEYS or self.backend.modifiers_down())):
                    self.cancel(f'聊天、退出或组合键已取消本次{self.action_label}连按。')
            except OSError as failure:
                self.fail(failure)
        return False

    def tick(self):
        try:
            if self.status.state == 'requested':
                if self.backend.foreground_target() != self.target or self.backend.modifiers_down():
                    self.cancel(f'游戏窗口或按键状态已变化，本次{self.action_label}连按取消。')
                elif self.can_start():
                    self._next_send = self._clock_ns()
                    self._status('running', f'正在{self.action_label}连按，按{self.binding}取消。')
            if self.status.state == 'running':
                super().tick()
        except OSError as failure:
            self.fail(failure)
        # 空闲仍保留监听，失败则退出，避免自动重试原生输入。
        return self.status.state != 'error'


class RepeatHelper:
    """一次准备对应一个线程/钩子对；取消与关闭跨过发送栅栏后返回。"""
    def __init__(self, *, backend_factory=_native_backend, clock=monotonic):
        self.backend_factory, self.clock = backend_factory, clock
        self.thread = self._backend = None
        self._closed = False
        self._lock = threading.Lock()
        self._dispatch = threading.RLock()
        self._events = queue.SimpleQueue()
        self._cancel = self._stop = self._displayed = None
        self._generation = 0
        self._direct_mode = False
        self._binding = None
        self._action, self._interval_ms = 'S', 200
        self._session = self._latest_status = None

    @property
    def active(self):
        alive = self.thread is not None and self.thread.is_alive()
        if not self._direct_mode:
            return alive
        with self._lock:
            return (alive and self._latest_status is not None
                    and self._latest_status.state in ('requested', 'running')
                    and not self._cancel.is_set())

    @property
    def listening(self):
        return self._direct_mode and self.thread is not None and self.thread.is_alive()

    def start_listening(self, binding='F8', *, action='S', interval_ms=20):
        validate_repeat_config(action, interval_ms)
        binding = f'F{parse_repeat_key(binding) - 0x6f}'
        with self._lock:
            if self._closed:
                return False
            if (self.listening and (self._binding, self._action, self._interval_ms)
                    == (binding, action, interval_ms)):
                return True
        if not self._stop_current():
            return False
        return self._launch(binding, direct=True, action=action, interval_ms=interval_ms)

    def prepare(self, binding='F8', *, action='S', interval_ms=20):
        validate_repeat_config(action, interval_ms)
        vk = parse_repeat_key(binding)
        binding = f'F{vk - 0x6f}'
        return self._launch(binding, direct=False, action=action, interval_ms=interval_ms)

    def _launch(self, binding, *, direct, action, interval_ms):
        with self._lock:
            if self._closed or (self.thread is not None and self.thread.is_alive()):
                return False
            self._direct_mode, self._binding = direct, binding
            self._action, self._interval_ms = action, interval_ms
            self._cancel, self._stop, self._displayed = (
                threading.Event(), threading.Event(), threading.Event())
            self._generation += 1
            self._latest_status = None
            if not direct:
                label = 'Y+S' if action == 'YS' else 'S'
                self._events.put(RepeatStatus('preparing', f'正在准备{label}连按…', self._generation))
            self.thread = threading.Thread(
                target=self._run,
                args=(self._generation, binding, self._cancel, self._stop, self._displayed,
                      direct, action, interval_ms),
                name='repeat-helper', daemon=True)
            self.thread.start()
            return True

    def _run(self, generation, binding, cancelled, stopped, displayed, direct=False,
             action='S', interval_ms=20):
        session = None
        def emit(status):
            if direct:
                with self._lock:
                    if status.state == 'requested':
                        cancelled.clear()
                        displayed.clear()
                    self._latest_status = status
                self._events.put(status)
            else:
                self._events.put(replace(status, generation=generation))
        def next_generation():
            with self._lock:
                self._generation += 1
                return self._generation
        def allowed():
            return not cancelled.is_set() and not stopped.is_set()
        try:
            backend = self.backend_factory()
            with self._lock:
                self._backend = backend
            arguments = dict(binding=binding, clock=self.clock,
                             can_start=displayed.is_set, can_send=allowed,
                             action=action, interval_ms=interval_ms)
            session = (DirectRepeatSession(backend, emit, next_generation=next_generation, **arguments)
                       if direct else RepeatSession(backend, emit, **arguments))
            if direct:
                session.generation = generation
            with self._lock:
                self._session = session
            def ready():
                with self._dispatch:
                    if allowed():
                        session.listen() if direct else session.prepare()
                    else:
                        session.cancel()
            def tick():
                with self._dispatch:
                    if not allowed():
                        session.cancel()
                    return session.tick()
            def key(vk, down, extra_info):
                with self._dispatch:
                    if not allowed():
                        session.cancel()
                    return session.key(vk, down, extra_info)
            if not stopped.is_set():
                run = backend.run_listening if direct else backend.run
                run(key, session.mouse, tick, ready, stopped)
            if session.status.state != 'error':
                session.cancel(session.status.detail if session.status.state == 'idle' else '')
        except Exception as failure:
            if session is None:
                label = 'Y+S' if action == 'YS' else 'S'
                emit(RepeatStatus('error', label + '连按未能准备：' + str(failure), generation))
            else:
                session.fail(failure)
        finally:
            with self._lock:
                self._backend = None
                self._session = None

    def drain_status(self):
        statuses = []
        while True:
            try:
                status = self._events.get_nowait()
                if self.accepts_status(status):
                    statuses.append(status)
            except queue.Empty:
                return statuses

    def cancel(self, generation=None):
        with self._lock:
            if generation is not None and generation != self._generation:
                return False
            if self._cancel is not None:
                self._cancel.set()
            if self._displayed is not None:
                self._displayed.clear()
        with self._dispatch:
            with self._lock:
                if generation is not None and generation != self._generation:
                    return False
                session = self._session
            if self._direct_mode and session is not None:
                session.cancel()
        return True

    def accepts_status(self, status):
        with self._lock:
            cancelled = self._cancel is not None and self._cancel.is_set()
            if self._direct_mode:
                return (status.generation == self._generation and status == self._latest_status
                        and (not cancelled or status.state in ('idle', 'error')))
            return status.generation == self._generation and (
                not cancelled or status.state in ('idle', 'error'))

    def confirm_ready(self, generation):
        with self._lock:
            if (generation == self._generation and self._displayed is not None
                    and not self._cancel.is_set() and not self._stop.is_set()
                    and not self._closed
                    and (not self._direct_mode or (self._latest_status is not None
                                                  and self._latest_status.state == 'requested'))):
                self._displayed.set()
                return True
        return False

    def close(self):
        with self._lock:
            self._closed = True
        self._stop_current()

    def _stop_current(self):
        with self._lock:
            if self._cancel is not None:
                self._cancel.set()
            if self._stop is not None:
                self._stop.set()
            if self._displayed is not None:
                self._displayed.clear()
            backend, thread = self._backend, self.thread
        with self._dispatch:
            pass
        try:
            if backend is not None:
                backend.stop()
        finally:
            if thread is not None and thread is not threading.current_thread():
                thread.join(2)
        return thread is None or not thread.is_alive()
