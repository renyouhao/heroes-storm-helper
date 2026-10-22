"""本人密语身份独立于联系人名字缓存；只使用合成内存。"""
import struct
import unittest

from hots_chat.acquisition.history import HistoryInvalid
from hots_chat.acquisition.lobby_metadata import LobbyMetadata
from hots_chat.acquisition.lobby_native import LobbyReader
from tests import test_lobby_metadata as fixtures


class WhisperIdentityRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.memory = fixtures.MetadataMemory()
        self.metadata = LobbyMetadata(self.memory, self.memory.base)

    def outgoing(self):
        try:
            return self.metadata.resolve(self.memory.whisper, self.memory.self_sender)
        except HistoryInvalid as failure:
            self.fail('本人身份不应因联系人缓存不可用而丢失：' + str(failure))

    def remove_presence(self, key):
        table = struct.unpack('<Q', self.memory.read(self.memory.backend + 0x68, 8))[0]
        self.memory.put(table + (self.memory.hashes[key] & 15) * 24, '<IIQQ', 0, 0, 0, 0)

    def test_outgoing_missing_recipient_keeps_independently_verified_self_identity(self):
        self.memory.put(self.memory.whisper + 0x34, '<II', 404, 405)
        self.assertEqual(self.outgoing(), ('密语', '我 → 联系人未知'))

    def test_outgoing_invalid_name_property_keeps_self_identity_without_inventing_name(self):
        variant, _ = self.memory.values[203, 0x1001c]
        self.memory.put(variant, '<I', 1)
        self.assertEqual(self.outgoing(), ('密语', '我 → 联系人未知'))

    def test_outgoing_missing_registered_name_backend_keeps_self_identity(self):
        self.memory.put(self.memory.base + 0x3b7c6c0, '<Q', 0)
        self.assertEqual(self.outgoing(), ('密语', '我 → 联系人未知'))

    def test_outgoing_contact_name_read_failure_keeps_verified_self_identity(self):
        _, name_pointer = self.memory.values[203, 0x1001c]
        original = self.memory.read
        def read(address, count):
            if address == name_pointer:
                raise OSError('synthetic expired contact name')
            return original(address, count)
        self.memory.read = read
        self.assertEqual(self.outgoing(), ('密语', '我 → 联系人未知'))

    def test_outgoing_missing_own_presence_still_resolves_recipient(self):
        self.remove_presence(101)
        self.assertEqual(self.outgoing(), ('密语', '我 → 好友显示'))

    def test_outgoing_invalid_own_presence_ancestry_cannot_block_recipient_name(self):
        self.memory.put(self.memory.presences[101] + 0x30, '<Q', 0)
        self.assertEqual(self.outgoing(), ('密语', '我 → 好友显示'))

    def test_outgoing_unavailable_friendship_choice_does_not_fabricate_a_contact_name(self):
        self.memory.put(self.memory.rid + 0x20, '<Q', 16385)
        self.assertEqual(self.outgoing(), ('密语', '我 → 联系人未知'))

    def test_outgoing_fallback_recovers_contact_name_in_a_new_poll(self):
        variant, _ = self.memory.values[203, 0x1001c]
        self.memory.put(variant, '<I', 1)
        self.metadata.begin_poll()
        self.assertEqual(self.outgoing(), ('密语', '我 → 联系人未知'))
        self.memory.put(variant, '<I', 0xd)
        self.metadata.begin_poll()
        self.assertEqual(self.outgoing(), ('密语', '我 → 好友显示'))

    def test_outgoing_same_poll_still_reuses_name_tables_with_a_bounded_read_budget(self):
        self.metadata.begin_poll()
        self.assertEqual(self.outgoing(), ('密语', '我 → 好友显示'))
        self.memory.calls.clear()
        self.assertEqual(self.outgoing(), ('密语', '我 → 好友显示'))
        self.assertLessEqual(sum(count for _, count in self.memory.calls), 64)

    def test_invalid_sender_user_vtable_cannot_be_accepted_as_self(self):
        self.memory.put(self.memory.self_sender, '<Q', self.memory.base + 0x12340)
        with self.assertRaises(HistoryInvalid):
            self.metadata.resolve(self.memory.whisper, self.memory.self_sender)

    def test_unavailable_own_user_handle_cannot_be_accepted_as_self(self):
        self.memory.put(self.memory.users + 8, '<Q', 0)
        with self.assertRaises(HistoryInvalid):
            self.metadata.resolve(self.memory.whisper, self.memory.self_sender)

    def test_invalid_own_user_directory_cannot_be_accepted_as_self(self):
        self.memory.put(self.memory.users, '<Q', self.memory.base + 0x12340)
        with self.assertRaises(HistoryInvalid):
            self.metadata.resolve(self.memory.whisper, self.memory.self_sender)

    def test_incoming_missing_sender_presence_still_fails_without_self_prefix(self):
        self.remove_presence(201)
        with self.assertRaises(HistoryInvalid):
            self.metadata.resolve(self.memory.whisper, self.memory.contact_sender)

    def test_incoming_account_mismatch_still_fails_without_self_prefix(self):
        other_sender = self.memory.user(102)
        with self.assertRaises(HistoryInvalid):
            self.metadata.resolve(self.memory.whisper, other_sender)

    def test_incoming_invalid_contact_name_is_not_relabelled_as_self(self):
        variant, _ = self.memory.values[203, 0x1001c]
        self.memory.put(variant, '<I', 1)
        with self.assertRaises(HistoryInvalid):
            self.metadata.resolve(self.memory.whisper, self.memory.contact_sender)

    def test_normal_incoming_and_outgoing_keep_their_existing_names(self):
        self.assertEqual(self.outgoing(), ('密语', '我 → 好友显示'))
        self.assertEqual(self.metadata.resolve(self.memory.whisper, self.memory.contact_sender),
                         ('密语', '好友显示'))

    def test_unknown_conversation_still_returns_none_before_inspecting_sender(self):
        conversation = self.memory.allocate(8)
        self.memory.put(conversation, '<Q', self.memory.base + 0x12340)
        self.assertIsNone(self.metadata.resolve(conversation, 0))

    def test_unrelated_party_memory_failure_still_raises_history_invalid(self):
        original = self.memory.read
        def read(address, count):
            if address == self.memory.base + 0x3b7c6c0:
                raise OSError('synthetic unavailable API root')
            return original(address, count)
        self.memory.read = read
        with self.assertRaises(HistoryInvalid):
            self.metadata.resolve(self.memory.party, self.memory.self_sender)

    def test_reader_keeps_outgoing_body_when_contact_name_backend_is_missing(self):
        self.memory.put(self.memory.base + 0x3b7c6c0, '<Q', 0)
        record = self.memory.allocate(0x80)
        node = record + 8
        reader = LobbyReader(self.memory, node, self.memory.base)
        self.memory.put(record, '<3Q', reader.layout.message_table, node | 1, 2)
        self.memory.put(node + 16, '<2Q', self.memory.whisper, self.memory.self_sender)
        body = b'hello'
        self.memory.put(node + 32, '<2I8s', (len(body) << 2) | 1, 0, body.ljust(8, b'\0'))
        self.memory.put(node + 48, '<Q', 134053920000000000)
        message = reader.entry(node)['message']
        self.assertEqual((message.channel, message.sender, message.text),
                         ('密语', '我 → 联系人未知', 'hello'))


if __name__ == '__main__':
    unittest.main()
