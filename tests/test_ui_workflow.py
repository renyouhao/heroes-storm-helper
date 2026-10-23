"""创建屏幕外的离线窗口，验证启动、事件分发和跨局历史，不操作用户游戏。"""
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hots_chat.app import build_controller
from hots_chat.paths import AppPaths
from hots_chat.models import AppEvent, ChatEvent
from hots_chat.window_settings import WindowSettings
from hots_chat.ui.main_window import create_app


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        p=Path(self.tmp.name)
        self.controller=build_controller(AppPaths(p/'.env',p/'usage.db',p/'language.db',p/'settings.json'),True)
        self.root=tk.Tk();self.root.withdraw()
        self.window=create_app(self.root,self.controller,WindowSettings(x=50000,y=50000,hotkeys={},overlay_enabled=False))
        self.addCleanup(self.window.close)

    def test_startup_receives_automatically_without_cloud_baseline(self):
        self.root.update()
        self.window.pump()
        self.assertEqual([self.window.notebook.tab(tab,'text') for tab in self.window.notebook.tabs()],
                         ['实时消息','常用短句','开箱助手','动作连按','天赋辅助','双击播报'])
        self.assertEqual(len(self.window.vm.messages),3)
        self.assertTrue(self.controller.paused,'未知额度不得调用云')

    def test_party_whisper_channel_picker_routes_new_messages_and_preserves_history(self):
        self.root.update()
        self.window.pump()
        self.assertEqual(self.window.channel.get(),'全部聊天')
        self.assertIn('仅队伍',self.window.channel_combo['values'])
        self.assertIn('仅密语',self.window.channel_combo['values'])
        self.window.channel.set('仅密语')
        self.window.channel_combo.event_generate('<<ComboboxSelected>>')
        self.root.update()
        self.controller._source_emit(AppEvent('chat',0,ChatEvent('private',0,'密语','好友','00:01','hi')))
        self.controller._source_emit(AppEvent('chat',0,ChatEvent('party',0,'队伍','我','00:01','hello')))
        self.window.pump()
        self.assertIn('private',self.window.vm.messages)
        self.assertNotIn('party',self.window.vm.messages)
        self.window.channel.set('仅队伍')
        self.window.channel_combo.event_generate('<<ComboboxSelected>>')
        self.root.update()
        self.controller._source_emit(AppEvent('chat',0,ChatEvent('party2',0,'队伍','我','00:02','hello')))
        self.window.pump()
        self.assertIn('party2',self.window.vm.messages)
        self.assertIn('private',self.window.vm.messages,'切换频道保留已接收的历史')

    def test_hotkey_opens_small_input_without_deiconifying_main(self):
        opened=[]
        self.window.quick_input=SimpleNamespace(open=lambda:opened.append(True),handle_event=lambda e:None,close=lambda:None,
                                               _visible=False)
        self.controller._source_emit(AppEvent('hotkey',0,{'action':'focus'}))
        self.window.pump()
        self.assertEqual(opened,[True])
        self.assertEqual(self.root.state(),'withdrawn')

    def test_quick_input_native_failure_does_not_stop_event_pump(self):
        with patch.object(self.window.quick_input,'open',side_effect=OSError('native initialization failed')):
            self.controller._source_emit(AppEvent('hotkey',0,{'action':'focus'}))
            self.window.pump()
        self.assertIn('快捷输入',self.window.status_var.get())
        self.assertIn(self.window.timer,self.root.tk.call('after','info'))
        self.controller._source_emit(AppEvent('capture_state',0,'已连接'))
        self.window.pump()
        self.assertEqual(self.window.capture_state.get(),'采集：已连接')

    def test_new_generation_keeps_old_table_row_and_accepts_new_message(self):
        self.controller._source_emit(AppEvent('chat',0,ChatEvent('old',0,'团队','test','00:01','before')))
        self.window.pump()
        self.controller._source_emit(AppEvent('generation',1,'换局'))
        self.controller._source_emit(AppEvent('chat',1,ChatEvent('new',1,'团队','test','00:01','after')))
        self.window.pump()
        self.assertEqual([m.original for m in self.window.vm.messages.values()],['before','after'])
        self.assertEqual(len(self.window.tree.get_children()),2)

    def test_confirmed_startup_allows_cloud_and_pause_only_affects_automatic(self):
        from hots_chat.clock import month_key
        self.controller.ledger.confirm_baseline('main',month_key(self.controller.clock()),0,self.controller.clock())
        self.window.enable_confirmed_cloud()
        self.window.automatic.set(False);self.window.toggle_automatic()
        self.assertFalse(self.controller.paused)
        self.assertTrue(self.controller.automatic_paused)

