"""圆角外壳保留原生编辑、选区与紧凑布局，不操作真实游戏。"""
import tkinter as tk
import unittest

from tests import test_adaptive_display_flow as adaptive


class RoundedReplyTests(unittest.TestCase):
    def test_reply_texts_keep_content_selection_and_geometry_when_restyled(self):
        fixture = adaptive.AdaptiveDisplayFlowTests(methodName='runTest')
        with fixture.app() as (root, window, settings, screen, native):
            original = window.input, window.preview
            draft = '等我复活后一起打团。 같이 가요!'
            window.input.insert('1.0', draft)
            window.input.edit_modified(False)
            window.input.tag_add('sel', '1.0', '1.3')
            window.set_text(window.preview, 'Wait for me before the next fight.')
            for theme in ('moonlight', 'graphite', 'mint'):
                with self.subTest(theme=theme):
                    settings.color_theme = theme
                    window.apply_settings(layout_only=True)
                    root.geometry('540x420+10000+10000')
                    root.update()
                    self.assertEqual((window.input, window.preview), original)
                    self.assertEqual(window.input.get('1.0', 'end-1c'), draft)
                    self.assertEqual(tuple(map(str, window.input.tag_ranges('sel'))), ('1.0', '1.3'))
                    self.assertEqual(window.preview.get('1.0', 'end-1c'),
                                     'Wait for me before the next fight.')
                    self.assertEqual(window.preview.cget('state'), 'disabled')
                    for text in original:
                        self.assertIsInstance(text, tk.Text)
                        self.assertEqual(int(text.cget('highlightthickness')), 0)
                        self.assertEqual(int(text.cget('borderwidth')), 0)
                        fixture.assert_inside(text, root)
                        self.assertGreater(text.winfo_height(), 12)


if __name__ == '__main__':
    unittest.main()
