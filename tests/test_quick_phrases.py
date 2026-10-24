"""Ctrl+T 本机候选的纯函数和 fake 控件回归，不创建 GUI 或访问用户配置。"""
from dataclasses import replace
from types import SimpleNamespace as NS
import re
import unittest
from unittest.mock import Mock, patch

from hots_chat.layout import DisplayMetrics
from hots_chat.phrases import Phrase, suggest_phrases
from hots_chat.ui.quick_input import QuickInput
from tests import test_quick_input as fixtures


def phrase(identifier, source='先撤退', *, favorite=True, reviewed=True, translations=None):
    return Phrase(identifier,'团战',source,{'ko':'후퇴해요'} if translations is None else translations,
                  favorite=favorite,reviewed=reviewed)


class PhraseSuggestionTests(unittest.TestCase):
    def test_empty_input_shows_only_favorites_in_stable_order(self):
        items=[phrase('z'),phrase('hidden',favorite=False),phrase('a','先集合')]
        self.assertEqual([item.phrase.id for item in suggest_phrases(items,'  ','ko')],['a','z'])

    def test_query_matches_chinese_only_and_prioritizes_favorites_then_prefix(self):
        items=[phrase('middle','大家先撤退'),phrase('prefix','撤退到基地'),
               phrase('other','撤退后集合',favorite=False),phrase('translated','谢谢',translations={'en':'撤退'})]
        self.assertEqual([item.phrase.id for item in suggest_phrases(items,'撤退','ko')],
                         ['prefix','middle','other'])
        self.assertEqual(suggest_phrases(items,'不存在','ko'),())

    def test_five_item_limit_and_duplicate_sources_do_not_fill_the_list(self):
        items=[phrase(str(index),f'第{index}句') for index in range(8)]
        items.insert(0,replace(items[0],id='duplicate'))
        self.assertEqual(len(suggest_phrases(iter(items),'','ko',limit=100)),5)
        self.assertEqual(len(suggest_phrases(items,'','ko',limit=3)),3)
        self.assertEqual(suggest_phrases(items,'','ko',limit=0),())

    def test_local_badge_requires_reviewed_target_and_one_unique_translation(self):
        items=[phrase('approved'),phrase('missing','集合',translations={'en':'Group up'}),
               phrase('unreviewed','谢谢',reviewed=False),phrase('conflict-one','打营地'),
               phrase('conflict-two','打营地',translations={'ko':'different'})]
        result={item.phrase.source_text:item.local_available for item in suggest_phrases(items,'','ko')}
        self.assertEqual(result,{'先撤退':True,'集合':False,'谢谢':False,'打营地':False})
        self.assertFalse(suggest_phrases([phrase('spaces',' 先撤退 ')],'','ko')[0].local_available)


class Frame:
    def __init__(self):
        self.visible=False
        self.options={}

    def pack(self,**kwargs):self.visible=True
    def pack_forget(self):self.visible=False
    def configure(self,**kwargs):self.options.update(kwargs)


class Font:
    def __init__(self):self.size=12
    def measure(self,text):return sum(14 if ord(char)>127 else 7 for char in text)
    def cget(self,name):return self.size
    def configure(self,**kwargs):self.size=kwargs.get('size',self.size)
    def metrics(self,name):return 24


class CandidateList(Frame):
    def __init__(self,focus):
        super().__init__()
        self.focus=focus
        self.items=[]
        self.selected=()

    def curselection(self):return self.selected
    def delete(self,*args):self.items.clear();self.selected=()
    def insert(self,index,text):self.items.append(text)
    def selection_clear(self,*args):self.selected=()
    def selection_set(self,index):self.selected=(index,)
    def activate(self,index):pass
    def see(self,index):pass
    def focus_set(self):self.focus.current=self
    def nearest(self,y):return max(0,min(len(self.items)-1,y//24))
    def bbox(self,index):return (0,index*24,500,24) if self.items else None


class QuickPhraseTests(unittest.TestCase):
    result=fixtures.QuickInputTests.result

    def setUp(self):
        fixtures.QuickInputTests.setUp(self)
        self.quick=self.window
        self.quick.open()
        self.addCleanup(self.quick.close)
        self.items=[phrase('a','先撤退'),phrase('b','撤退到基地'),phrase('c','先集合')]
        self.controller.phrases=NS(list=lambda:self.items)
        self.focus=NS(current=self.quick.entry)
        self.quick.window.focus_get=lambda:self.focus.current
        entry=self.quick.entry
        focus=self.focus
        original_focus=entry.focus_set
        def focus_entry():
            original_focus()
            focus.current=entry
        entry.focus_set=focus_entry
        self.quick._phrase_frame=Frame()
        self.quick._phrase_hint=Frame()
        self.quick._phrase_list=CandidateList(self.focus)
        self.quick._entry_font=Font()
        self.quick.refresh_layout=Mock(side_effect=lambda **kwargs:self.quick._render_phrase_labels(600))
        self.quick._refresh_phrases()

    def test_tab_explicitly_focuses_then_fills_without_submitting_or_copying(self):
        self.quick.text.set('撤退')
        self.assertEqual(self.quick._on_phrase_tab(NS(widget=self.quick.entry)),'break')
        self.assertIs(self.focus.current,self.quick._phrase_list)
        self.assertEqual(self.quick.text.get(),'撤退')
        self.quick._move_phrase(1)
        expected=self.quick._phrase_candidates[1].phrase.source_text
        self.assertEqual(self.quick._on_phrase_tab(NS(widget=self.quick._phrase_list)),'break')
        self.assertEqual(self.quick.text.get(),expected)
        self.assertIs(self.focus.current,self.quick.entry)
        self.assertEqual(self.controller.submitted,[])
        self.assertEqual(self.clipboard,[])
        self.assertFalse(self.quick._phrase_frame.visible)
        self.assertIsNone(self.quick._on_phrase_tab(NS(widget=self.quick.entry)))

    def test_arrow_keys_only_navigate_the_focused_candidate_list(self):
        self.assertIsNone(self.quick._move_phrase(1))
        self.assertEqual(self.quick._phrase_list.curselection(),(0,))
        self.quick._phrase_list.focus_set()
        self.assertEqual(self.quick._move_phrase(1),'break')
        self.assertEqual(self.quick._phrase_list.curselection(),(1,))
        self.native.composing=True
        self.assertEqual(self.quick._move_phrase(1),'break')
        self.assertEqual(self.quick._phrase_list.curselection(),(1,))

    def test_composition_hides_suggestions_and_protects_tab_and_mouse(self):
        self.native.composing=True
        self.quick.text.set('撤')
        self.assertFalse(self.quick._phrase_frame.visible)
        self.assertEqual(self.quick._phrase_candidates,())
        self.assertEqual(self.quick._on_phrase_tab(NS(widget=self.quick.entry)),'break')
        self.quick._on_phrase_click(NS(y=0))
        self.assertEqual(self.quick.text.get(),'撤')
        self.native.composing=False
        self.native.return_guard=True
        self.quick._refresh_phrases()
        self.assertEqual(self.quick._phrase_candidates,())
        self.native.clear_return_guard()
        self.quick._refresh_phrases()
        self.assertTrue(self.quick._phrase_frame.visible)

    def test_mouse_selection_invalidates_old_result_but_never_submits(self):
        self.quick.text.set('撤退')
        self.quick._submit()
        job=self.controller.submitted[-1][2]
        self.quick._on_focus_out()
        self.assertIsNotNone(self.quick._pending,'同一窗口内部焦点变化不取消请求')
        self.quick._on_phrase_press(NS(y=25))
        self.quick._on_phrase_click(NS(y=25))
        self.assertEqual(len(self.controller.submitted),1)
        self.assertIsNone(self.quick._pending)
        self.quick.handle_event(self.result(job))
        self.assertEqual(self.clipboard,[])

    def test_mouse_press_during_ime_cannot_fill_after_composition_ends(self):
        self.native.composing=True
        self.assertEqual(self.quick._on_phrase_press(NS(y=0)),'break')
        self.native.composing=False
        self.quick._on_phrase_click(NS(y=0))
        self.assertEqual(self.quick.text.get(),'')

    def test_modified_tab_keeps_target_switch_and_reverse_focus_navigation(self):
        for state in (1,4,8):
            self.assertIsNone(self.quick._on_phrase_tab(NS(widget=self.quick.entry,state=state)))
        self.assertIs(self.focus.current,self.quick.entry)

    def test_enter_keeps_translating_current_draft_instead_of_highlighted_candidate(self):
        self.quick.text.set('撤')
        self.quick._phrase_list.focus_set()
        self.quick._on_return_press()
        self.quick._on_return_release()
        self.assertEqual(self.controller.submitted[-1][:2],('撤','ko'))

    def test_language_change_updates_local_availability_without_editing_draft(self):
        self.assertIn('本机可用',self.quick._phrase_list.items[0])
        self.quick.text.set('撤')
        self.quick.target.set('en')
        self.quick._target_changed()
        self.assertEqual(self.quick.text.get(),'撤')
        self.assertTrue(all('需翻译' in label for label in self.quick._phrase_list.items))

    def test_no_match_or_missing_book_hides_the_panel_without_replacing_draft(self):
        self.quick.text.set('完全不同的内容')
        self.assertFalse(self.quick._phrase_frame.visible)
        self.controller.phrases=None
        self.quick.text.set('撤退')
        self.assertFalse(self.quick._phrase_frame.visible)
        self.assertEqual(self.quick.text.get(),'撤退')

    def test_no_favorites_shows_only_a_small_discoverability_hint(self):
        self.items[:]=[phrase('not-favorite',favorite=False)]
        self.quick._refresh_phrases()
        self.assertEqual(self.quick._phrase_candidates,())
        self.assertTrue(self.quick._phrase_frame.visible)
        self.assertFalse(self.quick._phrase_list.visible)
        self.assertIn('主窗口收藏',self.quick._phrase_hint.options['text'])

    def test_cancel_and_reopen_preserve_unsubmitted_draft_and_do_not_choose(self):
        self.quick.text.set('撤')
        self.quick.cancel()
        self.assertEqual(self.quick.text.get(),'撤')
        self.assertFalse(self.quick._phrase_frame.visible)
        self.quick.open()
        self.assertEqual(self.quick.text.get(),'撤')
        self.assertTrue(self.quick._phrase_frame.visible)
        self.assertEqual(self.controller.submitted,[])

    def test_long_source_is_shortened_only_for_display(self):
        source='先撤退' * 100
        self.items[:]=[phrase('long',source)]
        self.quick._refresh_phrases()
        self.assertIn('…',self.quick._phrase_list.items[0])
        self.assertLessEqual(self.quick._entry_font.measure(self.quick._phrase_list.items[0]),600)
        self.quick._fill_phrase(0)
        self.assertEqual(self.quick.text.get(),source)

    def test_candidate_height_uses_existing_bounded_layout_and_no_new_timer(self):
        self.quick._metrics=DisplayMetrics((100,50,900,550))
        self.quick.body=Frame()
        self.quick.window.configure=Mock()
        self.quick.window.update_idletasks=Mock()
        self.quick.window.winfo_reqheight=lambda:120+24*len(self.quick._phrase_candidates)
        self.quick.window.geometry=Mock()
        timers=dict(self.root.callbacks)
        with patch('hots_chat.ui.quick_input.style_native_window'):
            QuickInput.refresh_layout(self.quick,force=True)
        geometry=self.quick.window.geometry.call_args.args[0]
        width,height,x,y=map(int,re.fullmatch(r'(\d+)x(\d+)\+(-?\d+)\+(-?\d+)',geometry).groups())
        self.assertGreater(height,120)
        self.assertGreaterEqual(x,100)
        self.assertGreaterEqual(y,50)
        self.assertLessEqual(x+width,900)
        self.assertLessEqual(y+height,550)
        self.assertEqual(self.root.callbacks,timers)

    def test_small_height_reduces_visible_rows_and_keeps_candidates_scrollable(self):
        self.quick._metrics=DisplayMetrics((0,0,800,260))
        self.quick.body=Frame()
        self.quick.window.configure=Mock()
        self.quick.window.update_idletasks=Mock()
        self.quick.window.winfo_reqheight=lambda:160+24*self.quick._phrase_list.options.get('height',3)
        self.quick.window.geometry=Mock()
        with patch('hots_chat.ui.quick_input.style_native_window'):
            QuickInput.refresh_layout(self.quick,force=True)
        self.assertLess(self.quick._phrase_list.options['height'],len(self.quick._phrase_candidates))
        self.assertEqual(len(self.quick._phrase_candidates),3)

    def test_close_releases_candidate_widgets_and_callbacks(self):
        self.quick.close()
        self.assertEqual(self.quick._phrase_candidates,())
        self.assertIsNone(self.quick._phrase_list)
        self.assertIsNone(self.quick._phrase_frame)
        self.assertIsNone(self.quick._phrase_hint)
        self.assertIsNone(self.quick._clipboard)


if __name__=='__main__':unittest.main()
