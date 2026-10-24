from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from hots_chat.controller import ChatController
from hots_chat.diagnostics import DiagnosticRecorder
from hots_chat.models import AppEvent, ChatEvent, TranslationResult
from hots_chat.translation.base import TranslationError
from tests.test_controller import Source


class FlowEngine:
    provider_id, config_version = 'synthetic', '1'
    def __init__(self):
        self.fail = False
        self.entered=threading.Event()
        self.release=threading.Event()
        self.release.set()
    def translate(self, job):
        self.entered.set()
        self.release.wait(3)
        if self.fail:
            raise TranslationError('timeout', 'SECRET-FIXTURE', provider_code='SDK.ReadTimeout')
        return TranslationResult(job.job_id, 'result-fixture', 'en', 'request-123', 2.5)
    def close(self):
        pass


class DiagnosticFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.recorder = DiagnosticRecorder(self.base / 'logs', enabled=True)
        self.addCleanup(self.recorder.close)
        self.now = datetime(2026, 10, 22, tzinfo=timezone.utc)
        self.engine = FlowEngine()
        self.controller = ChatController(Source(), self.engine, 'main', lambda: self.now,
                                         diagnostics=self.recorder)
        self.addCleanup(self.controller.close)

    def wait_idle(self):
        with self.controller.condition:
            self.assertTrue(self.controller.condition.wait_for(lambda: self.controller.idle, timeout=3))

    def rows(self):
        target = self.recorder.export_report(self.base / 'flow.zip')
        with zipfile.ZipFile(target) as archive:
            return [json.loads(line) for name in archive.namelist() if name.startswith('helper.log')
                    for line in archive.read(name).decode().splitlines()]

    def test_request_and_result_share_identifiers_without_usage_events(self):
        self.controller.on_chat(ChatEvent('fixture-message', 0, '团队', 'private-name', '00:01', 'hello'))
        self.wait_idle()
        rows = self.rows()
        events = {row['event'] for row in rows}
        self.assertTrue({'message.received', 'translation.submitted', 'translation.request',
                         'translation.result'} <= events)
        self.assertFalse(any(event.startswith('usage.') for event in events))
        submitted = next(r for r in rows if r['event'] == 'translation.submitted')
        self.assertEqual(submitted['message_id'], 'fixture-message')
        for kind in ('translation.request', 'translation.result'):
            self.assertTrue(any(r['event'] == kind and r['job_id'] == submitted['job_id'] for r in rows))
        result = next(r for r in rows if r['event'] == 'translation.result')
        self.assertEqual(result['request_id'], 'request-123')
        self.assertEqual(result['elapsed_ms'], 2.5)
        self.assertIn('hello', json.dumps(rows))
        self.assertEqual(submitted['original'], 'hello')
        self.assertEqual(result['translated'], 'result-fixture')
        self.assertNotIn('private-name', json.dumps(rows))

    def test_cloud_failure_keeps_safe_error_type_and_code(self):
        self.engine.fail = True
        self.controller.on_chat(ChatEvent('failed', 0, '密语', 'private-name', '00:01', 'hello'))
        self.wait_idle()
        rows = self.rows()
        row = next(r for r in rows if r['event'] == 'translation.failure')
        self.assertEqual(row['error_code'], 'timeout')
        self.assertEqual(row['provider_code'], 'SDK.ReadTimeout')
        self.assertNotIn('SECRET-FIXTURE', json.dumps(rows))

    def test_cache_and_non_translatable_message_have_terminal_reasons(self):
        self.controller.on_chat(ChatEvent('first', 0, '团队', 'p', '00:01', 'hello'))
        self.wait_idle()
        self.controller.on_chat(ChatEvent('cached', 0, '团队', 'p', '00:01', 'hello'))
        self.controller.on_chat(ChatEvent('emoji', 0, '团队', 'p', '00:01', '[表情]'))
        rows = self.rows()
        self.assertTrue(any(r.get('message_id') == 'cached' and r.get('outcome') == 'cache' for r in rows))
        self.assertTrue(any(r.get('message_id') == 'emoji' and r.get('outcome') == 'unsupported_or_symbol' for r in rows))

    def test_source_states_do_not_copy_arbitrary_payload_text(self):
        self.controller.handle_source_event(AppEvent('capture_state', 0, 'sensitive-failure-path'))
        self.controller.handle_source_event(AppEvent('error', 0, 'SECRET-FIXTURE'))
        rows = self.rows()
        content = json.dumps(rows)
        self.assertNotIn('sensitive-failure-path', content)
        self.assertNotIn('SECRET-FIXTURE', content)
        self.assertTrue(any(r['event'] == 'capture.state' for r in rows))

    def test_pausing_automatic_logs_queued_task_cancellation(self):
        self.engine.release.clear()
        self.addCleanup(self.engine.release.set)
        self.controller.on_chat(ChatEvent('active',0,'团队','p','00:01','first'))
        self.assertTrue(self.engine.entered.wait(1))
        self.controller.on_chat(ChatEvent('queued',0,'团队','p','00:01','second'))
        self.controller.pause_automatic(True)
        self.engine.release.set()
        self.wait_idle()
        self.assertTrue(any(r['event']=='translation.discarded' and r.get('message_id')=='queued'
                            and r.get('outcome')=='automatic_paused' for r in self.rows()))

    def test_ui_log_reports_job_mismatch_when_view_discards_old_manual_result(self):
        from hots_chat.ui.main_window import MainWindow
        from hots_chat.ui.viewmodel import AppViewModel
        window=MainWindow.__new__(MainWindow)
        window.vm=AppViewModel()
        window.vm.expected_job='new-job'
        old=AppEvent('manual_result',0,{'job_id':'old-job','translated':'old-result','provenance':'fixture'})
        window.loot_helper=SimpleNamespace(drain_status=lambda:[])
        window._account_events=lambda:None
        window.screen_ticks=0
        window.render_manual_preview=lambda:None
        window.controller=SimpleNamespace(drain_events=lambda:[old],diagnostics=self.recorder)
        window._pump_events()
        self.assertEqual(window.vm.copy_text,'')
        row=next(r for r in self.rows() if r['event']=='ui.apply')
        self.assertEqual(row['outcome'],'job_mismatch')

    def test_uncaught_hooks_record_safe_types_and_restore_previous_hooks(self):
        from hots_chat.diagnostic_hooks import DiagnosticHooks
        old_sys, old_thread = sys.excepthook, threading.excepthook
        calls = []
        root = SimpleNamespace(report_callback_exception=lambda *args: calls.append('tk'))
        old_tk = root.report_callback_exception
        hooks = DiagnosticHooks(self.recorder, root=root)
        try:
            try:
                raise ValueError('SECRET-FIXTURE C:/Users/private-name')
            except ValueError:
                root.report_callback_exception(*sys.exc_info())
            self.assertEqual(calls, ['tk'])
            rows = self.rows()
            self.assertTrue(any(r['event'] == 'runtime.exception' and r['error_type'] == 'ValueError' for r in rows))
            self.assertNotIn('SECRET-FIXTURE', json.dumps(rows))
            self.assertNotIn('private-name', json.dumps(rows))
        finally:
            hooks.close()
        self.assertIs(sys.excepthook, old_sys)
        self.assertIs(threading.excepthook, old_thread)
        self.assertIs(root.report_callback_exception, old_tk)

    def test_saved_and_replaced_keys_are_redacted_without_cloud_calls(self):
        from hots_chat.app import build_controller
        from hots_chat.config import Credentials
        from hots_chat.paths import AppPaths
        saved = SimpleNamespace(credentials=Credentials('fixture-key-id', 'fixture-old-secret'), account='fixture-account')
        store = SimpleNamespace(load=lambda: saved)
        paths = AppPaths(self.base / '.env', self.base / 'language.db', self.base / 'preferences.json')
        with patch('hots_chat.credentials.CredentialStore', return_value=store):
            controller = build_controller(paths, diagnostics=self.recorder)
        try:
            self.recorder.record('message.received', original='fixture-key-id fixture-old-secret')
            setup = controller.account_setup
            setup.identity = SimpleNamespace(resolve=lambda credentials:'fixture-account')
            setup.identify(Credentials('fixture-new-key', 'fixture-new-secret'))
            deadline = time.monotonic() + 2
            while setup.busy and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertFalse(setup.busy)
            self.recorder.record('message.received', original='fixture-old-secret fixture-new-secret')
            content = json.dumps(self.rows())
            for secret in ('fixture-key-id','fixture-old-secret','fixture-new-key','fixture-new-secret'):
                self.assertNotIn(secret, content)
            self.assertIn('configuration.progress', content)
        finally:
            controller.account_setup.close()
            controller.close()
