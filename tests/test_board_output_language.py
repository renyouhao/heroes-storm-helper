"""双击播报语言契约；只使用离线目录、合成快照及内存发送边界。"""
from copy import deepcopy
from contextlib import nullcontext
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from hots_chat.board_sender import ChatInputState, TeamChatSender
from hots_chat.board_service import BoardHelper
from hots_chat.diagnostics import DiagnosticRecorder
from hots_chat.helper_replay import _decode, _encode_plain, replay_helper_frame
from hots_chat.loot_native import GameTarget
from hots_chat.quest_catalog import QuestCatalog
from hots_chat.talent_catalog import TalentCatalog
from hots_chat.talent_broadcast import (
    CooldownHitSnapshot, DispatchAction, HitSnapshot, InputContext, QuestHitSnapshot,
    StatHitSnapshot, TalentBroadcastConfig, TalentBroadcastSession,
    format_cooldown_message, format_quest_message, format_stat_message,
    format_talent_message,
)


def wait_for(predicate, timeout=2.):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.005)
    return False


def hits(now=10.):
    common = dict(pid=11, hwnd=22, match_token='synthetic-match', player_slot=3,
        team_relation='ally', rect=(100, 200, 160, 260), observed_at=now,
        valid_until=now + 1., panel_visible=True, mapping_trusted=True,
        player_name='Player', hero_name='吉安娜', hero_id='Jaina')
    return (
        HitSnapshot(**common, tier_level=4, talent_id='JainaFrostbiteFrostArmor',
            talent_name='寒冰护甲', effect_summary='使攻击者冰冻并获得物理护甲。',
            selected=True, advice='不要输出这段中文建议', advice_verified=True),
        StatHitSnapshot(**common, metric_id='hero_damage', value=12345),
        QuestHitSnapshot(**common, quest_id='JainaFrostbiteTokenCounter',
            quest_name='霜寒', progress=1234, state='known'),
        CooldownHitSnapshot(**common, ability_id='JainaRingOfFrost',
            ability_name='冰霜之环', remaining_seconds=12.5, state='cooldown'),
    )


FORMATTERS = (format_talent_message, format_stat_message, format_quest_message,
              format_cooldown_message)


class LanguageSessionTests(unittest.TestCase):
    def setUp(self):
        self.context = InputContext(11, 22, 'synthetic-match', True)

    def session(self, language='zh', diagnostics=None):
        return TalentBroadcastSession(double_click_ms=500, double_click_size=(4, 4),
            config=TalentBroadcastConfig(enabled=True, language=language),
            diagnostics=diagnostics)

    def ready(self, session, hit=None, start=10.):
        hit = hits()[1] if hit is None else hit
        for now in (start, start + .2):
            session.mouse(120, 220, True, hit, self.context, now)
            session.mouse(120, 220, False, hit, self.context, now)
        return session.tick(hit, self.context, start + .2)

    def test_config_defaults_remain_chinese_and_only_supported_languages_are_accepted(self):
        self.assertEqual(TalentBroadcastConfig(True, True, 150).language, 'zh')
        for language in ('zh', 'en'):
            self.assertEqual(TalentBroadcastConfig(language=language).language, language)
        for language in ('', 'EN', 'zhCN', 'ko', None, True, 1):
            with self.subTest(language=language), self.assertRaises(ValueError):
                TalentBroadcastConfig(language=language)

    def test_all_formatters_keep_exact_default_chinese_text(self):
        expected = (
            '注意【我方】Player（吉安娜）｜4级天赋「寒冰护甲」：使攻击者冰冻并获得物理护甲。',
            '【我方】Player（吉安娜）｜英雄伤害：12,345',
            '注意【我方】Player（吉安娜）｜基础任务「霜寒」｜任务层数：1,234',
            '注意【我方】Player（吉安娜）｜大招「冰霜之环」｜冷却显示：12.5秒',
        )
        for formatter, hit, text in zip(FORMATTERS, hits(), expected):
            with self.subTest(kind=type(hit).__name__):
                self.assertEqual(formatter(hit), text)
                self.assertEqual(formatter(hit, language='zh'), text)

    def test_english_templates_localize_chinese_snapshots_without_mutating_reader_data(self):
        expected = (
            'Watch [Ally] Player (Jaina) | L4 "Frost Armor": '
            'Chill attackers and gain Physical Armor',
            '[Ally] Player (Jaina) | Hero damage: 12,345',
            'Watch [Ally] Player (Jaina) | Quest "Frostbite" | Quest stacks: 1,234',
            'Watch [Ally] Player (Jaina) | Heroic "Ring of Frost" | Cooldown shown: 12.5s',
        )
        for formatter, hit, text in zip(FORMATTERS, hits(), expected):
            before = replace(hit)
            with self.subTest(kind=type(hit).__name__):
                self.assertEqual(formatter(hit, language='en'), text)
                self.assertEqual(hit, before)
                self.assertEqual(hit.hero_name, '吉安娜')
        self.assertEqual(format_talent_message(hits()[0], language='en', include_advice=True),
                         expected[0])

    def test_english_dynamic_states_remain_observations_and_keep_valid_zero_values(self):
        talent, stat, quest, cooldown = hits()
        self.assertIn('Quest stacks: 0', format_quest_message(
            replace(quest, progress=0), language='en'))
        self.assertIn('Quest complete (1,234 stacks)', format_quest_message(
            replace(quest, state='complete'), language='en'))
        self.assertIn('Quest complete', format_talent_message(
            replace(talent, quest_state='complete'), language='en', max_chars=240))
        self.assertIn('Hero damage: 0', format_stat_message(replace(stat, value=0), language='en'))
        self.assertIn(' | Ready', format_cooldown_message(
            replace(cooldown, state='ready', remaining_seconds=0), language='en'))
        for formatter, hit in ((format_stat_message, replace(stat, value=None)),
                (format_quest_message, replace(quest, state='unknown')),
                (format_cooldown_message, replace(cooldown, state='unknown'))):
            self.assertIsNone(formatter(hit, language='en'))

    def test_missing_english_identity_or_item_never_falls_back_to_chinese_or_raw_ids(self):
        for formatter, hit in zip(FORMATTERS, hits()):
            with self.subTest(kind=type(hit).__name__):
                self.assertIsNone(formatter(replace(hit, hero_id=''), language='en'))
                self.assertIsNone(formatter(replace(hit, hero_id='SyntheticUnknown'), language='en'))
        for formatter, hit, field in zip((FORMATTERS[0], FORMATTERS[2], FORMATTERS[3]),
                (hits()[0], hits()[2], hits()[3]), ('talent_id', 'quest_id', 'ability_id')):
            self.assertIsNone(formatter(replace(hit, **{field: 'SyntheticUnknown'}), language='en'))

    def test_english_messages_clean_untrusted_nicknames_and_respect_all_size_limits(self):
        nickname = '<c>Player</c>\r\n/all\x00\u202e' + 'x' * 150
        for limit in (100, 150, 240):
            for formatter, hit in zip(FORMATTERS, hits()):
                with self.subTest(limit=limit, kind=type(hit).__name__):
                    text = formatter(replace(hit, player_name=nickname), language='en', max_chars=limit)
                    self.assertIsNotNone(text)
                    self.assertLessEqual(len(text), limit)
                    for forbidden in ('\r', '\n', '\x00', '\u202e', '<', '>', '/', '/all'):
                        self.assertNotIn(forbidden, text)

    def test_action_freezes_language_and_dynamic_value_after_handoff(self):
        session, hit = self.session('en'), hits()[1]
        action = self.ready(session, hit)
        self.assertEqual(action.language, 'en')
        self.assertEqual(action.message, '[Ally] Player (Jaina) | Hero damage: 12,345')
        with self.assertRaises(FrozenInstanceError):
            action.language = 'zh'
        sent = []
        status = session.dispatch(action,
            lambda value, **_: sent.append(value) or 'sent',
            replace(hit, value=54321), self.context, 10.3)
        self.assertEqual(status.state, 'sent')
        self.assertEqual(sent, [action])
        self.assertNotIn('54,321', sent[0].message)

    def test_english_pending_is_cancelled_when_stable_hero_id_changes_despite_same_display_text(self):
        session, hit = self.session('en'), hits()[1]
        action = self.ready(session, hit)
        changed = replace(hit, hero_id='Anduin')
        self.assertEqual(changed.hero_name, hit.hero_name)
        self.assertEqual(changed.metric_id, hit.metric_id)
        self.assertEqual(changed.value, hit.value)
        self.assertEqual(format_stat_message(changed, language='en'),
                         '[Ally] Player (Anduin) | Hero damage: 12,345')
        self.assertIsNone(session.tick(changed, self.context, 10.3))
        self.assertEqual(session.status.state, 'cancelled')
        sent = []
        result = session.dispatch(action,
            lambda value, **_: sent.append(value) or 'sent', changed, self.context, 10.3)
        self.assertEqual(result.state, 'cancelled')
        self.assertEqual(sent, [])

    def test_reconfiguration_cancels_an_already_offered_action_and_stop_keeps_language(self):
        session, hit = self.session('en'), hits()[1]
        action = self.ready(session, hit)
        session.configure(TalentBroadcastConfig(enabled=True, language='zh'))
        sent = []
        result = session.dispatch(action,
            lambda value, **_: sent.append(value) or 'sent', hit, self.context, 10.3)
        self.assertEqual(result.state, 'cancelled')
        self.assertEqual(sent, [])
        session.configure(TalentBroadcastConfig(enabled=True, language='en'))
        session.stop()
        self.assertEqual(session.config.language, 'en')
        self.assertFalse(session.config.enabled)

    def test_language_switch_does_not_reset_cross_language_rate_limit(self):
        session, hit = self.session('en'), hits()[1]
        action = self.ready(session, hit)
        self.assertEqual(session.dispatch(action, lambda *_args, **_kwargs: 'sent',
                         hit, self.context, 10.2).state, 'sent')
        session.configure(TalentBroadcastConfig(enabled=True, language='zh'))
        self.assertIsNone(self.ready(session, hits(12.)[1], start=12.))
        self.assertEqual(session.status.state, 'limited')

    @staticmethod
    def strip_new_fields(value):
        if isinstance(value, dict):
            if value.get('type') in ('HitSnapshot', 'StatHitSnapshot', 'QuestHitSnapshot',
                                      'CooldownHitSnapshot'):
                value['fields'].pop('hero_id', None)
            if value.get('type') in ('TalentBroadcastConfig', 'DispatchAction'):
                value['fields'].pop('language', None)
            for item in value.values():
                LanguageSessionTests.strip_new_fields(item)
        elif isinstance(value, list):
            for item in value:
                LanguageSessionTests.strip_new_fields(item)

    def test_legacy_frames_default_to_chinese_without_rewriting_expected_messages(self):
        class Frames:
            def __init__(self): self.frames = []
            def snapshot(self): return {'enabled': True}
            def record(self, event, **fields):
                if event == 'replay.frame': self.frames.append(deepcopy(fields))
        recorder = Frames()
        session = self.session(diagnostics=recorder)
        self.assertIsNotNone(self.ready(session))
        self.strip_new_fields(recorder.frames)
        self.assertTrue(recorder.frames)
        for frame in recorder.frames:
            result = replay_helper_frame(frame['component'], frame['operation'], frame['payload'])
            self.assertTrue(result['matched'], result)
        altered = deepcopy(recorder.frames[-1])
        altered['payload']['expected']['result']['fields']['message'] = 'Recorded text changed'
        result = replay_helper_frame(altered['component'], altered['operation'], altered['payload'])
        self.assertFalse(result['matched'])
        self.assertIn('result', result['mismatches'])

    def test_legacy_migration_only_allows_documented_new_defaults(self):
        action = DispatchAction(1, 11, 22, 'synthetic-match', 3, '', 'Synthetic message')
        for value in (*hits(), TalentBroadcastConfig(), action):
            encoded = _encode_plain(value)
            self.strip_new_fields(encoded)
            restored = _decode(encoded)
            if hasattr(restored, 'language'):
                self.assertEqual(restored.language, 'zh')
            else:
                self.assertEqual(restored.hero_id, '')
            extra = deepcopy(encoded)
            extra['fields']['unsupported'] = 1
            with self.assertRaises(ValueError):
                _decode(extra)
        incomplete = _encode_plain(hits()[1])
        self.strip_new_fields(incomplete)
        del incomplete['fields']['pid']
        with self.assertRaises(ValueError):
            _decode(incomplete)


class LanguageWorkerTests(unittest.TestCase):
    def setUp(self):
        self.target = GameTarget(22, 11)
        self.hooks = threading.Event()
        self.reads, self.sent = [], []
        outer = self
        class Backend:
            def foreground_target(self): return outer.target
            def double_click_settings(self): return 500, (4, 4)
            def input_idle(self): return True
            def run(self, key, mouse, tick, ready, stopped):
                self.key, self.mouse = key, mouse
                ready()
                outer.hooks.set()
                stopped.wait()
            def stop(self): pass
        class Reader:
            def poll(self, pid, hwnd, now):
                outer.reads.append((pid, hwnd))
                return SimpleNamespace(pid=pid, hwnd=hwnd, match_token='synthetic-match',
                    status='available', hits=(hits(now)[1],), chat_active=False, menu_active=False)
            def close(self): pass
        self.backend, self.reader = Backend(), Reader()
        def sender(action, *, allowed):
            if allowed():
                self.sent.append(action)
                return 'sent'
            return 'failed'
        self.helper = BoardHelper(backend_factory=lambda: self.backend,
            reader_factory=lambda _: self.reader, sender_factory=lambda *_: sender)
        self.addCleanup(self.helper.close)

    def enable(self):
        self.assertTrue(self.helper.set_enabled(True))
        self.assertTrue(self.hooks.wait(2))

    def click(self):
        self.backend.mouse(120, 220, True, 'left', False)
        self.backend.mouse(120, 220, False, 'left', False)

    def test_changing_disabled_language_does_not_read_game_or_create_worker(self):
        self.assertEqual(self.helper.language, 'zh')
        self.helper.set_language('en')
        self.assertEqual(self.helper.language, 'en')
        self.assertEqual(self.reads, [])
        self.assertIsNone(self.helper.thread)
        for invalid in ('', 'enUS', None, True):
            with self.subTest(language=invalid), self.assertRaises(ValueError):
                self.helper.set_language(invalid)
        self.assertEqual(self.helper.language, 'en')
        self.enable()
        self.click()
        self.assertTrue(wait_for(lambda: self.helper._session._first_click is not None))
        self.click()
        self.assertTrue(wait_for(lambda: len(self.sent) == 1))
        self.assertEqual(self.sent[0].language, 'en')
        self.assertIn('Hero damage', self.sent[0].message)

    def test_switch_during_slow_second_read_invalidates_old_gesture_before_worker_updates(self):
        self.enable()
        self.click()
        self.assertTrue(wait_for(lambda: self.helper._session._first_click is not None))
        started, finish = threading.Event(), threading.Event()
        original = self.reader.poll
        def slow(pid, hwnd, now):
            started.set()
            finish.wait(2)
            return original(pid, hwnd, time.monotonic())
        self.reader.poll = slow
        self.click()
        self.assertTrue(started.wait(2))
        previous = self.helper._revision
        self.helper.set_language('en')
        self.assertGreater(self.helper._revision, previous)
        finish.set()
        self.assertTrue(wait_for(lambda: self.helper._session.config.language == 'en'))
        self.assertEqual(self.sent, [])
        self.reader.poll = original
        self.click()
        self.assertTrue(wait_for(lambda: self.helper._session._first_click is not None))
        self.click()
        self.assertTrue(wait_for(lambda: len(self.sent) == 1))
        self.assertEqual(self.sent[0].language, 'en')

    def test_round_trip_language_change_during_first_read_does_not_swallow_next_double_click(self):
        started, finish = threading.Event(), threading.Event()
        self.addCleanup(finish.set)
        original = self.reader.poll
        def slow(pid, hwnd, now):
            started.set()
            finish.wait(2)
            return original(pid, hwnd, time.monotonic())
        self.reader.poll = slow
        self.enable()
        self.click()
        self.assertTrue(started.wait(2))
        self.helper.set_language('en')
        self.helper.set_language('zh')
        self.reader.poll = original
        finish.set()
        self.assertTrue(wait_for(lambda: self.helper._inputs.empty()
            and self.helper._session._first_click is None))
        self.assertEqual(self.helper.language, 'zh')
        self.assertEqual(self.helper._session.config.language, 'zh')
        self.assertEqual(self.sent, [])
        self.assertFalse(self.helper._session._left_held)
        self.click()
        self.assertTrue(wait_for(lambda: self.helper._session._first_click is not None))
        self.click()
        self.assertTrue(wait_for(lambda: len(self.sent) == 1))
        self.assertEqual(self.sent[0].language, 'zh')
        self.assertEqual(self.sent[0].message, '【我方】Player（吉安娜）｜英雄伤害：12,345')

    def test_switch_after_sender_handoff_closes_the_next_input_gate(self):
        entered, resume, finished = threading.Event(), threading.Event(), threading.Event()
        allowed_after_switch = []
        def sender(action, *, allowed):
            self.assertEqual(action.language, 'en')
            entered.set()
            resume.wait(2)
            allowed_after_switch.append(allowed())
            if allowed_after_switch[-1]:
                self.sent.append(action)
            finished.set()
            return 'failed'
        self.helper.sender_factory = lambda *_: sender
        self.helper.set_language('en')
        self.enable()
        self.click()
        self.assertTrue(wait_for(lambda: self.helper._session._first_click is not None))
        self.click()
        self.assertTrue(entered.wait(2))
        self.helper.set_language('zh')
        resume.set()
        self.assertTrue(finished.wait(2))
        self.assertEqual(allowed_after_switch, [False])
        self.assertEqual(self.sent, [])
        self.assertTrue(wait_for(lambda: self.helper._session.config.language == 'zh'))

    def test_recording_failure_does_not_change_english_configuration_or_sending(self):
        class BrokenRecorder:
            def record(self, *args, **kwargs):
                raise RuntimeError('synthetic recorder failure')
        self.helper.diagnostics = BrokenRecorder()
        self.helper.set_language('en')
        self.enable()
        self.click()
        self.assertTrue(wait_for(lambda: self.helper._session._first_click is not None))
        self.click()
        self.assertTrue(wait_for(lambda: len(self.sent) == 1))
        self.assertEqual(self.sent[0].language, 'en')

    def test_real_zip_identifies_missing_english_catalog_and_never_calls_sender(self):
        self._assert_lookup_rejection('catalog')

    def test_real_zip_identifies_unmatched_english_item_and_never_calls_sender(self):
        self._assert_lookup_rejection('item')

    def _assert_lookup_rejection(self, unavailable):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        recorder = DiagnosticRecorder(directory / 'logs', enabled=True)
        self.addCleanup(recorder.close)
        self.addCleanup(self.helper.close)
        self.helper.diagnostics = recorder
        def poll(pid, hwnd, now):
            hit = hits(now)[0]
            if unavailable == 'item':
                hit = replace(hit, talent_id='SyntheticMissingTalent')
            return SimpleNamespace(pid=pid, hwnd=hwnd, match_token='synthetic-match',
                status='available', hits=(hit,), chat_active=False, menu_active=False)
        self.reader.poll = poll
        catalogs = (TalentCatalog(directory / 'absent-talents.json', locale='enUS'),
                    QuestCatalog(directory / 'absent-quests.json', locale='enUS'))
        override = patch('hots_chat.broadcast_text._english_catalogs', return_value=catalogs)
        with override if unavailable == 'catalog' else nullcontext():
            self.helper.set_language('en')
            self.enable()
            self.click()
            self.assertTrue(wait_for(lambda: self.helper._session.status.state == 'cancelled'))
            self.helper.set_enabled(False)
        report = recorder.export_report(directory / 'report.zip')
        with zipfile.ZipFile(report) as archive:
            rows = [json.loads(line) for name in archive.namelist()
                    if name.startswith('helper.log')
                    for line in archive.read(name).decode().splitlines()]
        expected = f'english_{unavailable}_unavailable'
        rejection = [row for row in rows if row.get('event') == 'helper.read'
                     and row.get('stage') == 'localization' and row.get('outcome') == 'rejected'
                     and row.get('reason') == 'english_text_unavailable'
                     and row.get('error_code') == expected]
        self.assertTrue(rejection, rows)
        self.assertTrue(all(row.get('target_language') == 'en' for row in rejection))
        self.assertEqual(self.sent, [])

    def test_real_zip_retains_language_on_configuration_dispatch_and_sender_confirmation(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        recorder = DiagnosticRecorder(Path(temporary.name) / 'logs', enabled=True)
        self.addCleanup(recorder.close)
        self.addCleanup(self.helper.close)
        recorder.register_secrets('synthetic-secret-key')
        self._assert_real_zip_language(recorder, Path(temporary.name))

    def _assert_real_zip_language(self, recorder, temporary):
        self.helper.diagnostics = recorder
        original_poll = self.reader.poll
        def poll(pid, hwnd, now):
            snapshot = original_poll(pid, hwnd, now)
            snapshot.hits = (replace(snapshot.hits[0], player_name='synthetic-secret-key'),)
            return snapshot
        self.reader.poll = poll
        active, editor = False, ''
        def inspect():
            return ChatInputState(11, 22, 'synthetic-match', active, False,
                'team' if active else None, editor if active else None)
        def open_chat(target, *, allowed):
            nonlocal active, editor
            if not allowed(): return False
            active, editor = True, ''
            return True
        def type_text(text, target, *, allowed):
            nonlocal editor
            if not allowed(): return False
            editor = text
            return True
        def submit(target, *, allowed):
            nonlocal active
            if not allowed(): return False
            active = False
            return True
        self.backend.open_team_chat = open_chat
        self.backend.type_unicode = type_text
        self.backend.submit_chat = submit
        actual_sender = TeamChatSender(self.backend, inspect=inspect, diagnostics=recorder)
        def sender(action, *, allowed):
            result = actual_sender.send_result(action, allowed=allowed)
            self.sent.append(action)
            return result
        self.helper.sender_factory = lambda *_: sender
        self.helper.set_language('en', request_ref='synthetic-language-change')
        self.enable()
        self.click()
        self.assertTrue(wait_for(lambda: self.helper._session._first_click is not None))
        self.click()
        self.assertTrue(wait_for(lambda: len(self.sent) == 1))
        self.assertTrue(wait_for(lambda: self.helper._session.status.state == 'sent'))
        self.helper.set_language('zh', request_ref='synthetic-language-return')
        self.assertTrue(wait_for(lambda: self.helper._session.config.language == 'zh'))
        self.helper.set_enabled(False)
        report = recorder.export_report(temporary / 'report.zip')
        with zipfile.ZipFile(report) as archive:
            content = '\n'.join(archive.read(name).decode() for name in archive.namelist()
                if name.startswith('helper.log'))
        rows = [json.loads(line) for line in content.splitlines()]
        self.assertTrue(recorder.snapshot()['payload_redacted'])
        self.assertNotIn('synthetic-secret-key', content)
        self.assertTrue(any(row.get('target_language') == 'zh' for row in rows))
        self.assertTrue(any(row.get('target_language') == 'en' for row in rows))
        confirmation = [row for row in rows if row.get('stage') == 'broadcast.confirm'
                        and row.get('outcome') == 'sent']
        self.assertEqual(len(confirmation), 1)
        self.assertEqual(confirmation[0]['target_language'], 'en')
        reference = f'board-action-{self.sent[0].token}'
        self.assertEqual(confirmation[0]['request_ref'], reference)
        linked = [row for row in rows if row.get('request_ref') == reference]
        self.assertGreaterEqual(len(linked), 3)
        self.assertTrue(all(row.get('target_language') == 'en' for row in linked))
        self.assertTrue(any(row.get('request_ref') == 'synthetic-language-change'
                            and row.get('target_language') == 'en' for row in rows))
        self.assertTrue(any(row.get('request_ref') == 'synthetic-language-return'
                            and row.get('target_language') == 'zh' for row in rows))


if __name__ == '__main__':
    unittest.main()
