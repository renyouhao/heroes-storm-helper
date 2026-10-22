import unittest
from hots_chat.cache import BoundedTranslationCache
from hots_chat.routing import should_translate

class CacheTests(unittest.TestCase):
    def test_cache_bytes_and_lru_bound(self):
        c=BoundedTranslationCache(2,20)
        c.put('a','x'); c.put('b','y'); c.get('a'); c.put('c','z')
        self.assertIsNone(c.get('b'))
        c.put('huge','x'*100)
        self.assertLessEqual(c.byte_size,20)
    def test_hangul_jamo_and_mixed_route(self):
        for text in ('a','ㄱㄱ','ㅋㅋ','ᄀ','hello你好','안녕'): self.assertTrue(should_translate(text))
        for text in ('你好','123','！！'): self.assertFalse(should_translate(text))
