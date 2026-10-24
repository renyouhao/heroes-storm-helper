"""Native lobby diagnostics use synthetic rings and virtual time only."""
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref
import zipfile

from hots_chat.acquisition.history import HistoryInvalid, PollResult
from hots_chat.acquisition.lobby_draft import LobbyDraftContext
from hots_chat.acquisition.lobby_native import (LobbyDiscovery, LobbyReader,
                                               NATIVE_COUNT_FIELDS)
from hots_chat.acquisition.lobby_source import LobbySource
from hots_chat.acquisition.memory import GameProcess
from hots_chat.diagnostics import DiagnosticRecorder
from test_lobby_native import FixtureMetadata, LobbyMemory
from test_lobby_draft_context import DraftMemory as DraftContextMemory
from test_lobby_recovery_latency import FILETIME, RecoveryGame, VirtualStop
from test_native_draft_flow import DraftMemory as NativeDraftMemory, DraftMetadata


START_SECONDS = (FILETIME - 116444736000000000) / 10000000


class LobbyReaderDiagnosticTests(unittest.TestCase):
    def test_known_system_unknown_and_unreadable_rows_are_counted_without_unknown_body_reads(self):
        memory = LobbyMemory()
        root = memory.records([('system', 'private system body'), ('队伍', 'known party body'),
            ('密语', 'known whisper body'), ('综合', 'private unsupported body'),
            ('综合', 'private second unknown body'), ('综合', 'private unreadable body')])
        memory.put(memory.links[5] + 16, struct.pack('<Q', 7))
        reader = LobbyReader(memory, root, memory.image_base,
            metadata_factory=FixtureMetadata, include_history=True)
        result = reader.poll()
        self.assertEqual([message.channel for message in result.messages], ['队伍', '密语'])
        counts = reader.diagnostic_snapshot()
        self.assertEqual(counts['native_lobby_schema'], 2)
        for key in ('native_system_count', 'native_party_count', 'native_whisper_count',
                    'native_unsupported_count', 'native_unreadable_count'):
            self.assertGreater(counts[key], 0, key)
        self.assertEqual(counts['native_parsed_count'], 2)
        self.assertEqual(counts['read_calls'], len(memory.calls))
        for index in (0, 3, 4, 5):
            self.assertFalse(any(address == memory.base + 0x40000 + index * 4096
                                 for address, _count in memory.calls))
        self.assertNotIn('private', json.dumps(counts))
        self.assertNotIn('root', counts)

    def test_before_start_and_dedup_have_distinct_counts(self):
        memory = LobbyMemory(); root = memory.records([('队伍', 'old'), ('密语', 'new')])
        reader = LobbyReader(memory, root, memory.image_base, metadata_factory=FixtureMetadata,
                             include_history=True, after_filetime=FILETIME)
        self.assertEqual([message.text for message in reader.poll().messages], ['new'])
        before = reader.diagnostic_snapshot()
        self.assertEqual(before['native_before_start_count'], 1)
        self.assertEqual(before['native_parsed_count'], 1)
        reader.entry(memory.links[-1])
        after = reader.diagnostic_snapshot()
        self.assertEqual(after['native_dedup_count'], 1)
        self.assertEqual(after['native_parsed_count'], 1)

    def test_invalid_entry_is_counted_with_fixed_reason(self):
        memory = LobbyMemory(); root = memory.records([('队伍', 'hi')])
        memory.put(root, struct.pack('<Q', 7))
        reader = LobbyReader(memory, root, memory.image_base)
        with self.assertRaises(HistoryInvalid):reader.entry(root)
        counts = reader.diagnostic_snapshot()
        self.assertEqual(counts['native_invalid_count'], 1)
        self.assertEqual(counts['last_rejection_reason'], 'native_invalid_links')

    def test_unknown_only_discovery_merges_inspector_counts_after_rejection(self):
        memory = LobbyMemory(); memory.records([('综合', 'private unknown text')])
        scanner = LobbyDiscovery(memory, SimpleNamespace(is_set=lambda:False), memory.image_base)
        with patch('hots_chat.acquisition.lobby_native.time.perf_counter', return_value=0):
            self.assertIsNone(scanner.step(time_budget=10, byte_budget=2 * 1048576))
        counts = scanner.diagnostic_snapshot()
        self.assertTrue(scanner.finished)
        self.assertGreater(counts['candidate_count'], 0)
        self.assertGreater(counts['invalid_candidate_count'], 0)
        self.assertEqual(counts['valid_snapshot_count'], 0)
        self.assertGreater(counts['native_unsupported_count'], 0)
        self.assertEqual(counts['native_parsed_count'], 0)
        self.assertIsNone(scanner.inspector)
        self.assertGreater(counts['read_calls'], 1)
        self.assertNotIn('private', json.dumps(counts))


class LobbySourceDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def recorder(self, clock, enabled=True):
        recorder = DiagnosticRecorder(self.base / ('logs-' + str(len(list(self.base.iterdir())))),
            monotonic=lambda:clock[0], enabled=enabled)
        recorder.register_secrets('synthetic-private-key')
        self.addCleanup(recorder.close)
        return recorder

    def run_source(self, game, *, recorder=None, clock=None, advance=lambda now, source:None,
                   deadline=8., discovery_factory=LobbyDiscovery, reader_factory=None,
                   draft_context_factory=LobbyDraftContext):
        clock = clock if clock is not None else [0.]
        events = []
        source = LobbySource(process_finder=lambda:[GameProcess(1, game.path, 98348)],
            game_factory=lambda pid:game, module_base_finder=lambda pid:game.image_base,
            clock=lambda:clock[0], wall_clock=lambda:START_SECONDS,
            reader_factory=reader_factory or (lambda memory, root, image_base, **options:LobbyReader(
                memory, root, image_base, metadata_factory=FixtureMetadata, **options)),
            discovery_factory=discovery_factory, draft_context_factory=draft_context_factory)
        source.diagnostics = recorder
        source._stop = VirtualStop(clock, deadline, lambda now:advance(now, source))
        with patch('hots_chat.acquisition.lobby_native.time.perf_counter', return_value=0):
            source._run(events.append)
        return source, events

    def rows(self, recorder):
        target = recorder.export_report(self.base / 'report.zip')
        with zipfile.ZipFile(target) as archive:
            return [json.loads(line) for name in archive.namelist() if name.startswith('helper.log')
                    for line in archive.read(name).decode('utf-8').splitlines() if line]

    def test_real_export_preserves_success_lifecycles_reader_ids_and_cumulative_fields(self):
        game, clock = RecoveryGame(), [0.]
        game.ring(game.base + 0x1008, [('old', FILETIME), ('new', FILETIME + 10)])
        recorder = self.recorder(clock)
        source, events = self.run_source(game, recorder=recorder, clock=clock, deadline=11.)
        self.assertEqual([event.payload.text for event in events if event.kind == 'chat'], ['new'])
        rows = [row for row in self.rows(recorder) if row.get('source') == 'lobby']
        self.assertTrue(all(row.get('native_lobby_schema') == 2 for row in rows))
        stages = {row['stage'] for row in rows}
        self.assertTrue({'initial', 'inventory', 'watcher', 'nearby', 'discovery', 'poll', 'summary'} <= stages)
        scans = [row for row in rows if row['stage'] in ('initial', 'inventory', 'watcher', 'nearby', 'discovery')]
        starts = [row['scan_id'] for row in scans if row['outcome'] == 'started']
        self.assertEqual(len(starts), len(set(starts)))
        self.assertTrue(all(row['scan_id'] > 0 for row in scans))
        polls = [row for row in rows if row['stage'] == 'poll']
        self.assertTrue(all(row['reader_id'] > 0 and row['scan_id'] > 0 for row in polls))
        totals = [row for row in rows if row['stage'] == 'summary']
        values = [row['native_node_checks'] for row in totals]
        self.assertEqual(values, sorted(values))
        final = next(row for row in reversed(rows) if row['stage'] == 'lifecycle')
        self.assertEqual(final['native_parsed_count'], source.diagnostic_snapshot()['native_parsed_count'])
        self.assertGreater(final['native_before_start_count'], 0)
        self.assertGreater(final['native_parsed_count'], 0)
        self.assertTrue(all(key in final for key in NATIVE_COUNT_FIELDS))

    def test_real_export_keeps_actual_draft_context_and_reader_success_and_rejection_counts(self):
        class ContextMemory(DraftContextMemory):
            base = NativeDraftMemory.image_base

            def allocate(self, size):
                # Keep the synthetic context and ring in disjoint memory ranges.
                self.next = max(self.next, 0x80000000)
                return super().allocate(size)

        class DraftGame(NativeDraftMemory):
            def __init__(self):
                super().__init__()
                self.context_memory = ContextMemory()

            def read(self, address, count):
                if self.base <= address and address + count <= self.base + len(self.data):
                    return super().read(address, count)
                self.calls.append((address, count))
                return self.context_memory.read(address, count)

        game, clock = DraftGame(), [0.]
        accepted = ('征召团队', 'synthetic accepted draft message', None, FILETIME + 10)
        rejected = ('征召团队', 'private ordinary lobby body', None, FILETIME + 20)
        game.records([accepted])
        changed = []

        def advance(now, source):
            if now >= 2 and not changed:
                changed.append(now)
                game.context_memory.attributes(mode=0x6c6f6279)
                game.records([accepted, rejected])

        def context_factory(memory, image_base, **options):
            return LobbyDraftContext(memory, image_base,
                code_fingerprints=memory.context_memory.code_fingerprints, **options)

        recorder = self.recorder(clock)
        source, events = self.run_source(game, recorder=recorder, clock=clock, advance=advance,
            deadline=5., draft_context_factory=context_factory,
            reader_factory=lambda memory, root, image_base, **options:LobbyReader(
                memory, root, image_base, metadata_factory=DraftMetadata, **options))
        self.assertEqual([event.payload.text for event in events if event.kind == 'chat'],
                         [accepted[1]])
        self.assertEqual(game.body_reads(1), [], '失效的征召上下文不能读取普通大厅正文')
        rows = [row for row in self.rows(recorder) if row.get('source') == 'lobby']
        contexts = [row for row in rows if row.get('stage') == 'draft_context']
        self.assertTrue(any(row['outcome'] == 'verified' for row in contexts))
        self.assertTrue(any(row['outcome'] == 'unavailable'
                            and row['reason'] == 'draft_not_selected' for row in contexts))
        fields = ('native_draft_context_checks', 'native_draft_context_changes',
                  'native_draft_context_rejected', 'native_draft_count',
                  'native_draft_rejected_count')
        final = next(row for row in reversed(rows) if row.get('stage') == 'lifecycle')
        snapshot = source.diagnostic_snapshot()
        for field in fields:
            self.assertGreater(final[field], 0, field)
            self.assertEqual(final[field], snapshot[field], field)
        self.assertGreaterEqual(final['native_draft_context_changes'], 2)
        self.assertEqual(final['native_lobby_schema'], 2)
        self.assertNotIn(rejected[1], json.dumps(rows))
        self.assertNotIn('synthetic-private-key', json.dumps(rows))

    def test_real_export_distinguishes_zero_candidates_from_unknown_only_ring(self):
        for unknown in (False, True):
            game, clock = RecoveryGame(), [0.]
            if unknown:
                game.records([('综合', 'private-body-must-not-be-exported')])
            recorder = self.recorder(clock)
            source, events = self.run_source(game, recorder=recorder, clock=clock, deadline=1.)
            self.assertFalse(any(event.kind == 'chat' for event in events))
            rows = [row for row in self.rows(recorder) if row.get('source') == 'lobby']
            scan = next(row for row in rows if row.get('stage') == 'initial' and row['outcome'] == 'exhausted')
            if unknown:
                self.assertGreater(scan['candidate_count'], 0)
                self.assertGreater(scan['native_unsupported_count'], 0)
                self.assertEqual(scan['last_rejection_reason'], 'native_no_supported_conversation')
            else:
                self.assertEqual(scan['candidate_count'], 0)
                self.assertEqual(scan['native_unsupported_count'], 0)
            self.assertNotIn('private-body', json.dumps(rows))
            self.assertNotIn('synthetic-private-key', json.dumps(rows))
            self.assertNotIn('root', scan)
            self.assertEqual(source.diagnostic_snapshot()['native_parsed_count'], 0)

    def test_diagnostic_switch_and_logger_failures_do_not_change_business_reads_or_messages(self):
        observations = []
        for mode in ('absent', 'disabled', 'enabled', 'record_error', 'snapshot_error'):
            game, clock = RecoveryGame(), [0.]
            game.ring(game.base + 0x1008, [('old', FILETIME), ('new', FILETIME + 10)])
            recorder = None
            if mode in ('disabled', 'enabled'):recorder = self.recorder(clock, enabled=mode == 'enabled')
            if mode == 'record_error':
                def fail(*args, **kwargs):raise OSError('private-log-path')
                recorder = SimpleNamespace(snapshot=lambda:dict(enabled=True), record=fail)
            if mode == 'snapshot_error':
                def fail():raise RuntimeError('private-log-state')
                recorder = SimpleNamespace(snapshot=fail, record=lambda *args, **kwargs:None)
            _source, events = self.run_source(game, recorder=recorder, clock=clock, deadline=7.)
            observations.append((list(game.calls), [event.payload.text for event in events if event.kind == 'chat']))
        self.assertTrue(all(item == observations[0] for item in observations))
        self.assertEqual(observations[0][1], ['new'])

    def test_bad_link_and_unreadable_candidates_have_distinct_exported_reasons(self):
        for unreadable in (False, True):
            class BadGame(RecoveryGame):
                failed_address = None
                def read(self, address, count):
                    if address == self.failed_address and count == 24:return b''
                    return super().read(address, count)
            game, clock = BadGame(), [0.]
            root = game.records([('队伍', 'private text')])
            if unreadable:game.failed_address = root - 8
            else:game.put(root, struct.pack('<Q', 7))
            recorder = self.recorder(clock)
            self.run_source(game, recorder=recorder, clock=clock, deadline=1.)
            rows = self.rows(recorder)
            rejected = next(row for row in rows if row.get('stage') == 'initial'
                            and row.get('outcome') == 'exhausted')
            self.assertGreater(rejected['candidate_count'], 0)
            self.assertGreater(rejected['native_invalid_count'], 0)
            self.assertEqual(rejected['native_unsupported_count'], 0)
            self.assertEqual(rejected['last_rejection_reason'],
                             'native_read_unavailable' if unreadable else 'native_invalid_links')
            self.assertNotIn('private text', json.dumps(rows))

    def test_scan_error_has_an_identified_terminal_and_never_exports_exception_body(self):
        clock = [0.]
        recorder = self.recorder(clock)
        class FailingScan:
            def __init__(self, *args, **kwargs):self.finished=False
            def step(self, **kwargs):raise OSError('private-filesystem-path')
        self.run_source(RecoveryGame(), recorder=recorder, clock=clock,
                        discovery_factory=FailingScan, deadline=1.)
        rows = self.rows(recorder)
        scan = [row for row in rows if row.get('stage') == 'initial']
        self.assertEqual([row['outcome'] for row in scan], ['started', 'error'])
        self.assertEqual(scan[0]['scan_id'], scan[1]['scan_id'])
        self.assertEqual(scan[1]['error_type'], 'OSError')
        self.assertNotIn('private-filesystem-path', json.dumps(rows))

    def test_enabling_recording_mid_scan_gets_a_current_snapshot_within_five_seconds(self):
        clock, enabled_at = [0.], []
        recorder = self.recorder(clock, enabled=False)
        class SlowScan:
            def __init__(self, *args, **kwargs):self.finished=False;self.steps=0
            def step(self, **kwargs):self.steps+=1;return None
            def diagnostic_snapshot(self):
                return dict(native_node_checks=self.steps, native_unsupported_count=self.steps,
                            candidate_count=self.steps, read_calls=self.steps, read_bytes=self.steps * 24)
        def advance(now, source):
            if now >= 2 and not enabled_at:
                enabled_at.append(now);recorder.set_enabled(True)
        self.run_source(RecoveryGame(), recorder=recorder, clock=clock, advance=advance,
                        deadline=8., discovery_factory=SlowScan)
        rows = [row for row in self.rows(recorder) if row.get('source') == 'lobby']
        snapshot = next(row for row in rows if row.get('native_node_checks', 0) > 0)
        self.assertLessEqual(snapshot['monotonic'] - enabled_at[0], 5.)
        self.assertEqual(snapshot['native_lobby_schema'], 2)
        scans = [row for row in rows if row['stage'] == 'initial']
        self.assertEqual(scans[-1]['outcome'], 'cancelled')
        self.assertEqual(len({row['scan_id'] for row in scans}), 1)
        self.assertLess(len(rows), 35)

    def test_reader_and_scanner_without_diagnostic_snapshot_remain_compatible(self):
        game, clock = RecoveryGame(), [0.]
        class PlainScan:
            def __init__(self, *args, **kwargs):self.finished=kwargs.get('regions')==();self.used=False
            def step(self, **kwargs):
                if self.finished:return None
                if self.used:self.finished=True;return None
                self.used=True;return SimpleNamespace(root=game.base+0x1008,region=None)
        class PlainReader:
            __slots__=('root','cursor','drain_nodes','record_identities')
            def __init__(self, memory, root, image_base, **options):
                self.root=root;self.cursor=(root,());self.drain_nodes=();self.record_identities=()
            def poll(self):return PollResult([],False,False,0)
        recorder = self.recorder(clock)
        source, events = self.run_source(game, recorder=recorder, clock=clock, deadline=2.,
                                        discovery_factory=PlainScan, reader_factory=PlainReader)
        self.assertTrue(any(event.kind == 'status' for event in events))
        self.assertFalse(any(event.kind == 'chat' for event in events))
        self.assertEqual(game.closed, 1)
        self.assertEqual(source.diagnostic_snapshot()['native_parsed_count'], 0)

    def test_reader_changes_keep_source_totals_monotonic(self):
        game, clock = RecoveryGame(), [0.]
        first, second = game.base + 0x1008, game.base + 0x80008
        game.ring(first, [('old-a', FILETIME)])
        game.ring(second, [('old-b', FILETIME)])
        changed = []
        def advance(now, source):
            if now >= 2 and not changed:
                changed.append(now);game.ring(second, [('old-b', FILETIME), ('new-b', FILETIME+10)])
        recorder = self.recorder(clock)
        source, events = self.run_source(game, recorder=recorder, clock=clock, advance=advance, deadline=12.)
        self.assertEqual([event.payload.text for event in events if event.kind == 'chat'], ['new-b'])
        rows = [row for row in self.rows(recorder) if row.get('stage') == 'summary']
        for key in NATIVE_COUNT_FIELDS:
            values = [row[key] for row in rows]
            self.assertEqual(values, sorted(values), key)
        self.assertGreater(source.diagnostic_snapshot()['native_parsed_count'], 0)

    def test_repeated_watcher_hits_do_not_retain_retired_scanners(self):
        game, clock = RecoveryGame(), [0.]
        roots = (game.base + 0x1008, game.base + 0x80008)
        live = weakref.WeakSet()
        full_started, watcher_hits, live_counts = [], [], []
        class ManyHitsScan:
            def __init__(self, *args, **options):
                live.add(self)
                self.finished = False
                self.steps = 0
                self.watch = options.get('regions') == ()
                self.preferred = options.get('preferred_roots', ())
                self.initial = not full_started and options.get('regions') is None
                if options.get('regions') is None:full_started.append(True)
            def step(self, **options):
                self.steps += 1
                if self.watch and self.preferred:
                    watcher_hits.append(True)
                    return SimpleNamespace(root=self.preferred[0], region=None)
                if self.initial and self.steps <= 2:
                    return SimpleNamespace(root=roots[self.steps - 1], region=None)
                self.finished = True
                return None
        class PlainReader:
            def __init__(self, memory, root, image_base, **options):
                self.root=root;self.cursor=(root,());self.drain_nodes=();self.record_identities=()
            def poll(self):return PollResult([], False, False, 0)
        def advance(now, source):live_counts.append(len(live))
        self.run_source(game, clock=clock, deadline=80., advance=advance,
                        discovery_factory=ManyHitsScan, reader_factory=PlainReader)
        self.assertGreater(len(watcher_hits), 100)
        self.assertLessEqual(max(live_counts), 4)
        self.assertEqual(len(live), 0)


if __name__ == '__main__':unittest.main()
