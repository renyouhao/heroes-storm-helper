import unittest
from hots_chat.models import ChatEvent, TranslationJob, TranslationResult, AppEvent


class ModelTests(unittest.TestCase):
    def test_contracts_and_import_has_no_side_effect(self):
        c = ChatEvent('1', 1, '团队', 'tester', '01:01', 'hello')
        j = TranslationJob('j', c.message_id, 1, c.text, 'auto', 'zh', 'auto')
        r = TranslationResult(j.job_id, '你好', 'en', None, 1.0)
        self.assertEqual(AppEvent('result', 1, r).payload.translated, '你好')
