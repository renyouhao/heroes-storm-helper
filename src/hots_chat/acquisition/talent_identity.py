"""Build-98348 local gameplay identity from the engine's actor-owner predicate.

IsActorLocalPlayer's vtable names it at RVA 0x1c63c70 and evaluates it at
0x1c63d10. The latter compares an actor's owner with state+0x0c; it does not
inspect hero names or the currently selected unit. Its state-mode guard is at
0x2694d0. Only the observed normal flags value zero is accepted here.

Trial -> lobby -> mirror trial verified guards clearing, slot 16 in the lobby,
and a new player-table context. The token is an allocation identity, not a
server match UUID: a completely unobserved transition with identical address
reuse cannot be distinguished. Callers must retain attempt suppression and
observe unknown transitions; they must not infer a fresh match from a model or
hero change. Replay/observer modes have not been live-tested.
"""
from dataclasses import dataclass
import struct


@dataclass(frozen=True)
class TalentBinding:
    pid: int
    slot: int
    session_token: tuple
    trusted: bool = True
    source: str = 'native-actor-local-player-98348'


def resolve_local_identity(game, image_base, process_creation_token):
    """Return a consistent native binding, or None for any unsupported state.

    process_creation_token is (pid, creation_filetime, image_base, build), supplied
    by the process-owning reader. No game function or input operation is used.
    """
    token = process_creation_token
    if (not isinstance(token, tuple) or len(token) != 4
            or any(type(value) is not int for value in token)
            or token[0] <= 0 or token[1] <= 0
            or token[2] != image_base or token[3] != 98348):
        return None
    try:
        memory = _IdentityMemory(game)
        if memory.read(image_base, 2) != b'MZ':
            return None
        first = _capture(memory, image_base)
        if _capture(memory, image_base) != first:
            return None
        root, slot, owner, table, _ = first
        return TalentBinding(token[0], slot,
                             token + ('actor-local-player', root, owner, table))
    except (OSError, ValueError, TypeError, RuntimeError, struct.error):
        return None


class _IdentityMemory:
    def __init__(self, game):
        self.game = game
        self.bytes = 0

    def read(self, address, size):
        if (type(address) is not int or not 0x10000 <= address
                or address + size >= 0x7fffffff0000
                or not 0 < size <= 16 or self.bytes + size > 512):
            raise ValueError('identity read outside bounds')
        value = self.game.read(address, size)
        self.bytes += len(value)
        if len(value) != size:
            raise ValueError('incomplete identity read')
        return value

    def u32(self, address):
        return struct.unpack('<I', self.read(address, 4))[0]

    def u64(self, address):
        return struct.unpack('<Q', self.read(address, 8))[0]


def _capture(memory, base):
    guards = (memory.read(base + 0x35c8cd9, 1),
              memory.read(base + 0x34ef8ad, 1))
    if guards != (b'\x01', b'\x01'):
        raise ValueError('gameplay talent context is inactive')
    encoded = tuple(memory.u32(base + rva) for rva in
                    (0x339fec8, 0x32c51f8, 0x339fecc, 0x32c403c, 0x32c3850))
    low = ~(encoded[0] - encoded[1]) & 0xffffffff
    high = ~(encoded[2] - encoded[3]) & 0xffffffff
    root = high << 32 | low
    if root % 8:
        raise ValueError('unaligned local-player state')
    header = memory.read(root, 16)
    flags = struct.unpack_from('<I', header)[0] ^ encoded[4]
    slot = header[12]
    if flags != 0 or not 0 <= slot < 16:
        raise ValueError('unverified gameplay mode or local-player slot')
    parts = tuple(memory.u32(base + rva) for rva in
                  (0x35c8cb0, 0x32c1ed0, 0x35c8cb4, 0x32c4cf4))
    owner = ((parts[2] - parts[3]) & 0xffffffff) << 32
    owner |= (parts[0] - parts[1]) & 0xffffffff
    if owner % 8:
        raise ValueError('unaligned player-table context')
    table = memory.u64(owner)
    if not 0x10000 <= table < 0x7fffffff0000 or table % 8:
        raise ValueError('invalid player table')
    # Include the original encodings and complete header in the consistency
    # comparison; an obfuscation-key or mode change mid-read is also unknown.
    return root, slot, owner, table, (encoded, header, parts, guards)
