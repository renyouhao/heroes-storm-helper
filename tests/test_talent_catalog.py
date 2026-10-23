import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class TalentCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = importlib.import_module('hots_chat.talent_catalog')

    def test_default_catalog_is_offline_and_resolves_face_alias_and_special_levels(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('network')):
            catalog = self.catalog.TalentCatalog()
            first = catalog.resolve('Chromie', 'ChromieSandBlastOnceAgainTheFirstTime')
            here = catalog.resolve('Chromie', 'ChromieHereAndThere')
        self.assertEqual(first.talent_name, '又一次的第一次')
        self.assertEqual(first.face_id, 'ChromieSandBlastOnceAgaintheFirstTime')
        self.assertEqual(here.level, 11)
        self.assertEqual(here.talent_name, '身影交错')
        self.assertTrue(first.effect_summary)

    def test_unknown_build_hero_talent_or_wrong_case_is_not_guessed(self):
        catalog = self.catalog.TalentCatalog()
        self.assertIsNone(catalog.resolve('Chromie', 'missing'))
        self.assertIsNone(catalog.resolve('chromie', 'ChromieHereAndThere'))
        self.assertIsNone(catalog.resolve('Chromie', 'ChromieHereAndThere', build=1))
        self.assertIsNone(catalog.resolve('Chromie', 'ChromieHereAndThere', level=13))

    def test_catalog_cannot_upgrade_unvalidated_or_dynamic_text_to_known(self):
        data = {'schema':1,'build':98348,'locale':'zhCN','heroes':{'Test':{'talents':{
            'Known':{'name':'天赋','effect_summary':'普通攻击提高伤害','face_id':'Button',
                     'level':4,'tier':2,'status':'available'},
            'Dynamic':{'name':'天赋','effect_summary':'<d ref="X"/>','face_id':'Button',
                       'level':4,'tier':2,'status':'available'}}}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'catalog.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            catalog = self.catalog.TalentCatalog(path)
            self.assertIsNotNone(catalog.resolve('Test', 'Known', level=4))
            self.assertIsNone(catalog.resolve('Test', 'Dynamic'))
            data['build'] = 42
            path.write_text(json.dumps(data), encoding='utf-8')
            self.assertIsNone(self.catalog.TalentCatalog(path).resolve('Test', 'Known'))

    def test_packaged_data_covers_multiple_heroes_without_raw_markup(self):
        catalog = self.catalog.TalentCatalog()
        self.assertGreaterEqual(catalog.hero_count, 90)
        self.assertGreaterEqual(catalog.talent_count, 1900)
        for hero, talent, record in catalog.entries():
            with self.subTest(hero=hero, talent=talent):
                self.assertTrue(1 <= record.level <= 100)
                self.assertTrue(0 < len(record.effect_summary) <= 200)
                self.assertNotIn('<', record.effect_summary)


if __name__ == '__main__':unittest.main()
