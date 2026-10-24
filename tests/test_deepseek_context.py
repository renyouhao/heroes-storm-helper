import copy
import json
import threading
import unittest

from hots_chat.controller import ChatController
from hots_chat.models import AppEvent, ChatEvent, TranslationJob, TranslationResult
from hots_chat.spam import AutomaticGuard
from hots_chat.translation_replay import replay_translation_frame


class Source:
    def stop(self):
        pass


class Engine:
    config_version = 'context-test-v1'

    def __init__(self, provider_id='deepseek', context_enabled=True):
        self.provider_id, self.context_enabled = provider_id, context_enabled
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def translate(self, job):
        self.calls.append(job)
        self.entered.set()
        self.release.wait(2)
        return TranslationResult(job.job_id, '译文:' + job.text, job.source, 'request-safe', 1)

    def close(self):
        pass


class Recorder:
    def __init__(self, enabled=True):
        self.enabled, self.rows = enabled, []

    def snapshot(self):
        return {'enabled': self.enabled}

    def record(self, event, **fields):
        if self.enabled:
            self.rows.append({'event': event, **copy.deepcopy(fields)})


class TranslationJobContextTests(unittest.TestCase):
    def test_existing_seven_arguments_keep_empty_context(self):
        job = TranslationJob('j', 'm', 0, 'hi', 'en', 'zh', 'auto')
        self.assertEqual(job.context, ())

    def test_replay_json_pairs_become_deeply_immutable(self):
        mutable = [['abc12345', 'hello'], ['def23456', 'back']]
        job = TranslationJob('j', 'm', 0, 'hi', 'en', 'zh', 'auto', mutable)
        mutable[0][1] = 'changed'
        self.assertEqual(job.context, (('abc12345', 'hello'), ('def23456', 'back')))
        with self.assertRaises(AttributeError):
            job.context = ()

    def test_context_rejects_more_than_three_and_invalid_pairs(self):
        for context in (None, [('s', 't')] * 4, [('s',)], [{'sender': 's', 'text': 't'}], [(1, 't')]):
            with self.assertRaisesRegex(ValueError, '^翻译上下文格式无效。$'):
                TranslationJob('j', 'm', 0, 'hi', 'en', 'zh', 'auto', context)


class DeepSeekContextTests(unittest.TestCase):
    def make_controller(self, provider='deepseek', context_enabled=True, recorder=None):
        engine = Engine(provider, context_enabled)
        controller = ChatController(Source(), engine, 'not-recorded-account', diagnostics=recorder)
        controller.guard.sender_limit = 1000
        self.addCleanup(controller.close)
        return controller, engine

    def chat(self, number, text, sender='Alice', channel='团队', generation=0):
        return ChatEvent(str(number), generation, channel, sender, '12:00', text)

    def wait_idle(self, controller):
        with controller.condition:
            self.assertTrue(controller.condition.wait_for(lambda: controller.idle, timeout=3))

    def test_only_previous_three_same_channel_events_are_frozen(self):
        controller, engine = self.make_controller()
        controller.paused = True
        for number in range(5):
            controller.on_chat(self.chat(number, 'prior ' + str(number), sender='Sender' + str(number)))
        controller.on_chat(self.chat('other', 'unrelated channel', channel='队伍'))
        controller.paused = False
        current = self.chat('current', 'translate current')
        controller.on_chat(current)
        self.wait_idle(controller)
        self.assertEqual([text for _, text in engine.calls[-1].context], ['prior 2', 'prior 3', 'prior 4'])
        self.assertEqual(len(engine.calls[-1].context), 3)
        expected_sender = AutomaticGuard._identity('团队', 'Sender2').hex()[:8]
        self.assertEqual(engine.calls[-1].context[0][0], expected_sender)
        self.assertNotIn('Sender2', str(engine.calls[-1].context))

    def test_whisper_inbound_outbound_share_only_known_contact(self):
        controller, _ = self.make_controller()
        controller.paused = True
        events = [self.chat('a1', 'Alice incoming', 'Alice', '密语'),
                  self.chat('b1', 'Bob incoming', 'Bob', '密语'),
                  self.chat('a2', 'Alice outgoing', '我 → Alice', '密语'),
                  self.chat('u1', 'unknown outgoing', '我 → 联系人未知', '密语'),
                  self.chat('team', 'same name other channel', 'Alice', '团队')]
        for event in events:
            controller.on_chat(event)
        current = self.chat('a3', 'current', 'Alice', '密语')
        self.assertEqual([event.message_id for event in controller._context_events(current)], ['a1', 'a2'])
        self.assertEqual(controller._conversation_key(events[0]), controller._conversation_key(events[2]))
        self.assertNotEqual(controller._conversation_key(events[0]), controller._conversation_key(events[1]))
        self.assertNotIn('Alice', controller._conversation_key(current))

    def test_oversized_original_only_message_cannot_reenter_cloud_as_context(self):
        controller, engine = self.make_controller()
        controller.paused = True
        controller.on_chat(self.chat('prior', 'useful prior'))
        oversized = '한' * 1366
        controller.on_chat(self.chat('oversized', oversized))
        controller.paused = False
        controller.on_chat(self.chat('current', 'translate current'))
        self.wait_idle(controller)
        self.assertEqual([text for _, text in engine.calls[-1].context], ['useful prior'])
        self.assertNotIn(oversized, str(engine.calls[-1]))

    def test_unknown_senders_and_contacts_never_link(self):
        controller, _ = self.make_controller()
        controller.paused = True
        for sender in ('发送者未知', '发言者未知', '联系人未知', '我 → 联系人未知', '', 'unknown'):
            previous = self.chat('prior-' + sender, 'prior', sender, '密语')
            controller.on_chat(previous)
            current = self.chat('new-' + sender, 'current', sender, '密语')
            self.assertIsNone(controller._conversation_key(current))
            self.assertEqual(controller._context_events(current), [])
        controller.on_chat(self.chat('unknown-team', 'unknown team prior', '发送者未知'))
        known = self.chat('known-team', 'known current', 'Alice')
        self.assertNotIn('unknown-team', [event.message_id for event in controller._context_events(known)])

    def test_retry_uses_only_history_before_message_never_future(self):
        controller, engine = self.make_controller()
        controller.paused = True
        controller.on_chat(self.chat('prior', 'before current'))
        controller.on_chat(self.chat('target', 'target current'))
        controller.on_chat(self.chat('future', 'future must not be used'))
        controller.paused = False
        controller.retry_message('target')
        self.wait_idle(controller)
        self.assertEqual([text for _, text in engine.calls[-1].context], ['before current'])
        self.assertNotIn('target current', str(engine.calls[-1].context))
        self.assertNotIn('future must not be used', str(engine.calls[-1].context))

    def test_queued_job_context_does_not_change_when_new_message_arrives(self):
        controller, engine = self.make_controller()
        engine.release.clear()
        self.addCleanup(engine.release.set)
        controller.on_chat(self.chat('first', 'first original'))
        self.assertTrue(engine.entered.wait(1))
        controller.on_chat(self.chat('second', 'second current'))
        with controller.condition:
            second = next(job for jobs in controller.subscribers.values() for job in jobs if job.message_id == 'second')
        self.assertEqual([text for _, text in second.context], ['first original'])
        controller.on_chat(self.chat('third', 'later third'))
        self.assertEqual([text for _, text in second.context], ['first original'])
        engine.release.set()
        self.wait_idle(controller)

    def test_aliyun_and_disabled_deepseek_keep_legacy_cache_keys(self):
        for provider, enabled in (('aliyun', True), ('deepseek', False)):
            with self.subTest(provider=provider):
                controller, engine = self.make_controller(provider, enabled)
                controller.paused = True
                controller.on_chat(self.chat('prior', 'do not upload prior'))
                controller.paused = False
                controller.on_chat(self.chat('current', 'same original'))
                self.wait_idle(controller)
                controller.on_chat(self.chat('again', 'same original'))
                self.wait_idle(controller)
                self.assertEqual(len(engine.calls), 1)
                self.assertEqual(engine.calls[0].context, ())
                self.assertTrue(all(len(key) == 5 for key in controller.cache.items))

    def test_manual_reply_never_guesses_private_conversation(self):
        controller, engine = self.make_controller()
        controller.paused = True
        controller.on_chat(self.chat('private', 'private original', 'Alice', '密语'))
        controller.paused = False
        controller.submit_manual('集合', 'en')
        self.wait_idle(controller)
        self.assertEqual(engine.calls[-1].kind, 'manual')
        self.assertEqual(engine.calls[-1].context, ())
        self.assertEqual(len(next(iter(controller.cache.items))), 5)

    def test_different_contexts_do_not_reuse_identical_original_cache(self):
        controller, engine = self.make_controller()
        controller.paused = True
        controller.on_chat(self.chat('prior-a', 'first situation'))
        controller.paused = False
        controller.on_chat(self.chat('first', 'same original'))
        self.wait_idle(controller)
        controller.paused = True
        controller.on_chat(self.chat('prior-b', 'different situation'))
        controller.paused = False
        controller.on_chat(self.chat('second', 'same original'))
        self.wait_idle(controller)
        self.assertEqual([job.text for job in engine.calls], ['same original', 'same original'])
        first_key, second_key = (controller._cache_key(job) for job in engine.calls)
        self.assertEqual(first_key[:5], second_key[:5])
        self.assertEqual(len(first_key), 6)
        self.assertIsInstance(first_key[-1], str)
        self.assertNotEqual(first_key[-1], second_key[-1])

    def test_generation_change_clears_context(self):
        controller, engine = self.make_controller()
        controller.paused = True
        controller.on_chat(self.chat('old', 'previous generation'))
        controller.handle_source_event(AppEvent('generation', 1, 'reset'))
        controller.paused = False
        controller.on_chat(self.chat('current', 'current generation', generation=1))
        self.wait_idle(controller)
        self.assertEqual(engine.calls[-1].context, ())
        self.assertEqual(list(controller.messages), ['current'])

    def test_result_provenance_follows_provider_and_preserves_cache_label(self):
        for provider, expected in (('deepseek', 'DeepSeek'), ('aliyun', '阿里云')):
            controller, _ = self.make_controller(provider, False)
            controller.on_chat(self.chat('current', 'same original'))
            self.wait_idle(controller)
            results = [event.payload for event in controller.drain_events() if event.kind == 'result']
            self.assertEqual(results[-1]['provenance'], expected)
            controller.on_chat(self.chat('again', 'same original'))
            self.wait_idle(controller)
            results = [event.payload for event in controller.drain_events() if event.kind == 'result']
            self.assertEqual(results[-1]['provenance'], '缓存')

    def test_deepseek_diagnostic_replay_matches_with_anonymous_private_context(self):
        recorder = Recorder(False)
        controller, engine = self.make_controller(recorder=recorder)
        controller.paused = True
        controller.on_chat(self.chat('a-out', 'earlier outgoing', '我 → AliceSecretNickname', '密语'))
        controller.on_chat(self.chat('b-in', 'other private conversation', 'BobSecretNickname', '密语'))
        controller.paused = False
        recorder.enabled = True
        controller.on_chat(self.chat('a-in', 'current incoming', 'AliceSecretNickname', '密语'))
        self.wait_idle(controller)
        controller.on_chat(self.chat('future', 'future incoming', 'AliceSecretNickname', '密语'))
        self.wait_idle(controller)
        controller.retry_message('a-in')
        self.wait_idle(controller)
        frames = [row['payload'] for row in recorder.rows if row['event'] == 'replay.frame']
        self.assertTrue(frames)
        rendered = json.dumps(frames, ensure_ascii=False)
        self.assertNotIn('AliceSecretNickname', rendered)
        self.assertNotIn('BobSecretNickname', rendered)
        self.assertNotIn('not-recorded-account', rendered)
        reports = [replay_translation_frame(json.loads(json.dumps(frame))) for frame in frames]
        self.assertTrue(all(report['status'] == 'matched' for report in reports), reports)
        on_chat = next(frame for frame in frames if frame['operation'] == 'on_chat')
        self.assertEqual(on_chat['before']['context_enabled'], True)
        self.assertEqual(set(on_chat['scope']['message_ids']), {'a-out', 'a-in'})
        self.assertEqual(len(on_chat['scope']['cache_key']), 6)
        self.assertEqual([text for _, text in engine.calls[0].context], ['earlier outgoing'])

    def test_old_replay_frames_without_context_metadata_remain_compatible(self):
        recorder = Recorder()
        controller, _ = self.make_controller('aliyun', False, recorder)
        controller.on_chat(self.chat('first', 'hello'))
        self.wait_idle(controller)
        frames = [row['payload'] for row in recorder.rows if row['event'] == 'replay.frame']
        self.assertTrue(frames)
        for frame in frames:
            old = copy.deepcopy(frame)
            old['scope'].pop('conversation_keys', None)
            for state in (old['before'], old['expected']['after']):
                state.pop('context_enabled', None)
                state.pop('conversation_keys', None)
            self.assertEqual(replay_translation_frame(old)['status'], 'matched', old['operation'])


if __name__ == '__main__':
    unittest.main()
