"""真实控件字段的只读契约；合成内存测试不操作游戏或原生输入。"""
import importlib
import struct
import unittest


class Memory:
    def __init__(self):
        self.data = {}
        self.reads = []
        self.mutation = None

    def put(self, address, data):
        self.data.update((address + i, value) for i, value in enumerate(data))

    def pack(self, address, format, *values):
        self.put(address, struct.pack('<' + format, *values))

    def read(self, address, size):
        self.reads.append((address, size))
        values = []
        for i in range(size):
            if address + i not in self.data:
                break
            values.append(self.data[address + i])
        result = bytes(values)
        if self.mutation is not None and self.mutation[0] == address:
            self.put(*self.mutation)
            self.mutation = None
        return result


class BoardInputTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module('hots_chat.acquisition.board_input')
        except ModuleNotFoundError:
            self.fail('尚未提供已核实控件字段的只读输入状态 reader')
        self.reader = self.module.BoardInputReader()
        self.memory = Memory()
        self.base = 0x140000000
        self.root, self.chat, self.edit, self.menu = (0x200000000 + i * 0x1000 for i in range(4))
        self.memory.pack(self.base + 0x338b2e8, 'Q', self.root)
        for at, vtable, flags in ((self.root, 0x2653738, 0x7b),
                                  (self.chat, 0x2659e30, 0x7a),
                                  (self.edit, 0x27deee0, 0x53),
                                  (self.menu, 0x265e1b0, 0x7a)):
            self.memory.pack(at, 'Q', self.base + vtable)
            self.memory.put(at + 0x48, bytes([flags]))
        self.memory.pack(self.root + 0x310, 'Q', self.chat)
        self.memory.pack(self.root + 0x350, 'Q', self.menu)
        self.memory.pack(self.chat + 0x170, 'Q', self.edit)
        self.memory.pack(self.chat + 0x130, 'I', 1)
        self.text('')

    def text(self, value, *, heap=False):
        raw = value.encode('utf8')
        if heap:
            pointer = 0x200010000
            self.memory.pack(self.edit + 0x260, 'IIQ', (len(raw) << 2) | 1, 0x23, pointer)
            self.memory.put(pointer, raw)
        else:
            self.assertLessEqual(len(raw), 7)
            self.memory.pack(self.edit + 0x260, 'II8s', (len(raw) << 2) | 1, 0x21, raw)

    def open_team(self):
        self.memory.put(self.chat + 0x48, b'\x7b')
        self.memory.put(self.edit + 0x48, b'\x73')

    def inspect(self):
        return self.reader.inspect(self.memory, self.base, self.root)

    def test_closed_chat_and_menu_are_false_without_reading_draft_buffer(self):
        value = self.inspect()
        self.assertEqual((value.chat_active, value.menu_active, value.channel, value.text),
                         (False, False, None, None))
        self.assertFalse(any(self.edit + 0x260 <= address < self.edit + 0x270
                             for address, size in self.memory.reads))
        self.assertEqual(self.reader.read(self.memory, self.base, self.root), (False, False))

    def test_open_team_empty_editor_is_exact_raw_empty_string(self):
        self.open_team()
        value = self.inspect()
        self.assertEqual((value.chat_active, value.menu_active, value.channel, value.text),
                         (True, False, 'team', ''))

    def test_heap_unicode_draft_is_returned_exactly_without_cleaning(self):
        self.open_team()
        text = '播报校验：甲乙 丙'
        self.text(text, heap=True)
        self.assertEqual(self.inspect().text, text)

    def test_inline_draft_preserves_spaces_and_punctuation(self):
        self.open_team()
        self.text(' ab!? ')
        self.assertEqual(self.inspect().text, ' ab!? ')

    def test_all_whisper_battlenet_observers_are_never_claimed_as_team(self):
        self.open_team()
        for audience in (0, 2, 3, 4, 0xffffffff):
            with self.subTest(audience=audience):
                self.memory.pack(self.chat + 0x130, 'I', audience)
                self.memory.reads.clear()
                value = self.inspect()
                self.assertIs(value.chat_active, True)
                self.assertIsNone(value.channel)
                self.assertIsNone(value.text)
                self.assertFalse(any(address == self.edit + 0x260 for address, size in self.memory.reads))

    def test_menu_effective_visibility_is_reported_even_when_chat_is_closed(self):
        self.memory.put(self.menu + 0x48, b'\x7b')
        self.assertEqual(self.reader.read(self.memory, self.base, self.root), (False, True))

    def test_inconsistent_chat_and_editor_visibility_is_unknown(self):
        self.memory.put(self.chat + 0x48, b'\x7b')
        self.assertIsNone(self.inspect().chat_active)

    def test_root_member_or_vtable_changes_are_rejected(self):
        for address, data in ((self.base + 0x338b2e8, struct.pack('<Q', self.root + 8)),
                              (self.chat, struct.pack('<Q', self.base + 0x2659e38)),
                              (self.edit, struct.pack('<Q', 0)),
                              (self.menu, struct.pack('<Q', self.base + 0x265e1b8))):
            with self.subTest(address=address):
                original = self.memory.read(address, len(data))
                self.memory.put(address, data)
                self.assertEqual(self.reader.read(self.memory, self.base, self.root), (None, None))
                self.memory.put(address, original)

    def test_missing_or_uninitialized_header_is_unknown_and_never_empty(self):
        self.open_team()
        for data in (bytes(16), struct.pack('<IIQ', 1, 0x23, 0)):
            with self.subTest(data=data):
                self.memory.put(self.edit + 0x260, data)
                self.assertIsNone(self.inspect().text)
        self.memory.data.pop(self.edit + 0x26f)
        self.assertIsNone(self.inspect().text)

    def test_oversized_invalid_utf8_and_impossible_inline_text_are_unknown(self):
        self.open_team()
        for length, flags, payload in ((4097, 0x23, b'a'), (1, 0x23, b'\xff'),
                                       (8, 0x21, b'12345678')):
            with self.subTest(length=length, flags=flags):
                self.memory.pack(self.edit + 0x260, 'IIQ', (length << 2) | 1, flags, 0x200010000)
                self.memory.put(0x200010000, payload)
                self.assertIsNone(self.inspect().text)

    def test_header_or_text_changed_during_sample_is_not_accepted(self):
        self.open_team()
        self.text('abc', heap=True)
        self.memory.mutation = (0x200010000, b'abd')
        self.assertIsNone(self.inspect().text)

    def test_changing_draft_preserves_verified_chat_menu_and_team(self):
        self.open_team()
        self.text('abc', heap=True)
        self.memory.mutation = (0x200010000, b'abd')
        state = self.inspect()
        self.assertEqual((state.chat_active, state.menu_active, state.channel, state.text),
                         (True, False, 'team', None))
        self.assertEqual(self.inspect().text, 'abd')

    def test_chat_visibility_transition_preserves_stable_menu_state(self):
        self.memory.mutation = (self.chat + 0x48, b'\x7b')
        state = self.inspect()
        self.assertEqual((state.chat_active, state.menu_active, state.channel, state.text),
                         (None, False, None, None))

    def test_menu_visibility_transition_preserves_verified_chat_and_team(self):
        self.open_team()
        self.memory.mutation = (self.menu + 0x48, b'\x7b')
        state = self.inspect()
        self.assertEqual((state.chat_active, state.menu_active, state.channel, state.text),
                         (True, None, 'team', ''))

    def test_object_binding_change_during_capture_invalidates_all_fields(self):
        self.open_team()
        self.memory.mutation = (self.base + 0x338b2e8, struct.pack('<Q', self.root + 8))
        state = self.inspect()
        self.assertEqual((state.chat_active, state.menu_active, state.channel, state.text),
                         (None, None, None, None))

    def test_missing_visibility_does_not_default_to_closed(self):
        self.memory.data.pop(self.menu + 0x48)
        self.assertEqual(self.reader.read(self.memory, self.base, self.root), (None, None))

    def test_snapshot_reuses_sender_dataclass_and_supplied_match_identity(self):
        from hots_chat.board_sender import ChatInputState
        self.open_team()
        state = self.reader.snapshot(self.memory, self.base, self.root,
                                     pid=11, hwnd=22, match_token='native-match-token')
        self.assertIsInstance(state, ChatInputState)
        self.assertEqual((state.pid, state.hwnd, state.match_token),
                         (11, 22, 'native-match-token'))
        self.assertEqual((state.chat_active, state.channel, state.text), (True, 'team', ''))


if __name__ == '__main__':
    unittest.main()
