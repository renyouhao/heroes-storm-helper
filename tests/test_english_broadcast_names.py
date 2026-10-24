"""英文播报姓名边界：实机电脑名回归、四类项目与真实诊断 ZIP。"""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from hots_chat import broadcast_text
from hots_chat.board_sender import ChatInputState, TeamChatSender
from hots_chat.diagnostics import DiagnosticRecorder
from hots_chat.talent_broadcast import (
    CooldownHitSnapshot, StatHitSnapshot, format_cooldown_message, format_stat_message,
)
from tests import test_board_output_language as language_cases


def has_chinese(text):
    return any('\u3400' <= character <= '\u9fff' or '\uf900' <= character <= '\ufaff'
               for character in text)


def anduin_hit(kind='stat', **values):
    common = dict(pid=11, hwnd=22, match_token='synthetic-match', player_slot=3,
        team_relation='ally', rect=(100, 200, 160, 260), observed_at=10.,
        valid_until=11., panel_visible=True, mapping_trusted=True,
        player_name='电脑 - 专家', hero_name='安度因', hero_id='Anduin')
    common.update(values)
    if kind == 'stat':
        return StatHitSnapshot(**common, metric_id='hero_damage', value=89164)
    return CooldownHitSnapshot(**common, ability_id='AnduinLightBomb',
        ability_name='圣光炸弹', remaining_seconds=8, state='cooldown')


class EnglishBroadcastNameTests(unittest.TestCase):
    def test_screenshot_hero_damage_is_english_with_stable_player_alias(self):
        hit = anduin_hit()
        self.assertEqual(format_stat_message(hit, language='en'),
            '[Ally] Player 4 (Anduin) | Hero damage: 89,164')
        self.assertEqual(hit.player_name, '电脑 - 专家')
        self.assertEqual(broadcast_text.player_name_policy(hit), 'english_name_fallback')

    def test_screenshot_lightbomb_preserves_exact_ability_and_observed_seconds(self):
        hit = anduin_hit('cooldown')
        self.assertEqual(format_cooldown_message(hit, language='en'),
            'Watch [Ally] Player 4 (Anduin) | Heroic "Lightbomb" | Cooldown shown: 8s')
        self.assertFalse(has_chinese(format_cooldown_message(hit, language='en')))

    def test_all_four_callout_kinds_apply_the_same_name_policy_without_mutating_input(self):
        for formatter, original in zip(language_cases.FORMATTERS, language_cases.hits()):
            hit = replace(original, player_name='电脑－专家')
            with self.subTest(kind=type(hit).__name__):
                localized = broadcast_text.localize_hit(hit, 'en')
                self.assertEqual(localized.player_name, 'Player 4')
                self.assertEqual(hit.player_name, '电脑－专家')
                output = formatter(hit, language='en')
                self.assertIn('Player 4 (Jaina)', output)
                self.assertFalse(has_chinese(output))

    def test_human_chinese_hangul_and_cyrillic_names_are_not_machine_translated(self):
        for name in ('中文玩家', '电脑 - 专家', '한글닉네임', 'Игрок', 'Latin中文'):
            with self.subTest(name=name):
                hit = anduin_hit(player_name=name)
                localized = broadcast_text.localize_hit(hit, 'en')
                self.assertEqual(localized.player_name, 'Player 4')
                output = format_stat_message(hit, language='en')
                self.assertFalse(has_chinese(output))
                self.assertNotIn('Expert', output)
                self.assertNotIn('Adept', output)
                self.assertEqual(broadcast_text.player_name_policy(hit), 'english_name_fallback')

    def test_latin_names_remain_readable_after_existing_text_cleaning(self):
        for name in ('Hunter', 'EggHead', 'Éowyn', 'Player-123'):
            with self.subTest(name=name):
                hit = anduin_hit(player_name=name)
                self.assertIn(f'{name} (Anduin)', format_stat_message(hit, language='en'))
                self.assertEqual(broadcast_text.player_name_policy(hit), 'english_name_preserved')
        decorated = anduin_hit(player_name='<c>Hunter</c>\r\n/all\x00\u202e')
        self.assertEqual(broadcast_text.localize_hit(decorated, 'en').player_name, 'Hunter')
        output = format_stat_message(decorated, language='en')
        for invalid in ('<', '>', '/all', '\r', '\n', '\x00', '\u202e'):
            self.assertNotIn(invalid, output)

    def test_unknown_or_absent_names_use_the_known_english_hero_without_a_fake_name(self):
        for name in ('', '未知', 'unknown', '?', None):
            with self.subTest(name=name):
                hit = anduin_hit(player_name=name)
                self.assertEqual(broadcast_text.player_name_policy(hit), 'english_name_omitted')
                self.assertEqual(format_stat_message(hit, language='en'),
                    '[Ally] Hero: Anduin | Hero damage: 89,164')

    def test_invalid_player_slot_omits_non_latin_name_instead_of_inventing_an_alias(self):
        for slot in (None, True, -1, 16, '3'):
            with self.subTest(slot=slot):
                hit = anduin_hit(player_slot=slot, player_name='中文玩家')
                self.assertEqual(broadcast_text.player_name_policy(hit), 'english_name_omitted')
                self.assertIn('Hero: Anduin', format_stat_message(hit, language='en'))
                self.assertNotIn('Player ', format_stat_message(hit, language='en'))

    def test_chinese_mode_retains_original_names_and_labels(self):
        hit = anduin_hit()
        self.assertIs(broadcast_text.localize_hit(hit, 'zh'), hit)
        self.assertEqual(format_stat_message(hit), '【我方】电脑 - 专家（安度因）｜英雄伤害：89,164')
        self.assertIn('电脑 - 专家', format_cooldown_message(anduin_hit('cooldown')))

    def test_a_name_change_does_not_retarget_an_existing_pending_english_action(self):
        harness = language_cases.LanguageSessionTests()
        harness.setUp()
        session, hit = harness.session('en'), anduin_hit()
        action = harness.ready(session, hit)
        self.assertIsNotNone(action)
        sent = []
        status = session.dispatch(action, lambda request, **_: sent.append(request) or 'sent',
            replace(hit, player_name='不同的中文名字'), harness.context, 10.3)
        self.assertEqual(status.state, 'cancelled')
        self.assertEqual(sent, [])

    def test_real_zip_links_name_fallback_to_english_dispatch_and_sender_confirmation(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        recorder = DiagnosticRecorder(base / 'logs', enabled=True)
        self.addCleanup(recorder.close)
        harness = language_cases.LanguageWorkerTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        harness.helper.diagnostics = recorder
        original_poll = harness.reader.poll
        def poll(pid, hwnd, now):
            snapshot = original_poll(pid, hwnd, now)
            snapshot.hits = (replace(snapshot.hits[0], player_name='电脑 - 专家'),)
            return snapshot
        harness.reader.poll = poll
        active, editor = False, ''
        typed = []
        def inspect():
            return ChatInputState(11, 22, 'synthetic-match', active, False,
                'team' if active else None, editor if active else None)
        def open_chat(target, *, allowed):
            nonlocal active, editor
            if not allowed():return False
            active, editor = True, ''
            return True
        def type_text(text, target, *, allowed):
            nonlocal editor
            if not allowed():return False
            editor = text
            typed.append(text)
            return True
        def submit(target, *, allowed):
            nonlocal active
            if not allowed():return False
            active = False
            return True
        harness.backend.open_team_chat = open_chat
        harness.backend.type_unicode = type_text
        harness.backend.submit_chat = submit
        boundary = TeamChatSender(harness.backend, inspect=inspect, diagnostics=recorder)
        def sender(action, *, allowed):
            result = boundary.send_result(action, allowed=allowed)
            harness.sent.append(action)
            return result
        harness.helper.sender_factory = lambda *_: sender
        harness.helper.set_language('en', request_ref='english-name-regression')
        harness.enable()
        harness.click()
        self.assertTrue(language_cases.wait_for(lambda: harness.helper._session._first_click is not None))
        harness.click()
        self.assertTrue(language_cases.wait_for(lambda: len(harness.sent) == 1))
        harness.helper.set_enabled(False)
        self.assertEqual(typed, ['[Ally] Player 4 (Jaina) | Hero damage: 12,345'])
        self.assertFalse(any(has_chinese(text) for text in typed))
        with zipfile.ZipFile(recorder.export_report(base / 'names.zip')) as archive:
            rows = [json.loads(line) for name in archive.namelist() if name.startswith('helper.log')
                for line in archive.read(name).decode('utf-8').splitlines()]
        normalized = [row for row in rows if row.get('event') == 'helper.read'
            and row.get('stage') == 'localization' and row.get('outcome') == 'applied'
            and row.get('reason') == 'english_name_fallback']
        self.assertTrue(normalized)
        self.assertTrue(all(row.get('target_language') == 'en' for row in normalized))
        self.assertTrue(all(type(row.get('attempt_id')) is int and row['attempt_id'] > 0
                            for row in normalized))
        self.assertFalse(any('电脑' in str(row) for row in normalized))
        reference = f'board-action-{harness.sent[0].token}'
        linked = [row for row in rows if row.get('request_ref') == reference]
        self.assertTrue(any(row.get('stage') == 'broadcast.confirm'
                            and row.get('outcome') == 'sent' for row in linked))
        self.assertGreaterEqual(len(linked), 3)
        self.assertTrue(all(row.get('target_language') == 'en' for row in linked))


if __name__ == '__main__':
    unittest.main()
