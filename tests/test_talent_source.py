"""合成只读内存验证；不访问游戏进程或用户数据。"""
import unittest
import struct
import json
from pathlib import Path
from dataclasses import dataclass, replace

from hots_chat.acquisition.memory import GameProcess

try:
    from hots_chat.acquisition.talent_source import TalentReader
except ModuleNotFoundError:
    TalentReader = None


class TalentReaderInterfaceTests(unittest.TestCase):
    def test_missing_game_returns_unknown_without_a_generation(self):
        self.assertIsNotNone(TalentReader, '只读天赋 reader 尚未实现')
        reader = TalentReader(process_finder=lambda: [])
        result = reader.poll(123)
        self.assertEqual(result.pid, 123)
        self.assertEqual(result.status, 'unknown')
        self.assertIsNone(result.generation)
        self.assertIsNone(result.pending_tier)
        self.assertEqual(result.tiers, ())
        reader.close()


@dataclass(frozen=True)
class Binding:
    pid: int = 123
    slot: int = 3
    session_token: tuple = ('match-1',)
    trusted: bool = True
    source: str = 'native-local-user'


class TalentMemory:
    base = 0x100000000
    pid = 123
    path = 'D:/game/Versions/Base98348/HeroesOfTheStorm_x64.exe'

    def __init__(self):
        self.blocks = {}
        self.calls = []
        self.closed = False
        self.creation = 100
        self.next = 0x24000000
        self.short_at = None
        self.after_read = None
        self.link_reads = 0
        self.put(self.base, b'MZ')
        self.put(self.base+0x35c8cd9,b'\x01')
        self.put(self.base+0x34ef8ad,b'\x01')
        self.pack(self.base+0x3366118,'I',10)
        core = json.loads((Path(__file__).parent / 'fixtures/model_codecs_98348.json')
                          .read_text(encoding='utf-8'))['native_core_fingerprints']['10']
        self.put(self.base + core['rva'], bytes.fromhex(core['core_hex']))
        self.blocks[self.base+0x32c1c90] = bytearray(0x4000)
        self.pack(self.base+0x32c1ed0,'I',0)
        self.pack(self.base+0x32c4cf4,'I',0)
        self.pack(self.base+0x35c8cb0,'II',0x21000000,0)
        self.pack(0x21000000,'Q',0x21001000)
        # With the synthetic all-zero codec table, these literal words decode
        # to 0x20000000. Slot 3 deliberately prevents a slot-1 implementation.
        self.pack(0x21001000+3*8,'II',0x20000000,0)
        self.model = 0x20000000
        self.blocks[self.model]=bytearray(0x200)
        self.unit = self.link('Chromie',33)
        self.pack(self.model+0x50,'Q',self.unit)
        self.manager,self.catalog,self.obj = self.allocate(0x160),self.allocate(0x60),self.allocate(0x540)
        self.pack(self.base+0x3388b18,'Q',self.manager)
        self.pack(self.manager+0x148,'Q',self.catalog)
        table=self.allocate(64*8)
        self.pack(self.catalog+0x38,'I',64)
        self.pack(self.catalog+0x48,'Q',table)
        self.pack(table+33*8,'Q',self.obj)
        self.catalog_table=table
        self.rows=self.allocate(7*12)
        for i,level in enumerate((1,2,5,8,11,14,18),1):self.pack(self.rows+(i-1)*12,'III',i,level,0)
        self.pack(self.obj+0x518,'I',7);self.pack(self.obj+0x528,'Q',self.rows)
        self.entries=self.allocate(21*0x40)
        self.links=[]
        for i in range(21):
            link=self.link('ChromieTalent'+str(i),100+i);self.links.append(link)
            self.pack(self.entries+i*0x40,'QI',link,i//3+1)
        self.pack(self.obj+0x500,'I',21);self.pack(self.obj+0x510,'Q',self.entries)
        self.selection=self.base+0x3677a30+3*0x80
        self.selected=[-1]*32;self.selected[1]=1
        self.set_selected(self.selected)
        self.set_level(2)
        self.pack(self.base+0x3678270,'I',0xffffffff)
        self.pack(self.base+0x3678280,'Q',0)

    def allocate(self,size):
        address=self.next;self.next+=0x1000
        self.blocks[address]=bytearray(size)
        return address

    def put(self,address,payload):
        for base,data in self.blocks.items():
            if base<=address and address+len(payload)<=base+len(data):
                data[address-base:address-base+len(payload)]=payload;return
        self.blocks[address]=bytearray(payload)

    def pack(self,address,fmt,*values):self.put(address,struct.pack('<'+fmt,*values))

    def link(self,name,index):
        handle=self.allocate(0x48);node=self.allocate(0x20+len(name)+8)
        self.pack(handle+0x30,'Q',node);self.pack(handle+0x40,'H',index)
        self.pack(node+0x18,'II',len(name)<<2,0);self.put(node+0x20,name.encode())
        return handle

    def set_selected(self,values):
        self.selected=list(values);self.pack(self.selection,'32i',*values)

    def set_level(self,level):self.pack(self.base+0x3678230+3*4,'I',level)

    def read(self,address,size):
        self.calls.append((address,size))
        if address==self.entries:self.link_reads+=1
        for base,data in self.blocks.items():
            if base<=address and address+size<=base+len(data):
                result=bytes(data[address-base:address-base+size])
                if self.after_read:self.after_read(address,size)
                return result[:-1] if address==self.short_at else result
        return b''

    def close(self):self.closed=True

    def override(self,entry,enabled):
        capacity=2;buckets=self.allocate(16);head=self.allocate(8);node=self.allocate(0x20)
        key=100+entry
        self.pack(self.base+0x3678280,'Q',capacity);self.pack(self.base+0x3678290,'Q',buckets)
        self.pack(self.base+0x2f727c0,'Q',0)
        self.pack(buckets+(key&1)*8,'Q',head);self.pack(head,'Q',node)
        self.pack(node,'II',key,key);self.put(node+0x10,bytes([enabled]))
        return node

    def require(self,entry,required_entry):
        links=self.allocate(8);self.pack(links,'Q',self.links[required_entry])
        self.pack(self.entries+entry*0x40+0x10,'I',1)
        self.pack(self.entries+entry*0x40+0x20,'Q',links)

    def deny(self,entry,required_entry):
        groups=self.allocate(0x18);links=self.allocate(8)
        self.pack(links,'Q',self.links[required_entry]);self.pack(groups,'I',1);self.pack(groups+0x10,'Q',links)
        self.pack(self.entries+entry*0x40+0x28,'I',1)
        self.pack(self.entries+entry*0x40+0x38,'Q',groups)


class TalentReaderTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(TalentReader)
        self.memory=TalentMemory();self.binding=Binding()
        self.processes=[GameProcess(123,self.memory.path,98348)]
        self.opens=0
        self.process_calls=self.base_calls=0
        def factory(pid):self.opens+=1;return self.memory
        def processes():self.process_calls+=1;return self.processes
        def base(pid):self.base_calls+=1;return self.memory.base
        self.reader=TalentReader(binding_provider=lambda game,base,token:self.binding,
            process_finder=processes,game_factory=factory,
            module_base_finder=base,
            creation_token_finder=lambda game:game.creation)

    def tearDown(self):self.reader.close()

    def test_chromie_uses_real_early_tiers_and_trusted_slot_three(self):
        value=self.reader.poll(123)
        self.assertEqual(value.status,'available')
        self.assertEqual(value.hero_id,'Chromie');self.assertEqual(value.level,2)
        self.assertEqual(value.pending_tier,2)
        self.assertEqual([t.required_level for t in value.tiers],[1,2,5,8,11,14,18])
        self.assertEqual(value.tiers[0].selected_value,1)
        self.assertEqual([c.entry_index for c in value.tiers[1].choices],[3,4,5])

    def test_packaged_hero_and_talent_ids_are_accepted_by_memory_link_reader(self):
        from hots_chat.acquisition.talent_source import _Memory, _link
        catalog = json.loads((Path(__file__).resolve().parents[1] /
                              'data/talents_98348_zhCN.json').read_text(encoding='utf-8'))
        identifiers = {hero for hero in catalog['heroes']}
        identifiers.update(talent for hero in catalog['heroes'].values()
                           for talent in hero['talents'])
        handle = self.memory.allocate(0x48)
        node = self.memory.allocate(0x20 + 160)
        self.memory.pack(handle + 0x30, 'Q', node)
        self.memory.pack(handle + 0x40, 'H', 42)
        for identifier in sorted(identifiers):
            with self.subTest(identifier=identifier):
                raw = identifier.encode('ascii')
                self.memory.pack(node + 0x18, 'II', len(raw) << 2, 0)
                self.memory.put(node + 0x20, raw)
                self.assertEqual(_link(_Memory(self.memory), handle), (identifier, 42))

    def test_catalog_links_still_reject_invalid_identifier_bytes(self):
        from hots_chat.acquisition.talent_source import _Memory, _link, _Unknown
        handle = self.memory.allocate(0x48)
        node = self.memory.allocate(0x20 + 161)
        self.memory.pack(handle + 0x30, 'Q', node)
        self.memory.pack(handle + 0x40, 'H', 42)
        for raw in (b'', b'9Talent', b'Bad Name', b'Bad\nID', b'Bad\x00ID',
                    b'<Talent>', b'Bad\xc3\xa9', b'A' * 161):
            with self.subTest(raw=raw):
                self.memory.pack(node + 0x18, 'II', len(raw) << 2, 0)
                self.memory.put(node + 0x20, raw)
                with self.assertRaises(_Unknown):
                    _link(_Memory(self.memory), handle)

    def test_brightwing_catalog_all_tiers_select_once_and_confirm_by_readback(self):
        from hots_chat.loot_native import GameTarget
        from hots_chat.talent import TalentConfig, TalentSession
        from tests.test_talent import Backend
        catalog = json.loads((Path(__file__).resolve().parents[1] /
                              'data/talents_98348_zhCN.json').read_text(encoding='utf-8'))
        talents = sorted(catalog['heroes']['FaerieDragon']['talents'].items(),
                         key=lambda item: (item[1]['tier'], item[1]['column']))
        self.assertEqual(len(talents), 21)
        self.assertIn('BrightwingArcaneFlareHush!', dict(talents))
        self.memory.pack(self.memory.model + 0x50, 'Q', self.memory.link('FaerieDragon', 33))
        for index, (identifier, record) in enumerate(talents):
            self.memory.pack(self.memory.entries + index * 0x40, 'QI',
                             self.memory.link(identifier, 100 + index), record['tier'])
        levels = (1, 4, 7, 10, 13, 16, 20)
        for tier, level in enumerate(levels, 1):
            self.memory.pack(self.memory.rows + (tier - 1) * 12, 'III', tier, level, 0)
        self.memory.set_selected([-1] * 32)
        backend = Backend()
        session = TalentSession(backend, config=TalentConfig(enabled=True))
        target = GameTarget(456, 123)
        for tier, level in enumerate(levels, 1):
            with self.subTest(tier=tier, level=level):
                self.memory.set_level(level)
                snapshot = self.reader.poll(123)
                self.assertEqual((snapshot.hero_id, snapshot.status, snapshot.pending_tier),
                                 ('FaerieDragon', 'available', tier), snapshot.reason)
                self.assertEqual(sum(len(row.choices) for row in snapshot.tiers), 21)
                self.assertEqual(session.poll(snapshot, tier * 10., target).state, 'confirming')
                self.assertEqual(session.poll(snapshot, tier * 10. + 1, target).state, 'confirming')
                self.assertEqual(len(backend.calls), tier)
                selected = list(self.memory.selected)
                selected[tier] = snapshot.tiers[tier - 1].choices[0].entry_index
                self.memory.set_selected(selected)
                confirmed = self.reader.poll(123)
                self.assertEqual(session.poll(confirmed, tier * 10. + 2, target).state, 'confirmed')
                self.assertEqual(len(backend.calls), tier)
        self.assertEqual((confirmed.status, confirmed.pending_tier), ('idle', None))

    def test_fourth_level_does_not_unlock_chromie_fifth_level_tier(self):
        values=self.memory.selected;values[2]=3;self.memory.set_selected(values);self.memory.set_level(4)
        value=self.reader.poll(123)
        self.assertEqual((value.status,value.pending_tier),('idle',None))

    def test_selected_value_must_belong_to_its_catalog_tier(self):
        values=self.memory.selected;values[2]=8;self.memory.set_selected(values)
        self.assertEqual(self.reader.poll(123).status,'unknown')

    def test_unknown_build_does_not_open_game(self):
        self.processes=[GameProcess(123,self.memory.path,99999)]
        self.assertEqual(self.reader.poll(123).status,'unknown');self.assertEqual(self.opens,0)

    def test_missing_or_untrusted_binding_does_not_use_unique_hero(self):
        for binding in (None,replace(self.binding,trusted=False),replace(self.binding,pid=456),replace(self.binding,slot=16)):
            self.binding=binding
            self.assertEqual(self.reader.poll(123).status,'unknown')

    def test_unknown_decoder_and_short_read_fail_closed(self):
        self.memory.pack(self.memory.base+0x3366118,'I',16)
        self.assertEqual(self.reader.poll(123).status,'unknown')
        self.memory.pack(self.memory.base+0x3366118,'I',10)
        self.memory.short_at=self.memory.selection
        self.assertEqual(self.reader.poll(123).status,'unknown')

    def test_verified_new_decoders_read_the_same_hero_and_pending_tier(self):
        from tests.test_model_codecs import set_memory_codec
        for decoder in range(16):
            with self.subTest(decoder=decoder):
                set_memory_codec(self.memory, decoder)
                result = self.reader.poll(123)
                self.assertEqual(result.status, 'available', result.reason)
                self.assertEqual((result.hero_id, result.level, result.pending_tier),
                                 ('Chromie', 2, 2))
                self.assertEqual(self.reader.last_diagnostic['decoder_mode'], decoder)
                self.assertEqual(self.reader.last_diagnostic['supported_decoders'],
                                 ','.join(map(str, range(16))))
                self.assertEqual(self.reader.last_diagnostic['decoder_code_verified'], 1)

    def test_new_codec_code_fingerprint_mismatch_does_not_decode(self):
        from tests.test_model_codecs import set_memory_codec
        from hots_chat.acquisition.model_codecs import DECODER_CODE_FINGERPRINTS
        for decoder in range(16):
            with self.subTest(decoder=decoder):
                set_memory_codec(self.memory, decoder)
                rva, _, _ = DECODER_CODE_FINGERPRINTS[decoder]
                first = self.memory.read(self.memory.base + rva, 1)
                self.memory.put(self.memory.base + rva, bytes([first[0] ^ 1]))
                result = self.reader.poll(123)
                self.assertEqual(result.status, 'unknown')
                self.assertEqual(result.reason, '当前解码分支的游戏代码尚未验证')
                self.assertEqual(self.reader.last_diagnostic['stage'], 'decoder_code')

    def test_decoder_or_used_key_change_during_decode_is_unknown_then_recovers(self):
        from tests.test_model_codecs import set_memory_codec
        for kind in ('decoder', 'key'):
            with self.subTest(kind=kind):
                details = set_memory_codec(self.memory, 6)
                key_address = self.memory.base + 0x32c1c90 + details['key_reads'][0]['index'] * 4
                changed = False

                def mutate(address, size):
                    nonlocal changed
                    if address == key_address and not changed:
                        changed = True
                        if kind == 'decoder':
                            self.memory.pack(self.memory.base + 0x3366118, 'I', 9)
                        else:
                            value = details['key_reads'][0]['value']
                            self.memory.pack(key_address, 'I', value ^ 1)

                self.memory.after_read = mutate
                result = self.reader.poll(123)
                self.assertEqual((result.status, result.reason),
                                 ('unknown', '玩家模型正在变化'))
                self.memory.after_read = None
                set_memory_codec(self.memory, 6)
                self.assertEqual(self.reader.poll(123).status, 'available')

    def test_changed_root_encoding_is_unknown_even_when_root_pointer_is_equal(self):
        from tests.test_model_codecs import set_memory_codec
        set_memory_codec(self.memory, 9)
        changed = False

        def mutate(address, size):
            nonlocal changed
            if address == self.memory.model and size == 1 and not changed:
                changed = True
                for rva in (0x35c8cb0, 0x32c1ed0):
                    current = struct.unpack('<I', self.memory.read(self.memory.base + rva, 4))[0]
                    self.memory.pack(self.memory.base + rva, 'I', (current + 1) & 0xffffffff)

        self.memory.after_read = mutate
        result = self.reader.poll(123)
        self.assertEqual((result.status, result.reason), ('unknown', '玩家模型表正在变化'))
        self.memory.after_read = None

    def test_salt_pointer_or_value_change_is_unknown_and_recovers(self):
        from tests.test_model_codecs import set_memory_codec
        from hots_chat.acquisition.model_codecs import DECODER_SALT_POINTER_RVAS
        for decoder, rva in DECODER_SALT_POINTER_RVAS.items():
            for change in ('pointer', 'value'):
                with self.subTest(decoder=decoder, change=change):
                    set_memory_codec(self.memory, decoder)
                    root = self.memory.base + rva
                    pointer = struct.unpack('<Q', self.memory.read(root, 8))[0]
                    value = struct.unpack('<I', self.memory.read(pointer, 4))[0]
                    alternate = self.memory.allocate(4)
                    self.memory.pack(alternate, 'I', value)
                    changed = False

                    def mutate(address, size):
                        nonlocal changed
                        if address == pointer and size == 4 and not changed:
                            changed = True
                            if change == 'pointer':
                                self.memory.pack(root, 'Q', alternate)
                            else:
                                self.memory.pack(pointer, 'I', value ^ 1)

                    self.memory.after_read = mutate
                    result = self.reader.poll(123)
                    self.assertEqual((result.status, result.reason),
                                     ('unknown', '玩家模型解码参数正在变化'))
                    self.memory.after_read = None
                    set_memory_codec(self.memory, decoder)
                    self.assertEqual(self.reader.poll(123).status, 'available')

    def test_missing_or_short_salt_never_falls_back_to_zero(self):
        from tests.test_model_codecs import set_memory_codec
        from hots_chat.acquisition.model_codecs import DECODER_SALT_POINTER_RVAS
        for decoder, rva in DECODER_SALT_POINTER_RVAS.items():
            for failure in ('pointer', 'short'):
                with self.subTest(decoder=decoder, failure=failure):
                    set_memory_codec(self.memory, decoder)
                    root = self.memory.base + rva
                    pointer = struct.unpack('<Q', self.memory.read(root, 8))[0]
                    if failure == 'pointer':
                        self.memory.pack(root, 'Q', 0)
                    else:
                        self.memory.short_at = pointer
                    result = self.reader.poll(123)
                    self.assertEqual(result.status, 'unknown')
                    self.assertIn(result.reason, ('天赋数据指针不正确', '天赋数据读取不完整'))
                    self.memory.short_at = None

    def test_native_guards_reject_bool_and_out_of_range_selectors(self):
        from hots_chat.acquisition.talent_source import _validate_guards, _validate_model_decoder
        for selector in (True, False, -1, 16, 17, 0x100000000):
            with self.subTest(selector=selector):
                with self.assertRaises(ValueError):
                    _validate_guards(1, 1, selector)
                with self.assertRaises(ValueError):
                    _validate_model_decoder(selector)

    def test_native_api_guard_or_talent_gate_false_is_unknown(self):
        for rva in (0x35c8cd9,0x34ef8ad):
            self.memory.put(self.memory.base+rva,b'\x00')
            self.assertEqual(self.reader.poll(123).status,'unknown')
            self.memory.put(self.memory.base+rva,b'\x01')

    def test_session_change_during_poll_returns_unknown(self):
        n=[0]
        def binding(game,base,token):
            n[0]+=1
            return replace(self.binding,session_token=(n[0],))
        self.reader.binding_provider=binding
        self.assertEqual(self.reader.poll(123).status,'unknown')

    def test_selection_change_during_poll_returns_unknown_then_recovers(self):
        n=[0]
        def mutate(address,size):
            if address==self.memory.selection:
                n[0]+=1
                if n[0]==1:
                    values=self.memory.selected;values[2]=4;self.memory.set_selected(values)
        self.memory.after_read=mutate
        self.assertEqual(self.reader.poll(123).status,'unknown')
        self.memory.after_read=None
        value=self.reader.poll(123)
        self.assertEqual(value.status,'idle');self.assertEqual(value.tiers[1].selected_value,4)

    def test_new_binding_generation_invalidates_metadata_cache(self):
        first=self.reader.poll(123)
        self.binding=replace(self.binding,session_token=('match-2',))
        self.memory.pack(self.memory.rows+12+4,'I',4)
        second=self.reader.poll(123)
        self.assertEqual(second.status,'idle');self.assertNotEqual(first.generation,second.generation)

    def test_metadata_is_cached_but_dynamic_selection_is_rechecked(self):
        self.reader.poll(123);count=self.memory.link_reads
        value=self.reader.poll(123)
        self.assertEqual(value.status,'available');self.assertEqual(self.memory.link_reads,count)
        selected=self.memory.selected;selected[2]=5;self.memory.set_selected(selected)
        self.assertEqual(self.reader.poll(123).tiers[1].selected_value,5)

    def test_aram_override_and_tier_bitmap_can_disable_available_talents(self):
        self.memory.override(3,False)
        self.memory.pack(self.memory.base+0x3678270,'I',0xffffffff & ~(1<<2))
        value=self.reader.poll(123)
        self.assertEqual((value.status,value.pending_tier),('idle',None))
        self.memory.pack(self.memory.base+0x3678270,'I',0xffffffff)
        value=self.reader.poll(123)
        self.assertEqual(value.status,'available')
        self.assertFalse(value.tiers[1].choices[0].eligible)
        self.assertTrue(value.tiers[1].choices[1].eligible)

    def test_requirements_and_deny_groups_use_owned_earlier_talent_links(self):
        self.memory.require(3,2);self.memory.deny(4,1);self.memory.require(5,1)
        value=self.reader.poll(123)
        self.assertEqual(value.status,'available')
        self.assertEqual([c.eligible for c in value.tiers[1].choices],[False,False,True])

    def test_unresolved_catalog_never_calls_a_resolver(self):
        self.memory.pack(self.memory.catalog_table+33*8,'Q',self.memory.obj|1)
        self.assertEqual(self.reader.poll(123).status,'unknown')

    def test_close_releases_owned_read_only_handle(self):
        self.reader.poll(123);self.reader.close();self.reader.close()
        self.assertTrue(self.memory.closed)

    def test_catalog_reconstruction_invalidates_cache_without_new_match_generation(self):
        first=self.reader.poll(123)
        new=self.memory.allocate(0x540)
        self.memory.blocks[new][:]=self.memory.blocks[self.memory.obj]
        self.memory.pack(self.memory.catalog_table+33*8,'Q',new)
        second=self.reader.poll(123)
        self.assertEqual(second.status,'available')
        self.assertEqual(first.generation,second.generation)

    def test_warm_poll_does_not_enumerate_processes_or_modules_again(self):
        self.reader.poll(123);self.reader.poll(123)
        self.assertEqual((self.process_calls,self.base_calls,self.opens),(1,1,1))

    def test_process_generation_change_fails_closed_before_rebinding(self):
        first=self.reader.poll(123);self.memory.creation=101
        self.assertEqual(self.reader.poll(123).status,'unknown')
        third=self.reader.poll(123)
        self.assertEqual(third.status,'available');self.assertNotEqual(first.generation,third.generation)

    def test_override_cycle_is_bounded_and_unknown(self):
        node=self.memory.override(3,False)
        self.memory.pack(node,'Q',node)
        value=self.reader.poll(123)
        self.assertEqual(value.status,'unknown')
        self.assertLess(self.reader.last_read_calls,1000)

    def test_default_provider_uses_native_local_identity_and_counts_all_reads(self):
        # A native slot-3 identity, independent of hero uniqueness or selections.
        root=self.memory.allocate(16)
        for rva,value in ((0x339fec8,(~root)&0xffffffff),(0x32c51f8,0),
                          (0x339fecc,0xffffffff),(0x32c403c,0),(0x32c3850,0)):
            self.memory.pack(self.memory.base+rva,'I',value)
        self.memory.put(root+12,b'\x03')
        reader=TalentReader(process_finder=lambda:self.processes,
            game_factory=lambda pid:self.memory,module_base_finder=lambda pid:self.memory.base,
            creation_token_finder=lambda game:game.creation)
        try:
            self.memory.calls.clear()
            value=reader.poll(123)
            self.assertEqual(value.status,'available')
            self.assertEqual(value.hero_id,'Chromie')
            self.assertEqual(reader.last_read_calls,len(self.memory.calls))
        finally:reader.close()
