"""柔雾紫聊天流：验证实际排版、可复制原文和有界头像资源。"""
from collections import OrderedDict
from types import SimpleNamespace
import tkinter as tk
from tkinter import font as tkfont
import unittest

from hots_chat.models import ChatEvent
from hots_chat.ui.chat_feed import ChatFeed
from hots_chat.ui.theme import apply_theme, theme_colors
from hots_chat.ui.viewmodel import MessageView
from hots_chat.window_settings import WindowSettings


class LavenderChatFeedTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry('1000x560+10000+10000')
        self.addCleanup(self.root.destroy)
        self.settings = WindowSettings()
        apply_theme(self.root, self.settings)
        self.feed = ChatFeed(self.root, font=tkfont.Font(root=self.root, size=12),
                             settings=self.settings)
        self.feed.pack(fill='both', expand=True)
        self.messages = OrderedDict()

    @staticmethod
    def message(key, *, sender='Raven', generation=0):
        return MessageView(ChatEvent(str(key), generation, '团队', sender, '14:21',
                                     f'Original message {key}'),
                           f'先拿营地，然后一起去下路。{key}', '缓存')

    def render(self, generation=0):
        self.feed.sync(self.messages, generation)
        self.root.update()

    def test_metadata_time_stays_at_right_edge_when_window_resizes(self):
        self.messages['first'] = self.message('first')
        self.render()
        body = self.feed.body
        sender = body.search('Raven', '1.0', 'end')
        timestamp = body.search('14:21', '1.0', 'end')
        for width in (1000, 640):
            with self.subTest(width=width):
                self.root.geometry(f'{width}x560+10000+10000')
                self.root.update()
                sender_box, time_box = body.bbox(sender), body.bbox(timestamp)
                self.assertGreater(time_box[0], body.winfo_width() - 100)
                self.assertLess(abs(sender_box[1] - time_box[1]), 8)

    def test_primary_translation_and_selectable_original_keep_separate_roles(self):
        self.messages['first'] = self.message('first')
        self.render()
        body = self.feed.body
        text = body.get('1.0', 'end-1c')
        self.assertIn('原文  Original message first', text)
        self.assertLess(text.index('先拿营地'), text.index('Original message first'))
        label_start = body.search('原文  ', '1.0', 'end', elide=True)
        self.assertTrue(label_start)
        self.assertEqual(int(body.tk.call(body._w, 'count', '-displaychars',
                                          label_start, label_start + '+4c')), 0)
        original = body.search('Original message first', '1.0', 'end')
        body.tag_add('sel', original, original + '+22c')
        self.assertEqual(body.get('sel.first', 'sel.last'), 'Original message first')
        colors = theme_colors(self.settings)
        self.assertEqual(body.tag_cget('sender', 'foreground'), colors['text'])
        self.assertEqual(body.tag_cget('channel', 'background'), colors['selection'])

    def test_avatar_gutter_renders_only_visible_messages_without_per_row_widgets(self):
        self.messages.update((str(i), self.message(i, sender=f'队友{i}')) for i in range(500))
        self.render()
        self.assertEqual(len(self.feed.get_children()), 500)
        self.assertEqual(len(self.feed.winfo_children()), 2)
        self.assertEqual(self.feed.body.winfo_children(), [self.feed.avatars])
        self.assertLess(len(self.feed._visible_avatars), 15)
        self.assertLessEqual(len(self.feed.avatars.find_all()), 30)
        for i in range(500, 520):
            self.messages.popitem(last=False)
            self.messages[str(i)] = self.message(i)
            self.render()
        self.assertEqual(len(self.feed._tags), 500)
        self.assertLessEqual(len(self.feed.avatars.find_all()), 30)
        self.messages.clear()
        self.render()
        self.assertFalse(self.feed.avatars.find_all())
        self.assertEqual(self.feed.avatars.winfo_manager(), '')
        self.assertIn('等待队友的第一条消息', self.feed.body.get('1.0', 'end'))

    def test_avatar_selection_preserves_keyboard_and_history_message_identity(self):
        old = self.message('collision')
        self.messages['collision'] = old
        self.render()
        key, top, bottom = self.feed._visible_avatars[0]
        self.feed._select_avatar(SimpleNamespace(y=(top + bottom) / 2))
        self.assertEqual(self.feed.selection(), (key,))
        self.messages = OrderedDict((('history:0:collision', old),
                                     ('collision', self.message('collision', sender='Jin', generation=1))))
        self.render(generation=1)
        self.assertEqual(self.feed.selection(), ('history:0:collision',))
        self.assertIn('历史对局', self.feed.body.get('1.0', 'end'))
        self.feed._move_selection(1)
        self.assertEqual(self.feed.selection(), ('collision',))

    def test_theme_and_dpi_refresh_reuse_gutter_and_preserve_source_selection(self):
        self.messages['first'] = self.message('first')
        self.render()
        body, gutter = self.feed.body, self.feed.avatars
        original = body.search('Original message first', '1.0', 'end')
        body.tag_add('sel', original, original + '+8c')
        width = self.feed._gutter
        self.root.tk.call('tk', 'scaling', float(self.root.tk.call('tk', 'scaling')) * 1.5)
        self.settings.color_theme = 'graphite'
        self.feed.apply_settings(self.settings)
        self.root.update()
        self.assertIs(self.feed.avatars, gutter)
        self.assertGreater(self.feed._gutter, width)
        self.assertEqual(gutter.cget('background'), theme_colors('graphite')['surface'])
        self.assertEqual(body.get('sel.first', 'sel.last'), 'Original')


if __name__ == '__main__':
    unittest.main()
