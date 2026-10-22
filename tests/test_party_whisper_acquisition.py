"""队伍/密语格式与采集回归。incoming样例为合成格式，非实机接收确认。"""
import threading
import unittest

from hots_chat.acquisition.discovery import DiscoveryPending, IncrementalDiscovery, discover
from hots_chat.acquisition.history import HistoryReader
from hots_chat.acquisition.parsing import parse_display_chat
from test_history import FakeMemory, chat_line

STAMP = '<s val="ChatDisplayTimeStamp">24:54</s><sp/>'


def party_out(body):
    return STAMP + '<c val="#ColorBattleNetParty">对 [队伍] 说：<sp/></c>' + body


def whisper_out(body, contact='Friend'):
    return STAMP + '<c val="#ColorBattleNetWhisper">对 [<w>' + contact + '</w>]说：<sp/></c>' + body


class PartyWhisperParsingTests(unittest.TestCase):
    def test_observed_party_outgoing_without_sender_word_tag(self):
        parsed = parse_display_chat(party_out('hello'))
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.channel, parsed.sender, parsed.text), ('队伍', '我', 'hello'))

    def test_observed_whisper_outgoing_uses_explicit_channel_and_contact(self):
        parsed = parse_display_chat(whisper_out('hi'))
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.channel, parsed.sender, parsed.text), ('密语', '我 → Friend', 'hi'))

    def test_classic_incoming_party_format_keeps_sender(self):
        parsed = parse_display_chat(chat_line('come here').replace('[团队]', '[队伍]', 1))
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.channel, parsed.sender, parsed.text), ('队伍', 'Hunter', 'come here'))

    def test_supported_synthetic_incoming_whisper_without_outgoing_direction(self):
        source = STAMP + '<c val="#ColorBattleNetWhisper">[<w>Friend</w>]说：<sp/></c>hello'
        parsed = parse_display_chat(source)
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.channel, parsed.sender, parsed.text), ('密语', 'Friend', 'hello'))

    def test_party_and_whisper_preserve_literal_markup_and_long_timestamp(self):
        body = 'hello 안녕하세요 你好 <keep literal> &amp; &lt;b&gt;x&lt;/b&gt;'
        expected = 'hello 안녕하세요 你好 <keep literal> & <b>x</b>'
        for factory in (party_out, whisper_out):
            with self.subTest(factory=factory.__name__):
                parsed = parse_display_chat(factory(body).replace('24:54', '01:15:59', 1))
                self.assertIsNotNone(parsed)
                self.assertEqual(parsed.time, '01:15:59')
                self.assertEqual(parsed.text, expected)

    def test_contact_markup_and_entities_only_clean_sender_not_body(self):
        parsed = parse_display_chat(whisper_out('<literal>', '<s val="tag">A&amp;B</s>'))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.sender, '我 → A&B')
        self.assertEqual(parsed.text, '<literal>')

    def test_malformed_battlenet_headers_and_system_lines_are_rejected(self):
        invalid = [
            STAMP + '<c val="#ColorBattleNetWhisper">private',
            STAMP + '<c val="#ColorBattleNetWhisper">对 [<w>Friend</w>]说：hi',
            whisper_out('hi', ''),
            STAMP + '<c val="#ColorBattleNetParty">系统提示：<sp/></c>joined',
            STAMP + '<c val="#ColorBattleNetParty">对 [系统] 说：<sp/></c>hello',
            STAMP + '<c val="#ColorSystem">system notice<sp/></c>hello',
            STAMP + '<c val="#ColorPing"><w>Friend</w>请求协助<sp/></c>ping',
        ]
        for source in invalid:
            with self.subTest(source=source):
                self.assertIsNone(parse_display_chat(source))

    def test_unknown_classic_channel_and_unmarked_whisper_are_rejected(self):
        for channel in ('综合', '系统', 'Ping', '密语'):
            self.assertIsNone(parse_display_chat(chat_line('hello').replace('[团队]', '[' + channel + ']', 1)))

    def test_battlenet_markers_in_regular_message_body_do_not_change_channel(self):
        body = '<c val="#ColorBattleNetWhisper"><w>Friend</w>]说：<sp/></c>literal'
        parsed = parse_display_chat(chat_line(body))
        self.assertEqual(parsed.channel, '团队')
        self.assertEqual(parsed.text, body)


class PartyWhisperHistoryTests(unittest.TestCase):
    def test_party_only_history_is_found_by_initial_discovery(self):
        memory = FakeMemory(); memory.set_nodes([party_out('hello')])
        self.assertEqual(discover(memory, threading.Event()).root, memory.root)

    def test_whisper_only_history_is_found_by_initial_discovery(self):
        memory = FakeMemory(); memory.set_nodes([whisper_out('hi')])
        self.assertEqual(discover(memory, threading.Event()).root, memory.root)

    def test_incremental_discovery_accepts_party_or_whisper_only_history(self):
        for factory in (party_out, whisper_out):
            with self.subTest(factory=factory.__name__):
                memory = FakeMemory(); memory.set_nodes([factory('hello')])
                result = IncrementalDiscovery(memory, threading.Event()).step(time_budget=60)
                self.assertIsNotNone(result)
                self.assertEqual(result.root, memory.root)

    def test_new_party_message_is_emitted_once_after_baseline(self):
        self.assert_new_message_once(party_out)

    def test_new_whisper_message_is_emitted_once_after_baseline(self):
        self.assert_new_message_once(whisper_out)

    def assert_new_message_once(self, factory):
        memory = FakeMemory(); baseline = factory('before connection')
        memory.set_nodes([baseline]); poller = HistoryReader(memory, memory.root)
        self.assertEqual(poller.poll().messages, [])
        memory.set_nodes([baseline, factory('new')])
        self.assertEqual([message.text for message in poller.poll().messages], ['new'])
        self.assertEqual(poller.poll().messages, [])

    def test_malformed_whisper_does_not_force_reading_its_body(self):
        source = STAMP + '<c val="#ColorBattleNetWhisper">' + 'private' * 200
        memory = FakeMemory(); node, = memory.set_nodes([source])
        self.assertEqual(HistoryReader(memory, memory.root, include_history=True).poll().messages, [])
        self.assertFalse(any(address == node + 24 and count > 96 for address, count in memory.calls))

    def test_system_and_ping_only_history_are_not_discovered(self):
        for color in ('#ColorSystem', '#ColorPing'):
            memory = FakeMemory()
            memory.set_nodes([STAMP + '<c val="' + color + '"><w>Friend</w>notice<sp/></c>content'])
            with self.assertRaises(DiscoveryPending):
                discover(memory, threading.Event())

    def test_valid_team_and_all_chat_remain_supported(self):
        memory = FakeMemory()
        memory.set_nodes([chat_line('team'), chat_line('all').replace('[团队]', '[所有玩家]', 1)])
        self.assertEqual([(m.channel, m.text) for m in HistoryReader(memory, memory.root, include_history=True).poll().messages],
                         [('团队', 'team'), ('所有玩家', 'all')])


if __name__ == '__main__':
    unittest.main()
