"""快捷输入离线回归；布局仅创建屏幕外控件，不触碰游戏或真实剪贴板。"""
from types import SimpleNamespace
import tkinter as tk
from tkinter import font as tkfont, ttk
import unittest
from unittest.mock import patch

from hots_chat.layout import DisplayMetrics
from hots_chat.ui.theme import apply_theme
from hots_chat.window_settings import WindowSettings
from tests import test_quick_input as fixtures


class Action:
    def __init__(self):
        self.disabled = False
        self.destroyed = False

    def state(self, values):
        if self.destroyed:
            raise tk.TclError('button has been destroyed')
        if 'disabled' in values:
            self.disabled = True
        if '!disabled' in values:
            self.disabled = False


class StatusLabel:
    def __init__(self):
        self.options = {'text': ''}

    def cget(self, name):
        return self.options.get(name, '')

    def configure(self, **values):
        self.options.update(values)


class LayoutWindow(fixtures.Window):
    def __init__(self, handle):
        super().__init__(handle)
        self.geometries = []

    def update_idletasks(self):
        pass

    def winfo_reqheight(self):
        return 180

    def winfo_height(self):
        return 120

    def geometry(self, value):
        self.geometries.append(value)

    def configure(self, **values):
        self.options = values


class NexusQuickInputTests(unittest.TestCase):
    setUp = fixtures.QuickInputTests.setUp
    submit = fixtures.QuickInputTests.submit
    result = fixtures.QuickInputTests.result

    def test_repeated_enter_while_pending_does_not_submit_a_second_job(self):
        self.submit()
        self.window._on_return_press()
        self.window._on_return_release()
        self.assertEqual(len(self.controller.submitted), 1)

    def test_busy_action_reenables_when_draft_changes(self):
        self.window.submit_button = Action()
        self.submit()
        self.assertTrue(self.window.submit_button.disabled)
        self.window.text.set('集合')
        self.assertFalse(self.window.submit_button.disabled)
        self.window._submit()
        self.assertEqual(self.controller.submitted[-1][:2], ('集合', 'ko'))

    def test_error_reenables_action_for_retry(self):
        self.window.submit_button = Action()
        job = self.submit()
        self.window.handle_event(SimpleNamespace(kind='error', generation=7,
                                 payload={'job_id': job, 'error': '请重试'}))
        self.assertFalse(self.window.submit_button.disabled)
        self.window._submit()
        self.assertEqual(len(self.controller.submitted), 2)

    def test_changing_target_returns_focus_to_entry(self):
        self.window.open()
        self.window.target.set('en')
        before = self.window.entry.focused
        self.window._target_changed()
        self.assertEqual(self.window.entry.focused, before + 1)
        self.assertEqual(self.saved_targets, ['en'])

    def test_reopen_selects_successful_draft_for_the_next_sentence(self):
        job = self.submit()
        self.window.handle_event(self.result(job))
        self.root.expire()
        self.window.open()
        self.assertEqual(self.window.text.get(), '撤退')
        self.assertEqual(self.window.entry.selections, [(0, 'end')])

    def test_success_shows_translation_preview_and_paste_hint_in_status(self):
        job = self.submit()
        self.window.handle_event(self.result(job, 'Fall back!'))
        self.assertIn('Fall back!', self.window.status.get())
        self.assertIn('Ctrl+V', self.window.status.get())
        self.assertEqual(self.clipboard, ['Fall back!'])

    def test_cancel_keeps_draft_without_selecting_it_on_reopen(self):
        self.window.open()
        self.window.text.set('尚未写完')
        self.window.cancel()
        self.window.open()
        self.assertEqual(self.window.text.get(), '尚未写完')
        self.assertEqual(self.window.entry.selections, [])

    def test_failed_foreground_restore_keeps_copy_confirmation_visible(self):
        job = self.submit()
        with patch.object(self.native, 'restore_foreground', return_value=False):
            self.window.handle_event(self.result(job))
        self.root.expire()
        self.assertEqual(self.clipboard, ['Retreat!'])
        self.assertTrue(self.window.window.visible)
        self.assertIn('手动', self.window.status.get())

    def test_foreground_error_keeps_copy_confirmation_visible(self):
        job = self.submit()
        with patch.object(self.native, 'restore_foreground', side_effect=OSError('not allowed')):
            self.window.handle_event(self.result(job))
        self.root.expire()
        self.assertTrue(self.window.window.visible)
        self.assertIn('已复制', self.window.status.get())

    def test_failed_initialization_can_close_without_touching_destroyed_action(self):
        build = self.window._create_window

        def create():
            build()
            action = Action()
            self.window.submit_button = action
            old_destroy = self.window.window.destroy

            def destroy():
                action.destroyed = True
                old_destroy()

            self.window.window.destroy = destroy

        self.window._create_window = create
        with patch.object(self.native, 'attach_ime', side_effect=OSError('IME init failed')):
            with self.assertRaises(OSError):
                self.window.open()
        try:
            self.window.close()
        except tk.TclError as failure:
            self.fail('初始化回滚后关闭仍访问了已销毁的按钮：' + str(failure))
        self.assertTrue(self.window._closed)

    def test_action_during_ime_composition_waits_for_candidate_confirmation(self):
        self.window.open()
        self.window.text.set('撤')
        self.native.composing = True
        self.window._submit()
        self.assertEqual(self.controller.submitted, [])
        self.assertIn('选词', self.window.status.get())

    def test_long_status_is_explicitly_shortened_without_moving_window(self):
        self.window.open()
        self.window.window = LayoutWindow(100)
        self.window._metrics = DisplayMetrics((0, 0, 1920, 1080))
        self.window.status_label = StatusLabel()
        self.window._status_font = SimpleNamespace(measure=lambda value: len(value) * 6)
        self.window._status_width = 144
        original = '翻译失败，详细原因：\n' + '配置不可用。' * 100
        self.window.status.set(original)
        self.window._status_changed()
        displayed = self.window.status_label.cget('text')
        self.assertEqual(self.window.status.get(), original)
        self.assertEqual(self.window.window.geometries, [])
        self.assertLessEqual(len(displayed) * 6, 144)
        self.assertIn('…', displayed)
        self.assertIn('重试', displayed)

    def test_every_reopen_refreshes_position_from_provider(self):
        positions = []
        self.window._position_provider = lambda: (80, 240)
        self.window.refresh_layout = lambda *args, **kwargs: positions.append(self.window._position_provider())
        self.window.open()
        self.window.cancel()
        self.window._position_provider = lambda: (80, 320)
        self.window.open()
        self.assertEqual(positions, [(80, 240), (80, 320)])

    def test_layout_tracks_new_provider_coordinates_with_unchanged_metrics(self):
        self.window.open()
        self.window.window = LayoutWindow(100)
        self.window.body = StatusLabel()
        self.window.status_label = StatusLabel()
        self.window._entry_font = SimpleNamespace(cget=lambda name: 12)
        self.window._status_font = SimpleNamespace(measure=lambda value: len(value) * 6)
        self.window._position_provider = lambda: (80, 240)
        metrics = DisplayMetrics((0, 0, 1920, 1080))
        with patch('hots_chat.ui.quick_input.style_native_window', return_value=False):
            fixtures.QuickInput.refresh_layout(self.window, metrics)
            self.window._position_provider = lambda: (80, 320)
            self.assertTrue(fixtures.QuickInput.refresh_layout(self.window, metrics))
        self.assertEqual(self.window.window.geometries, ['640x180+80+240', '640x180+80+320'])


class NexusQuickInputLayoutTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        apply_theme(self.root)

    def create_offscreen(self, size, scale):
        self.root.tk.call('tk', 'scaling', scale * 96 / 72)
        quick = fixtures.QuickInput(self.root, SimpleNamespace(generation=1),
                                   WindowSettings(font_size=size),
                                   native=SimpleNamespace(detach_ime=lambda: None),
                                   clipboard=lambda text: None,
                                   metrics=DisplayMetrics((10000, 10000, 11920, 11080), scale))
        self.addCleanup(quick.close)
        quick._create_window()
        quick.window.deiconify()
        self.root.update()
        return quick

    def descendants(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self.descendants(child)

    def test_input_target_and_action_share_one_row_with_readable_entry_at_largest_font(self):
        for size, scale in ((12, 1), (32, 1), (32, 2)):
            with self.subTest(size=size, scale=scale):
                quick = self.create_offscreen(size, scale)
                widgets = list(self.descendants(quick.body))
                targets = [widget for widget in widgets if isinstance(widget, (ttk.Combobox, ttk.Radiobutton))]
                self.assertTrue(targets)
                center = quick.entry.winfo_rooty() + quick.entry.winfo_height() / 2
                for widget in targets + [quick.submit_button]:
                    self.assertAlmostEqual(widget.winfo_rooty() + widget.winfo_height() / 2,
                                           center, delta=2)
                entry_font = tkfont.Font(root=self.root, font=quick.entry.cget('font'))
                self.assertGreaterEqual(quick.entry.winfo_width(), entry_font.measure('准备撤退集合'))
                self.assertGreaterEqual(quick.entry.winfo_height(), quick.entry.winfo_reqheight())
                quick.close()

    def test_client_height_contains_only_operation_status_and_shortcuts(self):
        for size, scale in ((12, 1), (32, 1), (32, 2)):
            with self.subTest(size=size, scale=scale):
                quick = self.create_offscreen(size, scale)
                labels = [widget for widget in self.descendants(quick.body) if isinstance(widget, ttk.Label)]
                shortcuts = next(widget for widget in labels if 'Esc' in str(widget.cget('text')))
                operation_height = max(quick.entry.winfo_height(), quick.submit_button.winfo_height())
                three_rows = (round(20 * scale) + operation_height + quick.status_label.winfo_reqheight()
                              + shortcuts.winfo_reqheight() + round(9 * scale))
                self.assertLessEqual(quick.window.winfo_height(), three_rows + 2)
                self.assertIn('Ctrl+Tab', shortcuts.cget('text'))
                quick.close()

    def test_readonly_target_displays_names_while_preserving_language_codes(self):
        quick = self.create_offscreen(32, 1)
        combos = [widget for widget in self.descendants(quick.body) if isinstance(widget, ttk.Combobox)]
        self.assertEqual(len(combos), 1)
        combo = combos[0]
        self.assertEqual(str(combo.cget('state')), 'readonly')
        self.assertEqual(combo.get(), '韩语')
        quick.target.set('en')
        self.assertEqual(combo.get(), '英语')
        combo.set('韩语')
        combo.event_generate('<<ComboboxSelected>>')
        self.root.update()
        self.assertEqual(quick.target.get(), 'ko')
        self.assertEqual(quick.settings.target_language, 'ko')

    def test_theme_only_refresh_recolors_existing_window_without_losing_draft(self):
        quick = self.create_offscreen(12, 1)
        quick.text.set('先打 Boss，再集合')
        original_window = quick.window
        quick.settings.color_theme = 'ice'
        from hots_chat.ui.theme import overlay_colors
        palette = dict(overlay_colors('moonlight'), background='#14202B')
        with patch('hots_chat.ui.quick_input.overlay_colors', return_value=palette), \
                patch('hots_chat.ui.quick_input.style_native_window', return_value=False) as native_style:
            refreshed = quick.refresh_layout()
        self.assertTrue(refreshed)
        self.assertEqual(quick.window.cget('background'), '#14202B')
        self.assertIs(quick.window, original_window)
        self.assertEqual(quick.text.get(), '先打 Boss，再集合')
        native_style.assert_called_once_with(original_window, palette)


if __name__ == '__main__':
    unittest.main()
