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

from ..talent_broadcast import HitSnapshot, StatHitSnapshot
from ..board_sender import ChatInputState
from .talent_source import TalentReader, _Memory, _Unknown, _guards, _model, _link, _catalog


ROOT_RVA = 0x338b2e8
ROOT_VTABLE = 0x2653738
PANEL_VTABLE = 0x2689b88
ROW_VTABLE = 0x2689920
TREE_VTABLE = 0x26916f0
ICON_VTABLE = 0x2680e88
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
    hits: tuple[HitSnapshot | StatHitSnapshot, ...] = ()
    chat_active: bool | None = None
    menu_active: bool | None = None
    coordinate_space: str = 'screen'


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
    def __init__(self, *, text_provider=None, viewport_converter=None,
                 input_state_provider=None, **talent_reader_options):
        self.text_provider = text_provider
        self.viewport_converter = viewport_converter
        self.input_state_provider = input_state_provider
        self._reader = TalentReader(**talent_reader_options)
        self._path = None
        self.last_read_bytes = self.last_read_calls = 0

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
            memory = _BoardMemory(self._reader._game)
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
            if not _visible(memory, frame) or kind in (TREE_VTABLE, NAME_VTABLE, HERO_NAME_VTABLE):
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
        path = self._locate(memory, base, root)
        input_state = self._input_fields(memory, base, root)
        records, stat_records = [], []
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
                stat_records.extend(self._row_contents(memory, base, row, slot, relation, names,
                    scales, render_size, tree if tree_visible else None))
                if not tree_visible:
                    continue
                model = _model(memory, base, slot)
                handle = memory.u64(model + 0x50)
                hero_id, index = _link(memory, handle)
                catalog = _catalog(memory, base, index)
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
                    records.append((slot, relation, hero_id, names, tier_id, level, talent_id, render, icon, image))
        if _guards(memory, base) != guards or memory.u64(base + ROOT_RVA) != root:
            raise _Unknown('游戏 UI 会话正在变化')
        return root, path, render_size, tuple(records), input_state, tuple(stat_records)

    def poll(self, target_pid, hwnd, now):
        memory = None
        self.last_read_bytes = self.last_read_calls = 0
        try:
            if type(hwnd) is not int or hwnd <= 0 or not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
                raise _Unknown('目标窗口或采样时间不正确')
            session = self._reader._connect(target_pid)
            pid, _, build, base, created = session
            memory = _BoardMemory(self._reader._game)
            if memory.read(base, 2) != b'MZ':
                raise _Unknown('游戏模块验证失败')
            process_token = (pid, created, base, build)
            binding = self._reader._resolve(memory, base, process_token)
            first = self._capture(memory, base, binding)
            second = self._capture(memory, base, binding)
            if first != second or self._reader._resolve(memory, base, process_token) != binding:
                raise _Unknown('战绩面板或玩家身份正在变化')
            if self._reader.creation_token_finder(self._reader._game) != created:
                raise _Unknown('游戏进程代次已变化')
            token = self._token(process_token, binding)
            hits = []
            if self.text_provider is not None and self.viewport_converter is not None:
                for slot, relation, hero_id, names, tier_id, level, talent_id, rect, _, _ in second[3]:
                    text = self.text_provider(hero_id, talent_id)
                    if text is None:
                        continue
                    get = text.get if isinstance(text, dict) else lambda key: getattr(text, key, None)
                    name, effect = get('talent_name'), get('effect_summary')
                    catalog_level, catalog_tier = get('level'), get('tier')
                    if ((catalog_level is not None and catalog_level != level)
                            or (catalog_tier is not None and catalog_tier != tier_id)):
                        continue
                    if not isinstance(name, str) or not name.strip() or not isinstance(effect, str) or not effect.strip():
                        continue
                    screen = self.viewport_converter(hwnd, rect, second[2])
                    if (not isinstance(screen, tuple) or len(screen) != 4
                            or any(type(v) is not int for v in screen)
                            or not screen[0] < screen[2] or not screen[1] < screen[3]):
                        continue
                    hits.append(HitSnapshot(pid, hwnd, token, slot, relation, level, talent_id,
                                            name, effect, screen, float(now), float(now) + .75,
                                            True, True, True, names[0], names[1]))
            if self.viewport_converter is not None:
                for slot, relation, names, metric, value, rect, _, _ in second[5]:
                    screen = self.viewport_converter(hwnd, rect, second[2])
                    if (not isinstance(screen, tuple) or len(screen) != 4
                            or any(type(v) is not int for v in screen)
                            or not screen[0] < screen[2] or not screen[1] < screen[3]):
                        continue
                    hits.append(StatHitSnapshot(pid, hwnd, token, slot, relation, metric, value,
                                               screen, float(now), float(now) + .75,
                                               True, True, names[0], names[1]))
            state = 'available' if hits else 'idle'
            reason = '' if hits else '当前没有可验证且可转换坐标的天赋或统计格'
            return BoardSnapshot(pid, hwnd, token, state, reason, tuple(hits), *second[4][:2])
        except (OSError, ValueError, RuntimeError, TypeError, UnicodeError, struct.error) as exc:
            self.close()
            return BoardSnapshot(target_pid, hwnd, reason=str(exc) or '面板状态暂不可用')
        finally:
            if memory is not None:
                self.last_read_bytes, self.last_read_calls = memory.bytes, memory.calls
