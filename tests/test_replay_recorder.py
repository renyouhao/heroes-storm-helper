import json
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile

from hots_chat.diagnostics import DiagnosticRecorder


class ReplayRecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def recorder(self, **kwargs):
        recorder = DiagnosticRecorder(self.base / 'logs', enabled=True, **kwargs)
        self.addCleanup(recorder.close)
        return recorder

    def export(self, recorder):
        with zipfile.ZipFile(recorder.export_report(self.base / 'report.zip')) as archive:
            summary = json.loads(archive.read('summary.json'))
            rows = [json.loads(line) for name in archive.namelist() if name != 'summary.json'
                    for line in archive.read(name).decode().splitlines()]
        return summary, sorted(rows, key=lambda row: row.get('sequence', 0))

    def test_frames_keep_full_input_and_duplicates_with_order_and_epoch(self):
        recorder = self.recorder(monotonic=lambda: 12.5)
        payload = {'original': '中' * 4096, 'translated': 't' * 1000,
                   'state': {'rects': [[1, 2, 3, 4]], 'ready': True, 'missing': None}}
        for _ in range(2):
            self.assertTrue(recorder.record('replay.frame', component='translation',
                                            operation='submit', payload=payload))
        payload['state']['rects'][0][0] = 999
        summary, rows = self.export(recorder)
        frames = [row for row in rows if row['event'] == 'replay.frame']
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0]['payload']['original'], '中' * 4096)
        self.assertEqual(frames[0]['payload']['state']['rects'], [[1, 2, 3, 4]])
        self.assertEqual([r['sequence'] for r in rows], [1, 2, 3])
        self.assertTrue(all(row['monotonic'] == 12.5 for row in rows))
        self.assertEqual(rows[0]['event'], 'diagnostic.start')
        self.assertTrue(all(row['recording_epoch'] == summary['recording_epoch'] for row in rows))
        self.assertEqual(summary['schema'], 2)
        self.assertEqual(summary['replay_schema'], 1)
        self.assertEqual(summary['max_total_bytes'], 104857600)
        self.assertFalse(summary['incomplete'])

    def test_nested_secrets_are_redacted_even_registered_after_first_export(self):
        recorder = self.recorder()
        recorder.register_secrets('known-key')
        recorder.record('replay.frame', component='translation', operation='submit',
                        payload={'known-key': ['say known-key', {'original': 'late-key'}],
                                 'api_key': 'never-registered-key', 'authorization': 'Bearer private'})
        self.export(recorder)
        recorder.register_secrets('late-key')
        summary, rows = self.export(recorder)
        text = json.dumps(rows)
        for secret in ('known-key', 'late-key', 'never-registered-key', 'Bearer private'):
            self.assertNotIn(secret, text)
        frame = next(r for r in rows if r['event'] == 'replay.frame')
        self.assertTrue(frame['payload_redacted'])
        self.assertTrue(summary['incomplete'])
        self.assertTrue(summary['payload_redacted'])

    def test_non_json_values_cycles_and_excess_strings_are_explicitly_incomplete(self):
        recorder = self.recorder()
        cyclic = []
        cyclic.append(cyclic)
        recorder.record('replay.frame', component='board', operation='poll',
                        payload={'bad': object(), 'cycle': cyclic, 'long': 'x' * 17000,
                                 'number': float('nan')})
        summary, rows = self.export(recorder)
        frame = next(r for r in rows if r['event'] == 'replay.frame')
        self.assertTrue(frame['payload_invalid'])
        self.assertTrue(frame['payload_truncated'])
        self.assertEqual(len(frame['payload']['long']), 16384)
        self.assertGreater(summary['truncated'], 0)
        self.assertTrue(summary['incomplete'])

    def test_sequence_remains_ordered_when_multiple_threads_record(self):
        recorder = self.recorder(queue_size=1000)
        threads = [threading.Thread(target=lambda: [recorder.record('replay.frame',
                   component='talent', operation='poll', payload={'x': 1}) for _ in range(40)])
                   for _ in range(4)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        _, rows = self.export(recorder)
        self.assertEqual(len(rows), 161)
        self.assertEqual([r['sequence'] for r in rows], list(range(1, 162)))

    def test_total_budget_covers_all_segments_and_oversized_old_upgrade_files(self):
        directory = self.base / 'logs'
        directory.mkdir()
        for name in ('helper.log', 'helper.log.1', 'helper.log.4'):
            (directory / name).write_text('old log' * 1000)
        recorder = self.recorder(max_total_bytes=1800)
        for i in range(20):
            recorder.record('replay.frame', component='board', operation='poll', payload={'i': i})
        summary, rows = self.export(recorder)
        paths = list(directory.glob('helper.log*'))
        self.assertLessEqual(sum(path.stat().st_size for path in paths), 1800)
        self.assertGreater(summary['overwritten'], 0)
        self.assertTrue(summary['incomplete'])
        self.assertEqual(next(r for r in reversed(rows) if r['event'] == 'replay.frame')['payload'], {'i': 19})

    def test_frame_larger_than_total_budget_is_rejected_and_reported(self):
        recorder = self.recorder(max_total_bytes=500)
        recorder.record('replay.frame', component='board', operation='poll', payload={'text': 'x' * 1000})
        summary, rows = self.export(recorder)
        self.assertFalse(any(r['event'] == 'replay.frame' for r in rows))
        self.assertGreater(summary['oversized'], 0)
        self.assertTrue(summary['incomplete'])
        self.assertLessEqual(sum(p.stat().st_size for p in (self.base / 'logs').glob('helper.log*')), 500)

    def test_new_recording_epoch_after_disable_and_enable(self):
        recorder = self.recorder()
        recorder.record('replay.frame', component='translation', operation='first', payload={})
        recorder.set_enabled(False)
        recorder.set_enabled(True)
        recorder.record('replay.frame', component='translation', operation='second', payload={})
        summary, rows = self.export(recorder)
        frames = [r for r in rows if r['event'] == 'replay.frame']
        self.assertNotEqual(frames[0]['recording_epoch'], frames[1]['recording_epoch'])
        self.assertEqual(summary['recording_epoch'], frames[1]['recording_epoch'])

    def test_component_partial_frames_are_reflected_in_export_summary(self):
        recorder=self.recorder()
        recorder.record('replay.frame',component='translation',operation='queue_overflow',
            payload={'schema':1,'complete':False,'reason':'output_queue_full'})
        recorder.record('replay.frame',component='board',operation='mouse',
            payload={'schema':1,'incomplete':'unsupported_input'})
        for _ in range(2):
            summary,_=self.export(recorder)
            self.assertEqual(summary['incomplete_frames'],2)
            self.assertTrue(summary['incomplete'])

    def test_single_event_utf8_limit_drops_frame_with_integrity_signal(self):
        recorder = self.recorder()
        recorder.record('replay.frame', component='translation', operation='submit',
                        payload={'chunks': ['中' * 16000 for _ in range(30)]})
        summary, rows = self.export(recorder)
        self.assertFalse(any(r['event'] == 'replay.frame' for r in rows))
        self.assertEqual(summary['oversized'], 1)
        self.assertTrue(summary['incomplete'])
        self.assertFalse(summary['write_error'])

    def test_payload_requires_structured_json_and_missing_payload_is_invalid(self):
        recorder = self.recorder()
        recorder.record('replay.frame', component='board', operation='scalar', payload='text')
        recorder.record('replay.frame', component='board', operation='missing')
        summary, rows = self.export(recorder)
        frames = [row for row in rows if row['event'] == 'replay.frame']
        self.assertEqual(len(frames), 2)
        self.assertTrue(all(row['payload_invalid'] for row in frames))
        self.assertTrue(summary['incomplete'])

    def test_payload_count_bound_applies_to_invalid_keys_and_credentials(self):
        recorder = self.recorder()
        # 每个无效键也消耗预算，不能因未复制子值而跳过节点限制。
        recorder.record('replay.frame', component='board', operation='poll',
                        payload={number: 'ignored' for number in range(15000)})
        summary, rows = self.export(recorder)
        frame = next(row for row in rows if row['event'] == 'replay.frame')
        self.assertTrue(frame['payload_truncated'])
        self.assertTrue(frame['payload_invalid'])
        self.assertTrue(summary['incomplete'])


if __name__ == '__main__':
    unittest.main()
