import copy
import threading
import time
import unittest
from unittest.mock import patch

from hots_chat.controller import ChatController
from hots_chat.models import AppEvent, ChatEvent, TranslationResult
from hots_chat.translation.base import TranslationError


class Recorder:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.rows = []

    def snapshot(self):
        return {'enabled': self.enabled}

    def record(self, event, **fields):
        if self.enabled:
            self.rows.append(dict(event=event, **copy.deepcopy(fields)))


class Source:
    def stop(self):
        pass


class Engine:
    provider_id = 'test'
    config_version = '1'

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.calls = []

    def translate(self, job):
        self.calls.append(job)
        self.entered.set()
        self.release.wait(2)
        return TranslationResult(job.job_id, '译文:' + job.text, 'en', 'request-1', 3)

    def close(self):
        pass


class TranslationReplayTests(unittest.TestCase):
    def setUp(self):
        self.recorder = Recorder()
        self.engine = Engine()
        self.controller = ChatController(Source(), self.engine, 'DO-NOT-LOG-ACCOUNT', diagnostics=self.recorder)
        self.addCleanup(self.controller.close)

    def chat(self, number, text='hello', generation=0, channel='团队'):
        return ChatEvent(str(number), generation, channel, 'sender', '12:00', text)

    def wait_idle(self):
        deadline = time.monotonic() + 3
        while not self.controller.idle and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(self.controller.idle)

    def frames(self):
        return [row['payload'] for row in self.recorder.rows if row['event'] == 'replay.frame']

    def replay_all(self):
        from hots_chat.translation_replay import replay_translation_frame
        frames = self.frames()
        self.assertTrue(frames, '开启诊断应记录可离线执行的翻译帧')
        with patch.object(Engine, 'translate', side_effect=AssertionError('不得调用云翻译')):
            reports = [replay_translation_frame(frame) for frame in frames]
        self.assertTrue(all(report['status'] == 'matched' for report in reports), reports)
        return frames

    def test_chat_normalization_dispatch_completion_and_cache_replay_offline(self):
        self.controller.on_chat(self.chat(1, 'hello\r\nworld'))
        self.wait_idle()
        self.controller.on_chat(self.chat(2, 'hello\r\nworld'))
        self.wait_idle()
        frames = self.replay_all()
        self.assertEqual(len(self.engine.calls), 1)
        self.assertTrue(any(frame['operation'] == 'cloud_complete' for frame in frames))
        self.assertEqual(frames[0]['input']['args'][0]['text'], 'hello\r\nworld')
        self.assertIn('译文:', str(frames))
        self.assertNotIn('DO-NOT-LOG-ACCOUNT', str(frames))

    def test_pauses_channels_retry_and_stale_generation_replay(self):
        self.engine.release.clear()
        self.controller.on_chat(self.chat(1))
        self.assertTrue(self.engine.entered.wait(1))
        self.controller.on_chat(self.chat(2, 'another'))
        self.controller.pause_automatic(True)
        self.controller.retry_message('2')
        self.controller.set_channels({'团队'})
        self.controller.on_chat(self.chat(3, channel='大厅'))
        self.controller.hold_configuration(True)
        self.controller.handle_source_event(AppEvent('generation', 1, 'reconnect'))
        self.engine.release.set()
        self.wait_idle()
        self.replay_all()

    def test_manual_local_resolver_output_is_recorded_as_external_input(self):
        self.controller.set_local_resolver(lambda text, target: 'gather up')
        self.controller.submit_manual('集合', 'en')
        self.assertEqual(self.engine.calls, [])
        self.replay_all()

    def test_replay_detects_changed_live_routing_rule_and_tampered_expected(self):
        from hots_chat.translation_replay import replay_translation_frame
        self.controller.on_chat(self.chat(1))
        self.wait_idle()
        frame = next(frame for frame in self.frames() if frame['operation'] == 'on_chat')
        with patch('hots_chat.controller.incoming_translation_route', return_value='unsupported'):
            self.assertEqual(replay_translation_frame(frame)['status'], 'mismatch')
        tampered = copy.deepcopy(frame)
        tampered['expected']['events'] = []
        self.assertEqual(replay_translation_frame(tampered)['status'], 'mismatch')

    def test_recording_can_start_mid_session_and_frames_are_self_contained(self):
        self.recorder.enabled = False
        self.controller.on_chat(self.chat(1))
        self.wait_idle()
        self.recorder.enabled = True
        self.controller.retry_message('1')
        self.wait_idle()
        self.replay_all()

    def test_disabled_recording_never_snapshots_business_state(self):
        self.recorder.enabled = False
        with patch('hots_chat.translation_replay.snapshot_state', side_effect=AssertionError('禁用时不能捕获')):
            self.controller.on_chat(self.chat(1))
            self.wait_idle()
        self.assertEqual(self.frames(), [])

    def test_invalid_and_partial_frames_are_not_reported_as_success(self):
        from hots_chat.translation_replay import replay_translation_frame
        self.assertEqual(replay_translation_frame({'schema': 1, 'complete': False})['status'], 'incomplete')
        self.assertEqual(replay_translation_frame({'schema': 999})['status'], 'invalid')
        self.assertEqual(replay_translation_frame({'schema': 1, 'complete': True, 'operation': '__import__'})['status'], 'invalid')

    def test_sender_guard_alias_keeps_rate_decisions_without_logging_nickname(self):
        self.engine.release.clear()
        for number in range(4):
            self.controller.on_chat(self.chat(number, 'hello ' + str(number)))
        self.engine.release.set()
        self.wait_idle()
        frames = self.replay_all()
        self.assertTrue(any(event['kind'] == 'suppressed' for frame in frames for event in frame['expected']['events']))
        self.assertNotIn("'sender': 'sender'", str(frames))

    def test_pending_expiry_and_request_coalescing_replay_with_recorded_clock(self):
        ticks = [100.0]
        self.controller._monotonic_source = lambda: ticks[0]
        self.engine.release.clear()
        self.controller.on_chat(self.chat(1, 'first'))
        self.assertTrue(self.engine.entered.wait(1))
        self.controller.on_chat(self.chat(2, 'shared'))
        self.controller.on_chat(self.chat(3, 'shared'))
        ticks[0] += 9
        self.controller.submit_manual(text='集合', target='en')
        self.engine.release.set()
        self.wait_idle()
        self.replay_all()
        self.assertEqual(len(self.engine.calls), 2)

    def test_cloud_errors_are_external_inputs_without_raw_exception_message(self):
        with patch.object(self.engine, 'translate', side_effect=TranslationError('timeout', 'secret-error-path')):
            self.controller.on_chat(self.chat(1))
            self.wait_idle()
        frames = self.replay_all()
        self.assertNotIn('secret-error-path', str(frames))
        self.assertTrue(any(event['kind'] == 'error' for frame in frames for event in frame['expected']['events']))

    def test_large_unrelated_cache_is_not_copied_and_oversized_capture_is_partial(self):
        for index in range(150):
            key = ('test', '1', 'irrelevant ' + str(index), 'en', 'zh')
            self.controller.cache.put(key, TranslationResult(str(index), 'large-result' * 500, 'en', None, 1))
        self.controller.on_chat(self.chat(1))
        self.wait_idle()
        frames = self.replay_all()
        self.assertNotIn('irrelevant ', str(frames))
        self.controller.on_chat(self.chat(2, 'too-big ' * 3000))
        from hots_chat.translation_replay import replay_translation_frame
        self.assertEqual(replay_translation_frame(self.frames()[-1])['status'], 'incomplete')

    def test_recording_failures_do_not_change_translation_output(self):
        with patch.object(self.recorder, 'record', side_effect=RuntimeError('disk unavailable')):
            self.controller.on_chat(self.chat(1))
            self.wait_idle()
        self.assertTrue(any(event.kind == 'result' for event in self.controller.drain_events()))

    def test_cache_projection_does_not_falsely_report_eviction_of_other_language(self):
        self.controller.cache.max_items = 2
        self.controller.cache.put(('test', '1', 'hello', 'ko', 'zh'), TranslationResult('old', 'old', 'ko', None, 1))
        self.controller.cache.put(('test', '1', 'other', 'en', 'zh'), TranslationResult('other', 'other', 'en', None, 1))
        self.controller.on_chat(self.chat(1))
        self.wait_idle()
        self.replay_all()

    def test_report_distinguishes_reproduced_failure_from_success(self):
        from hots_chat.translation_replay import replay_translation_frame
        self.controller.pause_automatic(True)
        self.controller.on_chat(self.chat(1))
        frame = self.frames()[-1]
        report = replay_translation_frame(frame)
        self.assertEqual(report['status'], 'matched')
        self.assertEqual(report['outcome'], 'skipped')
        self.assertEqual(report['reason'], 'automatic_paused')
        self.assertEqual(report['message_id'], '1')

    def test_source_error_dict_does_not_capture_arbitrary_debug_payload(self):
        self.controller.handle_source_event(AppEvent('error', 0,
            {'message_id': 'known-message', 'error': 'private-error-text', 'debug_path': 'private-path'}))
        frames = self.replay_all()
        self.assertNotIn('private-error-text', str(frames))
        self.assertNotIn('private-path', str(frames))

    def test_concurrent_pause_is_not_mistaken_for_nested_recording(self):
        entered, release, pause_waiting = threading.Event(), threading.Event(), threading.Event()
        original_lock = self.controller.condition._lock

        class ObservedLock:
            def __enter__(self):
                if threading.current_thread().name == 'replay-test-pause':
                    pause_waiting.set()
                return original_lock.__enter__()

            def __exit__(self, *args):
                return original_lock.__exit__(*args)

        def local_lookup(text, target):
            entered.set()
            release.wait(2)
            return 'gather'

        self.controller.condition._lock = ObservedLock()
        self.controller.set_local_resolver(local_lookup)
        submitter = threading.Thread(target=lambda: self.controller.submit_manual('集合', 'en'))
        pauser = threading.Thread(target=lambda: self.controller.pause_translation(True), name='replay-test-pause')
        try:
            submitter.start()
            self.assertTrue(entered.wait(1))
            pauser.start()
            self.assertTrue(pause_waiting.wait(1))
        finally:
            release.set()
            submitter.join(2)
            if pauser.ident is not None:
                pauser.join(2)
            self.controller.condition._lock = original_lock
        self.assertFalse(submitter.is_alive())
        self.assertFalse(pauser.is_alive())
        self.assertTrue(self.controller.paused)
        self.assertEqual([frame['operation'] for frame in self.frames()], ['submit_manual', 'pause_translation'])
        self.replay_all()

    def test_same_thread_configuration_pause_remains_a_single_operation(self):
        self.controller.hold_configuration(True)
        self.assertEqual([frame['operation'] for frame in self.frames()], ['hold_configuration'])
        self.replay_all()

    def test_full_output_queue_marks_recording_incomplete_and_preserves_raw_input(self):
        from hots_chat.translation_replay import replay_translation_frame
        for _ in range(self.controller.events.maxsize):
            self.controller.events.put_nowait(AppEvent('status', 0, 'queued'))
        self.controller.automatic_paused = True
        raw = 'hello <img src="game-emoticon">'
        self.controller.on_chat(self.chat(1, raw))
        self.assertTrue(self.controller.overflow)
        frame = next(frame for frame in self.frames() if frame['operation'] == 'on_chat')
        self.assertEqual(replay_translation_frame(frame)['status'], 'incomplete')
        self.assertEqual(frame['reason'], 'output_queue_full')
        self.assertEqual(frame['input']['args'][0]['text'], raw)

    def test_full_source_queue_records_an_explicit_replay_gap(self):
        from hots_chat.translation_replay import replay_translation_frame
        for _ in range(self.controller.source_events.maxsize):
            self.controller.source_events.put_nowait(AppEvent('capture_state', 0, None))
        self.controller._source_emit(AppEvent('chat', 0, self.chat(1)))
        self.assertTrue(self.controller.overflow)
        frames = self.frames()
        self.assertTrue(frames, '源队列溢出必须留下可导出的诊断证据')
        self.assertEqual(frames[-1]['reason'], 'source_queue_full')
        self.assertEqual(replay_translation_frame(frames[-1])['status'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
