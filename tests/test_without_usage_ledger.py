import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hots_chat.app import build_controller
from hots_chat.config import Credentials
from hots_chat.credentials import CredentialStore
from hots_chat.models import TranslationResult
from hots_chat.paths import AppPaths


class OfflineSource:
    def stop(self):
        pass


class OfflineEngine:
    provider_id = 'test'
    config_version = '1'

    def __init__(self, credentials):
        self.calls = []

    def translate(self, job):
        self.calls.append(job)
        return TranslationResult(job.job_id, 'Hello', None, None, 0)

    def close(self):
        pass


class WithoutUsageLedgerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        environment = patch.dict(os.environ, {'LOCALAPPDATA': str(self.base)})
        environment.start()
        self.addCleanup(environment.stop)
        self.paths = AppPaths.from_root(self.base)

    def build(self):
        with patch('hots_chat.app.AliyunEngine', OfflineEngine), \
             patch('hots_chat.app.PersistentChatSource', OfflineSource), \
             patch('hots_chat.identity.IdentityClient', return_value=SimpleNamespace(resolve=lambda key: '123456789')):
            controller = build_controller(self.paths)
        self.addCleanup(controller.close)
        self.addCleanup(controller.account_setup.close)
        return controller

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(predicate())

    def test_first_launch_does_not_create_usage_database_or_marker(self):
        controller = self.build()
        self.assertFalse(controller.cloud_configured)
        directory = self.paths.preferences_file.parent
        self.assertFalse((directory / 'usage.sqlite3').exists())
        self.assertFalse((directory / 'usage.sqlite3.initialized').exists())

    def test_corrupt_legacy_ledger_does_not_block_setup_translation_or_reload(self):
        directory = self.paths.preferences_file.parent
        directory.mkdir(parents=True, exist_ok=True)
        legacy = {'usage.sqlite3': b'corrupt old database',
                  'usage.sqlite3.initialized': b'old-ledger-identity'}
        for name, contents in legacy.items():
            (directory / name).write_bytes(contents)
        controller = self.build()
        credentials = Credentials('synthetic_id', 'synthetic_secret')
        controller.account_setup.connect(credentials)
        self.wait_for(lambda: not controller.account_setup.busy)
        self.assertEqual(controller.account_setup.state, 'ready', controller.account_setup.poll())
        self.assertFalse(controller.paused)
        self.assertEqual(CredentialStore(self.paths.credentials_file).load().credentials, credentials)
        controller.submit_manual('集合', 'en')
        self.wait_for(lambda: controller.idle)
        self.assertEqual([job.text for job in controller.engine.calls], ['你好', '集合'])
        reloaded = self.build()
        self.assertTrue(reloaded.cloud_configured)
        reloaded.submit_manual('撤退', 'en')
        self.wait_for(lambda: reloaded.idle)
        self.assertEqual([job.text for job in reloaded.engine.calls], ['撤退'])
        for name, contents in legacy.items():
            self.assertEqual((directory / name).read_bytes(), contents)


if __name__ == '__main__':
    unittest.main()
