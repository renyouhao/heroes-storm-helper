"""任务/CD 读取整链与不同视口的合成验证，不调用游戏或窗口 API。"""
import struct
import unittest
from unittest.mock import patch

from tests.test_board_cooldown import CooldownMemory
from tests.test_talent_source import Binding
from tests.test_model_codecs import set_memory_codec
from hots_chat.acquisition.board_source import BoardReader, BOARD_FIELD_GETTERS
from hots_chat.acquisition.memory import GameProcess
from hots_chat.talent_broadcast import HitSnapshot, QuestHitSnapshot, CooldownHitSnapshot


class ProgressMemory(CooldownMemory):
    def __init__(self):
        super().__init__()
        self.talent_label = self.new_label('2')
        self.children(self.icon, self.image, self.talent_label)
        self.set_talent_progress(1, 2, '2')
        self.baselines = []
        # Behavior 与英雄 Unit 是不同目录 bucket，故意使用两个不同表。
        self.behavior_bucket = self.allocate(0x60)
        self.behavior_table = self.allocate(32 * 8)
        self.pack(self.manager + 0x70, 'Q', self.behavior_bucket)
        self.pack(self.behavior_bucket + 0x38, 'I', 32)
        self.pack(self.behavior_bucket + 0x48, 'Q', self.behavior_table)

    def new_label(self, text):
        wrapper = self.label_frame(0x26928d0, text)
        return self.u64(wrapper + 0x140)

    def u64(self, address):
        return struct.unpack('<Q', self.read(address, 8))[0]

    def set_label(self, label, text, visible=True):
        obj = self.u64(label + 0x1d8)
        shared = self.u64(obj + 0x28)
        encoded = text.encode('utf8')
        self.pack(shared + 0x18, 'II', len(encoded) << 2, 0)
        self.put(shared + 0x20, encoded)
        self.put(label + 0x48, b'\x21' if visible else b'\x01')

    def set_talent_progress(self, state, value, text, *, visible=True):
        self.pack(self.icon + 0x178, 'II', state, value)
        self.set_label(self.talent_label, text, visible)

    def add_baseline(self, index=0, state=1, value=2, text='2', *, visible=True):
        frame = self.frame(0x269ab08)
        image = self.frame(0x2757668, rect=(500., 120., 540., 160.))
        label = self.new_label(text)
        catalog_index = 7 + len(self.baselines)
        handle = self.link('ChromieBaselineQuest' + str(index), catalog_index)
        descriptor = self.allocate(0x180)
        self.pack(descriptor + 0x160, 'I', 1 << 12)
        self.pack(self.behavior_table + catalog_index * 8, 'Q', descriptor)
        self.pack(frame + 0x148, 'I', index)
        self.pack(frame + 0x14c, 'I', value)
        self.pack(frame + 0x150, 'Q', handle)
        self.pack(frame + 0x160, 'Q', image)
        self.pack(frame + 0x178, 'I', state)
        self.children(frame, image, label)
        self.set_label(label, text, visible)
        record = dict(frame=frame, image=image, label=label, descriptor=descriptor,
                      handle=handle, catalog_index=catalog_index)
        self.baselines.append(record)
        self.children(self.tree, self.icon, *(item['frame'] for item in self.baselines))
        return record

    def set_baseline_progress(self, record, state, value, text, *, visible=True):
        self.pack(record['frame'] + 0x178, 'I', state)
        self.pack(record['frame'] + 0x14c, 'I', value)
        self.set_label(record['label'], text, visible)


class BoardProgressTests(unittest.TestCase):
    def setUp(self):
        self.memory = ProgressMemory()
        self.binding = Binding()
        self.conversions = []

        def converter(hwnd, rect, size):
            self.conversions.append((hwnd, rect, size))
            return tuple(round(value) for value in rect)

        self.reader = BoardReader(
            text_provider=lambda hero, item: {'talent_name': '测试项目', 'effect_summary': '测试效果'},
            quest_text_provider=lambda hero, quest: {'quest_name': '基础时光任务'},
            viewport_converter=converter, binding_provider=lambda *_: self.binding,
            process_finder=lambda: [GameProcess(123, self.memory.path, 98348)],
            game_factory=lambda _: self.memory, module_base_finder=lambda _: self.memory.base,
            creation_token_finder=lambda _: self.memory.creation)

    def tearDown(self):
        self.reader.close()

    def poll(self):
        return self.reader.poll(123, 77, 10.)

    @staticmethod
    def hits(result, kind):
        return tuple(hit for hit in result.hits if isinstance(hit, kind))

    def change_between_captures(self, change):
        capture = self.reader._capture
        calls = 0

        def changing(*args):
            nonlocal calls
            result = capture(*args)
            calls += 1
            if calls == 1:
                change()
            return result

        with patch.object(self.reader, '_capture', changing):
            return self.poll()

    def test_active_talent_zero_and_positive_values_require_exact_ui_agreement(self):
        for value in (0, 2, 1024):
            with self.subTest(value=value):
                text = format(value, ',')
                self.memory.set_talent_progress(1, value, text)
                result = self.poll()
                self.assertEqual(result.status, 'available', result.reason)
                hit, = self.hits(result, HitSnapshot)
                self.assertEqual((hit.quest_state, hit.quest_progress), ('known', value))

    def test_hidden_non_numeric_mismatch_and_ambiguous_labels_reject_progress(self):
        for visible, text, extra in ((False, '2', False), (True, '任务', False),
                                     (True, '3', False), (True, '2', True)):
            with self.subTest(visible=visible, text=text, extra=extra):
                self.memory.set_talent_progress(1, 2, text, visible=visible)
                label = self.memory.new_label('2')
                children = (self.memory.image, self.memory.talent_label) + ((label,) if extra else ())
                self.memory.children(self.memory.icon, *children)
                result = self.poll()
                self.assertEqual(self.hits(result, HitSnapshot), ())
                self.assertEqual(len(self.hits(result, CooldownHitSnapshot)), 1)

    def test_nonquest_untracked_and_completed_never_reuse_stale_count(self):
        for state, expected in ((0, ''), (2, None), (3, 'complete')):
            with self.subTest(state=state):
                self.memory.set_talent_progress(state, 888, '888', visible=False)
                result = self.poll()
                hits = self.hits(result, HitSnapshot)
                if expected is None:
                    self.assertEqual(hits, ())
                else:
                    hit, = hits
                    self.assertEqual(hit.quest_state, expected)
                    self.assertIsNone(hit.quest_progress)

    def test_infinite_quest_state_four_keeps_accumulating(self):
        for value in (0, 3, 9999):
            self.memory.set_talent_progress(4, value, str(value))
            hit, = self.hits(self.poll(), HitSnapshot)
            self.assertEqual((hit.quest_state, hit.quest_progress), ('known', value))

    def test_live_progress_two_to_three_uses_second_value_without_losing_board(self):
        result = self.change_between_captures(
            lambda: self.memory.set_talent_progress(1, 3, '3'))
        self.assertEqual(result.status, 'available', result.reason)
        hit, = self.hits(result, HitSnapshot)
        self.assertEqual(hit.quest_progress, 3)
        self.assertFalse(self.memory.closed)

    def test_selected_talent_change_is_still_rejected_between_captures(self):
        def change():
            selected = self.memory.selected.copy()
            selected[1] = 2
            self.memory.set_selected(selected)
        result = self.change_between_captures(change)
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(result.hits, ())

    def test_live_cooldown_change_and_expiry_do_not_change_binding_identity(self):
        for text, visible, expected in (('29', True, 29.), ('0.1', True, .1),
                                        ('0.1', False, None)):
            with self.subTest(text=text, visible=visible):
                self.memory.set_label(self.memory.cooldown_label, '38')
                def change():
                    self.memory.set_label(self.memory.cooldown_label, text, visible)
                result = self.change_between_captures(change)
                self.assertEqual(result.status, 'available', result.reason)
                hits = self.hits(result, CooldownHitSnapshot)
                if expected is None:
                    self.assertEqual(hits, ())
                else:
                    hit, = hits
                    self.assertEqual((hit.state, hit.remaining_seconds), ('cooldown', expected))

    def test_baseline_uses_behavior_bucket_and_retains_zero(self):
        self.memory.add_baseline(value=0, text='0')
        quest, = self.hits(self.poll(), QuestHitSnapshot)
        self.assertEqual((quest.quest_id, quest.quest_name, quest.state, quest.progress),
                         ('ChromieBaselineQuest0', '基础时光任务', 'known', 0))
        # 只有 Behavior bucket 的 index7 有 descriptor；英雄 Unit bucket 的同index没有。
        self.assertEqual(self.memory.read(self.memory.catalog_table + 7 * 8, 8), bytes(8))

    def test_baseline_completion_does_not_invent_a_final_value(self):
        self.memory.add_baseline(state=3, value=0xdead, text='555', visible=False)
        quest, = self.hits(self.poll(), QuestHitSnapshot)
        self.assertEqual(quest.state, 'complete')
        self.assertIsNone(quest.progress)

    def test_baseline_hidden_non_numeric_and_mismatched_labels_are_not_hits(self):
        record = self.memory.add_baseline()
        for text, visible in (('2', False), ('内容', True), ('3', True)):
            with self.subTest(text=text, visible=visible):
                self.memory.set_baseline_progress(record, 1, 2, text, visible=visible)
                self.assertEqual(self.hits(self.poll(), QuestHitSnapshot), ())

    def test_baseline_untracked_and_nonquest_states_ignore_residual_text(self):
        record = self.memory.add_baseline()
        for state in (0, 2):
            with self.subTest(state=state):
                self.memory.set_baseline_progress(record, state, 999, '999')
                self.assertEqual(self.hits(self.poll(), QuestHitSnapshot), ())

    def test_baseline_value_change_is_not_part_of_structural_identity(self):
        record = self.memory.add_baseline()
        result = self.change_between_captures(
            lambda: self.memory.set_baseline_progress(record, 1, 3, '3'))
        self.assertEqual(result.status, 'available', result.reason)
        quest, = self.hits(result, QuestHitSnapshot)
        self.assertEqual(quest.progress, 3)

    def test_baseline_infinite_progress_and_completion_transition_use_actual_state(self):
        record = self.memory.add_baseline(state=4, value=19, text='19')
        result = self.change_between_captures(
            lambda: self.memory.set_baseline_progress(record, 4, 20, '20'))
        quest, = self.hits(result, QuestHitSnapshot)
        self.assertEqual((quest.state, quest.progress), ('known', 20))
        result = self.change_between_captures(
            lambda: self.memory.set_baseline_progress(record, 3, 20, '20', visible=False))
        self.assertEqual(result.status, 'available', result.reason)
        quest, = self.hits(result, QuestHitSnapshot)
        self.assertEqual(quest.state, 'complete')
        self.assertIsNone(quest.progress)

    def test_baseline_name_falls_back_to_its_index_without_inventing_progress(self):
        record = self.memory.add_baseline(index=1, value=0, text='0')
        self.reader.quest_text_provider = lambda *_: None
        quest, = self.hits(self.poll(), QuestHitSnapshot)
        self.assertEqual((quest.quest_name, quest.progress), ('基础任务2', 0))
        self.memory.set_baseline_progress(record, 1, 2, '3')
        self.assertEqual(self.hits(self.poll(), QuestHitSnapshot), ())

    def test_baseline_descriptor_type_index_and_unresolved_tags_are_rejected(self):
        record = self.memory.add_baseline()
        target = self.memory.behavior_table + record['catalog_index'] * 8
        changes = (
            lambda: self.memory.pack(self.memory.behavior_bucket + 0x38, 'I', record['catalog_index']),
            lambda: self.memory.pack(record['descriptor'] + 0x160, 'I', 0),
            lambda: self.memory.pack(target, 'Q', record['descriptor'] | 1),
            lambda: self.memory.pack(target, 'Q', record['descriptor'] | 2),
            lambda: self.memory.put(record['descriptor'], b'\x01'),
        )
        for change in changes:
            with self.subTest(change=change):
                self.memory.pack(self.memory.behavior_bucket + 0x38, 'I', 32)
                self.memory.pack(record['descriptor'] + 0x160, 'I', 1 << 12)
                self.memory.pack(target, 'Q', record['descriptor'])
                self.memory.put(record['descriptor'], b'\0')
                self.reader.close()
                change()
                self.assertEqual(self.poll().status, 'unknown')

    def test_duplicate_or_invalid_baseline_quest_indices_are_rejected(self):
        first = self.memory.add_baseline(index=0)
        second = self.memory.add_baseline(index=0)
        self.assertEqual(self.poll().status, 'unknown')
        self.memory.pack(second['frame'] + 0x148, 'I', 2)
        self.assertEqual(self.poll().status, 'unknown')
        self.memory.pack(second['frame'] + 0x148, 'I', 1)
        self.assertEqual(len(self.hits(self.poll(), QuestHitSnapshot)), 2)

    def test_detached_baseline_image_and_removed_frame_never_become_click_targets(self):
        record = self.memory.add_baseline()
        self.memory.children(record['frame'], record['label'])
        self.assertEqual(self.hits(self.poll(), QuestHitSnapshot), ())
        self.memory.children(record['frame'], record['image'], record['label'])
        self.memory.children(self.memory.tree, self.memory.icon)
        self.assertEqual(self.hits(self.poll(), QuestHitSnapshot), ())

    def test_new_baseline_or_label_pointer_between_captures_is_rejected(self):
        self.memory.add_baseline()
        for change in ('image', 'label'):
            with self.subTest(change=change):
                def mutate():
                    record = self.memory.baselines[0]
                    if change == 'image':
                        new = self.memory.frame(0x2757668, rect=(500., 120., 540., 160.))
                        self.memory.pack(record['frame'] + 0x160, 'Q', new)
                        record['image'] = new
                    else:
                        record['label'] = self.memory.new_label('2')
                    self.memory.children(record['frame'], record['image'], record['label'])
                result = self.change_between_captures(mutate)
                self.assertEqual(result.status, 'unknown', result.reason)

    def test_every_decoder_runs_talent_baseline_and_cooldown_together(self):
        self.memory.add_baseline(value=2, text='2')
        for decoder in range(16):
            with self.subTest(decoder=decoder):
                set_memory_codec(self.memory, decoder)
                result = self.poll()
                self.assertEqual(result.status, 'available', result.reason)
                talent, = self.hits(result, HitSnapshot)
                quest, = self.hits(result, QuestHitSnapshot)
                cooldown, = self.hits(result, CooldownHitSnapshot)
                self.assertEqual((talent.quest_progress, quest.progress, cooldown.remaining_seconds), (2, 2, 38.))
                self.assertEqual(self.reader.last_diagnostic['decoder_mode'], decoder)

    def test_enemy_row_has_no_cooldown_field_or_text_reads(self):
        self.binding = Binding(slot=4)
        self.memory.pack(self.memory.base + 0x35a1c50 + 3 * 4, 'I', 0)
        self.memory.pack(self.memory.base + 0x35a1e10 + 3 * 4, 'I', 0)
        self.memory.calls.clear()
        result = self.poll()
        self.assertEqual(result.status, 'available', result.reason)
        self.assertEqual(self.hits(result, CooldownHitSnapshot), ())
        forbidden = {self.memory.ability + 0x148, self.memory.ability_icon + 0x140,
                     self.memory.ability_icon + 0x148, self.memory.cooldown_label + 0x1d8}
        self.assertFalse(forbidden & {address for address, _ in self.memory.calls})

    def test_2k_aspect_ratios_scales_and_screen_origins_use_external_transform(self):
        self.memory.add_baseline()
        for width, height, scale, screen_scale, origin in (
                (2560, 1440, (1., 1.), (.8, .8), (280, 160)),
                (2560, 1440, (1.25, 1.25), (1., 1.), (0, 0)),
                (2560, 1600, (1.2, .9), (.75, .75), (-1920, 80)),
                (2560, 1600, (.9, 1.2), (1.1, .9), (1920, 0))):
            with self.subTest(size=(width, height), scale=scale, origin=origin):
                self.reader.close()
                self.memory.pack(self.memory.root + 0xa8, '4f', 0., 0., height / scale[1], width / scale[0])
                self.memory.pack(self.memory.base + 0x2f868f4, '2f', *scale)
                native_scale = struct.unpack('<2f', self.memory.read(self.memory.base + 0x2f868f4, 8))
                seen_sizes = []
                def convert(hwnd, rect, size):
                    seen_sizes.append(size)
                    return tuple(round(value * screen_scale[i % 2] + origin[i % 2])
                                 for i, value in enumerate(rect))
                self.reader.viewport_converter = convert
                result = self.poll()
                self.assertEqual(result.status, 'available', result.reason)
                self.assertEqual(len(result.hits), 3)
                for size in seen_sizes:
                    self.assertAlmostEqual(size[0], width, places=3)
                    self.assertAlmostEqual(size[1], height, places=3)
                for hit, native in ((self.hits(result, HitSnapshot)[0], (100., 100., 140., 140.)),
                                    (self.hits(result, QuestHitSnapshot)[0], (120., 500., 160., 540.)),
                                    (self.hits(result, CooldownHitSnapshot)[0], (110., 100., 130., 140.))):
                    expected = tuple(round(value * native_scale[i % 2] * screen_scale[i % 2] + origin[i % 2])
                                     for i, value in enumerate(native))
                    self.assertEqual(hit.rect, expected)

    def test_each_progress_getter_signature_change_rejects_same_build_fields(self):
        for rva, original in BOARD_FIELD_GETTERS.items():
            with self.subTest(rva=hex(rva)):
                self.reader.close()
                self.memory.put(self.memory.base + rva, bytes([original[0] ^ 1]) + original[1:])
                result = self.poll()
                self.assertEqual(result.status, 'unknown')
                self.assertEqual(result.hits, ())
                self.assertEqual(self.reader.last_diagnostic['stage'], 'board_profile')
                self.assertEqual(self.reader.last_diagnostic['board_code_verified'], 0)
                self.memory.put(self.memory.base + rva, original)
        result = self.poll()
        self.assertEqual(result.status, 'available', result.reason)
        self.assertEqual(self.reader.last_diagnostic['board_code_verified'], 1)


if __name__ == '__main__':
    unittest.main()
