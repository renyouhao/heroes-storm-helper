import unittest

from hots_chat.acquisition.lobby_parsing import parse_lobby_chat
from hots_chat.acquisition.parsing import ParsedChat


class LobbyParsingTests(unittest.TestCase):
    def test_incoming_whisper_has_no_invented_timestamp(self):
        source = (
            '<s val="BattleChatWhisper"><a name="player-17" href="player:17">'
            '[Hunter]悄悄地说：</a> hello 안녕하세요 你好</s>'
        )
        self.assertEqual(
            parse_lobby_chat(source),
            ParsedChat('', '密语', 'Hunter', 'hello 안녕하세요 你好'),
        )

    def test_incoming_whisper_accepts_observed_space_after_sender_bracket(self):
        source = (
            '<s val="BattleChatWhisper"><a name="playerName" href="12">'
            '[달빛] 悄悄地说：</a> hello 你好</s>'
        )
        self.assertEqual(
            parse_lobby_chat(source, time='20:45:16'),
            ParsedChat('20:45:16', '密语', '달빛', 'hello 你好'),
        )

    def test_outgoing_whisper_preserves_caller_timestamp(self):
        source = (
            '<s val="BattleChatWhisper"><a name="player-18" href="player:18">'
            '悄悄地告诉[星光]说：</a> 같이 가요</s>'
        )
        self.assertEqual(
            parse_lobby_chat(source, time='20:31:09'),
            ParsedChat('20:31:09', '密语', '我 → 星光', '같이 가요'),
        )

    def test_party_uses_confirmed_two_anchor_structure(self):
        source = (
            '<s val="BattleChatParty"><a name="party" href="channel:party">[队伍]</a> '
            '<a name="player-19" href="player:19">달빛:</a> ready 你好</s>'
        )
        self.assertEqual(
            parse_lobby_chat(source, time='20:31'),
            ParsedChat('20:31', '队伍', '달빛', 'ready 你好'),
        )

    def test_entities_are_decoded_once_in_sender_and_body(self):
        source = (
            '<s val="BattleChatWhisper"><a name="player-20" href="player:20">'
            '[A&amp;B&#x661F;]悄悄地说：</a> &lt;tag&gt; &amp;lt; &#xD55C;</s>'
        )
        self.assertEqual(
            parse_lobby_chat(source),
            ParsedChat('', '密语', 'A&B星', '<tag> &lt; 한'),
        )

    def test_party_sender_entities_are_decoded_once(self):
        source = (
            '<s val="BattleChatParty"><a name="party" href="channel:party">[队伍]</a> '
            '<a name="player-21" href="player:21">A&amp;B:</a> &amp;hello</s>'
        )
        self.assertEqual(parse_lobby_chat(source), ParsedChat('', '队伍', 'A&B', '&hello'))

    def test_body_keeps_literal_tags_nested_styles_and_internal_closing_style(self):
        source = (
            '<s val="BattleChatWhisper"><a name="player-22" href="player:22">'
            '[Hunter]悄悄地说：</a> <keep this literal> '
            '<s val="literal">inside</s> trailing </s> text\nsecond line</s>'
        )
        parsed = parse_lobby_chat(source)
        self.assertIsNotNone(parsed)
        self.assertEqual(
            parsed.text,
            '<keep this literal> <s val="literal">inside</s> trailing </s> text\nsecond line',
        )

    def test_exactly_one_separator_space_is_consumed(self):
        sources = (
            '<s val="BattleChatWhisper"><a name="p" href="p">[Hunter]悄悄地说：</a>   body </s>',
            '<s val="BattleChatParty"><a name="p" href="p">[队伍]</a> '
            '<a name="q" href="q">Hunter:</a>   body </s>',
        )
        for source in sources:
            with self.subTest(source=source):
                parsed = parse_lobby_chat(source)
                self.assertIsNotNone(parsed)
                self.assertEqual(parsed.text, '  body ')

    def test_empty_message_body_is_retained(self):
        source = '<s val="BattleChatWhisper"><a name="p" href="p">[Hunter]悄悄地说：</a> </s>'
        self.assertEqual(parse_lobby_chat(source), ParsedChat('', '密语', 'Hunter', ''))

    def test_unrecognized_styles_cannot_spoof_a_supported_channel(self):
        for style in ('BattleChatSystem', 'BattleChatGeneral', 'BattleChatPartyExtra', 'battlechatwhisper'):
            source = f'<s val="{style}"><a name="p" href="p">[Hunter]悄悄地说：</a> body</s>'
            with self.subTest(style=style):
                self.assertIsNone(parse_lobby_chat(source))

    def test_whisper_requires_the_confirmed_direction_labels(self):
        for label in (
            '[Hunter]说：', '[Hunter]悄悄地告诉：', '对[Hunter]说：',
            '悄悄地告诉[Hunter]：', '[Hunter]悄悄地说:', '[队伍] Hunter:',
            '[]悄悄地说：', '[Hunter]悄悄地说：extra', '[Hunter]  悄悄地说：',
        ):
            source = f'<s val="BattleChatWhisper"><a name="p" href="p">{label}</a> body</s>'
            with self.subTest(label=label):
                self.assertIsNone(parse_lobby_chat(source))

    def test_party_requires_its_exact_channel_and_sender_header(self):
        for header in (
            '<a name="p" href="p">[团队]</a> <a name="q" href="q">Hunter:</a> ',
            '<a name="p" href="p">[密语]</a> <a name="q" href="q">Hunter:</a> ',
            '<a name="p" href="p">[队伍]</a> <a name="q" href="q">Hunter：</a> ',
            '<a name="p" href="p">[队伍]</a> <a name="q" href="q">Hunter</a>: ',
            '<a name="p" href="p">[队伍]</a><a name="q" href="q">Hunter:</a> ',
            '<a name="p" href="p">[队伍]</a> <a name="q" href="q">:</a> ',
        ):
            with self.subTest(header=header):
                self.assertIsNone(parse_lobby_chat(f'<s val="BattleChatParty">{header}body</s>'))

    def test_unconfirmed_or_malformed_anchor_metadata_is_rejected(self):
        for anchor in (
            '<a href="p">[Hunter]悄悄地说：</a>',
            '<a name="p">[Hunter]悄悄地说：</a>',
            '<a name="" href="p">[Hunter]悄悄地说：</a>',
            '<a name="p" href="">[Hunter]悄悄地说：</a>',
            '<a name="p" href="p" extra="x">[Hunter]悄悄地说：</a>',
            '<a name="p" href="p"><w>Hunter</w>悄悄地说：</a>',
            '<a name="{player}" href="p">[Hunter]悄悄地说：</a>',
            '<a name="p" href="p">[{NAME}]悄悄地说：</a>',
            '<a name="p" href="p">[${NAME}]悄悄地说：</a>',
        ):
            with self.subTest(anchor=anchor):
                self.assertIsNone(parse_lobby_chat(f'<s val="BattleChatWhisper">{anchor} body</s>'))

    def test_incomplete_or_non_chat_envelopes_are_rejected(self):
        valid = '<s val="BattleChatWhisper"><a name="p" href="p">[Hunter]悄悄地说：</a> body</s>'
        for source in (
            '', 'plain text', valid[:-4], valid.replace('</a>', '', 1),
            valid.replace('</a> ', '</a>', 1), 'prefix' + valid, valid + 'suffix',
            valid.replace('<s val="BattleChatWhisper">', '<s val="BattleChatWhisper" extra="x">', 1),
        ):
            with self.subTest(source=source):
                self.assertIsNone(parse_lobby_chat(source))

    def test_parser_bounds_input_without_imposing_the_readers_byte_limit(self):
        prefix = '<s val="BattleChatWhisper"><a name="p" href="p">[Hunter]悄悄地说：</a> '
        suffix = '</s>'
        source = prefix + 'a' * (65536 - len(prefix) - len(suffix)) + suffix
        parsed = parse_lobby_chat(source)
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed.text), 65536 - len(prefix) - len(suffix))
        self.assertIsNone(parse_lobby_chat(source.replace('</s>', 'a</s>')))
        multilingual = prefix + '你好한' * 1000 + suffix
        parsed = parse_lobby_chat(multilingual)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.text, '你好한' * 1000)


if __name__ == '__main__':
    unittest.main()
