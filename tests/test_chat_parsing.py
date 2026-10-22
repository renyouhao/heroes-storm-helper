from dataclasses import asdict
import unittest
from hots_chat.acquisition.memory import find_matches
from hots_chat.acquisition.parsing import parse_display_chat
from test_history import chat_line

class ParsingTests(unittest.TestCase):
    def test_hour_timestamp_preserves_chat_time_and_multilingual_body(self):
        body='Please wait for level 10. 같이 가요. 你好'
        parsed=parse_display_chat(chat_line(body).replace('24:54','01:15:59',1))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.time,'01:15:59')
        self.assertEqual(parsed.text,body)
        self.assertEqual(parsed.channel,'团队')

    def test_multilingual_literal_tags_and_plain_input(self):
        body='hello 안녕하세요 你好 <keep this literal>'
        parsed=parse_display_chat(chat_line(body))
        self.assertEqual(parsed.text,body);self.assertEqual(parsed.sender,'Hunter')
        self.assertIsNone(parse_display_chat(body))
        self.assertIsNone(parse_display_chat('<s val="ChatDisplayTimeStamp">24:54</s><w>Hunter'))
    def test_chunk_edges_disjoint_overlap_and_empty(self):
        self.assertEqual(list(find_matches([(100,b'xxAB'),(104,b'Cyy')],b'ABC')),[102])
        self.assertEqual(list(find_matches([(100,b'xxAB'),(999,b'Cyy')],b'ABC')),[])
        self.assertEqual(list(find_matches([(100,b'xxABCxx'),(107,b'ABC')],b'ABC')),[102,107])
        with self.assertRaises(ValueError):list(find_matches([(100,b'x')],b''))
