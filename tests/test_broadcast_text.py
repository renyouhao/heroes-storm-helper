"""离线英文资料、精确 ID 映射与合成面板元数据；不触碰游戏或云服务。"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hots_chat import broadcast_text
from hots_chat.diagnostics import DiagnosticRecorder
from hots_chat.quest_catalog import QuestCatalog
from hots_chat.talent_catalog import TalentCatalog
from hots_chat.talent_broadcast import (CooldownHitSnapshot, HitSnapshot, QuestHitSnapshot,
                                        StatHitSnapshot)
from tests import test_board_source as source_tests
from tests import test_board_progress as progress_tests


class BroadcastTextTests(unittest.TestCase):
    def setUp(self):
        self.common = dict(pid=123, hwnd=77, match_token='match', player_slot=3,
            team_relation='ally', rect=(10, 20, 50, 60), observed_at=10., valid_until=11.,
            panel_visible=True, mapping_trusted=True, player_name='玩家昵称',
            hero_name='吉安娜', hero_id='Jaina')
        self.hit = HitSnapshot(**self.common, tier_level=4,
            talent_id='JainaFrostbiteFrostArmor', talent_name='寒冰护甲',
            effect_summary='受到普攻时获得物理护甲。', selected=True,
            advice='中文建议不能混入英文', advice_verified=True)

    def test_english_catalog_is_complete_and_preserves_native_metadata(self):
        original, english = TalentCatalog(), TalentCatalog(locale='enUS')
        self.assertEqual((english.hero_count, english.talent_count), (91, 1941))
        original_rows = {(hero, talent): record for hero, talent, record in original.entries()}
        for hero, talent, record in english.entries():
            expected = original_rows[hero, talent]
            self.assertEqual((record.face_id, record.level, record.tier),
                             (expected.face_id, expected.level, expected.tier))
            self.assertTrue(broadcast_text._english(record.talent_name))
            self.assertTrue(broadcast_text._english(record.effect_summary))
            self.assertLessEqual(len(record.effect_summary), 200)
            self.assertTrue(broadcast_text._english(english.hero_name(hero)))
        self.assertEqual(english.resolve('Tassadar', 'TassadarHeroicAbilityArchon').talent_name,
                         'Archon')
        self.assertEqual(english.resolve('Chromie', 'ChromieHereAndThere').level, 11)

    def test_english_quest_catalog_preserves_all_exact_task_and_face_ids(self):
        original, english = QuestCatalog(), QuestCatalog(locale='enUS')
        self.assertEqual((english.hero_count, english.quest_count), (16, 16))
        expected = {(hero, quest): record.face_id for hero, quest, record in original.entries()}
        self.assertEqual({(hero, quest): record.face_id for hero, quest, record in english.entries()},
                         expected)
        for _hero, _quest, record in english.entries():
            self.assertTrue(broadcast_text._english(record.quest_name))

    def test_source_metadata_checksums_match_the_packaged_english_files(self):
        data = Path(__file__).resolve().parents[1] / 'data'
        source = json.loads((data / 'talent_catalog_source_enUS.json').read_text(encoding='utf-8'))
        self.assertEqual(source['commit'], 'e3e57cb640f18002a4feebe3872cefe04072d814')
        self.assertEqual((source['heroes'], source['talents'], source['quests']), (91, 1941, 16))
        self.assertEqual(len(source['inputs']), 136)
        for name, expected in source['compiled_sha256'].items():
            self.assertEqual(hashlib.sha256((data / name).read_bytes()).hexdigest(), expected)
        self.assertTrue(all(len(row['sha256']) == 64 for row in source['inputs']))

    def test_chinese_returns_the_same_snapshot_without_loading_other_data(self):
        with patch.object(broadcast_text, '_english_catalogs', side_effect=AssertionError('load')):
            self.assertIs(broadcast_text.localize_hit(self.hit, 'zh'), self.hit)
            self.assertEqual(broadcast_text.localization_reason(self.hit, 'zh'), '')

    def test_english_replaces_only_localized_text_and_drops_untranslated_advice(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('network')):
            result = broadcast_text.localize_hit(self.hit, 'en')
        self.assertEqual((result.hero_name, result.talent_name, result.effect_summary),
            ('Jaina', 'Frost Armor', 'Chill attackers and gain Physical Armor'))
        self.assertEqual((result.advice, result.advice_verified), ('', False))
        self.assertEqual((result.player_name, result.hero_id, result.talent_id, result.rect),
                         ('Player 4', 'Jaina', self.hit.talent_id, self.hit.rect))
        self.assertEqual(self.hit.player_name, '玩家昵称')
        self.assertEqual(self.hit.hero_name, '吉安娜')
        self.assertEqual(broadcast_text.localization_reason(self.hit), '')

    def test_quest_cooldown_and_stat_names_use_the_same_verified_hero(self):
        quest = QuestHitSnapshot(**self.common, quest_id='JainaFrostbiteTokenCounter',
            quest_name='寒冰指', progress=42, state='known')
        cooldown = CooldownHitSnapshot(**self.common, ability_id='JainaRingOfFrost',
            ability_name='冰霜之环', remaining_seconds=38., state='cooldown')
        stat = StatHitSnapshot(**self.common, metric_id='hero_damage', value=12345)
        self.assertEqual(broadcast_text.localize_hit(quest, 'en').quest_name, 'Frostbite')
        self.assertEqual(broadcast_text.localize_hit(cooldown, 'en').ability_name, 'Ring of Frost')
        self.assertEqual(broadcast_text.localize_hit(stat, 'en').hero_name, 'Jaina')
        self.assertEqual(TalentCatalog(locale='enUS').ability_name('Kaelthas', 'KaelthasPhoenix'),
                         'Phoenix')
        self.assertIsNone(TalentCatalog(locale='enUS').ability_name('Jaina', 'KaelthasPhoenix'))

    def test_unknown_or_mismatched_ids_never_fall_back_to_chinese_or_guessed_names(self):
        for changed, reason in (
            (replace(self.hit, hero_id=''), 'english_hero_unavailable'),
            (replace(self.hit, hero_id='jaina'), 'english_hero_unavailable'),
            (replace(self.hit, hero_id='Chromie'), 'english_item_unavailable'),
            (replace(self.hit, talent_id='Missing'), 'english_item_unavailable'),
            (replace(self.hit, tier_level=13), 'english_item_unavailable'),
        ):
            self.assertIsNone(broadcast_text.localize_hit(changed, 'en'))
            self.assertEqual(broadcast_text.localization_reason(changed), reason)
        self.assertIsNone(broadcast_text.localize_hit(self.hit, 'ko'))

    def test_missing_or_wrong_locale_catalog_has_a_distinct_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / 'missing.json'
            catalogs = (TalentCatalog(missing, locale='enUS'), QuestCatalog(missing, locale='enUS'))
            with patch.object(broadcast_text, '_english_catalogs', return_value=catalogs):
                self.assertIsNone(broadcast_text.localize_hit(self.hit, 'en'))
                self.assertEqual(broadcast_text.localization_reason(self.hit),
                                 'english_catalog_unavailable')
            path = Path(tmp) / 'wrong-locale.json'
            path.write_text('{"schema":1,"build":98348,"locale":"zhCN","heroes":{}}')
            self.assertEqual(TalentCatalog(path, locale='enUS').talent_count, 0)

    def test_missing_quest_catalog_does_not_disable_english_statistics(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalogs = (TalentCatalog(locale='enUS'), QuestCatalog(Path(tmp) / 'missing', locale='enUS'))
            quest = QuestHitSnapshot(**self.common, quest_id='JainaFrostbiteTokenCounter',
                quest_name='任务', progress=42, state='known')
            stat = StatHitSnapshot(**self.common, metric_id='hero_damage', value=12345)
            with patch.object(broadcast_text, '_english_catalogs', return_value=catalogs):
                self.assertEqual(broadcast_text.localization_reason(quest), 'english_catalog_unavailable')
                self.assertIsNone(broadcast_text.localize_hit(quest, 'en'))
                self.assertEqual(broadcast_text.localize_hit(stat, 'en').hero_name, 'Jaina')

    def test_non_english_catalog_text_is_rejected_without_affecting_chinese(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'catalog.json'
            path.write_text(json.dumps(dict(schema=1, build=98348, locale='enUS', heroes={
                'Jaina': {'name': 'Jaina', 'talents': {self.hit.talent_id: {
                    'name': '中文', 'effect_summary': 'English effect', 'status': 'available',
                    'face_id': 'JainaFrostbiteFrostArmorTalent', 'level': 4, 'tier': 2}}}})),
                encoding='utf-8')
            catalogs = (TalentCatalog(path, locale='enUS'), QuestCatalog(locale='enUS'))
            with patch.object(broadcast_text, '_english_catalogs', return_value=catalogs):
                self.assertIsNone(broadcast_text.localize_hit(self.hit, 'en'))
                self.assertEqual(broadcast_text.localization_reason(self.hit), 'english_item_unavailable')
                self.assertIs(broadcast_text.localize_hit(self.hit, 'zh'), self.hit)


class BoardEnglishIdentityTests(unittest.TestCase):
    setUp = source_tests.BoardReaderTests.setUp
    tearDown = source_tests.BoardReaderTests.tearDown
    poll = source_tests.BoardReaderTests.poll

    def test_talent_retains_verified_hero_id_instead_of_inferring_displayed_name(self):
        self.assertEqual(self.poll().hits[0].hero_id, 'Chromie')

    def test_statistics_get_exact_hero_id_even_when_the_talent_tree_is_hidden(self):
        import hots_chat.acquisition.board_source as source
        self.memory.add_stat()
        with patch.object(source, '_model', wraps=source._model) as read_model:
            result = self.poll()
        self.assertEqual(read_model.call_count, 2)
        self.assertEqual((result.hits[0].hero_id, result.hits[0].metric_id), ('Chromie', 'hero_damage'))
        self.assertEqual(broadcast_text.localize_hit(result.hits[0], 'en').hero_name, 'Chromie')

    def test_missing_stat_hero_id_preserves_chinese_values_and_refuses_english(self):
        self.memory.add_stat()
        with patch('hots_chat.acquisition.board_source._model', side_effect=ValueError('unknown')):
            result = self.poll()
        self.assertEqual(result.status, 'available', result.reason)
        hit, = result.hits
        self.assertEqual((hit.hero_id, hit.value), ('', 133027))
        self.assertIs(broadcast_text.localize_hit(hit, 'zh'), hit)
        self.assertIsNone(broadcast_text.localize_hit(hit, 'en'))
        self.assertEqual(broadcast_text.localization_reason(hit), 'english_hero_unavailable')

    def test_stat_hero_identity_change_between_captures_is_rejected(self):
        self.memory.add_stat()
        with patch('hots_chat.acquisition.board_source._link',
                   side_effect=[('Chromie', 0), ('Jaina', 0)]):
            result = self.poll()
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(result.hits, ())

    def test_quest_and_cooldown_keep_the_captured_hero_id(self):
        fixture = progress_tests.BoardProgressTests()
        fixture.setUp()
        try:
            fixture.memory.add_baseline()
            result = fixture.poll()
            types = {type(hit) for hit in result.hits}
            self.assertTrue({HitSnapshot, QuestHitSnapshot, CooldownHitSnapshot} <= types)
            self.assertTrue(all(hit.hero_id == 'Chromie' for hit in result.hits))
        finally:
            fixture.tearDown()

    def test_enabling_diagnostics_does_not_add_statistics_hero_memory_reads(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            for enabled in (False, True):
                fixture = source_tests.BoardReaderTests()
                fixture.setUp()
                recorder = DiagnosticRecorder(Path(tmp) / str(enabled), enabled=enabled)
                try:
                    fixture.memory.add_stat()
                    fixture.memory.calls.clear()
                    fixture.reader.diagnostics = recorder
                    result = fixture.poll()
                    self.assertEqual(result.hits[0].hero_id, 'Chromie')
                    calls.append(tuple(fixture.memory.calls))
                finally:
                    fixture.tearDown()
                    recorder.close()
        self.assertEqual(calls[0], calls[1])


if __name__ == '__main__':unittest.main()
