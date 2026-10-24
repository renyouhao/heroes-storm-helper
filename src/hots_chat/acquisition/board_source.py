"""Build 98348 的 TAB 只读映射；不调用游戏函数、窗口 API 或输入 API。

字段来自同版本原生函数及镜像英雄试用对照。坐标为引擎 render-client 像素，
必须由外部 viewport_converter 转为桌面坐标；未知输入状态始终保留 None。
"""
from collections import deque
from dataclasses import dataclass
import hashlib
import math
import re
import struct

from ..talent_broadcast import HitSnapshot, StatHitSnapshot, QuestHitSnapshot, CooldownHitSnapshot
from ..board_sender import ChatInputState
from .talent_source import TalentReader, _Memory, _Unknown, _guards, _model, _link, _catalog
from .board_values import parse_quest_progress
from .board_cooldown import read_row_cooldowns


ROOT_RVA = 0x338b2e8
ROOT_VTABLE = 0x2653738
PANEL_VTABLE = 0x2689b88
ROW_VTABLE = 0x2689920
TREE_VTABLE = 0x26916f0
ICON_VTABLE = 0x2680e88
BASELINE_QUEST_VTABLE = 0x269ab08
BOARD_FIELD_GETTERS = {
    # 完整的叶子 getter；HeroicAbilLink 取验证过字段的完整前两条指令。
    0x87d080: bytes.fromhex('8b8178010000c3'),
    0x87d090: bytes.fromhex('8b817c010000c3'),
    0x90ae30: bytes.fromhex('8b8148010000c3'),
    0x90ae40: bytes.fromhex('8b8178010000c3'),
    0x90ae50: bytes.fromhex('8b814c010000c3'),
    0x8d9190: bytes.fromhex('4883ec28488b8170010000'),
}
_BOARD_CODE_FIELDS = ('talent_quest_state', 'talent_quest_value', 'baseline_index',
                      'baseline_quest_state', 'baseline_quest_value', 'heroic_ability_link')
IMAGE_VTABLE = 0x2757668
NAME_VTABLE = 0x26928d0
HERO_NAME_VTABLE = 0x26913b8
LABEL_VTABLE = 0x270f248
SCORE_GROUP_VTABLE = 0x2693b88
SCORE_LABEL_VTABLE = 0x26777c0
_STAT_LINKS = {
    'SoloKill': 'solo_kills', 'Assists': 'assists', 'Deaths': 'deaths',
    'HeroDamage': 'hero_damage', 'SiegeDamage': 'siege_damage',
    'Healing': 'healing', 'SelfHealing': 'self_healing',
    'ExperienceContribution': 'experience',
}


@dataclass(frozen=True)
class BoardSnapshot:
    pid: int | None
    hwnd: int
    match_token: str = ''
    status: str = 'unknown'
    reason: str = ''
    hits: tuple[HitSnapshot | StatHitSnapshot | QuestHitSnapshot | CooldownHitSnapshot, ...] = ()
    chat_active: bool | None = None
    menu_active: bool | None = None
    coordinate_space: str = 'screen'


@dataclass(frozen=True)
class _MappedTalent:
    hit: HitSnapshot
    hero_id: str
    rect: tuple[float, float, float, float]
    image: int
    face_id: str | None
    catalog_bound: bool

    def aliases_cooldown(self, record, hero_id):
        # 同一屏幕位置可能是不同目标；这里只归并已绑定的同一原生大招覆盖层。
        return (self.hit.player_slot == record.player_slot
                and self.hit.team_relation == 'ally' and self.hero_id == hero_id
                and self.rect == record.rect
                and (self.image == record.image
                     or self.catalog_bound and self.face_id == record.ability_id))


def _capture_identity(capture):
    root, path, render_size, talents, inputs, stats, quests, cooldowns = capture
    # 战斗数值可以正常变化；行、指标、名称、矩形和原生格/标签仍必须逐一相同。
    stat_targets = tuple((slot, relation, names, metric, rect, cell, label, hero_id)
        for slot, relation, names, metric, _value, rect, cell, label, hero_id in stats)
    talent_targets = tuple(record[:10] + (record[10][2],) for record in talents)
    quest_targets = tuple(record[:9] + (record[9][2],) for record in quests)
    return (root, path, render_size, talent_targets, inputs, stat_targets, quest_targets,
            tuple((record.identity, names, hero_id) for record, names, hero_id in cooldowns))


class _BoardMemory(_Memory):
    """首次有界 frame 树定位允许较多小读取；热轮询只复核缓存成员链。"""
    def read(self, address, size):
        if (type(address) is not int or not 0x10000 <= address
                or address + size >= 0x7fffffff0000 or not 0 < size <= 16384
                or self.bytes + size > 1048576 or self.calls >= 24000):
            raise _Unknown('面板读取超过地址或总预算')
        data = self.game.read(address, size)
        self.calls += 1
        self.bytes += len(data)
        if len(data) != size:
            raise _Unknown('面板读取不完整')
        return data


def _visible(memory, frame):
    # CFrame::IsVisible(1), RVA1445ea0：自身 bit0 与继承 bit5 同时成立。
    return memory.read(frame + 0x48, 1)[0] & 0x21 == 0x21


def _vtable(memory, base, frame, expected):
    if memory.u64(frame) != base + expected:
        raise _Unknown('面板节点类型已变化')


def _children(memory, frame):
    """RVA1441c00 的 tag0 无调用分支；tag1 是哨兵，tag2 不调用 resolver。"""
    link = memory.u64(frame + 0x40)
    result, seen = [], set()
    while link and link & 3 != 1:
        if link & 3:
            raise _Unknown('当前 UI 成员链需要未验证的解析器')
        child = link - 0x18
        if child in seen or child == frame or len(result) >= 512:
            raise _Unknown('UI 成员链循环或超过界限')
        seen.add(child)
        result.append(child)
        link = memory.u64(child + 0x20)
    return tuple(result)


def _rect(memory, frame):
    # RVA14442b0 hit-test 与14520a0缓存 getter 的四个边界。
    if memory.read(frame + 0x28, 1)[0] & 15 != 15:
        return None
    top, left, bottom, right = struct.unpack('<4f', memory.read(frame + 0xa8, 16))
    if (not all(math.isfinite(v) for v in (left, top, right, bottom))
            or not left < right or not top < bottom):
        raise _Unknown('面板矩形数据不正确')
    return left, top, right, bottom


def _shared_text(memory, shared, *, limit=256, allow_empty=False):
    header = shared + 0x18
    raw = memory.read(header, 16)
    length, flags, pointer = struct.unpack('<IIQ', raw)
    length >>= 2
    if not (0 <= length <= limit) or (not allow_empty and not length):
        raise _Unknown('显示文本缺失或过长')
    value = memory.read(pointer if flags & 2 else header + 8, length).decode('utf8') if length else ''
    if memory.read(header, 16) != raw:
        raise _Unknown('显示文本正在变化')
    return value


def _label_text(memory, base, label, *, allow_empty=False):
    _vtable(memory, base, label, LABEL_VTABLE)
    # CLabel::SetText RVA14b19b0 比较/保存当前 label 文本的结构链。
    text_object = memory.u64(label + 0x1d8)
    shared = memory.u64(text_object + 0x28)
    return _shared_text(memory, shared, allow_empty=allow_empty)


def _display_text(memory, base, frame):
    value = _label_text(memory, base, memory.u64(frame + 0x140))
    if not value.strip():
        raise _Unknown('玩家显示文本缺失')
    return value


def _quest_progress(memory, base, icon, *, baseline=False, player_slot=None,
                    target_index=None, hero_id='', target_id=''):
    """原生 QuestProgress/QuestProgressValue 与当前可见标签交叉验证。

    Talent getters 87d080/87d090；baseline getters 90ae40/90ae50。
    标签隐藏时的残留数字不代表当前层数，有限任务完成后仅播报已完成。
    """
    offset = 0x14c if baseline else 0x17c
    state, value = memory.u32(icon + 0x178), memory.u32(icon + offset)
    labels = tuple(child for child in _children(memory, icon)
                   if memory.u64(child) == base + LABEL_VTABLE)
    label = labels[0] if len(labels) == 1 else 0
    visible = bool(label and _visible(memory, label))
    text = _label_text(memory, base, label, allow_empty=True) if visible else None
    # 日志只需数值标签；其它文本统一为 None，仍可回放 non_numeric 拒绝。
    numeric = text if isinstance(text, str) and re.fullmatch(r'[0-9,]{1,80}', text) else None
    observations = dict(raw_state=state, raw_progress=value, label_count=len(labels),
        label_visible=visible, label_text=numeric,
        stable=memory.u32(icon + 0x178) == state and memory.u32(icon + offset) == value)
    result = parse_quest_progress(**observations)
    context = dict(player_slot=player_slot, kind='quest' if baseline else 'talent',
                   index=target_index, hero_id=hero_id, target_id=target_id)
    try:
        recorder = getattr(memory, 'diagnostics', None)
        if recorder is not None and recorder.snapshot().get('enabled'):
            recorder.record('helper.read', source='broadcast', stage='quest',
                player_slot=player_slot, hit_kind=context['kind'],
                **({'quest_index': target_index} if baseline else {'tier_id': target_index}),
                quest_read_stage='value', quest_node_checks=int(bool(label)),
                quest_raw_state=state, quest_raw_progress=value, quest_state=result['state'],
                quest_progress=result['progress'], progress_reason=result['reason'],
                label_visible=int(visible), label_numeric=int(numeric is not None),
                label_matches=int(result['state'] == 'known'))
            recorder.record('replay.frame', component='reader', operation='quest_progress',
                source='broadcast', payload=dict(schema=1, context=context,
                                                input=observations, expected=result))
    except Exception:
        pass
    return result['state'], result['progress'], label


def _render_rect(rect, scales, render_size):
    if rect is None:
        return None
    render = tuple(v * scales[i % 2] for i, v in enumerate(rect))
    if not (0 <= render[0] < render[2] <= render_size[0]
            and 0 <= render[1] < render[3] <= render_size[1]):
        raise _Unknown('命中格在游戏视口之外')
    return render


class BoardReader:
    """poll 的 hits 只有在所有映射和外部坐标变换均确认时才标记可信。

    text_provider(hero_id, talent_id) -> dict/object(talent_name,effect_summary)。
    viewport_converter(hwnd, render_rect_ltrb, render_size_wh) -> screen int rect。
    input_state_provider(memory,base,root) -> (bool|None,bool|None)，可缺省。
    """
    def __init__(self, *, text_provider=None, quest_text_provider=None, viewport_converter=None,
                 input_state_provider=None, diagnostics=None, **talent_reader_options):
        self.diagnostics = diagnostics
        self.text_provider = text_provider
        self.quest_text_provider = quest_text_provider
        self.viewport_converter = viewport_converter
        self.input_state_provider = input_state_provider
        self._reader = TalentReader(**talent_reader_options)
        self._path = None
        self.last_read_bytes = self.last_read_calls = 0
        self.last_diagnostic = {}

    def close(self):
        self._path = None
        self._reader.close()

    @staticmethod
    def _token(process_token, binding):
        return hashlib.sha256(repr((process_token, binding)).encode('utf8')).hexdigest()

    def _input_fields(self, memory, base, root):
        if self.input_state_provider is None:
            return None, None, None, None
        state = self.input_state_provider(memory, base, root)
        if isinstance(state, (tuple, list)):
            if len(state) == 2:
                values = tuple(state) + (None, None)
            elif len(state) == 4:
                values = tuple(state)
            else:
                raise _Unknown('输入环境状态结构不正确')
        else:
            get = state.get if isinstance(state, dict) else lambda key: getattr(state, key, None)
            values = tuple(get(key) for key in ('chat_active', 'menu_active', 'channel', 'text'))
        if (any(v is not None and type(v) is not bool for v in values[:2])
                or any(v is not None and not isinstance(v, str) for v in values[2:])):
            raise _Unknown('输入环境状态尚未确认')
        return values

    def inspect_input(self, target_pid, hwnd):
        """发送阶段的小快照；不定位 TAB、不读取各行或聊天历史。"""
        memory = None
        self.last_read_bytes = self.last_read_calls = 0
        try:
            if type(hwnd) is not int or hwnd <= 0:
                raise _Unknown('目标窗口不正确')
            pid, _, build, base, created = self._reader._connect(target_pid)
            memory = _BoardMemory(self._reader._game, diagnostics=self.diagnostics,
                                  diagnostic_source='broadcast.input')
            process_token = (pid, created, base, build)
            if memory.read(base, 2) != b'MZ':
                raise _Unknown('游戏模块验证失败')
            binding = self._reader._resolve(memory, base, process_token)

            def capture():
                guards = _guards(memory, base)
                root = memory.u64(base + ROOT_RVA)
                _vtable(memory, base, root, ROOT_VTABLE)
                fields = self._input_fields(memory, base, root)
                if _guards(memory, base) != guards or memory.u64(base + ROOT_RVA) != root:
                    raise _Unknown('输入读取期间游戏UI会话正在变化')
                return guards, root, fields

            first, second = capture(), capture()
            if (first[:2] != second[:2] or self._reader._resolve(memory, base, process_token) != binding
                    or self._reader.creation_token_finder(self._reader._game) != created):
                raise _Unknown('输入状态或游戏会话正在变化')
            fields = tuple(before if before == after else None
                           for before, after in zip(first[2], second[2]))
            return ChatInputState(pid, hwnd, self._token(process_token, binding), *fields)
        except (OSError, ValueError, RuntimeError, TypeError, UnicodeError, struct.error):
            self.close()
            return ChatInputState(target_pid, hwnd, '', None, None, None, None)
        finally:
            if memory is not None:
                self.last_read_bytes, self.last_read_calls = memory.bytes, memory.calls

    def _locate(self, memory, base, root):
        path = self._path
        if path and path[0] == root:
            try:
                for parent, child in zip(path, path[1:]):
                    if child not in _children(memory, parent):
                        raise _Unknown('面板父子关系已变化')
                _vtable(memory, base, path[-1], PANEL_VTABLE)
                return path
            except (ValueError, OSError, struct.error):
                self._path = None
        queue, seen, candidates = deque([(root, (root,))]), set(), []
        while queue:
            frame, path = queue.popleft()
            if frame in seen:
                raise _Unknown('UI 树存在重复成员')
            seen.add(frame)
            if len(seen) > 4096 or len(path) > 32:
                raise _Unknown('UI 树定位超过节点预算')
            if not _visible(memory, frame):
                continue
            vtable = memory.u64(frame)
            if not base + 0x2600000 <= vtable < base + 0x2e00000:
                raise _Unknown('UI 节点虚表不正确')
            if vtable == base + PANEL_VTABLE:
                candidates.append(path)
                continue
            queue.extend((child, path + (child,)) for child in _children(memory, frame))
        if len(candidates) != 1:
            if not candidates:
                return None
            raise _Unknown('存在多个可见战绩面板')
        self._path = candidates[0]
        return self._path

    def _names(self, memory, base, row, slot):
        found = {}
        for child in _children(memory, row):
            vtable = memory.u64(child) - base
            if vtable in (NAME_VTABLE, HERO_NAME_VTABLE):
                if vtable in found or memory.read(child + 0x130, 1)[0] != slot:
                    raise _Unknown('玩家显示节点归属不一致')
                found[vtable] = _display_text(memory, base, child)
        if set(found) != {NAME_VTABLE, HERO_NAME_VTABLE}:
            raise _Unknown('当前行缺少可信玩家名称')
        return found[NAME_VTABLE], found[HERO_NAME_VTABLE]

    @staticmethod
    def _visible_row_members(memory, base, panel):
        """复核 panel 下真实行成员；行以下不再展开，避免遍历天赋/统计叶子。"""
        queue, seen, rows = deque([panel]), set(), set()
        while queue:
            frame = queue.popleft()
            if frame in seen or len(seen) >= 2048:
                raise _Unknown('面板行成员链重复或超过界限')
            seen.add(frame)
            if memory.u64(frame) == base + ROW_VTABLE:
                rows.add(frame)
                continue
            if _visible(memory, frame):
                queue.extend(_children(memory, frame))
        return rows

    @staticmethod
    def _row_contents(memory, base, row, slot, relation, names, scales, render_size, visible_tree):
        """统计与天赋树共享一次真实后代遍历，避免信任脱离行的缓存指针。"""
        queue, seen, records = deque([row]), set(), []
        while queue:
            frame = queue.popleft()
            if frame in seen or len(seen) >= 512:
                raise _Unknown('统计格成员链重复或超过界限')
            seen.add(frame)
            kind = memory.u64(frame) - base
            if not _visible(memory, frame) or kind in (TREE_VTABLE, NAME_VTABLE, HERO_NAME_VTABLE, 0x269b190):
                continue
            if kind != SCORE_GROUP_VTABLE:
                queue.extend(_children(memory, frame))
                continue
            if memory.read(frame + 0x130, 1)[0] != slot:
                raise _Unknown('统计组玩家归属不一致')
            count, array = memory.u32(frame + 0x140), memory.u64(frame + 0x150)
            if not 0 < count <= 32:
                raise _Unknown('统计格数量不正确')
            children = set(_children(memory, frame))
            items = struct.unpack('<' + 'Q' * count, memory.read(array, count * 8))
            if len(set(items)) != count or not set(items) <= children:
                raise _Unknown('统计格不属于当前组')
            for cell in items:
                if memory.u64(cell) != base + SCORE_LABEL_VTABLE or not _visible(memory, cell):
                    continue
                if memory.read(cell + 0x149, 1)[0] != slot:
                    raise _Unknown('统计格玩家归属不一致')
                # ScoreValueLink 是 interned 共享字符串，不是 talent catalog handle。
                link = _shared_text(memory, memory.u64(cell + 0x130), limit=80)
                metric = _STAT_LINKS.get(link)
                if metric is None:
                    continue
                label = memory.u64(cell + 0x150)
                if label not in _children(memory, cell) or not _visible(memory, label):
                    continue
                text = _label_text(memory, base, label, allow_empty=True)
                if re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)', text) is None:
                    continue
                value = int(text.replace(',', ''))
                if not 0 <= value < 2**32:
                    raise _Unknown('统计显示数值超出界限')
                rect = _render_rect(_rect(memory, cell), scales, render_size)
                if rect is not None:
                    records.append((slot, relation, names, metric, value, rect, cell, label))
        if visible_tree is not None and visible_tree not in seen:
            raise _Unknown('可见天赋子树不属于当前行的有效成员链')
        return tuple(records)

    @staticmethod
    def _relation(memory, base, slot, local):
        if slot == local:
            return 'ally'
        # Native9df4a0，CScoreValueFrame84aba0使用其self/ally/enemy结果。
        bit = 1 << local
        normal = memory.u32(base + 0x35a1c50 + slot * 4)
        friendly = memory.u32(base + 0x35a1e10 + slot * 4)
        if friendly & bit:
            return 'ally'
        if not normal & bit:
            return 'enemy'
        return 'unknown'

    @staticmethod
    def _baseline_quests(memory, base, tree, slot, relation, hero_id, names, scales, render_size):
        """读取同一可信玩家天赋树下的原生基础任务图标，不把未选天赋当任务。"""
        queue, seen, indices, records = deque([tree]), set(), set(), []
        while queue:
            frame = queue.popleft()
            if frame in seen or len(seen) >= 128:
                raise _Unknown('基础任务成员链重复或超过界限')
            seen.add(frame)
            if not _visible(memory, frame):
                continue
            kind = memory.u64(frame) - base
            if kind in (ICON_VTABLE, SCORE_GROUP_VTABLE):
                continue
            if kind != BASELINE_QUEST_VTABLE:
                queue.extend(_children(memory, frame))
                continue
            index = memory.u32(frame + 0x148)
            if index > 1 or index in indices:
                raise _Unknown('基础任务索引不正确或重复')
            indices.add(index)
            handle = memory.u64(frame + 0x150)
            quest_id, catalog_index = _link(memory, handle)
            # 原生 Behavior resolver 201d8f0；不触发延迟解析器。
            manager = memory.u64(base + 0x3388b18)
            bucket = memory.u64(manager + 0x70)
            count, table = memory.u32(bucket + 0x38), memory.u64(bucket + 0x48)
            if not 0 <= catalog_index < count <= 65536:
                raise _Unknown('基础任务目录索引不正确')
            descriptor = memory.u64(table + catalog_index * 8)
            if (not descriptor or descriptor & 31 or memory.read(descriptor, 1)[0] & 1
                    or not memory.u32(descriptor + 0x160) & (1 << 12)):
                raise _Unknown('基础任务目录尚未解析或类型不正确')
            image = memory.u64(frame + 0x160)
            _vtable(memory, base, image, IMAGE_VTABLE)
            if image not in _children(memory, frame) or not _visible(memory, image):
                continue
            rect = _render_rect(_rect(memory, image), scales, render_size)
            if rect is None:
                continue
            progress = _quest_progress(memory, base, frame, baseline=True, player_slot=slot,
                target_index=index, hero_id=hero_id, target_id=quest_id)
            if (memory.u64(frame + 0x150) != handle
                    or memory.u64(table + catalog_index * 8) != descriptor):
                raise _Unknown('基础任务绑定正在变化')
            records.append((slot, relation, hero_id, names, quest_id, index, rect,
                            frame, image, progress))
        return tuple(records)

    def _capture(self, memory, base, binding):
        guards = _guards(memory, base)
        root = memory.u64(base + ROOT_RVA)
        _vtable(memory, base, root, ROOT_VTABLE)
        root_rect = _rect(memory, root)
        scales = struct.unpack('<2f', memory.read(base + 0x2f868f4, 8))
        if (root_rect is None or not all(math.isfinite(s) and 0 < s < 16 for s in scales)):
            raise _Unknown('游戏视口坐标尚未确认')
        width, height = root_rect[2] - root_rect[0], root_rect[3] - root_rect[1]
        render_size = width * scales[0], height * scales[1]
        self.last_diagnostic.update(render_width=render_size[0], render_height=render_size[1])
        path = self._locate(memory, base, root)
        input_state = self._input_fields(memory, base, root)
        records, stat_records, quest_records, cooldown_records = [], [], [], []
        if path is not None and all(_visible(memory, frame) for frame in path):
            panel = path[-1]
            count, array = memory.u32(panel + 0x188), memory.u64(panel + 0x198)
            if not 0 < count <= 10:
                raise _Unknown('战绩面板行数不正确')
            rows = struct.unpack('<' + 'Q' * count, memory.read(array, count * 8))
            row_members = self._visible_row_members(memory, base, panel)
            slots, members = set(), set()
            # Rows live under the panel's tab container rather than its immediate children.
            for row in rows:
                _vtable(memory, base, row, ROW_VTABLE)
                if row in members:
                    raise _Unknown('战绩面板存在重复行')
                members.add(row)
                if not _visible(memory, row):
                    continue
                if row not in row_members:
                    raise _Unknown('可见战绩行不属于当前面板')
                slot = memory.read(row + 0x158, 1)[0]
                if slot == 16:
                    continue
                if slot > 15 or slot in slots:
                    raise _Unknown('战绩行玩家绑定不正确')
                slots.add(slot)
                relation = self._relation(memory, base, slot, binding[0])
                if relation == 'unknown':
                    continue
                names = self._names(memory, base, row, slot)
                tree = memory.u64(row + 0x150)
                _vtable(memory, base, tree, TREE_VTABLE)
                if memory.read(tree + 0x130, 1)[0] != slot:
                    raise _Unknown('天赋子树玩家归属不一致')
                tree_visible = _visible(memory, tree)
                row_stats = self._row_contents(memory, base, row, slot, relation, names,
                    scales, render_size, tree if tree_visible else None)
                hero_id, index = '', None
                if tree_visible or row_stats:
                    try:
                        model = _model(memory, base, slot)
                        handle = memory.u64(model + 0x50)
                        hero_id, index = _link(memory, handle)
                    except (OSError, ValueError, RuntimeError, TypeError, UnicodeError, struct.error):
                        # 中文统计原先无需英雄目录。可见天赋保持原校验；统计独立页
                        # 缺少精确英雄 ID 时仍保留原统计，只拒绝需要该 ID 的英文输出。
                        if tree_visible:raise
                stat_records.extend(record + (hero_id,) for record in row_stats)
                if not tree_visible:
                    continue
                catalog = _catalog(memory, base, index)
                quest_records.extend(self._baseline_quests(memory, base, tree, slot, relation,
                    hero_id, names, scales, render_size))
                cooldown_records.extend((record, names, hero_id) for record in read_row_cooldowns(
                    memory, base, row, slot, relation, scales, render_size))
                tier_count, tier_array = memory.u32(catalog + 0x518), memory.u64(catalog + 0x528)
                entry_count, entry_array = memory.u32(catalog + 0x500), memory.u64(catalog + 0x510)
                if not 0 < tier_count <= 32 or not 0 < entry_count <= 256:
                    raise _Unknown('英雄天赋目录大小不正确')
                selected = struct.unpack('<32i', memory.read(base + 0x3677a30 + slot * 0x80, 128))
                icons_count, icons_array = memory.u32(tree + 0x140), memory.u64(tree + 0x150)
                if not 0 < icons_count <= 33:
                    raise _Unknown('天赋格数量不正确')
                icons = struct.unpack('<' + 'Q' * icons_count, memory.read(icons_array, icons_count * 8))
                children = set(_children(memory, tree))
                if len(set(icons)) != len(icons) or not set(icons) <= children:
                    raise _Unknown('天赋格不属于当前子树')
                for icon in icons:
                    _vtable(memory, base, icon, ICON_VTABLE)
                    if not _visible(memory, icon):
                        continue
                    schema_index = memory.u32(icon + 0x150)
                    flat = struct.unpack('<i', memory.read(icon + 0x154, 4))[0]
                    if flat == -1:
                        continue
                    if (schema_index >= tier_count or not 0 <= flat < entry_count
                            or memory.u64(icon + 0x148) != handle):
                        raise _Unknown('天赋格英雄或选择绑定不正确')
                    tier_id, level = struct.unpack('<II', memory.read(tier_array + schema_index * 12, 8))
                    if not 0 <= tier_id < 32 or not 1 <= level <= 100 or selected[tier_id] != flat:
                        raise _Unknown('天赋格与实际已选天赋不一致')
                    entry = entry_array + flat * 0x40
                    if memory.u32(entry + 8) != tier_id:
                        raise _Unknown('天赋项不属于当前层级')
                    talent_id = _link(memory, memory.u64(entry))[0]
                    image = memory.u64(icon + 0x160)
                    _vtable(memory, base, image, IMAGE_VTABLE)
                    if image not in _children(memory, icon):
                        raise _Unknown('天赋图像不属于当前格')
                    if not _visible(memory, image):
                        continue
                    rect = _rect(memory, image)
                    if rect is None:
                        continue
                    render = _render_rect(rect, scales, render_size)
                    progress = _quest_progress(memory, base, icon, player_slot=slot,
                        target_index=tier_id, hero_id=hero_id, target_id=talent_id)
                    records.append((slot, relation, hero_id, names, tier_id, level, talent_id,
                                    render, icon, image, progress))
        if _guards(memory, base) != guards or memory.u64(base + ROOT_RVA) != root:
            raise _Unknown('游戏 UI 会话正在变化')
        return (root, path, render_size, tuple(records), input_state, tuple(stat_records),
                tuple(quest_records), tuple(cooldown_records))

    def poll(self, target_pid, hwnd, now):
        memory = None
        self.last_read_bytes = self.last_read_calls = 0
        self.last_diagnostic = dict(stage='connect', expected_build=98348, cooldown_alias_count=0)
        self._reader.last_diagnostic = {}
        connected = False
        try:
            if type(hwnd) is not int or hwnd <= 0 or not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
                raise _Unknown('目标窗口或采样时间不正确')
            session = self._reader._connect(target_pid)
            connected = True
            pid, _, build, base, created = session
            self.last_diagnostic.update(build=build, stage='identity')
            memory = _BoardMemory(self._reader._game, diagnostic_info=self.last_diagnostic,
                                  diagnostics=self.diagnostics, diagnostic_source='broadcast')
            if memory.read(base, 2) != b'MZ':
                raise _Unknown('游戏模块验证失败')
            process_token = (pid, created, base, build)
            binding = self._reader._resolve(memory, base, process_token)
            self.last_diagnostic.update(stage='board_profile', board_profile='98348-quest-cd-1',
                                        board_code_verified=0)
            for field, (rva, expected) in zip(_BOARD_CODE_FIELDS, BOARD_FIELD_GETTERS.items()):
                actual = memory.read(base + rva, len(expected))
                if actual != expected:
                    self.last_diagnostic.update(board_code_field=field,
                        board_code_fingerprint=hashlib.sha256(actual).hexdigest(),
                        board_expected_fingerprint=hashlib.sha256(expected).hexdigest())
                    raise _Unknown('任务与冷却字段映射代码已变化')
            self.last_diagnostic['board_code_verified'] = 1
            self.last_diagnostic['stage'] = 'board'
            first = self._capture(memory, base, binding)
            second = self._capture(memory, base, binding)
            if _capture_identity(first) != _capture_identity(second):
                raise _Unknown('战绩面板或玩家身份正在变化')
            self.last_diagnostic['stage'] = 'identity'
            if self._reader._resolve(memory, base, process_token) != binding:
                raise _Unknown('战绩面板或玩家身份正在变化')
            self.last_diagnostic['stage'] = 'board'
            if self._reader.creation_token_finder(self._reader._game) != created:
                raise _Unknown('游戏进程代次已变化')
            token = self._token(process_token, binding)
            self.last_diagnostic.update(stage='coordinates', raw_talent_count=len(second[3]),
                raw_stat_count=len(second[5]), raw_quest_count=len(second[6]),
                raw_cooldown_count=len(second[7]), coordinate_rejected=0, metadata_rejected=0,
                progress_rejected=0, cooldown_rejected=0)
            hits, mapped_talents, cooldown_aliases = [], [], set()
            if self.text_provider is not None and self.viewport_converter is not None:
                for slot, relation, hero_id, names, tier_id, level, talent_id, rect, _, image, progress in second[3]:
                    if progress[0] in ('unknown', 'untracked'):
                        self.last_diagnostic['progress_rejected'] += 1
                        continue
                    text = self.text_provider(hero_id, talent_id)
                    if text is None:
                        self.last_diagnostic['metadata_rejected'] += 1
                        continue
                    get = text.get if isinstance(text, dict) else lambda key: getattr(text, key, None)
                    name, effect = get('talent_name'), get('effect_summary')
                    catalog_level, catalog_tier = get('level'), get('tier')
                    if ((catalog_level is not None and catalog_level != level)
                            or (catalog_tier is not None and catalog_tier != tier_id)):
                        self.last_diagnostic['metadata_rejected'] += 1
                        continue
                    if not isinstance(name, str) or not name.strip() or not isinstance(effect, str) or not effect.strip():
                        self.last_diagnostic['metadata_rejected'] += 1
                        continue
                    screen = self.viewport_converter(hwnd, rect, second[2])
                    if (not isinstance(screen, tuple) or len(screen) != 4
                            or any(type(v) is not int for v in screen)
                            or not screen[0] < screen[2] or not screen[1] < screen[3]):
                        self.last_diagnostic['coordinate_rejected'] += 1
                        continue
                    hit = HitSnapshot(pid, hwnd, token, slot, relation, level, talent_id,
                                      name, effect, screen, float(now), float(now) + .75,
                                      True, True, True, names[0], names[1],
                                      quest_progress=progress[1], quest_state=progress[0],
                                      hero_id=hero_id)
                    hits.append(hit)
                    face_id = get('face_id')
                    catalog_bound = (type(catalog_level) is int and catalog_level == level
                                     and type(catalog_tier) is int and catalog_tier == tier_id)
                    mapped_talents.append(_MappedTalent(hit, hero_id, rect, image,
                        face_id if isinstance(face_id, str) else None, catalog_bound))
            if self.viewport_converter is not None:
                for slot, relation, hero_id, names, quest_id, index, rect, _, _, progress in second[6]:
                    if progress[0] not in ('known', 'complete'):
                        self.last_diagnostic['progress_rejected'] += 1
                        continue
                    # 离线资料必须按当前英雄及精确基础任务ID匹配，避免旧图标串到新英雄。
                    text = self.quest_text_provider(hero_id, quest_id) if self.quest_text_provider else None
                    get = text.get if isinstance(text, dict) else lambda key: getattr(text, key, None)
                    name = get('quest_name')
                    if not isinstance(name, str) or not name.strip():
                        self.last_diagnostic['metadata_rejected'] += 1
                        name = f'基础任务{index + 1}'
                    screen = self.viewport_converter(hwnd, rect, second[2])
                    if (not isinstance(screen, tuple) or len(screen) != 4
                            or any(type(v) is not int for v in screen)
                            or not screen[0] < screen[2] or not screen[1] < screen[3]):
                        self.last_diagnostic['coordinate_rejected'] += 1
                        continue
                    hits.append(QuestHitSnapshot(pid=pid, hwnd=hwnd, match_token=token,
                        player_slot=slot, team_relation=relation, quest_id=quest_id, quest_name=name,
                        progress=progress[1], state=progress[0], rect=screen, observed_at=float(now),
                        valid_until=float(now) + .75, panel_visible=True, mapping_trusted=True,
                        player_name=names[0], hero_name=names[1], hero_id=hero_id))
            if self.viewport_converter is not None:
                for record, names, hero_id in second[7]:
                    if record.state not in ('cooldown', 'ready'):
                        self.last_diagnostic['cooldown_rejected'] += 1
                        continue
                    screen = self.viewport_converter(hwnd, record.rect, second[2])
                    if (not isinstance(screen, tuple) or len(screen) != 4
                            or any(type(v) is not int for v in screen)
                            or not screen[0] < screen[2] or not screen[1] < screen[3]):
                        self.last_diagnostic['coordinate_rejected'] += 1
                        continue
                    aliases = [talent for talent in mapped_talents
                               if talent.hit.rect == screen and talent.aliases_cooldown(record, hero_id)]
                    alias = aliases[0] if len(aliases) == 1 else None
                    if alias is not None:
                        name = alias.hit.talent_name
                    else:
                        text = self.text_provider(hero_id, record.ability_id) if self.text_provider else None
                        get = text.get if isinstance(text, dict) else lambda key: getattr(text, key, None)
                        name = get('talent_name') or record.ability_name
                    hits.append(CooldownHitSnapshot(pid=pid, hwnd=hwnd, match_token=token,
                        player_slot=record.player_slot, team_relation='ally',
                        ability_id=record.ability_id, ability_name=name,
                        remaining_seconds=record.remaining_seconds, state=record.state,
                        rect=screen, observed_at=float(now), valid_until=float(now) + .75,
                        panel_visible=True, mapping_trusted=True,
                        player_name=names[0], hero_name=names[1], hero_id=hero_id))
                    if alias is not None:
                        cooldown_aliases.add(id(alias.hit))
            if cooldown_aliases:
                # 多个潜在别名仍保留全部目标，由唯一命中规则拒绝真正的歧义。
                hits = [hit for hit in hits if id(hit) not in cooldown_aliases]
                self.last_diagnostic['cooldown_alias_count'] = len(cooldown_aliases)
            if self.viewport_converter is not None:
                for slot, relation, names, metric, value, rect, _, _, hero_id in second[5]:
                    screen = self.viewport_converter(hwnd, rect, second[2])
                    if (not isinstance(screen, tuple) or len(screen) != 4
                            or any(type(v) is not int for v in screen)
                            or not screen[0] < screen[2] or not screen[1] < screen[3]):
                        self.last_diagnostic['coordinate_rejected'] += 1
                        continue
                    hits.append(StatHitSnapshot(pid, hwnd, token, slot, relation, metric, value,
                                               screen, float(now), float(now) + .75,
                                               True, True, names[0], names[1], hero_id=hero_id))
            state = 'available' if hits else 'idle'
            reason = '' if hits else '当前没有可验证且可转换坐标的天赋或统计格'
            if not hits:
                if self.viewport_converter is None:
                    reason = '未配置坐标转换器'
                elif self.last_diagnostic['coordinate_rejected']:
                    reason = '所有候选格的坐标转换均失败'
                elif self.last_diagnostic['metadata_rejected'] or (second[3] and self.text_provider is None):
                    reason = '天赋中文资料缺失或与当前层级不一致'
                elif second[1] is None:
                    reason = '未找到可见的 Tab 面板'
                else:
                    reason = 'Tab 面板中没有可验证的已选天赋或统计格'
            self.last_diagnostic.update(outcome=state, reason=reason, hit_count=len(hits))
            return BoardSnapshot(pid, hwnd, token, state, reason, tuple(hits), *second[4][:2])
        except (OSError, ValueError, RuntimeError, TypeError, UnicodeError, struct.error) as exc:
            if not connected:
                self.last_diagnostic.update(getattr(self._reader, 'last_diagnostic', {}))
            self.last_diagnostic.update(outcome='unknown', error_type=type(exc).__name__,
                reason=str(exc) if isinstance(exc, _Unknown) else '面板读取失败')
            if type(getattr(exc, 'winerror', None)) is int:
                self.last_diagnostic['winerror'] = exc.winerror
            self.close()
            return BoardSnapshot(target_pid, hwnd, reason=str(exc) or '面板状态暂不可用')
        finally:
            # 身份解析器也用于自动天赋；保留相同细节以跨功能比较，
            # 不用内层阶段/计数覆盖本次面板采样的数据。
            inner = getattr(self._reader, 'last_diagnostic', {})
            for field in ('identity_reason', 'identity_error_type', 'winerror'):
                if field in inner:self.last_diagnostic[field] = inner[field]
            if memory is not None:
                self.last_read_bytes, self.last_read_calls = memory.bytes, memory.calls
            self.last_diagnostic.update(read_calls=self.last_read_calls, read_bytes=self.last_read_bytes)
