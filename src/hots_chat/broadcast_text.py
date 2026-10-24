"""按已验证目录 ID 取得离线英文播报资料，不翻译玩家昵称或访问云服务。"""
from dataclasses import replace
from functools import lru_cache
import unicodedata

from .quest_catalog import QuestCatalog
from .talent_catalog import TalentCatalog


@lru_cache(maxsize=1)
def _english_catalogs():
    talents, quests = TalentCatalog(locale='enUS'), QuestCatalog(locale='enUS')
    # 缓存只发布已加载目录，避免并发首次使用看见半加载的索引。
    talents.entries()
    quests.entries()
    return talents, quests


def _english(value):
    return (isinstance(value, str) and bool(value.strip())
            and not any(character in value for character in '<>\n\r\x00')
            and not any(unicodedata.category(character).startswith('C') for character in value)
            and all(not character.isalpha() or 'LATIN' in unicodedata.name(character, '')
                    for character in value))


def _english_player(hit):
    """仅输出可识别英文昵称；其余以可信槽位代称，不猜译昵称或电脑身份。"""
    from .talent_broadcast import _clean, _known
    player = _clean(hit.player_name)
    if not _known(player):return '', 'english_name_omitted'
    if _english(player):return player, 'english_name_preserved'
    if type(hit.player_slot) is int and 0 <= hit.player_slot < 16:
        return f'Player {hit.player_slot + 1}', 'english_name_fallback'
    return '', 'english_name_omitted'


def player_name_policy(hit):
    """名称决策原因码；不返回原昵称，也不读取游戏或调用云服务。"""
    return _english_player(hit)[1]


def _localize(hit, language):
    if language == 'zh':return hit, ''
    if language != 'en':return None, 'output_language_unsupported'
    from .talent_broadcast import (CooldownHitSnapshot, HitSnapshot,
                                   QuestHitSnapshot, StatHitSnapshot)
    if not isinstance(hit, (HitSnapshot, QuestHitSnapshot, CooldownHitSnapshot, StatHitSnapshot)):
        return None, 'english_item_unavailable'
    talents, quests = _english_catalogs()
    if not talents.hero_count or isinstance(hit, QuestHitSnapshot) and not quests.quest_count:
        return None, 'english_catalog_unavailable'
    hero_id = hit.hero_id
    hero_name = talents.hero_name(hero_id)
    if not _english(hero_name):return None, 'english_hero_unavailable'
    hit = replace(hit, player_name=_english_player(hit)[0])
    if isinstance(hit, HitSnapshot):
        record = talents.resolve(hero_id, hit.talent_id, level=hit.tier_level)
        if record is None or not _english(record.talent_name) or not _english(record.effect_summary):
            return None, 'english_item_unavailable'
        return replace(hit, hero_name=hero_name, talent_name=record.talent_name,
                       effect_summary=record.effect_summary, advice='', advice_verified=False), ''
    if isinstance(hit, QuestHitSnapshot):
        record = quests.resolve(hero_id, hit.quest_id)
        if record is None or not _english(record.quest_name):return None, 'english_item_unavailable'
        return replace(hit, hero_name=hero_name, quest_name=record.quest_name), ''
    if isinstance(hit, CooldownHitSnapshot):
        name = talents.ability_name(hero_id, hit.ability_id)
        if not _english(name):return None, 'english_item_unavailable'
        return replace(hit, hero_name=hero_name, ability_name=name), ''
    return replace(hit, hero_name=hero_name), ''


def localize_hit(hit, language):
    """中文保留原快照；英文缺少精确英雄／项目资料时返回 None。"""
    return _localize(hit, language)[0]


def localization_reason(hit, language='en'):
    """仅检查同一离线缓存，区分资料不可用、英雄缺失与项目缺失。"""
    return _localize(hit, language)[1]
