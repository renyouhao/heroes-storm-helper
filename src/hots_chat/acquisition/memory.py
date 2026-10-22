"""只读游戏访问；校准扫描不持久化内存内容。"""
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GameProcess:
    pid: int
    path: str
    build: int


class ProcessEntry(ctypes.Structure):
    """复用 Win32 类型；在轮询里定义会被 ctypes 指针类型缓存永久保留。"""
    _fields_ = [('size',wintypes.DWORD), ('usage',wintypes.DWORD), ('pid',wintypes.DWORD),
                ('heap',ctypes.c_size_t), ('module',wintypes.DWORD), ('threads',wintypes.DWORD),
                ('parent',wintypes.DWORD), ('priority',wintypes.LONG), ('flags',wintypes.DWORD),
                ('exe',wintypes.WCHAR * 260)]


def find_game_processes():
    if sys.platform != 'win32':
        return []
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD,wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [wintypes.HANDLE,ctypes.POINTER(ProcessEntry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2,0)
    if handle in (None,ctypes.c_void_p(-1).value):
        return []
    result = []
    try:
        entry = ProcessEntry(); entry.size = ctypes.sizeof(entry)
        ok = kernel.Process32FirstW(handle,ctypes.byref(entry))
        while ok:
            if entry.exe.lower() == 'heroesofthestorm_x64.exe':
                try:
                    with ReadOnlyGame(entry.pid) as game:
                        match = re.search(r'Base(\d+)',game.path)
                        result.append(GameProcess(entry.pid,game.path,int(match[1]) if match else 0))
                except (OSError,ValueError):
                    pass
            ok = kernel.Process32NextW(handle,ctypes.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    return result

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

