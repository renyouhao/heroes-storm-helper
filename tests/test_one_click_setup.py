"""One-click configuration uses real controller with offline cloud boundaries."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hots_chat.config import Credentials, ConfigError
from hots_chat.translation.base import TranslationError
from tests import test_account_setup as fixtures


class OneClickSetupTests(unittest.TestCase):
    setUp = fixtures.AccountSetupTests.setUp
    wait_for = fixtures.AccountSetupTests.wait_for

    def connect(self):
        self.assertTrue(callable(getattr(self.setup, 'connect', None)),
                        '配置服务尚不支持一次点击完成验证、测试和保存')
        self.setup.connect(self.credentials)

    def test_one_click_saves_and_enables_cloud_without_usage_configuration(self):
        self.connect()
        events = self.wait_for('ready')
        self.assertEqual(self.store.value.credentials, self.credentials)
        self.assertTrue(self.controller.cloud_configured)
        self.assertFalse(self.controller.paused)
        self.assertFalse(self.controller.configuration_held)
        self.assertEqual(len(self.engines[0].calls), 1)
        stages = [event['stage'] for event in events if event['kind'] == 'progress']
        self.assertLess(stages.index('identity'), stages.index('translation'))
        self.assertLess(stages.index('translation'), stages.index('save'))

    def test_duplicate_connect_cannot_start_second_identity_request(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []
        def resolve(credentials):
            calls.append(credentials); entered.set(); release.wait(3); return '123456789'
        self.setup.identity = SimpleNamespace(resolve=resolve)
        self.connect(); self.assertTrue(entered.wait(2))
        with self.assertRaises(ValueError): self.setup.connect(self.credentials)
        release.set(); self.wait_for('ready')
        self.assertEqual(len(calls), 1)

    def test_identity_failure_reports_stage_without_leaking_secret(self):
        self.setup.identity = SimpleNamespace(resolve=lambda credentials: (_ for _ in ()).throw(
            ConfigError('provider echo ' + credentials.access_key_secret)))
        self.connect()
        events = self.wait_for('error')
        error = next(event for event in events if event['kind'] == 'error')
        self.assertEqual(error['stage'], 'identity')
        self.assertIn('身份验证', error['message'])
        self.assertNotIn(self.credentials.access_key_secret, str(events))
        self.assertEqual(self.engines, [])

    def test_translation_failure_reports_safe_actionable_error(self):
        engine = fixtures.Engine(self.credentials)
        engine.translate = lambda job: (_ for _ in ()).throw(TranslationError('permission', 'unsafe provider text'))
        self.setup.engine_factory = lambda credentials: engine
        self.connect()
        events = self.wait_for('error')
        error = next(event for event in events if event['kind'] == 'error')
        self.assertEqual(error['stage'], 'translation')
        self.assertIn('权限', error['message'])
        self.assertNotIn('unsafe provider', str(events))
        self.assertIsNone(self.store.value)

    def test_save_failure_is_distinguished_from_successful_translation(self):
        with patch.object(self.store, 'save', side_effect=ConfigError('private-path-and-secret')):
            self.connect()
            error = next(event for event in self.wait_for('error') if event['kind'] == 'error')
        self.assertEqual(error['stage'], 'save')
        self.assertIn('加密保存', error['message'])
        self.assertNotIn('private-path', str(error))
        self.assertEqual(self.controller.account, 'unconfigured')

    def test_cancel_during_identity_never_reaches_translation_or_save(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def resolve(credentials):
            entered.set(); release.wait(3); return '123456789'
        self.setup.identity = SimpleNamespace(resolve=resolve)
        self.connect(); self.assertTrue(entered.wait(2))
        self.setup.cancel(); release.set(); self.wait_for('cancelled')
        self.assertEqual(self.engines, [])
        self.assertIsNone(self.store.value)
        self.assertFalse(self.controller.configuration_held)

    def test_retry_revalidates_new_credentials_and_succeeds_without_second_step(self):
        entered = []
        def resolve(credentials):
            entered.append(credentials)
            if credentials == self.credentials: raise ConfigError('not authorized')
            return '123456789'
        self.setup.identity = SimpleNamespace(resolve=resolve)
        self.connect(); self.wait_for('error')
        if self.setup._thread: self.setup._thread.join(2)
        replacement = Credentials('replacement-id', 'replacement-secret')
        self.setup.connect(replacement)
        self.wait_for('ready')
        self.assertEqual(self.store.value.credentials, replacement)
        self.assertEqual(len(entered), 2)


if __name__ == '__main__': unittest.main()
