from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hots_chat.ui.main_window import MainWindow
from hots_chat.update_service import UpdateService, UpdateStatus
from hots_chat.app import main


class UpdateMainTests(unittest.TestCase):
    def test_normal_start_checks_once_without_opening_update_ui(self):
        calls = []
        service = UpdateService(check=lambda version: calls.append(version) or
                                SimpleNamespace(available=True, release=object()))
        self.addCleanup(service.close)
        window = MainWindow.__new__(MainWindow)
        window.closed = False
        window._update_check_enabled = True
        window.update_service = service
        window.controller = SimpleNamespace(start_receiving=Mock())
        window.open_updates = Mock()
        window.start(); service.thread.join(2); window.start()
        self.assertEqual(len(calls), 1)
        self.assertTrue(service.has_available_update)
        window.open_updates.assert_not_called()

    def test_disabled_demo_and_closed_start_do_not_check(self):
        for closed, enabled in ((False, False), (True, True)):
            with self.subTest(closed=closed, enabled=enabled):
                window = MainWindow.__new__(MainWindow)
                window.closed = closed; window._update_check_enabled = enabled
                window.update_service = SimpleNamespace(check_on_startup=Mock())
                window.controller = SimpleNamespace(start_receiving=Mock())
                window.start()
                window.update_service.check_on_startup.assert_not_called()

    def test_create_app_defaults_update_check_off_for_isolated_self_checks(self):
        from hots_chat.ui.main_window import create_app
        with patch('hots_chat.ui.main_window.MainWindow') as constructor:
            create_app('root', 'controller', 'settings')
        self.assertFalse(constructor.call_args.kwargs['enable_update_check'])

    def test_legacy_entry_enables_only_normal_startup_checks(self):
        from hots_chat.window_settings import WindowSettings
        for demo in (False, True):
            with self.subTest(demo=demo), \
                    patch('hots_chat.display_runtime.enable_process_dpi_awareness'), \
                    patch('hots_chat.display_runtime.TkDisplayRuntime'), \
                    patch('hots_chat.app.SingleInstance') as mutex, \
                    patch('hots_chat.app.build_controller', return_value=SimpleNamespace(close=Mock())), \
                    patch('hots_chat.diagnostics.DiagnosticRecorder'), \
                    patch('hots_chat.diagnostic_hooks.DiagnosticHooks'), \
                    patch('hots_chat.window_settings.WindowSettings.load', return_value=WindowSettings()), \
                    patch('tkinter.Tk'), patch('hots_chat.ui.main_window.create_app') as create:
                mutex.return_value.acquire.return_value = True
                self.assertEqual(main(['--legacy-ui'] + (['--demo'] if demo else [])), 0)
                self.assertEqual(create.call_args.kwargs['enable_update_check'], not demo)

    def window(self):
        window = MainWindow.__new__(MainWindow)
        window.closed = False; window._update_installing = False
        window.update_service = SimpleNamespace(install=Mock(return_value=True))
        window.update_dialog = SimpleNamespace(detail=SimpleNamespace(set=Mock()))
        window.cancel_loot = Mock(); window.cancel_repeat = Mock()
        window._sync_talent_gate = Mock()
        window.quick_input = SimpleNamespace(_visible=True,cancel=Mock())
        return window

    def test_install_fences_actions_before_background_prepare(self):
        window = self.window()
        def start(operation):
            self.assertTrue(window._update_installing)
            window.cancel_loot.assert_called_once()
            window.cancel_repeat.assert_called_once()
            window.quick_input.cancel.assert_called_once()
            window._sync_talent_gate.assert_called()
            return True
        window.update_service.install.side_effect = start
        with patch('sys.frozen',True,create=True):window.install_update()

    def test_source_mode_never_prepares_or_exits_application(self):
        window = self.window()
        with patch('sys.frozen',False,create=True):window.install_update()
        window.update_service.install.assert_not_called()
        self.assertFalse(window._update_installing)
        self.assertIn('源码',window.update_dialog.detail.set.call_args.args[0])

    def test_update_helper_mode_does_not_create_main_ui_or_mutex(self):
        with patch('hots_chat.update_install.apply_update',return_value=0) as apply, \
                patch('hots_chat.app.SingleInstance',side_effect=AssertionError('must not take app mutex')):
            self.assertEqual(main(['--apply-update','fake-plan.json']),0)
        self.assertEqual(str(apply.call_args.args[0]),'fake-plan.json')

    def test_pending_update_blocks_new_input_actions(self):
        window = self.window(); window._update_installing = True
        window._prepare_action('loot')
        window.open_quick_input()
        window.cancel_loot.assert_not_called()
        window.quick_input.cancel.assert_not_called()
