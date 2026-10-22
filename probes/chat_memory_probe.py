"""Throwaway read-only feasibility probe; not a production chat reader.

Searches only an explicit HOTS_CHAT_TEST marker. Never writes remote memory,
injects code, attaches a debugger, or saves a process memory dump.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import html
from pathlib import Path
import re
import sys
import time


class StreamingMatcher:
    def __init__(self, marker: bytes):
        if not marker:
            raise ValueError("marker must not be empty")
        self.marker = marker
        self.tail = b""
        self.previous_end = None

    def feed(self, base: int, data: bytes):
        prefix = self.tail if self.previous_end == base else b""
        combined = prefix + data
        start_address = base - len(prefix)
        position = combined.find(self.marker)
        while position >= 0:
            if position + len(self.marker) > len(prefix):
                yield start_address + position
            position = combined.find(self.marker, position + 1)
        self.tail = combined[-(len(self.marker) - 1):] if len(self.marker) > 1 else b""
        self.previous_end = base + len(data)


def find_matches(chunks, marker: bytes):
    matcher = StreamingMatcher(marker)
    for base, data in chunks:
        yield from matcher.feed(base, data)


class MessageBits:
    """Bit grouping follows Blizzard's public heroprotocol message schema."""
    def __init__(self, data):
        self.data = data
        self.byte = 0
        self.bit = 0

    def read(self, count):
        result = 0
        while count:
            if self.byte >= len(self.data):
                raise EOFError("Incomplete message header")
            take = min(count, 8 - self.bit)
            part = (self.data[self.byte] >> self.bit) & ((1 << take) - 1)
            result = (result << take) | part
            self.bit += take
            count -= take
            if self.bit == 8:
                self.byte += 1
                self.bit = 0
        return result

    def blob(self, length):
        start = self.byte + bool(self.bit)
        end = start + length
        if end > len(self.data):
            raise EOFError("Incomplete chat payload")
        self.byte, self.bit = end, 0
        return self.data[start:end]


def decode_chat_record(data):
    # Schema reference: Blizzard/heroprotocol protocol96477, SChatMessage.
    # Current-build compatibility is verified only against explicit live markers.
    bits = MessageBits(data)
    tag = bits.read(2)
    delta = bits.read((6, 14, 22, 32)[tag])
    user_id = bits.read(5)
    event_type = bits.read(4)
    if event_type != 0:
        raise ValueError("Not a chat message event")
    recipient = bits.read(3)
    length = bits.read(11)
    text = bits.blob(length).decode("utf-8", errors="strict")
    return {"gameloop_delta": delta, "user_id": user_id, "recipient": recipient,
            "text": text, "consumed_bytes": bits.byte}


def parse_display_chat(source):
    """Accept only the observed game chat format; preserve literal message markup."""
    if len(source) > 65536:
        return None
    match = re.fullmatch(
        r'<s val="ChatDisplayTimeStamp">(?P<time>\d+:\d{2})</s><sp/>'
        r'(?P<prefix>.*?)<w>(?P<sender>.*?)</w></c>: </c>(?P<body>.*)', source, re.DOTALL)
    if not match:
        return None
    channel = re.search(r'\[([^\[\]\r\n]{1,32})\]', match['prefix'])
    if not channel:
        return None
    sender = html.unescape(re.sub(r'<[^>]*>', '', match['sender']))
    return {"time":match['time'], "channel":html.unescape(channel[1]),
            "sender":sender, "text":html.unescape(match['body'])}


class MemoryInfo(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


class ReadOnlyGame:
    ACCESS = 0x0400 | 0x0010  # QUERY_INFORMATION | VM_READ, no write access.

    def __init__(self, process_id: int):
        if sys.platform != "win32" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise RuntimeError("Requires 64-bit Windows Python")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MemoryInfo), ctypes.c_size_t]
        self.kernel.VirtualQueryEx.restype = ctypes.c_size_t
        self.kernel.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        self.kernel.ReadProcessMemory.restype = wintypes.BOOL
        self.handle = self.kernel.OpenProcess(self.ACCESS, False, process_id)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            path = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(path))
            if not self.kernel.QueryFullProcessImageNameW(self.handle, 0, path, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.path = path.value
            if Path(self.path).name.lower() != "heroesofthestorm_x64.exe":
                raise ValueError("Probe is restricted to HeroesOfTheStorm_x64.exe")
        except BaseException:
            self.close()
            raise
        self.read_buffer = ctypes.create_string_buffer(1024 * 1024)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def regions(self):
        address = 0
        while address < 0x00007FFFFFFF0000:
            info = MemoryInfo()
            size = self.kernel.VirtualQueryEx(self.handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info))
            if not size:
                return
            start = info.BaseAddress or 0
            length = info.RegionSize
            if length <= 0 or start + length <= address:
                raise RuntimeError("Invalid virtual memory region")
            if info.State == 0x1000 and info.Type == 0x20000 and not info.Protect & 0x100 and info.Protect & 0xFF in (0x02, 0x04, 0x08):
                yield (start, length, info.Protect)
            address = start + length

    def read(self, address: int, length: int):
        if not 0 < length <= len(self.read_buffer):
            raise ValueError("Read length is outside the fixed buffer")
        count = ctypes.c_size_t()
        self.kernel.ReadProcessMemory(self.handle, ctypes.c_void_p(address), self.read_buffer, length, ctypes.byref(count))
        return ctypes.string_at(ctypes.addressof(self.read_buffer), count.value)


def scan(game, marker: str, max_mib: int, max_seconds: float, delay_ms: int):
    patterns = {"utf-8": marker.encode("utf-8"), "utf-16-le": marker.encode("utf-16-le")}
    matchers = {encoding: StreamingMatcher(pattern) for encoding, pattern in patterns.items()}
    regions = list(game.regions())
    started = time.perf_counter()
    cpu_start = time.process_time()
    matches = []
    bytes_read = 0
    failed_reads = 0
    next_progress = 128 * 1024 * 1024
    stopped_reason = None
    for region_base, region_size, protection in regions:
        offset = 0
        while offset < region_size:
            if bytes_read >= max_mib * 1024 * 1024:
                stopped_reason = "byte_budget"
                break
            if time.perf_counter() - started > max_seconds:
                stopped_reason = "time_budget"
                break
            address = region_base + offset
            amount = min(1024 * 1024, region_size - offset, max_mib * 1024 * 1024 - bytes_read)
            data = game.read(address, amount)
            bytes_read += len(data)
            if len(data) != amount:
                failed_reads += 1
            for encoding, matcher in matchers.items():
                for hit in matcher.feed(address, data):
                    entry = {"address": hex(hit), "encoding": encoding, "region_base": hex(region_base), "region_size": region_size}
                    matches.append(entry)
                    print(json.dumps({"match": entry}), flush=True)
            offset += amount
            if bytes_read >= next_progress:
                print(json.dumps({"scanned_mib": round(bytes_read / 1024 / 1024), "matches": len(matches)}), flush=True)
                next_progress += 128 * 1024 * 1024
            if delay_ms:
                time.sleep(delay_ms / 1000 * len(data) / (1024 * 1024))
        if stopped_reason:
            break
    for hit in matches:
        expected = patterns[hit["encoding"]]
        hit["still_matches_after_scan"] = game.read(int(hit["address"], 16), len(expected)) == expected
    elapsed = time.perf_counter() - started
    return {
        "kind": "temporary_marker_locator",
        "game_path": game.path,
        "open_process_access": hex(game.ACCESS),
        "marker": marker,
        "timestamp": datetime.now().astimezone().isoformat(),
        "eligible_regions": len(regions),
        "eligible_mib": round(sum(region[1] for region in regions) / 1024 / 1024, 2),
        "scanned_mib": round(bytes_read / 1024 / 1024, 2),
        "failed_or_partial_reads": failed_reads,
        "wall_seconds": round(elapsed, 3),
        "process_cpu_seconds": round(time.process_time() - cpu_start, 3),
        "stop_reason": stopped_reason or "completed",
        "matches": matches,
        "limitations": [
            "Matching strings can be input, render, history or stale copies; they do not establish a chat object layout.",
            "This scan is a one-time locator, not the steady-state resource usage of a chat reader.",
            "Only committed readable private memory was examined. No code injection, memory writes or access bypass is used.",
            "No game memory dump or unrelated payload is saved."
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-mib", type=int, default=3072)
    parser.add_argument("--max-seconds", type=float, default=90)
    parser.add_argument("--delay-ms", type=int, default=4)
    args = parser.parse_args()
    if not args.marker.startswith("HOTS_CHAT_TEST_") or len(args.marker) < 20 or len(args.marker) > 120:
        parser.error("Use a unique explicit HOTS_CHAT_TEST_ marker between 20 and 120 characters")
    if not 1 <= args.max_mib <= 8192 or not 1 <= args.max_seconds <= 180 or not 0 <= args.delay_ms <= 100:
        parser.error("Invalid scan resource budget")
    with ReadOnlyGame(args.pid) as game:
        report = scan(game, args.marker, args.max_mib, args.max_seconds, args.delay_ms)
    report["process_id"] = args.pid
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(args.output), "matches": len(report["matches"]), "stop_reason": report["stop_reason"]}), flush=True)


if __name__ == "__main__":
    main()
