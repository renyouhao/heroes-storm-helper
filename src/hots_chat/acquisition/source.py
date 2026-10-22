from collections import OrderedDict
import threading
import time
import re
from .memory import ReadOnlyGame, find_game_processes
from .history import HistoryReader, HistoryInvalid, HistoryCleared
from .discovery import discover, DiscoveryCancelled, DiscoveryPending, IncrementalDiscovery
from ..models import AppEvent, ChatEvent


class ChatSource:
    def __init__(self, selected_pid=None, process_finder=find_game_processes, game_factory=ReadOnlyGame, clock=None):
        self.selected_pid = selected_pid
        self.process_finder, self.game_factory = process_finder, game_factory
        self.clock = clock or time.monotonic
        self._stop = threading.Event()
        self._reconnect = threading.Event()
        self.thread = None
        self.generation = 0
        self.last_attempt = -float('inf')

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self, emit):
        if self.running:
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self._run,args=(emit,),name='chat-source',daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()
        if self.thread and self.thread != threading.current_thread():
            self.thread.join(3)

    def reconnect(self):
        self._reconnect.set()

    def _run(self, emit):
        game = reader = pid = locator = None
        serial = 0
        recovering = False
        has_baseline = False
        skip_unchanged = False
        next_scan = 0.
        cursors = OrderedDict()
        capture_state = None
        probe = None
        census = True
        last_activity = self.clock()
        next_probe = 0.
        recent_locations = ()

        def save_cursor(root,head,stamp):
            key=(pid,root)
            cursors[key]=(head,stamp)
            cursors.move_to_end(key)
            while len(cursors)>128:cursors.popitem(last=False)

        def state(value):
            nonlocal capture_state
            if value != capture_state:
                capture_state = value
                emit(AppEvent('capture_state',self.generation,value))

        def remember():
            nonlocal recent_locations
            if reader and reader.initialized:
                recent_locations=(reader.last_head,reader.root)
                save_cursor(reader.root,reader.last_head,reader.last_stamp)

        def transition(message):
            self.generation += 1
            emit(AppEvent('generation',self.generation,message))

        def publish(result):
            nonlocal serial
            if result.gap:
                emit(AppEvent('gap',self.generation,'聊天历史已覆盖，可能有遗漏。'))
            for parsed in result.messages:
                serial+=1
                chat=ChatEvent(f'{self.generation}:{serial}',self.generation,parsed.channel,parsed.sender,parsed.time,parsed.text)
                emit(AppEvent('chat',self.generation,chat))

        try:
            while not self._stop.is_set():
                try:
                    result = None
                    processes = self.process_finder()
                    if self.selected_pid is not None and not any(p.pid==self.selected_pid for p in processes):
                        self.selected_pid=None
                    candidates=[p for p in processes if self.selected_pid is None or p.pid==self.selected_pid]
                    process_gone=pid is not None and not any(p.pid==pid for p in candidates)
                    if process_gone or self._reconnect.is_set():
                        remember()
                        self._reconnect.clear()
                        reader=locator=None
                        probe=None
                        next_scan=0.
                        skip_unchanged=False
                        if process_gone:
                            if game:game.close()
                            game=pid=None
                            recovering=False
                            census=True
                            self.last_attempt=-float('inf')
                        else:
                            recovering=game is not None
                            if game is None:self.last_attempt=-float('inf')
                        transition('采集保持开启，正在自动接入聊天；已显示历史保留。')
                        state('自动连接中')

                    if reader is None:
                        if len(candidates)!=1:
                            emit(AppEvent('processes',self.generation,candidates))
                            state('等待游戏' if not candidates else '请选择游戏进程')
                            self._stop.wait(2);continue
                        p=candidates[0]
                        if p.build!=98348:
                            state('游戏版本未验证')
                            emit(AppEvent('status',self.generation,'游戏版本未验证，仅支持 98348。'))
                            self._stop.wait(2);continue
                        if game is None:
                            if self.clock()-self.last_attempt<60:
                                state('自动等待游戏聊天')
                                self._stop.wait(.5);continue
                            self.last_attempt=self.clock()
                            has_baseline=False
                            game=self.game_factory(p.pid);pid=p.pid
                            if not re.search(r'Base98348(?:[\\/]|$)',game.path):
                                raise HistoryInvalid('游戏版本未验证')

                        if recovering:
                            state('等待对局聊天，自动恢复')
                            if self.clock()<next_scan:
                                self._stop.wait(.5);continue
                            if locator is None:
                                ignored={root:cursor for (process,root),cursor in cursors.items() if process==pid} if skip_unchanged else {}
                                locator=IncrementalDiscovery(game,self._stop,ignore_snapshots=ignored,preferred_addresses=recent_locations)
                            calibrated=locator.step()
                            if calibrated is None:
                                if locator.finished:
                                    locator=None;next_scan=self.clock()+5
                                self._stop.wait(.5);continue
                        else:
                            transition('正在接入；首次有限补收最近 5 条聊天，随后持续接收。')
                            state('正在连接游戏聊天')
                            calibrated=discover(game,self._stop)
                        if self._stop.is_set():break

                        reader=HistoryReader(game,calibrated.root,initial_tail=5,
                                             channels=('团队','所有玩家'))
                        cursor=cursors.get((pid,reader.root))
                        if cursor:
                            reader.last_head,reader.last_stamp=cursor
                            reader.initialized=True
                        result=reader.poll()
                        if result.retry:
                            raise HistoryInvalid('校准时历史变化，自动重试')
                        has_baseline=True
                        locator=None
                        probe=None;last_activity=self.clock()
                        remember()
                        state('已连接')
                        emit(AppEvent('status',self.generation,'已连接，等待新的游戏内聊天。'))

                    if result is None:result=reader.poll()
                    publish(result)
                    if not result.retry:remember()
                    if result.messages:
                        last_activity=self.clock()
                        if not census:probe=None
                    # A readable old tree can outlive a match. Inventory other
                    # existing roots once, then probe only after quiet periods.
                    if census or (not result.messages and self.clock()-last_activity>=10 and self.clock()>=next_probe):
                        if probe is None:
                            remember()
                            ignored={root:cursor for (process,root),cursor in cursors.items() if process==pid}
                            probe=IncrementalDiscovery(game,self._stop,ignore_snapshots=ignored,preferred_addresses=recent_locations)
                        candidate=probe.step()
                        if candidate is not None:
                            if candidate.root==reader.root:
                                # The main reader owns this cursor. Any append seen by
                                # discovery is delivered by its next poll, exactly once.
                                probe.ignore_snapshots[reader.root]=(reader.last_head,reader.last_stamp)
                                if not census:probe=None;next_probe=self.clock()+30
                            elif census:
                                check=HistoryReader(game,reader.root)
                                checked=check.poll()
                                if checked.retry:
                                    self._stop.wait(.5);continue
                                if check.is_end(check.last_head) and not reader.is_end(reader.last_head):
                                    raise HistoryCleared('History cleared during inventory')
                                try:
                                    baseline=HistoryReader(game,candidate.root,initial_tail=5,
                                                           channels=('团队','所有玩家'))
                                    cursor=cursors.get((pid,candidate.root))
                                    if cursor:
                                        baseline.last_head,baseline.last_stamp=cursor
                                        baseline.initialized=True
                                    baseline_result=baseline.poll()
                                except (HistoryInvalid,OSError,ValueError,RuntimeError):
                                    baseline_result=None
                                if baseline_result is not None and not baseline_result.retry:
                                    # Discovery's snapshot is not a delivered cursor.
                                    # Publish the bounded first batch or cursor delta before saving it.
                                    publish(baseline_result)
                                    snapshot=(baseline.last_head,baseline.last_stamp)
                                    probe.ignore_snapshots[candidate.root]=snapshot
                                    save_cursor(candidate.root,*snapshot)
                            else:
                                remember()
                                cursor=cursors.get((pid,candidate.root))
                                replacement=HistoryReader(game,candidate.root,initial_tail=5,
                                                          channels=('团队','所有玩家'))
                                if cursor:
                                    replacement.last_head,replacement.last_stamp=cursor
                                    replacement.initialized=True
                                try:recovered=replacement.poll()
                                except (HistoryInvalid,OSError,ValueError,RuntimeError):recovered=None
                                if recovered is not None and not recovered.retry:
                                    reader=replacement;recovering=True;probe=None
                                    last_activity=self.clock();next_probe=self.clock()+30
                                    transition('已自动切换对局聊天，历史消息保留。')
                                    publish(recovered)
                        elif probe.finished:
                            census=False;probe=None;next_probe=self.clock()+30
                except DiscoveryCancelled:
                    break
                except (HistoryInvalid,OSError,ValueError,RuntimeError) as failure:
                    if reader is not None and not isinstance(failure,OSError):
                        remember()
                        if isinstance(failure,HistoryCleared):
                            cursors.pop((pid,reader.root),None)
                        reader=locator=None
                        probe=None;census=not has_baseline
                        recovering=True;skip_unchanged=True;next_scan=0.
                        transition('采集保持开启，等待下一局聊天并自动恢复；历史消息保留。')
                        state('等待对局聊天，自动恢复')
                    elif isinstance(failure,DiscoveryPending) and game is not None:
                        # 首次扫描可能早于首条聊天；正常等待不能退回关闭句柄的60秒冷却。
                        locator=None;probe=None;recovering=True;skip_unchanged=False
                        next_scan=self.clock()+5
                        transition('等待游戏内聊天，采集保持开启并自动接入。')
                        state('等待对局聊天，自动恢复')
                    elif recovering and game is not None and not isinstance(failure,OSError):
                        locator=None;next_scan=self.clock()+5
                        state('等待对局聊天，自动恢复')
                    else:
                        remember()
                        if game:game.close()
                        game=reader=pid=locator=None
                        probe=None;recovering=False;has_baseline=False;census=True
                        skip_unchanged=False;recent_locations=()
                        transition('游戏连接暂时不可用，采集保持开启并自动重试。')
                        state('等待重新连接')
                        self.last_attempt=self.clock()
                self._stop.wait(.5)
        finally:
            if game:game.close()
            state('已停止')
