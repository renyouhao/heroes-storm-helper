"""Native lobby history fixtures contain synthetic users and no live game data."""
import struct
import threading
import unittest

from hots_chat.acquisition.history import HistoryInvalid
from hots_chat.acquisition.lobby_native import LobbyReader, LobbyDiscovery, LobbyLayout


class LobbyMemory:
    base = 0x100000
    image_base = 0x400000000

    def __init__(self):
        self.data = bytearray(0x100000)
        self.calls = []
        self.layout = LobbyLayout(self.image_base)
        self.party = self.base + 0x20000
        self.whisper = self.base + 0x20100
        self.unsupported = self.base + 0x20200
        self.sender = self.base + 0x20300
        self.put(self.party, struct.pack('<Q', self.layout.party_table))
        self.put(self.whisper, struct.pack('<Q', self.layout.whisper_table))
        self.put(self.unsupported, struct.pack('<Q', self.image_base + 0x123456))
        self.links = []

    def put(self, address, data):
        start = address - self.base
        self.data[start:start + len(data)] = data

    def read(self, address, count):
        self.calls.append((address, count))
        start = address - self.base
        return bytes(self.data[start:start + count]) if 0 <= start and start + count <= len(self.data) else b''

    def regions(self):
        yield self.base, len(self.data), 4

    def records(self, entries):
        self.links = [self.base + 0x1000 + i * 0x80 + 8 for i in range(len(entries))]
        for index, entry in enumerate(entries):
            channel, body, *options = entry
            system = channel == 'system'
            data = body.encode('utf-8')
            link = self.links[index]
            next_link = self.links[index + 1] if index + 1 < len(entries) else self.links[0] | 1
            table = self.layout.system_table if system else self.layout.message_table
            self.put(link - 8, struct.pack('<3Q', table, next_link, 3 if system else 2))
            descriptor = link + 16 if system else link + 32
            body_pointer = self.base + 0x40000 + index * 4096
            if len(data) <= 7:
                encoded = struct.pack('<2I', (len(data) << 2) | 1, 0) + data.ljust(8, b'\0')
            else:
                encoded = struct.pack('<2IQ', (len(data) << 2) | 1, (len(data) << 2) | 3, body_pointer)
                self.put(body_pointer, data + b'\0')
            self.put(descriptor, encoded)
            if not system:
                conversation = {'队伍': self.party, '密语': self.whisper}.get(channel, self.unsupported)
                self.put(link + 16, struct.pack('<2Q', conversation, self.sender))
                self.put(link + 48, struct.pack('<Q', 134053920000000000 + index * 10000000))
        return self.links[0]


class FixtureMetadata:
    def __init__(self, memory, image_base):
        self.memory, self.layout = memory, LobbyLayout(image_base)

    def resolve(self, conversation, sender):
        table = struct.unpack('<Q', self.memory.read(conversation, 8))[0]
        if table == self.layout.party_table:
            return '队伍', '我'
        if table == self.layout.whisper_table:
            return '密语', '好友'
        return None


class LobbyNativeTests(unittest.TestCase):
    def reader(self, memory, root, **options):
        return LobbyReader(memory, root, memory.image_base, metadata_factory=FixtureMetadata, **options)

    def test_initial_connection_does_not_replay_lobby_history(self):
        memory = LobbyMemory(); root = memory.records([('队伍', 'before')])
        reader = self.reader(memory, root)
        self.assertEqual(reader.poll().messages, [])
        memory.records([('队伍', 'before'), ('密语', 'new')])
        self.assertEqual([(m.channel, m.text) for m in reader.poll().messages], [('密语', 'new')])

    def test_inline_external_and_literal_text_are_received_in_order(self):
        memory = LobbyMemory(); root = memory.records([('队伍', 'hi'), ('密语', '<literal> &amp; 안녕')])
        result = self.reader(memory, root, include_history=True).poll()
        self.assertEqual([(m.channel, m.text) for m in result.messages], [('队伍', 'hi'), ('密语', '<literal> &amp; 안녕')])
        self.assertTrue(all(m.time for m in result.messages))

    def test_identical_messages_at_distinct_records_are_not_collapsed(self):
        memory = LobbyMemory(); root = memory.records([('队伍', 'baseline')]); reader = self.reader(memory, root)
        reader.poll(); memory.records([('队伍', 'baseline'), ('密语', 'same'), ('密语', 'same')])
        self.assertEqual([m.text for m in reader.poll().messages], ['same', 'same'])
        self.assertEqual(reader.poll().messages, [])

    def test_system_and_unsupported_channel_do_not_enter_translation(self):
        memory = LobbyMemory(); root = memory.records([('system', '<s val="BattleChatSystem">notice</s>'), ('综合', 'not supported'), ('队伍', 'yes')])
        result = self.reader(memory, root, include_history=True).poll()
        self.assertEqual([m.text for m in result.messages], ['yes'])
        self.assertFalse(any(address == memory.base + 0x40000 for address, count in memory.calls))
        self.assertFalse(any(address == memory.base + 0x41000 for address, count in memory.calls))

    def test_unchanged_tail_uses_bounded_reads_without_scanning_regions(self):
        memory = LobbyMemory(); root = memory.records([('队伍', 'one'), ('密语', 'two')]); reader = self.reader(memory, root)
        reader.poll(); memory.calls.clear(); result = reader.poll()
        self.assertEqual(result.messages, [])
        self.assertLess(result.read_bytes, 2048)
        self.assertLess(len(memory.calls), 25)

    def test_reused_tail_does_not_replay_changed_history(self):
        memory = LobbyMemory(); root = memory.records([('队伍', 'old')]); reader = self.reader(memory, root)
        reader.poll(); memory.records([('队伍', 'reused')])
        result = reader.poll()
        self.assertTrue(result.gap)
        self.assertEqual(result.messages, [])
        memory.records([('队伍', 'reused'), ('密语', 'next')])
        self.assertEqual([m.text for m in reader.poll().messages], ['next'])

    def test_unreadable_history_and_cycle_are_rejected(self):
        memory = LobbyMemory(); root = memory.records([('队伍', 'hello')])
        memory.put(root, struct.pack('<Q', root))
        with self.assertRaises(HistoryInvalid):self.reader(memory, root).poll()
        with self.assertRaises(HistoryInvalid):self.reader(memory, 0x900000).poll()

    def test_budget_prevents_unbounded_history_reads(self):
        memory = LobbyMemory(); root = memory.records([('队伍', 'long enough body')])
        with self.assertRaises(HistoryInvalid):self.reader(memory, root, byte_budget=32).poll()

    def test_discovery_resumes_and_validates_the_native_ring(self):
        memory = LobbyMemory(); root = memory.records([('队伍', 'known')]); scanner = LobbyDiscovery(memory, threading.Event(), memory.image_base)
        found = None
        for _ in range(64):
            found = scanner.step(byte_budget=4096, time_budget=60)
            if found is not None:break
        self.assertIsNotNone(found)
        self.assertEqual(found.root, root)

    def test_discovery_does_not_accept_system_or_unknown_channel_only(self):
        for channel in ('system', '综合'):
            with self.subTest(channel=channel):
                memory = LobbyMemory(); memory.records([(channel, 'notice')]); scanner = LobbyDiscovery(memory, threading.Event(), memory.image_base)
                self.assertIsNone(scanner.step(byte_budget=len(memory.data), time_budget=60))
                self.assertTrue(scanner.finished)

    def test_discovery_cancellation_stops_before_any_read(self):
        from hots_chat.acquisition.discovery import DiscoveryCancelled
        memory = LobbyMemory(); cancel = threading.Event(); cancel.set()
        scanner = LobbyDiscovery(memory, cancel, memory.image_base)
        with self.assertRaises(DiscoveryCancelled):scanner.step()
        self.assertEqual(memory.calls, [])

    def test_expired_old_sender_does_not_block_connection_or_future_messages(self):
        class CurrentMetadata(FixtureMetadata):
            def resolve(self, conversation, sender):
                table = struct.unpack('<Q', self.memory.read(conversation, 8))[0]
                if table == self.layout.party_table:
                    raise HistoryInvalid('historical sender is no longer in the current presence cache')
                return super().resolve(conversation, sender)
        memory = LobbyMemory(); root = memory.records([('队伍', 'old sender')])
        reader = LobbyReader(memory, root, memory.image_base, metadata_factory=CurrentMetadata)
        self.assertEqual(reader.poll().messages, [])
        self.assertEqual(reader.poll().messages, [])
        memory.records([('队伍', 'old sender'), ('密语', 'new')])
        self.assertEqual([m.text for m in reader.poll().messages], ['new'])

    def test_unresolved_new_sender_does_not_block_later_valid_messages(self):
        class CurrentMetadata(FixtureMetadata):
            def resolve(self, conversation, sender):
                table = struct.unpack('<Q', self.memory.read(conversation, 8))[0]
                if table == self.layout.party_table:raise HistoryInvalid('expired sender')
                return super().resolve(conversation, sender)
        memory = LobbyMemory(); root = memory.records([('密语', 'baseline')])
        reader = LobbyReader(memory, root, memory.image_base, metadata_factory=CurrentMetadata)
        reader.poll()
        memory.records([('密语', 'baseline'), ('队伍', 'name unavailable'), ('密语', 'still receive')])
        result = reader.poll()
        self.assertEqual([(m.sender, m.text) for m in result.messages],
                         [('发送者未知', 'name unavailable'), ('好友', 'still receive')])
        self.assertEqual(reader.poll().messages, [])

    def test_expired_old_conversation_does_not_block_baseline_or_new_messages(self):
        memory = LobbyMemory(); root = memory.records([('队伍', 'old')])
        invalid = 0x900000
        memory.put(root + 16, struct.pack('<Q', invalid))
        reader = self.reader(memory, root)
        self.assertEqual(reader.poll().messages, [])
        memory.records([('队伍', 'old'), ('密语', 'new')])
        memory.put(root + 16, struct.pack('<Q', invalid))
        self.assertEqual([m.text for m in reader.poll().messages], ['new'])

    def test_large_pending_burst_advances_in_bounded_batches_without_duplicates(self):
        memory = LobbyMemory(); baseline = [('队伍', 'old')]; root = memory.records(baseline)
        reader = self.reader(memory, root); reader.poll()
        expected = [f'{index:03d}:' + 'x' * 4092 for index in range(80)]
        memory.records(baseline + [('密语', text) for text in expected])
        received = []
        for _ in range(12):
            result = reader.poll()
            self.assertLessEqual(result.read_bytes, 262144)
            received.extend(m.text for m in result.messages)
            if len(received) == len(expected):break
        self.assertEqual(received, expected)
        self.assertEqual(reader.poll().messages, [])

    def test_long_history_baseline_resumes_across_poll_work_budgets(self):
        memory = LobbyMemory(); entries = [('队伍', 'old')] * 520; root = memory.records(entries)
        reader = self.reader(memory, root)
        for _ in range(8):
            result = reader.poll()
            self.assertEqual(result.messages, [])
            self.assertLessEqual(result.read_bytes, 262144)
            if not result.retry:break
        self.assertFalse(result.retry)
        memory.records(entries + [('密语', 'new')])
        self.assertEqual([m.text for m in reader.poll().messages], ['new'])

    def test_long_history_discovery_continues_instead_of_rejecting_valid_ring(self):
        memory = LobbyMemory(); root = memory.records([('队伍', 'old')] * 520)
        scanner = LobbyDiscovery(memory, threading.Event(), memory.image_base)
        found = None
        for _ in range(16):
            found = scanner.step(byte_budget=len(memory.data), time_budget=60)
            if found is not None:break
        self.assertIsNotNone(found)
        self.assertEqual(found.root, root)

    def test_cycle_crossing_incremental_batch_boundary_cannot_replay_messages(self):
        for count in (33, 65):
            with self.subTest(count=count):
                memory = LobbyMemory(); baseline = [('队伍', 'old')]
                root = memory.records(baseline); reader = self.reader(memory, root); reader.poll()
                memory.records(baseline + [('密语', f'new {i}') for i in range(count)])
                memory.put(memory.links[-1], struct.pack('<Q', memory.links[1]))
                received = []
                for _ in range(count // 32):received.extend(m.text for m in reader.poll().messages)
                self.assertEqual(len(received), len(set(received)))
                committed_cursor = reader.cursor
                for _ in range(3):
                    with self.assertRaises(HistoryInvalid):reader.poll()
                    self.assertEqual(reader.cursor, committed_cursor)
                # Restoring a proper tagged end allows the undelivered row to resume.
                memory.put(memory.links[-1], struct.pack('<Q', root | 1))
                self.assertEqual([m.text for m in reader.poll().messages], [f'new {count - 1}'])
                self.assertEqual(reader.poll().messages, [])

    def test_failed_batch_fence_does_not_consume_cursor_or_drain_nodes(self):
        memory = LobbyMemory(); baseline = [('队伍', 'old')]
        root = memory.records(baseline); reader = self.reader(memory, root); reader.poll()
        memory.records(baseline + [('密语', f'new {i}') for i in range(65)])
        self.assertEqual(len(reader.poll().messages), 32)
        committed_cursor = reader.cursor
        fence = reader._fence
        reader._fence = lambda first, last:False
        retry = reader.poll()
        self.assertTrue(retry.retry); self.assertEqual(retry.messages, [])
        self.assertEqual(reader.cursor, committed_cursor)
        reader._fence = fence
        received = []
        for _ in range(3):received.extend(m.text for m in reader.poll().messages)
        self.assertEqual(received, [f'new {i}' for i in range(32, 65)])


if __name__ == '__main__':unittest.main()
