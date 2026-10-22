"""队伍发送者恢复只使用当前已验证账号；全部数据为合成内存。"""
import struct
import unittest

from hots_chat.acquisition.history import HistoryInvalid
from hots_chat.acquisition.lobby_metadata import LobbyMetadata
from hots_chat.acquisition.lobby_native import LobbyReader
from tests.test_lobby_metadata import MetadataMemory


class PartyIdentityRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.memory = MetadataMemory()
        self.metadata = LobbyMetadata(self.memory, self.memory.base)

    def incoming(self):
        return self.metadata.resolve(self.memory.party, self.memory.contact_sender)

    def remove_property(self, presence_key, property_key):
        table = struct.unpack('<Q', self.memory.read(self.memory.presences[presence_key] + 0x10, 8))[0]
        self.memory.put(table + (self.memory.hashes[property_key] & 15) * 16, '<IIQ', 0, 0, 0)

    def remove_names(self, keys):
        for key in keys:
            for prop in (0x1001c, 0x1001e):
                self.remove_property(key, prop)

    def unreadable_friendship(self):
        original = self.memory.read
        def read(address, count):
            if address == self.memory.rid + 0x20:
                raise OSError('synthetic friendship unavailable')
            return original(address, count)
        self.memory.read = read

    def test_unreadable_friendship_uses_verified_account_public_name(self):
        self.unreadable_friendship()
        self.assertEqual(self.incoming(), ('队伍', '好友战网名'))

    def test_invalid_friendship_count_uses_public_name(self):
        self.memory.put(self.memory.rid + 0x20, '<Q', 16385)
        self.assertEqual(self.incoming(), ('队伍', '好友战网名'))

    def test_missing_child_names_use_account_rid_display_name(self):
        self.remove_names((201, 202))
        self.assertEqual(self.incoming(), ('队伍', '好友显示'))

    def test_missing_child_names_use_account_battletag_for_non_rid_sender(self):
        self.memory.put(self.memory.rid + 0x20, '<Q', 0)
        self.remove_names((201, 202))
        self.assertEqual(self.incoming(), ('队伍', '好友战网名'))

    def test_missing_rid_display_names_fall_back_to_same_account_public_name(self):
        for key in (201, 202, 203):
            self.remove_property(key, 0x1001c)
        self.assertEqual(self.incoming(), ('队伍', '好友战网名'))

    def test_unreadable_friendship_and_missing_child_names_still_use_account(self):
        self.unreadable_friendship()
        self.remove_names((201, 202))
        self.assertEqual(self.incoming(), ('队伍', '好友战网名'))

    def test_missing_all_names_still_fails(self):
        self.remove_names((201, 202, 203))
        with self.assertRaises(HistoryInvalid):
            self.incoming()

    def test_invalid_account_battletag_still_fails(self):
        self.unreadable_friendship()
        _, pointer = self.memory.values[203, 0x1001e]
        self.memory.blocks[pointer] = bytearray(self.memory.string('#12345'))
        with self.assertRaises(HistoryInvalid):
            self.incoming()

    def test_present_but_malformed_child_name_variant_is_not_replaced(self):
        variant, _ = self.memory.values[201, 0x1001c]
        self.memory.put(variant, '<I', 1)
        with self.assertRaises(HistoryInvalid):
            self.incoming()

    def test_present_but_invalid_child_name_string_is_not_replaced(self):
        _, pointer = self.memory.values[201, 0x1001c]
        self.memory.blocks[pointer] = bytearray(self.memory.string('bad\nname'))
        with self.assertRaises(HistoryInvalid):
            self.incoming()

    def test_missing_identity_kind_cannot_be_treated_as_a_missing_name(self):
        self.remove_property(201, 0x10001)
        with self.assertRaises(HistoryInvalid):
            self.incoming()

    def test_missing_sender_presence_cannot_borrow_another_name(self):
        self.memory.put(self.memory.contact_sender + 8, '<I', 404)
        with self.assertRaises(HistoryInvalid):
            self.incoming()

    def test_sender_presence_identity_mismatch_is_rejected(self):
        self.memory.put(self.memory.presences[201] + 4, '<I', 202)
        with self.assertRaises(HistoryInvalid):
            self.incoming()

    def test_invalid_sender_vtable_is_rejected(self):
        self.memory.put(self.memory.contact_sender, '<Q', self.memory.base + 0x12340)
        with self.assertRaises(HistoryInvalid):
            self.incoming()

    def test_empty_sender_handle_is_rejected(self):
        self.memory.put(self.memory.contact_sender + 8, '<I', 0)
        with self.assertRaises(HistoryInvalid):
            self.incoming()

    def test_parent_cycle_is_rejected_even_when_child_name_is_valid(self):
        self.memory.put(self.memory.presences[202] + 0x30, '<Q', self.memory.presences[201])
        with self.assertRaises(HistoryInvalid):
            self.incoming()

    def test_normal_rid_display_name_remains_preferred(self):
        self.assertEqual(self.incoming(), ('队伍', '好友显示'))

    def test_fallback_names_do_not_cross_senders(self):
        self.unreadable_friendship()
        self.metadata.begin_poll()
        self.assertEqual(self.incoming(), ('队伍', '好友战网名'))
        self.assertEqual(self.metadata.resolve(self.memory.party, self.memory.self_sender), ('队伍', '玩家甲'))

    def test_new_poll_refreshes_account_fallback_name(self):
        self.remove_names((201, 202))
        self.metadata.begin_poll()
        self.assertEqual(self.incoming(), ('队伍', '好友显示'))
        _, pointer = self.memory.values[203, 0x1001c]
        self.memory.blocks[pointer] = bytearray(self.memory.string('改名后'))
        self.metadata.begin_poll()
        self.assertEqual(self.incoming(), ('队伍', '改名后'))

    def test_same_poll_reuses_fallback_with_bounded_reads(self):
        self.unreadable_friendship()
        self.metadata.begin_poll()
        self.assertEqual(self.incoming(), ('队伍', '好友战网名'))
        self.memory.calls.clear()
        self.assertEqual(self.incoming(), ('队伍', '好友战网名'))
        self.assertLessEqual(sum(count for _, count in self.memory.calls), 64)

    def test_party_public_fallback_does_not_replace_whisper_name_cache(self):
        for key in (201, 202, 203):
            self.remove_property(key, 0x1001c)
        self.metadata.begin_poll()
        self.assertEqual(self.incoming(), ('队伍', '好友战网名'))
        with self.assertRaises(HistoryInvalid):
            self.metadata.resolve(self.memory.whisper, self.memory.contact_sender)

    def test_reader_preserves_body_with_unknown_sender_when_all_names_are_missing(self):
        self.remove_names((201, 202, 203))
        record = self.memory.allocate(0x80)
        node = record + 8
        reader = LobbyReader(self.memory, node, self.memory.base)
        self.memory.put(record, '<3Q', reader.layout.message_table, node | 1, 2)
        self.memory.put(node + 16, '<2Q', self.memory.party, self.memory.contact_sender)
        self.memory.put(node + 32, '<2I8s', 21, 0, b'hello\0\0\0')
        self.memory.put(node + 48, '<Q', 134053920000000000)
        message = reader.entry(node)['message']
        self.assertEqual((message.channel, message.sender, message.text), ('队伍', '发送者未知', 'hello'))


if __name__ == '__main__':
    unittest.main()
