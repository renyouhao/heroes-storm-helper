"""Build 98348 的友方 Tab 大招冷却标签，只做有界内存读取。

HeroicAbilLink 的原生 getter 8d9190 使用天赋树 +170；能力框 +148
必须解析为同一目录链接。87f910 隐藏 CD label 时保留旧字符串，因此隐藏、
零值和无法确认的状态均为 unknown，不能据此生成 ready。
"""
from dataclasses import dataclass
import math
import re

from .talent_source import _Unknown, _link


_CONTAINER = 0x27d96d8
_SELECTION = 0x2691ae8
_UNIT_STATUS = 0x267a300
_TREE = 0x26916f0
_ABILITY = 0x269b190
_ICON = 0x26816a8
_IMAGE = 0x2757668
_LABEL = 0x270f248
_ROW = 0x2689920
_CHARGE_ABILITIES = frozenset({'TracerPulseBomb'})
_SECONDS = re.compile(r'(?:0|[1-9][0-9]{0,4})(?:\.[0-9]{1,3})?\Z')
_NUMERIC_TEXT = re.compile(r'[0-9]{1,9}(?:\.[0-9]{1,3})?\Z')


def parse_cooldown_display(ability_id, label_visible, label_text, *, selected=True,
                           binding_valid=True, reference_valid=True):
    """纯函数：只解析实际观察的 UI 数值，供现场与离线回放共用。

    label_text 只允许已筛选的有限数字字符串或 None，后者表示未读取/非数字；
    不把任意 UI 正文写入诊断。None 的显隐值表示因更早的检查未读取标签。
    """
    if (ability_id is not None and (type(ability_id) is not str
            or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.:/-]{0,159}', ability_id) is None)):
        raise ValueError('invalid cooldown ability identifier')
    if (label_visible is not None and type(label_visible) is not bool
            or any(type(value) is not bool for value in (selected, binding_valid, reference_valid))
            or label_text is not None and (type(label_text) is not str
                or _NUMERIC_TEXT.fullmatch(label_text) is None)):
        raise ValueError('invalid cooldown display observations')
    seconds, state = None, 'unknown'
    if not binding_valid:
        reason = 'cooldown_binding_untrusted'
    elif not selected:
        reason = 'heroic_ability_unselected'
    elif ability_id is None:
        reason = 'heroic_ability_unresolved'
    elif ability_id in _CHARGE_ABILITIES:
        reason = 'charge_ability_unsupported'
    elif not reference_valid:
        reason = 'ability_reference_unresolved'
    elif label_visible is False:
        reason = 'cooldown_label_hidden'
    elif label_visible is None:
        reason = 'cooldown_display_unobserved'
    else:
        reason = 'cooldown_label_not_positive_seconds'
        if label_text is not None and _SECONDS.fullmatch(label_text) is not None:
            value = float(label_text)
            if math.isfinite(value) and 0 < value <= 86400:
                seconds, state, reason = value, 'cooldown', 'cooldown_label_positive_seconds'
    return dict(state=state, remaining_seconds=seconds, reason=reason)


def _record_display(memory, slot, observations):
    """已有观察值的日志副本；不开启时不构造事件，不增加游戏读取。"""
    try:
        recorder = getattr(memory, 'diagnostics', None)
        if recorder is None:
            return
        snapshot = getattr(recorder, 'snapshot', None)
        if snapshot is not None and not snapshot().get('enabled'):
            return
        values = {name: observations[name] for name in (
            'ability_id', 'label_visible', 'label_text', 'selected', 'binding_valid', 'reference_valid')}
        result = parse_cooldown_display(**values)
        source = getattr(memory, 'diagnostic_source', '') or 'broadcast'
        visible = values['label_visible']
        recorder.record('helper.read', stage='cooldown', source=source, player_slot=slot,
            cooldown_state=result['state'], cooldown_seconds=result['remaining_seconds'],
            cooldown_reason=result['reason'], cooldown_read_stage='cooldown_display',
            cooldown_node_checks=observations['node_checks'],
            label_visible=-1 if visible is None else int(visible),
            label_numeric=int(values['label_text'] is not None))
        recorder.record('replay.frame', component='reader', operation='cooldown_display',
            source=source, payload=dict(schema=1, input=values, expected=result))
    except Exception:
        # 诊断不能改变读数或其安全判定。
        pass


@dataclass(frozen=True)
class RawCooldownRecord:
    player_slot: int
    ability_id: str
    ability_name: str
    remaining_seconds: float | None
    state: str
    rect: tuple[float, float, float, float]
    row: int
    tree: int
    node: int
    icon: int
    image: int
    label: int
    ability_handle: int
    tree_handle: int
    catalog_index: int
    reference: int
    command_index: int
    path: tuple[int, ...]
    reason: str = ''

    @property
    def identity(self):
        """两次采样须一致的绑定；秒数、标签显隐和其派生状态允许正常变化。"""
        return (self.player_slot, self.ability_id, self.rect, self.row, self.tree,
                self.node, self.icon, self.image, self.label, self.ability_handle,
                self.tree_handle, self.catalog_index, self.reference,
                self.command_index, self.path)


def read_row_cooldowns(memory, base, row, slot, relation, scales, render_size):
    """返回原生 render 坐标记录；调用者负责可信本地身份与外层会话双采样。

    敌方/未知关系在任何内存读取前返回。仅检查已验证的 Tab 天赋容器路径，
    不遍历名称、聊天、敌方技能或任意新类型节点。无可见大招时返回空元组；
    绑定歧义/指针脱离父子链时抛 _Unknown，交给外层统一停止信任。
    """
    if relation != 'ally':
        return ()
    observations = dict(ability_id=None, label_visible=None, label_text=None,
        selected=True, binding_valid=True, reference_valid=False, node_checks=0,
        candidate=False)
    try:
        return _read_row_cooldowns(memory, base, row, slot, scales, render_size, observations)
    except (ValueError, OSError, RuntimeError, TypeError):
        observations.update(binding_valid=False, candidate=True)
        raise
    finally:
        if observations['candidate']:
            _record_display(memory, slot, observations)


def _read_row_cooldowns(memory, base, row, slot, scales, render_size, observations):
    # 延迟导入，允许 board_source 将本模块作为独立读取组件。
    from .board_source import _children, _visible, _vtable, _rect, _render_rect, _label_text

    if type(slot) is not int or not 0 <= slot <= 15:
        raise _Unknown('大招行玩家编号不正确')
    _vtable(memory, base, row, _ROW)
    observations['node_checks'] = 1
    if memory.read(row + 0x158, 1)[0] != slot:
        raise _Unknown('大招行玩家绑定不一致')
    if not _visible(memory, row):
        return ()

    def matching(parent, kind):
        return tuple(child for child in _children(memory, parent)
                     if memory.u64(child) == base + kind and _visible(memory, child))

    # 实测 TalentsContainer -> SelectionUnitStatusPanel -> UnitStatusFrame。
    # 不将任何在行内偶然出现的 UnitStatusAbil 当成大招。
    candidates = []
    for container in matching(row, _CONTAINER):
        for selection in matching(container, _SELECTION):
            for unit in matching(selection, _UNIT_STATUS):
                candidates.append((row, container, selection, unit))
                if len(candidates) > 1:
                    raise _Unknown('大招状态容器存在歧义')
    if not candidates:
        observations.update(candidate=True, binding_valid=False)
        return ()
    path = candidates[0]
    observations.update(candidate=True, node_checks=4)
    unit = path[-1]
    trees, abilities = matching(unit, _TREE), matching(unit, _ABILITY)
    if len(trees) > 1 or len(abilities) > 1:
        raise _Unknown('大招节点存在歧义')
    if not trees or not abilities:
        observations['binding_valid'] = False
        return ()
    tree, ability = trees[0], abilities[0]
    observations['node_checks'] += 2
    if memory.read(tree + 0x130, 1)[0] != slot:
        raise _Unknown('大招天赋树玩家绑定不一致')
    tree_handle = memory.u64(tree + 0x170)
    ability_handle = memory.u64(ability + 0x148)
    observations['selected'] = bool(tree_handle)
    if not tree_handle or not ability_handle:
        return ()
    heroic_link = _link(memory, tree_handle)
    observations['ability_id'] = heroic_link[0]
    if _link(memory, ability_handle) != heroic_link:
        raise _Unknown('大招图标与已选大招目录链接不一致')

    icon = memory.u64(ability + 0x158)
    if icon not in _children(memory, ability):
        raise _Unknown('大招能力图标不属于当前框')
    _vtable(memory, base, icon, _ICON)
    observations['node_checks'] += 1
    if not _visible(memory, icon):
        return ()
    command = memory.read(ability + 0x150, 1)[0]
    reference = memory.u32(icon + 0x148)
    observations['reference_valid'] = reference not in (0, 0xffffffff)
    if memory.read(icon + 0x14c, 1)[0] != command:
        raise _Unknown('大招命令绑定不一致')
    image, label = memory.u64(icon + 0x130), memory.u64(icon + 0x140)
    members = _children(memory, icon)
    if image == label or image not in members or label not in members:
        raise _Unknown('大招图像或冷却标签不属于当前图标')
    _vtable(memory, base, image, _IMAGE)
    _vtable(memory, base, label, _LABEL)
    observations['node_checks'] += 2
    if not _visible(memory, image):
        return ()
    rect = _render_rect(_rect(memory, image), scales, render_size)
    if rect is None:
        return ()

    if heroic_link[0] not in _CHARGE_ABILITIES and observations['reference_valid']:
        observations['label_visible'] = _visible(memory, label)
        if observations['label_visible']:
            text = _label_text(memory, base, label, allow_empty=True)
            if _NUMERIC_TEXT.fullmatch(text) is not None:
                observations['label_text'] = text
    result = parse_cooldown_display(**{name: observations[name] for name in (
        'ability_id', 'label_visible', 'label_text', 'selected', 'binding_valid', 'reference_valid')})

    before = (tree_handle, ability_handle, icon, command, reference, image, label)
    after = (memory.u64(tree + 0x170), memory.u64(ability + 0x148),
             memory.u64(ability + 0x158), memory.read(ability + 0x150, 1)[0],
             memory.u32(icon + 0x148), memory.u64(icon + 0x130), memory.u64(icon + 0x140))
    if (before != after or memory.read(icon + 0x14c, 1)[0] != command
            or _link(memory, tree_handle) != heroic_link
            or _link(memory, ability_handle) != heroic_link):
        raise _Unknown('读取期间大招绑定正在变化')
    return (RawCooldownRecord(slot, heroic_link[0], '大招', result['remaining_seconds'], result['state'], rect,
        row, tree, ability, icon, image, label, ability_handle, tree_handle,
        heroic_link[1], reference, command, path + (ability, icon), result['reason']),)
