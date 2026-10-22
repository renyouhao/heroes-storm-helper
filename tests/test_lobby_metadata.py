"""纯合成 presence/属性缓存，验证大厅身份解析；不含实机身份数据。"""
import struct
import unittest

try:
    from hots_chat.acquisition.lobby_metadata import LobbyMetadata
except ModuleNotFoundError:
    LobbyMetadata = None
from hots_chat.acquisition.history import HistoryInvalid


class MetadataMemory:
    base = 0x100000000
    hashes = {101: 0xb530d7a0, 102: 0xa5b594d3, 103: 0xd58955c2,
              201: 0xdb9c61cc, 202: 0xcc211eff, 203: 0xfbf4dfee,
              0x10001: 0xa16c306d, 0x1001c: 0xe9086800, 0x1001e: 0x8960e622}

    def __init__(self):
        self.blocks = {}; self.next = 0x100000; self.calls = []; self.values = {}
        self.manager = self.allocate(0xc0); self.users = self.allocate(0x80)
        self.backend = self.allocate(0x80); self.core = self.allocate(0x6400)
        self.rid = self.allocate(0x30); rid_ids = self.allocate(4)
        self.put(rid_ids, '<I', 203)
        self.put(self.rid + 0x18, '<QQ', rid_ids, 1)
        self.put(self.backend + 8, '<Q', self.core)
        self.put(self.core + 0x6300, '<Q', self.rid)
        self.put(self.base + 0x3941a08, '<Q', self.backend)
        self.api = self.allocate(0x1010); api_table = self.allocate(16 * 40)
        self.api_table = api_table
        api_key = b'BattlenetAPI_GetDisplayNameForPresenceId\0'
        api_name = self.allocate(len(api_key)); self.blocks[api_name][:] = api_key
        self.put(self.base + 0x3b7c6c0, '<Q', self.api)
        self.put(self.api + 0xff0, '<Q', api_table)
        self.put(self.api + 0x1000, '<Q', 16)
        self.put(api_table + 9 * 40, '<IIQQQQ', 0xa434e9f9, 0, 0, api_name,
                 self.base + 0x23a90f0, self.backend)
        self.put(self.base + 0x3b7c6b0, '<Q', self.manager)
        self.put(self.manager + 0xb8, '<Q', self.users)
        self.self_sender = self.user(101); self.contact_sender = self.user(201)
        self.put(self.users, '<QQ', self.base + 0x28eacb8, self.self_sender)
        self.party = self.allocate(0x38); self.whisper = self.allocate(0x40)
        self.put(self.party, '<Q', self.base + 0x28e9720)
        self.put(self.whisper, '<Q', self.base + 0x28f01c0)
        self.put(self.whisper + 0x34, '<II', 201, 203)
        self.presences = {}
        for key in (101, 102, 103, 201, 202, 203):
            self.presences[key] = self.allocate(0x50)
        table = self.allocate(16 * 24)
        self.put(self.backend + 0x68, '<Q', table)
        self.put(self.backend + 0x78, '<Q', 16)
        for key, pointer in self.presences.items():
            self.put(table + (self.hashes[key] & 15) * 24, '<IIQQ',
                     self.hashes[key], key, pointer, 0)
            self.put(pointer + 4, '<I', key)
            kind = 1 if key in (101, 201) else 2 if key in (102, 202) else 3
            if kind != 3:
                self.put(pointer + 0x30, '<Q', self.presences[key + 1])
            props = self.allocate(16 * 16)
            self.put(pointer + 0x10, '<Q', props); self.put(pointer + 0x20, '<Q', 16)
            self.property(key, props, 0x10001, 1, bytes([kind]))
            own = key < 200
            self.property(key, props, 0x1001c, 0xd, self.string('本人实名' if own else '好友显示'))
            self.property(key, props, 0x1001e, 0x19, self.string('玩家甲#12345' if own else '好友战网名#9999'))

    def allocate(self, count):
        pointer = self.next; self.next += ((count + 0xfff) // 0x1000) * 0x1000
        self.blocks[pointer] = bytearray(count); return pointer

    def put(self, pointer, fmt, *values):
        payload = struct.pack(fmt, *values)
        for base, data in self.blocks.items():
            if base <= pointer and pointer + len(payload) <= base + len(data):
                data[pointer - base:pointer - base + len(payload)] = payload; return
        self.blocks[pointer] = bytearray(payload)

    def user(self, key):
        pointer = self.allocate(0x138)
        self.put(pointer, '<QI', self.base + 0x28e8c20, key); return pointer

    @staticmethod
    def string(value):
        raw = value.encode('utf8')
        return struct.pack('<QQ', len(raw), len(raw)) + raw + b'\0'

    def property(self, key, table, prop, kind, payload):
        pointer = self.allocate(len(payload)); self.blocks[pointer][:] = payload
        variant = self.allocate(16); self.put(variant, '<IIQ', kind, 0, pointer)
        self.put(table + (self.hashes[prop] & 15) * 16, '<IIQ', self.hashes[prop], prop, variant)
        self.values[key, prop] = (variant, pointer)

    def read(self, pointer, count):
        if count > 4096:
            raise HistoryInvalid('The owning history reader limits individual reads to 4096 bytes')
        self.calls.append((pointer, count))
        for base, data in self.blocks.items():
            if base <= pointer < base + len(data):
                return bytes(data[pointer - base:pointer - base + count])
        return b''


class LobbyMetadataTests(unittest.TestCase):
    def setUp(self):
        self.memory = MetadataMemory()

    def make(self):
        self.assertIsNotNone(LobbyMetadata, '大厅原生元数据解析尚未实现')
        return LobbyMetadata(self.memory, self.memory.base)

    def test_party_uses_battletag_without_discriminator_for_non_rid_sender(self):
        self.assertEqual(self.make().resolve(self.memory.party, self.memory.self_sender), ('队伍', '玩家甲'))

    def test_party_uses_display_name_only_for_rid_account_ancestor(self):
        self.assertEqual(self.make().resolve(self.memory.party, self.memory.contact_sender), ('队伍', '好友显示'))

    def test_outgoing_whisper_uses_contact_instead_of_self_name(self):
        self.assertEqual(self.make().resolve(self.memory.whisper, self.memory.self_sender), ('密语', '我 → 好友显示'))

    def test_incoming_whisper_has_no_outgoing_prefix(self):
        self.assertEqual(self.make().resolve(self.memory.whisper, self.memory.contact_sender), ('密语', '好友显示'))

    def test_unknown_conversation_is_ignored_without_inspecting_sender(self):
        unknown = self.memory.allocate(8); self.memory.put(unknown, '<Q', self.memory.base + 0x12340)
        self.assertIsNone(self.make().resolve(unknown, 0))

    def test_missing_presence_fails_instead_of_fabricating_a_sender(self):
        self.memory.put(self.memory.self_sender + 8, '<I', 404)
        with self.assertRaises(HistoryInvalid): self.make().resolve(self.memory.party, self.memory.self_sender)

    def test_parent_cycle_is_rejected(self):
        self.memory.put(self.memory.presences[102] + 0x30, '<Q', self.memory.presences[101])
        with self.assertRaises(HistoryInvalid): self.make().resolve(self.memory.party, self.memory.self_sender)

    def test_malformed_name_variant_is_rejected(self):
        variant, _ = self.memory.values[101, 0x1001e]
        self.memory.put(variant, '<I', 0xd)
        with self.assertRaises(HistoryInvalid): self.make().resolve(self.memory.party, self.memory.self_sender)

    def test_invalid_capacity_is_rejected_before_table_allocation(self):
        self.memory.put(self.memory.backend + 0x78, '<Q', 1 << 40)
        with self.assertRaises(HistoryInvalid): self.make().resolve(self.memory.party, self.memory.self_sender)

    def test_name_cache_is_not_reused_after_underlying_name_changes(self):
        metadata = self.make()
        self.assertEqual(metadata.resolve(self.memory.party, self.memory.self_sender), ('队伍', '玩家甲'))
        _, pointer = self.memory.values[101, 0x1001e]
        self.memory.blocks[pointer] = bytearray(self.memory.string('改名后#12345'))
        self.assertEqual(metadata.resolve(self.memory.party, self.memory.self_sender), ('队伍', '改名后'))

    def test_partial_native_read_raises_history_invalid(self):
        self.memory.blocks[self.memory.manager] = bytearray(8)
        with self.assertRaises(HistoryInvalid): self.make().resolve(self.memory.whisper, self.memory.self_sender)

    def test_resolves_registered_api_context_when_temporary_global_is_empty(self):
        self.memory.put(self.memory.base + 0x3941a08, '<Q', 0)
        self.assertEqual(self.make().resolve(self.memory.party, self.memory.self_sender), ('队伍', '玩家甲'))

    def test_missing_stable_api_root_is_rejected_despite_temporary_reference(self):
        self.memory.put(self.memory.base + 0x3b7c6c0, '<Q', 0)
        with self.assertRaises(HistoryInvalid): self.make().resolve(self.memory.party, self.memory.self_sender)

    def test_unexpected_api_callback_is_not_accepted_as_a_name_backend(self):
        self.memory.put(self.memory.api_table + 9 * 40 + 0x18, '<Q', self.memory.base + 0x12340)
        with self.assertRaises(HistoryInvalid): self.make().resolve(self.memory.party, self.memory.self_sender)

    def test_same_poll_reuses_names_and_global_tables_with_a_small_read_budget(self):
        metadata = self.make(); metadata.begin_poll()
        self.assertEqual(metadata.resolve(self.memory.party, self.memory.self_sender), ('队伍', '玩家甲'))
        self.memory.calls.clear()
        self.assertEqual(metadata.resolve(self.memory.party, self.memory.self_sender), ('队伍', '玩家甲'))
        self.assertLessEqual(sum(count for _, count in self.memory.calls), 64)

    def test_new_poll_refreshes_names_and_friendship_state(self):
        metadata = self.make(); metadata.begin_poll()
        self.assertEqual(metadata.resolve(self.memory.party, self.memory.self_sender), ('队伍', '玩家甲'))
        _, pointer = self.memory.values[101, 0x1001e]
        self.memory.blocks[pointer] = bytearray(self.memory.string('改名后#12345'))
        metadata.begin_poll()
        self.assertEqual(metadata.resolve(self.memory.party, self.memory.self_sender), ('队伍', '改名后'))
        self.memory.put(self.memory.rid + 0x20, '<Q', 0)
        metadata.begin_poll()
        self.assertEqual(metadata.resolve(self.memory.party, self.memory.contact_sender), ('队伍', '好友战网名'))

    def test_more_than_1024_rid_contacts_are_read_in_bounded_chunks(self):
        contacts = [203] + list(range(5000, 6024))
        array = self.memory.allocate(len(contacts) * 4)
        self.memory.put(array, '<' + 'I' * len(contacts), *contacts)
        self.memory.put(self.memory.rid + 0x18, '<QQ', array, len(contacts))
        metadata = self.make(); metadata.begin_poll()
        self.assertEqual(metadata.resolve(self.memory.party, self.memory.contact_sender), ('队伍', '好友显示'))


if __name__ == '__main__': unittest.main()
