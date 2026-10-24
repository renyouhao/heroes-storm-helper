"""Tab双击播报：hook只入有界队列，读取和发送在独立worker串行完成。"""
from dataclasses import dataclass, replace
import queue
import threading
import time

from .loot_native import INPUT_MARKER
from .talent_broadcast import (CooldownHitSnapshot, HitSnapshot, InputContext, QuestHitSnapshot,
    StatHitSnapshot, TalentBroadcastConfig, TalentBroadcastSession)


# 状态详情也可能由外部 sender 提供；仅记录程序内固定提示，不能把播报正文当原因。
_DIAGNOSTIC_REASONS = frozenset((
    '双击播报已关闭。', '正在连接双击播报。', 'Tab内双击一项天赋或数据，播报到己方团队。',
    '双击监听已停止，请点击“重新连接”恢复。', '双击播报已停止，请点击“重新连接”恢复。',
    '双击监听未能完整退出，请点击“重新连接”恢复。', '本次播报已取消。',
    '当前没有可信的可播报项目。', '等待同一项目的第二次点击。', '已识别双击，等待安全发送。',
    '其他鼠标操作已取消本次播报。', '点击处理超时，请重新双击。', '已离开游戏，本次播报取消。',
    '游戏聊天框仍打开，请先按 Esc 关闭，再重新双击。', '点击后的输入操作已取消本次播报。',
    '其他输入操作正在进行，请稍后重新双击。', '鼠标事件时间未知或已过期，请重新双击。',
    '新的鼠标点击已取消尚未发送的播报。', '播报限频中：任意两次需间隔2秒，同一项需间隔3秒。',
    '面板、目标或输入环境已变化，本次播报取消。', '天赋播报内容已变化，本次播报取消。',
    '目标或输入环境已变化，请重新双击。', '等待鼠标松开后播报。',
    '等待开箱、连按或快捷输入结束。', '播报动作已交给发送边界。',
    '播报动作已经失效或已被处理。', '等待其他输入操作结束。', '发送前栅栏已取消本次播报。',
    '聊天发送未完整确认；已记录本次尝试，不自动重试。', '已发送到己方团队。',
))


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


def _hit_kind(hit):
    return {HitSnapshot: 'talent', StatHitSnapshot: 'stat', QuestHitSnapshot: 'quest',
            CooldownHitSnapshot: 'cooldown'}.get(type(hit), 'unknown' if hit is not None else 'none')


def _hit_values(hit):
    """诊断只复制可信类型的状态/标量，不复制 ID、显示名称、正文或原生地址。"""
    result = {'hit_kind': _hit_kind(hit)}
    if type(hit) in (HitSnapshot, QuestHitSnapshot):
        state = hit.quest_state if type(hit) is HitSnapshot else hit.state
        value = hit.quest_progress if type(hit) is HitSnapshot else hit.progress
        if state in ('', 'known', 'complete', 'unknown', 'untracked'):
            result['quest_state'] = state
        if type(value) is int and 0 <= value < 2**32:
            result['quest_progress'] = value
    elif type(hit) is CooldownHitSnapshot:
        if hit.state in ('cooldown', 'ready', 'unknown', 'untracked'):
            result['cooldown_state'] = hit.state
        if type(hit.remaining_seconds) in (int, float):
            result['cooldown_seconds'] = hit.remaining_seconds
    return result


def _hit_counts(hits):
    result = {kind + '_hit_count': 0 for kind in ('talent', 'stat', 'quest', 'cooldown')}
    for hit in hits:
        key = _hit_kind(hit) + '_hit_count'
        if key in result:
            result[key] += 1
    return result


def _closed_input_busy():
    return True


class BoardHelper:
    def __init__(self, *, backend_factory=None, reader_factory=None, sender_factory=None,
                 busy_provider=lambda:False, input_locks=(), clock=time.monotonic, diagnostics=None,
                 language='zh'):
        TalentBroadcastConfig(language=language)
        self._language = language
        self.backend_factory = backend_factory or self._new_backend
        self.reader_factory = reader_factory or self._new_reader
        self.sender_factory = sender_factory or self._new_sender
        self.busy_provider, self.input_locks, self.clock = busy_provider, tuple(input_locks), clock
        self.diagnostics = diagnostics
        self._diagnostic_state = None
        self._attempt_id = 0
        self._control = threading.RLock()
        self._inputs = queue.Queue(maxsize=64)
        self._statuses = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._busy = threading.Event()
        self._enabled = self._closed = self._suspended = False
        self._generation = self._revision = 0
        self._language_revision = 0
        self._language_request = ''
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
        from .quest_catalog import QuestCatalog
        from .talent_catalog import TalentCatalog
        inputs = BoardInputReader()
        catalog, quests = TalentCatalog(), QuestCatalog()
        return BoardReader(text_provider=catalog.resolve, quest_text_provider=quests.resolve,
            viewport_converter=backend.viewport_converter, input_state_provider=inputs.inspect)

    def _new_sender(self, backend, reader):
        from .board_sender import TeamChatSender
        return lambda action, **kwargs:TeamChatSender(backend,
            inspect=lambda:reader.inspect_input(action.pid, action.hwnd),
            diagnostics=self.diagnostics).send_result(action, **kwargs)

    @property
    def busy(self):return self._busy.is_set()

    @property
    def language(self):
        with self._control:return self._language

    def set_language(self, language, *, request_ref=''):
        """立即关闭旧动作栅栏；会话配置始终由工作线程串行更新。"""
        TalentBroadcastConfig(language=language)
        with self._control:
            if self._closed:return False
            changed = language != self._language
            if changed:
                self._language = language
                self._revision += 1
                self._language_revision = self._revision
                self._language_request = request_ref
                self._enqueue('cancel')
            self._record('ui.apply', stage='configuration', operation='language',
                         outcome='applied', reason='language_changed' if changed else 'language_unchanged',
                         request_ref=request_ref, target_language=language)
            return True

    @property
    def running(self):
        """只读运行意图与停止栅栏；不暴露线程、hook或进程对象。"""
        return self._enabled and not self._closed and not self._stop.is_set()

    def _emit(self, state, detail='', generation=None):
        with self._control:
            if generation is not None and generation != self._generation:return
            status = BoardStatus(state, detail, self._generation)
            try:self._statuses.get_nowait()
            except queue.Empty:pass
            self._statuses.put_nowait(status)
            reason = detail if detail in _DIAGNOSTIC_REASONS else ''
            signature = (self._generation,state,reason,self._language)
            if signature != self._diagnostic_state and self._record('hotkey.state',
                    stage='broadcast', outcome=state, reason=reason):
                self._diagnostic_state = signature

    def _diagnostics_enabled(self):
        try:
            if self.diagnostics is None:return False
            snapshot = getattr(self.diagnostics, 'snapshot', None)
            return snapshot is None or bool(snapshot().get('enabled'))
        except Exception:return False

    def _record(self, event, **fields):
        try:
            if not self._diagnostics_enabled():return False
            fields.setdefault('target_language',self._language)
            return self.diagnostics.record(event, source='broadcast', generation=self._generation,
                attempt_id=self._attempt_id, **fields) is not False
        except Exception:return False

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
                self._emit('waiting', '正在连接双击播报。')
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
            if hasattr(reader, 'diagnostics'):reader.diagnostics = self.diagnostics
            sender = self.sender_factory(backend, reader)
            milliseconds, size = backend.double_click_settings()
            with self._control:
                language,applied_language_revision=self._language,self._language_revision
            configured = TalentBroadcastSession(double_click_ms=milliseconds, double_click_size=size,
                                                 config=TalentBroadcastConfig(enabled=True,language=language),
                                                 diagnostics=self.diagnostics)
            if self._session is None:self._session = configured
            else:
                # Validate fresh Windows settings above, then keep the existing
                # session's attempt history across disable/enable cycles.
                self._session.double_click_seconds = configured.double_click_seconds
                self._session.double_click_size = configured.double_click_size
                self._session.diagnostics = self.diagnostics
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
                unexpected = False
                try:backend.run(key, mouse, lambda:not stopped.is_set(),
                                lambda:self._emit('idle', 'Tab内双击一项天赋或数据，播报到己方团队。', generation), stopped)
                except Exception as error:
                    unexpected = not stopped.is_set()
                    stopped.set()
                    failure.append(error)
                    self._record('helper.read',stage='hook',outcome='error',
                        error_type=type(error).__name__,winerror=getattr(error,'winerror',None))
                finally:
                    unexpected = unexpected or not stopped.is_set()
                    stopped.set()
                    if unexpected:
                        self._emit('error', '双击监听已停止，请点击“重新连接”恢复。', generation)
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

            def fresh(stage='revalidate', event_age_ms=0):
                nonlocal snapshot, target
                target = backend.foreground_target()
                if target is None:
                    self._record('helper.click' if stage=='click' else 'helper.read',stage=stage,
                        outcome='foreground_missing',reason='未确认游戏前台窗口',foreground_state='false',
                        event_age_ms=event_age_ms)
                    return None, InputContext()
                # 诊断打开时才增加一次窗口几何查询；日志异常不得改变读取/发送。
                geometry = {}
                if self._diagnostics_enabled():
                    try:
                        # 清除上一点击的转换信息，避免早期读取失败沿用旧渲染尺寸。
                        if hasattr(backend, 'last_viewport'):backend.last_viewport = {}
                        inspect = getattr(backend, 'inspect_viewport', None)
                        if inspect is not None:geometry = inspect(target.hwnd)
                    except Exception as error:
                        geometry = dict(coordinate_reason='window_query_failed',
                            error_type=type(error).__name__,winerror=getattr(error,'winerror',None))
                started = self.clock()
                snapshot = reader.poll(target.pid, target.hwnd, self.clock())
                current = backend.foreground_target()
                context = InputContext(pid=target.pid,hwnd=target.hwnd,match_token=snapshot.match_token,
                    foreground=current==target,chat_active=snapshot.chat_active,menu_active=snapshot.menu_active,
                    quick_input_busy=self._suspended or bool(self.busy_provider()))
                hit = hit_test(snapshot,*point) if point else None
                if point and self._diagnostics_enabled():
                    from .helper_replay import record_board_hit_test
                    record_board_hit_test(self.diagnostics, snapshot, point, context, hit, session=session)
                if self._diagnostics_enabled():
                    info = dict(getattr(reader, 'last_diagnostic', {}))
                    info.update(geometry)
                    info.update(getattr(backend, 'last_viewport', {}))
                    flag = lambda value:'true' if value is True else 'false' if value is False else 'unknown'
                    info.update(reader_status=snapshot.status, hit_count=len(snapshot.hits),
                        chat_state=flag(snapshot.chat_active),menu_state=flag(snapshot.menu_active),
                        foreground_state=flag(context.foreground),elapsed_ms=round((self.clock()-started)*1000,3))
                    info.update(_hit_counts(snapshot.hits))
                    self._record('helper.read', **info)
                    if hit is not None and session.config.language=='en':
                        from .broadcast_text import localization_reason, player_name_policy
                        if session._message(hit) is None:
                            self._record('helper.read',stage='localization',outcome='rejected',
                                reason='english_text_unavailable',hit_kind=_hit_kind(hit),target_language='en',
                                error_code=localization_reason(hit,'en'))
                        else:
                            self._record('helper.read',stage='localization',outcome='applied',
                                reason=player_name_policy(hit),hit_kind=_hit_kind(hit),target_language='en')
                    if stage == 'click':
                        count = sum(1 for item in snapshot.hits if item.rect[0] <= point[0] < item.rect[2]
                                    and item.rect[1] <= point[1] < item.rect[3])
                        self._record('helper.click',stage='hit_test',outcome='hit' if count==1 else
                            'ambiguous' if count>1 else 'no_hit',candidate_count=count,click_x=point[0],click_y=point[1],
                            reason='' if count == 1 else 'overlapping_targets' if count > 1 else 'no_trusted_candidate',
                            **_hit_values(hit),
                            event_age_ms=event_age_ms)
                return hit, context

            while not stopped.is_set():
                try:kind,args,event_at,event_revision = self._inputs.get(timeout=.05)
                except queue.Empty:kind,args,event_at,event_revision = '',(),self.clock(),self._revision
                with self._control:
                    language, language_revision, language_request = (
                        self._language,self._language_revision,self._language_request)
                if language_revision != applied_language_revision:
                    session.configure(replace(session.config,language=language))
                    applied_language_revision=language_revision
                    pending_revision = point = None
                    self._record('ui.apply',stage='worker_configuration',operation='language',
                        outcome='applied',reason='pending_actions_cancelled',target_language=language,
                        request_ref=language_request)
                    publish_status()
                # Preserve both clicks in a queued gesture, but never reuse a click
                # captured before the latest language change.
                if event_revision < language_revision:continue
                if kind == 'cancel':
                    if has_gesture():
                        session.cancel()
                        pending_revision = None
                        publish_status()
                elif kind == 'mouse':
                    x,y,down,button = args
                    if down and button == 'left':self._attempt_id += 1
                    if button != 'left':
                        if has_gesture():
                            session.cancel('其他鼠标操作已取消本次播报。')
                            pending_revision = None
                            publish_status()
                        continue
                    if down and not 0 <= self.clock()-event_at <= .25:
                        self._record('helper.click',stage='queue',outcome='expired',
                            reason='点击处理超时，请重新双击。',event_age_ms=(self.clock()-event_at)*1000)
                        session.cancel('点击处理超时，请重新双击。')
                        pending_revision = None
                        publish_status()
                        continue
                    if down:
                        point = (x,y)
                        hit, context = fresh('click', (self.clock()-event_at)*1000)
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
                if session._left_held:
                    # 未处理第二次松开时只检查已有动作是否过期，先让事件队列前进。
                    # 此时 tick 不会交出动作；松开后仍须 fresh 复核才能发送。
                    session.tick(hit,context,self.clock())
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
                        request_ref=f'board-action-{action.token}'
                        self._record('helper.click',stage='dispatch',outcome='started',
                            request_ref=request_ref,target_language=action.language,hit_kind=action.kind)
                        status=session.dispatch(action,sender,hit,context,self.clock(),allowed=allowed)
                        self._record('helper.click',stage='dispatch',outcome=status.state,
                            request_ref=request_ref,target_language=action.language,hit_kind=action.kind)
                    publish_status()
                finally:
                    self._busy.clear()
                    for lock in reversed(locks):lock.release()
        except Exception as error:
            # Close the running fence before publishing either diagnostic or UI
            # error. Callback consumers must never observe an active failed worker.
            stopped.set()
            self._record('helper.read',stage='worker',outcome='error',
                error_type=type(error).__name__,winerror=getattr(error,'winerror',None))
            # 发布停止错误前先关闭运行栅栏，主窗读取同一条状态即可显示重连入口。
            self._emit('error', '双击播报已停止，请点击“重新连接”恢复。', generation)
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
                self._emit('error', '双击监听未能完整退出，请点击“重新连接”恢复。', generation)

    def close(self):
        try:self.set_enabled(False)
        finally:
            with self._control:
                self._closed=True
                self.busy_provider=_closed_input_busy
                self.diagnostics=None
