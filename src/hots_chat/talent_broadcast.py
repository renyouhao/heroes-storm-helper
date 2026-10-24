"""Tab 项目双击播报的纯业务边界；不读取游戏、不安装钩子、不发送输入。

HitSnapshot 必须由独立映射器验证实际天赋格、队伍、已选天赋与本局身份。
任务与冷却快照必须验证真实 UI 绑定和显示状态；所有项目共享同一会话的限频。
本模块不把坐标推算或未知文字升级为可信命中。所有调用应在同一调度线程串行执行。
"""
from dataclasses import dataclass, replace
import math
import re
import unicodedata
from .helper_replay import recorded_operation


_UNKNOWN = frozenset(('', '?', 'unknown', '未知', '待识别', '未识别'))
_MAX_HIT_AGE = 1.0
_STAT_METRICS = {
    'solo_kills': ('击杀', '次'), 'assists': ('助攻', '次'), 'deaths': ('死亡', '次'),
    'hero_damage': ('英雄伤害', ''), 'siege_damage': ('攻城伤害', ''),
    'healing': ('治疗', ''), 'self_healing': ('自我治疗', ''), 'experience': ('经验贡献', ''),
}


@dataclass(frozen=True)
class HitSnapshot:
    pid: int
    hwnd: int
    match_token: str
    player_slot: int
    team_relation: str
    tier_level: int
    talent_id: str
    talent_name: str
    effect_summary: str
    rect: tuple[int, int, int, int]
    observed_at: float
    valid_until: float
    panel_visible: bool = False
    selected: bool = False
    mapping_trusted: bool = False
    player_name: str = ''
    hero_name: str = ''
    advice: str = ''
    advice_verified: bool = False
    quest_progress: int | None = None
    quest_state: str = ''


@dataclass(frozen=True)
class StatHitSnapshot:
    pid: int
    hwnd: int
    match_token: str
    player_slot: int
    team_relation: str
    metric_id: str
    value: int | None
    rect: tuple[int, int, int, int]
    observed_at: float
    valid_until: float
    panel_visible: bool = False
    mapping_trusted: bool = False
    player_name: str = ''
    hero_name: str = ''


@dataclass(frozen=True, kw_only=True)
class QuestHitSnapshot:
    pid: int
    hwnd: int
    match_token: str
    player_slot: int
    team_relation: str
    quest_id: str
    quest_name: str
    rect: tuple[int, int, int, int]
    observed_at: float
    valid_until: float
    progress: int | None = None
    state: str = ''
    panel_visible: bool = False
    mapping_trusted: bool = False
    player_name: str = ''
    hero_name: str = ''


@dataclass(frozen=True, kw_only=True)
class CooldownHitSnapshot:
    pid: int
    hwnd: int
    match_token: str
    player_slot: int
    team_relation: str
    ability_id: str
    ability_name: str
    rect: tuple[int, int, int, int]
    observed_at: float
    valid_until: float
    remaining_seconds: float | None = None
    state: str = ''
    panel_visible: bool = False
    mapping_trusted: bool = False
    player_name: str = ''
    hero_name: str = ''


@dataclass(frozen=True)
class InputContext:
    pid: int = 0
    hwnd: int = 0
    match_token: str = ''
    foreground: bool = False
    chat_active: bool = False
    menu_active: bool = False
    loot_busy: bool = False
    repeat_busy: bool = False
    quick_input_busy: bool = False

    @property
    def busy(self):
        return self.loot_busy or self.repeat_busy or self.quick_input_busy


@dataclass(frozen=True)
class TalentBroadcastConfig:
    enabled: bool = False
    include_advice: bool = False
    max_chars: int = 150

    def __post_init__(self):
        if type(self.enabled) is not bool or type(self.include_advice) is not bool:
            raise ValueError('天赋播报开关必须为布尔值。')
        if type(self.max_chars) is not int or not 100 <= self.max_chars <= 240:
            raise ValueError('天赋播报长度必须为100–240个字符。')


@dataclass(frozen=True)
class TalentBroadcastStatus:
    state: str
    detail: str = ''


@dataclass(frozen=True)
class BroadcastSendResult:
    state: str
    code: str = ''
    detail: str = ''


@dataclass(frozen=True)
class DispatchAction:
    token: int
    pid: int
    hwnd: int
    match_token: str
    player_slot: int
    talent_id: str
    message: str
    channel: str = 'team'
    kind: str = 'talent'
    metric_id: str = ''


@dataclass
class _Pending:
    action: DispatchAction
    identity: tuple
    deadline: float
    offered: bool = False


def _bounded(text, limit):
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _clean(value):
    if not isinstance(value, str):
        return ''
    # 先限制处理量，避免把整段数据正文当作聊天输入；格式控制符也不可跨边界保留。
    value = value[:4096]
    value = re.sub(r'<[^>]*>', '', value)
    value = re.sub(r'\|c[0-9a-fA-F]{8}|\|r', '', value)
    value = re.sub(r'\|[TtHh][^|]*\|[tThH]', '', value)
    value = ''.join(' ' if character.isspace() else character
                    for character in value
                    if not unicodedata.category(character).startswith('C')
                    or character.isspace())
    value = re.sub(r'(?<!\w)[/／][A-Za-z][\w-]*', '', value)
    value = value.replace('/', '／').replace('\\', '＼')
    value = value.translate(str.maketrans({'<': '', '>': '', '|': '',
                                          '「': '“', '」': '”', '｜': ' '}))
    return ' '.join(value.split()).strip()


def _known(value):
    return value.casefold() not in _UNKNOWN


def _speaker(hit):
    if hit.team_relation not in ('ally', 'enemy'):
        return None
    player, hero = map(_clean, (hit.player_name, hit.hero_name))
    player = _bounded(player, 24) if _known(player) else ''
    hero = _bounded(hero, 18) if _known(hero) else ''
    if not (player or hero):
        return None
    identity = f'{player}（{hero}）' if player and hero else (player or f'英雄：{hero}')
    relation = '我方' if hit.team_relation == 'ally' else '敌方'
    return f'【{relation}】{identity}'


def _quest_detail(progress, state, *, allow_static=False):
    if state == '' and allow_static and progress is None:
        return ''
    valid_progress = type(progress) is int and 0 <= progress < 2**32
    if state == 'known' and valid_progress:
        return f'任务层数：{progress:,}'
    if state == 'complete' and (progress is None or valid_progress):
        return '任务已完成' + (f'（{progress:,}层）' if progress is not None else '')
    return None


def format_talent_message(hit, *, include_advice=False, max_chars=150):
    """只格式化可信已选天赋的已知摘要；不联网、不现场生成效果或战术建议。"""
    if type(max_chars) is not int or not 100 <= max_chars <= 240:
        raise ValueError('天赋播报长度必须为100–240个字符。')
    if (not isinstance(hit, HitSnapshot) or hit.selected is not True
            or hit.mapping_trusted is not True or hit.team_relation not in ('ally', 'enemy')
            or type(hit.tier_level) is not int or not 1 <= hit.tier_level <= 100
            or not isinstance(hit.talent_id, str) or not _known(hit.talent_id.strip())):
        return None
    speaker = _speaker(hit)
    talent, effect = map(_clean, (hit.talent_name, hit.effect_summary))
    if speaker is None or not _known(talent) or not _known(effect):
        return None
    quest = _quest_detail(hit.quest_progress, hit.quest_state, allow_static=True)
    if quest is None:
        return None
    suffix = '；' + quest if quest else ''
    fixed = f'注意{speaker}｜{hit.tier_level}级天赋「」：'
    talent = _bounded(talent, min(28, max_chars - len(fixed) - len(suffix) - 1))
    prefix = f'注意{speaker}｜{hit.tier_level}级天赋「{talent}」：'
    # 摘要来自已核实数据，只取第一句并限制长度，避免原始长正文刷屏。
    sentence_end = re.search(r'[。！？!?；;]', effect)
    effect = effect[:sentence_end.end()] if sentence_end else effect
    effect = _bounded(effect.strip(), min(70, max_chars - len(prefix) - len(suffix)))
    message = prefix + effect + suffix
    if include_advice and hit.advice_verified is True:
        advice = _clean(hit.advice)
        budget = max_chars - len(message) - len('；建议：')
        if _known(advice) and budget >= 8:
            message = message.rstrip('。') + '；建议：' + _bounded(advice, min(24, budget))
    return message


def _format_item_message(hit, noun, name, detail, max_chars):
    speaker, name = _speaker(hit), _clean(name)
    if speaker is None or not _known(name):
        return None
    fixed = f'注意{speaker}｜{noun}「」｜{detail}'
    name = _bounded(name, min(28, max_chars - len(fixed)))
    return f'注意{speaker}｜{noun}「{name}」｜{detail}'


def format_quest_message(hit, *, max_chars=150):
    """基础任务独立于天赋目录；只使用映射器确认的当前值或完成状态。"""
    if type(max_chars) is not int or not 100 <= max_chars <= 240:
        raise ValueError('播报长度必须为100–240个字符。')
    if (not isinstance(hit, QuestHitSnapshot) or hit.mapping_trusted is not True
            or not isinstance(hit.quest_id, str) or not _known(hit.quest_id.strip())):
        return None
    detail = _quest_detail(hit.progress, hit.state)
    if detail is None:
        return None
    return _format_item_message(hit, '基础任务', hit.quest_name, detail, max_chars)


def format_cooldown_message(hit, *, max_chars=150):
    """忠实播报我方大招冷却显示；不据计时推断可用充能或是否可释放。"""
    if type(max_chars) is not int or not 100 <= max_chars <= 240:
        raise ValueError('播报长度必须为100–240个字符。')
    if (not isinstance(hit, CooldownHitSnapshot) or hit.mapping_trusted is not True
            or hit.team_relation != 'ally' or not isinstance(hit.ability_id, str)
            or not _known(hit.ability_id.strip()) or not _finite(hit.remaining_seconds)):
        return None
    if hit.state == 'ready' and hit.remaining_seconds == 0:
        detail = '已就绪'
    elif hit.state == 'cooldown' and hit.remaining_seconds > 0:
        detail = f'冷却显示：{hit.remaining_seconds:.12g}秒'
    else:
        return None
    return _format_item_message(hit, '大招', hit.ability_name, detail, max_chars)


def format_stat_message(hit, *, max_chars=150):
    """只播报命中的一个白名单统计值；未知不转0，数值限定为uint32。"""
    if type(max_chars) is not int or not 100 <= max_chars <= 240:
        raise ValueError('播报长度必须为100–240个字符。')
    if (not isinstance(hit, StatHitSnapshot) or hit.mapping_trusted is not True
            or not isinstance(hit.metric_id, str) or hit.metric_id not in _STAT_METRICS
            or type(hit.value) is not int or not 0 <= hit.value < 2**32):
        return None
    speaker = _speaker(hit)
    if speaker is None:
        return None
    label, unit = _STAT_METRICS[hit.metric_id]
    return f'{speaker}｜{label}：{hit.value:,}{unit}'


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _identity(hit):
    if isinstance(hit, StatHitSnapshot):
        # 数值会持续变化；锁定格子和完整显示身份，数值只用于播报快照。
        return (hit.pid, hit.hwnd, hit.match_token, hit.player_slot, hit.team_relation,
                'stat', hit.metric_id, hit.rect, hit.player_name, hit.hero_name)
    if isinstance(hit, (QuestHitSnapshot, CooldownHitSnapshot)):
        kind, item = (('quest', hit.quest_id) if isinstance(hit, QuestHitSnapshot)
                      else ('cooldown', hit.ability_id))
        name = hit.quest_name if isinstance(hit, QuestHitSnapshot) else hit.ability_name
        return (hit.pid, hit.hwnd, hit.match_token, hit.player_slot, hit.team_relation,
                kind, item, hit.rect, hit.player_name, hit.hero_name, name)
    return (hit.pid, hit.hwnd, hit.match_token, hit.player_slot, hit.team_relation,
            'talent', hit.tier_level, hit.talent_id, hit.rect, hit.player_name, hit.hero_name,
            hit.talent_name, hit.effect_summary, hit.advice, hit.advice_verified,
            hit.quest_state != '')


def _target(hit):
    if isinstance(hit, StatHitSnapshot):
        return 'stat', hit.metric_id
    if isinstance(hit, QuestHitSnapshot):
        return 'quest', hit.quest_id
    if isinstance(hit, CooldownHitSnapshot):
        return 'cooldown', hit.ability_id
    return 'talent', hit.talent_id


def _dynamic(hit):
    return (isinstance(hit, (StatHitSnapshot, QuestHitSnapshot, CooldownHitSnapshot))
            or isinstance(hit, HitSnapshot) and hit.quest_state in ('known', 'complete'))


def _dedupe_key(hit):
    return hit.pid, hit.hwnd, hit.match_token, hit.player_slot, *_target(hit)


class TalentBroadcastSession:
    """观察鼠标事件并交出唯一动作；发送边界由调用方显式注入。

    double_click_ms/size 必须由调用方读取 Windows 双击设置后传入；size 为以首次
    点击为中心的完整矩形宽高。mouse 永远返回 False，原鼠标事件始终透传。
    tick 只交出一次动作；dispatch 一旦调用 sender，即使部分发送失败也不自动重试。
    动态值在 tick 交出动作前刷新；动作交出后冻结原文，后续同格数值变化不重建动作。
    """
    def __init__(self, *, double_click_ms, double_click_size, config=None, diagnostics=None):
        self.diagnostics = diagnostics
        if type(double_click_ms) is not int or double_click_ms <= 0:
            raise ValueError('必须提供Windows双击间隔。')
        if (not isinstance(double_click_size, tuple) or len(double_click_size) != 2
                or any(type(size) is not int or size <= 0 for size in double_click_size)):
            raise ValueError('必须提供Windows双击矩形宽高。')
        self.config = TalentBroadcastConfig() if config is None else config
        if not isinstance(self.config, TalentBroadcastConfig):
            raise ValueError('天赋播报配置无效。')
        self.double_click_seconds = double_click_ms / 1000.
        self.double_click_size = double_click_size
        self.status = TalentBroadcastStatus('idle' if self.config.enabled else 'disabled')
        self._first_click = self._pending = None
        self._left_held = False
        self._revision = self._token = 0
        self._attempts = {}
        self._last_attempt = None

    def _status(self, state, detail=''):
        self.status = TalentBroadcastStatus(state, detail)
        return self.status

    @recorded_operation('board')
    def configure(self, config):
        if not isinstance(config, TalentBroadcastConfig):
            raise ValueError('天赋播报配置无效。')
        self.config = config
        self._left_held = False
        self.cancel()
        return self._status('idle' if config.enabled else 'disabled')

    @recorded_operation('board')
    def stop(self):
        return self.configure(TalentBroadcastConfig(
            enabled=False, include_advice=self.config.include_advice,
            max_chars=self.config.max_chars))

    @recorded_operation('board')
    def cancel(self, detail='本次播报已取消。'):
        self._revision += 1
        self._first_click = self._pending = None
        return self._status('cancelled' if self.config.enabled else 'disabled', detail)

    def _valid(self, hit, context, now):
        if (not self.config.enabled or not _finite(now)
                or not isinstance(hit, (HitSnapshot, StatHitSnapshot, QuestHitSnapshot, CooldownHitSnapshot))
                or not isinstance(context, InputContext)
                or hit.panel_visible is not True or context.foreground is not True
                or type(context.pid) is not int or type(context.hwnd) is not int
                or any(type(flag) is not bool for flag in (
                    context.chat_active, context.menu_active, context.loot_busy,
                    context.repeat_busy, context.quick_input_busy))
                or context.chat_active or context.menu_active
                or type(hit.pid) is not int or hit.pid <= 0
                or type(hit.hwnd) is not int or hit.hwnd <= 0
                or not isinstance(hit.match_token, str) or not hit.match_token.strip()
                or type(hit.player_slot) is not int or not 0 <= hit.player_slot < 16
                or (hit.pid, hit.hwnd, hit.match_token)
                != (context.pid, context.hwnd, context.match_token)
                or not _finite(hit.observed_at) or not _finite(hit.valid_until)
                or not hit.observed_at <= now <= min(hit.valid_until, hit.observed_at + _MAX_HIT_AGE)
                or not isinstance(hit.rect, tuple) or len(hit.rect) != 4
                or any(type(value) is not int for value in hit.rect)):
            return False
        left, top, right, bottom = hit.rect
        return (left < right and top < bottom and self._message(hit) is not None)

    def _message(self, hit):
        if isinstance(hit, StatHitSnapshot):
            return format_stat_message(hit, max_chars=self.config.max_chars)
        if isinstance(hit, QuestHitSnapshot):
            return format_quest_message(hit, max_chars=self.config.max_chars)
        if isinstance(hit, CooldownHitSnapshot):
            return format_cooldown_message(hit, max_chars=self.config.max_chars)
        return format_talent_message(hit, include_advice=self.config.include_advice,
                                     max_chars=self.config.max_chars)

    def _limited(self, hit, now):
        # 仅保留还在3秒窗口内的记录；停用/新局不会解除全局限频。
        self._attempts = {key: sent for key, sent in self._attempts.items() if now - sent < 3.}
        return ((self._last_attempt is not None and now - self._last_attempt < 2.)
                or _dedupe_key(hit) in self._attempts)

    @recorded_operation('board')
    def mouse(self, x, y, down, hit, context, now, *, button='left', injected=False, event_at=None):
        """event_at 为原始钩子单调时间；now 负责新鲜度与动作过期判断。"""
        if injected:
            return False
        if button != 'left':
            self.cancel('其他鼠标操作已取消本次播报。')
            return False
        if not down:
            # 松开不创建或延长动作；即使事件在队列中迟到，也必须解除持键。
            # tick/dispatch 仍独立检查待发期限、当前命中和输入环境。
            self._left_held = False
            return False
        event_time = now if event_at is None else event_at
        if (not _finite(now) or not _finite(event_time)
                or (event_at is not None and (event_time < 0 or not 0 <= now - event_time <= .25))):
            self.cancel('鼠标事件时间未知或已过期，请重新双击。')
            return False
        if self._left_held:
            return False
        self._left_held = True
        if self._pending is not None:
            self.cancel('新的鼠标点击已取消尚未发送的播报。')
        if (not self._valid(hit, context, now) or type(x) is not int or type(y) is not int
                or not (hit.rect[0] <= x < hit.rect[2] and hit.rect[1] <= y < hit.rect[3])):
            self.cancel('当前没有可信的可播报项目。')
            return False
        first = self._first_click
        current = (_identity(hit), event_time, x, y)
        if (first is None or first[0] != current[0]
                or not 0 <= event_time - first[1] <= self.double_click_seconds
                or abs(x - first[2]) * 2 > self.double_click_size[0]
                or abs(y - first[3]) * 2 > self.double_click_size[1]):
            self._first_click = current
            self._status('armed', '等待同一项目的第二次点击。')
            return False
        self._first_click = None
        if self._limited(hit, now):
            self._status('limited', '播报限频中：任意两次需间隔2秒，同一项需间隔3秒。')
            return False
        self._token += 1
        kind, _ = _target(hit)
        action = DispatchAction(
            self._token, hit.pid, hit.hwnd, hit.match_token, hit.player_slot,
            hit.talent_id if isinstance(hit, HitSnapshot) else '', self._message(hit),
            kind=kind, metric_id=hit.metric_id if isinstance(hit, StatHitSnapshot) else '')
        self._pending = _Pending(action, _identity(hit), min(now + .8, hit.valid_until))
        self._status('pending', '已识别双击，等待安全发送。')
        return False

    def _current_pending(self, hit, context, now):
        pending = self._pending
        if pending is None:
            return None
        if (not self._valid(hit, context, now) or now > pending.deadline
                or _identity(hit) != pending.identity):
            self.cancel('面板、目标或输入环境已变化，本次播报取消。')
            return None
        message = self._message(hit)
        if _dynamic(hit):
            if not pending.offered and message != pending.action.message:
                pending.action = replace(pending.action, message=message)
        elif message != pending.action.message:
            self.cancel('天赋播报内容已变化，本次播报取消。')
            return None
        return pending

    @recorded_operation('board')
    def tick(self, hit, context, now):
        if self._first_click is not None and not self._valid(hit, context, now):
            self.cancel('目标或输入环境已变化，请重新双击。')
        pending = self._current_pending(hit, context, now)
        if pending is None:
            return None
        if self._left_held:
            self._status('deferred', '等待鼠标松开后播报。')
            return None
        if context.busy:
            self._status('deferred', '等待开箱、连按或快捷输入结束。')
            return None
        if pending.offered:
            return None
        pending.offered = True
        self._status('ready', '播报动作已交给发送边界。')
        return pending.action

    @recorded_operation('board')
    def dispatch(self, action, sender, hit, context, now, *, allowed=lambda: True):
        """sender返回'sent'或BroadcastSendResult；失败结果保留可操作的原因。

        allowed 应实时复核前台、聊天与其他输入所有者，并由 sender 在每次输入前调用。
        外部已接管动作但未调用 sender 时，可稍后手动重试 dispatch；不会重复交出动作。
        """
        pending = self._current_pending(hit, context, now)
        if pending is None or action is not pending.action or not pending.offered:
            return self._status('cancelled', '播报动作已经失效或已被处理。')
        if context.busy:
            return self._status('deferred', '等待其他输入操作结束。')
        revision = self._revision

        def can_send():
            return bool(self.config.enabled and revision == self._revision and allowed()
                        and self.config.enabled and revision == self._revision)

        if not can_send():
            return self.cancel('发送前栅栏已取消本次播报。')
        if self._limited(hit, now):
            self._pending = None
            return self._status('limited', '播报限频中：任意两次需间隔2秒，同一项需间隔3秒。')
        self._pending = self._first_click = None
        self._attempts[_dedupe_key(hit)] = self._last_attempt = now
        try:
            outcome = sender(action, allowed=can_send)
        except Exception:
            outcome = 'failed'
        result = outcome.state if isinstance(outcome, BroadcastSendResult) else outcome
        if result != 'sent':
            detail = outcome.detail if isinstance(outcome, BroadcastSendResult) else ''
            return self._status('error', detail if isinstance(detail, str) and detail else
                                '聊天发送未完整确认；已记录本次尝试，不自动重试。')
        return self._status('sent', '已发送到己方团队。')
