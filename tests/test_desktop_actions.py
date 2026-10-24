"""Pure fakes for renderer management actions; no Tk, personal files or network."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from hots_chat.config import Credentials
from hots_chat.credentials import SavedCredentials
from hots_chat.desktop_actions import (
    ACTION_COMMANDS, DesktopActionError, DesktopActions, validate_action,
)
from hots_chat.phrases import Phrase
from hots_chat.update_service import UpdateStatus
from hots_chat.window_settings import WindowSettings


def fake_window():
    service = NS(state='ready', busy=False,
                 current=SavedCredentials(Credentials('KEY-PRIVATE-1234', 'SECRET-PRIVATE-5678'), '123456789'),
                 connect=Mock(), cancel=Mock(), delete=Mock())
    source = NS(selected_pid=None, reconnect=Mock(),
                process_finder=Mock(return_value=[NS(pid=42, path='D:/game/Heroes.exe', build=98348)]))
    controller = NS(is_demo=False, cloud_configured=True, account_setup=service,
                    source=source, phrases=NS(save=Mock(), delete=Mock(), list=Mock(return_value=[])),
                    diagnostics=None)
    runtime = {'history': ['preserved'], 'editing': False}
    window = NS(settings=WindowSettings(), controller=controller, closed=False,
                update_service=NS(latest=UpdateStatus(), busy=False,
                                  check=Mock(return_value=True), download=Mock(return_value=True),
                                  cancel=Mock(return_value=True)),
                updates_can_install=False, install_update=Mock(return_value=True),
                quick_input=NS(cancel=Mock()), clear_draft=Mock(),
                phrase_panel=NS(changed=Mock()),
                overlay=NS(_editing=False, set_editing=Mock(), use_bottom_preset=Mock()),
                capture_settings_state=Mock(side_effect=lambda: deepcopy(runtime)),
                restore_settings_state=Mock(side_effect=lambda saved: (runtime.clear(), runtime.update(saved))),
                apply_settings=Mock(), save_settings=Mock(), start=Mock(), stop=Mock(),
                open_cloud_quota=Mock(return_value=True), runtime=runtime)
    return window


def phrase_value():
    return asdict(Phrase('phrase-1', '团战', '请等大招', {'en': 'Wait for our ult.'}, reviewed=True))


class ActionValidationTests(unittest.TestCase):
    def test_all_commands_reject_extra_fields_and_unknown_command(self):
        for command in ACTION_COMMANDS:
            with self.subTest(command=command), self.assertRaises(DesktopActionError):
                validate_action(command, {'unexpected': 'never echo this value'})
        with self.assertRaises(DesktopActionError):
            validate_action('arbitrary_python', {})

    def test_settings_reject_invalid_types_and_ranges_before_any_side_effect(self):
        invalid = [
            {'font_size': True}, {'font_size': '20'}, {'font_size': 20.5}, {'font_size': 9},
            {'font_size': 33}, {'font_size': 10 ** 1000}, {'overlay_font_size': 33},
            {'topmost': 1}, {'overlay_enabled': 'false'}, {'overlay_original': None},
            {'overlay_alpha': float('nan')}, {'overlay_alpha': float('inf')},
            {'overlay_alpha': .34}, {'overlay_width': 279}, {'overlay_width': 901},
            {'overlay_timeout': 4}, {'overlay_timeout': 121}, {'mode': 'unknown'},
            {'overlay_style': 'html'}, {'repeat_hotkey': 'S'}, {'repeat_hotkey': 8},
            {'hotkeys': []}, {'hotkeys': {'run_python': 'Ctrl+T'}},
            {'hotkeys': {'focus': True}}, {'hotkeys': {'focus': 'T'}},
            {'hotkeys': {'focus': 'Ctrl+Alt+T', 'copy': 'Alt+Ctrl+T'}},
            {'accessKeySecret': 'must-not-appear'}, {'alpha': .5}, {'locked': True},
        ]
        for values in invalid:
            window = fake_window()
            with self.subTest(values=values), self.assertRaises(DesktopActionError) as failure:
                DesktopActions(window).dispatch('settings_save', {'values': values})
            self.assertNotIn('must-not-appear', str(failure.exception))
            window.apply_settings.assert_not_called()
            window.save_settings.assert_not_called()
            window.capture_settings_state.assert_not_called()

    def test_empty_hotkeys_disable_and_reordered_modifiers_cannot_collide(self):
        result = validate_action('settings_save', {'values': {'hotkeys': {'focus': ' ', 'copy': ''},
                                                            'repeat_hotkey': ' f12 '}})
        self.assertEqual(result['values'], {'hotkeys': {}, 'repeat_hotkey': 'F12'})
        self.assertEqual(validate_action('settings_save', {'values': {'hotkeys': {}}})['values']['hotkeys'], {})

    def test_credentials_never_appear_in_validation_errors(self):
        for values in (
            {'accessKeyId': 'private!ID', 'accessKeySecret': 'private!SECRET'},
            {'accessKeyId': True, 'accessKeySecret': 'private!SECRET'},
            {'accessKeyId': 'A' * 257, 'accessKeySecret': 'private!SECRET'},
        ):
            with self.subTest(values=values), self.assertRaises(DesktopActionError) as failure:
                validate_action('account_connect', values)
            self.assertNotIn('private!', str(failure.exception))

    def test_pid_and_toggle_use_exact_types(self):
        for value in (True, 0, -1, 1.5, '42', 0x100000000):
            with self.subTest(value=value), self.assertRaises(DesktopActionError):
                validate_action('capture_select', {'pid': value})
        for value in (1, 'true', None):
            with self.subTest(value=value), self.assertRaises(DesktopActionError):
                validate_action('diagnostics_set', {'enabled': value})


class SettingsTransactionTests(unittest.TestCase):
    def test_valid_settings_save_deep_copies_and_snapshot_omits_private_fields(self):
        window = fake_window()
        actions = DesktopActions(window)
        payload = {'font_size': 24, 'topmost': True, 'mode': 'compact',
                   'hotkeys': {'focus': '', 'translate': 'Ctrl+Alt+R'},
                   'overlay_enabled': True, 'overlay_style': 'panel', 'overlay_font_size': 20,
                   'overlay_alpha': .6, 'overlay_width': 700, 'overlay_timeout': 30,
                   'overlay_original': True, 'repeat_hotkey': 'F12'}
        result = actions.dispatch('settings_save', {'values': payload})
        self.assertEqual(result['message'], '设置已保存。')
        self.assertEqual(window.settings.font_size, 24)
        self.assertEqual(window.settings.hotkeys, {'translate': 'Ctrl+Alt+R'})
        payload['hotkeys']['translate'] = 'Ctrl+Q'
        self.assertEqual(window.settings.hotkeys, {'translate': 'Ctrl+Alt+R'})
        window.apply_settings.assert_called_once_with()
        window.save_settings.assert_called_once_with()
        public = actions.snapshot()['preferences']
        public['hotkeys']['translate'] = 'Ctrl+X'
        self.assertEqual(window.settings.hotkeys['translate'], 'Ctrl+Alt+R')
        self.assertNotIn('alpha', public)
        self.assertNotIn('locked', public)

    def test_save_failure_rolls_back_all_fields_and_overlay_runtime(self):
        window = fake_window()
        original = deepcopy(asdict(window.settings))
        original_runtime = deepcopy(window.runtime)

        def mutate_layout():
            window.settings.x = 999
            window.settings.main_anchor = (.7, .8)
            window.settings.hotkeys['copy'] = 'Ctrl+C'
            window.runtime['history'].clear()
            window.runtime['editing'] = True

        window.apply_settings.side_effect = mutate_layout
        window.save_settings.side_effect = OSError('private-directory-and-request-input')
        with self.assertRaises(DesktopActionError) as failure:
            DesktopActions(window).dispatch('settings_save', {'values': {'font_size': 25, 'hotkeys': {}}})
        self.assertNotIn('private-directory', str(failure.exception))
        self.assertEqual(asdict(window.settings), original)
        self.assertEqual(window.runtime, original_runtime)
        self.assertEqual(window.apply_settings.call_count, 2)
        window.restore_settings_state.assert_called_once_with(original_runtime)

    def test_layout_application_failure_restores_even_if_rollback_layout_also_fails(self):
        window = fake_window()
        original = deepcopy(asdict(window.settings))
        window.apply_settings.side_effect = RuntimeError('unsafe backend exception')
        with self.assertRaises(DesktopActionError):
            DesktopActions(window).dispatch('settings_save', {'values': {'overlay_width': 800}})
        self.assertEqual(asdict(window.settings), original)
        window.save_settings.assert_not_called()
        window.restore_settings_state.assert_called_once()

    def test_bottom_preset_shares_transaction_and_rolls_back_on_failure(self):
        window = fake_window()
        window.settings.overlay_style = 'panel'
        original = deepcopy(asdict(window.settings))
        window.overlay.use_bottom_preset.side_effect = lambda: setattr(window.settings, 'overlay_placement', 'bottom')
        window.save_settings.side_effect = OSError('denied')
        with self.assertRaises(DesktopActionError):
            DesktopActions(window).dispatch('overlay_action', {'action': 'bottom'})
        self.assertEqual(asdict(window.settings), original)
        window.overlay.use_bottom_preset.assert_called_once_with()


class ManagementActionTests(unittest.TestCase):
    def test_update_snapshot_does_not_check_or_download_and_parses_notes(self):
        window = fake_window()
        window.update_service.latest = UpdateStatus('available', '有新版本',
            release=NS(version='2.1.0', notes='## 新版本\n\n- 清晰的 **标题**'))
        actions = DesktopActions(window)
        snapshot = actions.snapshot()['update']
        self.assertEqual(snapshot['version'], '2.1.0')
        self.assertEqual([block['kind'] for block in snapshot['noteBlocks']], ['heading', 'list'])
        window.update_service.check.assert_not_called()
        window.update_service.download.assert_not_called()
        window.install_update.assert_not_called()

    def test_update_check_download_cancel_only_run_after_explicit_command(self):
        window = fake_window()
        actions = DesktopActions(window)
        actions.dispatch('update_check')
        window.update_service.check.assert_called_once_with()
        window.update_service.download.assert_not_called()
        actions.dispatch('update_download')
        window.update_service.download.assert_called_once_with()
        with self.assertRaises(DesktopActionError):
            actions.dispatch('update_cancel')
        window.update_service.cancel.assert_not_called()
        window.update_service.latest = UpdateStatus('downloading')
        actions.dispatch('update_cancel')
        window.update_service.cancel.assert_called_once_with()

    def test_install_requires_verified_stage_and_supported_runtime(self):
        window = fake_window()
        actions = DesktopActions(window)
        staged = NS(archive_path=Path('D:/isolated-test/update.zip'))
        window.update_service.latest = UpdateStatus('downloaded', staged=staged)
        self.assertFalse(actions.snapshot()['update']['canInstall'])
        with self.assertRaises(DesktopActionError):
            actions.dispatch('update_install')
        window.install_update.assert_not_called()
        window.updates_can_install = True
        self.assertTrue(actions.snapshot()['update']['canInstall'])
        self.assertEqual(actions.snapshot()['update']['archivePath'], str(staged.archive_path))
        actions.dispatch('update_install')
        window.install_update.assert_called_once_with()

    def test_account_connect_hands_credentials_to_existing_service_only(self):
        window = fake_window()
        actions = DesktopActions(window)
        actions.dispatch('account_connect', {'accessKeyId': ' NEW-KEY-0000 ', 'accessKeySecret': ' NEW-SECRET-9999 '})
        window.controller.account_setup.connect.assert_called_once_with(Credentials('NEW-KEY-0000', 'NEW-SECRET-9999'))
        window.quick_input.cancel.assert_called_once_with()
        public = actions.snapshot()
        for private in ('NEW-KEY-0000', 'NEW-SECRET-9999', 'KEY-PRIVATE-1234', 'SECRET-PRIVATE-5678'):
            self.assertNotIn(private, str(public))
        self.assertEqual(public['account']['maskedKey'], '••••1234')
        self.assertEqual(public['account']['account'], '123456789')

    def test_account_busy_and_demo_guards_prevent_repeated_work(self):
        window = fake_window()
        actions = DesktopActions(window)
        window.controller.account_setup.busy = True
        with self.assertRaises(DesktopActionError):
            actions.dispatch('account_connect', {'accessKeyId': 'KEY', 'accessKeySecret': 'SECRET'})
        window.controller.account_setup.connect.assert_not_called()
        actions.dispatch('account_cancel')
        window.controller.account_setup.cancel.assert_called_once_with()
        window.controller.account_setup.busy = False
        window.controller.is_demo = True
        with self.assertRaises(DesktopActionError):
            actions.dispatch('account_delete')
        window.controller.account_setup.delete.assert_not_called()

    def test_account_cancel_refreshes_service_without_enabling_a_paused_saved_key(self):
        window = fake_window()
        window.cloud = NS(set=Mock())
        window.account_button = NS(configure=Mock())
        window.refresh_service_status = Mock()
        window.controller.paused = True
        window.controller.configuration_held = False
        actions = DesktopActions(window)
        actions.dispatch('account_cancel')
        window.cloud.set.assert_called_with(False)
        window.refresh_service_status.assert_called_once_with()
        window.controller.paused = False
        actions.dispatch('account_cancel')
        window.cloud.set.assert_called_with(True)
        self.assertEqual(window.refresh_service_status.call_count, 2)

    def test_account_service_exception_cannot_echo_key(self):
        window = fake_window()
        window.controller.account_setup.connect.side_effect = ValueError('KEY-INPUT SECRET-INPUT')
        with self.assertRaises(DesktopActionError) as failure:
            DesktopActions(window).dispatch('account_connect', {'accessKeyId': 'KEY-INPUT', 'accessKeySecret': 'SECRET-INPUT'})
        self.assertNotIn('KEY-INPUT', str(failure.exception))
        self.assertNotIn('SECRET-INPUT', str(failure.exception))

    def test_account_events_keep_existing_activation_and_only_retain_safe_message(self):
        from hots_chat.ui.main_window import MainWindow
        window = fake_window()
        window.enable_configured_cloud = Mock()
        window.account_button = NS(configure=Mock())
        window.credential_dialog = None
        window.status_var = NS(set=Mock())
        window.refresh_service_status = Mock()
        window.controller.account_setup.poll = Mock(return_value=[
            {'kind': 'ready', 'account': 'untrusted-account-field',
             'accessKeySecret': 'not-for-snapshot', 'translation': '你好'},
        ])
        MainWindow._account_events(window)
        window.enable_configured_cloud.assert_called_once_with()
        window.clear_draft.assert_called_once_with()
        self.assertEqual(window._desktop_account_message, '配置成功，翻译已启用。')
        self.assertNotIn('not-for-snapshot', str(DesktopActions(window).snapshot()))
        window.controller.account_setup.poll.return_value = [
            {'kind': 'error', 'message': '配置未完成，请检查网络后重试。',
             'accessKeyId': 'never-retain-event-fields'},
        ]
        MainWindow._account_events(window)
        self.assertEqual(window._desktop_account_message, '配置未完成，请检查网络后重试。')
        self.assertNotIn('never-retain-event-fields', str(DesktopActions(window).snapshot()))

    def test_process_selection_refreshes_then_rejects_stale_pid(self):
        window = fake_window()
        actions = DesktopActions(window)
        self.assertEqual(actions.snapshot()['processes'], [])
        window.controller.source.process_finder.assert_not_called()
        result = actions.dispatch('capture_processes')
        self.assertEqual(result['processes'][0]['pid'], 42)
        self.assertEqual(result['processes'][0]['build'], 98348)
        actions.dispatch('capture_select', {'pid': 42})
        self.assertEqual(window.controller.source.selected_pid, 42)
        window.controller.source.reconnect.assert_called_once_with()
        window.controller.source.process_finder.return_value = []
        with self.assertRaises(DesktopActionError):
            actions.dispatch('capture_select', {'pid': 42})
        self.assertEqual(window.controller.source.reconnect.call_count, 1)

    def test_capture_actions_delegate_and_never_create_dialogs(self):
        window = fake_window()
        actions = DesktopActions(window)
        for action in ('capture_start', 'capture_stop', 'capture_reconnect'):
            actions.dispatch(action)
        window.start.assert_called_once_with()
        window.stop.assert_called_once_with()
        window.controller.source.reconnect.assert_called_once_with()

    def test_phrase_save_and_remove_preserve_existing_validation(self):
        window = fake_window()
        actions = DesktopActions(window)
        value = phrase_value()
        actions.dispatch('phrase_save', {'phrase': value})
        window.controller.phrases.save.assert_called_once_with(Phrase(**value))
        window.controller.phrases.list.return_value = [Phrase(**value)]
        actions.dispatch('phrase_remove', {'id': value['id']})
        window.controller.phrases.delete.assert_called_once_with(value['id'])
        self.assertEqual(window.phrase_panel.changed.call_count, 2)
        with self.assertRaises(DesktopActionError):
            actions.dispatch('phrase_save', {'phrase': {**value, 'untrusted': 'x'}})
        with self.assertRaises(DesktopActionError):
            actions.dispatch('phrase_save', {'phrase': {**value, 'reviewed': 'yes'}})
        with self.assertRaises(DesktopActionError):
            actions.dispatch('phrase_save', {'phrase': {**value, 'translations': {}}})
        self.assertEqual(window.controller.phrases.save.call_count, 1)

    def test_overlay_edit_and_finish_are_explicit(self):
        window = fake_window()
        actions = DesktopActions(window)
        actions.dispatch('overlay_action', {'action': 'edit'})
        window.overlay.set_editing.assert_called_once_with(True)
        actions.dispatch('overlay_action', {'action': 'finish'})
        self.assertEqual(window.overlay.set_editing.call_args.args, (False,))
        with self.assertRaises(DesktopActionError):
            actions.dispatch('overlay_action', {'action': 'arbitrary'})

    def test_closed_window_does_not_start_work(self):
        window = fake_window()
        window.closed = True
        with self.assertRaises(DesktopActionError):
            DesktopActions(window).dispatch('update_check')
        window.update_service.check.assert_not_called()


class DiagnosticExportTests(unittest.TestCase):
    def test_export_runs_in_background_rejects_duplicate_and_publishes_safe_path(self):
        window = fake_window()
        entered, finish, done = threading.Event(), threading.Event(), threading.Event()
        destinations = []

        def export(destination):
            destinations.append(destination)
            entered.set()
            self.assertTrue(finish.wait(2))
            return destination

        recorder = NS(directory=Path('D:/isolated-test/logs'),
                      snapshot=Mock(return_value={'enabled': False, 'write_error': False,
                                                   'arbitrary_private': 'must-not-escape'}),
                      set_enabled=Mock(), export_report=Mock(side_effect=export))
        window.controller.diagnostics = recorder
        actions = DesktopActions(window)
        original_thread = threading.Thread
        threads = []

        def create_thread(**kwargs):
            target = kwargs['target']
            captured = dict(zip(target.__code__.co_freevars, (cell.cell_contents for cell in target.__closure__)))
            self.assertNotIn(window, captured.values())
            def worker():
                try:
                    target()
                finally:
                    done.set()
            thread = original_thread(target=worker, daemon=True)
            threads.append(thread)
            return thread

        with patch('hots_chat.desktop_actions.threading.Thread', side_effect=create_thread):
            actions.dispatch('diagnostics_export')
            self.assertTrue(entered.wait(1))
            self.assertTrue(actions.snapshot()['diagnostics']['busy'])
            with self.assertRaises(DesktopActionError):
                actions.dispatch('diagnostics_export')
            finish.set()
            self.assertTrue(done.wait(2))
        report = actions.snapshot()['diagnostics']
        self.assertFalse(report['busy'])
        self.assertEqual(report['exportPath'], str(destinations[0]))
        self.assertEqual(destinations[0].parent, recorder.directory)
        self.assertNotIn('arbitrary_private', report)
        actions.dispatch('diagnostics_set', {'enabled': True})
        recorder.set_enabled.assert_called_once_with(True)

    def test_missing_recorder_and_export_error_are_actionable_and_safe(self):
        window = fake_window()
        actions = DesktopActions(window)
        self.assertFalse(actions.snapshot()['diagnostics']['available'])
        with self.assertRaises(DesktopActionError):
            actions.dispatch('diagnostics_export')
        window.controller.diagnostics = NS(directory=Path('D:/isolated-test/logs'),
            snapshot=lambda: {}, export_report=Mock(side_effect=OSError('SECRET-provider-request')))
        with patch('hots_chat.desktop_actions.threading.Thread') as thread:
            thread.return_value.start.side_effect = lambda: thread.call_args.kwargs['target']()
            actions.dispatch('diagnostics_export')
        result = actions.snapshot()['diagnostics']
        self.assertFalse(result['busy'])
        self.assertEqual(result['exportPath'], '')
        self.assertNotIn('SECRET-provider-request', result['message'])

    def test_close_prevents_completed_export_publishing_new_state(self):
        window = fake_window()
        window.controller.diagnostics = NS(directory=Path('D:/isolated-test/logs'),
            snapshot=lambda: {}, export_report=lambda destination: destination)
        actions = DesktopActions(window)
        with patch('hots_chat.desktop_actions.threading.Thread') as thread:
            actions.dispatch('diagnostics_export')
            worker = thread.call_args.kwargs['target']
            actions.close()
            worker()
        self.assertEqual(actions.snapshot()['diagnostics']['exportPath'], '')
        self.assertFalse(actions.snapshot()['diagnostics']['busy'])
        with self.assertRaises(DesktopActionError):
            actions.dispatch('diagnostics_export')


if __name__ == '__main__':
    unittest.main()
