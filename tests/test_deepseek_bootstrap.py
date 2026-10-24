"""Build the real controller against isolated files and fake external boundaries."""
from pathlib import Path
import socket
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from hots_chat.app import build_controller
from hots_chat.config import Credentials
from hots_chat.credentials import CredentialStore
from hots_chat.models import TranslationResult
from hots_chat.paths import AppPaths
from hots_chat.provider_accounts import ProviderCredentialStore
from hots_chat.translation.deepseek import DeepSeekCredentials
from tests.test_provider_accounts import MemoryProtector


ALI = Credentials('synthetic-bootstrap-aliyun-id', 'synthetic-bootstrap-aliyun-secret')
DS_KEY = 'synthetic-bootstrap-deepseek-key-01923'


class OfflineSource:
    def __init__(self, **options):
        self.options = options

    def start(self, emit):
        raise AssertionError('startup must not access a real game')

    def stop(self):
        pass


class OfflineAliyunEngine:
    provider_id = 'aliyun'
    config_version = 'bootstrap-offline'
    created = []

    def __init__(self, credentials):
        self.credentials = credentials
        self.calls = []
        self.balance_calls = 0
        self.closed = False
        type(self).created.append(self)

    def translate(self, job):
        self.calls.append(job)
        return TranslationResult(job.job_id, 'Offline translation', None, None, 0)

    def close(self):
        self.closed = True


class OfflineDeepSeekEngine(OfflineAliyunEngine):
    provider_id = 'deepseek'
    created = []

    def __init__(self, credentials):
        super().__init__(credentials)
        self.context_enabled = credentials.context_enabled

    def get_balance(self):
        self.balance_calls += 1
        return {'is_available': True, 'balance_infos': [
            {'currency': 'CNY', 'total_balance': '10.00', 'granted_balance': '1.00', 'topped_up_balance': '9.00'}]}


class DeepSeekBootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        # Explicit paths avoid AppPaths.from_root and every real AppData location.
        self.paths = AppPaths(self.base / 'never-read.env', self.base / 'language.sqlite3', self.base / 'preferences.json')
        self.protector = MemoryProtector()
        self.store = ProviderCredentialStore(self.paths.credentials_file, protector=self.protector)
        self.identity = NS(resolve=Mock(return_value='123456789'))
        OfflineAliyunEngine.created = []
        OfflineDeepSeekEngine.created = []
        patches = (
            patch('hots_chat.credentials._WindowsDPAPI', return_value=self.protector),
            patch('hots_chat.app.AliyunEngine', OfflineAliyunEngine),
            patch('hots_chat.app.DeepSeekEngine', OfflineDeepSeekEngine),
            patch('hots_chat.app.PersistentChatSource', OfflineSource),
            patch('hots_chat.identity.IdentityClient', return_value=self.identity),
            patch('hots_chat.config.load_credentials', side_effect=AssertionError('environment credential import forbidden')),
            patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden')),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def build(self, diagnostics=None):
        controller = build_controller(self.paths, diagnostics=diagnostics)
        self.addCleanup(controller.close)
        self.addCleanup(controller.account_setup.close)
        return controller

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(predicate())

    def test_fresh_start_is_unconfigured_paused_and_starts_no_provider_requests(self):
        controller = self.build()
        self.assertFalse(controller.cloud_configured)
        self.assertTrue(controller.paused)
        self.assertIsNone(controller.account_setup.current)
        self.assertEqual(controller.account_setup.selected_provider, 'aliyun')
        self.assertEqual(OfflineAliyunEngine.created, [])
        self.assertEqual(OfflineDeepSeekEngine.created, [])
        self.identity.resolve.assert_not_called()
        self.assertFalse(self.paths.credentials_file.exists())
        self.assertFalse(self.store.deepseek.path.exists())

    def test_existing_aliyun_v1_starts_same_engine_without_rewriting_credentials(self):
        legacy = CredentialStore(self.paths.credentials_file, protector=self.protector)
        legacy.save(ALI, '123456789')
        before = self.paths.credentials_file.read_bytes()
        controller = self.build()
        self.assertEqual(controller.engine.provider_id, 'aliyun')
        self.assertEqual(controller.account_setup.current.credentials, ALI)
        self.assertTrue(controller.cloud_configured)
        self.assertFalse(controller.paused)
        self.assertEqual(self.paths.credentials_file.read_bytes(), before)
        self.assertEqual(len(OfflineAliyunEngine.created), 1)
        self.assertEqual(controller.engine.calls, [])
        self.assertEqual(OfflineDeepSeekEngine.created, [])
        self.identity.resolve.assert_not_called()

    def test_selected_deepseek_boots_saved_context_flag_without_querying_or_aliyun_fallback(self):
        self.store.save_provider('aliyun', ALI, '123456789')
        self.store.save_provider('deepseek', DeepSeekCredentials(DS_KEY, False), 'deepseek')
        self.store.select_provider('deepseek')
        before = self.paths.credentials_file.read_bytes()
        controller = self.build()
        self.assertEqual(controller.engine.provider_id, 'deepseek')
        self.assertEqual(controller.account_setup.current.provider_id, 'deepseek')
        self.assertEqual(controller.account_setup.selected_provider, 'deepseek')
        self.assertFalse(controller.engine.context_enabled)
        self.assertFalse(controller.account_setup.current.credentials.context_enabled)
        self.assertTrue(controller.cloud_configured)
        self.assertFalse(controller.paused)
        self.assertEqual(self.paths.credentials_file.read_bytes(), before)
        self.assertEqual(controller.engine.balance_calls, 0)
        self.assertEqual(controller.engine.calls, [])
        self.assertEqual(OfflineAliyunEngine.created, [])
        self.identity.resolve.assert_not_called()

    def test_missing_selected_deepseek_profile_never_enables_existing_aliyun(self):
        self.store.save_provider('aliyun', ALI, '123456789')
        self.store.select_provider('deepseek')
        controller = self.build()
        self.assertFalse(controller.cloud_configured)
        self.assertTrue(controller.paused)
        self.assertIsNone(controller.account_setup.current)
        self.assertEqual(controller.account_setup.selected_provider, 'deepseek')
        self.assertEqual(OfflineAliyunEngine.created, [])
        self.assertEqual(OfflineDeepSeekEngine.created, [])
        self.assertEqual(self.store.load_provider('aliyun').credentials, ALI)

    def test_corrupt_selection_allows_settings_recovery_without_starting_either_saved_key(self):
        self.store.save_provider('aliyun', ALI, '123456789')
        self.store.save_provider('deepseek', DeepSeekCredentials(DS_KEY, False), 'deepseek')
        before_aliyun = self.paths.credentials_file.read_bytes()
        before_deepseek = self.store.deepseek.path.read_bytes()
        self.store.selection_path.write_bytes(b'broken-selection')
        controller = self.build()
        self.assertFalse(controller.cloud_configured)
        self.assertTrue(controller.paused)
        self.assertIsNone(controller.account_setup.current)
        self.assertTrue(controller.startup_notice)
        self.assertTrue(controller.account_setup.store.selection_error)
        self.assertEqual(OfflineAliyunEngine.created, [])
        self.assertEqual(OfflineDeepSeekEngine.created, [])
        self.assertEqual(self.paths.credentials_file.read_bytes(), before_aliyun)
        self.assertEqual(self.store.deepseek.path.read_bytes(), before_deepseek)
        controller.account_setup.select_provider('deepseek')
        self.wait(lambda: not controller.account_setup.busy)
        self.assertEqual(controller.account_setup.state, 'ready', controller.account_setup.poll())
        self.assertEqual(controller.engine.provider_id, 'deepseek')
        self.assertTrue(controller.cloud_configured)
        self.assertFalse(controller.paused)
        self.assertFalse(controller.account_setup.store.selection_error)
        self.assertEqual(self.paths.credentials_file.read_bytes(), before_aliyun)

    def test_corrupt_selected_profile_starts_paused_with_notice_and_keeps_other_profile(self):
        self.store.save_provider('aliyun', ALI, '123456789')
        before = self.paths.credentials_file.read_bytes()
        self.store.deepseek.path.write_bytes(b'broken-deepseek')
        self.store.select_provider('deepseek')
        controller = self.build()
        self.assertFalse(controller.cloud_configured)
        self.assertTrue(controller.paused)
        self.assertTrue(controller.startup_notice)
        self.assertEqual(controller.account_setup.selected_provider, 'deepseek')
        self.assertEqual(self.paths.credentials_file.read_bytes(), before)
        self.assertEqual(OfflineAliyunEngine.created, [])
        self.assertEqual(OfflineDeepSeekEngine.created, [])

    def test_unselected_corrupt_deepseek_does_not_block_valid_legacy_aliyun(self):
        self.store.save_provider('aliyun', ALI, '123456789')
        self.store.deepseek.path.write_bytes(b'broken-unselected-profile')
        controller = self.build()
        self.assertTrue(controller.cloud_configured)
        self.assertFalse(controller.paused)
        self.assertEqual(controller.startup_notice, '')
        self.assertEqual(controller.engine.provider_id, 'aliyun')
        self.assertEqual(OfflineDeepSeekEngine.created, [])
        snapshot = controller.account_setup.profiles_snapshot()
        self.assertTrue(snapshot['profiles']['aliyun']['configured'])
        self.assertFalse(snapshot['profiles']['deepseek']['configured'])

    def test_deepseek_startup_registers_redaction_secret_without_recording_or_repr_leaks(self):
        self.store.save_provider('deepseek', DeepSeekCredentials(DS_KEY), 'deepseek')
        self.store.select_provider('deepseek')
        diagnostics = NS(register_secrets=Mock(), record=Mock())
        controller = self.build(diagnostics=diagnostics)
        self.assertTrue(any(DS_KEY in call.args for call in diagnostics.register_secrets.call_args_list))
        self.assertNotIn(DS_KEY, str(diagnostics.record.call_args_list))
        self.assertNotIn(DS_KEY, repr(controller.account_setup.current))
        self.assertNotIn(DS_KEY, repr(controller.account_setup.current.credentials))
        self.assertNotIn(DS_KEY, str(controller.account_setup.profiles_snapshot()))
        self.assertNotIn(DS_KEY, str(controller.account_setup.balance_snapshot()))


if __name__ == '__main__':
    unittest.main()
