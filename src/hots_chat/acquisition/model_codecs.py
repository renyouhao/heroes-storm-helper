"""Build 98348 player-slot codecs, recovered from the native compute paths.

All 16 native dispatch entries are supported. Only arithmetic runs here;
read_key receives an index into the 4096-word table. Five selectors additionally
require a captured u32 salt; missing salt must never be replaced with zero.
"""

SUPPORTED_DECODERS = tuple(range(16))
DECODER_SALT_POINTER_RVAS = {
    2: 0x33887a8, 5: 0x33887b8, 8: 0x3388ac0, 11: 0x3388ad0, 14: 0x3388ae0,
}
DECODER_CODE_FINGERPRINTS = {
    0: (0x31a594, 98, '17ddc8ca0f5b235513288d582f1809333b61732968565528677a4f7b7bbf1b9e'),
    1: (0x31a644, 97, 'f89d096e9222408d2e165cf2f4f8d88ce57d3711388279128bc7fb90ce29fb64'),
    2: (0x31a6f4, 106, '521ae988b563d7769a3f36eb72a015cf02c4d06259f0d590b4f54daebc57c90f'),
    3: (0x31a7a4, 97, '114ea5bcde9bb38d91007d058006263f18e949898d1bd35105158655f0e8b7c3'),
    4: (0x31a854, 100, '7a5df10ef9ce520fb17aece0af55c1132a4ae7a7dc34438e51a2a5e188d8ac25'),
    5: (0x31a904, 103, '08673cada25a0110a5a06eadf5066980bf22cab255387bbe29c6e9f1d782f6c1'),
    6: (0x31a9b4, 0x66, '4b3a0115d66599cb4ff8370e6ad821254a95a7c3b5d5a408a7518ff90fda35d2'),
    7: (0x31aa64, 108, '8b969225430450072fe2af626b602e236da15af09f1a121e390c715b8cada245'),
    8: (0x31ab12, 103, '05d7a3a3ba10e73faf764a11ddec2076946843e59d9c71c4debd529419696f07'),
    9: (0x31abc4, 0x66, 'c3e559cf208ea567930067ad86673d6d333a224aafa9ee62a27ef6114a0c0750'),
    10: (0x31ac74, 95, '3a293872179893259df90c34f65c9ba4306d2fa31d84f89b872612a4b9ebeec5'),
    11: (0x31ad24, 103, '914e534fea5f4fc76bd85e83ab409b1d50ae39da262bb8921f196c088f626f96'),
    12: (0x31add4, 103, '93691448b2556790acae81eaf2a7439231b6041944b9a8b53ca34f3d5f0ae6cc'),
    13: (0x31ae84, 89, 'cca3b5150af12df1a45ae883e4cba7735b657f07bda289db07eba1e79e7dc155'),
    14: (0x31af24, 107, '607c1d113f9c31b9ea29f4fea869d64f0681bb47301cb1d8ff3b10a4f445b3d4'),
    15: (0x31afd4, 102, 'e6def2be0bb93f7726f2369d15c74d4fd2948e9cf83b868c5173e8e04c973ef6'),
}
_U32 = 0xffffffff
_INDEX = 0xfff
_SWAP = 0x55555555


def _word(value):
    if type(value) is not int or not 0 <= value <= _U32:
        raise ValueError('invalid codec word')
    return value


def decode_pointer(selector, encoded_low, encoded_high, read_key, *, salt=None):
    """Decode one encoded slot; every intermediate follows x86 u32 arithmetic."""
    if type(selector) is not int or selector not in SUPPORTED_DECODERS:
        raise ValueError('unsupported model decoder')
    x, y = _word(encoded_low), _word(encoded_high)
    if selector in DECODER_SALT_POINTER_RVAS:
        salt = _word(salt)
    elif salt is not None:
        raise ValueError('unexpected codec salt')

    # First lookup: NOT/subtraction order is significant, even before masking.
    if selector == 0:
        first = (~x + (x >> 12)) & _INDEX
    elif selector in (1, 2, 3, 5, 8, 11, 14):
        first = (x + (x >> 12)) & _INDEX
    elif selector in (4, 6, 12):
        first = (~x - (x >> 12)) & _INDEX
    elif selector == 7:
        first = (x - (x >> 12)) & _INDEX
    elif selector == 9:
        first = ~(x - (x >> 12)) & _INDEX
    elif selector == 10:
        first = ((x >> 12) ^ ((x - 0x73010d88) & _U32)) & _INDEX
    elif selector == 13:
        first = ((x >> 12) ^ x) & _INDEX
    else:  # selector 15
        first = ((x >> 12) ^ ((x + 0x2516b167) & _U32)) & _INDEX
    first_key = _word(read_key(first))

    if selector in (0, 12):
        t = ~(y - first_key) & _U32
    elif selector in (1, 3, 5, 7, 14):
        t = (y + first_key) & _U32
    elif selector in (2, 4, 8, 10, 13):
        t = first_key ^ y
    else:
        t = (y - first_key) & _U32
    if salt is not None:
        t ^= salt

    if selector in (0, 11):
        second = ~(t + (t >> 12)) & _INDEX
    elif selector in (1, 3, 4, 5):
        second = (~t + (t >> 12)) & _INDEX
    elif selector in (2, 6, 8, 9, 12):
        second = ~(t - (t >> 12)) & _INDEX
    elif selector in (7, 14):
        second = (t - (t >> 12)) & _INDEX
    elif selector in (10, 13):
        second = ((t >> 12) + t) & _INDEX
    else:  # selector 15
        second = (~t - (t >> 12)) & _INDEX
    second_key = _word(read_key(second))
    if selector in (0, 4, 10, 11, 13):
        c = second_key ^ x
    elif selector in (1, 3, 6, 14):
        c = (second_key + x) & _U32
    elif selector in (2, 7, 9, 15):
        c = ~(x - second_key) & _U32
    else:  # selectors 5, 8, 12
        c = (x - second_key) & _U32
    mixed = (t ^ c) & _SWAP
    return ((mixed ^ t) << 32) | (mixed ^ c)
