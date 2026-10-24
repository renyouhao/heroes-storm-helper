"""离线ZIP解析不提取文件，不信任包内代码。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from hots_chat import replay


class ReplayArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'recording.zip'

    def archive(self, rows, summary=None, extra=None):
        summary = summary or dict(schema=2, replay_schema=1, session_id='session', dropped=0)
        with zipfile.ZipFile(self.path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('summary.json', json.dumps(summary))
            archive.writestr('helper.log', '\n'.join(json.dumps(row) for row in rows))
            for name, value in (extra or {}).items():archive.writestr(name, value)

    def frame(self, **fields):
        return dict(event='replay.frame', session_id='session', sequence=1,
                    component='board', operation='mouse', payload={}, **fields)

    def test_calls_only_known_adapter_without_extracting_extra_files(self):
        self.archive([self.frame()], extra={'../injected.py':'raise RuntimeError("executed")'})
        with patch.object(replay, '_replay_frame', return_value={'matched':True}) as adapter:
            report = replay.replay_archive(self.path)
        self.assertEqual(report['status'], 'matched')
        self.assertEqual(report['frames'], 1)
        self.assertEqual(adapter.call_count, 1)
        self.assertFalse((self.path.parent/'injected.py').exists())

    def test_mismatch_is_reported_and_changes_status(self):
        self.archive([self.frame()])
        with patch.object(replay, '_replay_frame', return_value={'matched':False,'mismatches':['state']}):
            report = replay.replay_archive(self.path)
        self.assertEqual(report['status'], 'mismatch')
        self.assertEqual(report['mismatched'], 1)

    def test_dropped_and_truncated_data_is_never_called_complete(self):
        self.archive([self.frame(payload_truncated=True)],
            summary=dict(schema=2,replay_schema=1,session_id='session',dropped=3,overwritten=1))
        with patch.object(replay, '_replay_frame') as adapter:
            report = replay.replay_archive(self.path)
        self.assertEqual(report['status'], 'incomplete')
        self.assertFalse(report['complete'])
        adapter.assert_not_called()

    def test_legacy_diagnostic_pack_is_readable_but_has_no_replay(self):
        self.archive([{'event':'hotkey.state','stage':'talent','outcome':'unknown'}],
            summary=dict(schema=1,session_id='session',dropped=0))
        report = replay.replay_archive(self.path)
        self.assertEqual(report['status'], 'unsupported')
        self.assertEqual(report['frames'], 0)

    def test_other_sessions_are_not_mixed_into_current_recording(self):
        row = self.frame(); row['session_id'] = 'old-session'
        self.archive([row, self.frame()])
        with patch.object(replay, '_replay_frame', return_value={'matched':True}) as adapter:
            report = replay.replay_archive(self.path)
        self.assertEqual(adapter.call_count, 1)
        self.assertEqual(report['session_id'], 'session')

    def test_unknown_component_and_invalid_payload_are_not_executed(self):
        row = self.frame(); row['component'] = 'os.system'
        self.archive([row])
        with patch.object(replay, '_replay_frame') as adapter:
            report = replay.replay_archive(self.path)
        adapter.assert_not_called()
        self.assertEqual(report['status'], 'incomplete')

    def test_export_rejected_or_invalid_payload_summary_means_incomplete(self):
        for field in ('export_rejected','payload_invalid','incomplete'):
            with self.subTest(field=field):
                summary = dict(schema=2,session_id='session');summary[field] = 1
                self.archive([self.frame()], summary=summary)
                with patch.object(replay, '_replay_frame', return_value={'matched':True}):
                    report = replay.replay_archive(self.path)
                self.assertFalse(report['complete'])
                self.assertEqual(report['status'], 'incomplete')

    def test_cli_never_overwrites_input_archive_with_report(self):
        self.archive([self.frame()])
        original = self.path.read_bytes()
        with patch.object(replay, '_replay_frame', return_value={'matched':True}):
            code = replay.main([str(self.path),'--output',str(self.path)])
        self.assertEqual(code, 2)
        self.assertEqual(self.path.read_bytes(), original)

    def test_partial_frame_report_preserves_queue_failure_reason(self):
        frame=self.frame();frame.update(component='translation',operation='queue_overflow',
            payload={'schema':1,'complete':False,'reason':'source_queue_full'})
        self.archive([frame])
        report=replay.replay_archive(self.path)
        self.assertEqual(report['status'],'incomplete')
        self.assertEqual(report['details'][0]['reason'],'source_queue_full')

    def test_invalid_json_line_does_not_hide_other_valid_frames(self):
        self.archive([self.frame()])
        with zipfile.ZipFile(self.path, 'w') as archive:
            archive.writestr('summary.json', json.dumps(dict(schema=2,session_id='session')))
            archive.writestr('helper.log', '{bad-json}\n'+json.dumps(self.frame()))
        with patch.object(replay, '_replay_frame', return_value={'matched':True}):
            report = replay.replay_archive(self.path)
        self.assertEqual(report['matched'], 1)
        self.assertEqual(report['status'], 'incomplete')

    def test_cli_writes_json_report_without_running_external_programs(self):
        self.archive([self.frame()])
        destination = self.path.parent / 'report.json'
        with patch.object(replay, '_replay_frame', return_value={'matched':True}):
            code = replay.main([str(self.path), '--output', str(destination)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(destination.read_text(encoding='utf-8'))['status'], 'matched')
