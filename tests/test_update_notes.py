"""发布说明文本与布局契约；仅使用 fake 控件，不创建 Tk 或访问网络。"""
from contextlib import ExitStack
from types import SimpleNamespace
import re
import unittest
from unittest.mock import Mock, patch

from hots_chat.layout import DisplayMetrics
from hots_chat.ui.release_notes import inline_spans, parse_release_notes
from hots_chat.ui.update_dialog import UpdateDialog, render_release_notes
from hots_chat.update_service import UpdateStatus
from hots_chat.window_settings import WindowSettings


def block_text(block):
    return ''.join(span.text for span in block.spans)


class FakeFont:
    def measure(self, text):
        return sum(14 if ord(char) > 127 else 7 for char in text)

    def metrics(self, name):
        return 24


class FakeWidget:
    def __init__(self, *args, **kwargs):
        self.options = kwargs
        self.rows = {}
        self.bindings = {}

    def configure(self, **kwargs):
        self.options.update(kwargs)

    def cget(self, name):
        return self.options[name]

    def grid(self, **kwargs):
        self.grid_options = kwargs

    def grid_remove(self):
        pass

    def pack(self, **kwargs):
        pass

    def columnconfigure(self, *args, **kwargs):
        pass

    def rowconfigure(self, index, **kwargs):
        self.rows[index] = kwargs

    def bind(self, event, callback):
        self.bindings[event] = callback

    def geometry(self, value):
        self.geometry_value = value

    def minsize(self, *values):
        self.minimum = values

    def state(self, values):
        self.state_values = values

    def __setitem__(self, key, value):
        self.options[key] = value

    def after(self, *args):
        return 'timer'

    def after_cancel(self, timer):
        pass

    def title(self, value):
        pass

    def transient(self, owner):
        pass

    def protocol(self, *args):
        pass

    def withdraw(self):
        pass

    def deiconify(self):
        pass

    def destroy(self):
        pass

    def set(self, *args):
        pass

    def yview(self, *args):
        pass


class FakeText(FakeWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.content = ''
        self.tags = {}
        self.inserts = []

    def tag_configure(self, name, **kwargs):
        self.tags[name] = kwargs

    def delete(self, *args):
        self.content = ''
        self.inserts.clear()

    def insert(self, index, content, tags=()):
        self.content += content
        self.inserts.append((content, tags))

    def yview_moveto(self, value):
        self.scroll = value


class FakeVar:
    def __init__(self, *args, value=''):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class ReleaseNotesParsingTests(unittest.TestCase):
    def test_release_headings_and_soft_wrapped_items_are_semantic_blocks(self):
        source = ('## v2.0\n\n- 主界面迁移到 React +\nWebView2，统一交互。\n'
                  '- 兼容游戏 build 98348 的全部 0–15\n模型解码分支。')
        blocks = parse_release_notes(source)
        self.assertEqual([block.kind for block in blocks], ['heading', 'list', 'list'])
        self.assertEqual(blocks[0].level, 2)
        self.assertEqual(block_text(blocks[0]), 'v2.0')
        self.assertEqual(block_text(blocks[1]), '主界面迁移到 React + WebView2，统一交互。')
        self.assertEqual(block_text(blocks[2]), '兼容游戏 build 98348 的全部 0–15 模型解码分支。')

    def test_soft_lines_merge_without_splitting_chinese_words_or_english_words(self):
        blocks = parse_release_notes('风暴\n英雄中文换行\n\nEnglish release\nnotes wrap cleanly.')
        self.assertEqual([block_text(block) for block in blocks],
                         ['风暴英雄中文换行', 'English release notes wrap cleanly.'])

    def test_blank_lines_and_new_items_keep_paragraph_boundaries(self):
        blocks = parse_release_notes('首段\n\n第二段\n\n- 第一项\n  继续该项\n- 第二项\n\n尾段')
        self.assertEqual([block_text(block) for block in blocks],
                         ['首段', '第二段', '第一项继续该项', '第二项', '尾段'])
        self.assertEqual([block.kind for block in blocks],
                         ['paragraph', 'paragraph', 'list', 'list', 'paragraph'])

    def test_ordered_and_nested_items_keep_their_markers(self):
        blocks = parse_release_notes('1. 第一项\n  - 子项\n12) 末项')
        self.assertEqual([(block.marker, block.depth) for block in blocks],
                         [('1.', 0), ('•', 1), ('12)', 0)])

    def test_inline_formatting_is_readable_without_executing_or_fetching_content(self):
        spans = inline_spans('**重要**、`Ctrl+T`、*提示*，[详情](https://example.test/notes)')
        self.assertEqual(''.join(span.text for span in spans),
                         '重要、Ctrl+T、提示，详情（https://example.test/notes）')
        self.assertIn(('strong',), [span.styles for span in spans])
        self.assertIn(('code',), [span.styles for span in spans])
        self.assertIn(('emphasis',), [span.styles for span in spans])
        self.assertEqual(block_text(parse_release_notes('<script>alert(1)</script>')[0]),
                         '<script>alert(1)</script>')

    def test_code_blocks_keep_newlines_and_do_not_parse_markdown_inside(self):
        blocks = parse_release_notes('```text\n# literal\n  **literal**\n```\n\n正常段落')
        self.assertEqual(blocks[0].kind, 'code')
        self.assertEqual(block_text(blocks[0]), '# literal\n  **literal**')
        self.assertEqual(block_text(blocks[1]), '正常段落')

    def test_heading_closing_markers_do_not_remove_csharp_or_unmatched_inline_text(self):
        blocks = parse_release_notes('# 支持 C#\n## 修复 ###\n\n未结束的 **标记与 snake_case')
        self.assertEqual([block_text(block) for block in blocks],
                         ['支持 C#', '修复', '未结束的 **标记与 snake_case'])


class ReleaseNotesRenderingTests(unittest.TestCase):
    def render(self, source):
        widget = FakeText()
        render_release_notes(widget, source, font_size=12,
                             colors={'surface': '#fff'}, measure=FakeFont().measure,
                             line_height=FakeFont().metrics('linespace'))
        return widget

    def test_render_uses_heading_tags_and_measured_hanging_list_margins(self):
        widget = self.render('## v2.0\n\n- **修复**快捷输入\n窗口关闭。\n12. 另一个条目')
        self.assertEqual(widget.content, 'v2.0\n•  修复快捷输入窗口关闭。\n12.  另一个条目\n')
        self.assertIn(('v2.0', ('heading_2',)), widget.inserts)
        self.assertIn(('修复', ('list_0_28', 'strong')), widget.inserts)
        self.assertEqual(widget.tags['list_0_28']['lmargin2'], FakeFont().measure('•  '))
        self.assertEqual(widget.tags['list_0_35']['lmargin2'], FakeFont().measure('12.  '))
        self.assertGreater(widget.tags['paragraph']['spacing3'], widget.tags['paragraph']['spacing2'])
        self.assertEqual(widget.options['state'], 'disabled')

    def test_long_unbroken_tokens_and_mixed_text_are_never_manually_wrapped_or_truncated(self):
        token = 'A' * 500
        widget = self.render('- ' + token + '中文 English 混排。')
        self.assertEqual(widget.content, '•  ' + token + '中文 English 混排。\n')
        self.assertEqual(widget.content.count('\n'), 1)
        self.assertEqual(widget.tags['list_0_28']['lmargin1'], 0)
        self.assertEqual(widget.tags['paragraph']['lmargin2'], 0)


class UpdateDialogLayoutTests(unittest.TestCase):
    def dialog(self, metrics=DisplayMetrics((0, 0, 1280, 720))):
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.multiple('hots_chat.ui.update_dialog.tk',
                                          Toplevel=FakeWidget, Text=FakeText, StringVar=FakeVar))
        stack.enter_context(patch.multiple('hots_chat.ui.update_dialog.ttk', Frame=FakeWidget,
                                          Label=FakeWidget, Progressbar=FakeWidget, Scrollbar=FakeWidget,
                                          Button=FakeWidget, Entry=FakeWidget))
        stack.enter_context(patch('hots_chat.ui.update_dialog.tkfont.Font', return_value=FakeFont()))
        stack.enter_context(patch('hots_chat.ui.update_dialog.style_native_window'))
        service = SimpleNamespace(latest=UpdateStatus(), busy=False, cancel=Mock())
        owner = SimpleNamespace(root=object(), _layout_metrics=metrics, settings=WindowSettings(),
                                update_service=service, updates_can_install=True, install_update=Mock())
        dialog = UpdateDialog(owner, show=False)
        self.addCleanup(dialog.close)
        return dialog, service

    def test_notes_fill_resizable_space_and_wrapping_hints_follow_available_width(self):
        dialog, _ = self.dialog()
        self.assertEqual(dialog.notes.options['wrap'], 'word')
        self.assertEqual(dialog.notes.options['width'], 1)
        self.assertEqual(dialog.notes.grid_options['sticky'], 'nsew')
        self.assertEqual(dialog.dialog.rows[3]['weight'], 1)
        for width in (420, 760, 1100):
            dialog._resize(SimpleNamespace(widget=dialog.dialog, width=width))
            for label in (dialog.detail_label, dialog.install_hint, dialog.archive_hint):
                self.assertEqual(label.options['wraplength'], width - 36)
        dialog._resize(SimpleNamespace(widget=dialog.notes, width=99))
        self.assertEqual(dialog.detail_label.options['wraplength'], 1064)

    def test_initial_geometry_fits_small_work_area_and_dpi_scale(self):
        for metrics in (DisplayMetrics((200, 50, 840, 530), 1),
                        DisplayMetrics((-1500, 0, -300, 900), 1.5)):
            with self.subTest(metrics=metrics):
                dialog, _ = self.dialog(metrics)
                width, height, x, y = map(int, re.fullmatch(r'(\d+)x(\d+)\+(-?\d+)\+(-?\d+)',
                                                            dialog.dialog.geometry_value).groups())
                left, top, right, bottom = metrics.work_area
                self.assertGreaterEqual(x, left)
                self.assertGreaterEqual(y, top)
                self.assertLessEqual(x + width, right)
                self.assertLessEqual(y + height, bottom)
                self.assertLessEqual(dialog.dialog.minimum[0], width)
                self.assertLessEqual(dialog.dialog.minimum[1], height)

    def test_refresh_formats_notes_only_when_source_changes(self):
        dialog, service = self.dialog()
        service.latest = UpdateStatus('available', release=SimpleNamespace(version='2.0', notes='## 修复\n\n- `X` 一次关闭'))
        dialog.refresh()
        self.assertEqual(dialog.notes.content, '修复\n•  X 一次关闭\n')
        dialog.notes.scroll = .5
        dialog.refresh()
        self.assertEqual(dialog.notes.scroll, .5)


if __name__ == '__main__':
    unittest.main()
