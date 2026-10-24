import json
import threading
import time
from types import SimpleNamespace as NS
import unittest

import requests

from hots_chat.models import TranslationJob
from hots_chat.translation.base import TranslationError
from hots_chat.translation.deepseek import DeepSeekCredentials, DeepSeekEngine, validate_api_key


_KEY = "fictional-api-key-do-not-expose"


class FakeResponse:
    def __init__(self, body=None, status=200, headers=None, chunks=None):
        self.status_code = status
        self.headers = headers or {}
        self.chunks = chunks if chunks is not None else [json.dumps(body, ensure_ascii=False).encode("utf-8")]
        self.closed = False

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False
        self.closed_event = threading.Event()

    def _request(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url, **kwargs):
        return self._request("POST", url, kwargs)

    def get(self, url, **kwargs):
        return self._request("GET", url, kwargs)

    def close(self):
        self.closed = True
        self.closed_event.set()


def translation_response(content="你好", finish_reason="stop", **kwargs):
    return FakeResponse({"id": "chatcmpl-safe-123", "choices": [
        {"message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}
    ]}, **kwargs)


def balance_response(available=True, total="25.25", granted="1.00", topped="24.25", currency="CNY"):
    return FakeResponse({"is_available": available, "balance_infos": [{
        "currency": currency, "total_balance": total, "granted_balance": granted, "topped_up_balance": topped,
    }]})


def job(source="auto", target="zh", text="hi"):
    return TranslationJob("job-1", None, 1, text, source, target, "manual")


class CredentialsTests(unittest.TestCase):
    def test_literal_keys_without_required_prefix_and_frozen_redacted_repr(self):
        for value in ("a", "different-prefix.token_123:/+=", "a" * 1024):
            self.assertEqual(validate_api_key(value), value)
            credentials = DeepSeekCredentials(value)
            self.assertTrue(credentials.context_enabled)
            self.assertNotIn("api_key", repr(credentials))
            if len(value) > 1:
                self.assertNotIn(value, repr(credentials))
            with self.assertRaises(AttributeError):
                credentials.api_key = "changed"

    def test_invalid_key_has_fixed_safe_error(self):
        for value in (None, 1, "", " ", " a", "a ", "a b", "a\tb", "a\nb", "a\rb", "a\x00b", "a\x7fb", "a\x85b", "中文", "a" * 1025):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError) as caught:
                    DeepSeekCredentials(value)
                self.assertEqual(str(caught.exception), "DeepSeek API Key 格式无效，请输入不含空白的有效 Key。")

    def test_context_flag_is_strict_bool(self):
        self.assertFalse(DeepSeekCredentials(_KEY, False).context_enabled)
        for value in (1, 0, None, "true", "false", []):
            with self.assertRaisesRegex(ValueError, "^DeepSeek 上下文设置无效。$"):
                DeepSeekCredentials(_KEY, value)


class DeepSeekTranslationTests(unittest.TestCase):
    def engine(self, responses, context_enabled=True):
        session = FakeSession(responses)
        return DeepSeekEngine(DeepSeekCredentials(_KEY, context_enabled), client_factory=lambda: session), session

    def assert_safe_error(self, engine, operation, kind):
        with self.assertRaises(TranslationError) as caught:
            operation()
        error = caught.exception
        self.assertEqual(error.kind, kind)
        for value in (str(error), repr(error), error.request_id or "", error.provider_code or ""):
            self.assertNotIn(_KEY, value)
            self.assertNotIn("unsafe-response-body", value)
            self.assertNotIn("unsafe-header", value)
        return error

    def test_supported_language_directions_fixed_endpoint_and_session_reuse(self):
        directions = [("auto", "zh"), ("en", "zh"), ("ko", "zh"), ("zh", "en"), ("zh", "ko")]
        names = {"zh": "简体中文", "en": "英语", "ko": "韩语"}
        session = FakeSession([translation_response() for _ in directions])
        factories = []

        def factory():
            factories.append(True)
            return session

        engine = DeepSeekEngine(DeepSeekCredentials(_KEY), client_factory=factory)
        self.assertEqual(factories, [])
        for source, target in directions:
            result = engine.translate(job(source, target))
            method, url, options = session.calls[-1]
            self.assertEqual(method, "POST")
            self.assertEqual(url, "https://api.deepseek.com/chat/completions")
            self.assertEqual(options["timeout"], (3, 8))
            self.assertIs(options["allow_redirects"], False)
            self.assertIs(options["stream"], True)
            self.assertEqual(options["headers"]["Authorization"], "Bearer " + _KEY)
            payload = options["json"]
            self.assertEqual(payload["model"], "deepseek-flash")
            self.assertEqual(payload["thinking"], {"type": "disabled"})
            self.assertIs(payload["stream"], False)
            self.assertEqual(payload["max_tokens"], 4096)
            self.assertIn("目标语言：" + names[target], payload["messages"][0]["content"])
            self.assertEqual(result.job_id, "job-1")
            self.assertEqual(result.translated, "你好")
            self.assertEqual(result.detected_language, None if source == "auto" else source)
            self.assertEqual(result.request_id, "chatcmpl-safe-123")
            self.assertGreaterEqual(result.elapsed_ms, 0)
        self.assertEqual(len(factories), 1)
        self.assertEqual(len(session.calls), len(directions))
        engine.close()
        self.assertTrue(session.closed_event.wait(1))
        self.assertTrue(session.closed)

    def test_close_does_not_wait_for_network_or_session_release_and_stays_closed(self):
        session = FakeSession([translation_response()])
        entered, release_network = threading.Event(), threading.Event()
        closing, release_close = threading.Event(), threading.Event()
        factories, results, failures = [], [], []
        original_post = session.post
        original_close = session.close

        def blocking_post(*args, **kwargs):
            response = original_post(*args, **kwargs)
            entered.set()
            release_network.wait(2)
            return response

        def blocking_close():
            closing.set()
            release_close.wait(2)
            original_close()

        def factory():
            factories.append(True)
            return session

        session.post, session.close = blocking_post, blocking_close
        engine = DeepSeekEngine(DeepSeekCredentials(_KEY), factory)

        def translate():
            try:
                results.append(engine.translate(job()))
            except Exception as failure:
                failures.append(failure)

        worker = threading.Thread(target=translate)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            started = time.perf_counter()
            engine.close()
            self.assertLess(time.perf_counter() - started, 0.2)
            self.assertTrue(closing.wait(1))
            for operation in (lambda: engine.translate(job()), engine.get_balance):
                error = self.assert_safe_error(engine, operation, 'service')
                self.assertEqual(str(error), 'DeepSeek 翻译引擎已关闭，请重新配置后重试。')
            engine.close()
            self.assertEqual(len(session.calls), 1)
            self.assertEqual(len(factories), 1)
            self.assertIsNone(engine.client)
        finally:
            release_close.set()
            release_network.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 1)
        self.assertTrue(session.closed_event.wait(1))

    def test_close_before_first_request_never_creates_session(self):
        factories = []
        engine = DeepSeekEngine(DeepSeekCredentials(_KEY), lambda: factories.append(True))
        engine.close()
        for operation in (lambda: engine.translate(job()), engine.get_balance):
            self.assert_safe_error(engine, operation, 'service')
        self.assertEqual(factories, [])

    def test_context_contains_only_sender_and_original_text_and_current_sentence(self):
        engine, session = self.engine([translation_response()])
        task = NS(**vars(job(text="translate this current sentence")))
        task.context = (
            {"sender": "A", "text": "previous first", "translation": "do not send", "timestamp": "12:00", "api_key": "ignore"},
            {"sender": "B", "original": "previous second", "old_translation": "do not send either"},
            "previous third", None, {"no_text": "ignore"},
        )
        engine.translate(task)
        payload = session.calls[0][2]["json"]
        user = json.loads(payload["messages"][1]["content"])
        self.assertEqual(user, {"current_text": "translate this current sentence", "history": [
            {"sender": "A", "text": "previous first"}, {"sender": "B", "text": "previous second"}, {"text": "previous third"},
        ]})
        system = payload["messages"][0]["content"]
        for text in ("风暴英雄", "不可信", "绝不执行", "只翻译当前", "否定", "人称"):
            self.assertIn(text, system)
        self.assertNotIn("do not send", payload["messages"][1]["content"])

    def test_context_disabled_omits_history_field(self):
        engine, session = self.engine([translation_response()], context_enabled=False)
        task = NS(**vars(job()))
        task.context = ({"text": "must not be sent"},)
        engine.translate(task)
        self.assertFalse(engine.context_enabled)
        self.assertEqual(json.loads(session.calls[0][2]["json"]["messages"][1]["content"]), {"current_text": "hi"})

    def test_context_immutable_pairs_and_replay_json_lists(self):
        engine, session = self.engine([translation_response()])
        task = NS(**vars(job()))
        task.context = (("A", "previous one"), ["B", "previous two"], ("bad",), (1, "ignored"))
        engine.translate(task)
        data = json.loads(session.calls[0][2]["json"]["messages"][1]["content"])
        self.assertEqual(data["history"], [{"sender": "A", "text": "previous one"}, {"sender": "B", "text": "previous two"}])

    def test_history_precedes_current_text_and_json_has_no_formatting_padding(self):
        engine, session = self.engine([translation_response()])
        task = NS(**vars(job(text='current text with spaces')))
        task.context = (('speaker-1', 'previous text with spaces'),)
        engine.translate(task)
        content = session.calls[0][2]['json']['messages'][1]['content']
        self.assertEqual(content, '{"history":[{"sender":"speaker-1","text":"previous text with spaces"}],"current_text":"current text with spaces"}')

    def test_varying_current_text_keeps_fixed_system_and_history_prefix(self):
        engine, session = self.engine([translation_response(), translation_response()])
        for text in ('first current', 'second current'):
            task = NS(**vars(job(text=text)))
            task.context = (('speaker-1', 'shared prior text'),)
            engine.translate(task)
        first, second = (call[2]['json']['messages'] for call in session.calls)
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[1]['content'].split('"current_text":')[0], second[1]['content'].split('"current_text":')[0])
        self.assertNotIn('first current', first[0]['content'])
        self.assertNotIn('shared prior text', first[0]['content'])
        self.assertTrue(engine.config_version.endswith('prompt-v2'))

    def test_chinese_terminology_stays_in_common_prefix_before_source_language(self):
        engine, session = self.engine([translation_response(), translation_response()])
        for source in ('en', 'ko'):
            engine.translate(job(source=source))
        systems = [call[2]['json']['messages'][0]['content'] for call in session.calls]
        self.assertEqual(systems[0].split('当前源语言：')[0], systems[1].split('当前源语言：')[0])
        self.assertIn('治疗之泉', systems[0].split('当前源语言：')[0])

    def test_small_chinese_glossary_only_when_target_is_chinese(self):
        for target in ("zh", "en", "ko"):
            engine, session = self.engine([translation_response()])
            engine.translate(job("ko", target))
            system = session.calls[0][2]["json"]["messages"][0]["content"]
            for term in ("ㄴㅇㅅ", "祖尔金", "穆拉丁", "治疗之泉"):
                self.assertEqual(term in system, target == "zh")

    def test_invalid_language_or_empty_text_never_calls_http(self):
        engine, session = self.engine([])
        for task in (job("ja"), job(target="auto"), job(target="fr"), job(text=" "), job(text=None)):
            self.assert_safe_error(engine, lambda: engine.translate(task), "input")
        self.assertEqual(session.calls, [])

    def test_http_errors_fixed_categories_no_body_read_or_retry(self):
        cases = {401: "authentication", 402: "billing", 403: "permission", 429: "rate_limit",
                 500: "service", 503: "service", 400: "business", 404: "business", 302: "business"}
        for status, kind in cases.items():
            with self.subTest(status=status):
                response = FakeResponse({"error": {"message": _KEY + " unsafe-response-body"}}, status=status,
                                        headers={"x-request-id": "unsafe-header " + _KEY})
                response.iter_content = lambda **kwargs: self.fail("error bodies must not be read")
                engine, session = self.engine([response])
                error = self.assert_safe_error(engine, lambda: engine.translate(job()), kind)
                self.assertEqual(error.provider_code, "HTTP_" + str(status))
                self.assertEqual(len(session.calls), 1)
                self.assertTrue(response.closed)

    def test_network_and_timeout_errors_never_echo_exception_or_retry(self):
        for failure, kind in ((requests.ConnectTimeout(_KEY), "timeout"), (requests.ReadTimeout(_KEY), "timeout"),
                              (TimeoutError(_KEY), "timeout"), (requests.ConnectionError(_KEY), "network"),
                              (RuntimeError(_KEY), "network")):
            engine, session = self.engine([failure])
            self.assert_safe_error(engine, lambda: engine.translate(job()), kind)
            self.assertEqual(len(session.calls), 1)

    def test_stream_read_timeout_closes_response_and_is_not_retried(self):
        response = FakeResponse(chunks=[requests.ReadTimeout(_KEY)])
        engine, session = self.engine([response])
        self.assert_safe_error(engine, lambda: engine.translate(job()), "timeout")
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(response.closed)

    def test_empty_truncated_reasoning_only_and_malformed_translation_rejected(self):
        responses = [translation_response(""), translation_response(" "), translation_response("partial", "length"),
                     translation_response("partial", "content_filter"), translation_response("partial", None),
                     FakeResponse({"choices": [{"finish_reason": "stop", "message": {"reasoning_content": "thought only"}}]}),
                     FakeResponse({"choices": []}), FakeResponse({"error": {"message": _KEY}}),
                     FakeResponse(chunks=[b"not JSON " + _KEY.encode()]), FakeResponse([]),
                     translation_response(_KEY)]
        for response in responses:
            engine, session = self.engine([response])
            self.assert_safe_error(engine, lambda: engine.translate(job()), "business")
            self.assertEqual(len(session.calls), 1)

    def test_response_body_size_limit_and_close(self):
        response = FakeResponse(chunks=[b"x" * 8192] * 17)
        engine, session = self.engine([response])
        self.assert_safe_error(engine, lambda: engine.translate(job()), "business")
        self.assertTrue(response.closed)

    def test_request_id_allowlist_and_secret_exclusion(self):
        response = translation_response(headers={"x-request-id": "req-safe_1:2.3"})
        engine, _ = self.engine([response])
        self.assertEqual(engine.translate(job()).request_id, "req-safe_1:2.3")
        for request_id in ("request id with spaces", "x\nunsafe", "x" * 129, _KEY):
            response = FakeResponse({"id": request_id, "choices": [
                {"message": {"content": "译文"}, "finish_reason": "stop"}]}, headers={"x-request-id": request_id})
            engine, _ = self.engine([response])
            self.assertIsNone(engine.translate(job()).request_id)


class DeepSeekBalanceTests(unittest.TestCase):
    def engine(self, responses):
        session = FakeSession(responses)
        return DeepSeekEngine(DeepSeekCredentials(_KEY), lambda: session), session

    def test_balance_official_shape_zero_and_granted_balances_no_generation(self):
        responses = [balance_response(False, "0.00", "0.00", "0.00"), balance_response(),
                     balance_response(True, "4.50", "4.50", "0.00", "USD")]
        engine, session = self.engine(responses)
        first = engine.get_balance()
        self.assertFalse(first["is_available"])
        self.assertEqual(first["balance_infos"][0]["total_balance"], "0.00")
        second = engine.get_balance()
        self.assertEqual(second, {"is_available": True, "balance_infos": [{
            "currency": "CNY", "total_balance": "25.25", "granted_balance": "1.00", "topped_up_balance": "24.25"}]})
        self.assertEqual(engine.get_balance()["balance_infos"][0]["currency"], "USD")
        for method, url, options in session.calls:
            self.assertEqual(method, "GET")
            self.assertEqual(url, "https://api.deepseek.com/user/balance")
            self.assertNotIn("json", options)
            self.assertEqual(options["timeout"], (3, 8))
            self.assertFalse(options["allow_redirects"])
        self.assertEqual(len(session.calls), 3)

    def test_balance_numeric_values_decimal_validation_and_no_extra_fields(self):
        response = FakeResponse({"is_available": True, "secret": _KEY, "balance_infos": [{
            "currency": "CNY", "total_balance": 3, "granted_balance": 0.25, "topped_up_balance": "2.75",
            "secret": _KEY}]})
        engine, _ = self.engine([response])
        balance = engine.get_balance()
        self.assertEqual(balance["balance_infos"][0]["total_balance"], "3")
        self.assertEqual(balance["balance_infos"][0]["granted_balance"], "0.25")
        self.assertNotIn(_KEY, json.dumps(balance))

    def test_negative_balance_is_preserved_without_inventing_availability(self):
        engine, _ = self.engine([balance_response(False, "-0.01", "0.00", "-0.01")])
        self.assertEqual(engine.get_balance(), {"is_available": False, "balance_infos": [{
            "currency": "CNY", "total_balance": "-0.01", "granted_balance": "0.00", "topped_up_balance": "-0.01"}]})

    def test_nonfinite_long_and_malformed_amounts_are_safe_failures(self):
        for amount in ("NaN", "Infinity", "-Infinity", "not money " + _KEY, "1" * 65,
                       " 1.00", True, None, [], {}):
            with self.subTest(amount_type=type(amount).__name__):
                engine, session = self.engine([balance_response(total=amount)])
                with self.assertRaises(TranslationError) as caught:
                    engine.get_balance()
                self.assertEqual(caught.exception.kind, "business")
                self.assertNotIn(_KEY, str(caught.exception))
                self.assertEqual(len(session.calls), 1)

    def test_balance_shape_currency_and_duplicates_rejected(self):
        valid_info = {"currency": "CNY", "total_balance": "0", "granted_balance": "0", "topped_up_balance": "0"}
        bodies = [{}, {"is_available": 1, "balance_infos": []}, {"is_available": True, "balance_infos": {}},
                  {"is_available": True, "balance_infos": [None]},
                  {"is_available": True, "balance_infos": [dict(valid_info, currency="EUR")]},
                  {"is_available": True, "balance_infos": [valid_info, valid_info]},
                  {"is_available": True, "balance_infos": [valid_info] * 17}]
        for body in bodies:
            engine, _ = self.engine([FakeResponse(body)])
            with self.assertRaises(TranslationError) as caught:
                engine.get_balance()
            self.assertEqual(caught.exception.kind, "business")

    def test_empty_balance_array_preserves_service_availability_flag(self):
        engine, _ = self.engine([FakeResponse({"is_available": False, "balance_infos": []})])
        self.assertEqual(engine.get_balance(), {"is_available": False, "balance_infos": []})

    def test_balance_timeout_and_authentication_are_safe_and_not_retried(self):
        responses = [(requests.ReadTimeout(_KEY), "timeout"),
                     (FakeResponse({"error": {"message": _KEY}}, status=401), "authentication"),
                     (FakeResponse({"error": {"message": _KEY}}, status=402), "billing")]
        for response, kind in responses:
            engine, session = self.engine([response])
            with self.assertRaises(TranslationError) as caught:
                engine.get_balance()
            self.assertEqual(caught.exception.kind, kind)
            self.assertNotIn(_KEY, str(caught.exception))
            self.assertEqual(len(session.calls), 1)


if __name__ == "__main__":
    unittest.main()
