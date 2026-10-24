import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def _catalog_data(quests=None):
    return {'schema': 1, 'build': 98348, 'locale': 'zhCN', 'heroes': {
        'Arthas': {'quests': quests or {'Counter': {
            'quest_name': '任务', 'face_id': 'Button', 'status': 'available'}}}}}


class QuestCatalogTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('hots_chat.quest_catalog')

    def load_data(self, data):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / 'quests.json'
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return self.module.QuestCatalog(path), path

    def test_packaged_catalog_resolves_exact_arthas_link_without_network(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('network')):
            record = self.module.QuestCatalog().resolve(
                'Arthas', 'ArthasFrostmourneHungersTokenCounter')
        self.assertEqual(record, self.module.LocalizedQuest(
            '霜之哀伤的饥渴', 'ArthasFrostmourneHungersToken'))

    def test_hero_id_case_build_and_historical_global_definitions_are_not_guessed(self):
        catalog = self.module.QuestCatalog()
        quest = 'ArthasFrostmourneHungersTokenCounter'
        for hero, identifier, build in [
            ('Kaelthas', quest, 98348), ('arthas', quest, 98348),
            ('Arthas', quest.lower(), 98348), ('Arthas', quest, 98349),
            ('Arthas', quest, True), (None, quest, 98348),
            ('Arthas', [], 98348),
            ('Kaelthas', 'KaelthasFlamestrikeConvectionQuestToken', 98348),
            ('Deathwing', 'DeathwingCataclysmQuestToken', 98348),
        ]:
            with self.subTest(hero=hero, identifier=identifier, build=build):
                self.assertIsNone(catalog.resolve(hero, identifier, build=build))

    def test_records_require_safe_plain_text_exact_ids_and_available_status(self):
        good = {'quest_name': '审判官的祈祷',
                'face_id': "WhitemaneInquisitor'sPrayerQuestTokens", 'status': 'available'}
        entries = {'Known': good}
        for key, changes in {
            'Markup': {'quest_name': '<d ref="dynamic"/>'},
            'Control': {'quest_name': '任务\u202e文字'},
            'Newline': {'quest_name': '任务\n文字'},
            'Unavailable': {'status': 'candidate'},
            'Blank': {'quest_name': '  '},
            'Long': {'quest_name': '字' * 81},
            'Path': {'face_id': '../Button'},
        }.items():
            entries[key] = dict(good, **changes)
        catalog, _path = self.load_data(_catalog_data(entries))
        self.assertEqual(catalog.resolve('Arthas', 'Known').face_id, good['face_id'])
        for key in entries.keys() - {'Known'}:
            self.assertIsNone(catalog.resolve('Arthas', key), key)

    def test_bad_envelope_missing_and_malformed_files_fail_closed(self):
        for changes in [{'schema': True}, {'schema': 2}, {'build': True},
                        {'build': 98349}, {'locale': 'enUS'}, {'heroes': []}]:
            catalog, _path = self.load_data(dict(_catalog_data(), **changes))
            self.assertIsNone(catalog.resolve('Arthas', 'Counter'))
        catalog, path = self.load_data(_catalog_data())
        path.write_bytes(b'{"invalid":')
        self.assertIsNone(catalog.resolve('Arthas', 'Counter'))
        catalog, path = self.load_data(_catalog_data())
        path.unlink()
        self.assertIsNone(catalog.resolve('Arthas', 'Counter'))

    def test_oversized_and_excessive_record_counts_are_bounded(self):
        catalog, path = self.load_data(_catalog_data())
        path.write_bytes(b' ' * (128 * 1024 + 1))
        self.assertIsNone(catalog.resolve('Arthas', 'Counter'))
        data = _catalog_data()
        data['heroes'] = {f'Hero{i}': data['heroes']['Arthas'] for i in range(129)}
        catalog, _path = self.load_data(data)
        self.assertIsNone(catalog.resolve('Hero0', 'Counter'))
        data = _catalog_data({f'Quest{i}': {
            'quest_name': '任务', 'face_id': 'Button', 'status': 'available'} for i in range(9)})
        catalog, _path = self.load_data(data)
        self.assertIsNone(catalog.resolve('Arthas', 'Quest0'))

    def test_catalog_is_cached_and_packaged_records_keep_explicit_source_evidence(self):
        catalog, path = self.load_data(_catalog_data())
        self.assertIsNotNone(catalog.resolve('Arthas', 'Counter'))
        path.write_bytes(b'invalid')
        self.assertIsNotNone(catalog.resolve('Arthas', 'Counter'))
        catalog = self.module.QuestCatalog()
        data = json.loads(catalog.path.read_text(encoding='utf-8'))
        self.assertEqual(catalog.hero_count, 16)
        self.assertEqual(catalog.quest_count, 16)
        self.assertEqual(data['source']['repository'],
                         'https://github.com/jamiephan/HeroesOfTheStorm_Gamedata')
        self.assertRegex(data['source']['ref'], r'^[a-f0-9]{40}$')
        for hero, quests in data['heroes'].items():
            for identifier, record in quests['quests'].items():
                with self.subTest(hero=hero, quest=identifier):
                    evidence = record['evidence']
                    self.assertEqual(evidence['face_field'], 'Face')
                    self.assertTrue(evidence['localization_key'].startswith('Button/Name/'))
                    self.assertIsNotNone(catalog.resolve(hero, identifier))
                    for field in ['unit_source', 'face_source', 'localization_source']:
                        source = evidence[field]
                        self.assertTrue(source.startswith('mods/'))
                        self.assertNotIn('artifacts/', source)
                        self.assertRegex(data['source']['source_sha256'][source],
                                         r'^[a-f0-9]{64}$')


if __name__ == '__main__':
    unittest.main()
