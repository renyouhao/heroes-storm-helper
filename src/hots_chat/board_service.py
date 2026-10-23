"""Tab双击播报：hook只入有界队列，读取和发送在独立worker串行完成。"""
from dataclasses import dataclass
import queue
import threading
import time

from .loot_native import INPUT_MARKER
from .talent_broadcast import InputContext, TalentBroadcastConfig, TalentBroadcastSession


@dataclass(frozen=True)
class BoardStatus:
    state: str
    detail: str = ''
    generation: int = 0


def hit_test(snapshot, x, y):
    """相邻行矩形可能重叠；没有唯一命中就不猜某一行。"""
    hits = [hit for hit in snapshot.hits if hit.rect[0] <= x < hit.rect[2]
            and hit.rect[1] <= y < hit.rect[3]]
    return hits[0] if len(hits) == 1 else None


def _closed_input_busy():
    return True


class BoardHelper:
    def __init__(self, *, backend_factory=None, reader_factory=None, sender_factory=None,
                 busy_provider=lambda:False, input_locks=(), clock=time.monotonic, diagnostics=None):
        self.backend_factory = backend_factory or self._new_backend
        self.reader_factory = reader_factory or self._new_reader
        self.sender_factory = sender_factory or self._new_sender
        self.busy_provider, self.input_locks, self.clock = busy_provider, tuple(input_locks), clock
        self.diagnostics = diagnostics
        self._diagnostic_state = None
        self._control = threading.RLock()
        self._inputs = queue.Queue(maxsize=64)
        self._statuses = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._busy = threading.Event()
        self._enabled = self._closed = self._suspended = False
        self._generation = self._revision = 0
        self._backend = None
        self._session = None
        self.thread = None
        self._emit('disabled', '双击播报已关闭。')

    @staticmethod
    def _new_backend():
        from .board_native import Win32BoardBackend
        return Win32BoardBackend()

    @staticmethod
    def _new_reader(backend):
        from .acquisition.board_source import BoardReader
        from .acquisition.board_input import BoardInputReader
        from .talent_catalog import TalentCatalog
        inputs = BoardInputReader()
        return BoardReader(text_provider=TalentCatalog().resolve,
            viewport_converter=backend.viewport_converter, input_state_provider=inputs.inspect)

    def _new_sender(self, backend, reader):
        from .board_sender import TeamChatSender
        return lambda action, **kwargs:TeamChatSender(backend,
            inspect=lambda:reader.inspect_input(action.pid, action.hwnd),
            diagnostics=self.diagnostics).send_result(action, **kwargs)

    @property
    def busy(self):return self._busy.is_set()

    def _emit(self, state, detail='', generation=None):
        with self._control:
            if generation is not None and generation != self._generation:return
            status = BoardStatus(state, detail, self._generation)
            try:self._statuses.get_nowait()
            except queue.Empty:pass
            self._statuses.put_nowait(status)
            signature = (self._generation,state)
            if self.diagnostics is not None and signature != self._diagnostic_state:
                self._diagnostic_state = signature
                try:self.diagnostics.record('hotkey.state',stage='broadcast',outcome=state,generation=self._generation)
                except Exception:pass

    def drain_status(self):
        result = []
        while True:
            try:
                status = self._statuses.get_nowait()
                if status.generation == self._generation:result.append(status)
            except queue.Empty:return result

    def _enqueue(self, kind, args=(), *, invalidate=False):
        with self._control:
            if self._stop.is_set():return
            if invalidate:self._revision += 1
            # Preserve the revision at the hook event, before any slow read.
            event = (kind, args, self.clock(), self._revision)
            try:self._inputs.put_nowait(event)
            except queue.Full:
                self._revision += 1
                while True:
                    try:self._inputs.get_nowait()
                    except queue.Empty:break
                try:self._inputs.put_nowait(('cancel', (), self.clock(), self._revision))
                except queue.Full:pass

    def set_suspended(self, value):
        value = bool(value)
        with self._control:
            if value and not self._suspended:self._enqueue('cancel',invalidate=True)
            self._suspended = value

    def set_enabled(self, value, *, wait=True):
        if not value:
            self._stop.set()
        with self._control:
            if not value:self._revision += 1
            if self._closed:return False
            value = bool(value)
            if value == self._enabled:return True
            if value and self.thread is not None and self.thread.is_alive():return False
            self._enabled = value
            self._generation += 1
            if value:
                self._stop = threading.Event()
                self._inputs = queue.Queue(maxsize=64)
                self.thread = threading.Thread(target=self._run, args=(self._generation,self._stop),
                                               name='board-worker', daemon=True)
                self.thread.start()
            else:
                if self._backend is not None:
                    try:self._backend.stop()
                    except Exception:pass
                self._emit('disabled', '双击播报已关闭。')
            thread = self.thread
        if not value and wait and thread is not None and thread is not threading.current_thread():thread.join(1)
        return True

    def _run(self, generation, stopped):
        reader = hook_thread = None
        failure = []
        session = None
        try:
            backend = self.backend_factory()
            self._backend = backend
            reader = self.reader_factory(backend)
            sender = self.sender_factory(backend, reader)
            milliseconds, size = backend.double_click_settings()
            configured = TalentBroadcastSession(double_click_ms=milliseconds, double_click_size=size,
                                                 config=TalentBroadcastConfig(enabled=True))
            if self._session is None:self._session = configured
            else:
                # Validate fresh Windows settings above, then keep the existing
                # session's attempt history across disable/enable cycles.
                self._session.double_click_seconds = configured.double_click_seconds
                self._session.double_click_size = configured.double_click_size
                self._session.configure(configured.config)
            session = self._session
            def key(vk, down, marker):
                if marker != INPUT_MARKER and down:
                    self._enqueue('cancel',invalidate=True)
                return False
            def mouse(x, y, down, button, injected, marker=0):
                # Windows的注入位不能区分用户桌面输入与助手自身输入。
                # 保留原始标记，只过滤本助手的事件；后续仍复核前台与面板。
                if marker != INPUT_MARKER:
                    self._enqueue('mouse', (x,y,down,button),invalidate=bool(down))
                return False
            def hooks():
                try:backend.run(key, mouse, lambda:not stopped.is_set(),
                                lambda:self._emit('idle', 'Tab内双击一项天赋或数据，播报到己方团队。', generation), stopped)
                except Exception as error:failure.append(error)
                finally:stopped.set()
            hook_thread = threading.Thread(target=hooks, name='board-hooks', daemon=True)
            hook_thread.start()
            snapshot = target = point = None
            pending_revision = None
            last_status = None

            def publish_status():
                nonlocal last_status
                if session.status != last_status:
                    last_status = session.status
                    self._emit(last_status.state,last_status.detail,generation)

            def has_gesture():
                return session._pending is not None or session._first_click is not None

            def fresh():
                nonlocal snapshot, target
                target = backend.foreground_target()
                if target is None:return None, InputContext()
                snapshot = reader.poll(target.pid, target.hwnd, self.clock())
                current = backend.foreground_target()
                context = InputContext(pid=target.pid,hwnd=target.hwnd,match_token=snapshot.match_token,
                    foreground=current==target,chat_active=snapshot.chat_active,menu_active=snapshot.menu_active,
                    quick_input_busy=self._suspended or bool(self.busy_provider()))
                return (hit_test(snapshot,*point) if point else None), context

            while not stopped.is_set():
                try:kind,args,event_at,event_revision = self._inputs.get(timeout=.05)
                except queue.Empty:kind,args,event_at,event_revision = '',(),self.clock(),self._revision
                if kind == 'cancel':
                    if has_gesture():
                        session.cancel()
                        pending_revision = None
                        publish_status()
                elif kind == 'mouse':
                    x,y,down,button = args
                    if button != 'left':
                        if has_gesture():
                            session.cancel('其他鼠标操作已取消本次播报。')
                            pending_revision = None
                            publish_status()
                        continue
                    if not 0 <= self.clock()-event_at <= .25:
                        session.cancel('点击处理超时，请重新双击。')
                        pending_revision = None
                        publish_status()
                        continue
                    if down:
                        point = (x,y)
                        hit, context = fresh()
                        if not context.foreground:
                            if has_gesture():
                                session.cancel('已离开游戏，本次播报取消。')
                                pending_revision = None
                                publish_status()
                            continue
                        if context.chat_active is True:
                            session.cancel('游戏聊天框仍打开，请先按 Esc 关闭，再重新双击。')
                            pending_revision = None
                            publish_status()
                            continue
                    else:hit,context = None,InputContext()
                    previous = session._pending
                    session.mouse(x,y,down,hit,context,self.clock(),button=button,event_at=event_at)
                    publish_status()
                    if down and session._pending is not None and session._pending is not previous:
                        pending_revision = event_revision
                if session._pending is None:
                    pending_revision = None
                    continue
                if pending_revision is None or pending_revision != self._revision:
                    session.cancel('点击后的输入操作已取消本次播报。')
                    pending_revision = None
                    publish_status()
                    continue
                if self._suspended or self.busy_provider():
                    session.cancel('其他输入操作正在进行，请稍后重新双击。')
                    publish_status()
                    continue
                if not backend.input_idle():continue
                locks = []
                try:
                    for lock in self.input_locks:
                        if not lock.acquire(blocking=False):break
                        locks.append(lock)
                    if len(locks) != len(self.input_locks):continue
                    self._busy.set()
                    revision = pending_revision
                    def allowed():
                        return (not stopped.is_set() and self._enabled and not self._closed
                                and not self._suspended and generation==self._generation
                                and revision==self._revision and not self.busy_provider())
                    if not allowed():continue
                    hit, context = fresh()
                    action = session.tick(hit,context,self.clock())
                    if action is not None:
                        session.dispatch(action,sender,hit,context,self.clock(),allowed=allowed)
                    publish_status()
                finally:
                    self._busy.clear()
                    for lock in reversed(locks):lock.release()
        except Exception:
            self._emit('error', '双击播报已停止，请关闭后重试。', generation)
        finally:
            stopped.set()
            cleanup_failed = False
            cleanups = (
                lambda:session.stop() if session is not None else None,
                lambda:self._backend.stop() if self._backend is not None else None,
                lambda:hook_thread.join(1) if hook_thread is not None else None,
                lambda:reader.close() if reader is not None else None,
            )
            try:
                for cleanup in cleanups:
                    try:cleanup()
                    except Exception:cleanup_failed = True
            finally:
                self._busy.clear()
                with self._control:
                    if generation==self._generation:self._enabled=False
            if failure or cleanup_failed:
                self._emit('error', '双击监听未能完整退出，请关闭后重试。', generation)

    def close(self):
        try:self.set_enabled(False)
        finally:
            with self._control:
                self._closed=True
                self.busy_provider=_closed_input_busy
                self.diagnostics=None
