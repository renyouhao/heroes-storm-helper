"""Build 98348 征召团队的只读上下文谓词；不执行游戏函数、不读取聊天正文。

原生名称函数依据当前全局房间属性选词，不能据此给历史消息重标频道。
这里验证的是该命名谓词及观察边界，不宣称 drft 表示瞬时选人活跃阶段。
"""
from dataclasses import dataclass
import hashlib
import math
import re
import struct
import time


PROFILE_BUILD = 98348
LOBBY_CHAT_VTABLE_RVA = 0x28e9540
LOBBY_CONVERSATION_VTABLE_RVA = LOBBY_CHAT_VTABLE_RVA
_MANAGER_GLOBAL = 0x3b7c6b0
_API_GLOBAL = 0x3b7c6c0
_LOBBY_VTABLE = 0x28ede18
_CONTEXT_VTABLE = 0x29e9038
_CALLBACK = 0x23b8470
_API_NAME = b'BattlenetAPI_IsInLobby'
_DRAFT = 0x64726674
_TOURNAMENT = 0x746f7572
_MODE_VALUES = (1819239033, 1751478895, _DRAFT)  # loby, hero, drft
_CATEGORY_VALUES = (1937006958, _DRAFT, _TOURNAMENT)  # stan, drft, tour
_MAX_ATTRIBUTES = 512
_MAX_OPERATION_BYTES = 32768

# Exact loaded-code ranges of build 98348; protected on-disk .text differs.
VERIFIED_CODE_FINGERPRINTS = (
    (0x1db7030, 130, '86354662219087810254c96953591fb96d9ede4ba3766458fb2e256006fee018'),
    (0x1e40050, 230, '8ae9b4bb121690deebd3b2b46975911039aa7f282fc36f91fdcc770c4c7c1145'),
    (0x1e40a70, 46, 'b139fbd2481c42f78871ad85d61f26f68b30217ca1f0186a73a4d665008ea645'),
    (0x1d82e00, 5, 'ca3aab76b94acd7081ad378320443ef2f9a4c3551ec59cde6a86c27d7062522a'),
    (0x1e0fbf0, 51, '9b75d7c02d73975482384d2cf962fc8d2b9fa8316c402f00eb5fab73b95f4729'),
    (0x1e16870, 109, '35896979b0f5de5532a31879240625b973180238e2b5fcca3dba375212c8fd44'),
    (0x1e16cf0, 109, 'd63a39cb33a3f46fd238340727eae763d15ef796f37626455a739d3ed299d0c8'),
    (0xa50450, 49, '293045083db2978f080d7ed7404752d9e58442c8af4669adc947531ffe427a7d'),
    (0xa50490, 49, '39a525363fa956990fa2dc346bcf94b23d644caf73c9aabfaa18c98ab98a0ca1'),
    (0x1e0fb70, 116, '5f6bc2477c0fa531a4a3aee5583b64abdc4fa0d406cff7efa7a66a23298db956'),
    (0x23b8470, 55, 'ecfabf811b0fd1a9d589d729ceb42ddca9f3f245027a79cee8ef8ef93a528120'),
    (0x236d960, 103, '4cd57d8640403feb5a78254265626bbf91b7e20f9378bc0a05d6e9e96f9d8b02'),
    (0x2227300, 15, '64c79e8f60539a39b5ce4a0f3f6a3f8917d8499844292e939a7e783b0fd346d9'),
    (0x22272c0, 11, '374b1a56c3f61f19b3e34b32787f41fc8e2f272800888e63bd8dbc544733db23'),
)


@dataclass(frozen=True)
class DraftContextSnapshot:
    identity: tuple
    after_filetime: int
    revision: int


class _Rejected(Exception):
    def __init__(self, reason):
        self.reason = reason


def _pointer(value):
    return type(value) is int and 0x10000 <= value < 0x7fffffff0000 and value % 8 == 0


def _hash_name(value):
    result = 0x811c9dc5
    for byte in value:
        result = ((result ^ byte) * 0x1000193) & 0xffffffff
    return result | 0x80000000


class LobbyDraftContext:
    def __init__(self, memory, image_base, *, capture_after_filetime=0, wall_clock=time.time,
                 code_fingerprints=VERIFIED_CODE_FINGERPRINTS):
        if not _pointer(image_base):raise ValueError('Invalid game image base')
        if type(capture_after_filetime) is not int or not 0 <= capture_after_filetime < 2**63:
            raise ValueError('Invalid capture boundary')
        specs = tuple(code_fingerprints)
        if (not specs or len(specs) > 32 or sum(spec[1] for spec in specs) > 8192
                or any(type(rva) is not int or rva < 0 or type(size) is not int
                       or not 0 < size <= 4096 or not isinstance(digest, str)
                       or re.fullmatch('[0-9a-f]{64}', digest) is None for rva, size, digest in specs)):
            raise ValueError('Invalid verified code profile')
        self.memory, self.image_base = memory, image_base
        self.capture_after_filetime, self.wall_clock = capture_after_filetime, wall_clock
        self._fingerprints = specs
        self._code_verified = None
        self._code_failure = ''
        self._current = None
        self._revision = 0
        self._operation_bytes = 0
        self._counts = dict(native_draft_context_checks=0, native_draft_context_changes=0,
                            native_draft_context_rejected=0, read_calls=0, read_bytes=0)
        self.last_reason = 'not_observed'

    @property
    def current(self):return self._current

    @property
    def snapshot(self):return self._current

    @property
    def code_verified(self):return self._code_verified is True

    def diagnostic_snapshot(self):return dict(self._counts)

    def _read(self, memory, address, count):
        if (type(address) is not int or not 0x10000 <= address < 0x7fffffff0000
                or not 0 < count <= 4096 or address + count >= 0x7fffffff0000):
            raise _Rejected('draft_invalid_address')
        if self._operation_bytes + count > _MAX_OPERATION_BYTES:
            raise _Rejected('draft_read_budget')
        self._operation_bytes += count
        own = memory is self.memory
        if own:self._counts['read_calls'] += 1
        try:data = memory.read(address, count)
        except Exception as error:
            raise _Rejected('draft_read_unavailable') from error
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise _Rejected('draft_read_unavailable')
        if own:self._counts['read_bytes'] += len(data)
        if len(data) != count:raise _Rejected('draft_read_unavailable')
        return data

    def _u64(self, memory, address):return struct.unpack('<Q', self._read(memory, address, 8))[0]

    def _ptr(self, memory, address, *, nullable=False):
        value = self._u64(memory, address)
        if nullable and value == 0:return 0
        if not _pointer(value):raise _Rejected('draft_invalid_pointer')
        return value

    def _bindings(self, memory):
        base = self.image_base
        expected = ((LOBBY_CONVERSATION_VTABLE_RVA, 0x1db7030),
                    (LOBBY_CONVERSATION_VTABLE_RVA + 8, 0x1dba2e0),
                    (LOBBY_CONVERSATION_VTABLE_RVA + 24, 0x1e40050),
                    (LOBBY_CONVERSATION_VTABLE_RVA + 40, 0x1e40a70),
                    (_LOBBY_VTABLE + 0x38, 0x1e0fb70))
        for slot, method in expected:
            if self._u64(memory, base + slot) != base + method:
                raise _Rejected('draft_binding_mismatch')
        for rva, value in ((0x28e9610, b'CChatLobbyChat\0'), (0x26faad8, b'BattleChatLobby\0'),
                           (0x28f0600, b'UI/BattleChat/LobbyDraftName\0')):
            if self._read(memory, base + rva, len(value)) != value:
                raise _Rejected('draft_name_mismatch')
        for rva, values in ((0x26ae6c0, _CATEGORY_VALUES), (0x26ae6f0, _MODE_VALUES)):
            if self._read(memory, base + rva, 12) != struct.pack('<3I', *values):
                raise _Rejected('draft_enum_mismatch')

    def _verify_code(self, memory):
        if self._code_verified is True:return
        try:
            path = getattr(self.memory, 'path', '')
            build = re.search(r'Base(\d+)', str(path), re.IGNORECASE)
            if build and int(build[1]) != PROFILE_BUILD:raise _Rejected('draft_build_unsupported')
            if self._read(memory, self.image_base, 2) != b'MZ':raise _Rejected('draft_image_mismatch')
            for rva, length, expected in self._fingerprints:
                if hashlib.sha256(self._read(memory, self.image_base + rva, length)).hexdigest() != expected:
                    raise _Rejected('draft_code_mismatch')
            self._bindings(memory)
        except _Rejected as error:
            self._code_verified = False
            self._code_failure = error.reason
            raise
        self._code_verified = True

    def _registry(self, memory):
        base = self.image_base
        api = self._ptr(memory, base + _API_GLOBAL)
        table = self._ptr(memory, api + 0xff0)
        capacity = self._u64(memory, api + 0x1000)
        if not 0 < capacity <= 16384 or capacity & (capacity - 1):
            raise _Rejected('draft_registry_invalid')
        expected_hash = _hash_name(_API_NAME)
        slot = expected_hash & (capacity - 1)
        for _ in range(min(capacity, 128)):
            address = table + slot * 40
            entry = self._read(memory, address, 40)
            stored_hash = struct.unpack_from('<I', entry)[0]
            if stored_hash == 0:break
            if stored_hash == expected_hash:
                name, callback, context = struct.unpack_from('<3Q', entry, 16)
                if not _pointer(name):raise _Rejected('draft_registry_invalid')
                if self._read(memory, name, len(_API_NAME) + 1) == _API_NAME + b'\0':
                    if callback != base + _CALLBACK or not _pointer(context):
                        raise _Rejected('draft_callback_mismatch')
                    if self._u64(memory, context) != base + _CONTEXT_VTABLE:
                        raise _Rejected('draft_context_class_mismatch')
                    return api, table, capacity, address, name, callback, context
            slot = (slot + 1) & (capacity - 1)
        raise _Rejected('draft_registry_missing')

    def _state(self, memory):
        self._bindings(memory)
        registry = self._registry(memory)
        context = registry[-1]
        backend = self._ptr(memory, context + 8)
        session = self._ptr(memory, backend + 0xdf0, nullable=True)
        if not session:raise _Rejected('draft_not_in_lobby')
        state = struct.unpack('<I', self._read(memory, session + 0x88, 4))[0]
        if state > 6:raise _Rejected('draft_not_in_lobby')
        manager = self._ptr(memory, self.image_base + _MANAGER_GLOBAL)
        lobby = self._ptr(memory, manager + 0x60)
        if self._u64(memory, lobby) != self.image_base + _LOBBY_VTABLE:
            raise _Rejected('draft_lobby_class_mismatch')
        header = self._read(memory, lobby + 0x1110, 32)
        count = struct.unpack_from('<I', header, 8)[0]
        array = struct.unpack_from('<Q', header, 24)[0]
        if not 0 < count <= _MAX_ATTRIBUTES or not _pointer(array):
            raise _Rejected('draft_attributes_invalid')
        payload = b''.join(self._read(memory, array + offset, min(4096, count * 16 - offset))
                           for offset in range(0, count * 16, 4096))
        relevant = {}
        for offset in range(0, len(payload), 16):
            namespace, identifier, raw = struct.unpack_from('<3I', payload, offset)
            if namespace == 999 and identifier in (4015, 4010):
                if identifier in relevant:raise _Rejected('draft_attribute_ambiguous')
                relevant[identifier] = raw
        mode, category = relevant.get(4015), relevant.get(4010)
        if mode not in _MODE_VALUES:raise _Rejected('draft_mode_unknown')
        if mode != _DRAFT:raise _Rejected('draft_not_selected')
        if category not in _CATEGORY_VALUES:raise _Rejected('draft_category_unknown')
        if category == _TOURNAMENT:raise _Rejected('draft_all_channel_rejected')
        # Registry/attribute reallocations are physical fences, not a new room.
        # Only verified owners and naming fields define the logical epoch.
        identity = (lobby, backend, session, mode, category)
        fence = (*registry, backend, session, state, manager, lobby, array, count, header, payload)
        return identity, fence

    def _stable_identity(self, memory):
        self._verify_code(memory)
        first, first_fence = self._state(memory)
        second, second_fence = self._state(memory)
        if first_fence != second_fence:raise _Rejected('draft_context_changed_during_read')
        return second

    def _check(self, memory):
        self._counts['native_draft_context_checks'] += 1
        self._operation_bytes = 0
        try:
            identity = self._stable_identity(memory)
            self.last_reason = 'draft_context_verified'
            return identity
        except _Rejected as error:
            self.last_reason = error.reason
            self._counts['native_draft_context_rejected'] += 1
            return None

    def observe(self):
        identity = self._check(self.memory)
        if identity is None:
            if self._current is not None:
                self._counts['native_draft_context_changes'] += 1
                self._revision += 1
            self._current = None
            return None
        if self._current is None or self._current.identity != identity:
            try:
                seconds = float(self.wall_clock())
                if not math.isfinite(seconds):raise ValueError
                boundary = 116444736000000000 + int(seconds * 10000000)
                if not 0 <= boundary < 2**63:raise ValueError
            except Exception:
                self.last_reason = 'draft_clock_invalid'
                self._counts['native_draft_context_rejected'] += 1
                if self._current is not None:
                    self._counts['native_draft_context_changes'] += 1
                    self._revision += 1
                self._current = None
                return None
            self._revision += 1
            self._counts['native_draft_context_changes'] += 1
            self._current = DraftContextSnapshot(identity,
                max(self.capture_after_filetime, boundary), self._revision)
        return self._current

    def verify(self, snapshot, memory=None):
        if (not isinstance(snapshot, DraftContextSnapshot) or self._current is None
                or snapshot != self._current):
            self.last_reason = 'draft_snapshot_stale'
            self._counts['native_draft_context_checks'] += 1
            self._counts['native_draft_context_rejected'] += 1
            return False
        identity = self._check(self.memory if memory is None else memory)
        if identity is None:return False
        if identity != snapshot.identity:
            self.last_reason = 'draft_snapshot_changed'
            self._counts['native_draft_context_rejected'] += 1
            return False
        return True
