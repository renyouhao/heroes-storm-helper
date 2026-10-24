"""Independent pinned native-oracle vectors; no live game or emulator required.

The fixture's expected pointers and ordered reads were produced by Unicorn
executing captured instructions, never by the Python implementation under test.
"""
import hashlib
import json
from pathlib import Path
import struct
import unittest

from hots_chat.acquisition.model_codecs import DECODER_CODE_FINGERPRINTS, SUPPORTED_DECODERS, decode_pointer


FIXTURE = Path(__file__).parent / 'fixtures/model_codecs_98348.json'
_MASK = 0xffffffff
_KEY_RVA = 0x32c1c90
_ROOT_RVAS = (0x35c8cb0, 0x32c1ed0, 0x35c8cb4, 0x32c4cf4)
_SALT_RVAS = {2: 0x33887a8, 5: 0x33887b8, 8: 0x3388ac0, 11: 0x3388ad0, 14: 0x3388ae0}
_SYNTHETIC_SALT = 0x6d2b79f5


def load_vectors():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def set_memory_codec(memory, selector, slot=3):
    """Encode its known model using a nonzero synthetic table and inverse mixing.

    The root's subtraction words overlap key-table entries 144 and 3097. Keep
    their original values, then use the final memory values to build the slot.
    This helper supplies integration inputs, never native-vector expectations.
    """
    if type(selector) is not int or not 0 <= selector <= 15:
        raise ValueError('unsupported synthetic selector')

    def word(rva):
        return struct.unpack('<I', memory.read(memory.base + rva, 4))[0]

    original_roots = [word(rva) for rva in _ROOT_RVAS]
    keys = [((index * 0x9e3779b9 + 0xa5a5a5a5) & _MASK) | 1 for index in range(4096)]
    memory.put(memory.base + _KEY_RVA, struct.pack('<4096I', *keys))
    for rva, value in zip(_ROOT_RVAS, original_roots):
        memory.pack(memory.base + rva, 'I', value)

    def key(index):
        return word(_KEY_RVA + index * 4)

    # The final alternating-bit swap is its own inverse.
    low, high = memory.model & _MASK, (memory.model >> 32) & _MASK
    swapped = (low ^ high) & 0x55555555
    c, t = low ^ swapped, high ^ swapped
    if selector in (0, 11):
        second = ~(t + (t >> 12)) & 0xfff
    elif selector in (1, 3, 4, 5):
        second = (~t + (t >> 12)) & 0xfff
    elif selector in (2, 6, 8, 9, 12):
        second = ~(t - (t >> 12)) & 0xfff
    elif selector in (7, 14):
        second = (t - (t >> 12)) & 0xfff
    elif selector in (10, 13):
        second = ((t >> 12) + t) & 0xfff
    else:  # 15: not precedes subtraction in the captured instructions.
        second = (~t - (t >> 12)) & 0xfff

    if selector in (0, 4, 10, 11, 13):
        x = c ^ key(second)
    elif selector in (1, 3, 6, 14):
        x = (c - key(second)) & _MASK
    elif selector in (2, 7, 9, 15):
        x = (~c + key(second)) & _MASK
    else:  # 5, 8, 12
        x = (c + key(second)) & _MASK

    if selector == 0:
        first = (~x + (x >> 12)) & 0xfff
    elif selector in (1, 2, 3, 5, 8, 11, 14):
        first = (x + (x >> 12)) & 0xfff
    elif selector in (4, 6, 12):
        first = (~x - (x >> 12)) & 0xfff
    elif selector == 7:
        first = (x - (x >> 12)) & 0xfff
    elif selector == 9:
        first = ~(x - (x >> 12)) & 0xfff
    elif selector == 10:
        first = ((x >> 12) ^ ((x - 0x73010d88) & _MASK)) & 0xfff
    elif selector == 13:
        first = ((x >> 12) ^ x) & 0xfff
    else:  # 15 includes a real LEA constant, not just a generic XOR index.
        first = ((x >> 12) ^ ((x + 0x2516b167) & _MASK)) & 0xfff

    salt = _SYNTHETIC_SALT if selector in _SALT_RVAS else None
    if selector in (0, 12):
        y = (~t + key(first)) & _MASK
    elif selector in (1, 3, 7):
        y = (t - key(first)) & _MASK
    elif selector in (2, 8):
        y = t ^ key(first) ^ salt
    elif selector in (4, 10, 13):
        y = t ^ key(first)
    elif selector in (5, 14):
        y = ((t ^ salt) - key(first)) & _MASK
    elif selector in (6, 9, 15):
        y = (t + key(first)) & _MASK
    else:  # 11
        y = ((t ^ salt) + key(first)) & _MASK

    root = (((original_roots[2] - original_roots[3]) & _MASK) << 32) | ((original_roots[0] - original_roots[1]) & _MASK)
    table = struct.unpack('<Q', memory.read(root, 8))[0]
    memory.pack(table + slot * 8, 'II', x, y)
    memory.pack(memory.base + 0x3366118, 'I', selector)
    core = load_vectors()['native_core_fingerprints'][str(selector)]
    memory.put(memory.base + core['rva'], bytes.fromhex(core['core_hex']))
    if salt is not None:
        salt_address = memory.allocate(4)
        memory.pack(salt_address, 'I', salt)
        memory.pack(memory.base + _SALT_RVAS[selector], 'Q', salt_address)
    return {'selector': selector, 'encoded_low': x, 'encoded_high': y, 'expected_model': memory.model, 'salt': salt,
            'key_reads': [{'index': first, 'value': key(first)}, {'index': second, 'value': key(second)}]}


class ModelCodecTests(unittest.TestCase):
    def test_supported_selectors_and_pinned_fixture_provenance(self):
        self.assertEqual(SUPPORTED_DECODERS, tuple(range(16)))
        fixture = load_vectors()
        self.assertEqual(fixture['build'], 98348)
        self.assertEqual(fixture['exe_sha256'], 'efdf5a2f13dfe4f66a63f525f4d4ae3c953064232c60da74b4e6540aa534dca9')
        self.assertIn('Unicorn', fixture['provenance'])
        profiles = fixture['native_core_fingerprints']
        self.assertEqual(set(profiles), {str(selector) for selector in range(16)})
        expected_functions = {int(selector): row['function_sha256'] for selector, row in profiles.items()}
        for selector in range(16):
            self.assertGreaterEqual(sum(row['selector'] == selector for row in fixture['vectors']), 5)
        for row in fixture['vectors']:
            self.assertEqual(row['function_sha256'], expected_functions[row['selector']])
        self.assertEqual(set(DECODER_CODE_FINGERPRINTS), set(range(16)))
        for selector, row in profiles.items():
            core = bytes.fromhex(row['core_hex'])
            self.assertEqual(len(core), row['length'])
            self.assertEqual(core[-1], 0xc3)
            self.assertEqual(hashlib.sha256(core).hexdigest(), row['sha256'])
            self.assertEqual(DECODER_CODE_FINGERPRINTS[int(selector)], (row['rva'], row['length'], row['sha256']))

    def test_nonzero_native_vectors_match_pointer_and_ordered_key_reads(self):
        for vector in load_vectors()['vectors']:
            with self.subTest(selector=vector['selector'], low=hex(vector['encoded_low']), high=hex(vector['encoded_high'])):
                keys = {row['index']: row['value'] for row in vector['key_reads']}
                reads = []

                def read_key(index):
                    self.assertIs(type(index), int)
                    self.assertGreaterEqual(index, 0)
                    self.assertLess(index, 4096)
                    value = keys[index]  # Unexpected reads cannot fall back to zero.
                    self.assertNotEqual(value, 0)
                    reads.append({'index': index, 'value': value})
                    return value

                kwargs = {'salt': vector['salt']} if vector.get('salt') is not None else {}
                pointer = decode_pointer(vector['selector'], vector['encoded_low'], vector['encoded_high'], read_key, **kwargs)
                self.assertEqual(pointer, vector['pointer'])
                self.assertEqual(reads, vector['key_reads'])

    def test_first_index_keeps_not_before_subtraction_distinct_from_not_after(self):
        vectors = load_vectors()['vectors']
        expected = {(6, 0xffffffff): 1, (9, 0xffffffff): 4095, (6, 0): 4095, (9, 0): 4095}
        for (selector, low), first in expected.items():
            row = next(row for row in vectors if row['selector'] == selector and row['encoded_low'] == low)
            with self.subTest(selector=selector, low=hex(low)):
                self.assertEqual(row['key_reads'][0]['index'], first)
                observed = []
                keys = {item['index']: item['value'] for item in row['key_reads']}
                decode_pointer(selector, low, row['encoded_high'], lambda index: (observed.append(index), keys[index])[1])
                self.assertEqual(observed[0], first)

    def test_invalid_selector_and_encoded_words_fail_before_any_key_read(self):
        invalid = (True, False, -1, 0x100000000, 1.5, '6', None)
        cases = [(selector, 0, 0) for selector in (16, 17, *invalid)]
        cases += [(selector, value, 0) for selector in SUPPORTED_DECODERS for value in invalid]
        cases += [(selector, 0, value) for selector in SUPPORTED_DECODERS for value in invalid]
        for selector, low, high in cases:
            with self.subTest(selector=selector, low=low, high=high):
                reads = []
                with self.assertRaises(ValueError):
                    kwargs = {'salt': _SYNTHETIC_SALT} if type(selector) is int and selector in _SALT_RVAS else {}
                    decode_pointer(selector, low, high, lambda index: (reads.append(index), 0)[1], **kwargs)
                self.assertEqual(reads, [])

    def test_invalid_first_or_second_key_is_rejected_without_further_reads(self):
        for selector in SUPPORTED_DECODERS:
            for invalid in (True, False, -1, 0x100000000, 0.5, 'key', None):
                for failing_read in (1, 2):
                    with self.subTest(selector=selector, key=invalid, failing_read=failing_read):
                        reads = []

                        def read_key(index):
                            reads.append(index)
                            return invalid if len(reads) == failing_read else 0xa5a5a5a5

                        with self.assertRaises(ValueError):
                            kwargs = {'salt': _SYNTHETIC_SALT} if selector in _SALT_RVAS else {}
                            decode_pointer(selector, 0xffffffff, 0, read_key, **kwargs)
                        self.assertEqual(len(reads), failing_read)

    def test_salted_selectors_require_explicit_valid_u32_before_key_reads(self):
        for selector in _SALT_RVAS:
            for salt in (None, True, False, -1, 0x100000000, 'salt', 0.5):
                with self.subTest(selector=selector, salt=salt):
                    reads = []
                    with self.assertRaises(ValueError):
                        decode_pointer(selector, 0, 0, lambda index: (reads.append(index), 1)[1], salt=salt)
                    self.assertEqual(reads, [])

    def test_non_salted_selectors_reject_an_extra_salt_instead_of_ignoring_it(self):
        for selector in set(range(16)) - set(_SALT_RVAS):
            with self.subTest(selector=selector):
                reads = []
                with self.assertRaises(ValueError):
                    decode_pointer(selector, 0, 0, lambda index: (reads.append(index), 1)[1], salt=0)
                self.assertEqual(reads, [])

    def test_native_salt_vectors_include_zero_ones_and_a_nonzero_pattern(self):
        vectors = load_vectors()['vectors']
        for selector in _SALT_RVAS:
            with self.subTest(selector=selector):
                salts = {row['salt'] for row in vectors if row['selector'] == selector}
                self.assertIn(0, salts)
                self.assertIn(0xffffffff, salts)
                self.assertTrue(any(value not in (0, 0xffffffff) for value in salts))

    def test_synthetic_helper_preserves_root_and_decodes_real_model(self):
        from tests.test_talent_source import TalentMemory
        from hots_chat.acquisition.talent_source import _Memory, _model
        for selector in SUPPORTED_DECODERS:
            with self.subTest(selector=selector):
                memory = TalentMemory()
                roots_before = [memory.read(memory.base + rva, 4) for rva in _ROOT_RVAS]
                setup = set_memory_codec(memory, selector)
                self.assertEqual([memory.read(memory.base + rva, 4) for rva in _ROOT_RVAS], roots_before)
                reads = []

                def read_key(index):
                    value = struct.unpack('<I', memory.read(memory.base + _KEY_RVA + index * 4, 4))[0]
                    reads.append({'index': index, 'value': value})
                    return value

                kwargs = {'salt': setup['salt']} if setup['salt'] is not None else {}
                pointer = decode_pointer(selector, setup['encoded_low'], setup['encoded_high'], read_key, **kwargs)
                self.assertEqual(pointer, memory.model)
                self.assertEqual(reads, setup['key_reads'])
                self.assertEqual(_model(_Memory(memory), memory.base, 3), memory.model)
                words = struct.unpack('<4096I', memory.read(memory.base + _KEY_RVA, 16384))
                preserved = {(0x32c1ed0 - _KEY_RVA) // 4, (0x32c4cf4 - _KEY_RVA) // 4}
                self.assertTrue(all(value != 0 for index, value in enumerate(words) if index not in preserved))

    def test_helper_uses_final_preserved_key_when_read_collides_with_root_subtractor(self):
        from tests.test_talent_source import TalentMemory
        from hots_chat.acquisition.talent_source import _Memory, _model
        # These known mixed outputs make the second lookup hit index 144,
        # exactly where the low root-subtraction word overlays the key table.
        targets = {selector: 0x0000008000000010 if selector in (7, 10, 13, 14) else 0x00000a2a00000545
                   for selector in range(16)}
        for selector, target in targets.items():
            with self.subTest(selector=selector):
                memory = TalentMemory()
                memory.blocks[target] = bytearray(memory.blocks[memory.model])
                memory.model = target
                memory.pack(memory.base + 0x32c1ed0, 'I', 0x11223344)
                memory.pack(memory.base + 0x35c8cb0, 'I', 0x32223344)
                memory.pack(memory.base + 0x32c4cf4, 'I', 0x55667788)
                memory.pack(memory.base + 0x35c8cb4, 'I', 0x55667788)
                roots_before = [memory.read(memory.base + rva, 4) for rva in _ROOT_RVAS]
                setup = set_memory_codec(memory, selector)
                self.assertEqual(setup['key_reads'][1], {'index': 144, 'value': 0x11223344})
                self.assertEqual([memory.read(memory.base + rva, 4) for rva in _ROOT_RVAS], roots_before)
                self.assertEqual(_model(_Memory(memory), memory.base, 3), target)


if __name__ == '__main__':
    unittest.main()
