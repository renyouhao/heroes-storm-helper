import json
import os
from pathlib import Path
import tempfile
import threading
import time
import tracemalloc
import unittest
from unittest.mock import patch
import zipfile

from hots_chat.diagnostics import DiagnosticRecorder


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.now = 0.0
        self.recorder = DiagnosticRecorder(self.base / 'logs', monotonic=lambda: self.now, enabled=True)
        self.addCleanup(self.recorder.close)

    def export_rows(self):
        target = self.recorder.export_report(self.base / 'report.zip')
        with zipfile.ZipFile(target) as archive:
            summary = json.loads(archive.read('summary.json'))
            rows = [json.loads(line) for name in archive.namelist() if name.endswith('.log')
                    for line in archive.read(name).decode('utf-8').splitlines() if line]
        return summary, rows

    def test_default_details_timeout_and_truncation(self):
        self.recorder.record('message.received', message_id='a', original='private-first')
        self.recorder.enable_details(True)
        self.recorder.record('message.received', message_id='b', original='好' * 300)
        self.now = 600
        self.recorder.record('message.received', message_id='c', original='private-last')
        summary, rows = self.export_rows()
        values = {r['message_id']: r for r in rows if r['event'] == 'message.received'}
        self.assertNotIn('original', values['a'])
        self.assertEqual(values['b']['original'], '好' * 256)
        self.assertTrue(values['b']['original_truncated'])
        self.assertNotIn('original', values['c'])
        self.assertTrue(summary['contains_body'])
        self.assertFalse(self.recorder.snapshot()['details_enabled'])

    def test_registered_secrets_never_reach_export_and_unrelated_files_stay_out(self):
        for secret in ('old-secret-fixture', 'new-secret-fixture'):
            self.recorder.register_secrets(secret)
        self.recorder.enable_details(True)
        self.recorder.record('message.received', message_id='safe', original='old-secret-fixture new-secret-fixture')
        (self.base / 'logs').mkdir(exist_ok=True)
        for name in ('credentials.dpapi', '.env', 'private.log', 'usage.sqlite3'):
            (self.base / 'logs' / name).write_text('private-file-fixture')
        summary, rows = self.export_rows()
        content = json.dumps([summary, rows])
        self.assertNotIn('old-secret-fixture', content)
        self.assertNotIn('new-secret-fixture', content)
        self.assertNotIn('private-file-fixture', content)
        self.assertIn('[redacted]', content)
        with zipfile.ZipFile(self.base / 'report.zip') as archive:
            self.assertTrue(set(archive.namelist()) <= {'summary.json', 'helper.log', 'helper.log.1', 'helper.log.2', 'helper.log.3', 'helper.log.4'})

    def test_unknown_fields_objects_and_raw_errors_are_rejected(self):
        self.assertFalse(self.recorder.record('arbitrary.event', text='secret'))
        self.recorder.record('runtime.exception', stage='worker', error_type='ValueError',
                             raw_exception='hidden-secret', credentials=object())
        _, rows = self.export_rows()
        row = next(r for r in rows if r['event'] == 'runtime.exception')
        self.assertNotIn('raw_exception', row)
        self.assertNotIn('credentials', row)

    def test_body_truncation_cannot_expose_partial_registered_secret(self):
        secret = 'SECRET-BOUNDARY-FIXTURE-NEVER-SHARE'
        self.recorder.register_secrets(secret)
        self.recorder.enable_details(True)
        self.recorder.record('message.received', original='x' * 245 + secret)
        _, rows = self.export_rows()
        body = next(r['original'] for r in rows if 'original' in r)
        self.assertNotIn('SECRET-', body)
        self.assertIn('[redacted]', body)

    def test_export_redacts_a_key_registered_after_its_text_was_written(self):
        self.recorder.enable_details(True)
        self.recorder.record('message.received', original='try NEW-SECRET-FIXTURE before switching')
        self.export_rows()
        self.recorder.register_secrets('NEW-SECRET-FIXTURE')
        _, rows = self.export_rows()
        self.assertNotIn('NEW-SECRET-FIXTURE', json.dumps(rows))
        self.assertIn('[redacted]', json.dumps(rows))

    def test_slow_writer_and_full_queue_do_not_wait_for_disk(self):
        entered, release = threading.Event(), threading.Event()
        original = self.recorder._write_line
        def slow(line):
            entered.set()
            release.wait(3)
            return original(line)
        with patch.object(self.recorder, '_write_line', side_effect=slow):
            self.recorder.record('app.start', stage='begin')
            self.assertTrue(entered.wait(2))
            started = time.perf_counter()
            accepted = [self.recorder.record('translation.result', job_id=str(i)) for i in range(400)]
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, .25)
            self.assertLessEqual(sum(accepted), 256)
            self.assertGreater(self.recorder.snapshot()['dropped'], 0)
            release.set()
        summary, _ = self.export_rows()
        self.assertGreater(summary['dropped'], 0)

    def test_writer_failure_is_visible_and_business_recording_does_not_raise(self):
        with patch.object(self.recorder, '_write_line', side_effect=OSError('sensitive-path')):
            self.recorder.record('app.start')
            deadline = time.monotonic() + 2
            while not self.recorder.snapshot()['write_error'] and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertTrue(self.recorder.snapshot()['write_error'])
            self.assertFalse(self.recorder.record('app.start'))
        with self.assertRaises(OSError):
            self.recorder.export_report(self.base / 'failed.zip')

    def test_rotation_limits_files_and_keeps_zip_readable(self):
        self.recorder.close()
        recorder = DiagnosticRecorder(self.base / 'rotated', max_bytes=500, enabled=True)
        self.addCleanup(recorder.close)
        for i in range(120):
            recorder.record('translation.result', job_id=str(i), outcome='ok', elapsed_ms=2.5)
        target = recorder.export_report(self.base / 'rotated.zip')
        with zipfile.ZipFile(target) as archive:
            self.assertIsNone(archive.testzip())
            logs = [n for n in archive.namelist() if n.endswith('.log') or '.log.' in n]
            self.assertLessEqual(len(logs), 5)
            self.assertGreater(len(logs), 1)
            for name in logs:
                for line in archive.read(name).decode().splitlines():
                    json.loads(line)

    def test_repeated_failure_is_aggregated(self):
        for _ in range(100):
            self.recorder.record('capture.state', source='lobby', outcome='error', error_type='OSError')
        _, rows = self.export_rows()
        matching = [r for r in rows if r['event'] == 'capture.state']
        self.assertLessEqual(len(matching), 2)
        self.assertEqual(sum(r.get('count', 1) for r in matching), 100)

    def test_export_excludes_expired_logs_without_deleting_unrelated_files(self):
        self.recorder.record('app.start')
        self.export_rows()
        old = time.time() - 8 * 24 * 3600
        path = self.base / 'logs' / 'helper.log'
        os.utime(path, (old, old))
        private = self.base / 'logs' / 'private.log'
        private.write_text('keep-private-fixture')
        os.utime(private, (old, old))
        _, rows = self.export_rows()
        self.assertFalse(any(row['event'] == 'app.start' for row in rows))
        self.assertTrue(private.is_file())

    def test_export_streams_retained_logs_instead_of_loading_them_all(self):
        directory=self.base/'logs'
        directory.mkdir(exist_ok=True)
        line=(json.dumps({'event':'message.received','original':'x'*3000})+'\n').encode()
        for suffix in ('','.1','.2','.3','.4'):
            (directory/('helper.log'+suffix)).write_bytes(line*600)
        tracemalloc.start()
        try:
            self.recorder.export_report(self.base/'large.zip')
            _,peak=tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak,3*1024*1024)
        with zipfile.ZipFile(self.base/'large.zip') as archive:
            self.assertTrue(json.loads(archive.read('summary.json'))['contains_body'])
            self.assertEqual(len(archive.namelist()),6)

    def test_default_off_has_no_worker_or_files_until_explicitly_enabled(self):
        recorder=DiagnosticRecorder(self.base/'off')
        self.addCleanup(recorder.close)
        self.assertFalse(recorder.record('app.start'))
        self.assertFalse(recorder.snapshot()['enabled'])
        self.assertIsNone(recorder.thread)
        self.assertFalse((self.base/'off').exists())
        recorder.set_enabled(True)
        self.assertTrue(recorder.record('app.start'))
        recorder.set_enabled(False)
        self.assertFalse(recorder.record('message.received',original='not-recorded'))
        target=recorder.export_report(self.base/'off.zip')
        with zipfile.ZipFile(target) as archive:
            content=archive.read('helper.log').decode()
        self.assertIn('app.start',content)
        self.assertNotIn('not-recorded',content)

    def test_timed_out_export_never_publishes_its_zip_later(self):
        recorder=DiagnosticRecorder(self.base/'timeout', enabled=True, export_timeout=.05)
        self.addCleanup(recorder.close)
        entered,release=threading.Event(),threading.Event()
        original=recorder._export
        def slow(request):
            entered.set()
            release.wait(2)
            return original(request)
        target=self.base/'timeout.zip'
        with patch.object(recorder,'_export',side_effect=slow):
            with self.assertRaises(OSError):recorder.export_report(target)
            self.assertTrue(entered.is_set())
            release.set()
            recorder.export_report(self.base/'after-timeout.zip')
        self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
