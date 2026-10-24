import struct
import time
import re
from itertools import count
from collections import OrderedDict
from dataclasses import dataclass
from .history import HistoryInvalid, HistoryReader, MATCH_CAPTURE_SCHEMA
from .parsing import TIMESTAMP_PREFIX, HISTORY_PREFIXES, has_history_prefix
from ..channels import DISPLAY_CHANNELS

class DiscoveryCancelled(HistoryInvalid):
    pass


class DiscoveryPending(HistoryInvalid):
    """有效游戏进程中暂时没有可接入的聊天，允许保留句柄继续定位。"""

    pass

@dataclass(frozen=True)
class DiscoveryResult:
    root: int
    scanned_bytes: int
    elapsed_seconds: float
    scan_id: int = 0

PREFIX=TIMESTAMP_PREFIX
# A common literal '<' keeps the regex search linear and fast on large blocks.
_SCAN_PREFIX = re.compile(
    rb'<(?:s val="ChatDisplayTimeStamp">|(?:sp/><)?c val="[^"<>\r\n]{1,32}">\[(?:'
    + '|'.join(re.escape(channel) for channel in DISPLAY_CHANNELS).encode('utf-8') + rb')\])')
_SCAN_IDS = count(1)
_FAILURE_REASONS = frozenset(('history_invalid', 'invalid_root', 'invalid_address',
    'read_budget', 'read_unavailable', 'invalid_links', 'invalid_layout', 'timestamp_missing',
    'invalid_utf8', 'history_cleared', 'node_budget'))


class _HistoryPrefixMatcher:
    def __init__(self):
        self.tail = b''
        self.previous_end = None

    def feed(self, base, data):
        prefix = self.tail if self.previous_end == base else b''
        combined = prefix + data
        start = base - len(prefix)
        for match in _SCAN_PREFIX.finditer(combined):
            address = start + match.start()
            if match.end() > len(prefix) and address % 8 == 0:
                yield address
        # Both supported heads fit in the same bounded metadata window.
        self.tail = combined[-95:]
        self.previous_end = base + len(data)


def read_checked(memory,address,size):
    if not 0x10000<=address<0x7fffffff0000 or not 0<size<=4096:
        raise HistoryInvalid('Invalid candidate read', reason='invalid_address')
    data=memory.read(address,size)
    if len(data)!=size: raise HistoryInvalid('Candidate is no longer readable', reason='read_unavailable')
    return data


def locate_root(memory,node,known_nodes=None):
    visited=set();headers={}
    def remember(root,root_header):
        if known_nodes is not None:
            for address,header in headers.items():known_nodes[address]=(root,header,root_header)
            while len(known_nodes)>4096:known_nodes.pop(next(iter(known_nodes)))
        return root
    for _ in range(512):
        if node in visited or node%8:
            raise HistoryInvalid('Candidate has a cycle or invalid alignment', reason='invalid_links')
        visited.add(node)
        header=read_checked(memory,node,24);headers[node]=header
        cached=known_nodes.get(node) if known_nodes is not None else None
        if cached and header==cached[1] and read_checked(memory,cached[0],16)==cached[2]:
            return remember(cached[0],cached[2])
        older,newer,packed=struct.unpack('<QQQ',header)
        length=(packed&0xffffffff)>>2
        if (not min(map(len, HISTORY_PREFIXES))<=length<=4096
                or not has_history_prefix(read_checked(memory,node+24,min(length,len(PREFIX))))):
            raise HistoryInvalid('Candidate is not an inline chat history string', reason='invalid_layout')
        if older&1:
            root=older&~1
            if root%8: raise HistoryInvalid('Invalid tagged sentinel', reason='invalid_links')
            root_header=read_checked(memory,root,16)
            head,tail=struct.unpack('<QQ',root_header)
            if tail!=node:
                raise HistoryInvalid('Sentinel tail does not reference the oldest node', reason='invalid_links')
            head_links=struct.unpack('<QQ',read_checked(memory,head,16))
            if head_links[1] not in (root,root|1):
                raise HistoryInvalid('Sentinel head reciprocity failed', reason='invalid_links')
            return remember(root,root_header)
        links=struct.unpack('<QQ',read_checked(memory,older,16))
        if links[1]!=node:
            raise HistoryInvalid('Candidate links are not reciprocal', reason='invalid_links')
        node=older
    raise HistoryInvalid('Candidate traversal exceeded its budget', reason='node_budget')


def _poll_candidate(memory, root, checked_roots, channels, counts=None):
    # A root can gain supported messages while a scan is still in progress.
    # Keep its bounded cursor, not a permanent "already checked" address mark.
    reader = checked_roots.get(root)
    if reader is None:
        reader = HistoryReader(memory, root, include_history=True, channels=channels)
    before = reader.diagnostic_snapshot() if counts is not None else {}
    try:
        snapshot = reader.poll()
    except (HistoryInvalid, ValueError, struct.error):
        checked_roots.pop(root, None)
        raise
    finally:
        if counts is not None:
            # Commit observations even when a reader fails or is later evicted.
            for field, value in reader.diagnostic_snapshot().items():
                counts[field] = counts.get(field, 0) + value - before[field]
    if not snapshot.retry:
        checked_roots[root] = reader
        checked_roots.move_to_end(root)
        while len(checked_roots) > 128:
            checked_roots.popitem(last=False)
    return reader, snapshot


class IncrementalDiscovery:
    """Resume a bounded scan without retaining regions or previously scanned blocks.

    ``byte_budget`` limits scan payload requests, each of at most 1 MiB. Candidate
    validation keeps locate_root/HistoryReader's separate node and byte limits;
    those reads are not part of the scan payload budget. Time and cancellation
    are checked between reads/candidates, not by preempting a synchronous read
    or one bounded candidate validation. Only unchanged ignored snapshots are
    skipped, so a reused root/head address can still produce a new result.
    """

    def __init__(self, memory, cancel_event, ignore_snapshots=None, preferred_addresses=(), channels=None):
        self.memory = memory
        self.cancel_event = cancel_event
        self.channels = channels
        self.ignore_snapshots = ignore_snapshots if ignore_snapshots is not None else {}
        self.finished = False
        self.scan_id = next(_SCAN_IDS)
        self._started = time.perf_counter()
        self._regions = iter(self._ordered_regions(tuple(preferred_addresses)[:8]))
        self._region = None
        self._offset = 0
        self._matcher = _HistoryPrefixMatcher()
        self._matches = None
        self._candidate = None
        self._scanned_bytes = 0
        self._scan_reads = 0
        self._requested_bytes = 0
        self._scan_regions = 0
        self._short_reads = 0
        self._last_rejection_reason = ''
        self._counts = dict(candidate_count=0, valid_snapshot_count=0,
                            invalid_candidate_count=0, empty_snapshot_count=0,
                            retry_snapshot_count=0, ignored_snapshot_count=0,
                            candidate_read_failure_count=0, candidate_layout_rejected_count=0,
                            candidate_link_rejected_count=0, candidate_budget_rejected_count=0)
        self._known_nodes = {}
        self._checked_roots = OrderedDict()

    def diagnostic_snapshot(self):
        """Return counts only; scan reads exclude bounded candidate validation."""
        return dict(self._counts, capture_schema=MATCH_CAPTURE_SCHEMA, scan_id=self.scan_id,
                    scan_region_count=self._scan_regions, requested_bytes=self._requested_bytes,
                    short_read_count=self._short_reads,
                    last_rejection_reason=self._last_rejection_reason, read_bytes=self._scanned_bytes,
                    read_calls=self._scan_reads,
                    elapsed_ms=(time.perf_counter() - self._started) * 1000)

    def _ordered_regions(self,addresses):
        if not addresses:
            yield from self.memory.regions()
            return
        preferred={}
        for region in self.memory.regions():
            if self.cancel_event.is_set():raise DiscoveryCancelled('校准已取消')
            base,size,_=region
            for index,address in enumerate(addresses):
                if address is not None and base<=address<base+size:
                    preferred[index]=region
            if len(preferred)==len(addresses):break
        emitted=set()
        for index in sorted(preferred):
            region=preferred[index]
            if region[:2] not in emitted:
                emitted.add(region[:2]);yield region
        for region in self.memory.regions():
            if self.cancel_event.is_set():raise DiscoveryCancelled('校准已取消')
            if region[:2] not in emitted:yield region

    def step(self, byte_budget=64 * 1024 * 1024, time_budget=0.05):
        if self.cancel_event.is_set():
            raise DiscoveryCancelled('校准已取消')
        if self.finished or byte_budget <= 0 or time_budget <= 0:
            return None
        deadline = time.perf_counter() + time_budget
        requested_bytes = 0
        while True:
            if self.cancel_event.is_set():
                raise DiscoveryCancelled('校准已取消')
            if self._candidate is None and self._matches is not None:
                try:
                    self._candidate = next(self._matches)
                except StopIteration:
                    self._matches = None
            if self._candidate is not None:
                if time.perf_counter() >= deadline:
                    return None
                address = self._candidate
                self._candidate = None
                self._counts['candidate_count'] += 1
                try:
                    root = locate_root(self.memory, address - 24, self._known_nodes)
                    reader, snapshot = _poll_candidate(self.memory, root, self._checked_roots, self.channels, self._counts)
                    if snapshot.retry:
                        self._counts['retry_snapshot_count'] += 1
                    else:
                        self._counts['valid_snapshot_count'] += 1
                        if not snapshot.messages:
                            self._counts['empty_snapshot_count'] += 1
                        elif self.ignore_snapshots.get(root) == (reader.last_head, reader.last_stamp):
                            self._counts['ignored_snapshot_count'] += 1
                    accepted = (snapshot.messages and not snapshot.retry and
                                self.ignore_snapshots.get(root) != (reader.last_head, reader.last_stamp))
                except (HistoryInvalid, ValueError, struct.error) as failure:
                    self._counts['invalid_candidate_count'] += 1
                    reason = getattr(failure, 'reason', 'invalid_layout')
                    self._last_rejection_reason = reason if reason in _FAILURE_REASONS else 'history_invalid'
                    category = {
                        'read_unavailable': 'candidate_read_failure_count',
                        'invalid_address': 'candidate_read_failure_count',
                        'invalid_layout': 'candidate_layout_rejected_count',
                        'timestamp_missing': 'candidate_layout_rejected_count',
                        'invalid_utf8': 'candidate_layout_rejected_count',
                        'invalid_root': 'candidate_link_rejected_count',
                        'invalid_links': 'candidate_link_rejected_count',
                        'read_budget': 'candidate_budget_rejected_count',
                        'node_budget': 'candidate_budget_rejected_count',
                    }.get(self._last_rejection_reason)
                    if category:
                        self._counts[category] += 1
                    accepted = False
                if self.cancel_event.is_set():
                    raise DiscoveryCancelled('校准已取消')
                if accepted:
                    return DiscoveryResult(root, self._scanned_bytes, time.perf_counter() - self._started, self.scan_id)
                continue
            if self._region is None or self._offset >= self._region[1]:
                try:
                    self._region = next(self._regions)
                    self._scan_regions += 1
                    self._offset = 0
                except StopIteration:
                    self.finished = True
                    return None
                if self._region[1] <= 0:
                    continue
            if requested_bytes >= byte_budget or time.perf_counter() >= deadline:
                return None
            base, size, _ = self._region
            count = min(1024 * 1024, size - self._offset, byte_budget - requested_bytes)
            address = base + self._offset
            self._scan_reads += 1
            self._requested_bytes += count
            data = self.memory.read(address, count)
            self._offset += count
            self._short_reads += int(len(data) != count)
            requested_bytes += count
            self._scanned_bytes += len(data)
            self._matches = iter(self._matcher.feed(address, data))


def discover(memory,cancel_event,max_seconds=60,channels=None,progress=None):
    scanner = IncrementalDiscovery(memory, cancel_event, channels=channels)

    def report(outcome):
        if progress is not None:
            try:
                progress(outcome, scanner.diagnostic_snapshot())
            except Exception:
                pass  # Diagnostic callbacks must not change discovery results.

    report('started')
    try:
        while True:
            if cancel_event.is_set():
                raise DiscoveryCancelled('校准已取消')
            if time.perf_counter() - scanner._started > max_seconds:
                report('timeout')
                raise DiscoveryPending('Calibration time budget exhausted', reason='scan_timeout')
            result = scanner.step(byte_budget=1024 * 1024)
            if result is not None:
                report('candidate_found')
                return result
            if scanner.finished:
                report('exhausted')
                raise DiscoveryPending('No validated supported in-game chat history found', reason='scan_exhausted')
            report('scanning')
            # Keep the existing 128 MiB/s initialization throttle.
            due = scanner._requested_bytes / (128 * 1024 * 1024)
            cancel_event.wait(max(0, due - (time.perf_counter() - scanner._started)))
    except DiscoveryCancelled:
        report('cancelled')
        raise
    except DiscoveryPending:
        raise
    except (HistoryInvalid, OSError, ValueError, RuntimeError):
        report('failed')
        raise

