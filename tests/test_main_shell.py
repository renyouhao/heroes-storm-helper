"""主界面导航和小窗口适配；只创建临时数据、屏幕外窗口。"""
import unittest
from tkinter import ttk
from collections import OrderedDict

from hots_chat.models import ChatEvent
from hots_chat.ui.viewmodel import MessageView
from tests import test_adaptive_display_flow as adaptive


class MainShellTests(unittest.TestCase):
    def setUp(self):
        fixture = adaptive.AdaptiveDisplayFlowTests(methodName='runTest')
        self.context = fixture.app()
        self.root, self.window, self.settings, self.screen, _ = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def test_wide_navigation_switches_pages_without_resetting_reply(self):
        self.assertTrue(hasattr(self.window, 'sidebar'), '宽屏应使用独立导航区')
        self.assertTrue(self.window.sidebar.winfo_ismapped())
        self.window.input.insert('1.0', '等我一下')
        self.window.input.edit_modified(False)
        original = self.window.controller
        self.window.navigation['phrases'].invoke()
        self.root.update_idletasks()
        self.assertEqual(self.window.notebook.select(), str(self.window.phrase_panel))
        self.assertEqual(self.window.section_title.get(), '常用短句')
        self.window.navigation['chat'].invoke()
        self.root.update_idletasks()
        self.assertEqual(self.window.notebook.select(), str(self.window.chat_page))
        self.assertEqual(self.window.input.get('1.0', 'end-1c'), '等我一下')
        self.assertIs(self.window.controller, original)

    def test_narrow_window_keeps_navigation_and_reply_actions_available(self):
        self.assertTrue(hasattr(self.window, 'sidebar'), '窄屏要能收起侧栏')
        self.root.geometry('540x420+10000+10000')
        self.root.update()
        self.assertFalse(self.window.sidebar.winfo_ismapped())
        self.assertTrue(ttk.Style(self.root).layout('Main.TNotebook.Tab'))
        for name in ('input', 'preview', 'action_row', 'settings_button', 'quick_button'):
            widget = getattr(self.window, name)
            self.assertTrue(widget.winfo_ismapped(), name)
            self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(),
                                 self.root.winfo_rootx() + self.root.winfo_width(), name)
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                                 self.root.winfo_rooty() + self.root.winfo_height(), name)

    def test_input_visibility_hides_subtitles_without_extending_their_lifetime(self):
        overlay = self.window.overlay
        self.settings.overlay_style = 'subtitle'
        self.settings.overlay_placement = 'bottom'
        self.settings.overlay_font_size = 11
        self.settings.overlay_timeout = 6
        self.settings.overlay_original = False
        self.settings.overlay_width = 420
        now = [100.]
        overlay._clock = lambda: now[0]
        overlay.apply_settings(self.screen.refresh())
        messages = OrderedDict(a=MessageView(
            ChatEvent('a', 0, '团队', '队友', '0:01', 'group up'), engine_translation='集合'))
        overlay.sync(messages, 0)
        self.root.update()
        self.assertEqual(overlay.window.state(), 'normal')
        self.assertTrue(overlay.subtitle_canvas.winfo_ismapped())
        deadline = overlay.history.next_expiry
        # 走输入条的统一可见状态通知，核对主窗口实际连线；不调用真实焦点API。
        self.window.quick_input._set_visible(True)
        self.assertEqual(overlay.window.state(), 'withdrawn')
        now[0] = 102.
        self.window.quick_input._set_visible(False)
        self.assertEqual(overlay.window.state(), 'normal')
        self.assertEqual(overlay.history.next_expiry, deadline)
        self.window.quick_input._set_visible(True)
        now[0] = deadline
        self.window.quick_input._set_visible(False)
        self.assertEqual(overlay.window.state(), 'withdrawn')
        self.assertFalse(overlay.history.entries)
        self.assertEqual(len(messages), 1, '字幕到期不能清理主窗口历史')


if __name__ == '__main__':
    unittest.main()
