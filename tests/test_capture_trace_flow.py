"""采集合并到翻译的日志关联回归；仅使用可控来源与离线翻译引擎。"""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from hots_chat.acquisition.memory import GameProcess
from hots_chat.acquisition.persistent import PersistentChatSource
from hots_chat.controller import ChatController
from hots_chat.diagnostics import DiagnosticRecorder
from hots_chat.models import ChatEvent, TranslationResult
from test_persistent_chat import ControlledWorker, PROCESS


class OfflineTraceEngine:
    provider_id = 'offline-capture-trace'
    config_version = '1'

    def __init__(self):
        self.calls = []

    def translate(self, job):
        self.calls.append(job)
        return TranslationResult(job.job_id, '你好', 'en', None, 1)

    def close(self):
        pass


class CaptureTraceFlowTests(unittest.TestCase):
    private_sender = 'PRIVATE-SENDER-FIXTURE'
    private_body = 'PRIVATE-BODY-FIXTURE'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.recorder = DiagnosticRecorder(self.base / 'logs', enabled=True)
        self.addCleanup(self.recorder.close)
        self.processes = [PROCESS]
        self.source = PersistentChatSource(
            process_finder=lambda: list(self.processes),
            match_factory=ControlledWorker, lobby_factory=ControlledWorker,
            diagnostics=self.recorder)
        self.engine = OfflineTraceEngine()
        self.controller = ChatController(
            self.source, self.engine, 'offline-account',
            clock=lambda: datetime(2026, 10, 24, tzinfo=timezone.utc),
            diagnostics=self.recorder)
        self.addCleanup(self.controller.close)
        self.controller.start_receiving()
        self.match, self.lobby = self.source.match_source, self.source.lobby_source
        self.match.connect()
        self.lobby.connect()
        self.controller.drain_events()

    def child_chat(self, identifier, *, text='hello', channel='团队', generation=None):
        generation = self.match.generation if generation is None else generation
        self.match.send('chat', ChatEvent(identifier, generation, channel,
            self.private_sender, '00:01', text), generation=generation)

    def wait_idle(self):
        with self.controller.condition:
            self.assertTrue(self.controller.condition.wait_for(
                lambda: self.controller.idle, timeout=3))

    def rows(self):
        archive_path = self.recorder.export_report(self.base / 'capture-trace.zip')
        with zipfile.ZipFile(archive_path) as archive:
            return [json.loads(line) for name in archive.namelist()
                    if name.startswith('helper.log')
                    for line in archive.read(name).decode('utf-8').splitlines() if line]

    def test_child_message_remains_linked_through_merge_queue_and_translation(self):
        self.child_chat('child:1')
        self.controller.drain_events()
        self.wait_idle()
        results = [event.payload for event in self.controller.drain_events()
                   if event.kind == 'result']
        self.assertEqual([(row['original'], row['translated']) for row in results],
                         [('hello', '你好')])

        rows = self.rows()
        merged = next(row for row in rows if row.get('stage') == 'merge'
                      and row.get('outcome') == 'forwarded')
        self.assertEqual(merged['source'], 'match')
        self.assertEqual(merged['source_message_id'], 'child:1')
        self.assertEqual(merged['child_generation'], self.match.generation)
        self.assertEqual(merged['generation'], self.controller.generation)
        message_id = merged['message_id']
        self.assertTrue(message_id.startswith('match:'))
        queued = next(row for row in rows if row.get('stage') == 'queue'
                      and row.get('outcome') == 'queued')
        self.assertEqual(queued['message_id'], message_id)
        for event in ('message.received', 'translation.submitted',
                      'translation.request', 'translation.result'):
            self.assertTrue(any(row.get('event') == event and
                                row.get('message_id') == message_id for row in rows), event)
        summaries = [row for row in rows if row.get('event') == 'capture.state']
        self.assertNotIn(self.private_sender, json.dumps(summaries))

    def test_merge_rejections_explain_channel_generation_and_session_boundaries(self):
        self.child_chat('child:channel', text=self.private_body, channel='密语')
        previous = self.match.generation
        self.match.transition()
        self.child_chat('child:stale', text=self.private_body, generation=previous)
        self.processes[:] = [GameProcess(20, PROCESS.path, PROCESS.build)]
        self.lobby.observe()
        self.lobby.transition()
        self.child_chat('child:session', text=self.private_body)

        self.controller.drain_events()
        rows = [row for row in self.rows() if row.get('event') == 'capture.state'
                and row.get('stage') == 'merge' and row.get('outcome') == 'discarded']
        self.assertEqual({row['source_message_id']: row['reason'] for row in rows}, {
            'child:channel': 'channel_filtered',
            'child:stale': 'stale_generation',
            'child:session': 'session_mismatch',
        })
        summaries = json.dumps(rows)
        self.assertNotIn(self.private_sender, summaries)
        self.assertNotIn(self.private_body, summaries)
        self.assertEqual(self.engine.calls, [])

    def test_controller_rejections_have_fixed_reasons_without_copying_chat_text(self):
        self.controller.on_chat(ChatEvent('controller:stale', self.controller.generation + 1,
            '团队', self.private_sender, '00:01', self.private_body))
        self.controller.allowed_channels = {'队伍'}
        self.controller.on_chat(ChatEvent('controller:channel', self.controller.generation,
            '团队', self.private_sender, '00:01', self.private_body))
        rows = [row for row in self.rows() if row.get('event') == 'capture.state'
                and row.get('stage') == 'controller' and row.get('outcome') == 'discarded']
        self.assertEqual({row['message_id']: row['reason'] for row in rows}, {
            'controller:stale': 'generation_mismatch',
            'controller:channel': 'channel_filtered',
        })
        self.assertNotIn(self.private_sender, json.dumps(rows))
        self.assertNotIn(self.private_body, json.dumps(rows))
        self.assertEqual(self.engine.calls, [])

    def test_failed_diagnostic_output_does_not_interrupt_merge_queue_or_translation(self):
        with patch.object(self.recorder, 'record', side_effect=RuntimeError('offline writer failure')):
            self.child_chat('child:writer-failure')
            self.controller.drain_events()
            self.wait_idle()
            results = [event.payload for event in self.controller.drain_events()
                       if event.kind == 'result']
        self.assertEqual([row['translated'] for row in results], ['你好'])
        self.assertEqual([job.text for job in self.engine.calls], ['hello'])


if __name__ == '__main__':
    unittest.main()
