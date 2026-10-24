"""Build-specific, bounded read-only talent state; no UI or input operations.

The binding provider is a separate trust boundary: it must identify the local
gameplay player through native identity evidence. A missing binding, a unique
hero candidate or a manually remembered slot never authorizes this reader.
"""
from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import hashlib
import re
import struct
from typing import Any

from .memory import ReadOnlyGame, find_game_processes
from .lobby_source import main_module_base
from .model_codecs import (DECODER_CODE_FINGERPRINTS, DECODER_SALT_POINTER_RVAS,
                           SUPPORTED_DECODERS, decode_pointer)


@dataclass(frozen=True)
class TalentChoice:
    entry_index: int
    talent_id: str
    eligible: bool


@dataclass(frozen=True)
class TalentTier:
    tier_id: int
    required_level: int
    selected_value: int
    enabled: bool
    choices: tuple[TalentChoice, ...] = ()


@dataclass(frozen=True)
class TalentSnapshot:
    pid: int | None
    generation: tuple[Any, ...] | None = None
    hero_id: str | None = None
    level: int | None = None
    tiers: tuple[TalentTier, ...] = ()
    pending_tier: int | None = None
    status: str = 'unknown'
    reason: str = ''

    @property
    def selected(self):
        return tuple((tier.tier_id, tier.selected_value) for tier in self.tiers)


class TalentReader:
    def __init__(self, binding_provider=None, process_finder=find_game_processes,
                 game_factory=ReadOnlyGame, module_base_finder=main_module_base,
                 creation_token_finder=None, diagnostics=None):
        self.diagnostics = diagnostics
        self._diagnostic_binding_provider = None
        if binding_provider is None:
            try:
                from .talent_identity import resolve_local_identity
            except ImportError:
                resolve_local_identity = None
            binding_provider = resolve_local_identity
            self._diagnostic_binding_provider = resolve_local_identity
        self.binding_provider = binding_provider
        self.process_finder = process_finder
        self.game_factory = game_factory
        self.module_base_finder = module_base_finder
        self.creation_token_finder = creation_token_finder or _creation_token
        self._game = self._session = self._metadata = None
        self.last_read_bytes = self.last_read_calls = 0
        self.last_diagnostic = {}

    def poll(self, target_pid):
        self.last_read_bytes = self.last_read_calls = 0
        self.last_diagnostic = dict(expected_build=98348, stage='connect',
                                    read_calls=0, read_bytes=0)
        memory = None
        try:
            if self.binding_provider is None:
                raise _Unknown('本地玩家身份尚未确认')
            session = self._connect(target_pid)
            pid, _, build, base, created = session
            memory = _Memory(self._game, diagnostic_info=self.last_diagnostic,
                             diagnostics=self.diagnostics, diagnostic_source='talent')
            if memory.read(base, 2) != b'MZ':
                raise _Unknown('游戏模块验证失败')
            process_token = (pid, created, base, build)
            binding = self._resolve(memory, base, process_token)
            self.last_diagnostic['stage'] = 'talent'
            first = self._frame(memory, base, process_token, binding)
            second = self._frame(memory, base, process_token, binding)
            if first != second or self._resolve(memory, base, process_token) != binding:
                raise _Unknown('本局身份或天赋状态正在变化')
            if self.creation_token_finder(self._game) != created:
                raise _Unknown('游戏进程代次已变化')
            self.last_diagnostic.update(stage='talent', outcome=second.status, reason='')
            return second
        except (OSError, ValueError, RuntimeError, TypeError, struct.error) as exc:
            reason = str(exc) if isinstance(exc, _Unknown) else '游戏读取失败'
            self.last_diagnostic.update(outcome='unknown', reason=reason,
                                        error_type=type(exc).__name__)
            if type(getattr(exc, 'winerror', None)) is int:
                self.last_diagnostic['winerror'] = exc.winerror
            self.close()
            return TalentSnapshot(target_pid, reason=reason)
        finally:
            if memory is not None:
                self.last_read_bytes = memory.bytes
                self.last_read_calls = memory.calls
            self.last_diagnostic.update(read_bytes=self.last_read_bytes,
                                        read_calls=self.last_read_calls)

    def close(self):
        game, self._game = self._game, None
        self._session = self._metadata = None
        if game is not None:
            game.close()

    def _connect(self, target_pid):
        # Board readers also call this entry point directly; never reuse a stale
        # build or failure from a previous process/session.
        self.last_diagnostic = dict(expected_build=98348, stage='connect',
                                    read_calls=0, read_bytes=0)
        if self._session is not None and self._session[0] == target_pid:
            self.last_diagnostic['build'] = self._session[2]
            if self.creation_token_finder(self._game) != self._session[4]:
                raise _Unknown('游戏进程代次已变化')
            return self._session
        self.close()
        process = next((p for p in self.process_finder() if p.pid == target_pid), None)
        if process is None:
            raise _Unknown('等待游戏进程')
        self.last_diagnostic['build'] = process.build
        if process.build != 98348:
            raise _Unknown('当前游戏版本尚未验证')
        self._game = self.game_factory(process.pid)
        if self._game.path.lower() != process.path.lower():
            raise _Unknown('游戏进程映像已变化')
        base = self.module_base_finder(process.pid)
        created = self.creation_token_finder(self._game)
        if type(created) is not int or created <= 0:
            raise _Unknown('无法验证游戏进程代次')
        self._session = (process.pid, process.path.lower(), process.build, base, created)
        return self._session

    def _resolve(self, memory, base, process_token):
        self.last_diagnostic['stage'] = 'identity'
        if (self._diagnostic_binding_provider is not None
                and self.binding_provider is self._diagnostic_binding_provider):
            identity_diagnostic = {}
            binding = self.binding_provider(memory, base, process_token, diagnostic=identity_diagnostic)
            self.last_diagnostic.update(identity_diagnostic)
        else:
            binding = self.binding_provider(memory, base, process_token)
        if binding is None or getattr(binding, 'trusted', False) is not True:
            raise _Unknown('本地玩家身份尚未确认')
        if getattr(binding, 'pid', None) != process_token[0]:
            raise _Unknown('玩家身份属于其他进程')
        slot = getattr(binding, 'slot', None)
        token = getattr(binding, 'session_token', None)
        source = getattr(binding, 'source', None)
        if type(slot) is not int or not 0 <= slot < 16 or token is None or not source:
            raise _Unknown('玩家身份绑定不完整')
        hash(token)
        return (slot, token, source)

    def _frame(self, memory, base, process_token, binding):
        guards = _guards(memory, base)
        slot, binding_token, _ = binding
        model = _model(memory, base, slot)
        handle = memory.u64(model + 0x50)
        if not handle:
            raise _Unknown('当前没有有效英雄')
        hero_id, index = _link(memory, handle)
        catalog = _catalog(memory, base, index)
        header = (memory.u32(catalog + 0x518), memory.u64(catalog + 0x528),
                  memory.u32(catalog + 0x500), memory.u64(catalog + 0x510))
        identity = process_token + binding + (model, handle, index, catalog, hero_id)
        cache_key = identity + header
        if self._metadata is None or self._metadata[0] != cache_key:
            self._metadata = (cache_key, _metadata(memory, header))
        rows, entries = self._metadata[1]
        selected_raw = memory.read(base + 0x3677a30 + slot * 0x80, 0x80)
        selected = struct.unpack('<32i', selected_raw)
        level = memory.u32(base + 0x3678230 + slot * 4)
        bitmap = memory.u32(base + 0x3678270)
        if level > 100:
            raise _Unknown('英雄等级数据不正确')
        for tier_id, _ in rows:
            value = selected[tier_id]
            if value != -1 and (not 0 <= value < len(entries) or entries[value].tier_id != tier_id):
                raise _Unknown('已选天赋不属于当前英雄层级')
        pending = None
        for tier_id, required in rows:
            if bitmap & (1 << tier_id) and selected[tier_id] == -1:
                if level >= required:
                    pending = tier_id
                break
        tiers = []
        eligible_found = False
        for tier_id, required in rows:
            choices = []
            for entry in entries:
                if entry.tier_id != tier_id:
                    continue
                eligible = False
                if pending == tier_id and _conditions(entry, selected, entries):
                    eligible = _override(memory, base, entry.link_index)
                eligible_found |= eligible
                choices.append(TalentChoice(entry.index, entry.talent_id, eligible))
            tiers.append(TalentTier(tier_id, required, selected[tier_id],
                                    bool(bitmap & (1 << tier_id)), tuple(choices)))
        if _guards(memory, base) != guards or memory.read(base + 0x3677a30 + slot * 0x80, 0x80) != selected_raw:
            raise _Unknown('天赋状态正在更新')
        if memory.u32(base + 0x3678230 + slot * 4) != level or _model(memory, base, slot) != model or memory.u64(model + 0x50) != handle:
            raise _Unknown('英雄或等级正在变化')
        status = 'available' if pending is not None and eligible_found else 'idle'
        generation = process_token + (binding_token,)
        return TalentSnapshot(process_token[0], generation, hero_id, level, tuple(tiers),
                              pending if status == 'available' else None, status)


class _Unknown(ValueError):
    pass


class _Memory:
    """A per-poll budget; all addresses must already come from a validated chain."""
    def __init__(self, game, *, diagnostic_info=None, diagnostics=None, diagnostic_source=''):
        self.game, self.bytes, self.calls = game, 0, 0
        self.diagnostic_info = diagnostic_info
        self.diagnostics = diagnostics
        self.diagnostic_source = diagnostic_source

    def __getattr__(self, name):
        return getattr(self.game, name)

    def read(self, address, size):
        if type(address) is not int or not 0x10000 <= address < 0x7fffffff0000 or not 0 < size <= 16384:
            raise _Unknown('天赋数据指针不正确')
        if address + size >= 0x7fffffff0000 or self.bytes + size > 262144 or self.calls >= 4096:
            raise _Unknown('天赋读取超过安全预算')
        data = self.game.read(address, size)
        self.calls += 1
        self.bytes += len(data)
        if len(data) != size:
            raise _Unknown('天赋数据读取不完整')
        return data

    def u16(self, address): return struct.unpack('<H', self.read(address, 2))[0]
    def u32(self, address): return struct.unpack('<I', self.read(address, 4))[0]
    def u64(self, address): return struct.unpack('<Q', self.read(address, 8))[0]


def _creation_token(game):
    kernel = game.kernel
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    values = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(game.handle, *(ctypes.byref(value) for value in values)):
        raise _Unknown('无法读取游戏进程代次')
    return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime


def _guards(memory, base):
    api, talent = memory.read(base + 0x35c8cd9, 1)[0], memory.read(base + 0x34ef8ad, 1)[0]
    decoder = memory.u32(base + 0x3366118)
    try:
        return _validate_guards(api, talent, decoder)
    finally:
        from ..reader_replay import record_guard
        record_guard(memory, 'guards', api=api, talent=talent, decoder=decoder)


def _validate_guards(api, talent, decoder):
    if not api or not talent:
        raise _Unknown('当前场景没有可用天赋状态')
    if type(decoder) is not int or decoder not in SUPPORTED_DECODERS:
        raise _Unknown('当前天赋解码方式尚未验证')
    return api, talent, decoder


def _model(memory, base, slot):
    decoder = memory.u32(base + 0x3366118)
    try:
        _validate_model_decoder(decoder)
    finally:
        from ..reader_replay import record_guard
        record_guard(memory, 'model_decoder', decoder=decoder)
    _verify_model_codec(memory, base, decoder)
    root_rvas = (0x35c8cb0, 0x32c1ed0, 0x35c8cb4, 0x32c4cf4)
    parts = tuple(memory.u32(base + rva) for rva in root_rvas)
    low = (parts[0] - parts[1]) & 0xffffffff
    high = (parts[2] - parts[3]) & 0xffffffff
    pointer = _decode_model(memory, base, slot, low, high, decoder)
    if tuple(memory.u32(base + rva) for rva in root_rvas) != parts:
        raise _Unknown('玩家模型表正在变化')
    return pointer


def _validate_model_decoder(decoder):
    if type(decoder) is not int or decoder not in SUPPORTED_DECODERS:
        raise _Unknown('天赋解码方式已变化')


def _verify_model_codec(memory, base, decoder):
    profile = DECODER_CODE_FINGERPRINTS.get(decoder)
    if profile is None:
        raise _Unknown('当前解码分支的游戏代码尚未验证')
    rva, size, expected = profile
    actual = hashlib.sha256(memory.read(base + rva, size)).hexdigest()
    info = getattr(memory, 'diagnostic_info', None)
    if info is not None:
        info.update(decoder_code_fingerprint=actual, decoder_expected_fingerprint=expected,
                    decoder_code_verified=int(actual == expected))
    if actual != expected:
        if info is not None:
            info['stage'] = 'decoder_code'
        raise _Unknown('当前解码分支的游戏代码尚未验证')


def _decode_model(memory, base, slot, low, high, decoder):
    root = (high << 32) | low
    table = memory.u64(root)
    address = table + slot * 8
    raw = memory.read(address, 8)
    x, y = struct.unpack('<II', raw)
    keys = []
    salt_rva = DECODER_SALT_POINTER_RVAS.get(decoder)
    salt_pointer = memory.u64(base + salt_rva) if salt_rva is not None else None
    salt = memory.u32(salt_pointer) if salt_pointer is not None else None

    def read_key(index):
        value = memory.u32(base + 0x32c1c90 + index * 4)
        keys.append((index, value))
        return value

    pointer = decode_pointer(decoder, x, y, read_key, salt=salt)
    from ..reader_replay import record_model_decode
    record_model_decode(memory, decoder, x, y, keys, pointer, salt=salt)
    if (memory.u32(base + 0x3366118) != decoder
            or memory.u64(root) != table or memory.read(address, 8) != raw
            or any(memory.u32(base + 0x32c1c90 + index * 4) != value
                   for index, value in keys)):
        raise _Unknown('玩家模型正在变化')
    if salt_rva is not None and (memory.u64(base + salt_rva) != salt_pointer
                                 or memory.u32(salt_pointer) != salt):
        raise _Unknown('玩家模型解码参数正在变化')
    memory.read(pointer, 1)
    return pointer


def _link(memory, handle):
    node = memory.u64(handle + 0x30)
    view = node + 0x18
    length = memory.u32(view) >> 2
    if not 0 < length <= 160:
        raise _Unknown('英雄或天赋内部标识不正确')
    data = memory.u64(view + 8) if memory.u32(view + 4) & 2 else view + 8
    raw = memory.read(data, length)
    if not re.fullmatch(rb'[A-Za-z_][A-Za-z0-9_.:/-]{0,159}', raw):
        raise _Unknown('内部标识不是目录标识符')
    index = memory.u16(handle + 0x40)
    if index == 0xffff:
        raise _Unknown('目录链接尚未解析')
    return raw.decode('ascii'), index


def _catalog(memory, base, index):
    manager = memory.u64(base + 0x3388b18)
    catalog = memory.u64(manager + 0x148)
    count = memory.u32(catalog + 0x38)
    if not 0 < count <= 65536 or not 0 <= index < count:
        raise _Unknown('英雄目录索引不正确')
    table = memory.u64(catalog + 0x48)
    pointer = memory.u64(table + index * 8)
    if pointer == 0 or pointer & 1 or memory.read(pointer, 1)[0] & 1:
        raise _Unknown('英雄目录尚未解析；不会调用游戏解析器')
    return pointer


@dataclass(frozen=True)
class _Entry:
    index: int
    tier_id: int
    talent_id: str
    link_index: int
    required: tuple[int, ...]
    denied: tuple[tuple[int, ...], ...]


def _condition_links(memory, pointer, count):
    if not 0 <= count <= 128:
        raise _Unknown('天赋条件数量不正确')
    if not count:
        return ()
    raw = memory.read(pointer, count * 8)
    return tuple(_link(memory, struct.unpack_from('<Q', raw, i * 8)[0])[1] for i in range(count))


def _metadata(memory, header):
    tier_count, tier_pointer, entry_count, entry_pointer = header
    if not 0 < tier_count <= 32 or not 0 < entry_count <= 256:
        raise _Unknown('英雄天赋目录大小不正确')
    raw_rows = memory.read(tier_pointer, tier_count * 12)
    rows = tuple(sorted((struct.unpack_from('<III', raw_rows, i * 12)[:2]
                         for i in range(tier_count))))
    if len({tier for tier, _ in rows}) != tier_count or any(tier >= 32 or level > 100 for tier, level in rows):
        raise _Unknown('英雄天赋层级不正确')
    tier_ids = {tier for tier, _ in rows}
    raw_entries = memory.read(entry_pointer, entry_count * 0x40)
    entries = []
    for i in range(entry_count):
        offset = i * 0x40
        handle = struct.unpack_from('<Q', raw_entries, offset)[0]
        tier_id = struct.unpack_from('<I', raw_entries, offset + 8)[0]
        if tier_id not in tier_ids:
            raise _Unknown('天赋项属于未知层级')
        talent_id, link_index = _link(memory, handle)
        count = struct.unpack_from('<I', raw_entries, offset + 0x10)[0]
        pointer = struct.unpack_from('<Q', raw_entries, offset + 0x20)[0]
        required = _condition_links(memory, pointer, count)
        count = struct.unpack_from('<I', raw_entries, offset + 0x28)[0]
        pointer = struct.unpack_from('<Q', raw_entries, offset + 0x38)[0]
        if count > 128:
            raise _Unknown('天赋否决条件数量不正确')
        groups = memory.read(pointer, count * 0x18) if count else b''
        denied = tuple(_condition_links(memory,
                         struct.unpack_from('<Q', groups, j * 0x18 + 0x10)[0],
                         struct.unpack_from('<I', groups, j * 0x18)[0]) for j in range(count))
        entries.append(_Entry(i, tier_id, talent_id, link_index, required, denied))
    if any(not any(entry.tier_id == tier_id for entry in entries) for tier_id in tier_ids):
        raise _Unknown('天赋层没有对应选项')
    return rows, tuple(entries)


def _conditions(entry, selected, entries):
    owned = {entries[value].link_index for value in selected[:min(entry.tier_id, 32)]
             if 0 <= value < len(entries)}
    return (all(key in owned for key in entry.required)
            and not any(group and all(key in owned for key in group) for group in entry.denied))


def _override(memory, base, key):
    capacity = memory.u64(base + 0x3678280) & 0xffffffff
    if not capacity:
        return True
    if capacity > 65536 or capacity & (capacity - 1):
        raise _Unknown('天赋启用表结构不正确')
    buckets = memory.u64(base + 0x3678290)
    head = memory.u64(buckets + (key & (capacity - 1)) * 8)
    if not head:
        return True
    subtract = memory.u64(base + 0x2f727c0)
    node = memory.u64(head) & 0xfffffffffffffffe
    visited = set()
    for _ in range(16):
        if not node:
            return True
        if node in visited:
            raise _Unknown('天赋启用表发生循环')
        visited.add(node)
        record = node - subtract
        if memory.u32(record) == key and memory.u32(record + 4) == key:
            return bool(memory.read(record + 0x10, 1)[0])
        if memory.read(node, 1)[0] & 1:
            return True
        node = memory.u64(node)
    raise _Unknown('天赋启用表超过有界读取范围')
