"""扫描诊断使用合成内存和虚拟时钟，验证终态与累计观测。"""
import json
import struct
import unittest
from unittest.mock import patch

from hots_chat.acquisition import discovery
from test_history import FakeMemory, chat_line
from test_party_whisper_acquisition import party_out


class VirtualCancel:
    def __init__(self, clock, cancelled=False):
        self.clock, self.cancelled = clock, cancelled

    def is_set(self):
        return self.cancelled

    def wait(self, seconds):
        self.clock[0] += seconds
        return self.cancelled


class ScanMemory(FakeMemory):
    def __init__(self):
        super().__init__()
        self.scan_regions = []

    def regions(self):
        yield from self.scan_regions


class DiscoveryDiagnosticTests(unittest.TestCase):
    terminal_outcomes = frozenset(('candidate_found', 'exhausted', 'timeout', 'cancelled', 'failed'))

    def assert_trace(self, events, terminal):
        self.assertEqual(events[0][0], 'started')
        self.assertEqual(events[-1][0], terminal)
        self.assertEqual(sum(outcome in self.terminal_outcomes for outcome, _ in events), 1)
        self.assertEqual({stats['capture_schema'] for _, stats in events}, {2})
        self.assertEqual(len({stats['scan_id'] for _, stats in events}), 1)
        self.assertGreater(events[0][1]['scan_id'], 0)

    def run_discovery(self, memory, *, cancelled=False, progress_hook=None):
        clock, events = [0.], []
        cancel = VirtualCancel(clock, cancelled)

        def progress(outcome, stats):
            events.append((outcome, stats))
            if progress_hook:
                progress_hook(outcome, stats, clock)

        with patch.object(discovery.time, 'perf_counter', side_effect=lambda: clock[0]):
            try:
                result = discovery.discover(memory, cancel, channels=('团队', '所有玩家'), progress=progress)
            except (discovery.DiscoveryPending, discovery.DiscoveryCancelled, OSError) as failure:
                return None, failure, events
        return result, None, events

    def test_initial_scan_success_exhaustion_and_precancel_have_correlated_terminal(self):
        for case in ('success', 'exhausted', 'cancelled'):
            with self.subTest(case=case):
                memory = ScanMemory()
                node, = memory.set_nodes([chat_line('private body from private-sender')])
                memory.scan_regions = [(node + 24, len(memory.blocks[node]) - 24, 4)]
                if case == 'exhausted':
                    memory.blocks = {0x80000: b'private text without chat prefix'}
                    memory.scan_regions = [(0x80000, len(memory.blocks[0x80000]), 4)]
                result, failure, events = self.run_discovery(memory, cancelled=case == 'cancelled')
                self.assert_trace(events, 'candidate_found' if case == 'success' else case)
                if case == 'success':
                    self.assertIsNone(failure)
                    self.assertEqual(result.root, memory.root)
                    self.assertEqual(events[-1][1]['parsed_message_count'], 1)
                elif case == 'exhausted':
                    self.assertIsInstance(failure, discovery.DiscoveryPending)
                    self.assertEqual(failure.reason, 'scan_exhausted')
                    self.assertEqual(events[-1][1]['candidate_count'], 0)
                else:
                    self.assertIsInstance(failure, discovery.DiscoveryCancelled)
                    self.assertEqual(memory.calls, [])

    def test_initial_scan_timeout_after_progress_uses_virtual_time(self):
        class LargeMemory:
            def regions(self):
                yield 0x80000, 2 * 1024 * 1024, 4

            def read(self, address, count):
                return b'x' * count

        def expire(outcome, stats, clock):
            if outcome == 'scanning':
                clock[0] = 61.

        _, failure, events = self.run_discovery(LargeMemory(), progress_hook=expire)
        self.assertIsInstance(failure, discovery.DiscoveryPending)
        self.assertEqual(failure.reason, 'scan_timeout')
        self.assert_trace(events, 'timeout')
        self.assertIn('scanning', [outcome for outcome, _ in events])
        self.assertEqual(events[-1][1]['requested_bytes'], 1024 * 1024)

    def test_initial_scan_read_failure_has_terminal_without_error_body(self):
        class UnreadableMemory:
            def regions(self):
                yield 0x80000, 64, 4

            def read(self, address, count):
                raise OSError(5, 'private-os-error-and-path')

        _, failure, events = self.run_discovery(UnreadableMemory())
        self.assertIsInstance(failure, OSError)
        self.assert_trace(events, 'failed')
        self.assertEqual(events[-1][1]['read_calls'], 1)
        self.assertEqual(events[-1][1]['requested_bytes'], 64)
        self.assertEqual(events[-1][1]['read_bytes'], 0)
        self.assertNotIn('private-os-error', json.dumps(events))

    def test_cancellation_during_scan_read_retains_progress_and_terminal(self):
        clock, events = [0.], []
        cancel = VirtualCancel(clock)

        class CancelDuringRead:
            def regions(self):
                yield 0x80000, 64, 4

            def read(self, address, count):
                cancel.cancelled = True
                return b'x' * count

        with patch.object(discovery.time, 'perf_counter', side_effect=lambda: clock[0]):
            with self.assertRaises(discovery.DiscoveryCancelled):
                discovery.discover(CancelDuringRead(), cancel,
                                   progress=lambda outcome, stats: events.append((outcome, stats)))
        self.assert_trace(events, 'cancelled')
        self.assertEqual(events[-1][1]['read_calls'], 1)
        self.assertEqual(events[-1][1]['read_bytes'], 64)

    def test_short_scan_read_is_visible_without_claiming_a_win32_error(self):
        memory = ScanMemory()
        memory.blocks = {0x80000: b'x' * 8}
        memory.scan_regions = [(0x80000, 64, 4)]
        _, failure, events = self.run_discovery(memory)
        self.assertIsInstance(failure, discovery.DiscoveryPending)
        self.assert_trace(events, 'exhausted')
        stats = events[-1][1]
        self.assertEqual(stats['requested_bytes'], 64)
        self.assertEqual(stats['read_bytes'], 8)
        self.assertEqual(stats['short_read_count'], 1)
        self.assertEqual(stats['last_rejection_reason'], '')
        self.assertNotIn('winerror', stats)

    def test_callback_errors_do_not_change_results_or_memory_reads(self):
        def make_memory():
            memory = ScanMemory()
            line = chat_line('private body').replace('Hunter', 'private-sender')
            node, = memory.set_nodes([line])
            memory.scan_regions = [(node + 24, len(memory.blocks[node]) - 24, 4)]
            return memory

        plain, recorded = make_memory(), make_memory()
        clock = [0.]
        with patch.object(discovery.time, 'perf_counter', side_effect=lambda: clock[0]):
            expected = discovery.discover(plain, VirtualCancel(clock), channels=('团队', '所有玩家'))

        def broken_callback(outcome, stats, clock):
            raise RuntimeError('private callback failure')

        result, failure, events = self.run_discovery(recorded, progress_hook=broken_callback)
        self.assertIsNone(failure)
        self.assertEqual(result.root, expected.root)
        self.assertEqual(recorded.calls, plain.calls)
        self.assert_trace(events, 'candidate_found')
        text = json.dumps(events)
        for sensitive in ('private body', 'private-sender', 'private callback failure'):
            self.assertNotIn(sensitive, text)
        for _, stats in events:
            self.assertNotIn('root', stats)
            self.assertNotIn('node', stats)
            self.assertTrue(all(type(value) in (int, float) for key, value in stats.items()
                                if key != 'last_rejection_reason'))

    def test_arbitrary_exception_reason_is_replaced_by_fixed_rejection_code(self):
        memory = ScanMemory()
        memory.blocks = {0x80000: discovery.PREFIX}
        memory.scan_regions = [(0x80000, len(discovery.PREFIX), 4)]
        scanner = discovery.IncrementalDiscovery(memory, VirtualCancel([0.]))
        failure = discovery.HistoryInvalid('private exception body', reason='private dynamic reason')
        with patch.object(discovery, 'locate_root', side_effect=failure):
            self.assertIsNone(scanner.step(time_budget=60))
        stats = scanner.diagnostic_snapshot()
        self.assertEqual(stats['invalid_candidate_count'], 1)
        self.assertEqual(stats['last_rejection_reason'], 'history_invalid')
        self.assertNotIn('private', json.dumps(stats))

    def test_rejections_before_reader_failure_survive_cache_removal_and_rebuild(self):
        memory = ScanMemory()
        party = party_out('private party baseline')
        node, = memory.set_nodes([party])
        # Revisit the root during the same scan, keeping each scan step bounded.
        count = max(len(party.encode('utf-8')), len(chat_line('private team').encode('utf-8')))
        memory.scan_regions = [(node + 24, count, 4)] * 3
        scanner = discovery.IncrementalDiscovery(memory, VirtualCancel([0.]), channels=('团队', '所有玩家'))
        self.assertIsNone(scanner.step(byte_budget=count, time_budget=60))
        baseline = scanner.diagnostic_snapshot()
        self.assertEqual(baseline['channel_filtered_count'], 1)

        unknown = chat_line('private unknown body').replace('[团队]', '[秘密]')
        memory.set_nodes([chat_line('private team'), unknown])
        corrupt = bytearray(memory.blocks[node])
        corrupt[-2] = 255
        memory.blocks[node] = bytes(corrupt)
        self.assertIsNone(scanner.step(byte_budget=count, time_budget=60))
        rejected = scanner.diagnostic_snapshot()
        self.assertEqual(rejected['channel_unsupported_count'], 1)
        self.assertEqual(rejected['prefix_rejected_count'], 1)
        self.assertEqual(rejected['last_rejection_reason'], 'invalid_utf8')
        self.assertEqual(rejected['candidate_layout_rejected_count'], 1)

        memory.set_nodes([chat_line('private team'), unknown])
        result = scanner.step(time_budget=60)
        self.assertIsNotNone(result)
        final = scanner.diagnostic_snapshot()
        self.assertEqual(final['channel_unsupported_count'], 2)
        self.assertEqual(final['candidate_layout_rejected_count'], 1)
        self.assertGreater(final['parsed_message_count'], rejected['parsed_message_count'])
        for key, value in baseline.items():
            if key.endswith('_count'):
                self.assertGreaterEqual(final.get(key, 0), value)
        self.assertNotIn('秘密', json.dumps(final))

    def test_reader_counter_totals_do_not_decrease_when_lru_entries_are_evicted(self):
        memory = ScanMemory()
        memory.blocks.clear()
        line = party_out('same party message')
        count = len(line.encode('utf-8'))
        for slot in range(129):
            source = FakeMemory()
            node, = source.set_nodes([line])
            delta = slot * 0x100000
            for address, data in source.blocks.items():
                width = 2 if address == source.root else 3
                values = list(struct.unpack('<' + 'Q' * width, data[:width * 8]))
                values[0] += delta
                values[1] += delta
                memory.blocks[address + delta] = struct.pack('<' + 'Q' * width, *values) + data[width * 8:]
            memory.scan_regions.append((node + delta + 24, count, 4))
        memory.scan_regions.append(memory.scan_regions[0])
        scanner = discovery.IncrementalDiscovery(memory, VirtualCancel([0.]), channels=('团队', '所有玩家'))
        snapshots = []
        for budget in (128 * count, count, count):
            self.assertIsNone(scanner.step(byte_budget=budget, time_budget=60))
            snapshots.append(scanner.diagnostic_snapshot())
        self.assertEqual([stats['parsed_message_count'] for stats in snapshots], [128, 129, 130])
        self.assertEqual([stats['channel_filtered_count'] for stats in snapshots], [128, 129, 130])
        self.assertLessEqual(len(scanner._checked_roots), 128)
        calls = list(memory.calls)
        scanner.diagnostic_snapshot()
        self.assertEqual(memory.calls, calls)


if __name__ == '__main__':
    unittest.main()
