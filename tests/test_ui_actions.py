from types import SimpleNamespace as NS
from unittest.mock import patch
import unittest
from hots_chat.ui.main_window import MainWindow
from hots_chat.ui.compact_window import apply_mode
from hots_chat.window_settings import WindowSettings
from hots_chat.ui.viewmodel import AppViewModel
from hots_chat.models import AppEvent


class TextBuffer:
    def __init__(self,text=''):self.text=text
    def configure(self,**kwargs):pass
    def delete(self,*args):self.text=''
    def insert(self,index,text):self.text=text
    def get(self,*args):return self.text


class Value:
    def __init__(self,value=''):self.value=value
    def set(self,value):self.value=value
    def get(self):return self.value

class UiActionsTests(unittest.TestCase):
    def test_shutdown_without_layout_guard_never_cancels_empty_timer(self):
        cleaned=[]
        w=MainWindow.__new__(MainWindow);w.closed=False;w.timer='pump';w.start_timer='startup'
        w.root=NS(after_cancel=lambda token:cleaned.append(token),destroy=lambda:cleaned.append('root'))
        w.loot_helper=NS(close=lambda:cleaned.append('loot-service'))
        w.loot_panel=NS(close=lambda:cleaned.append('loot-ui'))
        w.hotkeys=NS(stop=lambda:None);w.quick_input=NS(close=lambda:None)
        w.overlay=NS(close=lambda:None);w.controller=NS(close=lambda:None)
        w.save_settings=lambda:None
        w.close()
        self.assertEqual(cleaned,['pump','startup','loot-service','loot-ui','root'])

    def test_shutdown_finishes_even_when_one_native_cleanup_fails(self):
        cleaned=[]
        def fail():
            cleaned.append('quick');raise OSError('subclass cleanup failed')
        w=MainWindow.__new__(MainWindow);w.closed=False;w.timer='pump';w.start_timer='startup'
        w._layout_guard_timer='layout-guard'
        w.loot_helper=NS(close=lambda:cleaned.append('loot-service'))
        w.loot_panel=NS(close=lambda:cleaned.append('loot-ui'))
        w.root=NS(after_cancel=lambda token:cleaned.append(token),destroy=lambda:cleaned.append('root'))
        w.hotkeys=NS(stop=lambda:cleaned.append('hotkeys'))
        w.quick_input=NS(close=fail);w.overlay=NS(close=lambda:cleaned.append('overlay'))
        w.controller=NS(close=lambda:cleaned.append('controller'))
        w.save_settings=lambda:cleaned.append('settings')
        w.close();w.close()
        self.assertEqual(cleaned,['pump','startup','layout-guard','loot-service','loot-ui','hotkeys','quick','overlay','settings','controller','root'])

    def test_capture_state_is_independent_of_translation_status(self):
        w=self.make_preview_window();w.capture_state=Value();w.status_var=Value()
        w.controller=NS(drain_events=lambda:[AppEvent('capture_state',0,'等待对局聊天，自动恢复'),AppEvent('status',0,'译文已复制')])
        w.pump()
        self.assertEqual(w.capture_state.get(),'采集：等待对局聊天，自动恢复')
        self.assertEqual(w.status_var.get(),'译文已复制')

    def test_old_history_details_do_not_show_new_match_context(self):
        w=MainWindow.__new__(MainWindow);w.vm=AppViewModel()
        from hots_chat.models import ChatEvent
        w.vm.apply(AppEvent('chat',0,ChatEvent('old',0,'团队','tester','0:01','old message')))
        w.vm.apply(AppEvent('generation',1,'new match'))
        w.tree=NS(selection=lambda:['old']);w.detail=TextBuffer()
        w.controller=NS(generation=1,context=NS(recent=lambda:[NS(text='current match only')]))
        w.show_selected()
        self.assertIn('old message',w.detail.text)
        self.assertNotIn('current match only',w.detail.text)
    def make_preview_window(self):
        w=MainWindow.__new__(MainWindow)
        w.vm=AppViewModel();w.preview=TextBuffer();w.manual_source=Value()
        w.closed=False;w.screen_ticks=0
        w.loot_helper=NS(drain_status=lambda:[])
        w.root=NS(after=lambda *args:None)
        return w

    def test_cloud_preview_contains_only_translation_for_selection_copy(self):
        w=self.make_preview_window();w.vm.expected_job='current'
        w.controller=NS(drain_events=lambda:[AppEvent('manual_result',0,{'job_id':'current','translated':'Wait for level 10.','provenance':'阿里云'})])
        w.pump()
        self.assertEqual(w.preview.text,'Wait for level 10.')
        self.assertEqual(w.manual_source.get(),'来源：阿里云')

    def test_local_phrase_preview_also_excludes_provenance(self):
        w=self.make_preview_window();w.vm.expected_job='current'
        w.controller=NS(drain_events=lambda:[AppEvent('manual_result',0,{'job_id':'current','translated':'Wait for level 10.','provenance':'已校对本机短句'})])
        w.pump()
        self.assertEqual(w.preview.text,'Wait for level 10.')
        self.assertEqual(w.manual_source.get(),'来源：已校对本机短句')

    def test_connection_generation_clears_visible_translation_and_source(self):
        w=self.make_preview_window();w.vm.expected_job='current'
        events=[AppEvent('manual_result',0,{'job_id':'current','translated':'Wait for level 10.','provenance':'阿里云'})]
        w.controller=NS(drain_events=lambda:events)
        w.status_var=Value();w.tree=NS(get_children=lambda:[])
        w.show_selected=lambda:None
        w.pump();self.assertEqual(w.preview.text,'Wait for level 10.')
        events[:]=[AppEvent('generation',1,'已重连')]
        w.pump()
        self.assertEqual(w.preview.text,'')
        self.assertEqual(w.manual_source.get(),'')

    def test_negative_monitor_position_is_absolute_tk_geometry(self):
        calls=[]
        root=NS(minsize=lambda *a:None,geometry=calls.append)
        w=NS(root=root,settings=WindowSettings(x=-1000,y=100),detail=NS(pack_forget=lambda:None))
        apply_mode(w,'compact')
        self.assertEqual(calls[-1],'740x520+-1000+100')
    def test_cancelled_process_selection_can_be_reopened(self):
        calls=[]
        source=NS(selected_pid=None,process_finder=lambda:[NS(pid=7),NS(pid=8)],reconnect=lambda:calls.append(True))
        window=MainWindow.__new__(MainWindow);window.controller=NS(source=source);window.root=None
        with patch('hots_chat.ui.main_window.simpledialog.askinteger',side_effect=[None,8]):
            window.choose_process();window.choose_process()
        self.assertEqual(source.selected_pid,8);self.assertEqual(len(calls),1)
