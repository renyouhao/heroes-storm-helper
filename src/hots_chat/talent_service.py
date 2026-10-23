"""首选天赋后台调度。关闭时无工作线程、无进程读取、无原生 API 初始化。"""
import queue
import threading
import time


class TalentHelper:
    def __init__(self, *, reader_factory=None, backend_factory=None, session_factory=None,
                 clock=time.monotonic, interval=.05, diagnostics=None):
        self.reader_factory = reader_factory or self._new_reader
        self.backend_factory = backend_factory or self._new_backend
        self.session_factory = session_factory or self._new_session
        self.clock, self.interval, self.diagnostics = clock, interval, diagnostics
        self._dispatch = threading.RLock()
        self._control = threading.RLock()
        self._events = queue.Queue(maxsize=1)
        self._generation = 0
        self._enabled = self._closed = self._suspended = False
        self._stop = threading.Event()
        self._session = self._backend = None
        self.thread = None
        self._last = None
        self._emit('disabled', '自动选择已关闭。')

    @staticmethod
    def _new_reader():
        from .acquisition.talent_source import TalentReader
        return TalentReader()

    @staticmethod
    def _new_backend():
        from .talent_native import Win32TalentBackend
        return Win32TalentBackend()

    @staticmethod
    def _new_session(backend):
        from .talent import TalentSession
        return TalentSession(backend)

    @property
    def running(self):
        """只读运行意图与停止栅栏；不会等待工作线程或查询游戏。"""
        return self._enabled and not self._closed and not self._stop.is_set()

    def _emit(self, state, detail='', tier_id=None, *, source_generation=None):
        with self._control:
            if source_generation is not None and source_generation != self._generation:return
            self._emit_locked(state, detail, tier_id)

    def _emit_locked(self, state, detail, tier_id):
        from .talent import TalentStatus
        status = TalentStatus(state, detail, self._generation, tier_id)
        if status == self._last:return
        self._last = status
        # 只保留最新状态，主窗关闭/忙碌不堆积轮询结果。
        try:self._events.get_nowait()
        except queue.Empty:pass
        self._events.put_nowait(status)
        if self.diagnostics is not None:
            self.diagnostics.record('hotkey.state', stage='talent', outcome=state,
                                    generation=self._generation)

    def set_suspended(self, suspended):
        # 不能等待正在做前台查询的发送锁，allowed须立即看见暂停请求。
        self._suspended = bool(suspended)

    def set_enabled(self, enabled, *, wait=True):
        if not enabled:self._stop.set()
        with self._control:
            if self._closed:return False
            enabled = bool(enabled)
            if enabled == self._enabled:
                if not enabled:self._emit('disabled', '自动选择已关闭。')
                return True
            if enabled and self.thread is not None and self.thread.is_alive():return False
            self._generation += 1
            self._enabled = enabled
            if not enabled:
                self._stop.set()
                if self._session is not None:self._session.stop()
                self._emit('disabled', '自动选择已关闭。')
            else:
                self._stop = threading.Event()
                generation = self._generation
                self._emit('waiting', '等待本地玩家与天赋状态。')
                self.thread = threading.Thread(target=self._run, args=(generation, self._stop),
                                               name='talent-helper', daemon=True)
                self.thread.start()
            thread = self.thread
        if not enabled and wait and thread is not None and thread is not threading.current_thread():
            thread.join(1)
        return True

    def _run(self, generation, stopped):
        from .talent import TalentConfig
        reader = None
        snapshot = None
        target = None
        last_pid = None
        def allowed():
            return (target is not None and not stopped.is_set() and self._enabled and not self._closed
                    and not self._suspended and generation == self._generation)
        def validate():
            if not allowed() or reader is None or snapshot is None:return False
            # 最后发送栅栏重新读取；同PID返回大厅、人工抢选也不能沿用旧快照。
            fresh = reader.poll(snapshot.pid)
            return (allowed() and fresh.status in ('available', 'idle')
                    and fresh.pid == snapshot.pid and fresh.hero_id == snapshot.hero_id
                    and fresh.generation == snapshot.generation
                    and fresh.pending_tier == snapshot.pending_tier
                    and fresh.selected == snapshot.selected)
        try:
            with self._control:
                if stopped.is_set():return
                if self._backend is None:self._backend = self.backend_factory()
                if self._session is None:self._session = self.session_factory(self._backend)
                self._session.configure(TalentConfig(enabled=True))
            reader = self.reader_factory()
            while not stopped.is_set():
                target = self._backend.foreground_target()
                if target is not None:last_pid = target.pid
                # 切出后低频只读，观察大厅失效；不激活窗口、不发送输入。
                snapshot = reader.poll(last_pid) if last_pid is not None else None
                with self._dispatch:
                    if stopped.is_set() or generation != self._generation:return
                    if snapshot is None:
                        self._emit('deferred', '回到游戏后继续检测。', source_generation=generation)
                    else:
                        status = self._session.poll(snapshot, self.clock(), target, allowed=allowed, validate=validate)
                        self._emit(status.state, status.detail, status.tier_id, source_generation=generation)
                delay = self.interval if target is not None else max(.25, self.interval)
                if snapshot is not None and snapshot.status == 'unknown':
                    # 大厅/版本未知会释放读句柄；限速重连避免20Hz枚举进程和模块。
                    delay = max(.5, delay)
                stopped.wait(delay)
        except Exception as failure:
            with self._control:
                if generation == self._generation and not stopped.is_set():
                    self._enabled = False
                    if self._session is not None:self._session.stop()
                    self._emit('error', '天赋辅助已停止：' + str(failure))
        finally:
            if reader is not None:
                try:reader.close()
                except OSError:pass

    def accepts_status(self, status):
        with self._control:return status.generation == self._generation and status == self._last

    def drain_status(self):
        statuses = []
        while True:
            try:
                status = self._events.get_nowait()
                if self.accepts_status(status):statuses.append(status)
            except queue.Empty:return statuses

    def close(self):
        self.set_enabled(False)
        with self._control:self._closed = True
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():thread.join(1)
