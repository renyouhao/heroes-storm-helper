import unittest
from hots_chat.context import LocalContext
from hots_chat.models import ChatEvent
from hots_chat.translation.base import ProviderRegistry


class ContextAndProviderTests(unittest.TestCase):
    def test_context_three_and_generation_clear(self):
        c=LocalContext()
        for i in range(5):c.add(ChatEvent(str(i),1,'团队','tester','1:00','hello'))
        self.assertEqual(len(c.recent()),3)
        c.clear(2);self.assertFalse(c.recent())

    def test_registry_real_aliyun_only_no_fallback(self):
        r=ProviderRegistry()
        self.assertEqual(set(r.real_providers),{'aliyun'})
        with self.assertRaises(ValueError):r.create('other',None)
