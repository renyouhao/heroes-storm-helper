"""字幕与快捷输入的互斥显示；仅用隔离剪贴板、FakeNative 和屏幕外 Tk。"""
import tkinter as tk
import unittest
from unittest.mock import patch

from hots_chat.layout import DisplayMetrics
from hots_chat.ui.quick_input import QuickInput
from hots_chat.ui.theme import apply_theme
from hots_chat.window_settings import WindowSettings
from tests import test_quick_input as fixtures


class SubtitleQuickInputTests(unittest.TestCase):
    submit = fixtures.QuickInputTests.submit
    result = fixtures.QuickInputTests.result

    def setUp(self):
        fixtures.QuickInputTests.setUp(self)
        self.visibility = []
        self.window.on_visibility_changed = self.visibility.append

    def test_constructor_accepts_optional_visibility_callback_as_keyword(self):
        try:
            quick = QuickInput(self.root, self.controller, self.settings,
                               native=self.native, clipboard=self.clipboard.append,
                               on_visibility_changed=self.visibility.append)
        except TypeError as failure:
            self.fail('快捷输入缺少可选可见状态回调：' + str(failure))
        self.assertFalse(quick._visible)
        self.assertEqual(self.visibility, [])

    def test_open_and_cancel_report_only_actual_visibility_changes(self):
        observed = []

        def changed(visible):
            observed.append((visible, self.window.window.visible))

        self.window.on_visibility_changed = changed
        self.window.open()
        self.window.text.set('尚未写完')
        self.window.open()
        self.window.cancel()
        self.window.cancel()
        self.assertEqual(observed, [(True, True), (False, False)])
        self.assertEqual(self.window.text.get(), '尚未写完')
        self.assertEqual(self.native.restored, [99])

    def test_copy_keeps_input_active_until_success_hint_timer_hides_it(self):
        job = self.submit()
        self.window.handle_event(self.result(job))
        self.assertEqual(self.visibility, [True])
        self.assertTrue(self.window.window.visible)
        self.assertEqual(self.clipboard, ['Retreat!'])
        self.assertEqual(self.native.restored, [99])
        self.root.expire()
        self.assertFalse(self.window.window.visible)
        self.assertEqual(self.visibility, [True, False])
        self.window.handle_event(self.result(job, 'late'))
        self.assertEqual(self.clipboard, ['Retreat!'])

    def test_failed_focus_restore_keeps_subtitle_suppressed_and_input_visible(self):
        job = self.submit()
        with patch.object(self.native, 'restore_foreground', return_value=False):
            self.window.handle_event(self.result(job))
        self.root.expire()
        self.assertTrue(self.window.window.visible)
        self.assertTrue(self.window._visible)
        self.assertEqual(self.visibility, [True])
        self.assertIn('手动', self.window.status.get())

    def test_cancel_and_reopen_reject_old_result_without_extra_notifications(self):
        old = self.submit()
        self.window.cancel()
        self.window.open()
        self.window.handle_event(self.result(old, 'stale'))
        self.assertEqual(self.clipboard, [])
        self.assertEqual(self.visibility, [True, False, True])
        self.assertEqual(self.window.text.get(), '撤退')

    def test_reopen_cancels_previous_hide_timer_without_flickering_subtitle(self):
        job = self.submit()
        self.window.handle_event(self.result(job))
        self.window.open()
        self.root.expire()
        self.assertTrue(self.window.window.visible)
        self.assertEqual(self.visibility, [True])

    def test_initialization_failure_does_not_report_a_visible_window(self):
        with patch.object(self.native, 'attach_ime', side_effect=OSError('IME unavailable')):
            with self.assertRaises(OSError):
                self.window.open()
        self.assertFalse(self.window._visible)
        self.assertIsNone(self.window.window)
        self.assertEqual(self.visibility, [])
        self.window.open()
        self.assertEqual(self.visibility, [True])
        self.assertEqual(self.native.attached, [101])

    def test_open_error_after_display_rolls_back_visibility_and_preserves_draft(self):
        self.window.open()
        self.window.text.set('保留草稿')
        with patch.object(self.native, 'activate', side_effect=OSError('activation denied')):
            with self.assertRaises(OSError):
                self.window.open()
        self.assertFalse(self.window.window.visible)
        self.assertFalse(self.window._visible)
        self.assertEqual(self.visibility, [True, False])
        self.assertEqual(self.window.text.get(), '保留草稿')
        self.window.open()
        self.assertEqual(self.visibility, [True, False, True])

    def test_failed_first_display_does_not_report_a_visible_window(self):
        build = self.window._create_window

        def failed_deiconify():
            raise tk.TclError('display failed')

        def create():
            build()
            self.window.window.deiconify = failed_deiconify

        self.window._create_window = create
        with self.assertRaises(tk.TclError):
            self.window.open()
        self.assertFalse(self.window.window.visible)
        self.assertFalse(self.window._visible)
        self.assertEqual(self.visibility, [])

    def test_callback_tk_or_os_error_cannot_interrupt_focus_draft_or_cleanup(self):
        for failure in (tk.TclError('overlay gone'), OSError('native overlay gone')):
            with self.subTest(failure=type(failure).__name__):
                self.visibility.clear()

                def changed(visible):
                    self.visibility.append(visible)
                    raise failure

                self.window.on_visibility_changed = changed
                self.window.open()
                self.window.text.set('保留草稿')
                self.window.cancel()
                self.assertEqual(self.window.text.get(), '保留草稿')
                self.assertEqual(self.visibility, [True, False])
                self.assertFalse(self.window._visible)
        self.window.close()
        self.assertTrue(self.native.detached)

    def test_close_reports_hidden_once_and_invalidates_pending_result(self):
        job = self.submit()
        self.window.close()
        self.window.close()
        self.window.handle_event(self.result(job))
        self.assertEqual(self.visibility, [True, False])
        self.assertEqual(self.clipboard, [])
        self.assertTrue(self.native.detached)
        self.assertTrue(self.window.window.destroyed)
        self.assertEqual(self.root.callbacks, {})


class SubtitleQuickInputTkTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        apply_theme(self.root)
        self.native = fixtures.Native()
        self.visibility = []
        self.clipboard = []
        self.quick = QuickInput(self.root, fixtures.Controller(), WindowSettings(),
                               native=self.native, clipboard=self.clipboard.append,
                               metrics=DisplayMetrics((10000, 10000, 11920, 11080)))
        self.quick.on_visibility_changed = self.visibility.append
        self.addCleanup(self.quick.close)

    def test_external_withdraw_reports_hidden_and_reopen_restores_visibility(self):
        self.quick.open()
        self.root.update()
        self.quick.text.set('屏幕外草稿')
        self.quick.window.withdraw()
        self.root.update()
        self.assertFalse(self.quick._visible)
        self.assertEqual(self.visibility, [True, False])
        self.quick.open()
        self.root.update()
        self.assertTrue(self.quick._visible)
        self.assertEqual(self.visibility, [True, False, True])
        self.assertEqual(self.quick.text.get(), '屏幕外草稿')

    def test_child_unmap_does_not_mark_whole_input_hidden(self):
        self.quick.open()
        self.root.update()
        self.quick.entry.pack_forget()
        self.root.update()
        self.assertTrue(self.quick._visible)
        self.assertEqual(self.visibility, [True])

    def test_cancel_and_immediate_reopen_ignore_stale_unmap_event(self):
        self.quick.open()
        self.root.update()
        self.quick.cancel()
        self.quick.open()
        self.root.update()
        self.assertTrue(self.quick._visible)
        self.assertEqual(self.visibility, [True, False, True])

    def test_external_destroy_reports_hidden_and_close_still_cleans_up(self):
        self.quick.open()
        self.root.update()
        self.quick.window.destroy()
        self.root.update()
        self.assertFalse(self.quick._visible)
        self.assertEqual(self.visibility, [True, False])
        self.quick.close()
        self.assertTrue(self.native.detached)


if __name__ == '__main__':
    unittest.main()
