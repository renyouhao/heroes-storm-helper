"""把大厅和对局来源合并到一个进程会话，子来源独立恢复。"""
from dataclasses import dataclass
import ntpath
import threading

from .memory import ReadOnlyGame, find_game_processes
from .source import ChatSource
from ..models import AppEvent, ChatEvent
from ..channels import DISPLAY_CHANNELS


_UNKNOWN = object()
_CHANNELS = {'match': frozenset(DISPLAY_CHANNELS),
             'lobby': frozenset(('队伍', '密语'))}
_LABELS = {'match': '对局', 'lobby': '大厅'}
_DIAGNOSTIC_STATES = {
    '已停止':'stopped','已连接':'connected','大厅已连接':'connected',
    '等待游戏':'waiting_game','请选择游戏进程':'select_process','游戏版本未验证':'unsupported_build',
    '自动连接中':'connecting','正在连接游戏聊天':'connecting',
    '自动等待游戏聊天':'waiting_chat','等待对局聊天，自动恢复':'waiting_chat',
    '大厅等待聊天，采集保持开启':'waiting_chat','大厅等待新聊天，持续定位':'discovering',
    '大厅连接等待重试，采集保持开启':'retrying','等待重新连接':'retrying',
}


@dataclass
class _WorkerState:
    source: object = None
    observed: object = _UNKNOWN
    session_revision: int = -1
    generation_floor: int = 0
    latest_generation: int = -1
    state: str = '等待连接'


class PersistentChatSource:
    def __init__(self, selected_pid=None, process_finder=find_game_processes,
                 game_factory=ReadOnlyGame, clock=None, match_factory=ChatSource,
                 lobby_factory=None, diagnostics=None):
        self.diagnostics = diagnostics
        if lobby_factory is None:
            from .lobby_source import LobbySource
            lobby_factory = LobbySource
        self.process_finder = process_finder
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._selected_pid = selected_pid
        self._generation = 0
        self._serial = 0
        self._active = False
        self._emit = None
        self._run_revision = 0
        self._selection_revision = 0
        self._session_revision = 0
        self._session = _UNKNOWN
        self._poll_serial = self._latest_poll = 0
        self._last_capture_state = None
        self._deferred_stop = threading.Event()
        self._deferred_thread = None
        self._workers = {'match': _WorkerState(), 'lobby': _WorkerState()}
        for name, factory in (('match', match_factory), ('lobby', lobby_factory)):
            # Pass the constructor, never an opened game or shared read_buffer.
            self._workers[name].source = factory(
                selected_pid=selected_pid,
                process_finder=lambda name=name: self._find_for_worker(name),
                game_factory=game_factory, clock=clock)
            if hasattr(self._workers[name].source,'diagnostics'):
                self._workers[name].source.diagnostics=diagnostics
        self.match_source = self._workers['match'].source
        self.lobby_source = self._workers['lobby'].source

    @property
    def generation(self):
        with self._lock:
            return self._generation

    @generation.setter
    def generation(self, value):
        with self._lock:
            self._generation = value

    @property
    def selected_pid(self):
        with self._lock:
            return self._selected_pid

    @selected_pid.setter
    def selected_pid(self, value):
        with self._lifecycle_lock:
            with self._lock:
                if value == self._selected_pid:
                    return
                self._selected_pid = value
                self._selection_revision += 1
                self._session_revision += 1
                self._session = _UNKNOWN
                for worker in self._workers.values():
                    worker.source.selected_pid = value
                    worker.session_revision = -1
                    worker.generation_floor = worker.source.generation + 1
                    worker.state = '等待重新连接'
                if not self._active:
                    return
                self._advance('游戏进程选择已改变，正在接入所选进程。')
                self._capture_state()
            self._reconnect_workers()

    @property
    def running(self):
        with self._lock:
            return any(worker.source.running for worker in self._workers.values())

    def start(self, emit):
        with self._lifecycle_lock:
            with self._lock:
                if self._active:
                    return
                self._active = True
                self._emit = emit
                self._deferred_stop = threading.Event()
                self._run_revision += 1
                run_revision = self._run_revision
                self._session_revision += 1
                self._session = _UNKNOWN
                self._last_capture_state = None
                for worker in self._workers.values():
                    worker.observed = _UNKNOWN
                    worker.session_revision = -1
                    worker.generation_floor = worker.source.generation
                    worker.latest_generation = -1
                    worker.state = '等待连接'
                    worker.source.selected_pid = self._selected_pid
                self._advance('持续采集已启动，大厅与对局聊天分别自动连接。')
                self._capture_state()
            pending = []
            for name, worker in self._workers.items():
                if worker.source.running:
                    pending.append(name)
                    with self._lock:
                        worker.state = '等待上次采集退出'
                        self._capture_state()
                else:
                    self._start_worker(name, run_revision)
            if pending:
                self._deferred_thread = threading.Thread(
                    target=self._restart_pending, args=(pending, run_revision, self._deferred_stop),
                    name='chat-source-restart', daemon=True)
                self._deferred_thread.start()

    def _start_worker(self, name, run_revision):
        worker = self._workers[name]
        try:
            worker.source.start(lambda event: self._receive(name, run_revision, event))
        except Exception as failure:
            # A broken source must not prevent starting the healthy one.
            with self._lock:
                worker.state = '连接失败'
                self._publish('status', f'{_LABELS[name]}：{failure}')
                self._capture_state()

    def _restart_pending(self, pending, run_revision, cancelled):
        # Only finish starts explicitly requested by the user. The old worker's
        # bounded join may still be pending; its callback remains fenced out.
        while not cancelled.wait(.05):
            with self._lifecycle_lock:
                with self._lock:
                    if not self._active or run_revision != self._run_revision:
                        return
                for name in list(pending):
                    if not self._workers[name].source.running:
                        self._start_worker(name, run_revision)
                        pending.remove(name)
                if not pending:
                    return

    def stop(self):
        with self._lifecycle_lock:
            with self._lock:
                if not self._active:
                    return
                self._publish('capture_state', '已停止')
                self._active = False
                self._emit = None
                self._run_revision += 1
                self._deferred_stop.set()
            # Never join a child while holding the event lock it needs to exit.
            for worker in self._workers.values():
                try:
                    worker.source.stop()
                except Exception:
                    # Continue releasing the other source even if this source's
                    # handle was already invalidated during shutdown.
                    continue

    def reconnect(self):
        with self._lifecycle_lock:
            self._reconnect_workers()

    def _reconnect_workers(self):
        for name, worker in self._workers.items():
            try:
                worker.source.reconnect()
            except Exception as failure:
                with self._lock:
                    worker.state = '连接失败'
                    self._publish('status', f'{_LABELS[name]}：{failure}')
                    self._capture_state()

    @staticmethod
    def _token(process):
        return process.pid, ntpath.normcase(ntpath.normpath(str(process.path))), process.build

    def _find_for_worker(self, name):
        with self._lock:
            self._poll_serial += 1
            poll_serial = self._poll_serial
            selection_revision = self._selection_revision
            run_revision = self._run_revision
        processes = list(self.process_finder())
        with self._lock:
            if (not self._active or run_revision != self._run_revision or
                    selection_revision != self._selection_revision):
                return []
            if (poll_serial > self._latest_poll and self._selected_pid is not None and
                    not any(process.pid == self._selected_pid for process in processes)):
                # Preserve ChatSource's automatic fallback after a selected PID
                # exits. Update under this lock, never via the public setter
                # (which acquires the lifecycle lock before the event lock).
                self._selected_pid = None
                self._selection_revision += 1
                for state in self._workers.values():
                    state.source.selected_pid = None
            worker = self._workers[name]
            worker.source.selected_pid = self._selected_pid
            candidates = [process for process in processes
                          if self._selected_pid is None or process.pid == self._selected_pid]
            if self._selected_pid is None and self._session not in (_UNKNOWN, None):
                current = [process for process in candidates if self._token(process) == self._session]
                if current:
                    candidates = current
            token = self._token(candidates[0]) if len(candidates) == 1 else None
            previous = worker.observed
            if poll_serial > self._latest_poll:
                self._latest_poll = poll_serial
                if self._session is _UNKNOWN:
                    self._session = token
                elif token != self._session:
                    self._session = token
                    self._session_revision += 1
                    for state in self._workers.values():
                        state.state = '等待重新连接' if token else '等待游戏'
                    self._advance('游戏进程会话已改变，正在重新接入聊天。')
                    self._capture_state()
            missed_session = (previous not in (_UNKNOWN, None) and token is not None and
                              worker.session_revision != self._session_revision)
            if previous is not _UNKNOWN and (previous != token or missed_session):
                worker.generation_floor = max(worker.generation_floor, worker.source.generation + 1)
            # The legacy match source compares PID only. A one-poll absence
            # makes it close a handle when that PID now identifies another build/path.
            same_pid_replaced = (previous not in (_UNKNOWN, None) and token is not None and
                                 previous[0] == token[0] and (previous != token or missed_session))
            worker.observed = _UNKNOWN if same_pid_replaced else token
            worker.session_revision = (self._session_revision if token == self._session and
                                       not same_pid_replaced else -1)
            return [] if same_pid_replaced else candidates

    def _trace_message(self, name, event, outcome, *, reason='', message_id=None):
        if self.diagnostics is None or event.kind != 'chat':
            return
        try:
            self.diagnostics.record('capture.state', source=name, stage='merge',
                outcome=outcome, reason=reason, capture_schema=1,
                generation=self._generation, child_generation=event.generation,
                source_message_id=event.payload.message_id,
                message_id=message_id or event.payload.message_id)
        except Exception:
            pass

    def _receive(self, name, run_revision, event):
        with self._lock:
            if not self._active or run_revision != self._run_revision:
                self._trace_message(name, event, 'discarded', reason='inactive_run')
                return
            worker = self._workers[name]
            floor = max(worker.generation_floor, worker.latest_generation, worker.source.generation)
            if event.generation < floor:
                self._trace_message(name, event, 'discarded', reason='stale_generation')
                return
            worker.latest_generation = event.generation
            if (worker.session_revision != self._session_revision or
                    worker.observed != self._session):
                self._trace_message(name, event, 'discarded', reason='session_mismatch')
                return
            if event.kind == 'generation':
                # Child generations guard its cursor only. Forwarding them would
                # cancel the other child's pending/inflight translations.
                return
            if event.kind == 'chat':
                chat = event.payload
                if self._session in (_UNKNOWN, None) or chat.channel not in _CHANNELS[name]:
                    self._trace_message(name, event, 'discarded',
                        reason='session_mismatch' if self._session in (_UNKNOWN,None) else 'channel_filtered')
                    return
                if chat.generation != event.generation:
                    self._trace_message(name, event, 'discarded', reason='generation_mismatch')
                    return
                self._serial += 1
                replacement = ChatEvent(f'{name}:{self._serial}', self._generation,
                                        chat.channel, chat.sender, chat.timestamp, chat.text)
                self._trace_message(name, event, 'forwarded', message_id=replacement.message_id)
                self._publish('chat', replacement)
            elif event.kind == 'capture_state':
                if self.diagnostics is not None:
                    # 只记录来源/事件分类，原始状态可能嵌入操作系统路径或异常。
                    self.diagnostics.record('capture.state', source=name, stage='state',
                                            outcome=_DIAGNOSTIC_STATES.get(event.payload,'other') if isinstance(event.payload,str) else 'other',
                                            generation=self._generation)
                worker.state = '等待恢复' if event.payload == '已停止' else str(event.payload)
                self._capture_state()
            else:
                if self.diagnostics is not None and event.kind in ('status', 'error'):
                    self.diagnostics.record('capture.state', source=name, stage=event.kind,
                                            outcome='received', generation=self._generation)
                payload = f'{_LABELS[name]}：{event.payload}' if event.kind == 'status' else event.payload
                self._publish(event.kind, payload)
                if event.kind == 'error':
                    worker.state = '错误，等待恢复'
                    self._capture_state()

    def _advance(self, message):
        self._generation += 1
        self._publish('generation', message)

    def _capture_state(self):
        value = '持续采集中 · ' + ' / '.join(
            f'{_LABELS[name]}：{self._workers[name].state.removeprefix(_LABELS[name])}'
            for name in ('lobby', 'match'))
        if value != self._last_capture_state:
            self._last_capture_state = value
            self._publish('capture_state', value)

    def _publish(self, kind, payload):
        # Callers hold _lock so stop and a concurrent source cannot interleave
        # generation changes with their events or publish after stop returns.
        if self._active and self._emit is not None:
            self._emit(AppEvent(kind, self._generation, payload))
