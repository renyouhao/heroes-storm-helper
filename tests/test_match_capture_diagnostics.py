"""对局定位诊断使用虚拟时钟和合成内存，不访问游戏或翻译服务。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from hots_chat.acquisition.discovery import DiscoveryPending
from hots_chat.acquisition.memory import GameProcess
from hots_chat.acquisition.source import ChatSource
from hots_chat.diagnostics import DiagnosticRecorder
from test_history import FakeMemory, chat_line
from test_lobby_recovery_latency import VirtualStop


class DiagnosticGame(FakeMemory):
    path = r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'

    def __init__(self):
        super().__init__()
        self.set_nodes([chat_line('hello')])
        self.closed = False

    def close(self):
        self.closed = True


class MatchCaptureDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.clock = [0.]
        self.recorder = DiagnosticRecorder(self.base / 'logs',
            monotonic=lambda: self.clock[0], enabled=True)
        self.addCleanup(self.recorder.close)

    def scan_factory(self, *, accepted_at=None, exhausted_at=None, snapshot_error=False):
        clock = self.clock
        scanners = []

        class Scanner:
            def __init__(self, *args, **options):
                self.ignore_snapshots = options.get('ignore_snapshots', {})
                self.finished = False
                self.steps = self.snapshots = 0
                scanners.append(self)

            def step(self):
                self.steps += 1
                if accepted_at is not None and clock[0] >= accepted_at:
                    return SimpleNamespace(root=0x10000)
                self.finished = exhausted_at is not None and clock[0] >= exhausted_at
                return None

            def diagnostic_snapshot(self):
                self.snapshots += 1
                if snapshot_error:
                    raise RuntimeError('synthetic diagnostic failure')
                return dict(read_bytes=self.steps * 100, read_calls=self.steps,
                    candidate_count=7, valid_snapshot_count=6,
                    invalid_candidate_count=5, empty_snapshot_count=4,
                    retry_snapshot_count=3, ignored_snapshot_count=2,
                    elapsed_ms=123.5)

        return Scanner, scanners

    def run_source(self, scanner, *, recovering=False, deadline=6., recorder=None):
        game, events = DiagnosticGame(), []
        source = ChatSource(process_finder=lambda: [GameProcess(1, game.path, 98348)],
                            game_factory=lambda pid: game, clock=lambda: self.clock[0])
        source.diagnostics = recorder
        source._stop = VirtualStop(self.clock, deadline, lambda now: None)
        initial = (dict(side_effect=DiscoveryPending('synthetic wait')) if recovering else
                   dict(return_value=SimpleNamespace(root=game.root)))
        with patch('hots_chat.acquisition.source.discover', **initial), \
             patch('hots_chat.acquisition.source.IncrementalDiscovery', scanner):
            source._run(events.append)
        self.assertTrue(game.closed)
        return game, events

    def exported_scan_rows(self, stage):
        target = self.recorder.export_report(self.base / 'report.zip')
        with zipfile.ZipFile(target) as archive:
            rows = [json.loads(line) for name in archive.namelist() if name.startswith('helper.log')
                    for line in archive.read(name).decode('utf-8').splitlines() if line]
        return [row for row in rows if row.get('event') == 'capture.state'
                and row.get('source') == 'match' and row.get('stage') == stage]

    def test_recovery_records_progress_every_five_seconds_and_connection_immediately(self):
        scanner, scanners = self.scan_factory(accepted_at=10.5)
        _, events = self.run_source(scanner, recovering=True, deadline=11., recorder=self.recorder)
        rows = self.exported_scan_rows('discovery')
        self.assertEqual([row['outcome'] for row in rows], ['started', 'scanning', 'candidate_found'])
        self.assertEqual([row['monotonic'] for row in rows], [5., 10., 10.5])
        self.assertEqual(scanners[0].snapshots, 3)
        self.assertEqual([event.payload.text for event in events if event.kind == 'chat'], ['hello'])

    def test_inventory_exhaustion_and_all_counters_survive_real_report_export(self):
        scanner, _ = self.scan_factory(exhausted_at=5.5)
        self.run_source(scanner, recorder=self.recorder)
        rows = self.exported_scan_rows('inventory')
        self.assertEqual([row['outcome'] for row in rows], ['started', 'scanning', 'exhausted'])
        self.assertEqual([row['monotonic'] for row in rows], [0., 5., 5.5])
        self.assertEqual({key: rows[-1][key] for key in (
            'read_bytes', 'read_calls', 'candidate_count', 'valid_snapshot_count',
            'invalid_candidate_count', 'empty_snapshot_count', 'retry_snapshot_count',
            'ignored_snapshot_count', 'elapsed_ms')}, dict(read_bytes=1200, read_calls=12,
            candidate_count=7, valid_snapshot_count=6, invalid_candidate_count=5,
            empty_snapshot_count=4, retry_snapshot_count=3, ignored_snapshot_count=2,
            elapsed_ms=123.5))

    def test_real_initial_scan_rejection_and_reader_heartbeat_survive_export(self):
        for rejected in (True, False):
            with self.subTest(rejected=rejected):
                self.clock[0] = 0.
                game, events = DiagnosticGame(), []
                if rejected:
                    game.set_nodes([chat_line('private-body').replace('[团队]', '[SecretChannel]')])
                source = ChatSource(process_finder=lambda: [GameProcess(1, game.path, 98348)],
                    game_factory=lambda pid: game, clock=lambda: self.clock[0])
                source.diagnostics = self.recorder
                source._stop = VirtualStop(self.clock, 6., lambda now: None)
                source._run(events.append)
                rows = self.exported_scan_rows('initial')
                terminal = rows[-1]
                self.assertEqual(terminal['capture_schema'], 2)
                self.assertGreater(terminal['scan_id'], 0)
                self.assertEqual(terminal['outcome'], 'exhausted' if rejected else 'candidate_found')
                self.assertGreater(terminal['nodes_checked_count'], 0)
                if rejected:
                    self.assertGreater(terminal['channel_unsupported_count'], 0)
                    self.assertGreater(terminal['prefix_rejected_count'], 0)
                    self.assertNotIn('SecretChannel', json.dumps(rows))
                    self.assertNotIn('private-body', json.dumps(rows))
                else:
                    polls = self.exported_scan_rows('poll')
                    self.assertTrue(any(row['outcome'] == 'idle' for row in polls))
                    self.assertTrue(all(row['reader_id'] > 0 for row in polls))
                    self.assertTrue(all(row['scan_id'] == terminal['scan_id'] for row in polls))
                    published = self.exported_scan_rows('publish')
                    self.assertEqual(len(published), 1)
                    self.assertEqual(published[0]['published_count'], 1)

    def test_diagnostic_snapshot_and_recorder_failures_do_not_interrupt_capture(self):
        def broken_record(*args, **kwargs):
            raise RuntimeError('synthetic writer failure')

        for snapshot_error, recorder in ((True, self.recorder),
                                         (False, SimpleNamespace(record=broken_record))):
            with self.subTest(snapshot_error=snapshot_error):
                self.clock[0] = 0.
                scanner, _ = self.scan_factory(exhausted_at=1., snapshot_error=snapshot_error)
                _, events = self.run_source(scanner, deadline=2., recorder=recorder)
                self.assertEqual([event.payload.text for event in events if event.kind == 'chat'], ['hello'])

    def test_disabled_diagnostics_do_not_add_game_reads_and_legacy_scanners_still_work(self):
        scanner, scanners = self.scan_factory(exhausted_at=1.)
        plain_game, _ = self.run_source(scanner, deadline=2.)
        self.assertEqual(scanners[0].snapshots, 0)
        self.clock[0] = 0.
        self.recorder.set_enabled(False)
        disabled_game, _ = self.run_source(scanner, deadline=2., recorder=self.recorder)
        self.assertEqual(disabled_game.calls, plain_game.calls)
        self.assertEqual(scanners[-1].snapshots, 0)

        class LegacyScanner:
            def __init__(self, *args, **options):
                self.ignore_snapshots = options.get('ignore_snapshots', {})
                self.finished = False

            def step(self):
                self.finished = True
                return None

        self.clock[0] = 0.
        _, events = self.run_source(LegacyScanner, deadline=2., recorder=self.recorder)
        self.assertEqual([event.payload.text for event in events if event.kind == 'chat'], ['hello'])


if __name__ == '__main__':
    unittest.main()
