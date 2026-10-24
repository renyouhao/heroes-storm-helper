"""仅创建屏幕外离线窗口，验证聊天阅读区和三主题切换的真实状态。"""
from tkinter import ttk
import unittest
from unittest.mock import patch

from hots_chat.models import AppEvent, ChatEvent
from hots_chat.ui.theme import theme_colors
from tests import test_adaptive_display_flow as adaptive


class ThreeThemeMainTests(unittest.TestCase):
    def setUp(self):
        self.fixture = adaptive.AdaptiveDisplayFlowTests(methodName='runTest')
        context = self.fixture.app()
        self.root, self.window, self.settings, self.screen, self.native = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def emit(self, *events):
        for event in events:
            self.window.controller.handle_source_event(event)
        self.window.pump()
        while not self.window.controller.events.empty() or not self.window.controller.source_events.empty():
            self.window.pump()
        self.root.update_idletasks()

    def add_scrolling_messages(self,start,count):
        # A chat also emits translation state/results; pump bounded batches so
        # this selection fixture does not accidentally test queue overflow.
        controller=self.window.controller
        with patch.object(controller,'_queue_overflow',wraps=controller._queue_overflow) as overflow:
            for offset in range(start,start+count,50):
                self.emit(*(AppEvent('chat',0,ChatEvent(f'scroll{index}',0,'团队',f'user{index:03}',
                            '00:01',f'row{index:03} original message'))
                            for index in range(offset,min(offset+50,start+count))))
            self.assertFalse(overflow.called,'阅读区回归必须完整交付消息，不能混入队列溢出')

    def top_text(self):
        body=self.window.tree.body
        index=body.index('@0,0')
        return body.get(index,index+'+20c')

    def select_source(self,index):
        body=self.window.tree.body
        start=body.search(f'row{index:03} original','1.0','end')
        self.assertTrue(start)
        body.tag_add('sel',start,start+'+15c')
        return body.get('sel.first','sel.last')

    def test_nonzero_reading_anchor_survives_ten_appended_messages(self):
        self.add_scrolling_messages(0,100)
        self.window.tree.yview_moveto(.28)
        self.root.update_idletasks()
        before=self.top_text()
        self.add_scrolling_messages(100,10)
        self.assertEqual(self.top_text(),before)

    def test_unrelated_translation_update_preserves_reading_anchor_and_text_selection(self):
        self.add_scrolling_messages(0,100)
        self.window.tree.yview_moveto(.28)
        self.root.update_idletasks()
        before=self.top_text()
        selected=self.select_source(40)
        self.window.controller._source_emit(AppEvent('result',0,
            {'message_id':'scroll2','engine_translation':'这是一段较长的新译文。'*30}))
        self.window.pump()
        self.root.update_idletasks()
        self.assertEqual(self.top_text(),before)
        self.assertTrue(self.window.tree.body.tag_ranges('sel'))
        self.assertEqual(self.window.tree.body.get('sel.first','sel.last'),selected)

    def test_appending_messages_preserves_selected_text(self):
        self.add_scrolling_messages(0,100)
        selected=self.select_source(40)
        self.add_scrolling_messages(100,10)
        body=self.window.tree.body
        self.assertTrue(body.tag_ranges('sel'))
        self.assertEqual(body.get('sel.first','sel.last'),selected)

    def test_history_limit_keeps_surviving_anchor_and_selection_on_original_messages(self):
        self.add_scrolling_messages(0,500)
        self.window.tree.yview_moveto(.28)
        self.root.update_idletasks()
        before=self.top_text()
        selected=self.select_source(250)
        self.add_scrolling_messages(500,10)
        body=self.window.tree.body
        self.assertEqual(len(self.window.tree.get_children()),500)
        self.assertEqual(self.top_text(),before)
        self.assertTrue(body.tag_ranges('sel'))
        self.assertEqual(body.get('sel.first','sel.last'),selected)

    def test_selection_of_evicted_or_changed_message_is_cleared_safely(self):
        self.add_scrolling_messages(0,500)
        self.select_source(10)
        self.add_scrolling_messages(500,20)
        self.assertFalse(self.window.tree.body.tag_ranges('sel'))
        self.select_source(250)
        self.window.controller._source_emit(AppEvent('result',0,
            {'message_id':'scroll250','engine_translation':'这条消息自身已更新。'}))
        self.window.pump()
        self.root.update_idletasks()
        self.assertFalse(self.window.tree.body.tag_ranges('sel'))

    def test_reused_message_id_never_moves_text_selection_to_new_match(self):
        self.emit(AppEvent('chat',0,ChatEvent('collision',0,'团队','旧队友','0:01','old selected text')))
        body=self.window.tree.body
        start=body.search('old selected text','1.0','end')
        body.tag_add('sel',start,start+'+8c')
        self.emit(AppEvent('generation',1,'新对局'),
                  AppEvent('chat',1,ChatEvent('collision',1,'团队','新队友','0:02','new message text')))
        if body.tag_ranges('sel'):
            self.assertEqual(body.get('sel.first','sel.last'),'old sele')

    def test_reading_area_prioritizes_translation_and_keeps_message_metadata(self):
        self.emit(AppEvent('chat', 0, ChatEvent('readable', 0, '所有玩家', '队友阿明',
                                              '12:34', 'Please help at objective.')))
        self.window.controller._source_emit(AppEvent('result', 0,
            {'message_id': 'readable', 'engine_translation': '请来支援地图机制。',
             'provenance': '已翻译'}))
        self.window.pump()
        self.root.update_idletasks()
        self.assertTrue(hasattr(self.window.tree, 'body'), '主阅读区应为聊天流')
        text = self.window.tree.body.get('1.0', 'end-1c')
        for expected in ('队友阿明', '所有玩家', '12:34', '已翻译'):
            self.assertIn(expected, text)
        self.assertLess(text.index('请来支援地图机制。'), text.index('Please help at objective.'))
        self.assertEqual(self.window.tree.item('readable', 'values'),
                         ('Please help at objective.', '请来支援地图机制。', '已翻译'))
        self.assertEqual(self.window.tree.body.cget('state'), 'disabled')
        self.assertEqual(self.window.detail.winfo_manager(), '', '默认阅读区不重复展开详情')

    def test_translation_source_labels_are_hidden_but_errors_and_message_text_remain(self):
        self.emit(*(AppEvent('chat', 0, ChatEvent(key, 0, '团队', '队友', '00:01', original))
                    for key, original in (('cloud', 'hello'), ('cached', 'hi'), ('failed', 'help'))))
        for key, translated, source in (('cloud', '你好', '阿里云'), ('cached', '嗨', '缓存')):
            self.window.controller._source_emit(AppEvent('result', 0,
                {'message_id': key, 'engine_translation': translated, 'provenance': source}))
        self.window.controller._source_emit(AppEvent('error', 0,
            {'message_id': 'failed', 'error': '阿里云暂时不可用，请重试'}))
        self.window.pump()
        self.root.update_idletasks()
        lines = self.window.tree.body.get('1.0', 'end-1c').splitlines()
        for source in ('阿里云', '缓存'):
            self.assertNotIn(source, lines, '成功译文不应再展示来源标签')
        for expected in ('你好', '嗨', '原文  hello', '原文  hi', '阿里云暂时不可用，请重试'):
            self.assertIn(expected, lines)
        self.assertEqual(self.window.tree.item('cloud', 'values'), ('hello', '你好', '阿里云'))
        self.assertEqual(self.window.tree.item('cached', 'values'), ('hi', '嗨', '缓存'))

    def test_reused_id_keeps_selected_historical_message_and_cannot_retry_new_match(self):
        self.emit(AppEvent('chat', 0, ChatEvent('reused', 0, '团队', '旧队友', '0:01', 'old text')))
        self.window.tree.selection_set('reused')
        self.emit(AppEvent('generation', 1, '新对局'),
                  AppEvent('chat', 1, ChatEvent('reused', 1, '团队', '新队友', '0:02', 'new text')))
        self.assertEqual(self.window.tree.selection(), ('history:0:reused',))
        self.assertIn('old text', self.window.detail.get('1.0', 'end-1c'))
        self.assertNotIn('new text', self.window.detail.get('1.0', 'end-1c'))
        self.window.retry()
        self.assertEqual(self.window.status_var.get(), '消息已过期。')

    def test_three_theme_changes_preserve_controller_history_selection_draft_and_preview(self):
        window = self.window
        self.emit(AppEvent('chat', 0, ChatEvent('selected', 0, '团队', '队友', '0:01', 'wait')))
        window.tree.selection_set('selected')
        window.input.insert('1.0', '请等我复活。')
        window.input.edit_modified(False)
        self.root.update_idletasks()
        window.vm.copy_text = 'Please wait for me to respawn.'
        window.vm.manual_provenance = '本机测试'
        window.vm.expected_job = 'keep-pending-job'
        window.render_manual_preview()
        original_controller, original_feed = window.controller, window.tree
        original_font = window.input.cget('font')
        rows = [(key, window.tree.item(key, 'values')) for key in window.tree.get_children()]
        pending = list(window.controller.pending.items)
        for theme in ('moonlight', 'graphite', 'mint'):
            with self.subTest(theme=theme):
                self.settings.color_theme = theme
                window.apply_settings(layout_only=True)
                self.root.update_idletasks()
                self.assertIs(window.controller, original_controller)
                self.assertIs(window.tree, original_feed)
                self.assertEqual(window.tree.body.cget('background'), theme_colors(theme)['surface'])
                self.assertEqual(window.tree.selection(), ('selected',))
                self.assertEqual(window.input.get('1.0', 'end-1c'), '请等我复活。')
                self.assertEqual(window.preview.get('1.0', 'end-1c'), 'Please wait for me to respawn.')
                self.assertEqual(window.vm.expected_job, 'keep-pending-job')
                self.assertEqual(window.input.cget('font'), original_font)
                self.assertEqual([(key, window.tree.item(key, 'values'))
                                  for key in window.tree.get_children()], rows)
                self.assertEqual(list(window.controller.pending.items), pending)

    def test_incoming_message_does_not_pull_reader_away_from_older_history(self):
        for index in range(35):
            self.window.controller.handle_source_event(AppEvent('chat', 0,
                ChatEvent('long' + str(index), 0, '团队', '队友', '00:01', 'older message ' * 10)))
        self.window.pump()
        self.root.update_idletasks()
        self.window.tree.yview_moveto(0)
        self.root.update_idletasks()
        self.emit(AppEvent('chat', 0, ChatEvent('latest', 0, '团队', '队友', '00:02', 'new message')))
        self.assertLess(self.window.tree.yview()[0], .03)
        self.assertNotEqual(self.window.tree.body.winfo_class(), 'Treeview')

    def test_feed_colors_and_translation_text_remain_readable_in_each_theme(self):
        for theme in ('moonlight', 'graphite', 'mint'):
            self.settings.color_theme = theme
            self.window.apply_settings(layout_only=True)
            colors = theme_colors(theme)
            self.assertTrue(hasattr(self.window.tree, 'body'), '消息区必须主动刷新自有颜色')
            self.assertEqual(self.window.tree.body.tag_cget('translation', 'foreground'), colors['text'])
            self.assertEqual(self.window.tree.body.tag_cget('original', 'foreground'), colors['muted'])
            self.assertTrue(ttk.Style(self.root).lookup('TEntry', 'bordercolor', ('focus',)))

    def test_minimum_window_keeps_message_feed_input_preview_and_actions_visible(self):
        window = self.window
        for mode, height in (('normal', 420), ('compact', 380)):
            for font_size in (12, 32):
                with self.subTest(mode=mode, font_size=font_size):
                    self.settings.mode, self.settings.font_size = mode, font_size
                    window.apply_settings(layout_only=True)
                    self.root.geometry(f'540x{height}+10000+10000')
                    self.root.update()
                    for widget in (window.tree, window.input, window.preview,
                                   *window.action_row.winfo_children()):
                        self.fixture.assert_inside(widget, self.root)
                        self.assertGreater(widget.winfo_height(), 20, str(widget))

    def test_phrase_management_actions_fit_the_smallest_window(self):
        self.root.geometry('540x420+10000+10000')
        self.window.notebook.select(self.window.phrase_panel)
        self.root.update()
        self.fixture.assert_inside(self.window.phrase_panel.listbox,self.root)
        self.assertGreater(self.window.phrase_panel.listbox.winfo_height(),20)
        pending = [self.window.phrase_panel]
        labels = []
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if widget.winfo_class() == 'TButton':
                self.fixture.assert_inside(widget, self.root)
                labels.append(widget.cget('text'))
        self.assertCountEqual(labels, ['使用', '新增', '编辑', '收藏', '删除', '导入', '导出所选'])

    def test_feed_list_compatibility_allocates_ids_and_clears_removed_selection(self):
        feed=self.window.tree
        feed.delete(*feed.get_children())
        first=feed.insert('','end',values=('raw','译文','已翻译'))
        second=feed.insert('','end',values=('hello','','原文'))
        self.assertNotEqual(first,second)
        feed.selection_set(first)
        feed.item(first,values=('raw','修正译文','已翻译'))
        self.root.update_idletasks()
        self.assertIn('修正译文',feed.body.get('1.0','end-1c'))
        feed.delete(first)
        self.root.update_idletasks()
        self.assertEqual(feed.selection(),())
        self.assertEqual(feed.get_children(),(second,))


if __name__ == '__main__':
    unittest.main()
