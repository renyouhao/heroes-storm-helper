import importlib
import json
import os
from pathlib import Path
import socket
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hots_chat.app import build_controller as real_build_controller
from hots_chat.config import Credentials
from hots_chat.models import TranslationResult


ACCESS_ID = 'synthetic_diagnostic_id_79823'
ACCESS_SECRET = 'synthetic_diagnostic_secret_29487'
ACCOUNT = '1234567890123456'


class OfflineSource:
    def start(self, emit):
        raise AssertionError('诊断不得开始游戏采集')

    def stop(self):
        pass


class OfflineEngine:
    provider_id = 'aliyun'
    config_version = 'diagnostic-offline'

    def __init__(self, credentials):
        self.credentials = credentials

    def translate(self, job):
        if (job.text, job.source, job.target) != ('你好', 'zh', 'en'):
            raise AssertionError('诊断只能执行最小翻译')
        # Provider text must never enter the report, even after success.
        return TranslationResult(job.job_id, ACCESS_SECRET + ACCOUNT, None, None, 0)

    def close(self):
        pass


class CloudDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.input = self.base / 'private-source.txt'
        self.output = self.base / 'report.json'
        self.controllers = []
        self.paths = []
        # An accidentally unpatched SDK/identity boundary must never reach a network.
        self.network = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def diagnostic(self):
        try:
            return importlib.import_module('hots_chat.cloud_diagnostic')
        except ModuleNotFoundError:
            self.fail('缺少隔离云配置诊断功能')

    def write_credentials(self, encoding='utf-8-sig'):
        self.input.write_text(
            f'AccessKey ID： {ACCESS_ID}\nAccessKey Secret： {ACCESS_SECRET}\n', encoding=encoding)

    def builder(self, paths):
        self.paths.append(paths)
        with patch('hots_chat.app.AliyunEngine', OfflineEngine), \
             patch('hots_chat.app.PersistentChatSource', OfflineSource), \
             patch('hots_chat.identity.IdentityClient', return_value=SimpleNamespace(resolve=lambda credentials: ACCOUNT)):
            controller = real_build_controller(paths)
        self.controllers.append(controller)
        return controller

    def run_check(self, builder=None):
        module = self.diagnostic()
        with patch.object(module, 'build_controller', builder or self.builder):
            status = module.check_cloud_configuration(self.input, self.output)
        report_text = self.output.read_text(encoding='utf-8')
        for private in (ACCESS_ID, ACCESS_SECRET, ACCOUNT, str(self.input), self.input.name):
            self.assertNotIn(private, report_text)
        return status, json.loads(report_text)

    @unittest.skipUnless(os.name == 'nt', '需要真实 Windows DPAPI')
    def test_real_controller_encrypts_reloads_and_translates_in_removed_isolation(self):
        self.write_credentials()
        status, report = self.run_check()
        self.assertEqual(status, 0)
        self.assertTrue(report['passed'])
        self.assertTrue(report['fresh_user'])
        self.assertTrue(report['credentials_encrypted'])
        self.assertTrue(report['credentials_reloaded'])
        self.assertTrue(report['restarted_translation_succeeded'])
        self.assertEqual(report['cloud_requests_started'], 2)
        self.assertEqual(report['translation_characters'], 4)
        self.assertEqual(report['local_reserved'], 4)
        self.assertEqual(report['external_used'], 0)
        self.assertFalse(report['real_user_configuration_modified'])
        self.assertFalse(report['game_access'])
        self.assertFalse(report['user_ui_automated'])
        self.assertTrue(report['isolated_data_removed'])
        self.assertEqual(len(self.controllers), 2)
        self.assertEqual(self.paths[0], self.paths[1])
        self.assertTrue(all(not paths.ledger_file.parent.exists() for paths in self.paths))
        self.assertTrue(all(not controller.thread.is_alive() for controller in self.controllers))
        self.assertTrue(all(not controller.account_setup.busy for controller in self.controllers))
        self.assertGreaterEqual(report['elapsed_ms']['initial_configuration'], 0)
        self.assertGreaterEqual(report['elapsed_ms']['reload_translation'], 0)

    @unittest.skipUnless(os.name == 'nt', '需要真实 Windows DPAPI')
    def test_gb18030_export_works_without_environment_fallback(self):
        self.write_credentials('gb18030')
        with patch.dict(os.environ, {'ALIBABA_CLOUD_ACCESS_KEY_ID': 'wrong',
                                     'ALIBABA_CLOUD_ACCESS_KEY_SECRET': 'wrong'}):
            status, report = self.run_check()
        self.assertEqual(status, 0)
        self.assertTrue(report['passed'])
        self.assertEqual(self.controllers[1].account_setup.current.credentials,
                         Credentials(ACCESS_ID, ACCESS_SECRET))

    @unittest.skipUnless(os.name == 'nt', '需要真实 Windows DPAPI')
    def test_export_metadata_and_space_separated_keys_are_supported(self):
        self.input.write_text(
            '登录名称 synthetic-login\n登录密码 synthetic-password\n'
            f'AccessKey ID {ACCESS_ID}\nAccessKey Secret\t{ACCESS_SECRET}\n'
            'SecurityPhoneDevice synthetic-phone\nSecurityEmailDevice synthetic-email\n',
            encoding='utf-8-sig')
        status, report = self.run_check()
        self.assertEqual(status, 0)
        self.assertTrue(report['passed'])
        report_text = self.output.read_text(encoding='utf-8')
        for metadata in ('synthetic-login', 'synthetic-password', 'synthetic-phone', 'synthetic-email'):
            self.assertNotIn(metadata, report_text)

    def test_malformed_export_fails_without_constructing_controller(self):
        self.input.write_text(f'AccessKey ID: {ACCESS_ID}\nAccessKey ID: {ACCESS_SECRET}', encoding='utf-8')
        status, report = self.run_check(builder=lambda paths: self.fail('无效输入不得创建控制器'))
        self.assertEqual(status, 2)
        self.assertFalse(report['passed'])
        self.assertEqual(report['error'], {'type': 'credentials_invalid', 'stage': 'read'})
        self.assertEqual(report['cloud_requests_started'], 0)
        self.assertTrue(report['isolated_data_removed'])

    def test_missing_export_error_is_safe_and_has_no_private_path(self):
        status, report = self.run_check(builder=lambda paths: self.fail('不得创建控制器'))
        self.assertEqual(status, 2)
        self.assertEqual(report['error'], {'type': 'credentials_invalid', 'stage': 'read'})

    def test_builder_exception_never_discloses_provider_or_keys(self):
        self.write_credentials()
        def broken_builder(paths):
            self.paths.append(paths)
            raise RuntimeError(ACCESS_SECRET + ACCOUNT)
        status, report = self.run_check(broken_builder)
        self.assertEqual(status, 2)
        self.assertEqual(report['error'], {'type': 'diagnostic_failed', 'stage': 'startup'})
        self.assertTrue(report['isolated_data_removed'])
        self.assertFalse(self.paths[0].ledger_file.parent.exists())

    @unittest.skipUnless(os.name == 'nt', '需要真实 Windows DPAPI')
    def test_background_identity_failure_is_sanitized_and_joined(self):
        self.write_credentials()
        def failed_identity(credentials):
            raise RuntimeError(ACCESS_SECRET + ACCOUNT)
        def builder(paths):
            controller = self.builder(paths)
            controller.account_setup.identity = SimpleNamespace(resolve=failed_identity)
            return controller
        status, report = self.run_check(builder)
        self.assertEqual(status, 2)
        self.assertFalse(report['passed'])
        self.assertEqual(report['error'], {'type': 'configuration_failed', 'stage': 'identity'})
        self.assertEqual(report['cloud_requests_started'], 0)
        self.assertTrue(report['isolated_data_removed'])
        self.assertFalse(self.controllers[0].account_setup.busy)

    @unittest.skipUnless(os.name == 'nt', '需要真实 Windows DPAPI')
    def test_reloaded_empty_translation_cannot_pass(self):
        self.write_credentials()
        class EmptyEngine(OfflineEngine):
            def translate(self, job):
                return TranslationResult(job.job_id, '', None, None, 0)
        def builder(paths):
            controller = self.builder(paths)
            if len(self.controllers) == 2:
                controller.engine = EmptyEngine(controller.account_setup.current.credentials)
            return controller
        status, report = self.run_check(builder)
        self.assertEqual(status, 2)
        self.assertFalse(report['passed'])
        self.assertFalse(report['restarted_translation_succeeded'])
        self.assertTrue(report['isolated_data_removed'])

    def test_report_cannot_overwrite_the_input_credentials(self):
        self.write_credentials()
        original = self.input.read_bytes()
        module = self.diagnostic()
        self.assertEqual(module.check_cloud_configuration(self.input, self.input), 2)
        self.assertEqual(self.input.read_bytes(), original)

    def test_ready_enqueued_after_poll_is_not_lost_when_worker_ends(self):
        class CompletionBetweenPollAndBusy:
            state = 'ready'
            polls = 0

            @property
            def busy(self):
                return self.polls == 0

            def poll(self):
                self.polls += 1
                return [] if self.polls == 1 else [{'kind': 'ready'}]

        report = {'events': []}
        self.diagnostic()._configuration_result(
            CompletionBetweenPollAndBusy(), report, time.monotonic() + 1)
        self.assertEqual(report['events'], [{'kind': 'ready', 'stage': 'activate'}])


if __name__ == '__main__':
    unittest.main()
