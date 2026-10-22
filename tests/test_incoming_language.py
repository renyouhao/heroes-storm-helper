from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest

from hots_chat.config import Credentials
from hots_chat.controller import ChatController
from hots_chat.models import ChatEvent, TranslationJob, TranslationResult
from hots_chat import routing
from hots_chat.routing import should_translate
from hots_chat.translation.aliyun import AliyunEngine
from hots_chat.translation.base import TranslationError
from hots_chat.usage import BudgetLedger


class _Source:
    def stop(self):
        pass


class _Client:
    def __init__(self):
        self.calls = []
        self.fail_next = False

    def translate_general_with_options(self, request, runtime):
        self.calls.append((request, runtime))
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError('simulated network failure')
        translated = '不好' if request.source_language == 'en' else '洗澡'
        return NS(body=NS(code=200, data=NS(
            translated=translated, detected_language=request.source_language),
            request_id='fictional-request'))


class IncomingLanguageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.now = lambda: datetime(2026, 10, 21, tzinfo=timezone.utc)
        self.ledger = BudgetLedger(Path(tmp.name) / 'usage.db', 10000)
        self.ledger.confirm_baseline('fictional', '2026-10', 0, self.now())
        self.client = _Client()
        engine = AliyunEngine(Credentials('fictional', 'fictional-secret'),
                              client_factory=lambda config: self.client)
        self.controller = ChatController(_Source(), engine, self.ledger,
                                         'fictional', self.now)
        self.addCleanup(self.controller.close)

    def receive(self, identifier, text):
        event = ChatEvent(str(identifier), 0, '团队', 'sender-' + str(identifier), '1:00', text)
        self.controller.on_chat(event)
        with self.controller.condition:
            self.assertTrue(self.controller.condition.wait_for(
                lambda: self.controller.idle, timeout=3))
        return self.controller.drain_events()

    def wait_idle(self):
        with self.controller.condition:
            self.assertTrue(self.controller.condition.wait_for(
                lambda: self.controller.idle, timeout=3))

    def test_source_route_is_pure_and_limits_supported_scripts(self):
        for text, expected in (('BAD', 'en'), ('坏 BAD 😀', 'en'), ('BAD ✌️', 'en'),
                               ('BAD 1️⃣', 'en'), ('안녕 1️⃣', 'ko'),
                               ('가자 BAD', 'ko'), ('ㅋㅋ', 'ko'),
                               ('\ua960', 'ko'), ('\uffa1', 'ko'),
                               ('BAD \U00020000', 'en'),
                               ('BAD schön', None), ('BAD scho\u0308n', None),
                               ('가자 あ', None), ('BAD Привет', None), ('가자 Привет', None),
                               ('集合', None), ('123 😀', None)):
            with self.subTest(text=text):
                self.assertEqual(routing.incoming_source_language(text), expected)

    def test_bad_uses_english_once_and_publishes_english_translation(self):
        events = self.receive('bad', 'BAD')
        results = [event.payload for event in events if event.kind == 'result']
        self.assertEqual([result['translated'] for result in results], ['不好'])
        self.assertEqual([result['original'] for result in results], ['BAD'])
        self.assertEqual(len(self.client.calls), 1)
        request, runtime = self.client.calls[0]
        self.assertEqual((request.source_language, request.target_language), ('en', 'zh'))
        self.assertEqual(request.source_text, 'BAD')
        self.assertFalse(runtime.autoretry)
        self.assertEqual(self.ledger.snapshot('fictional', self.now()).local_reserved, 3)

    def test_supported_text_chooses_english_or_korean_without_changing_body(self):
        for index, (text, expected) in enumerate((
                ('hello 123!? 😀', 'en'), ('集合 BAD 2!', 'en'),
                ('안녕하세요', 'ko'), ('BAD 가자 中文! 2', 'ko'),
                ('ㅋㅋ gg', 'ko'), ('\u1100 hi', 'ko'))):
            with self.subTest(text=text):
                events = self.receive(index, text)
                self.assertTrue(any(event.kind == 'result' for event in events))
                request = self.client.calls[-1][0]
                self.assertEqual(request.source_language, expected)
                self.assertEqual(request.source_text, text)
        self.assertEqual(len(self.client.calls), 6)
        self.assertFalse(any(request.source_language == 'auto'
                             for request, _ in self.client.calls))

    def test_unsupported_letters_retain_original_without_cloud_or_usage(self):
        for index, text in enumerate(('BAD 日本語かな', 'BAD Привет', '가자 Привет', 'BAD schön',
                                      '가자 schön', 'BAD α', 'BAD é', 'ＢＡＤ')):
            with self.subTest(text=text):
                events = self.receive(index, text)
                self.assertEqual([event.payload.text for event in events if event.kind == 'chat'], [text])
                self.assertFalse(any(event.kind == 'result' for event in events))
                self.assertFalse(should_translate(text))
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.ledger.snapshot('fictional', self.now()).local_reserved, 0)

    def test_chinese_numbers_and_emoji_do_not_start_translation(self):
        for index, text in enumerate(('', '集合', '123', '😀!?', '中文 123 😀')):
            with self.subTest(text=text):
                events = self.receive(index, text)
                self.assertTrue(any(event.kind == 'chat' for event in events))
                self.assertFalse(should_translate(text))
        self.assertEqual(self.client.calls, [])

    def test_retry_reuses_language_route_after_failure_without_automatic_retry(self):
        for index, (text, expected) in enumerate((('BAD', 'en'), ('가자 BAD', 'ko'))):
            with self.subTest(text=text):
                self.client.fail_next = True
                events = self.receive(index, text)
                self.assertTrue(any(event.kind == 'error' for event in events))
                self.assertEqual(len(self.client.calls), index * 2 + 1)
                self.controller.retry_message(str(index))
                self.wait_idle()
                results = self.controller.drain_events()
                self.assertTrue(any(event.kind == 'result' for event in results))
                self.assertEqual([request.source_language for request, _ in self.client.calls[-2:]],
                                 [expected, expected])
        self.assertEqual(len(self.client.calls), 4)
        self.assertEqual(self.ledger.snapshot('fictional', self.now()).local_reserved, 18)

    def test_retry_unsupported_text_is_explicit_and_does_not_call_cloud(self):
        for index, text in enumerate(('BAD schön', '가자 日本語かな', '가자 Привет', '集合', '123 😀')):
            with self.subTest(text=text):
                self.receive(index, text)
                with self.assertRaisesRegex(ValueError, '仅支持英文和韩文'):
                    self.controller.retry_message(str(index))
                self.wait_idle()
        self.assertEqual(self.client.calls, [])

    def test_old_auto_cache_is_not_reused_but_new_english_result_is_reused(self):
        engine = self.controller.engine
        key = (engine.provider_id, engine.config_version, 'BAD', 'auto', 'zh')
        self.controller.cache.put(key, TranslationResult('old', '洗澡', 'de', None, 1))
        first = self.receive('one', 'BAD')
        second = self.receive('two', 'BAD')
        self.assertEqual([event.payload['translated'] for event in first if event.kind == 'result'], ['不好'])
        self.assertEqual([event.payload['translated'] for event in second if event.kind == 'result'], ['不好'])
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.ledger.snapshot('fictional', self.now()).local_reserved, 3)

    def test_manual_chinese_still_uses_chinese_source_for_both_targets(self):
        for target in ('en', 'ko'):
            self.controller.submit_manual('集合', target)
            self.wait_idle()
            events = self.controller.drain_events()
            self.assertTrue(any(event.kind == 'manual_result' for event in events))
            request = self.client.calls[-1][0]
            self.assertEqual((request.source_language, request.target_language), ('zh', target))
            self.assertEqual(request.source_text, '集合')
        self.assertEqual(len(self.client.calls), 2)

    def test_pause_and_budget_still_prevent_incoming_cloud_calls(self):
        self.controller.pause_automatic(True)
        self.receive('paused', 'BAD')
        self.assertEqual(self.client.calls, [])
        self.controller.pause_automatic(False)
        self.controller.account = 'unconfirmed'
        events = self.receive('budget', 'BAD')
        self.assertTrue(any(event.kind == 'error' for event in events))
        self.assertEqual(self.client.calls, [])


class AliyunIncomingSourceTests(unittest.TestCase):
    def test_engine_accepts_explicit_english_and_korean_and_sends_body_once(self):
        client = _Client()
        engine = AliyunEngine(Credentials('fictional', 'fictional-secret'),
                              client_factory=lambda config: client)
        for source, text in (('en', 'BAD'), ('ko', '가자 BAD')):
            with self.subTest(source=source):
                result = engine.translate(TranslationJob(source, None, 0, text, source, 'zh', 'auto'))
                request, runtime = client.calls[-1]
                self.assertEqual((request.source_language, request.target_language), (source, 'zh'))
                self.assertEqual(request.source_text, text)
                self.assertEqual(result.detected_language, source)
                self.assertFalse(runtime.autoretry)
        self.assertEqual(len(client.calls), 2)

    def test_engine_still_rejects_unsupported_source(self):
        client = _Client()
        engine = AliyunEngine(Credentials('fictional', 'fictional-secret'),
                              client_factory=lambda config: client)
        with self.assertRaises(TranslationError):
            engine.translate(TranslationJob('de', None, 0, 'schön', 'de', 'zh', 'auto'))
        self.assertEqual(client.calls, [])
