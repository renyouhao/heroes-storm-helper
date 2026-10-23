"""98348 面板 reader 的合成边界测试；不访问真实进程或窗口。"""
import unittest
from types import SimpleNamespace
from dataclasses import replace

from tests.test_talent_source import TalentMemory, Binding
from hots_chat.acquisition.memory import GameProcess

try:
    from hots_chat.acquisition.board_source import BoardReader
except ModuleNotFoundError:
    BoardReader = None


class BoardMemory(TalentMemory):
    def __init__(self):
        super().__init__()
        self.root = self.frame(0x2653738)
        self.container = self.frame(0x27d96d8)
        self.panel = self.frame(0x2689b88)
        self.row = self.frame(0x2689920)
        self.tree = self.frame(0x26916f0)
        self.icon = self.frame(0x2680e88)
        self.image = self.frame(0x2757668, rect=(100., 100., 140., 140.))
        self.name = self.label_frame(0x26928d0, '测试玩家')
        self.hero_name = self.label_frame(0x26913b8, '克罗米')
        self.children(self.root, self.container)
        self.children(self.container, self.panel)
        self.children(self.panel, self.row)
        self.children(self.row, self.name, self.hero_name, self.tree)
        self.children(self.tree, self.icon)
        self.children(self.icon, self.image)
        self.pack(self.base+0x338b2e8, 'Q', self.root)
        self.pack(self.base+0x2f868f4, '2f', 1.2, .9)
        self.pack(self.panel+0x188, 'I', 1)
        array=self.allocate(8);self.pack(array,'Q',self.row)
        self.pack(self.panel+0x198, 'Q', array)
        self.put(self.row+0x158, b'\x03')
        self.put(self.tree+0x130, b'\x03')
        self.pack(self.row+0x150, 'Q', self.tree)
        array=self.allocate(8);self.pack(array,'Q',self.icon)
        self.pack(self.tree+0x140, 'I', 1)
        self.pack(self.tree+0x150, 'Q', array)
        self.pack(self.icon+0x148, 'QIi', self.unit, 0, 1)
        self.pack(self.icon+0x160, 'Q', self.image)

    def frame(self, vtable, rect=(0., 0., 1200., 1600.)):
        at=self.allocate(0x240)
        self.pack(at,'Q',self.base+vtable)
        self.put(at+0x48,b'\x21');self.put(at+0x28,b'\x0f')
        self.pack(at+0xa8,'4f',*rect)
        return at

    def children(self, parent, *children):
        self.pack(parent+0x40,'Q',children[0]+0x18 if children else 0)
        for i, child in enumerate(children):
            self.pack(child+0x20,'Q',children[i+1]+0x18 if i+1<len(children) else 1)

    def label_frame(self, vtable, text):
        at=self.frame(vtable);self.put(at+0x130,b'\x03')
        label=self.frame(0x270f248);obj=self.allocate(0x40)
        raw=text.encode('utf8');shared=self.allocate(0x40+len(raw))
        self.pack(at+0x140,'Q',label);self.pack(label+0x1d8,'Q',obj)
        self.pack(obj+0x28,'Q',shared)
        self.pack(shared+0x18,'II',len(raw)<<2,0);self.put(shared+0x20,raw)
        self.children(at,label)
        return at

    def add_stat(self, link='HeroDamage', text='133,027', raw=133027.75):
        group=self.frame(0x2693b88);cell=self.frame(0x26777c0,rect=(200.,100.,240.,180.))
        name=self.label_frame(0x26928d0,text);label=self.read(name+0x140,8)
        self.put(group+0x130,b'\x03');self.put(cell+0x149,b'\x03')
        self.pack(cell+0x140,'f',raw);self.put(cell+0x150,label)
        encoded=link.encode('ascii');shared=self.allocate(0x40+len(encoded))
        self.pack(shared+0x18,'II',len(encoded)<<2,0);self.put(shared+0x20,encoded)
        self.pack(cell+0x130,'Q',shared)
        array=self.allocate(8);self.pack(array,'Q',cell)
        self.pack(group+0x140,'I',1);self.pack(group+0x150,'Q',array)
        import struct
        self.children(cell,struct.unpack('<Q',label)[0]);self.children(group,cell)
        self.children(self.row,self.name,self.hero_name,self.tree,group)
        self.put(self.tree+0x48,b'\x01')
        return cell


class BoardReaderTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(BoardReader, '有界面板 reader 尚未实现')
        self.memory=BoardMemory();self.binding=Binding()
        self.reader=BoardReader(
            text_provider=lambda hero,talent: {'talent_name':'测试天赋','effect_summary':'测试效果'},
            viewport_converter=lambda hwnd,rect,size: tuple(round(x) for x in rect),
            binding_provider=lambda *_:self.binding,
            process_finder=lambda:[GameProcess(123,self.memory.path,98348)],
            game_factory=lambda _:self.memory,
            module_base_finder=lambda _:self.memory.base,
            creation_token_finder=lambda _:self.memory.creation)

    def tearDown(self):
        if hasattr(self,'reader'):self.reader.close()

    def poll(self):return self.reader.poll(123,77,10.)

    def test_native_row_schema_selected_talent_and_actual_image_rectangle(self):
        result=self.poll()
        self.assertEqual(result.status,'available',result.reason)
        self.assertEqual(len(result.hits),1)
        hit=result.hits[0]
        self.assertEqual((hit.player_slot,hit.team_relation,hit.tier_level),(3,'ally',1))
        self.assertEqual((hit.talent_id,hit.player_name,hit.hero_name),
                         ('ChromieTalent1','测试玩家','克罗米'))
        self.assertEqual(hit.rect,(120,90,168,126))
        self.assertTrue(hit.mapping_trusted)
        self.assertIsNone(result.chat_active)
        self.assertIsNone(result.menu_active)

    def test_closed_row_suppresses_stale_rect_while_panel_wrapper_stays_visible(self):
        self.assertEqual(len(self.poll().hits),1)
        self.memory.put(self.memory.row+0x48,b'\x01')
        result=self.poll()
        self.assertEqual(result.hits,())
        self.assertEqual(result.status,'idle')

    def test_image_not_visible_or_rectangle_not_resolved_suppresses(self):
        for offset,value in ((0x48,b'\x01'),(0x28,b'\x0a')):
            with self.subTest(offset=offset):
                original=self.memory.read(self.memory.image+offset,1)
                self.memory.put(self.memory.image+offset,value)
                self.assertEqual(self.poll().hits,())
                self.memory.put(self.memory.image+offset,original)

    def test_talent_flat_index_must_match_current_slot_selection(self):
        self.memory.pack(self.memory.icon+0x154,'i',2)
        self.assertEqual(self.poll().status,'unknown')

    def test_mirror_hero_name_never_substitutes_for_row_slot_binding(self):
        self.memory.put(self.memory.tree+0x130,b'\x04')
        self.assertEqual(self.poll().status,'unknown')

    def test_cached_parent_membership_is_rechecked(self):
        self.assertEqual(len(self.poll().hits),1)
        self.memory.children(self.memory.container)
        self.assertEqual(self.poll().hits,())

    def test_unknown_tag_and_over_budget_root_do_not_become_false_idle(self):
        self.memory.pack(self.memory.root+0x40,'Q',self.memory.container|2)
        self.assertEqual(self.poll().status,'unknown')

    def test_changed_snapshot_or_process_generation_is_unknown(self):
        reads=[0]
        def mutate(address,size):
            if address==self.memory.icon+0x154:
                reads[0]+=1
                if reads[0]==1:self.memory.pack(address,'i',2)
        self.memory.after_read=mutate
        self.assertEqual(self.poll().status,'unknown')

    def test_missing_coordinate_or_localized_text_providers_never_trusts_hit(self):
        self.reader.viewport_converter=None
        self.assertEqual(self.poll().hits,())
        self.reader.viewport_converter=lambda hwnd,rect,size:tuple(round(x) for x in rect)
        self.reader.text_provider=lambda *_:None
        self.assertEqual(self.poll().hits,())

    def test_statistics_uses_native_link_and_formatted_integer_not_float_rounding(self):
        self.memory.add_stat()
        result=self.poll()
        self.assertEqual(result.status,'available',result.reason)
        self.assertEqual(len(result.hits),1)
        hit=result.hits[0]
        self.assertEqual((hit.metric_id,hit.value),('hero_damage',133027))
        self.assertEqual(hit.rect,(120,180,216,216))

    def test_unknown_and_inapplicable_statistics_do_not_become_zero(self):
        for link,text in (('NewUnknownScore','8'),('Healing','-'),('Deaths','1.5')):
            with self.subTest(link=link,text=text):
                self.memory.add_stat(link,text)
                self.assertEqual(self.poll().hits,())

    def test_statistics_cell_slot_must_match_authoritative_row_slot(self):
        cell=self.memory.add_stat()
        self.memory.put(cell+0x149,b'\x06')
        self.assertEqual(self.poll().status,'unknown')

    def test_inspect_input_uses_same_match_token_without_reading_tab_rows(self):
        board=self.poll();self.memory.calls.clear()
        state=self.reader.inspect_input(123,77)
        self.assertEqual(state.match_token,board.match_token)
        self.assertIsNone(state.chat_active)
        self.assertIsNone(state.menu_active)
        self.assertIsNone(state.channel)
        self.assertIsNone(state.text)
        self.assertNotIn(self.memory.row,[at for at,size in self.memory.calls])

    def test_input_provider_object_is_preserved_and_invalid_flags_stay_unknown(self):
        self.reader.input_state_provider=lambda *_:SimpleNamespace(
            chat_active=True,menu_active=False,channel='team',text='测试输入')
        state=self.reader.inspect_input(123,77)
        self.assertEqual((state.chat_active,state.menu_active,state.channel,state.text),
                         (True,False,'team','测试输入'))
        self.reader.input_state_provider=lambda *_:{'chat_active':1,'menu_active':False}
        state=self.reader.inspect_input(123,77)
        self.assertIsNone(state.chat_active)
        self.assertIsNone(state.menu_active)

    def test_changing_draft_preserves_match_token_and_verified_input_fields(self):
        board = self.poll()
        fields = iter(((True, False, 'team', 'prefix'),
                       (True, False, 'team', 'prefix-complete')))
        self.reader.input_state_provider = lambda *_: next(fields)
        state = self.reader.inspect_input(123, 77)
        self.assertEqual(state.match_token, board.match_token)
        self.assertEqual((state.chat_active, state.menu_active, state.channel, state.text),
                         (True, False, 'team', None))
        self.assertFalse(self.memory.closed)

    def test_input_transition_only_invalidates_fields_that_changed(self):
        board = self.poll()
        cases = (
            ((False, False, None, None), (True, False, None, None),
             (None, False, None, None)),
            ((True, False, 'team', 'same'), (True, True, 'team', 'same'),
             (True, None, 'team', 'same')),
            ((True, False, 'team', 'same'), (True, False, None, 'same'),
             (True, False, None, 'same')),
        )
        for first, second, expected in cases:
            with self.subTest(first=first, second=second):
                fields = iter((first, second))
                self.reader.input_state_provider = lambda *_: next(fields)
                state = self.reader.inspect_input(123, 77)
                self.assertEqual(state.match_token, board.match_token)
                self.assertEqual((state.chat_active, state.menu_active, state.channel, state.text),
                                 expected)

    def inspect_input_with_changing_fields(self, change, *, change_on=1):
        fields = iter(((True, False, 'team', 'prefix'),
                       (True, False, 'team', 'prefix-complete')))
        calls = 0

        def provider(*_):
            nonlocal calls
            value = next(fields)
            calls += 1
            if calls == change_on:
                change()
            return value

        self.reader.input_state_provider = provider
        return self.reader.inspect_input(123, 77)

    def assert_unknown_input_identity(self, state):
        self.assertEqual(state.match_token, '')
        self.assertEqual((state.chat_active, state.menu_active, state.channel, state.text),
                         (None, None, None, None))

    def test_draft_changes_do_not_mask_changed_root_identity(self):
        replacement = self.memory.frame(0x2653738)
        state = self.inspect_input_with_changing_fields(
            lambda: self.memory.pack(self.memory.base + 0x338b2e8, 'Q', replacement))
        self.assert_unknown_input_identity(state)

    def test_draft_changes_do_not_mask_changed_game_guards(self):
        state = self.inspect_input_with_changing_fields(
            lambda: self.memory.put(self.memory.base + 0x35c8cd9, b'\x02'))
        self.assert_unknown_input_identity(state)

    def test_root_change_during_final_input_capture_invalidates_identity(self):
        replacement = self.memory.frame(0x2653738)
        state = self.inspect_input_with_changing_fields(
            lambda: self.memory.pack(self.memory.base + 0x338b2e8, 'Q', replacement),
            change_on=2)
        self.assert_unknown_input_identity(state)

    def test_guards_change_during_final_input_capture_invalidates_identity(self):
        state = self.inspect_input_with_changing_fields(
            lambda: self.memory.put(self.memory.base + 0x35c8cd9, b'\x02'), change_on=2)
        self.assert_unknown_input_identity(state)

    def test_draft_changes_do_not_mask_changed_match_binding(self):
        def change_match():
            self.binding = replace(self.binding, session_token=('match-2',))

        state = self.inspect_input_with_changing_fields(change_match)
        self.assert_unknown_input_identity(state)

    def test_draft_changes_do_not_mask_changed_process_generation(self):
        def change_process():
            self.memory.creation += 1

        state = self.inspect_input_with_changing_fields(change_process)
        self.assert_unknown_input_identity(state)

    def test_native_alliance_masks_distinguish_trial_enemy_despite_equal_team(self):
        self.binding=replace(self.binding,slot=4)
        self.memory.pack(self.memory.base+0x35a1c50+3*4,'I',0)
        self.memory.pack(self.memory.base+0x35a1e10+3*4,'I',0)
        self.assertEqual(self.poll().hits[0].team_relation,'enemy')
        self.memory.pack(self.memory.base+0x35a1e10+3*4,'I',1<<4)
        self.assertEqual(self.poll().hits[0].team_relation,'ally')
        self.memory.pack(self.memory.base+0x35a1e10+3*4,'I',0)
        self.memory.pack(self.memory.base+0x35a1c50+3*4,'I',1<<4)
        self.assertEqual(self.poll().hits,())

    def test_cached_row_array_cannot_refer_to_detached_but_otherwise_valid_row(self):
        self.assertEqual(len(self.poll().hits),1)
        self.memory.children(self.memory.panel)
        self.assertEqual(self.poll().status,'unknown')

    def test_visible_tree_must_remain_in_current_rows_real_descendant_chain(self):
        self.assertEqual(len(self.poll().hits),1)
        self.memory.children(self.memory.row,self.memory.name,self.memory.hero_name)
        result=self.poll()
        self.assertEqual(result.hits,())
        self.assertEqual(result.status,'unknown')

    def test_visible_tree_can_live_below_a_visible_row_container(self):
        container=self.memory.frame(0x27d96d8)
        self.memory.children(self.memory.row,self.memory.name,self.memory.hero_name,container)
        self.memory.children(container,self.memory.tree)
        self.assertEqual(len(self.poll().hits),1)

    def test_localized_metadata_must_match_native_tier_and_early_unlock_level(self):
        for field,value in (('level',4),('tier',0)):
            self.reader.text_provider=lambda *_,field=field,value=value:{
                'talent_name':'测试天赋','effect_summary':'效果',field:value}
            self.assertEqual(self.poll().hits,())


if __name__=='__main__':unittest.main()
