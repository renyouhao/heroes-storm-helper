"""自动天赋纯会话；不创建线程、不读游戏、不加载原生输入边界。"""
from dataclasses import dataclass
import math

from .loot_native import GameTarget


@dataclass(frozen=True)
class TalentConfig:
    enabled: bool = False
    confirm_timeout: float = 3.0

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError('自动天赋开关必须为布尔值。')
        if (isinstance(self.confirm_timeout, bool)
                or not isinstance(self.confirm_timeout, (int, float))
                or not math.isfinite(self.confirm_timeout)
                or self.confirm_timeout <= 0):
            raise ValueError('天赋确认超时必须为正数。')


@dataclass(frozen=True)
class TalentStatus:
    state: str
    detail: str = ''
    generation: int = 0
    tier_id: int | None = None


@dataclass
class _Attempt:
    tier_id: int
    sent_at: float
    outcome: str = 'waiting'


class TalentSession:
    """由调用方串行poll；同一可信局标识的每层最多发一次。

    configure/stop不会解除去重。unknown、短读、失焦也不代表新局。
    原生favorite动作没有目标ID，因此回读只能确认该层已选择。
    """

    def __init__(self, backend, *, config=None):
        self.backend = backend
        self.config = config if config is not None else TalentConfig()
        if not isinstance(self.config, TalentConfig):
            raise ValueError('自动天赋配置无效。')
        self.status = TalentStatus('disabled' if not self.config.enabled else 'idle')
        self._generation = None
        self._retired_generations = []
        self._attempts = {}
        self._revision = 0

    def configure(self, config):
        if not isinstance(config, TalentConfig):
            raise ValueError('自动天赋配置无效。')
        self._revision += 1
        self.config = config
        return self._status('idle' if config.enabled else 'disabled')

    def stop(self):
        return self.configure(TalentConfig(False, self.config.confirm_timeout))

    def _status(self, state, detail='', tier_id=None):
        self.status = TalentStatus(state, detail, tier_id=tier_id)
        return self.status

    def _observe_attempt(self, tiers, now):
        for attempt in self._attempts.values():
            if attempt.outcome == 'confirmed':
                continue
            tier = tiers.get(attempt.tier_id)
            if tier is None:
                return self._status('unknown', '天赋层读取不完整，保留本层尝试记录。',
                                    attempt.tier_id)
            if tier.selected_value >= 0:
                attempt.outcome = 'confirmed'
                return self._status('confirmed', '已回读确认：该层已选择。', attempt.tier_id)
            if attempt.outcome == 'error':
                return self._status('error', '组合键未完整发送，本局该层不再重试。',
                                    attempt.tier_id)
            if now - attempt.sent_at >= self.config.confirm_timeout:
                return self._status('blocked', '3秒内未确认选择，请检查该层爱心标记和快捷键；本层不再重试。',
                                    attempt.tier_id)
            return self._status('confirming', '组合键已发送，等待回读该层已选记录。', attempt.tier_id)
        return None

    def poll(self, snapshot, now, target, *, allowed=lambda: True, validate=None):
        """消费可信reader快照；now为monotonic秒，allowed为发送前栅栏。"""
        if not self.config.enabled:
            return self._status('disabled')
        if (snapshot.status not in ('available', 'idle')
                or not snapshot.generation):
            return self._status('unknown', snapshot.reason or '当前天赋状态未知，等待可信快照。')
        if isinstance(target, GameTarget) and target.pid != snapshot.pid:
            return self._status('deferred', '快照不属于当前前台游戏进程。')
        tiers = {tier.tier_id: tier for tier in snapshot.tiers}
        if (not tiers or len(tiers) != len(snapshot.tiers)
                or any(type(tier.selected_value) is not int or tier.selected_value < -1
                       for tier in tiers.values())):
            return self._status('unknown', '天赋层读取不完整，保留已有尝试记录。')
        pending = snapshot.pending_tier
        if pending is not None and pending not in tiers:
            return self._status('unknown', '待选层未包含在可信快照中。')
        if snapshot.generation != self._generation:
            if snapshot.generation in self._retired_generations:
                return self._status('unknown', '已忽略上一局的过期快照。')
            if self._generation is not None:
                self._retired_generations.append(self._generation)
            self._generation = snapshot.generation
            self._attempts.clear()
            self._revision += 1
        observed = self._observe_attempt(tiers, now)
        if observed is not None:
            return observed
        if pending is None or tiers[pending].selected_value >= 0:
            return self._status('idle', '当前没有待选天赋。')
        if not tiers[pending].enabled:
            return self._status('unknown', '待选层尚未启用。')
        if pending in self._attempts:
            return self._status('blocked', '本局该层已经尝试，不重复发送。', pending)
        if not isinstance(target, GameTarget) or target.pid != snapshot.pid:
            return self._status('deferred', '等待目标游戏回到前台。', pending)

        revision, source_generation = self._revision, self._generation

        def can_send():
            if (not self.config.enabled or revision != self._revision
                    or source_generation != self._generation):
                return False
            permitted = allowed()
            return bool(permitted and self.config.enabled and revision == self._revision
                        and source_generation == self._generation)

        if not can_send():
            return self._status('disabled' if not self.config.enabled else 'deferred',
                                '等待其他输入操作结束。', pending)
        attempt = _Attempt(pending, now)
        self._attempts[pending] = attempt
        def validate_snapshot():
            return bool(can_send() and validate() and can_send())
        try:
            options = dict(allowed=can_send)
            if validate is not None:options['validate'] = validate_snapshot
            outcome = self.backend.select_favorite(target, **options)
        except OSError:
            outcome = 'failed'
        if outcome == 'deferred':
            self._attempts.pop(pending, None)
            return self._status('disabled' if not self.config.enabled else 'deferred',
                                '游戏窗口或输入状态已变化，稍后再选。', pending)
        if outcome != 'sent':
            attempt.outcome = 'error'
            return self._status('error', '组合键未完整发送，本局该层不再重试。', pending)
        if not self.config.enabled:
            return self._status('disabled')
        return self._status('confirming', '组合键已发送，等待回读该层已选记录。', pending)
