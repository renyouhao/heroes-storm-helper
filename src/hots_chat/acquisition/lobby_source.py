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
from .lobby_native import LobbyDiscovery, LobbyReader
from .memory import ReadOnlyGame, find_game_processes
from ..models import AppEvent, ChatEvent


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
                 reader_factory=LobbyReader, discovery_factory=LobbyDiscovery, wall_clock=None):
        self.selected_pid = selected_pid
        self.process_finder, self.game_factory = process_finder, game_factory
        self.clock = clock or time.monotonic
        self.wall_clock = wall_clock or time.time
        self._capture_after_filetime = None
        self.diagnostics = None
        self.module_base_finder = module_base_finder
        self.reader_factory, self.discovery_factory = reader_factory, discovery_factory
        self.generation, self.last_attempt = 0, -float('inf')
        self.thread = None
        self._stop, self._reconnect = threading.Event(), threading.Event()

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

        def state(value):
            nonlocal capture_state
            if value != capture_state:
                capture_state = value
                emit(AppEvent('capture_state', self.generation, value))

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

        def discovery(regions=None, ignore_seen=True, watch=False):
            return self.discovery_factory(game, self._stop, image_base,
                                          ignore_snapshots=cursors if ignore_seen else {},
                                          preferred_roots=tuple(root for root in reversed(cursors)
                                                                if not watch or reader is None or root != reader.root),
                                          regions=regions)

        def replacement(location):
            # A cursor belongs to one ring. Reusing another readable ring's tail
            # would silently redirect polling back to that obsolete ring.
            cursor = cursors.get(location.root)
            if location.region is not None:
                areas[location.root] = location.region
            return self.reader_factory(game, location.root, image_base, cursor=cursor,
                                       drain_nodes=drain_nodes.get(location.root, ()),
                                       record_identities=record_identities.get(location.root, ()),
                                       include_history=True,
                                       after_filetime=capture_after_filetime)

        def publish(result, owner=None):
            nonlocal connected, serial, last_activity, probe_backoff
            if result.retry:
                return
            owner = owner or reader
            if not connected:
                state('大厅已连接')
                emit(AppEvent('status', self.generation, '已连接大厅，持续接收队伍与密语。'))
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
            remember(owner.root, owner.cursor, owner)
            state('大厅已连接')

        def poll_inventory():
            nonlocal inventory_reader, inventory_warning_emitted
            try:
                inventory_result = inventory_reader.poll()
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
            while not self._stop.is_set():
                try:
                    scanned = False
                    processes = self.process_finder()
                    if self.selected_pid is not None and not any(p.pid == self.selected_pid for p in processes):
                        self.selected_pid = None
                    candidates = [p for p in processes if self.selected_pid is None or p.pid == self.selected_pid]
                    process = candidates[0] if len(candidates) == 1 else None
                    key = (process.pid, process.path.lower(), process.build) if process else None
                    if session is not None and key != session:
                        if game:game.close()
                        game = pid = image_base = reader = scanner = None
                        probe = nearby = watcher = None
                        cursors.clear()
                        areas.clear()
                        drain_nodes.clear()
                        record_identities.clear()
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
                        transition('正在接入大厅聊天；接收本次采集开启后的消息。')
                    if reader is None:
                        state('大厅等待聊天，采集保持开启')
                        if self.clock() < next_scan:
                            self._stop.wait(.5)
                            continue
                        if scanner is None:
                            scanner = discovery(ignore_seen=False)
                        location = scanner.step()
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
                        scanner = None
                        result = reader.poll()
                        if result.retry:
                            self._stop.wait(.1)
                            continue
                    else:
                        result = reader.poll()
                    failures = 0
                    publish(result)
                    if result.retry:
                        self._stop.wait(.1)
                        continue

                    # An intact old ring can remain readable after returning to
                    # the lobby. Watch saved tails cheaply instead of waiting
                    # for the active reader to throw an error.
                    if watcher is None:
                        watcher = discovery(regions=(), watch=True)
                    candidate = watcher.step(time_budget=.01)
                    if candidate is not None:
                        if candidate.root != reader.root:
                            reader = replacement(candidate)
                            publish(reader.poll())
                        watcher = None
                    elif watcher.finished:
                        watcher = None

                    if census:
                        if inventory_reader is not None:
                            poll_inventory()
                        elif probe is not None and not scanned:
                            candidate = probe.step()
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
                            nearby = discovery(regions=ranges)
                        if nearby is not None:
                            candidate = nearby.step(byte_budget=4 * 1048576, time_budget=.01)
                            if candidate is not None:
                                if candidate.root != reader.root:
                                    reader = replacement(candidate)
                                    publish(reader.poll())
                                watcher = None
                            elif nearby.finished:
                                nearby = None
                                next_nearby = self.clock() + (1 if quiet else 2)
                        if probe is None and self.clock() >= next_probe:
                            probe = discovery()
                        if probe is not None:
                            candidate = probe.step()
                            if candidate is not None:
                                if candidate.root != reader.root:
                                    reader = replacement(candidate)
                                    publish(reader.poll())
                                watcher = None
                                probe_backoff = 5.
                            elif probe.finished:
                                probe = None
                                next_probe = self.clock() + probe_backoff
                                probe_backoff = min(20., probe_backoff * 2)
                except DiscoveryCancelled:
                    break
                except OSError as failure:
                    if self.diagnostics is not None:
                        self.diagnostics.record('capture.state',source='lobby',outcome='error',
                            stage='poll' if reader is not None else 'connect',
                            error_type=type(failure).__name__,winerror=getattr(failure,'winerror',None))
                    if reader:remember(reader.root, reader.cursor)
                    if game:game.close()
                    game = pid = image_base = reader = scanner = None
                    probe = nearby = watcher = None
                    inventory_reader = None
                    connected = False
                    self.last_attempt = self.clock()
                    state('大厅连接等待重试，采集保持开启')
                except (HistoryInvalid, ValueError, RuntimeError, struct.error) as failure:
                    if self.diagnostics is not None:
                        self.diagnostics.record('capture.state',source='lobby',outcome='error',
                            stage='poll' if reader is not None else 'discover',error_type=type(failure).__name__)
                    if reader:remember(reader.root, reader.cursor)
                    failures += 1
                    if reader is not None and failures <= 5:
                        # Keep the verified cursor through short list mutations.
                        # Each poll still validates its stamp and read budget.
                        self._stop.wait(.1)
                        continue
                    reader = scanner = None
                    probe = nearby = watcher = None
                    inventory_reader = None
                    connected = False
                    next_scan = 0.
                    state('大厅等待新聊天，持续定位')
                self._stop.wait(.05 if probe is not None else .5)
        finally:
            if game:game.close()
            state('已停止')
