"""天赋和统计双击播报的纯业务边界；不读取游戏、不安装钩子、不发送输入。

HitSnapshot 必须由独立映射器验证实际天赋格、队伍、已选天赋与本局身份。
StatHitSnapshot 必须验证统计格、指标与数值；两者共享同一会话的限频。
本模块不把坐标推算或未知文字升级为可信命中。所有调用应在同一调度线程串行执行。
"""
from dataclasses import dataclass, replace
import math
import re
import unicodedata


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
    talent = _bounded(talent, 28)
    prefix = f'{speaker}｜{hit.tier_level}级天赋「{talent}」：'
    # 摘要来自已核实数据，只取第一句并限制长度，避免原始长正文刷屏。
    sentence_end = re.search(r'[。！？!?；;]', effect)
    effect = effect[:sentence_end.end()] if sentence_end else effect
    effect = _bounded(effect.strip(), min(70, max_chars - len(prefix)))
    message = prefix + effect
    if include_advice and hit.advice_verified is True:
        advice = _clean(hit.advice)
        budget = max_chars - len(message) - len('；建议：')
        if _known(advice) and budget >= 8:
            message = message.rstrip('。') + '；建议：' + _bounded(advice, min(24, budget))
    return message


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
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def _identity(hit):
    if isinstance(hit, StatHitSnapshot):
        # 数值会持续变化；锁定格子和完整显示身份，数值只用于播报快照。
        return (hit.pid, hit.hwnd, hit.match_token, hit.player_slot, hit.team_relation,
                'stat', hit.metric_id, hit.rect, hit.player_name, hit.hero_name)
    return (hit.pid, hit.hwnd, hit.match_token, hit.player_slot, hit.team_relation,
            'talent', hit.tier_level, hit.talent_id, hit.rect)


def _dedupe_key(hit):
    target = ('stat', hit.metric_id) if isinstance(hit, StatHitSnapshot) else ('talent', hit.talent_id)
    return hit.pid, hit.hwnd, hit.match_token, hit.player_slot, *target


class TalentBroadcastSession:
    """观察鼠标事件并交出唯一动作；发送边界由调用方显式注入。

    double_click_ms/size 必须由调用方读取 Windows 双击设置后传入；size 为以首次
    点击为中心的完整矩形宽高。mouse 永远返回 False，原鼠标事件始终透传。
    tick 只交出一次动作；dispatch 一旦调用 sender，即使部分发送失败也不自动重试。
    统计值在 tick 交出动作前刷新；动作交出后冻结原文，后续同格数值变化不重建动作。
    """
    def __init__(self, *, double_click_ms, double_click_size, config=None):
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

    def configure(self, config):
        if not isinstance(config, TalentBroadcastConfig):
            raise ValueError('天赋播报配置无效。')
        self.config = config
        self._left_held = False
        self.cancel()
        return self._status('idle' if config.enabled else 'disabled')

    def stop(self):
        return self.configure(TalentBroadcastConfig(
            enabled=False, include_advice=self.config.include_advice,
            max_chars=self.config.max_chars))

    def cancel(self, detail='本次播报已取消。'):
        self._revision += 1
        self._first_click = self._pending = None
        return self._status('cancelled' if self.config.enabled else 'disabled', detail)

    def _valid(self, hit, context, now):
        if (not self.config.enabled or not _finite(now)
                or not isinstance(hit, (HitSnapshot, StatHitSnapshot))
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
        return format_talent_message(hit, include_advice=self.config.include_advice,
                                     max_chars=self.config.max_chars)

    def _limited(self, hit, now):
        # 仅保留还在3秒窗口内的记录；停用/新局不会解除全局限频。
        self._attempts = {key: sent for key, sent in self._attempts.items() if now - sent < 3.}
        return ((self._last_attempt is not None and now - self._last_attempt < 2.)
                or _dedupe_key(hit) in self._attempts)

    def mouse(self, x, y, down, hit, context, now, *, button='left', injected=False, event_at=None):
        """event_at 为原始钩子单调时间；now 负责新鲜度与动作过期判断。"""
        if injected:
            return False
        event_time = now if event_at is None else event_at
        if (not _finite(now) or not _finite(event_time)
                or (event_at is not None and (event_time < 0 or not 0 <= now - event_time <= .25))):
            self.cancel('鼠标事件时间未知或已过期，请重新双击。')
            if not down:self._left_held = False
            return False
        if button != 'left':
            self.cancel('其他鼠标操作已取消本次播报。')
            return False
        if not down:
            self._left_held = False
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
        is_stat = isinstance(hit, StatHitSnapshot)
        action = DispatchAction(
            self._token, hit.pid, hit.hwnd, hit.match_token, hit.player_slot,
            '' if is_stat else hit.talent_id, self._message(hit),
            kind='stat' if is_stat else 'talent', metric_id=hit.metric_id if is_stat else '')
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
        if isinstance(hit, StatHitSnapshot):
            if not pending.offered and message != pending.action.message:
                pending.action = replace(pending.action, message=message)
        elif message != pending.action.message:
            self.cancel('天赋播报内容已变化，本次播报取消。')
            return None
        return pending

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
