import unittest
import struct
import threading
from unittest.mock import patch
from test_history import FakeMemory, chat_line
from test_party_whisper_acquisition import party_out

try:
    import hots_chat.acquisition.discovery as discovery
except ModuleNotFoundError:
    discovery=None


class DiscoveryTests(unittest.TestCase):
    def locate(self,memory,node):
        self.assertIsNotNone(discovery,'Automatic history discovery has not been implemented')
        return discovery.locate_root(memory,node)

    def test_finds_root_from_an_interior_message_without_fixed_addresses(self):
        memory=FakeMemory(); nodes=memory.set_nodes([chat_line('a'),chat_line('b'),chat_line('c')])
        self.assertEqual(self.locate(memory,nodes[1]),memory.root)

    def test_rejects_a_forged_sentinel_tail(self):
        self.assertIsNotNone(discovery,'Automatic history discovery has not been implemented')
        memory=FakeMemory(); nodes=memory.set_nodes([chat_line('a'),chat_line('b')])
        memory.blocks[memory.root]=struct.pack('<QQ',nodes[-1],nodes[-1])
        with self.assertRaises(Exception) as context: self.locate(memory,nodes[1])
        self.assertIsInstance(context.exception,discovery.HistoryInvalid)

    def test_rejects_a_plain_input_buffer(self):
        self.assertIsNotNone(discovery,'Automatic history discovery has not been implemented')
        memory=FakeMemory(); nodes=memory.set_nodes(['HOTS_CHAT_TEST_20261019_C9D5 buffer growth test'])
        with self.assertRaises(Exception) as context: self.locate(memory,nodes[0])
        self.assertIsInstance(context.exception,discovery.HistoryInvalid)

    def test_initial_scan_rechecks_same_root_after_transient_snapshot_change(self):
        memory=FakeMemory();memory.set_nodes([chat_line('first'),chat_line('second')])
        original_read=memory.read;root_reads=[0]
        def changing_read(address,count):
            data=original_read(address,count)
            if address==memory.root and count==16:
                root_reads[0]+=1
                if root_reads[0]==4:return struct.pack('<QQ',memory.root|1,memory.root|1)
            return data
        memory.read=changing_read
        result=discovery.discover(memory,threading.Event())
        self.assertEqual(result.root,memory.root)

    def test_initial_scan_rechecks_lobby_root_when_team_message_arrives(self):
        memory = FakeMemory()
        party, team = party_out('party baseline'), chat_line('first team message')
        node, = memory.set_nodes([party])

        def regions():
            yield node + 24, len(party.encode('utf-8')), 4
            memory.set_nodes([party, team])
            yield node + 0x10000 + 24, len(team.encode('utf-8')), 4

        memory.regions = regions
        result = discovery.discover(memory, threading.Event(), channels=('团队', '所有玩家'))
        self.assertEqual(result.root, memory.root)


class ScanMemory(FakeMemory):
    def __init__(self):
        super().__init__()
        self.scan_regions = []

    def regions(self):
        yield from self.scan_regions

    def expose_node(self, node):
        self.scan_regions = [(node + 24, len(self.blocks[node]) - 24, 4)]


class IncrementalDiscoveryTests(unittest.TestCase):
    def test_scan_diagnostics_distinguish_no_prefix_invalid_and_unsupported_chat(self):
        cases = ('no_prefix', 'invalid_candidate', 'unsupported_channel')
        for case in cases:
            with self.subTest(case=case):
                memory = ScanMemory()
                if case == 'no_prefix':
                    memory.blocks = {0x80000: b'private text without chat markup'}
                    memory.scan_regions = [(0x80000, len(memory.blocks[0x80000]), 4)]
                elif case == 'invalid_candidate':
                    memory.blocks = {0x80000: discovery.PREFIX + b'not a history node'}
                    memory.scan_regions = [(0x80000, len(memory.blocks[0x80000]), 4)]
                else:
                    node, = memory.set_nodes([party_out('private party body')])
                    memory.expose_node(node)
                scanner = self.make_scanner(memory, channels=('团队', '所有玩家'))
                self.assertIsNone(scanner.step(time_budget=60))
                self.assertTrue(scanner.finished)
                calls_before = list(memory.calls)
                stats = scanner.diagnostic_snapshot()
                self.assertEqual(memory.calls, calls_before, '诊断快照不得额外读取游戏内存')
                self.assertTrue(all(type(value) in (int, float)
                                    for key, value in stats.items() if key != 'last_rejection_reason'))
                self.assertIn(stats['last_rejection_reason'], ('', 'invalid_address', 'read_unavailable',
                    'invalid_links', 'invalid_layout', 'history_invalid'))
                self.assertEqual(stats['read_calls'], 1)
                self.assertGreater(stats['read_bytes'], 0)
                self.assertEqual(stats['candidate_count'], 0 if case == 'no_prefix' else 1)
                self.assertEqual(stats['invalid_candidate_count'], int(case == 'invalid_candidate'))
                self.assertEqual(stats['empty_snapshot_count'], int(case == 'unsupported_channel'))

    def test_previous_chat_region_is_checked_before_unrelated_regions(self):
        import inspect
        self.assertIn('preferred_addresses',inspect.signature(discovery.IncrementalDiscovery).parameters)
        memory=ScanMemory();node,=memory.set_nodes([chat_line('next match')])
        memory.scan_regions=[(0x80000,1024*1024,4),(node+24,len(memory.blocks[node])-24,4)]
        scanner=self.make_scanner(memory,preferred_addresses=(node+24,))
        result=scanner.step(byte_budget=256,time_budget=60)
        self.assertIsNotNone(result)
        self.assertEqual(result.root,memory.root)
        self.assertFalse(any(address==0x80000 for address,count in memory.calls))

    def test_many_nodes_of_ignored_root_are_not_revalidated_quadratically(self):
        memory=ScanMemory();nodes=memory.set_nodes([chat_line(str(i)) for i in range(64)])
        memory.scan_regions=[(n+24,len(memory.blocks[n])-24,4) for n in nodes]
        reader=discovery.HistoryReader(memory,memory.root,include_history=True);reader.poll()
        memory.calls.clear()
        scanner=self.make_scanner(memory,ignore_snapshots={memory.root:(reader.last_head,reader.last_stamp)})
        self.assertIsNone(scanner.step(time_budget=60))
        self.assertTrue(scanner.finished)
        self.assertLess(len(memory.calls),64*30,'同一条历史的结构校验应近似线性')

    def make_scanner(self, memory, cancel_event=None, **kwargs):
        self.assertTrue(hasattr(discovery, 'IncrementalDiscovery'),
                        'Incremental history discovery has not been implemented')
        return discovery.IncrementalDiscovery(memory, cancel_event or threading.Event(), **kwargs)

    def test_byte_budget_preserves_cursor_across_regions_without_rereading(self):
        memory = ScanMemory()
        memory.blocks = {0x80000: b'x' * 35, 0x90000: b'y' * 17}
        memory.scan_regions = [(0x80000, 35, 4), (0x90000, 17, 4)]
        scanner = self.make_scanner(memory)

        for _ in range(3):
            self.assertIsNone(scanner.step(byte_budget=16, time_budget=60))
            self.assertFalse(scanner.finished)
        self.assertIsNone(scanner.step(byte_budget=16, time_budget=60))

        self.assertTrue(scanner.finished)
        self.assertEqual(memory.calls, [(0x80000, 16), (0x80010, 16),
                                        (0x80020, 3), (0x90000, 13), (0x9000d, 4)])

    def test_large_budget_still_reads_blocks_of_at_most_one_mebibyte(self):
        memory = ScanMemory()
        memory.blocks = {0x80000: b'x' * (2 * 1024 * 1024 + 19)}
        memory.scan_regions = [(0x80000, 2 * 1024 * 1024 + 19, 4)]
        scanner = self.make_scanner(memory)

        self.assertIsNone(scanner.step(byte_budget=4 * 1024 * 1024, time_budget=60))

        self.assertTrue(scanner.finished)
        self.assertEqual([count for _, count in memory.calls], [1024 * 1024, 1024 * 1024, 19])

    def test_prefix_split_between_steps_still_finds_fully_validated_history(self):
        memory = ScanMemory()
        node, = memory.set_nodes([chat_line('new game')])
        memory.expose_node(node)
        scanner = self.make_scanner(memory)
        budget = len(discovery.PREFIX) - 1

        self.assertIsNone(scanner.step(byte_budget=budget, time_budget=60))
        result = scanner.step(byte_budget=budget, time_budget=60)

        self.assertEqual(result.root, 0x10000)
        self.assertEqual(result.scanned_bytes, 2 * budget)
        self.assertGreaterEqual(result.elapsed_seconds, 0)

    def test_time_budget_yields_after_a_read_and_resumes_at_the_next_byte(self):
        memory = ScanMemory()
        memory.blocks = {0x80000: b'x' * (1024 * 1024 + 19)}
        memory.scan_regions = [(0x80000, 1024 * 1024 + 19, 4)]
        current_time = [0.0]
        original_read = memory.read

        def slow_read(address, count):
            data = original_read(address, count)
            current_time[0] += 0.06
            return data

        memory.read = slow_read
        with patch.object(discovery.time, 'perf_counter', side_effect=lambda: current_time[0]):
            scanner = self.make_scanner(memory)
            self.assertIsNone(scanner.step(time_budget=0.05))
            self.assertFalse(scanner.finished)
            self.assertEqual(memory.calls, [(0x80000, 1024 * 1024)])
            self.assertIsNone(scanner.step(time_budget=0.05))
            self.assertTrue(scanner.finished)
        self.assertEqual(memory.calls[-1], (0x180000, 19))

    def test_empty_regions_finish_without_reading_or_raising(self):
        memory = ScanMemory()
        scanner = self.make_scanner(memory)

        self.assertIsNone(scanner.step())
        self.assertTrue(scanner.finished)
        self.assertIsNone(scanner.step())
        self.assertEqual(memory.calls, [])

    def test_unchanged_ignored_snapshot_is_not_accepted(self):
        memory = ScanMemory()
        node, = memory.set_nodes([chat_line('old game')])
        memory.expose_node(node)
        reader = discovery.HistoryReader(memory, memory.root, include_history=True)
        reader.poll()
        ignored = {memory.root: (reader.last_head, reader.last_stamp)}
        scanner = self.make_scanner(memory, ignore_snapshots=ignored)

        self.assertIsNone(scanner.step(time_budget=60))
        self.assertTrue(scanner.finished)

    def test_same_root_and_head_with_new_content_is_accepted(self):
        memory = ScanMemory()
        node, = memory.set_nodes([chat_line('old game')])
        reader = discovery.HistoryReader(memory, memory.root, include_history=True)
        reader.poll()
        ignored = {memory.root: (reader.last_head, reader.last_stamp)}
        memory.set_nodes([chat_line('new game')])
        memory.expose_node(node)
        scanner = self.make_scanner(memory, ignore_snapshots=ignored)

        result = scanner.step(time_budget=60)

        self.assertEqual(result.root, memory.root)

    def test_malformed_whisper_history_is_rejected(self):
        memory = ScanMemory()
        private = '<s val="ChatDisplayTimeStamp">24:54</s><sp/><c val="#ColorBattleNetWhisper">private'
        node, = memory.set_nodes([private])
        memory.expose_node(node)
        scanner = self.make_scanner(memory)

        self.assertIsNone(scanner.step(time_budget=60))
        self.assertTrue(scanner.finished)

    def test_changing_snapshot_is_rejected_instead_of_returned(self):
        memory = ScanMemory()
        node, = memory.set_nodes([chat_line('unstable game')])
        memory.expose_node(node)
        original_read = memory.read
        root_reads = [0]

        def changing_read(address, count):
            data = original_read(address, count)
            if address == memory.root and count == 16:
                root_reads[0] += 1
                if root_reads[0] == 3:
                    return struct.pack('<QQ', memory.root | 1, memory.root | 1)
            return data

        memory.read = changing_read
        scanner = self.make_scanner(memory)

        self.assertIsNone(scanner.step(time_budget=60))
        self.assertTrue(scanner.finished)

    def test_cancellation_before_scanning_does_not_read_memory(self):
        memory = ScanMemory()
        event = threading.Event()
        event.set()
        scanner = self.make_scanner(memory, event)

        with self.assertRaises(discovery.DiscoveryCancelled):
            scanner.step()
        self.assertEqual(memory.calls, [])

    def test_cancellation_during_a_read_prevents_the_next_read(self):
        memory = ScanMemory()
        memory.blocks = {0x80000: b'x' * (1024 * 1024 + 19)}
        memory.scan_regions = [(0x80000, 1024 * 1024 + 19, 4)]
        event = threading.Event()
        original_read = memory.read

        def cancelling_read(address, count):
            data = original_read(address, count)
            event.set()
            return data

        memory.read = cancelling_read
        scanner = self.make_scanner(memory, event)

        with self.assertRaises(discovery.DiscoveryCancelled):
            scanner.step(time_budget=60)
        self.assertEqual(memory.calls, [(0x80000, 1024 * 1024)])


if __name__=='__main__': unittest.main()
