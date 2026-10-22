"""Regression checks for the temporary read-only marker locator."""
import unittest

try:
    import chat_memory_probe as probe
except ModuleNotFoundError:
    probe = None


class MarkerMatchingTests(unittest.TestCase):
    def matches(self, chunks, marker):
        self.assertIsNotNone(probe, "Read-only marker locator has not been implemented")
        return list(probe.find_matches(chunks, marker))

    def test_marker_split_across_adjacent_chunks(self):
        self.assertEqual(self.matches([(100, b"xxHOTS_CH"), (109, b"AT_TESTyy")], b"HOTS_CHAT_TEST"), [102])

    def test_disjoint_memory_regions_cannot_form_a_message(self):
        self.assertEqual(self.matches([(100, b"xxHOTS_CH"), (999, b"AT_TESTyy")], b"HOTS_CHAT_TEST"), [])

    def test_overlap_does_not_duplicate_a_match(self):
        self.assertEqual(self.matches([(100, b"xxABCxx"), (107, b"ABC")], b"ABC"), [102, 107])

    def test_utf16_marker_can_cross_an_odd_chunk_boundary(self):
        encoded = "HOTS_CHAT_TEST".encode("utf-16-le")
        self.assertEqual(self.matches([(200, b"xx" + encoded[:9]), (211, encoded[9:] + b"zz")], encoded), [202])

    def test_empty_marker_is_rejected(self):
        with self.assertRaises(ValueError):
            self.matches([(100, b"abc")], b"")


class ChatRecordTests(unittest.TestCase):
    def decode(self, data):
        self.assertTrue(callable(getattr(probe, "decode_chat_record", None)), "Chat record decoder has not been implemented")
        return probe.decode_chat_record(data)

    def test_observed_record_preserves_korean_chinese_and_english(self):
        text = "HOTS_CHAT_TEST_20261019_B8E4 hello 안녕하세요 你好"
        decoded = self.decode(bytes.fromhex("41 60 00 02 39") + text.encode("utf-8"))
        self.assertEqual(decoded["text"], text)
        self.assertEqual(decoded["user_id"], 0)
        self.assertEqual(decoded["recipient"], 1)
        self.assertEqual(decoded["consumed_bytes"], 62)

    def test_partial_message_waits_for_remaining_bytes(self):
        with self.assertRaises(EOFError):
            self.decode(bytes.fromhex("41 60 00 02 39") + b"short")

    def test_corrupt_utf8_is_rejected(self):
        with self.assertRaises(UnicodeDecodeError):
            self.decode(bytes.fromhex("41 60 00 02 39") + b"\xff" * 57)


class DisplayChatTests(unittest.TestCase):
    def parse(self, text):
        self.assertTrue(callable(getattr(probe, "parse_display_chat", None)), "Display chat parser has not been implemented")
        return probe.parse_display_chat(text)

    def test_extracts_sender_channel_and_multilingual_body(self):
        body = "Wait for 10 안녕하세요 你好 <keep this literal>"
        source = '<s val="ChatDisplayTimeStamp">24:54</s><sp/><c val="3184FF">[团队]<c val="3184FF"><w><s val="ChatDisplayHeroIcon"><img path="hero.dds"/></s>Hunter</w></c>: </c>' + body
        self.assertEqual(self.parse(source), {"time":"24:54", "channel":"团队", "sender":"Hunter", "text":body})

    def test_plain_input_text_is_not_a_received_chat_line(self):
        self.assertIsNone(self.parse("HOTS_CHAT_TEST_20261019_C9D5 buffer growth test"))

    def test_incomplete_display_markup_is_not_emitted(self):
        self.assertIsNone(self.parse('<s val="ChatDisplayTimeStamp">24:54</s><w>Hunter'))


if __name__ == "__main__":
    unittest.main()
