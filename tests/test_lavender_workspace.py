"""选定原型的结构与操作回归；仅使用临时数据和屏幕外测试窗口。"""
from tkinter import ttk
import unittest

from tests.test_adaptive_display_flow import AdaptiveDisplayFlowTests


class LavenderWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.fixture=AdaptiveDisplayFlowTests(methodName='runTest')
        self.context=self.fixture.app()
        self.root,self.window,self.settings,*_=self.context.__enter__()
        self.addCleanup(self.context.__exit__,None,None,None)

    def test_reply_columns_and_language_choices_keep_real_draft_flow(self):
        w=self.window
        self.assertEqual(self.settings.color_theme,'lavender')
        self.assertLess(w.input.winfo_rootx(),w.preview.winfo_rootx())
        self.assertEqual(w.input.winfo_rooty(),w.preview.winfo_rooty())
        w.input.insert('1.0','等我一下')
        w.input.edit_modified(False)
        w.vm.copy_text='stale preview'
        w.render_manual_preview()
        w.language_choices.winfo_children()[0].invoke()
        self.assertEqual(w.target.get(),'en')
        self.assertEqual(w.settings.target_language,'en')
        self.assertEqual(w.input.get('1.0','end-1c'),'等我一下')
        self.assertEqual(w.preview.get('1.0','end-1c'),'')
        self.assertIn('英语',w.preview_title.cget('text'))

    def test_helper_pages_restore_chat_controls_and_pending_draft(self):
        w=self.window
        w.input.insert('1.0','保留输入')
        w.input.edit_modified(False)
        for page in ('loot','repeat','talent','broadcast'):
            w.select_section(page)
            self.root.update_idletasks()
            self.assertFalse(w.reply_panel.winfo_ismapped())
            self.assertFalse(w.channel_row.winfo_ismapped())
        w.select_section('chat')
        self.root.update_idletasks()
        self.assertTrue(w.channel_row.winfo_ismapped())
        self.assertTrue(w.reply_panel.winfo_ismapped())
        self.assertEqual(w.input.get('1.0','end-1c'),'保留输入')

    def test_icons_reuse_fixed_pool_and_release_on_close(self):
        w=self.window
        # Populate each navigation state before measuring the fixed pool.
        for page in w.navigation:w.select_section(page)
        w.select_section('chat')
        pool=w.icons
        before={key:str(image) for key,image in pool.images.items()}
        for theme in ('graphite','mint','lavender'):
            self.settings.color_theme=theme
            w.apply_settings(layout_only=True)
            self.root.update_idletasks()
            self.assertEqual({key:str(image) for key,image in pool.images.items()},before)
        self.assertEqual(ttk.Style(self.root).lookup('Sidebar.Selected.TButton','foreground'),'#7547F5')
        w.close()
        self.assertEqual(pool.images,{})
        self.assertIsNone(pool.root)

    def test_short_wide_window_keeps_settings_and_update_reachable(self):
        self.root.geometry('1080x420+10000+10000')
        self.root.update()
        w=self.window
        self.assertFalse(w.sidebar.winfo_ismapped())
        self.assertTrue(w.settings_button.winfo_ismapped())
        self.assertTrue(w.update_button.winfo_ismapped())
        self.assertTrue(ttk.Style(self.root).layout('Main.TNotebook.Tab'))
        self.fixture.assert_inside(w.settings_button,self.root)


if __name__=='__main__':unittest.main()
