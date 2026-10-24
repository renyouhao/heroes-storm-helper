"""随包离线天赋短描述；只按精确目录ID解析，缺失时不编造。"""
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import unicodedata


CATALOG_FILE = 'talents_98348_zhCN.json'
CATALOG_LOCALES = frozenset(('zhCN', 'enUS'))


@dataclass(frozen=True)
class LocalizedTalent:
    talent_name: str
    effect_summary: str
    face_id: str
    level: int
    tier: int


def _text(value, limit):
    return (isinstance(value, str) and 0 < len(value.strip()) <= limit
            and not any(character in value for character in '<>\n\r\x00')
            and not any(unicodedata.category(c).startswith('C') for c in value))


class TalentCatalog:
    def __init__(self, path=None, *, locale='zhCN'):
        if locale not in CATALOG_LOCALES:
            raise ValueError('不支持的天赋资料语言。')
        root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
        self.locale = locale
        self.path = Path(path) if path is not None else root/'data'/f'talents_98348_{locale}.json'
        self._records = None
        self._hero_names = {}
        self._abilities = {}

    def _load(self):
        if self._records is not None:return
        self._records = {}
        try:
            if self.path.stat().st_size > 2 * 1024 * 1024:return
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if (not isinstance(data, dict) or type(data.get('schema')) is not int
                    or data['schema'] != 1 or type(data.get('build')) is not int
                    or data['build'] != 98348 or data.get('locale') != self.locale):
                return
            heroes = data.get('heroes')
            if not isinstance(heroes, dict) or len(heroes) > 256:return
            records = {}
            for hero_id, hero in heroes.items():
                if not _text(hero_id, 160) or not isinstance(hero, dict):continue
                if _text(hero.get('name'), 80):self._hero_names[hero_id] = hero['name']
                abilities = hero.get('abilities', {})
                if isinstance(abilities, dict) and len(abilities) <= 256:
                    for ability_id, ability in abilities.items():
                        if (_text(ability_id, 160) and isinstance(ability, dict)
                                and ability.get('status') == 'available'
                                and _text(ability.get('name'), 80)):
                            self._abilities[hero_id, ability_id] = ability['name']
                talents = hero.get('talents')
                if not isinstance(talents, dict) or len(talents) > 256:continue
                for talent_id, record in talents.items():
                    if (not _text(talent_id, 160) or not isinstance(record, dict)
                            or record.get('status') != 'available'
                            or not _text(record.get('name'), 80)
                            or not _text(record.get('effect_summary'), 200)
                            or not _text(record.get('face_id'), 160)
                            or type(record.get('level')) is not int or not 1 <= record['level'] <= 100
                            or type(record.get('tier')) is not int or not 0 <= record['tier'] < 32):
                        continue
                    records[hero_id, talent_id] = LocalizedTalent(
                        record['name'], record['effect_summary'], record['face_id'],
                        record['level'], record['tier'])
            self._records = records
        except (OSError, ValueError, TypeError, UnicodeError):
            pass

    def resolve(self, hero_id, talent_id, *, build=98348, level=None):
        self._load()
        if type(build) is not int or build != 98348:return None
        if not isinstance(hero_id, str) or not isinstance(talent_id, str):return None
        record = self._records.get((hero_id, talent_id))
        if record is None or (level is not None and (type(level) is not int or record.level != level)):
            return None
        return record

    def hero_name(self, hero_id, *, build=98348):
        self._load()
        if type(build) is not int or build != 98348 or not isinstance(hero_id, str):return None
        return self._hero_names.get(hero_id)

    def ability_name(self, hero_id, ability_id, *, build=98348):
        self._load()
        if (type(build) is not int or build != 98348
                or not isinstance(hero_id, str) or not isinstance(ability_id, str)):
            return None
        name = self._abilities.get((hero_id, ability_id))
        if name is not None:return name
        talent = self._records.get((hero_id, ability_id))
        return talent.talent_name if talent is not None else None

    @property
    def hero_count(self):
        self._load()
        return len({hero for hero, talent in self._records})

    @property
    def talent_count(self):
        self._load()
        return len(self._records)

    def entries(self):
        self._load()
        return tuple((hero, talent, record) for (hero, talent), record in self._records.items())
