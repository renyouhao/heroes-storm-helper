"""大招覆盖层的真实 reader→命中→双击业务回归；只使用合成内存。"""
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from tests.test_board_progress import ProgressMemory
from tests.test_talent_source import Binding
from hots_chat.acquisition.board_source import BoardReader
from hots_chat.acquisition.memory import GameProcess
from hots_chat.board_service import hit_test
from hots_chat.helper_replay import record_board_hit_test, replay_helper_frame
from hots_chat.talent_broadcast import (
    BroadcastSendResult, CooldownHitSnapshot, HitSnapshot, InputContext,
    TalentBroadcastConfig, TalentBroadcastSession,
)
from hots_chat.talent_catalog import LocalizedTalent


class _Recorder:
    def __init__(self):
        self.frames = []

    def snapshot(self):
        return {'enabled': True}

    def record(self, event, **values):
        if event == 'replay.frame':
            self.frames.append(json.loads(json.dumps(values)))


class CooldownAliasTests(unittest.TestCase):
    hero_id = 'Xalatath'
    talent_id = 'XalatathHeroicAbilityVoidEruption'
    ability_id = 'XalatathVoidEruption'

    def setUp(self):
        self.memory = ProgressMemory()
        memory = self.memory
        memory.unit = memory.link(self.hero_id, 33)
        memory.pack(memory.model + 0x50, 'Q', memory.unit)
        memory.pack(memory.icon + 0x148, 'Q', memory.unit)
        memory.pack(memory.entries + 0x40, 'Q', memory.link(self.talent_id, 101))
        memory.pack(memory.rows, 'III', 1, 10, 0)
        memory.handle = memory.link(self.ability_id, 390)
        memory.pack(memory.tree + 0x170, 'Q', memory.handle)
        memory.pack(memory.ability + 0x148, 'Q', memory.handle)
        # 两个不同原生图像覆盖同一格，需精确目录绑定才能证明是同一大招。
        memory.pack(memory.ability_image + 0xa8, '4f', 100., 100., 140., 140.)
        memory.set_label(memory.cooldown_label, '9')
        self.metadata = LocalizedTalent('虚空爆发', '已核实的大招效果。', self.ability_id, 10, 1)
        self.lookups = []

        def text_provider(hero, item):
            self.lookups.append((hero, item))
            if hero in (self.hero_id, 'OtherHero') and item in (self.talent_id, self.talent_id + 'Duplicate'):
                return self.metadata
            return None

        self.reader = BoardReader(
            text_provider=text_provider,
            viewport_converter=lambda hwnd, rect, size: tuple(round(value) for value in rect),
            binding_provider=lambda *_: Binding(),
            process_finder=lambda: [GameProcess(123, memory.path, 98348)],
            game_factory=lambda _: memory, module_base_finder=lambda _: memory.base,
            creation_token_finder=lambda _: memory.creation)
        self.addCleanup(self.reader.close)

    def poll(self, transform=None):
        if transform is None:
            result = self.reader.poll(123, 77, 10.)
        else:
            capture = self.reader._capture
            with patch.object(self.reader, '_capture', lambda *args: transform(capture(*args))):
                result = self.reader.poll(123, 77, 10.)
        self.assertEqual(result.status, 'available', result.reason)
        return result

    @staticmethod
    def change_talent(capture, **changes):
        # 保留真实读取与双采样流程，只替换 mapper 的边界输入。
        positions = {'slot': 0, 'relation': 1, 'hero_id': 2, 'rect': 7, 'image': 9}
        talent = list(capture[3][0])
        for name, value in changes.items():
            talent[positions[name]] = value
        return capture[:3] + ((tuple(talent),),) + capture[4:]

    @staticmethod
    def center(hit):
        return ((hit.rect[0] + hit.rect[2]) // 2, (hit.rect[1] + hit.rect[3]) // 2)

    def assert_alias_count(self, count):
        self.assertEqual(self.reader.last_diagnostic.get('cooldown_alias_count'), count)

    def assert_replays(self, recorder):
        self.assertTrue(recorder.frames)
        for frame in recorder.frames:
            report = replay_helper_frame(frame['component'], frame['operation'], frame['payload'])
            self.assertTrue(report['matched'], report)

    def test_verified_face_overlay_maps_to_one_named_cooldown(self):
        self.assertNotEqual(self.memory.image, self.memory.ability_image)
        result = self.poll()
        self.assertEqual(len(result.hits), 1, '同一已选大招不能输出两个互相遮挡的点击目标')
        hit, = result.hits
        self.assertIsInstance(hit, CooldownHitSnapshot)
        self.assertEqual((hit.ability_name, hit.remaining_seconds), ('虚空爆发', 9.))
        self.assertIs(hit_test(result, *self.center(hit)), hit)
        self.assert_alias_count(1)
        self.assertEqual(self.reader.last_diagnostic['raw_talent_count'], 1)
        self.assertEqual(self.reader.last_diagnostic['raw_cooldown_count'], 1)

    def test_same_native_image_is_evidence_without_optional_catalog_binding(self):
        self.metadata = {'talent_name': '虚空爆发', 'effect_summary': '大招效果', 'face_id': 'Other'}
        result = self.poll(lambda capture: self.change_talent(capture, image=self.memory.ability_image))
        hit, = result.hits
        self.assertIsInstance(hit, CooldownHitSnapshot)
        self.assertEqual(hit.ability_name, '虚空爆发')
        self.assert_alias_count(1)

    def test_face_mismatch_or_absent_level_tier_does_not_hide_real_overlap(self):
        for metadata in (
                replace(self.metadata, face_id='OtherHeroic'),
                {'talent_name': '虚空爆发', 'effect_summary': '大招效果', 'face_id': self.ability_id},
                {'talent_name': '虚空爆发', 'effect_summary': '大招效果', 'face_id': self.ability_id,
                 'level': 10, 'tier': True}):
            with self.subTest(metadata=metadata):
                self.metadata = metadata
                result = self.poll()
                self.assertEqual(len(result.hits), 2)
                self.assertIsNone(hit_test(result, *self.center(result.hits[-1])))
                self.assert_alias_count(0)

    def test_other_player_relation_or_hero_is_never_an_alias(self):
        for changes in ({'slot': 4}, {'relation': 'enemy'}, {'hero_id': 'OtherHero'}):
            with self.subTest(changes=changes):
                result = self.poll(lambda capture: self.change_talent(capture, **changes))
                self.assertEqual(len(result.hits), 2)
                self.assertIsNone(hit_test(result, *self.center(result.hits[-1])))
                self.assert_alias_count(0)

    def test_same_screen_rect_without_same_native_rect_is_not_alias_evidence(self):
        self.reader.viewport_converter = lambda *_: (100, 100, 140, 140)
        result = self.poll(lambda capture: self.change_talent(capture, rect=(121., 90., 169., 126.)))
        self.assertEqual(len(result.hits), 2)
        self.assertIsNone(hit_test(result, 120, 120))
        self.assert_alias_count(0)

    def test_multiple_potential_talent_aliases_remain_ambiguous(self):
        def duplicate(capture):
            extra = list(capture[3][0])
            extra[6] = self.talent_id + 'Duplicate'
            extra[9] += 0x1000
            return capture[:3] + ((capture[3][0], tuple(extra)),) + capture[4:]
        result = self.poll(duplicate)
        self.assertEqual(len(result.hits), 3)
        self.assertIsNone(hit_test(result, *self.center(result.hits[-1])))
        self.assert_alias_count(0)

    def test_hidden_zero_and_unknown_cooldown_keep_the_selected_talent(self):
        self.poll()
        self.assert_alias_count(1)
        for text, visible in (('9', False), ('0', True), ('unknown', True)):
            with self.subTest(text=text, visible=visible):
                self.memory.set_label(self.memory.cooldown_label, text, visible)
                hit, = self.poll().hits
                self.assertIsInstance(hit, HitSnapshot)
                self.assertEqual(hit.talent_id, self.talent_id)
                self.assert_alias_count(0)

    def test_failed_cooldown_coordinate_conversion_keeps_the_talent(self):
        calls = []
        def converter(hwnd, rect, size):
            calls.append(rect)
            return tuple(round(value) for value in rect) if len(calls) == 1 else None
        self.reader.viewport_converter = converter
        hit, = self.poll().hits
        self.assertIsInstance(hit, HitSnapshot)
        self.assert_alias_count(0)
        self.assertEqual(self.reader.last_diagnostic['coordinate_rejected'], 1)

    def test_viewport_change_between_conversions_keeps_both_targets(self):
        screens = iter(((100, 100, 140, 140), (200, 200, 240, 240)))
        self.reader.viewport_converter = lambda *_: next(screens)
        result = self.poll()
        self.assertEqual([type(hit) for hit in result.hits], [HitSnapshot, CooldownHitSnapshot])
        self.assertEqual(result.hits[1].ability_name, '大招')
        for hit in result.hits:
            self.assertIs(hit_test(result, *self.center(hit)), hit)
        self.assert_alias_count(0)

    def test_separate_normal_talent_and_cooldown_both_remain_clickable(self):
        self.memory.pack(self.memory.ability_image + 0xa8, '4f', 300., 300., 340., 340.)
        result = self.poll()
        self.assertEqual([type(hit) for hit in result.hits], [HitSnapshot, CooldownHitSnapshot])
        for hit in result.hits:
            self.assertIs(hit_test(result, *self.center(hit)), hit)
        self.assert_alias_count(0)

    def test_alias_name_wins_over_an_unrelated_ability_catalog_name(self):
        provider = self.reader.text_provider
        self.reader.text_provider = lambda hero, item: (
            {'talent_name': '错误名称'} if item == self.ability_id else provider(hero, item))
        result = self.poll()
        cooldown = next(hit for hit in result.hits if isinstance(hit, CooldownHitSnapshot))
        self.assertEqual(cooldown.ability_name, '虚空爆发')

    def test_mapped_cooldown_double_click_dispatch_and_replay(self):
        result = self.poll()
        cooldown = next(hit for hit in result.hits if isinstance(hit, CooldownHitSnapshot))
        point = self.center(cooldown)
        hit = hit_test(result, *point)
        self.assertIs(hit, cooldown)
        context = InputContext(result.pid, result.hwnd, result.match_token, True)
        recorder = _Recorder()
        session = TalentBroadcastSession(double_click_ms=500, double_click_size=(4, 4),
            config=TalentBroadcastConfig(True), diagnostics=recorder)
        record_board_hit_test(recorder, result, point, context, hit, session=session)
        for now in (10., 10.2):
            session.mouse(*point, True, hit, context, now, event_at=now)
            session.mouse(*point, False, None, InputContext(), now, event_at=now)
        action = session.tick(hit, context, 10.2)
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, 'cooldown')
        self.assertIn('虚空爆发', action.message)
        self.assertIn('冷却显示：9秒', action.message)
        sent = []
        def sender(action, *, allowed):
            self.assertTrue(allowed())
            sent.append(action)
            return BroadcastSendResult('sent', 'ok')
        self.assertEqual(session.dispatch(action, sender, hit, context, 10.2).state, 'sent')
        self.assertEqual(sent, [action])
        self.assert_replays(recorder)

    def test_unrelated_overlap_still_replays_as_no_hit(self):
        def other_player(capture):
            extra = list(capture[3][0])
            extra[0] = 4
            extra[6] = self.talent_id + 'Duplicate'
            extra[9] += 0x1000
            return capture[:3] + ((capture[3][0], tuple(extra)),) + capture[4:]
        result = self.poll(other_player)
        self.assertEqual(len(result.hits), 2)
        self.assertEqual({hit.player_slot for hit in result.hits}, {3, 4})
        self.assert_alias_count(1)
        point = self.center(result.hits[-1])
        context = InputContext(result.pid, result.hwnd, result.match_token, True)
        recorder = _Recorder()
        selected = hit_test(result, *point)
        self.assertIsNone(selected)
        record_board_hit_test(recorder, result, point, context, selected)
        self.assert_replays(recorder)


if __name__ == '__main__':
    unittest.main()
