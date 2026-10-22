"""Build 98348 的大厅身份缓存读取；不调用游戏或 Battle.net 函数。"""
import struct

from .history import HistoryInvalid


PARTY_VTABLE_RVA = 0x28e9720
WHISPER_VTABLE_RVA = 0x28f01c0
USER_VTABLE_RVA = 0x28e8c20
USERS_VTABLE_RVA = 0x28eacb8
MANAGER_GLOBAL_RVA = 0x3b7c6b0
API_GLOBAL_RVA = 0x3b7c6c0


class LobbyPropertyMissing(HistoryInvalid):
    """An absent property may have a verified same-account alternative."""


class LobbyMetadata:
    """Resolve only the two observed native conversation classes.

    User objects contain a presence handle, not a display-name NString. Names
    come from the native presence cache, with the game's RID/BattleTag choice.
    Every call uses fresh pointers; the owning reader accounts for read budgets.
    """

    def __init__(self, memory, image_base):
        self.memory = memory
        self.image_base = image_base
        self._poll_cache = None

    def begin_poll(self):
        """Start a fresh bounded snapshot; identities never carry across polls."""
        self._poll_cache = {}

    def _cached(self, key, read_value):
        if self._poll_cache is None:
            return read_value()
        if key not in self._poll_cache:
            self._poll_cache[key] = read_value()
        return self._poll_cache[key]

    def _read(self, address, count):
        if not 0x10000 <= address < 0x7fffffff0000 or not 0 < count <= 4096:
            raise HistoryInvalid('Invalid lobby metadata read')
        try:
            data = self.memory.read(address, count)
        except OSError as error:
            raise HistoryInvalid('Lobby metadata is no longer readable') from error
        if len(data) != count:
            raise HistoryInvalid('Lobby metadata is no longer readable')
        return data

    def _u32(self, address):
        return struct.unpack('<I', self._read(address, 4))[0]

    def _u64(self, address):
        return struct.unpack('<Q', self._read(address, 8))[0]

    def _pointer(self, address):
        pointer = self._u64(address)
        if not 0x10000 <= pointer < 0x7fffffff0000 or pointer % 8:
            raise HistoryInvalid('Invalid lobby metadata pointer')
        return pointer

    @staticmethod
    def _hash_bytes(payload):
        value = 0x811c9dc5
        for byte in payload:
            value = ((value ^ byte) * 0x1000193) & 0xffffffff
        return value | 0x80000000

    def _hash_key(self, key):
        return self._hash_bytes(struct.pack('<I', key))

    def _backend(self):
        return self._cached('backend', self._read_backend)

    def _read_backend(self):
        api = self._pointer(self.image_base + API_GLOBAL_RVA)
        table = self._pointer(api + 0xff0)
        capacity = self._u64(api + 0x1000)
        if not 0 < capacity <= 16384 or capacity & (capacity - 1):
            raise HistoryInvalid('Invalid lobby API table capacity')
        key = b'BattlenetAPI_GetDisplayNameForPresenceId'
        expected_hash = self._hash_bytes(key)
        slot = expected_hash & (capacity - 1)
        for _ in range(min(capacity, 128)):
            entry = self._read(table + slot * 40, 40)
            stored_hash = struct.unpack_from('<I', entry)[0]
            if stored_hash == 0:
                break
            if stored_hash == expected_hash:
                name_pointer = struct.unpack_from('<Q', entry, 0x10)[0]
                if self._read(name_pointer, len(key) + 1) == key + b'\0':
                    callback = struct.unpack_from('<Q', entry, 0x18)[0]
                    if callback != self.image_base + 0x23a90f0:
                        raise HistoryInvalid('Unsupported lobby name API registration')
                    return self._pointer(table + slot * 40 + 0x20)
            slot = (slot + 1) & (capacity - 1)
        raise HistoryInvalid('Lobby presence API context is unavailable')

    def _lookup(self, table_header, key, stride):
        table = self._pointer(table_header)
        capacity = self._u64(table_header + 0x10)
        if not 0 < capacity <= 65536 or capacity & (capacity - 1):
            raise HistoryInvalid('Invalid lobby cache capacity')
        expected_hash = self._hash_key(key)
        slot = expected_hash & (capacity - 1)
        for _ in range(min(capacity, 128)):
            entry = self._read(table + slot * stride, stride)
            stored_hash, stored_key = struct.unpack_from('<II', entry)
            if stored_hash == 0:
                return None
            if stored_hash == expected_hash and stored_key == key:
                pointer = struct.unpack_from('<Q', entry, 8)[0]
                if not 0x10000 <= pointer < 0x7fffffff0000 or pointer % 8:
                    raise HistoryInvalid('Invalid lobby cache value')
                return pointer
            slot = (slot + 1) & (capacity - 1)
        raise HistoryInvalid('Lobby cache probe limit exceeded')

    def _presence(self, backend, key):
        return self._cached(('presence', backend, key), lambda: self._read_presence(backend, key))

    def _read_presence(self, backend, key):
        if key == 0:
            return None
        pointer = self._lookup(backend + 0x68, key, 24)
        if pointer is not None and self._u32(pointer + 4) != key:
            raise HistoryInvalid('Lobby presence identity changed')
        return pointer

    def _property(self, presence, key, expected_type):
        variant = self._lookup(presence + 0x10, key, 16)
        if variant is None:
            raise LobbyPropertyMissing('Lobby presence property is unavailable')
        if self._u32(variant) != expected_type:
            raise HistoryInvalid('Unsupported lobby presence property')
        return self._pointer(variant + 8)

    def _account(self, presence):
        return self._cached(('account', presence), lambda: self._read_account(presence))

    def _read_account(self, presence):
        visited = set()
        for _ in range(8):
            if presence in visited:
                raise HistoryInvalid('Lobby presence parent cycle')
            visited.add(presence)
            kind_pointer = self._property(presence, 0x10001, 1)
            kind = self._read(kind_pointer, 1)[0]
            if kind == 3:
                return presence, self._u32(presence + 4)
            if kind not in (1, 2):
                raise HistoryInvalid('Unsupported lobby presence kind')
            presence = self._pointer(presence + 0x30)
        raise HistoryInvalid('Lobby presence ancestry limit exceeded')

    def _rid_accounts(self, backend):
        return self._cached(('rid', backend), lambda: self._read_rid_accounts(backend))

    def _read_rid_accounts(self, backend):
        core = self._pointer(backend + 8)
        relationship = self._pointer(core + 0x6300)
        count = self._u64(relationship + 0x20)
        if count > 16384:
            raise HistoryInvalid('Invalid lobby friendship count')
        if count == 0:
            return set()
        array = self._pointer(relationship + 0x18)
        accounts = set()
        for offset in range(0, count, 1024):
            chunk_count = min(1024, count - offset)
            payload = self._read(array + offset * 4, chunk_count * 4)
            accounts.update(struct.unpack('<' + 'I' * chunk_count, payload))
        return accounts

    def _string(self, pointer):
        length, capacity = struct.unpack('<QQ', self._read(pointer, 16))
        if not 0 < length <= 256 or not length <= capacity <= 4096:
            raise HistoryInvalid('Invalid lobby display-name length')
        raw = self._read(pointer + 0x10, length + 1)
        if raw[-1] != 0 or b'\0' in raw[:-1]:
            raise HistoryInvalid('Incomplete lobby display name')
        try:
            value = raw[:-1].decode('utf8', errors='strict')
        except UnicodeDecodeError as error:
            raise HistoryInvalid('Invalid lobby display-name UTF-8') from error
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise HistoryInvalid('Invalid lobby display name')
        return value

    def _name(self, presence, rid_accounts):
        return self._cached(('name', presence), lambda: self._read_name(presence, rid_accounts))

    def _read_name(self, presence, rid_accounts):
        _, account_id = self._account(presence)
        if account_id in rid_accounts:
            return self._string(self._property(presence, 0x1001c, 0xd))
        tag = self._string(self._property(presence, 0x1001e, 0x19))
        name = tag.split('#', 1)[0]
        if not name.strip():
            raise HistoryInvalid('Empty lobby BattleTag name')
        return name

    def _party_name(self, backend, sender):
        return self._cached(('party_name', backend, sender),
                            lambda: self._read_party_name(backend, sender))

    def _read_party_name(self, backend, sender):
        # 先独立验证当前发送者链；缺失/损坏的身份不能用名字回退掩盖。
        account, _ = self._account(sender)
        try:
            rid_accounts = self._rid_accounts(backend)
        except HistoryInvalid:
            # 无法判定实名好友关系时，只使用当前已验证账号的公开名字。
            return self._read_name(account, set())
        for presence in dict.fromkeys((sender, account)):
            try:
                return self._name(presence, rid_accounts)
            except LobbyPropertyMissing:
                pass
        # 仅缺失属性允许公开名字回退；类型错误或损坏字符串继续失败。
        # 不写入 _name 缓存，避免改变同一轮密语的严格 RID 选择。
        return self._read_name(account, set())

    def _user_handle(self, pointer):
        if self._u64(pointer) != self.image_base + USER_VTABLE_RVA:
            raise HistoryInvalid('Unsupported lobby user object')
        handle = self._u32(pointer + 8)
        if not handle:
            raise HistoryInvalid('Empty lobby user handle')
        return handle

    def _own_handle(self):
        return self._cached('own_handle', self._read_own_handle)

    def _read_own_handle(self):
        manager = self._pointer(self.image_base + MANAGER_GLOBAL_RVA)
        users = self._pointer(manager + 0xb8)
        if self._u64(users) != self.image_base + USERS_VTABLE_RVA:
            raise HistoryInvalid('Unsupported lobby user directory')
        return self._user_handle(self._pointer(users + 8))

    def _whisper_contact(self, conversation_ptr, backend):
        contact_handle = self._u32(conversation_ptr + 0x34)
        contact = self._presence(backend, contact_handle)
        if contact is None:
            contact = self._presence(backend, self._u32(conversation_ptr + 0x38))
        if contact is None:
            raise HistoryInvalid('Whisper contact presence is unavailable')
        return self._account(contact)

    def resolve(self, conversation_ptr, sender_ptr):
        vtable = self._u64(conversation_ptr)
        if vtable not in (self.image_base + PARTY_VTABLE_RVA,
                          self.image_base + WHISPER_VTABLE_RVA):
            return None
        sender_handle = self._user_handle(sender_ptr)
        if (vtable == self.image_base + WHISPER_VTABLE_RVA
                and sender_handle == self._own_handle()):
            # 用户对象与本机用户目录已独立证明是本人发送；名字缓存失效
            # 只能使联系人未知，不能丢掉已验证的方向或要求本人presence仍在。
            try:
                backend = self._backend()
                rid_accounts = self._rid_accounts(backend)
                contact, _ = self._whisper_contact(conversation_ptr, backend)
                name = self._name(contact, rid_accounts)
            except HistoryInvalid:
                name = '联系人未知'
            return '密语', '我 → ' + name
        backend = self._backend()
        sender = self._presence(backend, sender_handle)
        if sender is None:
            raise HistoryInvalid('Lobby sender presence is unavailable')
        if vtable == self.image_base + PARTY_VTABLE_RVA:
            return '队伍', self._party_name(backend, sender)
        rid_accounts = self._rid_accounts(backend)
        contact, contact_account = self._whisper_contact(conversation_ptr, backend)
        if self._account(sender)[1] != contact_account:
            raise HistoryInvalid('Whisper sender and contact do not match')
        name = self._name(contact, rid_accounts)
        return '密语', name
