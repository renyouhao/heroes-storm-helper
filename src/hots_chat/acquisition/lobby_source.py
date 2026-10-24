"""Read-only lobby source with a lifecycle independent from match chat."""
import ctypes
from collections import OrderedDict
from ctypes import wintypes
import re
import struct
import sys
import threading
import time

from .discovery import DiscoveryCancelled
from .history import HistoryInvalid
from .lobby_draft import LobbyDraftContext
from .lobby_native import (LobbyDiscovery, LobbyReader, NATIVE_COUNT_FIELDS,
                           NATIVE_LOBBY_SCHEMA, NATIVE_REJECTION_REASONS, native_failure_reason)
from .memory import ReadOnlyGame, find_game_processes
from ..models import AppEvent, ChatEvent


_TOTAL_FIELDS = NATIVE_COUNT_FIELDS + (
    'native_draft_context_checks', 'native_draft_context_changes', 'native_draft_context_rejected',
    'read_calls', 'read_bytes', 'requested_bytes', 'short_read_count', 'scan_region_count',
    'candidate_count', 'valid_snapshot_count', 'invalid_candidate_count', 'ignored_snapshot_count',
)
_SNAPSHOT_FIELDS = frozenset(_TOTAL_FIELDS + (
    'native_lobby_schema', 'scan_id', 'elapsed_ms', 'last_rejection_reason',
))


class ModuleEntry(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('module_id', wintypes.DWORD),
                ('pid', wintypes.DWORD), ('global_usage', wintypes.DWORD),
                ('process_usage', wintypes.DWORD), ('base', ctypes.c_void_p),
                ('bytes', wintypes.DWORD), ('handle', wintypes.HMODULE),
                ('name', wintypes.WCHAR * 256), ('path', wintypes.WCHAR * 260)]


def main_module_base(pid):
    """Query the game module through Toolhelp; no game function is invoked."""
    if sys.platform != 'win32' or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise OSError('Lobby capture requires 64-bit Windows')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Module32FirstW.argtypes = kernel.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ModuleEntry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(0x18, pid)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ModuleEntry(); entry.size = ctypes.sizeof(entry)
        present = kernel.Module32FirstW(handle, ctypes.byref(entry))
        while present:
            if entry.name.lower() == 'heroesofthestorm_x64.exe' and entry.base:
                return int(entry.base)
            present = kernel.Module32NextW(handle, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    raise OSError('Game image is not available')


class LobbySource:
    def __init__(self, selected_pid=None, process_finder=find_game_processes,
                 game_factory=ReadOnlyGame, clock=None, module_base_finder=main_module_base,
                 reader_factory=LobbyReader, discovery_factory=LobbyDiscovery, wall_clock=None,
                 draft_context_factory=LobbyDraftContext):
        self.selected_pid = selected_pid
        self.process_finder, self.game_factory = process_finder, game_factory
        self.clock = clock or time.monotonic
        self.wall_clock = wall_clock or time.time
        self._capture_after_filetime = None
        self.diagnostics = None
        self.module_base_finder = module_base_finder
        self.reader_factory, self.discovery_factory = reader_factory, discovery_factory
        self.draft_context_factory = draft_context_factory
        self.generation, self.last_attempt = 0, -float('inf')
        self.thread = None
        self._stop, self._reconnect = threading.Event(), threading.Event()
        self._diagnostic_totals = dict.fromkeys(_TOTAL_FIELDS, 0)
        self._scan_serial = self._reader_serial = 0

    def diagnostic_snapshot(self):
        """Source-instance cumulative checks survive reader/cursor eviction."""
        return dict(self._diagnostic_totals, native_lobby_schema=NATIVE_LOBBY_SCHEMA)

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self, emit):
        if self.running:
            return
        self._capture_after_filetime = 116444736000000000 + int(self.wall_clock() * 10000000)
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, args=(emit,), name='lobby-source', daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()
        if self.thread and self.thread != threading.current_thread():
            self.thread.join(3)

    def reconnect(self):
        self._reconnect.set()

    def _run(self, emit):
        if self._capture_after_filetime is None:
            self._capture_after_filetime = 116444736000000000 + int(self.wall_clock() * 10000000)
        capture_after_filetime = self._capture_after_filetime
        game = pid = image_base = reader = scanner = None
        session = capture_state = None
        probe = nearby = watcher = None
        cursors = OrderedDict()
        areas = {}
        drain_nodes = {}
        record_identities = {}
        inventory_reader = None
        inventory_warning_emitted = False
        census = True
        last_activity = self.clock()
        next_probe = next_nearby = 0.
        probe_backoff = 5.
        failures = 0
        next_scan, serial = 0., 0
        connected = False
        last_trace = OrderedDict()
        active_scans = {}
        found_scan_ids = OrderedDict()
        draft_context = None
        context_previous = {}
        context_reason = None

        def trace(stage, outcome, *, interval=0, **fields):
            recorder = self.diagnostics
            if recorder is None:return
            try:
                enabled = getattr(recorder, 'snapshot', None)
                if callable(enabled) and not enabled().get('enabled'):return
                now = self.clock()
                key = (stage, outcome, fields.get('scan_id'), fields.get('reader_id'))
                if interval and now - last_trace.get(key, -float('inf')) < interval:return
                if interval:
                    last_trace[key] = now
                    last_trace.move_to_end(key)
                    while len(last_trace) > 256:last_trace.popitem(last=False)
                fields.setdefault('native_lobby_schema', NATIVE_LOBBY_SCHEMA)
                recorder.record('capture.state', source='lobby', stage=stage, outcome=outcome,
                                generation=self.generation, **fields)
            except Exception:
                pass

        def snapshot(target):
            try:
                method = getattr(target, 'diagnostic_snapshot', None)
                value = method() if callable(method) else {}
                if not isinstance(value, dict):return {}
                result = {key: field for key, field in value.items() if key in _SNAPSHOT_FIELDS}
                if result.get('last_rejection_reason') not in NATIVE_REJECTION_REASONS:
                    result.pop('last_rejection_reason', None)
                return result
            except Exception:
                return {}

        def accumulate(before, after):
            for field in _TOTAL_FIELDS:
                previous, current = before.get(field, 0), after.get(field, 0)
                if type(previous) is int and type(current) is int:
                    self._diagnostic_totals[field] += max(0, current - previous)

        def observe_draft(*, observe=True):
            nonlocal context_previous, context_reason
            if draft_context is None:return
            if observe:draft_context.observe()
            current = snapshot(draft_context)
            accumulate(context_previous, current)
            context_previous = current
            reason = draft_context.last_reason
            changed = reason != context_reason
            context_reason = reason
            trace('draft_context', 'verified' if draft_context.current is not None else 'unavailable',
                  interval=0 if changed else 3, reason=reason, **current)

        def scan_trace(target, outcome, *, failure=None, reason=''):
            context = active_scans.get(id(target))
            if context is None:return
            fields = snapshot(target)
            fields['scan_id'] = context['scan_id']
            if reason:fields['reason'] = reason
            if failure is not None:
                fields.update(error_type=type(failure).__name__,
                              winerror=getattr(failure, 'winerror', None),
                              reason=native_failure_reason(failure))
            trace(context['stage'], outcome, interval=4 if outcome == 'progress' else 0, **fields)

        def cancel_scans(reason):
            for context in tuple(active_scans.values()):
                scan_trace(context['target'], 'cancelled', reason=reason)
            active_scans.clear()

        def scan_step(target, **options):
            if id(target) not in active_scans and not target.finished:
                self._scan_serial += 1
                active_scans[id(target)] = dict(target=target,
                    stage=getattr(target, '_diagnostic_stage', 'discovery'), scan_id=self._scan_serial)
                scan_trace(target, 'started', reason='retry_after_error')
            before = snapshot(target)
            try:
                location = target.step(**options)
            except DiscoveryCancelled as failure:
                scan_trace(target, 'cancelled', failure=failure)
                active_scans.pop(id(target), None)
                raise
            except (HistoryInvalid, OSError, ValueError, RuntimeError, struct.error) as failure:
                scan_trace(target, 'error', failure=failure)
                active_scans.pop(id(target), None)
                raise
            finally:
                accumulate(before, snapshot(target))
            scan_trace(target, 'found' if location is not None else
                       'exhausted' if target.finished else 'progress')
            if location is not None:
                context = active_scans.get(id(target))
                if context is not None:
                    found_scan_ids[location.root] = context['scan_id']
                    found_scan_ids.move_to_end(location.root)
                    while len(found_scan_ids) > 128:found_scan_ids.popitem(last=False)
            if target.finished:active_scans.pop(id(target), None)
            return location

        def poll_reader(target, stage='poll'):
            before = snapshot(target)
            fields = dict(reader_id=getattr(target, 'diagnostic_id', 0),
                          scan_id=getattr(target, 'diagnostic_scan_id', 0), published_count=serial)
            try:
                result = target.poll()
            except (HistoryInvalid, OSError, ValueError, RuntimeError, struct.error) as failure:
                fields.update(snapshot(target))
                trace(stage, 'error', reason=native_failure_reason(failure),
                      error_type=type(failure).__name__, winerror=getattr(failure, 'winerror', None), **fields)
                raise
            finally:
                accumulate(before, snapshot(target))
            fields.update(snapshot(target))
            fields.update(message_count=len(result.messages), gap_count=int(result.gap))
            outcome = 'retry' if result.retry else 'messages' if result.messages else 'idle'
            trace(stage, outcome, interval=3 if outcome in ('idle', 'retry') else 0, **fields)
            return result

        def state(value):
            nonlocal capture_state
            if value != capture_state:
                capture_state = value
                emit(AppEvent('capture_state', self.generation, value))
                trace('state', 'changed', reason=value)

        def transition(message):
            self.generation += 1
            emit(AppEvent('generation', self.generation, message))

        def remember(root, cursor, owner=None):
            if cursor is not None:
                owner = owner or (reader if reader is not None and reader.root == root else None)
                if owner is not None:
                    drain_nodes[root] = owner.drain_nodes
                    record_identities[root] = owner.record_identities
                elif cursors.get(root) != cursor:
                    drain_nodes.pop(root, None)
                    record_identities.pop(root, None)
                cursors[root] = cursor
                cursors.move_to_end(root)
                while len(cursors) > 128:
                    old_root, _ = cursors.popitem(last=False)
                    areas.pop(old_root, None)
                    drain_nodes.pop(old_root, None)
                    record_identities.pop(old_root, None)

        def discovery(regions=None, ignore_seen=True, watch=False, stage='discovery'):
            target = self.discovery_factory(game, self._stop, image_base,
                                          ignore_snapshots=cursors if ignore_seen else {},
                                          preferred_roots=tuple(root for root in reversed(cursors)
                                                                if not watch or reader is None or root != reader.root),
                                          regions=regions)
            if hasattr(target, 'draft_context'):target.draft_context = draft_context
            self._scan_serial += 1
            try:target._diagnostic_stage = stage
            except (AttributeError, TypeError):pass
            active_scans[id(target)] = dict(target=target, stage=stage, scan_id=self._scan_serial)
            scan_trace(target, 'started')
            return target

        def replacement(location):
            # A cursor belongs to one ring. Reusing another readable ring's tail
            # would silently redirect polling back to that obsolete ring.
            cursor = cursors.get(location.root)
            if location.region is not None:
                areas[location.root] = location.region
            target = self.reader_factory(game, location.root, image_base, cursor=cursor,
                                       drain_nodes=drain_nodes.get(location.root, ()),
                                       record_identities=record_identities.get(location.root, ()),
                                       include_history=True,
                                       after_filetime=capture_after_filetime)
            if hasattr(target, 'draft_context'):target.draft_context = draft_context
            self._reader_serial += 1
            try:
                target.diagnostic_id = self._reader_serial
                target.diagnostic_scan_id = found_scan_ids.get(location.root, 0)
            except (AttributeError, TypeError):
                pass
            trace('reader', 'attached', reader_id=self._reader_serial,
                  scan_id=found_scan_ids.get(location.root, 0), **snapshot(target))
            return target

        def publish(result, owner=None):
            nonlocal connected, serial, last_activity, probe_backoff
            if result.retry:
                return
            owner = owner or reader
            if not connected:
                state('大厅已连接')
                emit(AppEvent('status', self.generation, '已连接大厅，持续接收队伍、密语与已验证的征召团队。'))
                connected = True
            if result.gap:
                emit(AppEvent('gap', self.generation, '大厅聊天记录已变化，采集已恢复，期间可能有遗漏。'))
            if result.messages:
                last_activity = self.clock()
                probe_backoff = 5.
            for parsed in result.messages:
                serial += 1
                chat = ChatEvent(f'{self.generation}:{serial}', self.generation, parsed.channel,
                                 parsed.sender, parsed.time, parsed.text)
                emit(AppEvent('chat', self.generation, chat))
                trace('publish', 'forwarded', source_message_id=chat.message_id,
                      channel=chat.channel, reader_id=getattr(owner, 'diagnostic_id', 0),
                      scan_id=getattr(owner, 'diagnostic_scan_id', 0), published_count=serial)
            remember(owner.root, owner.cursor, owner)
            state('大厅已连接')

        def poll_inventory():
            nonlocal inventory_reader, inventory_warning_emitted
            try:
                inventory_result = poll_reader(inventory_reader, 'inventory_poll')
            except HistoryInvalid:
                # A stale inventory candidate must not stall the remaining
                # scan. Its partial baseline has not committed a cursor.
                inventory_reader = None
                if not inventory_warning_emitted:
                    emit(AppEvent('gap', self.generation,
                                  '大厅部分聊天记录暂不可读，继续定位其他聊天，期间可能有遗漏。'))
                    inventory_warning_emitted = True
                return
            publish(inventory_result, inventory_reader)
            if not inventory_result.retry:
                inventory_reader = None

        try:
            trace('lifecycle', 'started', **self.diagnostic_snapshot())
            while not self._stop.is_set():
                try:
                    scanned = False
                    trace('summary', 'snapshot', interval=3, reader_status=capture_state or 'starting',
                          reason='source_lifetime_totals', published_count=serial, **self.diagnostic_snapshot())
                    processes = self.process_finder()
                    if self.selected_pid is not None and not any(p.pid == self.selected_pid for p in processes):
                        self.selected_pid = None
                    candidates = [p for p in processes if self.selected_pid is None or p.pid == self.selected_pid]
                    process = candidates[0] if len(candidates) == 1 else None
                    key = (process.pid, process.path.lower(), process.build) if process else None
                    trace('process', 'observed', interval=3, process_count=len(processes),
                          candidate_count=len(candidates), build=process.build if process else None,
                          expected_build=98348)
                    if session is not None and key != session:
                        observe_draft(observe=False)
                        cancel_scans('process_changed')
                        if game:game.close()
                        game = pid = image_base = reader = scanner = None
                        draft_context = None
                        context_previous, context_reason = {}, None
                        probe = nearby = watcher = None
                        cursors.clear()
                        areas.clear()
                        drain_nodes.clear()
                        record_identities.clear()
                        found_scan_ids.clear()
                        inventory_reader = None
                        census = True
                        failures = 0
                        next_scan = next_probe = next_nearby = 0.
                        connected = False
                        session = None
                        self.last_attempt = -float('inf')
                        transition('大厅采集保持开启，等待游戏连接。')
                    if self._reconnect.is_set():
                        self._reconnect.clear()
                        cancel_scans('requested_reconnect')
                        if reader:remember(reader.root, reader.cursor)
                        reader = scanner = None
                        probe = nearby = watcher = None
                        inventory_reader = None
                        connected = False
                        next_scan = 0.
                        if game is None:self.last_attempt = -float('inf')
                        transition('大厅采集持续开启，正在重新定位。')
                    if process is None:
                        emit(AppEvent('processes', self.generation, candidates))
                        state('等待游戏' if not candidates else '请选择游戏进程')
                        self._stop.wait(.5)
                        continue
                    if process.build != 98348:
                        state('游戏版本未验证')
                        self._stop.wait(2)
                        continue
                    if game is None:
                        if self.clock() - self.last_attempt < 60:
                            state('大厅连接等待重试，采集保持开启')
                            self._stop.wait(.5)
                            continue
                        self.last_attempt = self.clock()
                        game = self.game_factory(process.pid)
                        pid, session = process.pid, key
                        if not re.search(r'Base98348(?:[\\/]|$)', game.path):
                            raise OSError('Unsupported lobby client build')
                        image_base = self.module_base_finder(pid)
                        if not isinstance(image_base, int) or not 0x10000 <= image_base < 0x7fffffff0000:
                            raise OSError('Invalid game image address')
                        if game.read(image_base, 2) != b'MZ':
                            raise OSError('Invalid game image')
                        draft_context = self.draft_context_factory(game, image_base,
                            capture_after_filetime=capture_after_filetime, wall_clock=self.wall_clock)
                        transition('正在接入大厅聊天；接收本次采集开启后的消息。')
                    # Observe room boundaries even while discovery has no ring.
                    # Diagnostics only consume the resulting business snapshot.
                    observe_draft()
                    if reader is None:
                        state('大厅等待聊天，采集保持开启')
                        if self.clock() < next_scan:
                            self._stop.wait(.5)
                            continue
                        if scanner is None:
                            scanner = discovery(ignore_seen=False, stage='initial')
                        location = scan_step(scanner)
                        scanned = True
                        if location is None:
                            if scanner.finished:
                                scanner = None
                                next_scan = self.clock() + 5
                            self._stop.wait(.5 if scanner is None else .05)
                            continue
                        reader = replacement(location)
                        if census:
                            probe = scanner
                            if id(probe) in active_scans:
                                active_scans[id(probe)]['stage'] = 'inventory'
                                try:probe._diagnostic_stage = 'inventory'
                                except (AttributeError, TypeError):pass
                                scan_trace(probe, 'progress', reason='continued_initial_scan')
                        else:
                            active_scans.pop(id(scanner), None)
                        scanner = None
                        result = poll_reader(reader)
                        if result.retry:
                            self._stop.wait(.1)
                            continue
                    else:
                        result = poll_reader(reader)
                    failures = 0
                    publish(result)
                    if result.retry:
                        self._stop.wait(.1)
                        continue

                    # An intact old ring can remain readable after returning to
                    # the lobby. Watch saved tails cheaply instead of waiting
                    # for the active reader to throw an error.
                    if watcher is None:
                        watcher = discovery(regions=(), watch=True, stage='watcher')
                    candidate = scan_step(watcher, time_budget=.01)
                    if candidate is not None:
                        if candidate.root != reader.root:
                            reader = replacement(candidate)
                            publish(poll_reader(reader))
                        active_scans.pop(id(watcher), None)
                        watcher = None
                    elif watcher.finished:
                        watcher = None

                    if census:
                        if inventory_reader is not None:
                            poll_inventory()
                        elif probe is not None and not scanned:
                            candidate = scan_step(probe)
                            if candidate is not None:
                                if candidate.root != reader.root:
                                    inventory_reader = replacement(candidate)
                                    poll_inventory()
                            elif probe.finished:
                                census = False
                                probe = None
                                next_probe = self.clock() + 5
                    else:
                        quiet = self.clock() - last_activity >= 1
                        # Small neighborhoods of recent validated addresses are
                        # cheap enough to revisit promptly; unknown distant
                        # allocations use a separate, backed-off full pass.
                        if nearby is None and self.clock() >= next_nearby:
                            ranges = []
                            for root in reversed(cursors):
                                region = areas.get(root)
                                if region is None:
                                    continue
                                base = max(root & ~0xfffff, region[0])
                                end = min((root & ~0xfffff) + 1048576, region[0] + region[1])
                                area = (base, end - base, region[2])
                                if area not in ranges:
                                    ranges.append(area)
                                if len(ranges) == 8:
                                    break
                            nearby = discovery(regions=ranges, stage='nearby')
                        if nearby is not None:
                            candidate = scan_step(nearby, byte_budget=4 * 1048576, time_budget=.01)
                            if candidate is not None:
                                if candidate.root != reader.root:
                                    reader = replacement(candidate)
                                    publish(poll_reader(reader))
                                if watcher is not None:
                                    scan_trace(watcher, 'cancelled', reason='reader_replaced')
                                    active_scans.pop(id(watcher), None)
                                watcher = None
                            elif nearby.finished:
                                nearby = None
                                next_nearby = self.clock() + (1 if quiet else 2)
                        if probe is None and self.clock() >= next_probe:
                            probe = discovery(stage='discovery')
                        if probe is not None:
                            candidate = scan_step(probe)
                            if candidate is not None:
                                if candidate.root != reader.root:
                                    reader = replacement(candidate)
                                    publish(poll_reader(reader))
                                if watcher is not None:
                                    scan_trace(watcher, 'cancelled', reason='reader_replaced')
                                    active_scans.pop(id(watcher), None)
                                watcher = None
                                probe_backoff = 5.
                            elif probe.finished:
                                probe = None
                                next_probe = self.clock() + probe_backoff
                                probe_backoff = min(20., probe_backoff * 2)
                except DiscoveryCancelled:
                    break
                except OSError as failure:
                    trace('poll' if reader is not None else 'connect', 'error',
                          error_type=type(failure).__name__, winerror=getattr(failure,'winerror',None))
                    cancel_scans('read_error')
                    observe_draft(observe=False)
                    if reader:remember(reader.root, reader.cursor)
                    if game:game.close()
                    game = pid = image_base = reader = scanner = None
                    draft_context = None
                    context_previous, context_reason = {}, None
                    probe = nearby = watcher = None
                    inventory_reader = None
                    connected = False
                    self.last_attempt = self.clock()
                    state('大厅连接等待重试，采集保持开启')
                except (HistoryInvalid, ValueError, RuntimeError, struct.error) as failure:
                    trace('poll' if reader is not None else 'discover', 'error',
                          error_type=type(failure).__name__, reason=native_failure_reason(failure))
                    if reader:remember(reader.root, reader.cursor)
                    failures += 1
                    if reader is not None and failures <= 5:
                        # Keep the verified cursor through short list mutations.
                        # Each poll still validates its stamp and read budget.
                        self._stop.wait(.1)
                        continue
                    reader = scanner = None
                    cancel_scans('validation_error')
                    probe = nearby = watcher = None
                    inventory_reader = None
                    connected = False
                    next_scan = 0.
                    state('大厅等待新聊天，持续定位')
                self._stop.wait(.05 if probe is not None else .5)
        finally:
            observe_draft(observe=False)
            cancel_scans('source_stopped')
            trace('lifecycle', 'stopped', published_count=serial, **self.diagnostic_snapshot())
            if game:game.close()
            state('已停止')
