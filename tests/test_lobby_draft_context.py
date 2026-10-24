"""征召命名谓词、观察边界和受限元数据fence；不连接游戏进程。"""
from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path
import struct
import unittest

from hots_chat.acquisition.lobby_draft import (
    LobbyDraftContext, VERIFIED_CODE_FINGERPRINTS,
)


class DraftMemory:
    base = 0x140000000
    path = r'D:\game\Heroes of the Storm\Versions\Base98348\HeroesOfTheStorm_x64.exe'

    def __init__(self):
        self.blocks, self.calls, self.next = {}, [], 0x100000
        self.after_read = None
        self.put(self.base, b'MZ')
        self.code = b'synthetic-verified-code'
        self.put(self.base + 0x1500, self.code)
        self.code_fingerprints = ((0x1500, len(self.code), hashlib.sha256(self.code).hexdigest()),)
        for offset, value in ((0x28e9540, 0x1db7030), (0x28e9548, 0x1dba2e0),
                              (0x28e9558, 0x1e40050), (0x28e9568, 0x1e40a70),
                              (0x28ede50, 0x1e0fb70)):
            self.put(self.base + offset, struct.pack('<Q', self.base + value))
        for offset, value in ((0x28e9610, b'CChatLobbyChat\0'), (0x26faad8, b'BattleChatLobby\0'),
                              (0x28f0600, b'UI/BattleChat/LobbyDraftName\0')):
            self.put(self.base + offset, value)
        self.put(self.base + 0x26ae6c0, struct.pack('<3I', 0x7374616e, 0x64726674, 0x746f7572))
        self.put(self.base + 0x26ae6f0, struct.pack('<3I', 0x6c6f6279, 0x6865726f, 0x64726674))
        self.api = self.allocate(0x1010)
        self.table = self.allocate(8 * 40)
        self.name = self.allocate(32)
        self.context = self.allocate(16)
        self.backend = self.allocate(0xe00)
        self.session = self.allocate(0x90)
        self.manager = self.allocate(0x68)
        self.lobby = self.allocate(0x1130)
        self.array = self.allocate(48)
        self.put(self.base + 0x3b7c6c0, struct.pack('<Q', self.api))
        self.put(self.api + 0xff0, struct.pack('<Q', self.table))
        self.put(self.api + 0x1000, struct.pack('<Q', 8))
        self.put(self.name, b'BattlenetAPI_IsInLobby\0')
        self.registration = self.table + 4 * 40  # FNV-1a | high bit == ce5291bc.
        self.put(self.registration, struct.pack('<IIQQQQ', 0xce5291bc, 0, 0, self.name,
                                               self.base + 0x23b8470, self.context))
        self.put(self.context, struct.pack('<QQ', self.base + 0x29e9038, self.backend))
        self.put(self.backend + 0xdf0, struct.pack('<Q', self.session))
        self.put(self.session + 0x88, struct.pack('<I', 2))
        self.put(self.base + 0x3b7c6b0, struct.pack('<Q', self.manager))
        self.put(self.manager + 0x60, struct.pack('<Q', self.lobby))
        self.put(self.lobby, struct.pack('<Q', self.base + 0x28ede18))
        self.attributes()

    def allocate(self, size):
        pointer = self.next
        self.next += ((size + 0xfff) // 0x1000) * 0x1000
        self.blocks[pointer] = bytearray(size)
        return pointer

    def put(self, pointer, data):
        for start, block in self.blocks.items():
            if start <= pointer and pointer + len(data) <= start + len(block):
                block[pointer - start:pointer - start + len(data)] = data
                return
        self.blocks[pointer] = bytearray(data)

    def attributes(self, mode=0x64726674, category=0x64726674):
        self.put(self.array, struct.pack('<4I', 999, 4015, mode, 0)
                 + struct.pack('<4I', 999, 4010, category, 0)
                 + struct.pack('<4I', 12, 4015, 123, 0))
        self.put(self.lobby + 0x1118, struct.pack('<I', 3))
        self.put(self.lobby + 0x1128, struct.pack('<Q', self.array))

    def read(self, pointer, size):
        self.calls.append((pointer, size))
        result = b''
        for start, block in self.blocks.items():
            if start <= pointer and pointer + size <= start + len(block):
                result = bytes(block[pointer - start:pointer - start + size])
                break
        if self.after_read:self.after_read(pointer, size)
        return result


class LobbyDraftContextTests(unittest.TestCase):
    def setUp(self):
        self.memory = DraftMemory()
        self.clock = [1700000000.]
        self.context = self.make()

    def make(self, **options):
        return LobbyDraftContext(self.memory, self.memory.base,
            wall_clock=lambda:self.clock[0], code_fingerprints=self.memory.code_fingerprints, **options)

    def test_draft_uses_verified_naming_attributes_and_immutable_after_boundary(self):
        snapshot = self.context.observe()
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.identity,
            (self.memory.lobby, self.memory.backend, self.memory.session, 0x64726674, 0x64726674))
        self.assertEqual(snapshot.after_filetime, 116444736000000000 + 1700000000 * 10000000)
        self.assertTrue(self.context.verify(snapshot))
        self.assertIs(self.context.current, snapshot)
        self.assertIs(self.context.snapshot, snapshot)
        with self.assertRaises(FrozenInstanceError):snapshot.after_filetime = 0
        self.clock[0] += 10
        self.assertIs(self.context.observe(), snapshot)
        self.assertTrue(self.context.code_verified)

    def test_start_boundary_never_recedes_before_capture_start(self):
        upper = 116444736000000000 + 1700000010 * 10000000
        context = self.make(capture_after_filetime=upper)
        self.assertEqual(context.observe().after_filetime, upper)

    def test_all_recognized_gate_states_and_categories_are_explicit(self):
        for state in range(7):
            self.memory.put(self.memory.session + 0x88, struct.pack('<I', state))
            for category in (0x7374616e, 0x64726674):
                self.memory.attributes(category=category)
                self.assertIsNotNone(self.make().observe())
        for state in (7, 0xffffffff):
            self.memory.put(self.memory.session + 0x88, struct.pack('<I', state))
            context = self.make()
            self.assertIsNone(context.observe())
            self.assertEqual(context.last_reason, 'draft_not_in_lobby')

    def test_standard_all_draft_unknown_and_namespace_mismatch_are_rejected(self):
        for mode, category, reason in ((0x6c6f6279, 0x64726674, 'draft_not_selected'),
                (0x6865726f, 0x64726674, 'draft_not_selected'),
                (0x64726674, 0x746f7572, 'draft_all_channel_rejected'),
                (2, 0x64726674, 'draft_mode_unknown'),
                (0x64726674, 2, 'draft_category_unknown')):
            self.memory.attributes(mode, category)
            context = self.make()
            self.assertIsNone(context.observe())
            self.assertEqual(context.last_reason, reason)
        self.memory.attributes()
        self.memory.put(self.memory.array, struct.pack('<I', 998))
        self.assertIsNone(self.make().observe())

    def test_bad_or_missing_registry_and_callback_cannot_enable_capture(self):
        for offset, data, reason in ((0, struct.pack('<I', 0), 'draft_registry_missing'),
                (24, struct.pack('<Q', self.memory.base + 0x23b8478), 'draft_callback_mismatch'),
                (32, struct.pack('<Q', 0), 'draft_callback_mismatch')):
            original = self.memory.read(self.memory.registration, 40)
            self.memory.put(self.memory.registration + offset, data)
            context = self.make()
            self.assertIsNone(context.observe())
            self.assertEqual(context.last_reason, reason)
            self.memory.put(self.memory.registration, original)

    def test_wrong_class_name_binding_and_image_fail_closed(self):
        for pointer, data, reason in ((self.memory.base, b'NO', 'draft_image_mismatch'),
                (self.memory.base + 0x28e9558, struct.pack('<Q', 0), 'draft_binding_mismatch'),
                (self.memory.base + 0x28e9610, b'NotTheLobby!!!\0', 'draft_name_mismatch'),
                (self.memory.lobby, struct.pack('<Q', 0), 'draft_lobby_class_mismatch')):
            original = self.memory.read(pointer, len(data))
            self.memory.put(pointer, data)
            context = self.make()
            self.assertIsNone(context.observe())
            self.assertEqual(context.last_reason, reason)
            self.memory.put(pointer, original)

    def test_invalid_count_pointer_duplicates_and_read_budget_are_bounded(self):
        self.memory.put(self.memory.lobby + 0x1118, struct.pack('<I', 100000))
        self.assertIsNone(self.context.observe())
        self.assertEqual(self.context.last_reason, 'draft_attributes_invalid')
        self.memory.attributes()
        self.memory.put(self.memory.array + 32, struct.pack('<4I', 999, 4015, 0x64726674, 0))
        self.assertIsNone(self.context.observe())
        self.assertEqual(self.context.last_reason, 'draft_attribute_ambiguous')
        self.assertTrue(all(size <= 4096 for _, size in self.memory.calls))

    def test_bad_code_retries_later_but_successful_hashes_are_cached(self):
        self.memory.put(self.memory.base + 0x1500, b'X' + self.memory.code[1:])
        self.assertIsNone(self.context.observe())
        self.assertEqual(self.context.last_reason, 'draft_code_mismatch')
        self.memory.put(self.memory.base + 0x1500, self.memory.code)
        self.assertIsNotNone(self.context.observe())
        before = self.memory.calls.count((self.memory.base + 0x1500, len(self.memory.code)))
        self.context.observe()
        self.assertEqual(self.memory.calls.count((self.memory.base + 0x1500, len(self.memory.code))), before)

    def test_snapshot_is_fenced_across_room_change_and_reentry(self):
        first = self.context.observe()
        self.memory.put(self.memory.backend + 0xdf0, struct.pack('<Q', 0))
        self.assertFalse(self.context.verify(first))
        self.assertIs(self.context.current, first)  # verify does not mutate observe state.
        self.assertIsNone(self.context.observe())
        self.clock[0] += 5
        self.memory.put(self.memory.backend + 0xdf0, struct.pack('<Q', self.memory.session))
        second = self.context.observe()
        self.assertEqual(first.identity, second.identity)
        self.assertGreater(second.after_filetime, first.after_filetime)
        self.assertGreater(second.revision, first.revision)
        self.assertFalse(self.context.verify(first))

    def test_attribute_reallocation_preserves_logical_epoch_and_after_time(self):
        first = self.context.observe()
        payload = self.memory.read(self.memory.array, 48)
        self.memory.array = self.memory.allocate(48)
        self.memory.put(self.memory.array, payload)
        self.memory.put(self.memory.lobby + 0x1128, struct.pack('<Q', self.memory.array))
        self.clock[0] += 100
        self.assertTrue(self.context.verify(first))
        self.assertIs(self.context.observe(), first)

    def test_torn_attributes_are_discarded_without_partial_context(self):
        changes = []
        def mutate(pointer, size):
            if pointer == self.memory.array and not changes:
                changes.append(True)
                self.memory.attributes(category=0x7374616e)
        self.memory.after_read = mutate
        self.assertIsNone(self.context.observe())
        self.assertEqual(self.context.last_reason, 'draft_context_changed_during_read')
        self.assertIsNone(self.context.current)

    def test_reader_supplied_to_verify_owns_its_read_accounting(self):
        snapshot = self.context.observe()
        class Reader:
            def __init__(self, memory):self.memory, self.bytes, self.calls = memory, 0, 0
            def read(self, address, count):
                self.bytes += count; self.calls += 1
                return self.memory.read(address, count)
        reader = Reader(self.memory)
        before = self.context.diagnostic_snapshot()
        self.assertTrue(self.context.verify(snapshot, memory=reader))
        after = self.context.diagnostic_snapshot()
        self.assertGreater(reader.bytes, 0)
        self.assertEqual((before['read_bytes'], before['read_calls']),
                         (after['read_bytes'], after['read_calls']))
        self.assertGreater(after['native_draft_context_checks'], before['native_draft_context_checks'])
        after['read_bytes'] = -1
        self.assertGreaterEqual(self.context.diagnostic_snapshot()['read_bytes'], 0)

    def test_loaded_profile_cannot_be_replaced_with_protected_disk_code(self):
        path = Path(DraftMemory.path)
        if not path.is_file():self.skipTest('本机未安装该静态游戏PE；其余测试均为合成内存。')
        if hashlib.sha256(path.read_bytes()).hexdigest() != 'efdf5a2f13dfe4f66a63f525f4d4ae3c953064232c60da74b4e6540aa534dca9':
            self.skipTest('本机磁盘PE不是已取证的受保护样本。')
        try:import pefile
        except ImportError:self.skipTest('未安装可选静态PE验证依赖。')
        pe = pefile.PE(str(path), fast_load=True)
        self.assertEqual(len(VERIFIED_CODE_FINGERPRINTS), 14)
        self.assertLess(sum(size for _, size, _ in VERIFIED_CODE_FINGERPRINTS), 2048)
        self.assertTrue(all(hashlib.sha256(pe.get_data(rva, size)).hexdigest() != expected
                            for rva, size, expected in VERIFIED_CODE_FINGERPRINTS))
        # This checks the known protected-image distinction. Runtime code reads
        # must independently validate all loaded hashes; this is not a substitute.

    def test_operation_budget_rejects_large_registry_and_attribute_work(self):
        code = b'A' * 4096
        self.memory.put(self.memory.base + 0x10000, code)
        self.memory.put(self.memory.base + 0x12000, code)
        specs = ((0x10000, 4096, hashlib.sha256(code).hexdigest()),
                 (0x12000, 4096, hashlib.sha256(code).hexdigest()))
        table = self.memory.allocate(256 * 40)
        self.memory.put(self.memory.api + 0xff0, struct.pack('<Q', table))
        self.memory.put(self.memory.api + 0x1000, struct.pack('<Q', 256))
        for step in range(127):
            self.memory.put(table + ((188 + step) & 255) * 40, struct.pack('<I', 1))
        registration = self.memory.read(self.memory.registration, 40)
        self.memory.put(table + ((188 + 127) & 255) * 40, registration)
        array = self.memory.allocate(512 * 16)
        self.memory.put(array, self.memory.read(self.memory.array, 48))
        self.memory.put(self.memory.lobby + 0x1118, struct.pack('<I', 512))
        self.memory.put(self.memory.lobby + 0x1128, struct.pack('<Q', array))
        context = LobbyDraftContext(self.memory, self.memory.base, code_fingerprints=specs)
        self.assertIsNone(context.observe())
        self.assertEqual(context.last_reason, 'draft_read_budget')

    def test_invalid_clock_and_unsupported_build_do_not_publish_context(self):
        for value in (float('nan'), float('inf'), -10**30):
            context = self.make()
            context.wall_clock = lambda:value
            self.assertIsNone(context.observe())
            self.assertEqual(context.last_reason, 'draft_clock_invalid')
        self.memory.path = self.memory.path.replace('98348', '98349')
        context = self.make()
        self.assertIsNone(context.observe())
        self.assertEqual(context.last_reason, 'draft_build_unsupported')


if __name__ == '__main__':unittest.main()
