"""Bounded read-only native lobby history for Chinese client build 98348.

The native list marks its oldest *record* with a tagged closing link. It does
not use the separate timestamp-history sentinel from a running match.
"""
from dataclasses import dataclass
from collections import OrderedDict
from datetime import datetime
import struct
import time

from .discovery import DiscoveryCancelled
from .history import HistoryInvalid, PollResult
from .memory import StreamingMatcher
from .parsing import ParsedChat


def valid_pointer(value):
    return 0x10000 <= value < 0x7fffffff0000 and value % 8 == 0


def read_exact(memory, address, count):
    if not 0x10000 <= address < 0x7fffffff0000 or not 0 < count <= 4096:
        raise HistoryInvalid('Invalid native chat read')
    data = memory.read(address, count)
    if len(data) != count:
        raise HistoryInvalid('Native chat data is no longer readable')
    return data


def read_nstring(memory, address, max_bytes=4096):
    """Read the observed 16-byte NString, including its small inline storage."""
    descriptor = read_exact(memory, address, 16)
    packed, flags = struct.unpack_from('<2I', descriptor)
    length = packed >> 2
    if length > max_bytes:
        raise HistoryInvalid('Native chat string exceeds its limit')
    if flags & 2:
        pointer = struct.unpack_from('<Q', descriptor, 8)[0]
        if not 0x10000 <= pointer < 0x7fffffff0000:
            raise HistoryInvalid('Invalid native string pointer')
        data = read_exact(memory, pointer, length) if length else b''
    else:
        if length > 7:
            raise HistoryInvalid('Invalid native inline string length')
        data = descriptor[8:8 + length]
    if read_exact(memory, address, 16) != descriptor:
        raise HistoryInvalid('Native chat string changed while reading')
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError as failure:
        raise HistoryInvalid('Incomplete native UTF-8 string') from failure

@dataclass(frozen=True)
class LobbyLayout:
    image_base: int
    @property
    def message_table(self):return self.image_base + 0x28e9ba0
    @property
    def system_table(self):return self.image_base + 0x28e9ba8
    @property
    def party_table(self):return self.image_base + 0x28e9720
    @property
    def whisper_table(self):return self.image_base + 0x28f01c0


@dataclass(frozen=True)
class LobbyLocation:
    root: int
    scanned_bytes: int
    cursor: tuple | None = None
    latest_filetime: int = 0
    region: tuple | None = None


def native_filetime(stamp):
    value = stamp[4] if stamp and len(stamp) == 6 and stamp[1] == 2 else 0
    return value if 125911584000000000 <= value <= 157469184000000000 else 0


class LobbyReader:
    def __init__(self, memory, root, image_base, metadata_factory=None,
                 include_history=False, max_nodes=512, byte_budget=262144, cursor=None,
                 after_filetime=None, drain_nodes=(), record_identities=()):
        if not valid_pointer(root) or not valid_pointer(image_base):
            raise HistoryInvalid('Invalid native lobby history location')
        self.memory, self.root = memory, root
        self.layout = LobbyLayout(image_base)
        self.metadata_factory, self.metadata = metadata_factory, None
        self.include_history = include_history
        self.capture_after_filetime = after_filetime
        self.after_filetime = after_filetime if cursor is None else None
        self.max_nodes, self.byte_budget = max_nodes, byte_budget
        self.poll_bytes = 0
        self.initialized = cursor is not None
        self.last_tail, self.last_stamp = cursor if cursor else (None, None)
        self._baseline_iterator = None
        self._baseline_gap = False
        self._drain_seen = set(drain_nodes)
        self._record_identities = OrderedDict((identity, None) for identity in record_identities)
        self.latest_filetime = native_filetime(self.last_stamp)
        self.inspected_cursor = None
        self.inspected_nodes = set()

    @property
    def cursor(self):
        return (self.last_tail, self.last_stamp) if self.initialized else None

    @property
    def drain_nodes(self):
        return frozenset(self._drain_seen)

    @property
    def record_identities(self):
        return tuple(self._record_identities)

    def _remember_records(self, identities):
        if self.capture_after_filetime is None:
            return
        for identity in identities:
            self._record_identities[identity] = None
            self._record_identities.move_to_end(identity)
        while len(self._record_identities) > 16384:
            self._record_identities.popitem(last=False)

    def read(self, address, count):
        if count <= 0 or self.poll_bytes + count > self.byte_budget:
            raise HistoryInvalid('Native lobby polling budget exceeded')
        self.poll_bytes += count
        return read_exact(self.memory, address, count)

    def _metadata(self):
        if self.metadata is None:
            factory = self.metadata_factory
            if factory is None:
                from .lobby_metadata import LobbyMetadata
                factory = LobbyMetadata
            self.metadata = factory(self, self.layout.image_base)
            if hasattr(self.metadata, 'begin_poll'):
                self.metadata.begin_poll()
        return self.metadata

    def entry(self, node, parse=True, classify=False):
        if not valid_pointer(node):
            raise HistoryInvalid('Invalid native chat node')
        table, following, kind = struct.unpack('<3Q', self.read(node - 8, 24))
        if not valid_pointer(following & ~1) or following & 6:
            raise HistoryInvalid('Invalid native history link')
        if (table, kind) not in ((self.layout.message_table, 2), (self.layout.system_table, 3)):
            raise HistoryInvalid('Unsupported native history record')
        parsed, supported = None, False
        if kind == 3:
            # System rows share the list, but their contents are not collected.
            stamp = (table, kind, self.read(node + 16, 16))
        else:
            payload = self.read(node + 16, 40)
            conversation, sender = struct.unpack_from('<2Q', payload)
            filetime = struct.unpack_from('<Q', payload, 32)[0]
            conversation_table = None
            if (parse or classify) and valid_pointer(conversation):
                try:
                    conversation_table = struct.unpack('<Q', self.read(conversation, 8))[0]
                except HistoryInvalid:
                    pass  # An expired conversation must not block a newer live one.
                supported = conversation_table in (self.layout.party_table, self.layout.whisper_table)
            stamp = (table, kind, conversation, sender, filetime, payload[16:32])
            if (supported and parse and (node, stamp) not in self._record_identities
                    and (self.after_filetime is None or filetime > self.after_filetime)):
                if not 125911584000000000 <= filetime <= 157469184000000000:
                    raise HistoryInvalid('Invalid native chat timestamp')
                text = read_nstring(self, node + 32)
                channel = '队伍' if conversation_table == self.layout.party_table else '密语'
                try:
                    metadata = self._metadata().resolve(conversation, sender)
                except HistoryInvalid:
                    metadata = None
                name = metadata[1] if metadata and metadata[0] == channel and metadata[1] else '发送者未知'
                timestamp = datetime.fromtimestamp((filetime - 116444736000000000) / 10000000).strftime('%H:%M:%S')
                parsed = ParsedChat(timestamp, channel, name, text)
        return dict(node=node, following=following, stamp=stamp, message=parsed, supported=supported)

    def _walk(self, start, parse=True, forbidden=None, classify=False, batch_size=None, seen_before=()):
        entries, seen, node = [], set(seen_before), start
        for _ in range(self.max_nodes):
            if node in seen or node == forbidden:
                raise HistoryInvalid('Native history contains an unmarked cycle')
            if len(seen) >= 16384:
                raise HistoryInvalid('Native history exceeds the safety limit')
            seen.add(node)
            entry = self.entry(node, parse=parse, classify=classify)
            entries.append(entry)
            following = entry['following']
            if following & 1:
                return entries, following & ~1
            if batch_size is not None and (len(entries) >= batch_size or self.poll_bytes >= self.byte_budget * .5):
                return entries, None
            node = following
        if batch_size is not None:
            return entries, None
        raise HistoryInvalid('Native history exceeded its node limit')

    def _ring_entries(self, start, parse=False, classify=False):
        """Work is resumed by callers; the corruption guard is not a poll budget."""
        seen, node = set(), start
        for _ in range(16384):
            if node in seen:
                raise HistoryInvalid('Native history contains an unmarked cycle')
            seen.add(node)
            entry = self.entry(node, parse=parse, classify=classify)
            yield entry
            if entry['following'] & 1:
                return
            node = entry['following']
        raise HistoryInvalid('Native history exceeds the safety limit')

    def inspect_steps(self):
        start = self.root
        self.latest_filetime = 0
        self.inspected_nodes.clear()
        for _ in range(2):
            first = last = None
            supported = False
            for entry in self._ring_entries(start, classify=True):
                if first is None:first = entry
                last = entry
                self.inspected_nodes.add(entry['node'])
                self.latest_filetime = max(self.latest_filetime, native_filetime(entry['stamp']))
                supported = supported or entry['supported']
                yield
            root = last['following'] & ~1
            if root != start:
                start = root
                continue
            if supported and self._fence(first, last):
                self.inspected_cursor = last['node'], last['stamp']
                return root
            return None
        return None

    def inspect(self):
        """Synchronous diagnostic helper; production drives inspect_steps instead."""
        self.poll_bytes = 0
        iterator = self.inspect_steps()
        while True:
            try:next(iterator)
            except StopIteration as completed:return completed.value

    def _fence(self, first, last):
        return (struct.unpack('<Q', self.read(first['node'], 8))[0] == first['following']
                and struct.unpack('<Q', self.read(last['node'], 8))[0] == last['following'])

    def _baseline_steps(self, gap):
        collect = self.include_history and (not gap or self.capture_after_filetime is not None)
        start = self.root
        for _ in range(2):
            first = last = None
            messages = []
            identities = []
            latest_filetime = 0
            for entry in self._ring_entries(start, parse=collect):
                if first is None:first = entry
                last = entry
                latest_filetime = max(latest_filetime, native_filetime(entry['stamp']))
                identity = entry['node'], entry['stamp']
                identities.append(identity)
                if collect and entry['message'] and identity not in self._record_identities:
                    messages.append(entry['message'])
                yield
            root = last['following'] & ~1
            if root != start:
                start = root
                continue
            if not self._fence(first, last):return None
            tail = self.entry(last['node'], parse=False)
            if tail['following'] != last['following']:return None
            return root, tail, messages, latest_filetime, identities
        return None

    def _baseline(self, gap=False):
        if self._baseline_iterator is None:
            self._drain_seen.clear()
            self._baseline_gap = gap
            if gap and self.capture_after_filetime is not None:
                self.after_filetime = self.capture_after_filetime
            self._baseline_iterator = self._baseline_steps(gap)
        deadline = time.perf_counter() + .05
        for _ in range(self.max_nodes):
            try:
                next(self._baseline_iterator)
            except StopIteration as completed:
                self._baseline_iterator = None
                if completed.value is None:
                    return PollResult([], False, True, self.poll_bytes)
                root, tail, messages, latest_filetime, identities = completed.value
                self.root = root
                self.last_tail, self.last_stamp = tail['node'], tail['stamp']
                self.initialized = True
                self.latest_filetime = max(self.latest_filetime, latest_filetime)
                self.after_filetime = None
                self._remember_records(identities)
                return PollResult(messages, self._baseline_gap, False, self.poll_bytes)
            except HistoryInvalid:
                self._baseline_iterator = None
                raise
            if self.poll_bytes >= self.byte_budget * .5 or time.perf_counter() >= deadline:
                break
        return PollResult([], False, True, self.poll_bytes)

    def poll(self):
        self.poll_bytes = 0
        if self.metadata is not None and hasattr(self.metadata, 'begin_poll'):
            self.metadata.begin_poll()
        if self._baseline_iterator is not None or not self.initialized:
            return self._baseline()
        try:
            anchor = self.entry(self.last_tail, parse=False)
        except HistoryInvalid:
            return self._baseline(gap=True)
        if anchor['stamp'] != self.last_stamp:
            return self._baseline(gap=True)
        if anchor['following'] & 1:
            self._drain_seen.clear()
            self.root = anchor['following'] & ~1
            return PollResult([], False, False, self.poll_bytes)
        seen = self._drain_seen or {self.last_tail}
        entries, root = self._walk(anchor['following'], forbidden=self.last_tail,
                                   batch_size=32, seen_before=seen)
        if not self._fence(anchor, entries[-1]):
            return PollResult([], False, True, self.poll_bytes)
        if root is not None:
            self.root = root
            self._drain_seen.clear()
        else:
            self._drain_seen = seen | {entry['node'] for entry in entries}
        self.last_tail, self.last_stamp = entries[-1]['node'], entries[-1]['stamp']
        self.latest_filetime = max(self.latest_filetime, *(native_filetime(entry['stamp']) for entry in entries))
        self._remember_records((entry['node'], entry['stamp']) for entry in entries)
        return PollResult([entry['message'] for entry in entries if entry['message']], False, False, self.poll_bytes)

class LobbyDiscovery:
    """Incrementally search the observed native message class, never heap text copies."""
    def __init__(self, memory, cancelled, image_base, ignore_snapshots=None,
                 preferred_roots=(), regions=None):
        self.memory, self.cancelled, self.image_base = memory, cancelled, image_base
        self.finished = False
        self.regions = iter(memory.regions() if regions is None else regions)
        self.ignore_snapshots = ignore_snapshots if ignore_snapshots is not None else {}
        self.preferred_roots = iter(preferred_roots)
        self.preferred_finished = False
        self.seen_nodes = set()
        self.region, self.offset = None, 0
        self.matches = None
        self.validator = self.inspector = None
        self.matcher = StreamingMatcher(struct.pack('<Q', LobbyLayout(image_base).message_table))
        self.scanned_bytes = 0

    def step(self, byte_budget=64 * 1024 * 1024, time_budget=0.05):
        if self.cancelled.is_set():
            raise DiscoveryCancelled('大厅聊天定位已取消')
        if self.finished or byte_budget <= 0 or time_budget <= 0:
            return None
        deadline, requested = time.perf_counter() + time_budget, 0
        validations = 0
        if self.inspector is not None:self.inspector.poll_bytes = 0
        while time.perf_counter() < deadline:
            if self.cancelled.is_set():
                raise DiscoveryCancelled('大厅聊天定位已取消')
            if self.validator is not None:
                try:
                    next(self.validator)
                    validations += 1
                    if validations >= 512 or self.inspector.poll_bytes >= self.inspector.byte_budget * .5:
                        return None
                except StopIteration as completed:
                    root = completed.value
                    inspector = self.inspector
                    self.validator = self.inspector = None
                    if root is not None:
                        # Avoid validating the same ring once per record in the heap.
                        if len(self.seen_nodes) < 131072:
                            self.seen_nodes.update(inspector.inspected_nodes)
                        if self.ignore_snapshots.get(root) != inspector.inspected_cursor:
                            region = self.region
                            if region is not None and not region[0] <= root < region[0] + region[1]:
                                region = None
                            return LobbyLocation(root, self.scanned_bytes, inspector.inspected_cursor,
                                                 inspector.latest_filetime, region)
                except (HistoryInvalid, ValueError, struct.error):
                    self.validator = self.inspector = None
                continue
            if not self.preferred_finished:
                try:
                    root = next(self.preferred_roots)
                except StopIteration:
                    self.preferred_finished = True
                else:
                    if not valid_pointer(root) or root in self.seen_nodes:
                        continue
                    self.inspector = LobbyReader(self.memory, root, self.image_base)
                    cursor = self.ignore_snapshots.get(root)
                    if cursor is not None:
                        try:
                            tail = self.inspector.entry(cursor[0], parse=False)
                            unchanged = (tail['stamp'] == cursor[1]
                                         and tail['following'] == (root | 1))
                        except HistoryInvalid:
                            unchanged = False
                        if unchanged:
                            self.seen_nodes.add(root)
                            self.inspector = None
                            validations += 1
                            if validations >= 128:
                                return None
                            continue
                    self.validator = self.inspector.inspect_steps()
                    continue
            if self.matches is not None:
                try:
                    candidate = next(self.matches)
                except StopIteration:
                    self.matches = None
                else:
                    if candidate % 8 or candidate + 8 in self.seen_nodes:
                        continue
                    self.inspector = LobbyReader(self.memory, candidate + 8, self.image_base)
                    self.validator = self.inspector.inspect_steps()
                    continue
            if self.region is None or self.offset >= self.region[1]:
                try:
                    self.region = next(self.regions)
                    self.offset = 0
                except StopIteration:
                    self.finished = True
                    return None
            if requested >= byte_budget:
                return None
            base, size, _ = self.region
            count = min(1048576, size - self.offset, byte_budget - requested)
            if count <= 0:
                self.region = None
                continue
            address = base + self.offset
            data = self.memory.read(address, count)
            self.offset += count
            requested += count
            self.scanned_bytes += len(data)
            self.matches = iter(self.matcher.feed(address, data))
        return None
