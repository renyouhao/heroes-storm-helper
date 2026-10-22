"""开箱页与现有主窗的联动；临时数据、屏幕外窗口，不装系统钩子。"""
from collections import OrderedDict
from types import SimpleNamespace
import unittest

from hots_chat.loot import LootStatus
from hots_chat.models import ChatEvent
from hots_chat.ui.viewmodel import MessageView
from tests import test_adaptive_display_flow as adaptive


class LootMainTests(unittest.TestCase):
    def setUp(self):
        fixture = adaptive.AdaptiveDisplayFlowTests(methodName='runTest')
        self.context = fixture.app()
        self.root, self.window, self.settings, self.screen, _ = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def test_loot_navigation_keeps_chat_reply_draft_and_collapses_composer(self):
        self.assertIn('loot', self.window.navigation, '需要可发现的开箱助手入口')
        self.window.input.insert('1.0', '等我集合')
        self.window.input.edit_modified(False)
        self.assertFalse(self.window.loot_helper.active)
        self.window.navigation['loot'].invoke()
        self.root.update()
        self.assertEqual(self.window.notebook.select(), str(self.window.loot_panel))
        self.assertEqual(self.window.section_title.get(), '开箱助手')
        self.assertFalse(self.window.reply_panel.winfo_ismapped())
        self.window.navigation['chat'].invoke()
        self.root.update()
        self.assertTrue(self.window.reply_panel.winfo_ismapped())
        self.assertEqual(self.window.input.get('1.0', 'end-1c'), '等我集合')

    def test_prepare_button_only_requests_service_and_main_pump_displays_its_status(self):
        self.assertTrue(hasattr(self.window, 'loot_helper'), '主窗须连接按需开箱服务')
        calls = []
        queue = []

        def drain():
            statuses = queue[:]
            queue.clear()
            return statuses

        self.window.loot_helper = SimpleNamespace(
            prepare=lambda: calls.append('prepare') or True,
            cancel=lambda: calls.append('cancel'), close=lambda: calls.append('close'),
            drain_status=drain, accepts_status=lambda status:True)
        self.window.loot_panel.prepare_button.invoke()
        self.assertEqual(calls, ['prepare'])
        self.assertEqual(self.window.loot_panel.state, 'preparing')
        # 不映射游戏浮条；主线状态联动检查用独立UI记录器。
        seen = []
        self.window.loot_panel.update_status = lambda state, detail='': seen.append(state)
        queue.append(LootStatus('running', '正在开箱'))
        self.window.pump()
        self.assertEqual(seen[-1], 'running')
        self.assertTrue(self.window._loot_active)
        queue.append(LootStatus('idle'))
        self.window.pump()
        self.assertFalse(self.window._loot_active)

    def test_quick_input_and_loot_visibility_do_not_restore_each_others_subtitles(self):
        self.assertTrue(hasattr(self.window, 'loot_helper'), '需要统一浮层避让')
        overlay = self.window.overlay
        overlay._clock = lambda: 100.
        overlay.sync(OrderedDict(a=MessageView(
            ChatEvent('a', 0, '团队', '队友', '00:01', 'help'), engine_translation='支援')), 0)
        self.root.update()
        self.assertEqual(overlay.window.state(), 'normal')
        self.window.loot_panel.update_status = lambda *args: None
        self.window.update_loot_status(LootStatus('ready'))
        self.assertEqual(overlay.window.state(), 'withdrawn')
        self.window.quick_input._set_visible(True)
        self.window.update_loot_status(LootStatus('idle'))
        self.assertEqual(overlay.window.state(), 'withdrawn')
        self.window.quick_input._set_visible(False)
        self.assertEqual(overlay.window.state(), 'normal')

    def test_unavailable_status_overlay_cancels_the_service_and_exposes_an_error(self):
        self.assertTrue(hasattr(self.window, 'loot_helper'))
        cancelled, seen = [], []
        self.window.loot_helper = SimpleNamespace(cancel=lambda: cancelled.append(True),
                                                  close=lambda: None, drain_status=lambda: [])

        def update(state, detail=''):
            seen.append((state, detail))
            if state == 'ready':
                raise OSError('overlay unavailable')

        self.window.loot_panel.update_status = update
        self.window.update_loot_status(LootStatus('ready'))
        self.assertEqual(cancelled, [True])
        self.assertEqual(seen[-1][0], 'error')
        self.assertFalse(self.window._loot_active)
