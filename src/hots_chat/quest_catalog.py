"""随包离线基础任务名称；精确匹配英雄与任务ID，缺失时不猜名称。"""
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
import unicodedata


CATALOG_FILE = 'quests_98348_zhCN.json'
CATALOG_LOCALES = frozenset(('zhCN', 'enUS'))
MAX_CATALOG_BYTES = 128 * 1024
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_'.-]{0,159}\Z")


@dataclass(frozen=True)
class LocalizedQuest:
    quest_name: str
    face_id: str


def _identifier(value):
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _name(value):
    return (isinstance(value, str) and 0 < len(value) <= 80
            and value == value.strip() and value.strip()
            and not any(character in value for character in '<>')
            and not any(unicodedata.category(character).startswith('C') for character in value))


class QuestCatalog:
    def __init__(self, path=None, *, locale='zhCN'):
        if locale not in CATALOG_LOCALES:
            raise ValueError('不支持的基础任务资料语言。')
        root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
        self.locale = locale
        self.path = Path(path) if path is not None else root / 'data' / f'quests_98348_{locale}.json'
        self._records = None

    def _load(self):
        if self._records is not None:
            return
        self._records = {}
        try:
            with self.path.open('rb') as stream:
                raw = stream.read(MAX_CATALOG_BYTES + 1)
            if len(raw) > MAX_CATALOG_BYTES:
                return
            data = json.loads(raw)
            if (not isinstance(data, dict) or type(data.get('schema')) is not int
                    or data['schema'] != 1 or type(data.get('build')) is not int
                    or data['build'] != 98348 or data.get('locale') != self.locale):
                return
            heroes = data.get('heroes')
            if not isinstance(heroes, dict) or len(heroes) > 128:
                return
            records = {}
            for hero_id, hero in heroes.items():
                if not _identifier(hero_id) or not isinstance(hero, dict):
                    continue
                quests = hero.get('quests')
                if not isinstance(quests, dict) or len(quests) > 8:
                    continue
                for quest_id, record in quests.items():
                    if (not _identifier(quest_id) or not isinstance(record, dict)
                            or record.get('status') != 'available'
                            or not _name(record.get('quest_name'))
                            or not _identifier(record.get('face_id'))):
                        continue
                    records[hero_id, quest_id] = LocalizedQuest(
                        record['quest_name'], record['face_id'])
            self._records = records
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            return

    def resolve(self, hero_id, quest_id, *, build=98348):
        if type(build) is not int or build != 98348:
            return None
        if not _identifier(hero_id) or not _identifier(quest_id):
            return None
        self._load()
        return self._records.get((hero_id, quest_id))

    @property
    def hero_count(self):
        self._load()
        return len({hero for hero, _quest in self._records})

    @property
    def quest_count(self):
        self._load()
        return len(self._records)

    def entries(self):
        self._load()
        return tuple((hero, quest, record) for (hero, quest), record in self._records.items())
