"""只读大招 CD 链的合成边界测试；不连接游戏或窗口。"""
import struct
import unittest

from tests.test_board_source import BoardMemory
from hots_chat.acquisition.board_source import _BoardMemory
from hots_chat.acquisition.talent_source import _Unknown

try:
    from hots_chat.acquisition.board_cooldown import read_row_cooldowns, parse_cooldown_display
except ModuleNotFoundError:
    read_row_cooldowns = None


class CooldownMemory(BoardMemory):
    def __init__(self):
        super().__init__()
        self.talents_container = self.frame(0x27d96d8)
        self.selection_panel = self.frame(0x2691ae8)
        self.unit_status = self.frame(0x267a300)
        self.ability = self.frame(0x269b190)
        self.ability_icon = self.frame(0x26816a8, rect=(100., 100., 140., 140.))
        self.ability_image = self.frame(0x2757668, rect=(100., 110., 140., 130.))
        self.cooldown_bar = self.frame(0x2754460)
        wrapper = self.label_frame(0x26928d0, '38')
        self.cooldown_label = struct.unpack('<Q', self.read(wrapper + 0x140, 8))[0]
        self.handle = self.link('KaelthasPhoenix', 390)
        self.children(self.row, self.name, self.hero_name, self.talents_container)
        self.children(self.talents_container, self.selection_panel)
        self.children(self.selection_panel, self.unit_status)
        self.children(self.unit_status, self.tree, self.ability)
        self.children(self.ability, self.ability_icon)
        self.children(self.ability_icon, self.ability_image, self.cooldown_bar, self.cooldown_label)
        self.pack(self.tree + 0x170, 'Q', self.handle)
        self.pack(self.ability + 0x148, 'Q', self.handle)
        self.put(self.ability + 0x150, b'\0')
        self.pack(self.ability + 0x158, 'Q', self.ability_icon)
        self.pack(self.ability_icon + 0x130, 'QQQI', self.ability_image,
                  self.cooldown_bar, self.cooldown_label, 0x20c0002)
        self.put(self.ability_icon + 0x14c, b'\0')

    def set_text(self, text):
        obj = struct.unpack('<Q', self.read(self.cooldown_label + 0x1d8, 8))[0]
        shared = struct.unpack('<Q', self.read(obj + 0x28, 8))[0]
        encoded = text.encode('utf8')
        self.pack(shared + 0x18, 'II', len(encoded) << 2, 0)
        self.put(shared + 0x20, encoded)


class BoardCooldownTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(read_row_cooldowns, '独立大招冷却读取器尚未实现')
        self.game = CooldownMemory()

    def read(self, *, slot=3, relation='ally'):
        return read_row_cooldowns(_BoardMemory(self.game), self.game.base,
            self.game.row, slot, relation, (1.2, .9), (1920., 1080.))

    def test_positive_visible_value_has_heroic_link_and_native_image_rect(self):
        record, = self.read()
        self.assertEqual((record.player_slot, record.ability_id, record.ability_name),
                         (3, 'KaelthasPhoenix', '大招'))
        self.assertEqual((record.state, record.remaining_seconds), ('cooldown', 38.))
        self.assertEqual(record.rect, (132., 90., 156., 126.))
        self.assertEqual((record.node, record.icon, record.image, record.label),
                         (self.game.ability, self.game.ability_icon,
                          self.game.ability_image, self.game.cooldown_label))
        self.assertEqual(record.reference, 0x20c0002)

    def test_enemy_and_unknown_relation_never_read_the_row_or_descendants(self):
        for relation in ('enemy', 'unknown'):
            before = len(self.game.calls)
            self.assertEqual(self.read(relation=relation), ())
            self.assertEqual(len(self.game.calls), before)

    def test_row_and_tree_player_bindings_must_match(self):
        with self.assertRaises(_Unknown):
            self.read(slot=4)
        self.game.put(self.game.tree + 0x130, b'\x04')
        with self.assertRaises(_Unknown):
            self.read()

    def test_tree_heroic_property_must_match_the_ability_catalog_link(self):
        self.game.pack(self.game.tree + 0x170, 'Q', self.game.link('KaelthasPyroblast', 391))
        with self.assertRaises(_Unknown):
            self.read()

    def test_unselected_heroic_link_returns_no_record_without_reading_label(self):
        self.game.pack(self.game.tree + 0x170, 'Q', 0)
        self.assertEqual(self.read(), ())

    def test_same_heroic_id_with_different_catalog_index_is_rejected(self):
        self.game.pack(self.game.ability + 0x148, 'Q', self.game.link('KaelthasPhoenix', 391))
        with self.assertRaises(_Unknown):
            self.read()

    def test_arbitrary_ability_elsewhere_in_row_is_not_a_heroic_candidate(self):
        self.game.children(self.game.row, self.game.tree, self.game.ability)
        self.assertEqual(self.read(), ())

    def test_hidden_tree_suppresses_the_ability_on_other_tab_pages(self):
        self.game.put(self.game.tree + 0x48, b'\x01')
        self.assertEqual(self.read(), ())

    def test_icon_image_and_label_pointers_must_be_actual_children_with_expected_types(self):
        for offset, source in ((0x158, self.game.ability),
                               (0x130, self.game.ability_icon),
                               (0x140, self.game.ability_icon)):
            with self.subTest(offset=offset):
                original = self.game.read(source + offset, 8)
                detached = self.game.frame(0x2757668)
                self.game.pack(source + offset, 'Q', detached)
                with self.assertRaises(_Unknown):
                    self.read()
                self.game.put(source + offset, original)

    def test_command_index_must_match_in_both_native_layers(self):
        self.game.put(self.game.ability_icon + 0x14c, b'\x01')
        with self.assertRaises(_Unknown):
            self.read()

    def test_hidden_stale_label_is_unknown_and_is_not_dereferenced(self):
        self.game.put(self.game.cooldown_label + 0x48, b'\x01')
        before = len(self.game.calls)
        record, = self.read()
        self.assertEqual((record.state, record.remaining_seconds), ('unknown', None))
        self.assertEqual(record.reason, 'cooldown_label_hidden')
        self.assertNotIn((self.game.cooldown_label + 0x1d8, 8), self.game.calls[before:])

    def test_zero_or_non_numeric_text_never_becomes_ready(self):
        for text in ('0', '0.0', '-1', 'NaN', 'inf', '1,2', ' 8', '8s', '999999', ''):
            with self.subTest(text=text):
                self.game.set_text(text)
                record, = self.read()
                self.assertEqual((record.state, record.remaining_seconds), ('unknown', None))

    def test_positive_fraction_and_second_ticks_do_not_change_structural_identity(self):
        first, = self.read()
        self.game.set_text('29')
        second, = self.read()
        self.game.set_text('0.1')
        third, = self.read()
        self.game.put(self.game.cooldown_label + 0x48, b'\x01')
        hidden, = self.read()
        self.assertEqual((second.remaining_seconds, third.remaining_seconds), (29., .1))
        self.assertEqual(first.identity, second.identity)
        self.assertEqual(second.identity, third.identity)
        self.assertEqual(third.identity, hidden.identity)

    def test_native_reference_change_changes_identity_and_null_reference_is_unknown(self):
        first, = self.read()
        self.game.pack(self.game.ability_icon + 0x148, 'I', 0x2100002)
        second, = self.read()
        self.assertNotEqual(first.identity, second.identity)
        for invalid in (0, 0xffffffff):
            self.game.pack(self.game.ability_icon + 0x148, 'I', invalid)
            record, = self.read()
            self.assertEqual(record.state, 'unknown')
            self.assertIsNone(record.remaining_seconds)

    def test_tracer_charge_ui_is_not_treated_as_a_normal_cooldown(self):
        handle = self.game.link('TracerPulseBomb', 450)
        self.game.pack(self.game.tree + 0x170, 'Q', handle)
        self.game.pack(self.game.ability + 0x148, 'Q', handle)
        record, = self.read()
        self.assertEqual((record.state, record.reason), ('unknown', 'charge_ability_unsupported'))

    def test_hidden_or_unresolved_image_is_not_a_click_target(self):
        for offset, value in ((0x48, b'\x01'), (0x28, b'\x00')):
            with self.subTest(offset=offset):
                original = self.game.read(self.game.ability_image + offset, 1)
                self.game.put(self.game.ability_image + offset, value)
                self.assertEqual(self.read(), ())
                self.game.put(self.game.ability_image + offset, original)

    def test_binding_changes_during_value_read_fail_closed(self):
        changed = False
        def mutate(address, size):
            nonlocal changed
            if address == self.game.cooldown_label + 0x1d8 and not changed:
                changed = True
                self.game.pack(self.game.ability_icon + 0x148, 'I', 0x2100002)
        self.game.after_read = mutate
        with self.assertRaises(_Unknown):
            self.read()

    def test_duplicate_heroic_frames_are_ambiguous(self):
        duplicate = self.game.frame(0x269b190)
        self.game.children(self.game.unit_status, self.game.tree, self.game.ability, duplicate)
        with self.assertRaises(_Unknown):
            self.read()

    def test_diagnostics_records_actual_numeric_parser_input_without_addresses(self):
        events = []
        recorder = type('Recorder', (), {'snapshot': lambda self: {'enabled': True},
            'record': lambda self, event, **fields: events.append((event, fields))})()
        memory = _BoardMemory(self.game, diagnostics=recorder)
        record, = read_row_cooldowns(memory, self.game.base, self.game.row, 3, 'ally',
                                     (1.2, .9), (1920., 1080.))
        summary, replay = events
        self.assertEqual(summary[0], 'helper.read')
        self.assertEqual((summary[1]['cooldown_state'], summary[1]['cooldown_seconds']),
                         ('cooldown', 38.))
        self.assertEqual(replay[1]['operation'], 'cooldown_display')
        payload = replay[1]['payload']
        self.assertEqual(payload['input']['label_text'], '38')
        self.assertEqual(payload['input']['ability_id'], 'KaelthasPhoenix')
        self.assertEqual(parse_cooldown_display(**payload['input']), payload['expected'])
        self.assertEqual(payload['expected']['remaining_seconds'], record.remaining_seconds)
        self.assertNotIn('reference', payload['input'])
        self.assertNotIn('node', payload['input'])

    def test_disabled_diagnostics_changes_neither_reads_nor_events(self):
        before = len(self.game.calls)
        self.read()
        baseline = self.game.calls[before:]
        events = []
        recorder = type('Recorder', (), {'snapshot': lambda self: {'enabled': False},
            'record': lambda self, *a, **kw: events.append((a, kw))})()
        before = len(self.game.calls)
        memory = _BoardMemory(self.game, diagnostics=recorder)
        read_row_cooldowns(memory, self.game.base, self.game.row, 3, 'ally',
                           (1.2, .9), (1920., 1080.))
        self.assertEqual(self.game.calls[before:], baseline)
        self.assertEqual(events, [])

    def test_non_numeric_label_and_binding_failure_are_safe_replay_inputs(self):
        for text, detach, reason in (('聊天私文', False, 'cooldown_label_not_positive_seconds'),
                                      ('38', True, 'cooldown_binding_untrusted')):
            with self.subTest(reason=reason):
                self.game = CooldownMemory()
                self.game.set_text(text)
                if detach:
                    self.game.pack(self.game.ability + 0x158, 'Q', self.game.tree)
                events = []
                recorder = type('Recorder', (), {'snapshot': lambda self: {'enabled': True},
                    'record': lambda self, event, **fields: events.append((event, fields))})()
                memory = _BoardMemory(self.game, diagnostics=recorder)
                try:
                    read_row_cooldowns(memory, self.game.base, self.game.row, 3, 'ally',
                                       (1.2, .9), (1920., 1080.))
                except _Unknown:
                    pass
                payload = events[-1][1]['payload']
                self.assertIsNone(payload['input']['label_text'])
                self.assertEqual(parse_cooldown_display(**payload['input']), payload['expected'])
                self.assertEqual(payload['expected']['reason'], reason)

    def test_missing_visible_tree_is_unresolved_not_proof_of_no_selection(self):
        events = []
        recorder = type('Recorder', (), {'snapshot': lambda self: {'enabled': True},
            'record': lambda self, event, **fields: events.append((event, fields))})()
        self.game.put(self.game.tree + 0x48, b'\x01')
        memory = _BoardMemory(self.game, diagnostics=recorder)
        self.assertEqual(read_row_cooldowns(memory, self.game.base, self.game.row, 3, 'ally',
                                            (1.2, .9), (1920., 1080.)), ())
        self.assertEqual(events[-1][1]['payload']['expected']['reason'], 'cooldown_binding_untrusted')

    def test_missing_native_container_is_logged_without_claiming_no_selection(self):
        events = []
        recorder = type('Recorder', (), {'snapshot': lambda self: {'enabled': True},
            'record': lambda self, event, **fields: events.append((event, fields))})()
        self.game.children(self.game.row, self.game.tree)
        memory = _BoardMemory(self.game, diagnostics=recorder)
        self.assertEqual(read_row_cooldowns(memory, self.game.base, self.game.row, 3, 'ally',
                                            (1.2, .9), (1920., 1080.)), ())
        report = events[-1][1]['payload']
        self.assertEqual(report['expected']['reason'], 'cooldown_binding_untrusted')
        self.assertIsNone(report['input']['ability_id'])
        self.assertFalse(report['input']['binding_valid'])


class CooldownDisplayParserTests(unittest.TestCase):
    def test_zero_hidden_and_unbound_states_cannot_produce_ready(self):
        for values in (dict(label_visible=True, label_text='0'),
                       dict(label_visible=False, label_text='38'),
                       dict(label_visible=None, label_text=None, reference_valid=False),
                       dict(label_visible=None, label_text=None, selected=False),
                       dict(label_visible=True, label_text='38', binding_valid=False)):
            with self.subTest(values=values):
                result = parse_cooldown_display('KaelthasPhoenix', **values)
                self.assertEqual(result['state'], 'unknown')
                self.assertIsNone(result['remaining_seconds'])

    def test_only_typed_bounded_numeric_observations_are_accepted(self):
        for values in (dict(ability_id='bad id', label_visible=True, label_text='38'),
                       dict(ability_id='KaelthasPhoenix', label_visible=1, label_text='38'),
                       dict(ability_id='KaelthasPhoenix', label_visible=True, label_text='private text'),
                       dict(ability_id='KaelthasPhoenix', label_visible=True, label_text='8'*100),
                       dict(ability_id='KaelthasPhoenix', label_visible=True, label_text='38', selected=1)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                parse_cooldown_display(**values)


if __name__ == '__main__':
    unittest.main()
