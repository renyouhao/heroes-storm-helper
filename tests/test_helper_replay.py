"""Offline replay uses the actual helper sessions and never native senders."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from hots_chat.acquisition.talent_source import TalentChoice, TalentSnapshot, TalentTier
from hots_chat.loot_native import GameTarget
from hots_chat.talent import TalentConfig, TalentSession
from hots_chat.talent_broadcast import (
    BroadcastSendResult, InputContext, StatHitSnapshot, TalentBroadcastConfig,
    TalentBroadcastSession,
)
from hots_chat.helper_replay import record_board_hit_test, replay_helper_frame


class Recorder:
    def __init__(self, enabled=True):
        self.enabled, self.frames = enabled, []

    def snapshot(self):
        return {'enabled': self.enabled}

    def record(self, event, **fields):
        if event == 'replay.frame':
            self.frames.append(json.loads(json.dumps(fields)))


class Backend:
    def __init__(self):
        self.calls = 0
        self.outcome = 'sent'

    def select_favorite(self, target, *, allowed, validate=None):
        self.calls += 1
        if not allowed() or (validate is not None and not validate()) or not allowed():
            return 'deferred'
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def talent_snapshot():
    return TalentSnapshot(84517, (84517, 998877665544, 0x7FF055AA, 'match-secret'),
        'Jaina', 1, (TalentTier(1, 1, -1, True,
            (TalentChoice(0, 'FrostArmor', True),)),), 1, 'available')


class HelperReplayTests(unittest.TestCase):
    def assert_replays(self, recorder):
        self.assertTrue(recorder.frames)
        for frame in recorder.frames:
            result = replay_helper_frame(frame['component'], frame['operation'], frame['payload'])
            self.assertTrue(result['matched'], result)
            self.assertEqual(result['mismatches'], [])

    def test_talent_poll_records_callbacks_and_replays_real_send_decision(self):
        recorder, backend = Recorder(), Backend()
        session = TalentSession(backend, diagnostics=recorder)
        session.configure(TalentConfig(True))
        session.poll(talent_snapshot(), 1., GameTarget(778899, 84517), validate=lambda: True)
        session.poll(talent_snapshot(), 5., GameTarget(778899, 84517))
        self.assertEqual(backend.calls, 1)
        self.assertEqual(session.status.state, 'blocked')
        self.assert_replays(recorder)
        text = json.dumps(recorder.frames)
        for private in ('84517', '778899', '998877665544', 'match-secret', str(0x7FF055AA)):
            self.assertNotIn(private, text)

    def test_talent_backend_failure_and_validate_false_are_replayed(self):
        for outcome, validate in ((OSError('secret-path'), True), ('sent', False)):
            recorder, backend = Recorder(), Backend()
            backend.outcome = outcome
            session = TalentSession(backend, config=TalentConfig(True), diagnostics=recorder)
            session.poll(talent_snapshot(), 1., GameTarget(22, 84517), validate=lambda: validate)
            self.assert_replays(recorder)
            self.assertNotIn('secret-path', json.dumps(recorder.frames))

    def test_disabled_capture_is_lazy_and_can_start_in_middle_of_attempt(self):
        recorder, backend = Recorder(False), Backend()
        session = TalentSession(backend, config=TalentConfig(True), diagnostics=recorder)
        session.poll(talent_snapshot(), 1., GameTarget(22, 84517))
        self.assertEqual(recorder.frames, [])
        recorder.enabled = True
        session.poll(talent_snapshot(), 5., GameTarget(22, 84517))
        self.assert_replays(recorder)
        self.assertEqual(len(recorder.frames), 1)

    def test_callback_side_effect_disabling_session_is_replayed(self):
        recorder, backend = Recorder(), Backend()
        session = TalentSession(backend, config=TalentConfig(True), diagnostics=recorder)
        def allowed():
            session.configure(TalentConfig(False))
            return True
        session.poll(talent_snapshot(), 1., GameTarget(22, 84517), allowed=allowed)
        self.assertEqual(session.status.state, 'disabled')
        self.assert_replays(recorder)

    def board(self, recorder):
        session = TalentBroadcastSession(double_click_ms=500, double_click_size=(4, 4),
            config=TalentBroadcastConfig(True), diagnostics=recorder)
        hit = StatHitSnapshot(84517, 778899, 'match-secret', 3, 'enemy', 'hero_damage', 123,
            (100, 200, 160, 260), 10., 11., True, True, 'Player', 'Jaina')
        context = InputContext(hit.pid, hit.hwnd, hit.match_token, True)
        return session, hit, context

    def ready(self, session, hit, context):
        for now in (10., 10.2):
            session.mouse(120, 220, True, hit, context, now, event_at=now)
            session.mouse(120, 220, False, None, InputContext(), now, event_at=now)
        return session.tick(hit, context, 10.2)

    def test_board_click_tick_and_dispatch_replay_pending_action_identity(self):
        recorder = Recorder()
        session, hit, context = self.board(recorder)
        action = self.ready(session, hit, context)
        calls = []
        def sender(action, *, allowed):
            calls.append(action)
            self.assertTrue(allowed())
            self.assertTrue(allowed())
            return BroadcastSendResult('sent', 'ok')
        session.dispatch(action, sender, hit, context, 10.2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(session.status.state, 'sent')
        self.assert_replays(recorder)

    def test_board_middle_enable_after_pending_and_error_result(self):
        recorder = Recorder(False)
        session, hit, context = self.board(recorder)
        action = self.ready(session, hit, context)
        recorder.enabled = True
        session.dispatch(action, lambda *_args, **_kw: BroadcastSendResult('error', 'input_busy', 'busy'),
            hit, context, 10.2)
        self.assertEqual(len(recorder.frames), 1)
        self.assert_replays(recorder)

    def test_board_stale_mouse_cancel_and_stop_configuration_replay(self):
        recorder = Recorder()
        session, hit, context = self.board(recorder)
        session.mouse(120, 220, True, hit, context, 10.4, event_at=10.)
        session.cancel('test reason')
        session.stop()
        self.assert_replays(recorder)

    def test_tampered_expectation_is_a_mismatch(self):
        recorder = Recorder()
        session, hit, context = self.board(recorder)
        session.mouse(120, 220, True, hit, context, 10.)
        frame = deepcopy(recorder.frames[-1])
        frame['payload']['expected']['result'] = True
        result = replay_helper_frame(frame['component'], frame['operation'], frame['payload'])
        self.assertFalse(result['matched'])
        self.assertIn('result', ' '.join(result['mismatches']))

    def test_untrusted_or_incomplete_payload_is_rejected_without_execution(self):
        for payload in ({'schema': 99}, {'schema': 1, 'incomplete': 'size_limit'},
                        {'schema': 1, 'before': {'type': '__import__'}}):
            result = replay_helper_frame('board', 'mouse', payload)
            self.assertFalse(result['matched'])
            self.assertTrue(result['mismatches'])
        self.assertFalse(replay_helper_frame('board', '__getattribute__', {})['matched'])

    def test_recorder_exception_cannot_change_session_behavior(self):
        class Broken(Recorder):
            def record(self, *_args, **_kwargs):
                raise RuntimeError('disk')
        backend = Backend()
        session = TalentSession(backend, config=TalentConfig(True), diagnostics=Broken())
        self.assertEqual(session.poll(talent_snapshot(), 1., GameTarget(22, 84517)).state, 'confirming')
        self.assertEqual(backend.calls, 1)

    def test_unsupported_callback_result_does_not_change_actual_decision(self):
        recorder, backend = Recorder(), Backend()
        session = TalentSession(backend, config=TalentConfig(True), diagnostics=recorder)
        self.assertEqual(session.poll(talent_snapshot(), 1., GameTarget(22, 84517),
            allowed=lambda: object()).state, 'confirming')
        self.assertEqual(backend.calls, 1)
        self.assertTrue(recorder.frames[-1]['payload'].get('incomplete'))

    def test_board_hit_test_replays_no_hit_unique_and_overlap(self):
        from hots_chat.acquisition.board_source import BoardSnapshot
        from hots_chat.board_service import hit_test
        for copies, point in ((0, (120, 220)), (1, (120, 220)),
                              (2, (120, 220)), (1, (200, 300))):
            recorder = Recorder()
            _session, hit, context = self.board(recorder)
            snapshot = BoardSnapshot(hit.pid, hit.hwnd, hit.match_token,
                'available', hits=(hit,) * copies)
            record_board_hit_test(recorder, snapshot, point, context, hit_test(snapshot, *point))
            self.assert_replays(recorder)

    def test_full_sender_fences_survive_real_recorder_export(self):
        from pathlib import Path
        import tempfile
        from hots_chat.diagnostics import DiagnosticRecorder
        from hots_chat.replay import replay_archive
        from tests.test_board_sender import BoardSenderTests
        fixture = BoardSenderTests()
        fixture.setUp()
        with tempfile.TemporaryDirectory() as directory:
            recorder = DiagnosticRecorder(Path(directory) / 'logs', enabled=True)
            try:
                session, hit, _context = self.board(recorder)
                hit = replace(hit, pid=fixture.target.pid, hwnd=fixture.target.hwnd, match_token='match')
                context = InputContext(hit.pid, hit.hwnd, hit.match_token, True)
                action = self.ready(session, hit, context)
                self.assertEqual(session.dispatch(action, fixture.sender.send_result,
                    hit, context, 10.2).state, 'sent')
                path = recorder.export_report(Path(directory) / 'diagnostics.zip')
                result = replay_archive(path)
                self.assertEqual(result['status'], 'matched', result)
            finally:
                recorder.close()

    def test_talent_replay_detects_changed_backend_target(self):
        from hots_chat.helper_replay import invoke_talent_backend
        recorder = Recorder()
        session = TalentSession(Backend(), config=TalentConfig(True), diagnostics=recorder)
        session.poll(talent_snapshot(), 1., GameTarget(22, 84517))
        self.assert_replays(recorder)
        frame = recorder.frames[-1]
        def wrong_target(session, _target, options):
            return invoke_talent_backend(session, GameTarget(999999, 999999), options)
        with patch('hots_chat.talent.invoke_talent_backend', side_effect=wrong_target):
            report = replay_helper_frame(frame['component'], frame['operation'], frame['payload'])
        self.assertFalse(report['matched'], report)
        self.assertIn('backend.input', report['mismatches'])

    def test_board_replay_detects_changed_sender_message(self):
        from hots_chat.helper_replay import _Replay
        recorder = Recorder()
        session, hit, context = self.board(recorder)
        action = self.ready(session, hit, context)
        session.dispatch(action, lambda *_args, **_options: 'sent', hit, context, 10.2)
        self.assert_replays(recorder)
        frame = recorder.frames[-1]
        original_transport = _Replay.transport
        def wrong_message(replay, kind, function, value, options):
            if kind == 'sender':
                value = replace(value, message='different message')
            return original_transport(replay, kind, function, value, options)
        with patch.object(_Replay, 'transport', wrong_message):
            report = replay_helper_frame(frame['component'], frame['operation'], frame['payload'])
        self.assertFalse(report['matched'], report)
        self.assertIn('sender.input', report['mismatches'])


if __name__ == '__main__':
    unittest.main()
