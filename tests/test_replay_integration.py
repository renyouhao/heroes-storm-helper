"""实际录制→ZIP→真实业务离线回放，所有外部边界均为替身。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hots_chat.controller import ChatController
from hots_chat.diagnostics import DiagnosticRecorder
from hots_chat.models import ChatEvent
from hots_chat.replay import replay_archive
from hots_chat.talent import TalentConfig, TalentSession
from hots_chat.loot_native import GameTarget
from tests import test_helper_replay as helpers
from tests import test_diagnostic_flow as flows
from tests.test_controller import Source


class ReplayIntegrationTests(unittest.TestCase):
    def test_three_components_export_and_replay_without_any_external_calls(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        recorder = DiagnosticRecorder(base/'logs', enabled=True)
        self.addCleanup(recorder.close)
        controller = ChatController(Source(), flows.FlowEngine(), 'fake', diagnostics=recorder)
        self.addCleanup(controller.close)
        controller.on_chat(ChatEvent('message-1',0,'团队','private-sender','00:01','hello world'))
        with controller.condition:
            self.assertTrue(controller.condition.wait_for(lambda:controller.idle,timeout=3))
        controller.on_chat(ChatEvent('message-2',0,'团队','private-sender','00:02','hello world'))
        controller.submit_manual('集合','en')
        with controller.condition:
            self.assertTrue(controller.condition.wait_for(lambda:controller.idle,timeout=3))
        session = TalentSession(helpers.Backend(),config=TalentConfig(True),diagnostics=recorder)
        session.poll(helpers.talent_snapshot(),1.,GameTarget(22,84517),validate=lambda:True)
        session.poll(helpers.talent_snapshot(),5.,GameTarget(22,84517))
        fixture = helpers.HelperReplayTests()
        board, hit, context = fixture.board(recorder)
        action = fixture.ready(board,hit,context)
        board.dispatch(action,lambda *_args,**_kwargs:'sent',hit,context,10.2)
        path = recorder.export_report(base/'recording.zip')
        with patch('socket.socket',side_effect=AssertionError('offline replay cannot use network')), \
             patch('hots_chat.acquisition.memory.ReadOnlyGame',side_effect=AssertionError('no real game')):
            report = replay_archive(path)
        self.assertEqual(report['status'],'matched',report)
        self.assertTrue(report['complete'],report)
        self.assertEqual(set(report['components']),{'translation','talent','board'})
        self.assertGreaterEqual(report['frames'],10)
