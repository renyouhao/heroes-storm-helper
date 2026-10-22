"""隐藏的真实 Tk 控件 + 配置服务/账本/DPAPI；所有云边界均为受控离线服务。"""
from datetime import datetime, timezone
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from hots_chat.app import build_controller
from hots_chat.config import ConfigError, Credentials
from hots_chat.credentials import CredentialStore
from hots_chat.models import AppEvent, ChatEvent, TranslationResult
from hots_chat.paths import AppPaths
from hots_chat.ui.main_window import create_app
from hots_chat.window_settings import WindowSettings


NOW = datetime(2026, 10, 20, 4, tzinfo=timezone.utc)
ACCOUNT = '1968132000123456'
OTHER_ACCOUNT = '1968132000654321'
KEYS = Credentials('acceptance-old-id', 'acceptance-old-secret')
NEXT_KEYS = Credentials('acceptance-next-id', 'acceptance-next-secret')


class OfflineSource:
    selected_pid = None

    def start(self, emit):
        self.emit = emit

    def stop(self):
        pass

    def process_finder(self):
        return []


class ControlledIdentity:
    def __init__(self):
        self.calls = []
        self.account = ACCOUNT
        self.failure = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def resolve(self, credentials):
        self.calls.append(credentials)
        self.entered.set()
        if not self.release.wait(3):
            raise TimeoutError('offline identity gate timed out')
        if self.failure:
            raise self.failure
        return self.account


class ControlledEngine:
    provider_id = 'configuration-acceptance'
    config_version = '1'

    def __init__(self, credentials):
        self.credentials = credentials
        self.calls = []
        self.closed = False
        self.failure = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def translate(self, job):
        self.calls.append(job)
        self.entered.set()
        if not self.release.wait(3):
            raise TimeoutError('offline translation gate timed out')
        if self.failure:
            raise self.failure
        return TranslationResult(job.job_id, '离线受控译文：' + job.text,
                                 job.source, None, 0)

    def close(self):
        self.closed = True


@unittest.skipUnless(os.name == 'nt', 'Windows Tk and current-user DPAPI integration')
class CredentialDialogIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hots-config-acceptance-')
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name)
        self.paths = AppPaths(folder / '.env', folder / 'usage.sqlite3',
                              folder / 'language.sqlite3', folder / 'preferences.json')
        # Any accidental use of a real network boundary fails the test.
        self.dns = patch.object(socket, 'getaddrinfo', side_effect=AssertionError('network forbidden'))
        self.connect = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        self.dns.start(); self.connect.start()
        self.addCleanup(self.dns.stop); self.addCleanup(self.connect.stop)
        self.controller = build_controller(self.paths)
        self.controller.clock = lambda: NOW
        self.controller.source = OfflineSource()
        self.service = self.controller.account_setup
        self.identity = ControlledIdentity()
        self.service.identity = self.identity
        self.engines = []
        self.next_engine = None

        def factory(credentials):
            engine = self.next_engine or ControlledEngine(credentials)
            self.next_engine = None
            self.engines.append(engine)
            return engine

        self.service.engine_factory = factory
        self.root = tk.Tk()
        self.root.withdraw()
        # Keep every test-owned popup hidden before its first geometry update.
        original_toplevel = tk.Toplevel

        def hidden_toplevel(*args, **kwargs):
            widget = original_toplevel(*args, **kwargs)
            widget.withdraw()
            return widget

        self.hidden = patch.object(tk, 'Toplevel', side_effect=hidden_toplevel)
        self.hidden.start()
        self.addCleanup(self.hidden.stop)
        self.window = create_app(self.root, self.controller,
                                 WindowSettings(hotkeys={}, overlay_enabled=False))
        self.addCleanup(self.cleanup_application)
        self.root.update()
        self.window.pump()
        self.sequence = 0

    def cleanup_application(self):
        self.identity.release.set()
        for engine in self.engines:
            engine.release.set()
        if self.next_engine:
            self.next_engine.release.set()
        if self.service._thread:
            self.service._thread.join(4)
        self.window.close()

    def spin_until(self, predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.root.update()
            self.window.pump()
            if predicate():
                return
            time.sleep(.005)
        self.fail('隔离配置状态未在期限内完成。')

    def open_dialog(self):
        self.window.configure_account()
        return self.window.credential_dialog

    def fill_credentials(self, dialog, credentials=KEYS):
        dialog.key.set(credentials.access_key_id)
        dialog.secret.set(credentials.access_key_secret)

    def save(self, dialog, credentials=KEYS, external=None):
        self.fill_credentials(dialog, credentials)
        if external is not None:
            dialog.baseline.set(str(external))
        dialog.connect_button.invoke()
        self.spin_until(lambda: not self.service.busy and self.service.state == 'ready'
                        and '配置成功' in dialog.status.get())

    def test_single_click_with_default_zero_enables_translation(self):
        dialog = self.open_dialog()
        self.assertEqual(dialog.baseline.get(), '0')
        self.assertEqual(str(dialog.baseline_entry['state']), 'normal')
        self.save(dialog)
        self.assertEqual(self.identity.calls, [KEYS])
        self.assertEqual(len(self.engines), 1)
        self.assertEqual(len(self.engines[0].calls), 1)
        self.assertEqual(self.controller.ledger.snapshot(ACCOUNT, NOW).external_used, 0)
        self.assertTrue(self.controller.cloud_configured)
        self.assertFalse(self.controller.paused)
        self.assertFalse(self.controller.configuration_held)
        self.assertEqual(self.reserved(), 2)
        dialog.cancel()
        self.assert_translates()

    def test_identified_progress_keeps_controls_disabled_until_ready(self):
        candidate = ControlledEngine(KEYS)
        candidate.release.clear()
        self.next_engine = candidate
        dialog = self.open_dialog()
        self.fill_credentials(dialog)
        dialog.connect_button.invoke()
        self.assertTrue(candidate.entered.wait(2))
        self.window.pump()
        self.assertTrue(self.service.busy)
        for widget in (dialog.connect_button, dialog.baseline_entry,
                       dialog.key_entry, dialog.secret_entry,
                       dialog.import_button, dialog.delete_button):
            self.assertEqual(str(widget['state']), 'disabled')
        self.assertTrue(self.controller.configuration_held)
        self.assertFalse(self.paths.credentials_file.exists())
        candidate.release.set()
        self.spin_until(lambda: not self.service.busy and '配置成功' in dialog.status.get())
        self.assertEqual(str(dialog.connect_button['state']), 'normal')

    def configured(self, external=0):
        dialog = self.open_dialog()
        self.save(dialog, external=external)
        dialog.cancel()
        self.assertFalse(self.controller.paused)
        return self.controller.engine

    def raw_message(self, text='hello acceptance'):
        self.sequence += 1
        message_id = f'acceptance-{self.sequence}'
        self.controller._source_emit(AppEvent('chat', 0,
            ChatEvent(message_id, 0, '团队', '离线测试', '00:01', text)))
        self.window.pump()
        self.assertEqual(self.window.tree.item(message_id, 'values')[0], text)
        return message_id

    def assert_translates(self):
        message_id = self.raw_message()
        self.spin_until(lambda: bool(self.window.vm.messages[message_id].engine_translation))

    def reserved(self, account=ACCOUNT):
        return self.controller.ledger.snapshot(account, NOW).local_reserved

    def test_skip_fresh_configuration_keeps_raw_messages_without_network_or_credentials(self):
        dialog = self.open_dialog()
        self.assertEqual(str(dialog.connect_button['state']), 'normal')
        dialog.cancel()
        message_id = self.raw_message()
        self.assertEqual(self.window.vm.messages[message_id].engine_translation, '')
        self.assertFalse(self.controller.cloud_configured)
        self.assertTrue(self.controller.paused)
        self.assertEqual(self.identity.calls, [])
        self.assertEqual(self.engines, [])
        self.assertIsNone(CredentialStore(self.paths.credentials_file).load())

    def test_invalid_input_stays_in_dialog_without_starting_identity(self):
        dialog = self.open_dialog()
        self.fill_credentials(dialog, Credentials('invalid key with spaces', 'placeholder'))
        dialog.connect_button.invoke()
        self.assertIn('有效', dialog.status.get())
        self.assertEqual(self.identity.calls, [])
        self.assertFalse(self.controller.configuration_held)
        self.assertEqual(str(dialog.connect_button['state']), 'normal')

    def test_wrong_key_response_is_redacted_and_filled_key_can_be_retried(self):
        dialog = self.open_dialog()
        self.identity.failure = ConfigError('provider echo: ' + KEYS.access_key_secret)
        self.fill_credentials(dialog)
        with patch.object(dialog, 'handle', wraps=dialog.handle) as received:
            dialog.connect_button.invoke()
            self.spin_until(lambda: not self.service.busy and '配置未完成' in dialog.status.get())
        self.assertEqual(dialog.secret.get(), KEYS.access_key_secret)
        self.assertEqual(dialog.secret_entry['show'], '●')
        self.assertNotIn(KEYS.access_key_secret, str(received.call_args_list))
        self.assertIn('身份验证', dialog.status.get())
        self.assertNotIn(KEYS.access_key_secret, dialog.status.get())
        self.assertEqual(str(dialog.connect_button['state']), 'normal')
        self.identity.failure = None
        self.save(dialog, NEXT_KEYS)
        self.assertEqual(self.identity.calls, [KEYS, NEXT_KEYS])
        self.assertEqual(CredentialStore(self.paths.credentials_file).load().credentials, NEXT_KEYS)
        self.assertTrue(self.controller.cloud_configured)
        self.assertFalse(self.controller.paused)

    def test_identity_network_failure_does_not_replace_saved_credentials(self):
        previous = self.configured()
        encrypted = self.paths.credentials_file.read_bytes()
        self.identity.failure = TimeoutError('provider echo: ' + NEXT_KEYS.access_key_secret)
        dialog = self.open_dialog()
        self.fill_credentials(dialog, NEXT_KEYS)
        dialog.connect_button.invoke()
        self.spin_until(lambda: not self.service.busy and '配置未完成' in dialog.status.get())
        self.assertIn('身份验证', dialog.status.get())
        self.assertNotIn(NEXT_KEYS.access_key_secret, dialog.status.get())
        dialog.cancel()
        self.assertEqual(self.paths.credentials_file.read_bytes(), encrypted)
        self.assertIs(self.controller.engine, previous)
        self.assert_translates()

    def test_cancel_during_identity_preserves_old_key_and_rejects_late_success(self):
        previous = self.configured()
        encrypted = self.paths.credentials_file.read_bytes()
        self.identity.entered.clear(); self.identity.release.clear()
        self.identity.account = OTHER_ACCOUNT
        dialog = self.open_dialog()
        self.fill_credentials(dialog, NEXT_KEYS)
        dialog.connect_button.invoke()
        self.assertTrue(self.identity.entered.wait(2))
        self.assertTrue(self.controller.configuration_held)
        self.raw_message('raw while configuring')
        dialog.cancel()
        self.identity.release.set()
        self.spin_until(lambda: not self.service.busy)
        self.assertIs(self.controller.engine, previous)
        self.assertEqual(self.controller.account, ACCOUNT)
        self.assertEqual(self.paths.credentials_file.read_bytes(), encrypted)
        self.assertFalse(self.controller.configuration_held)
        self.assertFalse(self.controller.paused)
        self.assert_translates()

    def test_empty_negative_and_noninteger_external_usage_block_before_identity(self):
        dialog = self.open_dialog()
        self.fill_credentials(dialog)
        for value in ('', '-1', '1.5', 'unknown'):
            with self.subTest(value=value):
                dialog.baseline.set(value)
                dialog.connect_button.invoke()
                self.assertIn('非负整数', dialog.status.get())
                self.assertEqual(self.identity.calls, [])
                self.assertEqual(self.engines, [])
                self.assertEqual(self.reserved(), 0)
                self.assertFalse(self.controller.configuration_held)
                self.assertFalse(self.paths.credentials_file.exists())

    def test_translation_network_failure_keeps_old_key_and_accounts_for_attempt(self):
        previous = self.configured()
        encrypted = self.paths.credentials_file.read_bytes()
        failed = ControlledEngine(NEXT_KEYS)
        failed.failure = TimeoutError('provider echo: ' + NEXT_KEYS.access_key_secret)
        self.next_engine = failed
        dialog = self.open_dialog()
        self.fill_credentials(dialog, NEXT_KEYS)
        dialog.connect_button.invoke()
        self.spin_until(lambda: not self.service.busy and '配置未完成' in dialog.status.get())
        self.assertIn('翻译测试', dialog.status.get())
        self.assertEqual(self.reserved(), 4)
        self.assertTrue(failed.closed)
        self.assertEqual(str(dialog.connect_button['state']), 'normal')
        self.assertNotIn(NEXT_KEYS.access_key_secret, dialog.status.get())
        self.assertEqual(self.paths.credentials_file.read_bytes(), encrypted)
        dialog.cancel()
        self.assertIs(self.controller.engine, previous)
        self.assert_translates()

    def test_cancel_during_translation_keeps_usage_and_discards_late_success(self):
        previous = self.configured()
        encrypted = self.paths.credentials_file.read_bytes()
        candidate = ControlledEngine(NEXT_KEYS); candidate.release.clear()
        self.next_engine = candidate
        dialog = self.open_dialog()
        self.fill_credentials(dialog, NEXT_KEYS)
        dialog.connect_button.invoke()
        self.assertTrue(candidate.entered.wait(2))
        self.assertEqual(self.reserved(), 4)
        dialog.cancel(); candidate.release.set()
        self.spin_until(lambda: not self.service.busy)
        self.assertTrue(candidate.closed)
        self.assertEqual(self.paths.credentials_file.read_bytes(), encrypted)
        self.assertIs(self.controller.engine, previous)
        self.assertEqual(self.reserved(), 4)
        self.assertFalse(self.controller.paused)
        self.assert_translates()

    def test_success_saves_real_dpapi_and_survives_a_fresh_controller_load(self):
        dialog = self.open_dialog(); self.save(dialog)
        self.assertEqual(dialog.key.get(), '')
        self.assertEqual(dialog.secret.get(), '')
        self.assertEqual(dialog.secret_entry['show'], '●')
        data = self.paths.credentials_file.read_bytes()
        self.assertNotIn(KEYS.access_key_id.encode(), data)
        self.assertNotIn(KEYS.access_key_secret.encode(), data)
        reloaded = build_controller(self.paths)
        try:
            self.assertTrue(reloaded.cloud_configured)
            self.assertEqual(reloaded.account, ACCOUNT)
            self.assertEqual(reloaded.account_setup.current.credentials, KEYS)
        finally:
            reloaded.account_setup.close(); reloaded.close()
        dialog.cancel()
        self.assertEqual(self.reserved(), 2)
        self.assert_translates()

    def test_cancel_during_client_initialization_does_not_start_test_translation(self):
        previous = self.configured()
        encrypted = self.paths.credentials_file.read_bytes()
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        candidate = ControlledEngine(NEXT_KEYS)

        def initialize(credentials):
            self.engines.append(candidate)
            entered.set()
            if not release.wait(3):
                raise TimeoutError('offline initialization gate timed out')
            return candidate

        self.service.engine_factory = initialize
        dialog = self.open_dialog()
        self.fill_credentials(dialog, NEXT_KEYS)
        dialog.connect_button.invoke()
        self.assertTrue(entered.wait(2))
        dialog.cancel(); release.set()
        self.spin_until(lambda: not self.service.busy)
        self.assertEqual(candidate.calls, [], '取消发生在客户端初始化中，未开始的测试不应继续。')
        self.assertTrue(candidate.closed)
        self.assertIs(self.controller.engine, previous)
        self.assertEqual(self.paths.credentials_file.read_bytes(), encrypted)
        self.assertEqual(self.reserved(), 4, '已预留的测试用量按保守规则继续保留。')
        self.assertFalse(self.controller.paused)
        self.assert_translates()

    def test_same_account_default_keeps_external_baseline_and_prior_usage(self):
        previous = self.configured(external=57)
        dialog = self.open_dialog()
        self.assertEqual(dialog.baseline.get(), '57')
        self.save(dialog, NEXT_KEYS)
        saved = CredentialStore(self.paths.credentials_file).load()
        self.assertEqual(saved.credentials, NEXT_KEYS)
        self.assertEqual(saved.account, ACCOUNT)
        self.assertTrue(previous.closed)
        self.assertEqual(self.reserved(), 4)
        self.assertEqual(self.controller.ledger.snapshot(ACCOUNT, NOW).external_used, 57)
        dialog.cancel()
        self.assert_translates()

    def test_untouched_previous_account_prefill_does_not_charge_new_account(self):
        self.configured(external=57)
        self.identity.account = OTHER_ACCOUNT
        dialog = self.open_dialog()
        self.assertEqual(dialog.baseline.get(), '57')
        self.save(dialog, NEXT_KEYS)
        self.assertEqual(self.controller.account, OTHER_ACCOUNT)
        self.assertEqual(self.controller.ledger.snapshot(ACCOUNT, NOW).external_used, 57)
        self.assertEqual(self.controller.ledger.snapshot(OTHER_ACCOUNT, NOW).external_used, 0)
        self.assertEqual(self.reserved(ACCOUNT), 2)
        self.assertEqual(self.reserved(OTHER_ACCOUNT), 2)

    def test_lowering_same_month_baseline_is_rejected_before_translation(self):
        previous = self.configured(external=57)
        encrypted = self.paths.credentials_file.read_bytes()
        dialog = self.open_dialog()
        self.fill_credentials(dialog, NEXT_KEYS)
        dialog.baseline.set('56')
        dialog.connect_button.invoke()
        self.spin_until(lambda: not self.service.busy and '配置未完成' in dialog.status.get())
        self.assertIn('用量确认', dialog.status.get())
        self.assertEqual(len(self.engines), 1)
        self.assertEqual(self.controller.ledger.snapshot(ACCOUNT, NOW).external_used, 57)
        self.assertEqual(self.reserved(), 2)
        self.assertEqual(self.paths.credentials_file.read_bytes(), encrypted)
        dialog.cancel()
        self.assertIs(self.controller.engine, previous)
        self.assertFalse(self.controller.paused)

    def test_different_account_uses_its_own_ledger_without_erasing_old_account(self):
        self.configured(external=57)
        self.identity.account = OTHER_ACCOUNT
        dialog = self.open_dialog(); self.save(dialog, NEXT_KEYS, external=91)
        self.assertEqual(self.controller.account, OTHER_ACCOUNT)
        self.assertEqual(self.reserved(ACCOUNT), 2)
        self.assertEqual(self.reserved(OTHER_ACCOUNT), 2)
        self.assertEqual(self.controller.ledger.snapshot(ACCOUNT, NOW).external_used, 57)
        self.assertEqual(self.controller.ledger.snapshot(OTHER_ACCOUNT, NOW).external_used, 91)
        self.assertEqual(CredentialStore(self.paths.credentials_file).load().account, OTHER_ACCOUNT)

    def test_connect_uses_latest_key_fields(self):
        dialog = self.open_dialog()
        self.fill_credentials(dialog)
        self.fill_credentials(dialog, NEXT_KEYS)
        dialog.connect_button.invoke()
        self.spin_until(lambda: not self.service.busy and '配置成功' in dialog.status.get())
        self.assertEqual(self.identity.calls, [NEXT_KEYS])
        self.assertEqual(self.engines[0].credentials, NEXT_KEYS)
        self.assertEqual(CredentialStore(self.paths.credentials_file).load().credentials, NEXT_KEYS)

    def test_repeated_connect_during_identity_starts_only_one_attempt(self):
        self.identity.release.clear()
        dialog = self.open_dialog()
        self.fill_credentials(dialog)
        dialog.connect_button.invoke()
        self.assertTrue(self.identity.entered.wait(2))
        dialog.connect_button.invoke()
        dialog.connect()
        self.assertTrue(self.service.busy)
        self.assertEqual(str(dialog.connect_button['state']), 'disabled')
        self.identity.release.set()
        self.spin_until(lambda: not self.service.busy and '配置成功' in dialog.status.get())
        self.assertEqual(self.identity.calls, [KEYS])
        self.assertEqual(len(self.engines), 1)
        self.assertEqual(len(self.engines[0].calls), 1)
        self.assertEqual(self.reserved(), 2)

    def test_cancel_keeps_preexisting_paused_state(self):
        self.configured()
        self.controller.pause_translation(True)
        self.identity.entered.clear(); self.identity.release.clear()
        dialog = self.open_dialog()
        self.fill_credentials(dialog, NEXT_KEYS)
        dialog.connect_button.invoke()
        self.assertTrue(self.identity.entered.wait(2))
        dialog.cancel(); self.identity.release.set()
        self.spin_until(lambda: not self.service.busy)
        self.assertTrue(self.controller.paused)
        self.assertFalse(self.controller.configuration_held)
        self.assertEqual(CredentialStore(self.paths.credentials_file).load().credentials, KEYS)

    def test_delete_confirmation_can_be_declined_without_changing_configuration(self):
        previous = self.configured()
        encrypted = self.paths.credentials_file.read_bytes()
        dialog = self.open_dialog()
        with patch('hots_chat.ui.credential_dialog.messagebox.askyesno', return_value=False):
            dialog.delete_button.invoke()
        self.assertIs(self.controller.engine, previous)
        self.assertFalse(self.controller.paused)
        self.assertEqual(self.paths.credentials_file.read_bytes(), encrypted)
        self.assertEqual(self.reserved(), 2)

    def test_delete_disables_cloud_keeps_usage_and_retains_raw_history(self):
        self.configured(external=57)
        old_id = self.raw_message('raw before delete')
        self.spin_until(lambda: self.controller.idle)
        self.window.pump()
        reserved = self.reserved()
        dialog = self.open_dialog()
        with patch('hots_chat.ui.credential_dialog.messagebox.askyesno', return_value=True):
            dialog.delete_button.invoke()
        self.spin_until(lambda: not self.service.busy and '已删除' in str(dialog.current['text']))
        self.assertFalse(self.paths.credentials_file.exists())
        self.assertTrue(self.controller.paused)
        self.assertFalse(self.controller.cloud_configured)
        self.assertEqual(self.window.account_button['text'], '配置云翻译')
        self.assertEqual(self.reserved(), reserved)
        self.assertEqual(self.controller.ledger.snapshot(ACCOUNT, NOW).external_used, 57)
        dialog.cancel()
        new_id = self.raw_message('raw after delete')
        self.assertTrue(self.window.tree.exists(old_id))
        self.assertTrue(self.window.tree.exists(new_id))
        self.assertEqual(self.window.vm.messages[new_id].engine_translation, '')
        self.assertEqual(self.reserved(), reserved)

    def test_encrypted_save_failure_preserves_previous_key_and_charges_attempt(self):
        previous = self.configured()
        encrypted = self.paths.credentials_file.read_bytes()
        dialog = self.open_dialog()
        self.fill_credentials(dialog, NEXT_KEYS)
        with patch('hots_chat.credentials.os.replace', side_effect=PermissionError('offline write denied')):
            dialog.connect_button.invoke()
            self.spin_until(lambda: not self.service.busy and '配置未完成' in dialog.status.get())
        self.assertIn('加密保存', dialog.status.get())
        self.assertIs(self.controller.engine, previous)
        self.assertEqual(self.paths.credentials_file.read_bytes(), encrypted)
        self.assertEqual(self.reserved(), 4)
        self.assertEqual(list(self.paths.credentials_file.parent.glob('credentials.dpapi.*.tmp')), [])
        dialog.cancel()
        self.assert_translates()


if __name__ == '__main__':
    unittest.main()
