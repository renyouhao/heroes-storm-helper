import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hots_chat.account_setup import UnconfiguredEngine
from hots_chat.config import ConfigError, Credentials
from hots_chat.controller import ChatController
from hots_chat.credentials import CredentialStore
from hots_chat.models import TranslationResult
from hots_chat.provider_accounts import ProviderCredentialStore, TranslationAccounts
from hots_chat.translation.deepseek import DeepSeekCredentials


ALI = Credentials('synthetic_aliyun_id', 'synthetic_aliyun_secret')
DS_KEY = 'synthetic-deepseek-secret-123456789'
BALANCE = {'is_available': True, 'balance_infos': [
    {'currency': 'CNY', 'total_balance': '11.25', 'granted_balance': '1.00', 'topped_up_balance': '10.25'},
    {'currency': 'USD', 'total_balance': '0.00', 'granted_balance': '0.00', 'topped_up_balance': '0.00'},
]}


class MemoryProtector:
    def __init__(self):
        self.documents = {}

    def protect(self, value):
        token = hashlib.sha256(value + str(len(self.documents)).encode()).digest()
        self.documents[token] = value
        return token

    def unprotect(self, value):
        return self.documents[value]


class Source:
    def stop(self):
        pass


class MockWorker:
    def __init__(self):
        self.waits = []

    def join(self, timeout):
        self.waits.append(timeout)


class Engine:
    config_version = 'offline-v1'

    def __init__(self, provider, credentials, controls=None):
        self.provider_id, self.credentials = provider, credentials
        self.controls = controls or SimpleNamespace()
        self.calls = []
        self.closed = False
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()

    def translate(self, job):
        self.calls.append(job)
        self.entered.set()
        self.release.wait(3)
        if getattr(self.controls, 'translate_error', None):
            raise RuntimeError(self.controls.translate_error)
        return TranslationResult(job.job_id, 'Hello', None, None, 1)

    def get_balance(self):
        entered = getattr(self.controls, 'balance_entered', None)
        release = getattr(self.controls, 'balance_release', None)
        if entered is not None:
            entered.set()
        if release is not None:
            release.wait(3)
        if getattr(self.controls, 'balance_error', None):
            raise RuntimeError(self.controls.balance_error)
        return getattr(self.controls, 'balance', BALANCE)

    def close(self):
        self.closed = True


class StoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.path = self.base / 'credentials.dpapi'
        self.protector = MemoryProtector()
        self.legacy = CredentialStore(self.path, protector=self.protector)
        self.store = ProviderCredentialStore(self.path, protector=self.protector, legacy_store=self.legacy)

    def test_existing_aliyun_v1_loads_without_conversion_or_other_profile_read(self):
        self.legacy.save(ALI, '123456')
        before = self.path.read_bytes()
        self.store.deepseek.path.write_bytes(b'broken-unselected-profile')
        self.assertEqual(self.store.load_active().credentials, ALI)
        self.assertEqual(self.store.selected_provider, 'aliyun')
        self.assertEqual(self.path.read_bytes(), before)

    def test_two_profiles_roundtrip_separately_and_do_not_leak_or_override(self):
        self.legacy.save(ALI, '123456')
        before = self.path.read_bytes()
        credentials = DeepSeekCredentials(DS_KEY, context_enabled=False)
        self.store.save_provider('deepseek', credentials, 'deepseek')
        self.store.select_provider('deepseek')
        restored = ProviderCredentialStore(self.path, protector=self.protector)
        self.assertEqual(restored.load_active().credentials, credentials)
        self.assertEqual(restored.load_active().provider_id, 'deepseek')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.legacy.load().credentials, ALI)
        self.assertEqual(json.loads(self.store.selection_path.read_text()), {'provider': 'deepseek'})
        for path in self.base.iterdir():
            data = path.read_bytes()
            self.assertNotIn(DS_KEY.encode(), data)
            self.assertNotIn(ALI.access_key_secret.encode(), data)
        self.assertNotIn(DS_KEY, repr(restored.load_active()))

    def test_missing_selected_profile_does_not_fall_back_to_saved_other_key(self):
        self.legacy.save(ALI, '123456')
        self.store.select_provider('deepseek')
        self.assertIsNone(self.store.load_active())
        self.assertIsNotNone(self.store.load_provider('aliyun'))

    def test_selected_corrupt_profile_fails_closed_without_fallback(self):
        self.legacy.save(ALI, '123456')
        self.store.deepseek.path.write_bytes(b'broken')
        self.store.select_provider('deepseek')
        with self.assertRaises(ConfigError):
            self.store.load_active()
        self.assertEqual(self.store.load_provider('aliyun').credentials, ALI)

    def test_delete_one_profile_preserves_the_other_and_selection(self):
        self.legacy.save(ALI, '123456')
        self.store.save_provider('deepseek', DeepSeekCredentials(DS_KEY), 'deepseek')
        self.store.select_provider('deepseek')
        self.store.delete_provider('deepseek')
        self.assertIsNone(self.store.load_active())
        self.assertEqual(self.store.selected_provider, 'deepseek')
        self.assertEqual(self.store.load_provider('aliyun').credentials, ALI)

    def test_invalid_deepseek_document_and_cross_provider_copy_are_rejected(self):
        self.store.save_provider('deepseek', DeepSeekCredentials(DS_KEY), 'deepseek')
        encrypted = self.store.deepseek.path.read_bytes()
        token = encrypted[len(self.store.deepseek._file_magic):]
        payload = json.dumps({'version': 1, 'provider': 'deepseek', 'account': 'deepseek',
                              'api_key': DS_KEY, 'context_enabled': 'false'}).encode()
        self.protector.documents[token] = b'HDSEEK1' + hashlib.sha256(payload).digest() + payload
        with self.assertRaises(ConfigError):
            self.store.load_provider('deepseek')
        self.path.write_bytes(encrypted)
        with self.assertRaises(ConfigError):
            self.legacy.load()

    def test_selection_is_strict_and_atomic_on_write_failure(self):
        self.store.select_provider('aliyun')
        before = self.store.selection_path.read_bytes()
        with patch('hots_chat.provider_accounts.os.replace', side_effect=OSError(DS_KEY)):
            with self.assertRaises(ConfigError) as caught:
                self.store.select_provider('deepseek')
        self.assertNotIn(DS_KEY, str(caught.exception))
        self.assertEqual(self.store.selection_path.read_bytes(), before)
        self.assertEqual(self.store.selected_provider, 'aliyun')
        self.assertEqual(list(self.base.glob('*.tmp')), [])
        self.store.selection_path.write_text('{"provider":"aliyun","provider":"deepseek"}')
        restored = ProviderCredentialStore(self.path, protector=self.protector)
        self.assertTrue(restored.selection_error)
        with self.assertRaises(ConfigError):
            restored.load_active()

    def test_corrupt_selection_keeps_both_profiles_and_requires_explicit_reselection(self):
        self.legacy.save(ALI, '123456')
        self.store.save_provider('deepseek', DeepSeekCredentials(DS_KEY, False), 'deepseek')
        original_aliyun = self.path.read_bytes()
        original_deepseek = self.store.deepseek.path.read_bytes()
        self.store.selection_path.write_bytes(b'broken-selection')
        restored = ProviderCredentialStore(self.path, protector=self.protector)
        self.assertEqual(restored.selected_provider, 'aliyun')
        self.assertTrue(restored.selection_error)
        with self.assertRaises(ConfigError):
            restored.load_active()
        self.assertEqual(restored.load_provider('aliyun').credentials, ALI)
        self.assertEqual(restored.load_provider('deepseek').credentials.api_key, DS_KEY)
        self.assertEqual(self.path.read_bytes(), original_aliyun)
        self.assertEqual(restored.deepseek.path.read_bytes(), original_deepseek)
        restored.select_provider('deepseek')
        self.assertEqual(restored.selection_error, '')
        self.assertEqual(restored.load_active().provider_id, 'deepseek')
        self.assertEqual(self.path.read_bytes(), original_aliyun)
        self.assertEqual(restored.deepseek.path.read_bytes(), original_deepseek)

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI integration')
    def test_deepseek_dpapi_roundtrip_uses_separate_real_user_encryption(self):
        store = ProviderCredentialStore(self.path)
        store.save_provider('deepseek', DeepSeekCredentials(DS_KEY, False), 'deepseek')
        saved = ProviderCredentialStore(self.path).load_provider('deepseek')
        self.assertEqual(saved.credentials.api_key, DS_KEY)
        self.assertFalse(saved.credentials.context_enabled)
        self.assertNotIn(DS_KEY.encode(), store.deepseek.path.read_bytes())


class AccountsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.store = ProviderCredentialStore(self.base / 'credentials.dpapi', protector=MemoryProtector())
        self.store.save_provider('aliyun', ALI, '123456')
        self.old_engine = Engine('aliyun', ALI)
        self.controller = ChatController(Source(), self.old_engine, '123456')
        self.controller.cloud_configured = True
        self.addCleanup(self.controller.close)
        self.ds_controls = SimpleNamespace()
        self.ds_engines, self.ali_engines = [], []
        def deepseek_factory(credentials):
            engine = Engine('deepseek', credentials, self.ds_controls)
            self.ds_engines.append(engine)
            return engine
        def aliyun_factory(credentials):
            engine = Engine('aliyun', credentials)
            self.ali_engines.append(engine)
            return engine
        self.accounts = TranslationAccounts(self.controller, self.store,
            SimpleNamespace(resolve=lambda credentials: '123456'), aliyun_factory, deepseek_factory,
            saved=self.store.load_active())
        self.addCleanup(self.accounts.close)

    def wait(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(condition())

    def connect_ds(self, context=True):
        self.accounts.connect_deepseek(DS_KEY, context_enabled=context)
        self.wait(lambda: not self.accounts.busy)
        self.assertEqual(self.accounts.state, 'ready', self.accounts.poll())

    def test_connect_validates_balance_tests_translates_then_activates_and_preserves_aliyun(self):
        before = self.store.path.read_bytes()
        self.connect_ds(False)
        self.assertEqual(self.controller.engine.provider_id, 'deepseek')
        self.assertEqual(self.accounts.current.provider_id, 'deepseek')
        self.assertEqual(self.accounts.selected_provider, 'deepseek')
        self.assertFalse(self.controller.paused)
        self.assertFalse(self.controller.configuration_held)
        self.assertTrue(self.ds_engines[0].closed)
        self.assertEqual([job.text for job in self.ds_engines[1].calls], ['你好'])
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertFalse(self.store.load_provider('deepseek').credentials.context_enabled)

    def test_switch_waits_for_inflight_request_before_identity_or_new_cloud_calls(self):
        self.old_engine.release.clear()
        self.controller.submit_manual('hold', 'en')
        self.assertTrue(self.old_engine.entered.wait(1))
        self.accounts.connect_deepseek(DS_KEY)
        time.sleep(.04)
        self.assertEqual(self.ds_engines, [])
        self.assertIs(self.controller.engine, self.old_engine)
        self.old_engine.release.set()
        self.wait(lambda: not self.accounts.busy)
        self.assertEqual(self.accounts.current.provider_id, 'deepseek')

    def test_failed_identity_keeps_original_engine_paused_without_saving_or_leaking(self):
        self.ds_controls.balance_error = DS_KEY
        self.accounts.connect_deepseek(DS_KEY)
        self.wait(lambda: not self.accounts.busy)
        self.assertIs(self.controller.engine, self.old_engine)
        self.assertEqual(self.accounts.current.credentials, ALI)
        self.assertTrue(self.controller.paused)
        self.assertTrue(self.controller.configuration_held)
        self.assertIsNone(self.store.load_provider('deepseek'))
        self.assertNotIn(DS_KEY, str(self.accounts.poll()))
        self.accounts.cancel()
        self.assertTrue(self.controller.paused)
        self.assertFalse(self.controller.configuration_held)
        self.accounts.select_provider('aliyun')
        self.wait(lambda: not self.accounts.busy)
        self.assertFalse(self.controller.paused)
        self.assertEqual(self.controller.engine.provider_id, 'aliyun')

    def test_failed_translation_never_saves_or_replaces_active_engine(self):
        self.ds_controls.translate_error = DS_KEY
        self.accounts.connect_deepseek(DS_KEY)
        self.wait(lambda: not self.accounts.busy)
        self.assertIs(self.controller.engine, self.old_engine)
        self.assertIsNone(self.store.load_provider('deepseek'))
        self.assertTrue(all(engine.closed for engine in self.ds_engines))
        self.assertNotIn(DS_KEY, str(self.accounts.poll()))

    def test_cancel_discards_late_identity_without_reenabling_old_key(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.ds_controls.balance_entered, self.ds_controls.balance_release = entered, release
        self.accounts.connect_deepseek(DS_KEY)
        self.assertTrue(entered.wait(1))
        self.accounts.cancel()
        release.set()
        self.wait(lambda: not self.accounts.busy)
        self.assertIsNone(self.store.load_provider('deepseek'))
        self.assertIs(self.controller.engine, self.old_engine)
        self.assertTrue(self.controller.paused)
        self.assertEqual(self.accounts.selected_provider, 'aliyun')

    def test_select_saved_deepseek_preserves_flag_and_allows_explicit_context_change(self):
        self.store.save_provider('deepseek', DeepSeekCredentials(DS_KEY, False), 'deepseek')
        self.accounts.select_provider('deepseek')
        self.wait(lambda: not self.accounts.busy)
        self.assertFalse(self.accounts.current.credentials.context_enabled)
        self.accounts.select_provider('deepseek', context_enabled=True)
        self.wait(lambda: not self.accounts.busy)
        self.assertEqual(self.accounts.current.credentials.api_key, DS_KEY)
        self.assertTrue(self.accounts.current.credentials.context_enabled)
        self.assertTrue(self.store.load_provider('deepseek').credentials.context_enabled)
        self.assertEqual(len(self.ds_engines), 4)

    def test_select_unconfigured_provider_disables_cloud_without_fallback(self):
        self.accounts.select_provider('deepseek')
        self.wait(lambda: not self.accounts.busy)
        self.assertEqual(self.accounts.selected_provider, 'deepseek')
        self.assertIsNone(self.accounts.current)
        self.assertFalse(self.controller.cloud_configured)
        self.assertTrue(self.controller.paused)
        self.assertEqual(self.controller.engine.provider_id, 'deepseek')
        self.assertEqual(self.ds_engines, [])
        self.assertEqual(self.store.load_provider('aliyun').credentials, ALI)

    def test_select_corrupt_profile_keeps_old_engine_paused_until_explicit_recovery(self):
        self.store.deepseek.path.write_bytes(b'broken')
        self.accounts.select_provider('deepseek')
        self.wait(lambda: not self.accounts.busy)
        self.assertEqual(self.accounts.state, 'error')
        self.assertIs(self.controller.engine, self.old_engine)
        self.assertTrue(self.controller.paused)
        self.assertTrue(self.controller.configuration_held)
        self.assertEqual(self.accounts.selected_provider, 'aliyun')
        self.accounts.cancel()
        self.assertTrue(self.controller.paused)

    def test_corrupt_selection_allows_unconfigured_start_and_explicit_profile_activation(self):
        self.store.save_provider('deepseek', DeepSeekCredentials(DS_KEY, False), 'deepseek')
        self.store.selection_path.write_bytes(b'broken-selection')
        restored = ProviderCredentialStore(self.store.path, protector=self.store.deepseek._protector)
        with self.assertRaises(ConfigError):
            restored.load_active()
        controller = ChatController(Source(), UnconfiguredEngine(), 'unconfigured')
        controller.cloud_configured = False
        controller.pause_translation(True)
        self.addCleanup(controller.close)
        accounts = TranslationAccounts(controller, restored, self.accounts.identity,
                                       self.accounts.aliyun_factory, self.accounts.deepseek_factory)
        self.addCleanup(accounts.close)
        self.assertIsNone(accounts.current)
        self.assertEqual(accounts.state, 'unconfigured')
        self.assertTrue(controller.paused)
        self.assertFalse(controller.cloud_configured)
        self.assertEqual(self.ds_engines, [])
        self.assertTrue(accounts.profiles_snapshot()['selection_error'])
        accounts.select_provider('deepseek')
        self.wait(lambda: not accounts.busy)
        self.assertEqual(accounts.current.provider_id, 'deepseek')
        self.assertEqual(restored.selection_error, '')
        self.assertFalse(controller.paused)
        self.assertTrue(controller.cloud_configured)
        self.assertEqual(restored.load_provider('aliyun').credentials, ALI)

    def test_delete_only_selected_profile_does_not_switch_to_other_saved_key(self):
        self.connect_ds()
        self.accounts.delete()
        self.wait(lambda: not self.accounts.busy)
        self.assertIsNone(self.store.load_provider('deepseek'))
        self.assertEqual(self.store.load_provider('aliyun').credentials, ALI)
        self.assertIsNone(self.accounts.current)
        self.assertEqual(self.accounts.selected_provider, 'deepseek')
        self.assertTrue(self.controller.paused)

    def test_profile_snapshot_is_masked_and_unselected_corruption_is_isolated(self):
        self.store.deepseek.path.write_bytes(b'broken')
        snapshot = self.accounts.profiles_snapshot()
        self.assertTrue(snapshot['profiles']['aliyun']['configured'])
        self.assertFalse(snapshot['profiles']['deepseek']['configured'])
        self.assertTrue(snapshot['profiles']['deepseek']['error'])
        self.assertNotIn(ALI.access_key_id, str(snapshot))
        self.assertNotIn(ALI.access_key_secret, str(snapshot))
        self.assertIs(self.controller.engine, self.old_engine)

    def test_balance_is_nonblocking_and_does_not_hold_or_pause_translation(self):
        self.connect_ds()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.ds_controls.balance_entered, self.ds_controls.balance_release = entered, release
        started = time.monotonic()
        self.assertTrue(self.accounts.refresh_balance())
        self.assertLess(time.monotonic() - started, .2)
        self.assertTrue(entered.wait(1))
        self.assertTrue(self.accounts.balance_snapshot()['busy'])
        self.assertFalse(self.accounts.refresh_balance())
        self.assertFalse(self.controller.paused)
        self.assertFalse(self.controller.configuration_held)
        self.controller.submit_manual('reply', 'ko')
        self.wait(lambda: self.controller.idle)
        release.set()
        self.wait(lambda: not self.accounts.balance_snapshot()['busy'])
        snapshot = self.accounts.balance_snapshot()
        self.assertEqual(snapshot['balance_infos'], BALANCE['balance_infos'])
        self.assertTrue(snapshot['is_available'])
        self.assertIsNotNone(snapshot['as_of'])
        self.assertNotIn(DS_KEY, str(snapshot))

    def test_late_balance_cannot_write_after_provider_switch(self):
        self.connect_ds()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.ds_controls.balance_entered, self.ds_controls.balance_release = entered, release
        self.accounts.refresh_balance()
        self.assertTrue(entered.wait(1))
        self.accounts.select_provider('aliyun')
        self.wait(lambda: not self.accounts.busy)
        release.set()
        self.wait(lambda: not self.accounts._balance_thread.is_alive())
        self.assertEqual(self.accounts.balance_snapshot()['balance_infos'], [])
        self.assertIsNone(self.accounts.balance_snapshot()['as_of'])

    def test_balance_error_text_is_fixed_and_malformed_balance_never_leaks(self):
        self.connect_ds()
        self.ds_controls.balance_error = DS_KEY
        self.accounts.refresh_balance()
        self.wait(lambda: not self.accounts.balance_snapshot()['busy'])
        self.assertTrue(self.accounts.balance_snapshot()['error'])
        self.assertNotIn(DS_KEY, str(self.accounts.balance_snapshot()))
        del self.ds_controls.balance_error
        self.ds_controls.balance = {'is_available': True, 'balance_infos': [{
            'currency': 'CNY', 'total_balance': DS_KEY,
            'granted_balance': '0', 'topped_up_balance': '0'}]}
        self.accounts.refresh_balance()
        self.wait(lambda: not self.accounts.balance_snapshot()['busy'])
        self.assertEqual(self.accounts.balance_snapshot()['balance_infos'], [])
        self.assertNotIn(DS_KEY, str(self.accounts.balance_snapshot()))

    def test_late_balance_cannot_write_after_close(self):
        self.connect_ds()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.ds_controls.balance_entered, self.ds_controls.balance_release = entered, release
        self.accounts.refresh_balance()
        self.assertTrue(entered.wait(1))
        self.accounts.close()
        release.set()
        self.wait(lambda: not self.accounts._balance_thread.is_alive())
        self.assertEqual(self.accounts.balance_snapshot()['balance_infos'], [])
        self.assertFalse(self.accounts.refresh_balance())

    def test_join_waits_for_old_balance_worker_after_switch_and_new_query(self):
        self.connect_ds()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.ds_controls.balance_entered, self.ds_controls.balance_release = entered, release
        self.accounts.refresh_balance()
        self.assertTrue(entered.wait(1))
        old_worker = self.accounts._balance_thread
        self.accounts.select_provider('aliyun')
        self.wait(lambda: not self.accounts.busy)
        del self.ds_controls.balance_entered
        del self.ds_controls.balance_release
        self.accounts.select_provider('deepseek')
        self.wait(lambda: not self.accounts.busy)
        self.accounts.refresh_balance()
        self.wait(lambda: not self.accounts.balance_snapshot()['busy'])
        latest_worker = self.accounts._balance_thread
        self.assertIsNot(old_worker, latest_worker)
        self.assertTrue(old_worker.is_alive())
        with self.accounts._lock:
            self.assertIn(old_worker, self.accounts._balance_threads)
        self.accounts.close()
        finished = threading.Event()
        waiter = threading.Thread(target=lambda: (self.accounts.join(1), finished.set()))
        waiter.start()
        try:
            self.assertFalse(finished.wait(.04))
        finally:
            release.set()
            waiter.join(1)
        self.assertTrue(finished.is_set())
        self.assertFalse(old_worker.is_alive())
        self.assertFalse(latest_worker.is_alive())
        with self.accounts._lock:
            self.assertEqual(self.accounts._balance_threads, set())

    def test_join_uses_one_deadline_for_all_balance_workers(self):
        first, second = MockWorker(), MockWorker()
        with self.accounts._lock:
            self.accounts._balance_threads.update((first, second))
        with patch.object(self.accounts._setup, 'join') as setup_join, \
             patch('hots_chat.provider_accounts.time.monotonic', side_effect=[10.0, 10.2, 10.6]):
            self.accounts.join(.8)
        setup_join.assert_called_once_with(.8)
        waits = sorted([first.waits[0], second.waits[0]])
        self.assertAlmostEqual(waits[0], .2)
        self.assertAlmostEqual(waits[1], .6)
        with self.accounts._lock:
            self.accounts._balance_threads.clear()

    def test_diagnostics_registers_secret_but_emits_no_key_or_provider_exception(self):
        registered, recorded = [], []
        self.controller.diagnostics = SimpleNamespace(
            register_secrets=lambda *values: registered.extend(values),
            record=lambda *args, **kwargs: recorded.append((args, kwargs)))
        self.ds_controls.balance_error = DS_KEY
        self.accounts.connect_deepseek(DS_KEY)
        self.wait(lambda: not self.accounts.busy)
        self.assertIn(DS_KEY, registered)
        self.assertNotIn(DS_KEY, str(recorded))
        self.assertNotIn(DS_KEY, str(self.accounts.poll()))

    def test_invalid_context_or_provider_does_not_change_active_key(self):
        for provider, context in [('aliyun', True), ('deepseek', 1), ('other', None)]:
            with self.subTest(provider=provider, context=context):
                with self.assertRaises(ValueError):
                    self.accounts.select_provider(provider, context_enabled=context)
        self.assertIs(self.controller.engine, self.old_engine)
        self.assertFalse(self.controller.paused)

    def test_activation_failure_restores_selection_and_keeps_old_current(self):
        with patch.object(self.controller, 'replace_engine', side_effect=RuntimeError(DS_KEY)):
            self.accounts.connect_deepseek(DS_KEY)
            self.wait(lambda: not self.accounts.busy)
        self.assertIs(self.controller.engine, self.old_engine)
        self.assertEqual(self.accounts.current.credentials, ALI)
        self.assertEqual(self.accounts.selected_provider, 'aliyun')
        self.assertTrue(self.controller.paused)
        self.assertNotIn(DS_KEY, str(self.accounts.poll()))

    def test_failed_activation_after_selection_recovery_does_not_enable_display_default(self):
        self.store.selection_path.write_bytes(b'broken-selection')
        restored = ProviderCredentialStore(self.store.path, protector=self.store.deepseek._protector)
        self.accounts.store = restored
        with patch.object(self.controller, 'replace_engine', side_effect=RuntimeError('activation failed')):
            self.accounts.connect_deepseek(DS_KEY)
            self.wait(lambda: not self.accounts.busy)
        self.assertTrue(restored.selection_error)
        self.assertEqual(restored.selection_path.read_bytes(), b'broken-selection')
        reloaded = ProviderCredentialStore(restored.path, protector=restored.deepseek._protector)
        with self.assertRaises(ConfigError):
            reloaded.load_active()
        self.assertEqual(restored.load_provider('aliyun').credentials, ALI)
        self.assertTrue(self.controller.paused)

    def test_failed_aliyun_rotation_restores_original_ciphertext_and_selected_account(self):
        before = self.store.path.read_bytes()
        replacement = Credentials('synthetic_new_aliyun_id', 'synthetic_new_aliyun_secret')
        with patch.object(self.controller, 'replace_engine', side_effect=RuntimeError('activation failed')):
            self.accounts.connect(replacement)
            self.wait(lambda: not self.accounts.busy)
        self.accounts.join(1)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.store.load_active().credentials, ALI)
        self.assertEqual(self.accounts.current.credentials, ALI)
        self.assertIs(self.controller.engine, self.old_engine)
        self.assertTrue(self.controller.paused)
        self.assertFalse(self.store.deepseek.path.exists())
        self.assertFalse(self.store.pending_path.exists())

    def test_failed_deepseek_rotation_restores_ciphertext_context_and_other_profile(self):
        self.connect_ds(False)
        before_deepseek = self.store.deepseek.path.read_bytes()
        before_aliyun = self.store.path.read_bytes()
        previous_engine = self.controller.engine
        with patch.object(self.controller, 'replace_engine', side_effect=RuntimeError('activation failed')):
            self.accounts.connect_deepseek('synthetic-rotated-key-348192', context_enabled=True)
            self.wait(lambda: not self.accounts.busy)
        self.accounts.join(1)
        self.assertEqual(self.store.deepseek.path.read_bytes(), before_deepseek)
        self.assertEqual(self.store.path.read_bytes(), before_aliyun)
        saved = self.store.load_active()
        self.assertEqual(saved.credentials.api_key, DS_KEY)
        self.assertFalse(saved.credentials.context_enabled)
        self.assertIs(self.controller.engine, previous_engine)
        self.assertEqual(self.accounts.current.credentials.api_key, DS_KEY)
        self.assertTrue(self.controller.paused)
        self.assertFalse(self.store.pending_path.exists())

    def test_first_deepseek_activation_failure_leaves_no_candidate_profile(self):
        before = self.store.path.read_bytes()
        with patch.object(self.controller, 'replace_engine', side_effect=RuntimeError('activation failed')):
            self.accounts.connect_deepseek(DS_KEY)
            self.wait(lambda: not self.accounts.busy)
        self.accounts.join(1)
        self.assertIsNone(self.store.load_provider('deepseek'))
        self.assertFalse(self.store.deepseek.path.exists())
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.store.load_active().credentials, ALI)
        self.assertFalse(self.store.pending_path.exists())

    def test_selection_write_failure_after_profile_save_restores_both_files(self):
        self.store.select_provider('aliyun')
        before_selection = self.store.selection_path.read_bytes()
        before_aliyun = self.store.path.read_bytes()
        original_write = self.store._write_selection
        calls = []
        def fail_first_write(payload):
            calls.append(payload)
            if len(calls) == 1:
                raise ConfigError('synthetic selection write failure')
            return original_write(payload)
        with patch.object(self.store, '_write_selection', side_effect=fail_first_write):
            self.accounts.connect_deepseek(DS_KEY)
            self.wait(lambda: not self.accounts.busy)
        self.assertEqual(self.store.selection_path.read_bytes(), before_selection)
        self.assertEqual(self.store.path.read_bytes(), before_aliyun)
        self.assertIsNone(self.store.load_provider('deepseek'))
        self.assertEqual(self.store.load_active().credentials, ALI)
        self.assertIs(self.controller.engine, self.old_engine)
        self.assertFalse(self.store.pending_path.exists())
        self.assertTrue(self.controller.paused)

    def test_delete_activation_failure_restores_selected_profile_ciphertext(self):
        self.connect_ds(False)
        before_deepseek = self.store.deepseek.path.read_bytes()
        before_aliyun = self.store.path.read_bytes()
        previous_engine = self.controller.engine
        with patch.object(self.controller, 'replace_engine', side_effect=RuntimeError('activation failed')):
            self.accounts.delete()
            self.wait(lambda: not self.accounts.busy)
        self.accounts.join(1)
        self.assertEqual(self.store.deepseek.path.read_bytes(), before_deepseek)
        self.assertEqual(self.store.path.read_bytes(), before_aliyun)
        self.assertEqual(self.store.load_active().credentials.api_key, DS_KEY)
        self.assertEqual(self.accounts.current.credentials.api_key, DS_KEY)
        self.assertIs(self.controller.engine, previous_engine)
        self.assertTrue(self.controller.paused)
        self.assertFalse(self.store.pending_path.exists())

    def test_failed_rollback_keeps_durable_guard_and_disables_until_explicit_recovery(self):
        with patch.object(self.controller, 'replace_engine', side_effect=RuntimeError(DS_KEY)), \
             patch.object(self.store, '_restore_profile', side_effect=OSError(DS_KEY)):
            self.accounts.connect_deepseek(DS_KEY)
            self.wait(lambda: not self.accounts.busy)
        self.accounts.join(1)
        self.assertTrue(self.store.pending_path.exists())
        self.assertFalse(self.controller.cloud_configured)
        self.assertTrue(self.controller.paused)
        self.assertIsNone(self.accounts.current)
        self.assertNotIn(DS_KEY, str(self.accounts.poll()))
        self.assertNotIn(DS_KEY.encode(), self.store.pending_path.read_bytes())
        with self.assertRaises(ConfigError):
            self.store.load_active()
        restarted = ProviderCredentialStore(self.store.path, protector=self.store.deepseek._protector)
        with self.assertRaises(ConfigError):
            restarted.load_active()
        self.accounts.cancel()
        self.assertFalse(self.controller.cloud_configured)
        self.assertTrue(self.controller.paused)
        self.accounts.select_provider('aliyun')
        self.wait(lambda: not self.accounts.busy)
        self.assertEqual(self.accounts.current.credentials, ALI)
        self.assertFalse(self.store.pending_path.exists())
        self.assertTrue(self.controller.cloud_configured)

    def test_cancelled_configuration_join_cleans_worker_without_partial_profile_or_guard(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.ds_controls.balance_entered, self.ds_controls.balance_release = entered, release
        self.accounts.connect_deepseek(DS_KEY)
        self.assertTrue(entered.wait(1))
        self.accounts.cancel()
        release.set()
        self.accounts.join(1)
        self.assertFalse(self.accounts.busy)
        self.assertFalse(self.store.pending_path.exists())
        self.assertIsNone(self.store.load_provider('deepseek'))
        self.assertTrue(all(engine.closed for engine in self.ds_engines))


if __name__ == '__main__':
    unittest.main()
