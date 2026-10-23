"""Native identity tests use sparse synthetic memory, never a live game."""
import struct
import unittest

try:
    from hots_chat.acquisition.talent_identity import resolve_local_identity
except ModuleNotFoundError:
    resolve_local_identity = None


class IdentityMemory:
    base = 0x140000000
    token = (123, 456, base, 98348)

    def __init__(self):
        self.blocks = {}
        self.calls = []
        self.after_read = None
        self.short_at = None
        self.root = 0x210001000
        self.owner = 0x220001000
        self.table = 0x230001000
        self.put(self.base, b'MZ')
        self.put(self.base + 0x35c8cd9, b'\x01')
        self.put(self.base + 0x34ef8ad, b'\x01')
        self.set_root(self.root)
        self.pack(self.base + 0x32c3850, 'I', 0x87654321)
        self.header(7)
        self.set_table(self.owner, self.table)

    def put(self, address, value):
        for offset, byte in enumerate(value):
            self.blocks[address + offset] = byte

    def pack(self, address, fmt, *values):
        self.put(address, struct.pack('<' + fmt, *values))

    def set_root(self, pointer):
        low_sub, high_sub = 0xf1234567, 0x76543210
        low = ((~pointer & 0xffffffff) + low_sub) & 0xffffffff
        high = ((~(pointer >> 32) & 0xffffffff) + high_sub) & 0xffffffff
        for rva, value in ((0x339fec8, low), (0x32c51f8, low_sub),
                           (0x339fecc, high), (0x32c403c, high_sub)):
            self.pack(self.base + rva, 'I', value)

    def header(self, slot=7, flags=0):
        value = bytearray(16)
        struct.pack_into('<I', value, 0, flags ^ 0x87654321)
        value[12] = slot
        self.put(self.root, value)

    def set_table(self, owner, table):
        for rva, value in ((0x35c8cb0, owner & 0xffffffff),
                           (0x35c8cb4, owner >> 32),
                           (0x32c1ed0, 0), (0x32c4cf4, 0)):
            self.pack(self.base + rva, 'I', value)
        self.pack(owner, 'Q', table)

    def read(self, address, size):
        self.calls.append((address, size))
        value = bytes(self.blocks.get(address + i, 0) for i in range(size))
        if self.after_read:
            self.after_read(address, size)
        return value[:-1] if address == self.short_at else value


class TalentIdentityTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(resolve_local_identity, 'native identity resolver is missing')
        self.game = IdentityMemory()

    def resolve(self, token=None):
        return resolve_local_identity(self.game, self.game.base, token or self.game.token)

    def test_native_slot_is_authoritative_without_hero_or_candidate_enumeration(self):
        binding = self.resolve()
        self.assertEqual((binding.pid, binding.slot), (123, 7))
        self.assertTrue(binding.trusted)
        self.assertIn('actor', binding.source)
        self.assertEqual(binding, self.resolve())
        self.assertLess(sum(n for _, n in self.game.calls), 1024)

    def test_mirror_heroes_cannot_change_slot_from_native_owner(self):
        self.game.put(self.game.table + 1 * 8, b'Chromie!')
        self.game.put(self.game.table + 7 * 8, b'Chromie!')
        self.assertEqual(self.resolve().slot, 7)
        self.assertNotIn((self.game.table + 1 * 8, 8), self.game.calls)
        self.assertNotIn((self.game.table + 7 * 8, 8), self.game.calls)

    def test_lobby_native_guard_rejects_stale_slot(self):
        self.game.put(self.game.base + 0x35c8cd9, b'\0')
        self.assertIsNone(self.resolve())

    def test_talent_gate_rejects_leaving_scene(self):
        self.game.put(self.game.base + 0x34ef8ad, b'\0')
        self.assertIsNone(self.resolve())

    def test_out_of_game_slot_sentinel_is_rejected(self):
        self.game.header(16)
        self.assertIsNone(self.resolve())

    def test_unverified_modes_including_native_actor_reject_bits_are_unknown(self):
        for flags in (0x10, 0x20, 0x30, 1, 0x80000000):
            with self.subTest(flags=flags):
                self.game.header(7, flags)
                self.assertIsNone(self.resolve())

    def test_unsupported_build_is_rejected_without_reads(self):
        self.assertIsNone(self.resolve((123, 456, self.game.base, 98349)))
        self.assertEqual(self.game.calls, [])

    def test_changed_process_creation_token_invalidates_previous_binding(self):
        first = self.resolve()
        second = self.resolve((123, 789, self.game.base, 98348))
        self.assertNotEqual(first.session_token, second.session_token)

    def test_new_scene_player_table_context_invalidates_previous_binding(self):
        first = self.resolve()
        self.game.set_table(self.game.owner + 0x1000, self.game.table + 0x1000)
        self.assertNotEqual(first.session_token, self.resolve().session_token)

    def test_hero_or_model_recreation_does_not_change_match_token(self):
        first = self.resolve()
        self.game.pack(self.game.table + 7 * 8, 'Q', 0x333330000)
        self.assertEqual(first.session_token, self.resolve().session_token)

    def test_identity_changing_during_read_is_unknown(self):
        changed = []
        def transition(address, size):
            if address == self.game.root and not changed:
                changed.append(True)
                self.game.header(8)
        self.game.after_read = transition
        self.assertIsNone(self.resolve())

    def test_short_header_read_is_unknown(self):
        self.game.short_at = self.game.root
        self.assertIsNone(self.resolve())

    def test_invalid_decoded_root_is_rejected_before_dereference(self):
        self.game.set_root(8)
        self.assertIsNone(self.resolve())
        self.assertNotIn((8, 16), self.game.calls)

    def test_missing_process_identity_is_unknown(self):
        for token in ((), (123, 0, self.game.base, 98348), (123, 456, 99, 98348)):
            with self.subTest(token=token):
                self.assertIsNone(resolve_local_identity(self.game, self.game.base, token))


if __name__ == '__main__':
    unittest.main()
