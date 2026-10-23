"""聊天尾部增量更新：验证真实文本与送往Tk的绘制量。"""
from collections import OrderedDict
import tkinter as tk
from tkinter import font as tkfont
import unittest
from unittest.mock import patch

from hots_chat.models import ChatEvent
from hots_chat.ui.chat_feed import ChatFeed
from hots_chat.ui.theme import apply_theme
from hots_chat.ui.viewmodel import MessageView
from hots_chat.window_settings import WindowSettings


class IncrementalChatFeedTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk()
        self.root.geometry('640x480+10000+10000')
        self.addCleanup(self.root.destroy)
        settings=WindowSettings()
        apply_theme(self.root,settings)
        self.feed=ChatFeed(self.root,font=tkfont.Font(root=self.root,size=12),settings=settings)
        self.feed.pack(fill='both',expand=True)
        self.messages=OrderedDict((str(i),self.message(i)) for i in range(500))
        self.feed.sync(self.messages,1)
        self.root.update()
        self.feed.yview_moveto(1)
        self.root.update_idletasks()

    @staticmethod
    def message(i):
        return MessageView(ChatEvent(str(i),1,'团队','队友','0:01',f'message {i} original'))

    def render_and_count(self):
        with patch.object(self.feed.body,'insert',wraps=self.feed.body.insert) as insert:
            self.feed.sync(self.messages,1)
            self.root.update_idletasks()
        return sum(len(call.args[1]) for call in insert.call_args_list)

    def test_append_at_history_limit_does_not_redraw_499_unchanged_messages(self):
        self.messages.popitem(last=False)
        self.messages['500']=self.message(500)
        self.assertLess(self.render_and_count(),300)
        text=self.feed.body.get('1.0','end-1c')
        self.assertIn('message 500 original',text)
        self.assertIn('message 1 original',text)
        self.assertNotIn('message 0 original',text)

    def test_translation_replaces_only_its_row_and_preserves_neighbours(self):
        self.messages['250'].engine_translation='仅更新这一条译文'
        self.assertLess(self.render_and_count(),300)
        text=self.feed.body.get('1.0','end-1c')
        self.assertEqual(text.count('仅更新这一条译文'),1)
        self.assertEqual(text.count('message 249 original'),1)
        self.assertEqual(text.count('message 251 original'),1)
        self.assertLess(text.index('message 249 original'),text.index('仅更新这一条译文'))
        self.assertLess(text.index('仅更新这一条译文'),text.index('message 251 original'))

    def test_unchanged_sync_does_not_schedule_a_new_paint(self):
        self.feed.sync(self.messages,1)
        self.assertIsNone(self.feed._paint_timer)


if __name__=='__main__':unittest.main()
