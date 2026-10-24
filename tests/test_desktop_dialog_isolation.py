"""Dialog ownership regressions using fake widgets only; no Tk interpreter or UI."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hots_chat.desktop_runtime import TkRuntime
from hots_chat.ui.main_window import MainWindow
from hots_chat.ui.quick_input import QuickInput
from tests.test_desktop_runtime import fake_window


class FakeRoot:
    def __init__(self):
        self.children = []
        self.idle_callbacks = []
        self._state = 'withdrawn'

    def winfo_children(self):
        return list(self.children)

    def after_idle(self, callback):
        self.idle_callbacks.append(callback)
        return len(self.idle_callbacks)

    def run_idle(self):
        callbacks, self.idle_callbacks = self.idle_callbacks, []
        for callback in callbacks:
            callback()

    def state(self):
        return self._state

    def deiconify(self):
        self._state = 'normal'

    def withdraw(self):
        self._state = 'withdrawn'


class FakeToplevel:
    def __init__(self, parent):
        self.parent = parent
        self.children = []
        self._state = 'withdrawn'
        self._exists = True
        self.deiconify_count = 0
        self.lift_count = 0
        self.transient_calls = []
        parent.children.append(self)

    def winfo_children(self):
        return list(self.children)

    def winfo_exists(self):
        return self._exists

    def state(self):
        return self._state

    def transient(self, owner):
        self.transient_calls.append(owner)

    def deiconify(self):
        self.deiconify_count += 1
        self._state = 'normal'

    def lift(self):
        self.lift_count += 1

    def withdraw(self):
        self._state = 'withdrawn'

    def destroy(self):
        self._exists = False
        self.parent.children.remove(self)


class DesktopDialogIsolationTests(unittest.TestCase):
    def setUp(self):
        self.toplevel_patch = patch('tkinter.Toplevel', FakeToplevel)
        self.toplevel_patch.start()
        self.addCleanup(self.toplevel_patch.stop)
        self.window = fake_window()
        self.window.root = FakeRoot()
        self.runtime = TkRuntime(self.window)

    def open_settings(self, callback):
        self.window.window_options = Mock(side_effect=callback)
        self.assertEqual(self.runtime.dispatch('open_settings'), {'opened': True})
        self.window.root.run_idle()

    def assert_foregrounded(self, dialog):
        self.assertEqual(dialog.state(), 'normal')
        self.assertEqual(dialog.transient_calls, [''])
        self.assertEqual(dialog.deiconify_count, 1)
        self.assertEqual(dialog.lift_count, 1)
        self.assertEqual(self.window.root.state(), 'withdrawn')

    def test_settings_does_not_resurrect_quick_input_created_after_runtime(self):
        quick_window = FakeToplevel(self.window.root)
        quick_window.deiconify()
        quick_window.withdraw()
        self.window.quick_input.window = quick_window
        settings_dialog = FakeToplevel(self.window.root)

        self.open_settings(lambda: settings_dialog)

        self.assertEqual(quick_window.state(), 'withdrawn')
        self.assertEqual(quick_window.deiconify_count, 1)
        self.assertFalse(self.window.quick_input._visible)
        self.window.quick_input.cancel.assert_not_called()
        self.assert_foregrounded(settings_dialog)

    def test_new_hidden_window_created_while_opening_settings_stays_hidden(self):
        created = {}

        def create_settings():
            created['quick'] = FakeToplevel(self.window.root)
            self.window.quick_input.window = created['quick']
            created['settings'] = FakeToplevel(self.window.root)
            return created['settings']

        self.open_settings(create_settings)

        self.assertEqual(created['quick'].state(), 'withdrawn')
        self.assertEqual(created['quick'].deiconify_count, 0)
        self.assertEqual(created['quick'].lift_count, 0)
        self.assertEqual(created['quick'].transient_calls, [])
        self.assertFalse(self.window.quick_input._visible)
        self.assert_foregrounded(created['settings'])

    def test_repeated_settings_open_does_not_reveal_unrelated_hidden_dialogs(self):
        quick_window = FakeToplevel(self.window.root)
        unrelated_dialog = FakeToplevel(self.window.root)
        settings_dialog = FakeToplevel(self.window.root)
        self.open_settings(lambda: settings_dialog)
        settings_dialog.withdraw()

        self.open_settings(lambda: settings_dialog)

        self.assertEqual(settings_dialog.state(), 'normal')
        self.assertEqual(settings_dialog.deiconify_count, 2)
        self.assertEqual(quick_window.state(), 'withdrawn')
        self.assertEqual(quick_window.deiconify_count, 0)
        self.assertEqual(unrelated_dialog.state(), 'withdrawn')
        self.assertEqual(unrelated_dialog.deiconify_count, 0)
        self.assertFalse(self.window.quick_input._visible)

    def test_phrase_editor_foregrounds_only_the_returned_window(self):
        settings_dialog = FakeToplevel(self.window.root)
        self.open_settings(lambda: settings_dialog)
        settings_dialog.withdraw()
        phrase_dialog = FakeToplevel(self.window.root)
        self.window.phrase_panel = SimpleNamespace(edit=Mock(return_value=phrase_dialog))

        self.assertEqual(self.runtime.dispatch('phrase_edit'), {'opened': True})
        self.window.root.run_idle()

        self.window.phrase_panel.edit.assert_called_once_with(None)
        self.assertEqual(settings_dialog.state(), 'withdrawn')
        self.assertEqual(settings_dialog.deiconify_count, 1)
        self.assert_foregrounded(phrase_dialog)

    def test_callback_without_dialog_does_not_reveal_hidden_windows(self):
        quick_window = FakeToplevel(self.window.root)
        self.open_settings(lambda: None)

        self.assertEqual(quick_window.state(), 'withdrawn')
        self.assertEqual(quick_window.deiconify_count, 0)
        self.assertFalse(self.runtime.snapshot()['nativeDialogOpen'])

    def test_snapshot_does_not_count_quick_input_as_a_native_dialog(self):
        quick_window = FakeToplevel(self.window.root)
        self.window.quick_input.window = quick_window
        self.assertFalse(self.runtime.snapshot()['nativeDialogOpen'])
        quick_window.deiconify()
        self.window.quick_input._visible = True

        self.assertFalse(self.runtime.snapshot()['nativeDialogOpen'])

    def test_snapshot_counts_pending_and_registered_dialog_and_removes_destroyed_one(self):
        settings_dialog = FakeToplevel(self.window.root)
        self.window.window_options = Mock(return_value=settings_dialog)
        self.runtime.dispatch('open_settings')
        self.assertTrue(self.runtime.snapshot()['nativeDialogOpen'])
        self.window.root.run_idle()
        self.assertTrue(self.runtime.snapshot()['nativeDialogOpen'])

        settings_dialog.destroy()

        self.assertFalse(self.runtime.snapshot()['nativeDialogOpen'])
        self.assertNotIn(settings_dialog, self.runtime._dialog_windows)

    def test_quick_input_cancel_hides_an_externally_shown_window_once_without_focus(self):
        native = Mock()
        visibility = Mock()
        quick = QuickInput(self.window.root, self.window.controller, self.window.settings,
                           native=native, on_visibility_changed=visibility)
        quick.window = FakeToplevel(self.window.root)
        quick.window.deiconify()
        self.assertFalse(quick._visible)

        self.assertEqual(quick.cancel(), 'break')

        self.assertEqual(quick.window.state(), 'withdrawn')
        self.assertFalse(quick._visible)
        native.restore_foreground.assert_not_called()
        visibility.assert_not_called()
        self.assertEqual(quick.cancel(), 'break')
        self.assertEqual(quick.window.state(), 'withdrawn')
        native.restore_foreground.assert_not_called()

    def test_main_window_reuses_the_live_settings_dialog_on_repeated_requests(self):
        owner = SimpleNamespace(root=self.window.root, closed=False, settings_dialog=None,
                                cancel_loot=Mock(), cancel_repeat=Mock())
        dialog = FakeToplevel(owner.root)
        with patch('hots_chat.ui.settings_dialog.SettingsDialog',
                   return_value=SimpleNamespace(dialog=dialog)) as constructor:
            first = MainWindow.window_options(owner)
            second = MainWindow.window_options(owner)

        constructor.assert_called_once_with(owner)
        self.assertIs(first, dialog)
        self.assertIs(second, dialog)
        self.assertIs(owner.settings_dialog.dialog, dialog)

    def test_main_window_creates_a_new_settings_dialog_after_the_old_one_is_destroyed(self):
        owner = SimpleNamespace(root=self.window.root, closed=False, settings_dialog=None,
                                cancel_loot=Mock(), cancel_repeat=Mock())
        first = FakeToplevel(owner.root)
        second = FakeToplevel(owner.root)
        with patch('hots_chat.ui.settings_dialog.SettingsDialog',
                   side_effect=[SimpleNamespace(dialog=first),
                                SimpleNamespace(dialog=second)]) as constructor:
            self.assertIs(MainWindow.window_options(owner), first)
            first.destroy()
            self.assertIs(MainWindow.window_options(owner), second)

        self.assertEqual(constructor.call_count, 2)
        self.assertIs(owner.settings_dialog.dialog, second)

    def test_main_window_restores_the_existing_minimized_settings_dialog(self):
        dialog = FakeToplevel(self.window.root)
        dialog._state = 'iconic'
        owner = SimpleNamespace(root=self.window.root, closed=False,
                                settings_dialog=SimpleNamespace(dialog=dialog),
                                cancel_loot=Mock(), cancel_repeat=Mock())
        with patch('hots_chat.ui.settings_dialog.SettingsDialog') as constructor:
            self.assertIs(MainWindow.window_options(owner), dialog)

        constructor.assert_not_called()
        self.assertEqual(dialog.state(), 'normal')
        self.assertEqual(dialog.lift_count, 1)


if __name__ == '__main__':
    unittest.main()
