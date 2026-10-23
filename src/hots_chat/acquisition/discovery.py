import struct
import time
from collections import OrderedDict
from dataclasses import dataclass
from .history import HistoryInvalid, HistoryReader
from .memory import StreamingMatcher

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

PREFIX=b'<s val="ChatDisplayTimeStamp">'


def read_checked(memory,address,size):
    if not 0x10000<=address<0x7fffffff0000 or not 0<size<=4096:
        raise HistoryInvalid('Invalid candidate read')
    data=memory.read(address,size)
    if len(data)!=size: raise HistoryInvalid('Candidate is no longer readable')
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
            raise HistoryInvalid('Candidate has a cycle or invalid alignment')
        visited.add(node)
        header=read_checked(memory,node,24);headers[node]=header
        cached=known_nodes.get(node) if known_nodes is not None else None
        if cached and header==cached[1] and read_checked(memory,cached[0],16)==cached[2]:
            return remember(cached[0],cached[2])
        older,newer,packed=struct.unpack('<QQQ',header)
        length=(packed&0xffffffff)>>2
        if not len(PREFIX)<=length<=4096 or read_checked(memory,node+24,len(PREFIX))!=PREFIX:
            raise HistoryInvalid('Candidate is not an inline chat history string')
        if older&1:
            root=older&~1
            if root%8: raise HistoryInvalid('Invalid tagged sentinel')
            root_header=read_checked(memory,root,16)
            head,tail=struct.unpack('<QQ',root_header)
            if tail!=node:
                raise HistoryInvalid('Sentinel tail does not reference the oldest node')
            head_links=struct.unpack('<QQ',read_checked(memory,head,16))
            if head_links[1] not in (root,root|1):
                raise HistoryInvalid('Sentinel head reciprocity failed')
            return remember(root,root_header)
        links=struct.unpack('<QQ',read_checked(memory,older,16))
        if links[1]!=node:
            raise HistoryInvalid('Candidate links are not reciprocal')
        node=older
    raise HistoryInvalid('Candidate traversal exceeded its budget')


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
        self._started = time.perf_counter()
        self._regions = iter(self._ordered_regions(tuple(preferred_addresses)[:8]))
        self._region = None
        self._offset = 0
        self._matcher = StreamingMatcher(PREFIX)
        self._matches = None
        self._candidate = None
        self._scanned_bytes = 0
        self._known_nodes = {}
        self._checked_roots = OrderedDict()

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
                try:
                    root = locate_root(self.memory, address - 24, self._known_nodes)
                    if root in self._checked_roots:
                        continue
                    reader = HistoryReader(self.memory, root, include_history=True, channels=self.channels)
                    snapshot = reader.poll()
                    if not snapshot.retry:
                        self._checked_roots[root]=None
                        while len(self._checked_roots)>128:self._checked_roots.popitem(last=False)
                    accepted = (snapshot.messages and not snapshot.retry and
                                self.ignore_snapshots.get(root) != (reader.last_head, reader.last_stamp))
                except (HistoryInvalid, ValueError, struct.error):
                    accepted = False
                if self.cancel_event.is_set():
                    raise DiscoveryCancelled('校准已取消')
                if accepted:
                    return DiscoveryResult(root, self._scanned_bytes, time.perf_counter() - self._started)
                continue
            if self._region is None or self._offset >= self._region[1]:
                try:
                    self._region = next(self._regions)
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
            data = self.memory.read(address, count)
            self._offset += count
            requested_bytes += count
            self._scanned_bytes += len(data)
            self._matches = iter(self._matcher.feed(address, data))


def discover(memory,cancel_event,max_seconds=60,channels=None):
    matcher=StreamingMatcher(PREFIX)
    started=time.perf_counter(); cpu=time.process_time(); scanned=0; candidates=0; checked_roots=set()
    for base,size,protection in memory.regions():
        for offset in range(0,size,1024*1024):
            if cancel_event.is_set():
                raise DiscoveryCancelled('校准已取消')
            if time.perf_counter()-started>max_seconds:
                raise DiscoveryPending('Calibration time budget exhausted')
            data=memory.read(base+offset,min(1024*1024,size-offset));scanned+=len(data)
            for address in matcher.feed(base+offset,data):
                candidates+=1
                try:
                    root=locate_root(memory,address-24)
                    if root in checked_roots: continue
                    snapshot=HistoryReader(memory,root,include_history=True,channels=channels).poll()
                    if snapshot.retry: continue
                    checked_roots.add(root)
                    if not snapshot.messages: continue
                except (HistoryInvalid,ValueError,struct.error):
                    continue
                return DiscoveryResult(root,scanned,time.perf_counter()-started)
            # Initialization is bounded and throttled; polling never repeats this scan.
            due=scanned/(128*1024*1024)
            cancel_event.wait(max(0,due-(time.perf_counter()-started)))
    raise DiscoveryPending('No validated supported in-game chat history found')

