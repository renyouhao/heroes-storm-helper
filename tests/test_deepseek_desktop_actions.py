"""DeepSeek renderer contract tests using pure fakes; no Tk, files or network."""
from copy import deepcopy
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from hots_chat.credentials import SavedCredentials
from hots_chat.desktop_actions import DesktopActionError, DesktopActions, validate_action
from hots_chat.desktop_protocol import ProtocolError, validate_command, validate_request
from hots_chat.desktop_runtime import TkRuntime
from hots_chat.translation.deepseek import DeepSeekCredentials
from hots_chat.ui.main_window import MainWindow
from tests.test_desktop_actions import fake_window
from tests.test_desktop_runtime import fake_window as runtime_window


KEY = 'synthetic-desktop-deepseek-private-830147'
COMMANDS = ('account_connect_deepseek', 'account_select_provider', 'account_refresh_balance')


def deepseek_service():
    return NS(
        state='ready', busy=False, selected_provider='deepseek',
        current=SavedCredentials(DeepSeekCredentials(KEY, False), 'deepseek', 'deepseek'),
        connect=Mock(), connect_deepseek=Mock(), select_provider=Mock(),
        refresh_balance=Mock(return_value=True), cancel=Mock(), delete=Mock(), poll=Mock(return_value=[]),
        profiles_snapshot=Mock(return_value={
            'selected_provider': 'deepseek', 'pending_provider': None,
            'profiles': {
                'aliyun': {'configured': True, 'maskedKey': '••••4321', 'context_enabled': False},
                'deepseek': {'configured': True, 'maskedKey': '••••0147', 'context_enabled': False,
                             'apiKey': KEY, 'credentials': {'api_key': KEY}},
            },
            'untrusted_private_field': KEY,
        }),
        balance_snapshot=Mock(return_value={
            'provider': 'deepseek', 'busy': False, 'error': '', 'as_of': '2026-10-24T10:20:30+00:00',
            'is_available': True, 'api_key': KEY,
            'balance_infos': [{'currency': 'CNY', 'total_balance': '11.25', 'granted_balance': '1.00',
                               'topped_up_balance': '10.25', 'api_key': KEY}],
        }),
    )


class DeepSeekActionValidationTests(unittest.TestCase):
    def test_new_commands_cross_protocol_only_with_exact_allowed_fields(self):
        accepted = (
            ('account_connect_deepseek', {'apiKey': KEY, 'contextEnabled': True}),
            ('account_connect_deepseek', {'apiKey': KEY, 'contextEnabled': False}),
            ('account_select_provider', {'provider': 'deepseek'}),
            ('account_select_provider', {'provider': 'deepseek', 'contextEnabled': False}),
            ('account_select_provider', {'provider': 'aliyun', 'contextEnabled': False}),
            ('account_refresh_balance', {}),
        )
        for command, payload in accepted:
            with self.subTest(command=command, payload=payload):
                self.assertEqual(validate_action(command, payload), payload)
                self.assertEqual(validate_command(command, payload), payload)
                self.assertEqual(validate_request({'request_id': 7, 'command': command, 'payload': payload}),
                                 (7, command, payload))
                with self.assertRaises(ProtocolError):
                    validate_command(command, {**payload, 'unexpected': KEY})

    def test_context_requires_exact_bool_and_rejects_aliyun_true(self):
        for value in (0, 1, 'true', 'false', None, [], {}):
            for command, payload in (
                ('account_connect_deepseek', {'apiKey': KEY, 'contextEnabled': value}),
                ('account_select_provider', {'provider': 'deepseek', 'contextEnabled': value}),
            ):
                with self.subTest(command=command, value=value), self.assertRaises(DesktopActionError):
                    validate_action(command, payload)
        with self.assertRaises(DesktopActionError):
            validate_action('account_select_provider', {'provider': 'aliyun', 'contextEnabled': True})

    def test_missing_wrong_or_extra_fields_fail_before_any_side_effect_without_echoing_key(self):
        invalid = (
            ('account_connect_deepseek', {'apiKey': KEY}),
            ('account_connect_deepseek', {'contextEnabled': True}),
            ('account_connect_deepseek', {'apiKey': KEY, 'contextEnabled': True, 'provider': 'deepseek'}),
            ('account_select_provider', {}),
            ('account_select_provider', {'provider': 'other-' + KEY}),
            ('account_select_provider', {'provider': 'deepseek', 'apiKey': KEY}),
            ('account_refresh_balance', {'apiKey': KEY}),
        )
        for command, payload in invalid:
            window = fake_window()
            window.controller.account_setup = deepseek_service()
            with self.subTest(command=command, payload=payload), self.assertRaises(DesktopActionError) as caught:
                DesktopActions(window).dispatch(command, payload)
            self.assertNotIn(KEY, str(caught.exception))
            window.clear_draft.assert_not_called()
            window.quick_input.cancel.assert_not_called()
            window.controller.account_setup.connect_deepseek.assert_not_called()
            window.controller.account_setup.select_provider.assert_not_called()
            window.controller.account_setup.refresh_balance.assert_not_called()

    def test_key_validation_never_echoes_invalid_secret(self):
        for value in (True, None, '', ' ', 'bad secret-' + KEY, 'bad\n' + KEY, '密钥' + KEY, 'x' * 1025):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(DesktopActionError) as caught:
                validate_action('account_connect_deepseek', {'apiKey': value, 'contextEnabled': True})
            self.assertNotIn(KEY, str(caught.exception))


class DeepSeekActionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.window = fake_window()
        self.service = deepseek_service()
        self.window.controller.account_setup = self.service
        self.window.controller.engine = NS(provider_id='deepseek')
        self.actions = DesktopActions(self.window)
        self.addCleanup(self.actions.close)

    def test_connect_delegates_key_once_and_forwards_context_choice(self):
        result = self.actions.dispatch('account_connect_deepseek', {'apiKey': ' ' + KEY + ' ', 'contextEnabled': False})
        self.service.connect_deepseek.assert_called_once_with(KEY, context_enabled=False)
        self.service.connect.assert_not_called()
        self.window.clear_draft.assert_called_once_with()
        self.window.quick_input.cancel.assert_called_once_with()
        self.assertIn('DeepSeek', result['message'])
        self.assertNotIn(KEY, str(result))
        self.assertNotIn(KEY, str(self.actions.snapshot()))

    def test_saved_provider_selection_does_not_need_key_and_only_changes_explicit_context(self):
        self.actions.dispatch('account_select_provider', {'provider': 'deepseek'})
        self.service.select_provider.assert_called_with('deepseek')
        self.actions.dispatch('account_select_provider', {'provider': 'deepseek', 'contextEnabled': True})
        self.service.select_provider.assert_called_with('deepseek', context_enabled=True)
        self.service.connect_deepseek.assert_not_called()
        self.assertIn('验证成功后启用', self.window._desktop_account_message)

    def test_snapshot_allowlists_profiles_and_maps_all_official_balance_fields(self):
        snapshot = self.actions.snapshot()['account']
        self.assertNotIn(KEY, str(snapshot))
        self.assertEqual(snapshot['provider'], 'deepseek')
        self.assertEqual(snapshot['maskedKey'], '••••0147')
        self.assertFalse(snapshot['contextEnabled'])
        providers = {provider['id']: provider for provider in snapshot['providers']}
        self.assertFalse(providers['aliyun']['contextEnabled'])
        self.assertFalse(providers['deepseek']['contextEnabled'])
        self.assertEqual(snapshot['balance']['balances'], [
            {'currency': 'CNY', 'totalBalance': '11.25', 'grantedBalance': '1.00', 'toppedUpBalance': '10.25'}])
        self.assertEqual(snapshot['balance']['updatedAt'], '2026-10-24T10:20:30+00:00')
        self.assertTrue(snapshot['balance']['available'])
        self.service.refresh_balance.assert_not_called()

    def test_refresh_balance_preserves_runtime_draft_translation_and_quick_input(self):
        window = runtime_window()
        window.controller.account_setup = self.service
        window.controller.engine = NS(provider_id='deepseek')
        window.clear_draft = Mock()
        window.input.text = '正在编辑的中文草稿'
        window.vm.copy_text = 'Existing translated preview'
        window.vm.manual_provenance = 'DeepSeek'
        window.quick_input._visible = True
        runtime = TkRuntime(window)
        self.addCleanup(runtime.actions.close)
        runtime._preview_source = window.input.text
        before = deepcopy(runtime.snapshot())
        result = runtime.dispatch('account_refresh_balance', {})
        after = runtime.snapshot()
        for field in ('draft', 'preview', 'previewSource', 'manualState', 'manualBusy'):
            self.assertEqual(after[field], before[field], field)
        self.assertTrue(window.quick_input._visible)
        window.clear_draft.assert_not_called()
        window.quick_input.cancel.assert_not_called()
        self.service.refresh_balance.assert_called_once_with()
        self.assertIn('正在查询', result['message'])

    def test_refresh_not_started_reports_fixed_error_but_existing_query_remains_busy(self):
        self.service.refresh_balance.return_value = False
        with self.assertRaises(DesktopActionError) as caught:
            self.actions.dispatch('account_refresh_balance', {})
        self.assertEqual(str(caught.exception), '余额查询未启动，请确认当前 DeepSeek 账户已连接且配置任务已结束。')
        state = self.service.balance_snapshot.return_value
        state['busy'] = True
        result = self.actions.dispatch('account_refresh_balance', {})
        self.assertIn('正在查询', result['message'])
        self.window.clear_draft.assert_not_called()
        self.window.quick_input.cancel.assert_not_called()

    def test_aliyun_balance_action_does_not_query_deepseek_or_cancel_input(self):
        self.service.selected_provider = 'aliyun'
        with self.assertRaises(DesktopActionError):
            self.actions.dispatch('account_refresh_balance', {})
        self.service.refresh_balance.assert_not_called()
        self.window.clear_draft.assert_not_called()
        self.window.quick_input.cancel.assert_not_called()

    def test_switch_and_key_changes_reject_busy_or_demo_without_partial_effects(self):
        commands = (
            ('account_connect_deepseek', {'apiKey': KEY, 'contextEnabled': True}),
            ('account_select_provider', {'provider': 'deepseek'}),
        )
        for busy, demo in ((True, False), (False, True)):
            self.service.busy = busy
            self.window.controller.is_demo = demo
            for command, payload in commands:
                with self.subTest(busy=busy, demo=demo, command=command), self.assertRaises(DesktopActionError):
                    self.actions.dispatch(command, payload)
        self.window.clear_draft.assert_not_called()
        self.window.quick_input.cancel.assert_not_called()
        self.service.connect_deepseek.assert_not_called()
        self.service.select_provider.assert_not_called()

    def test_provider_exception_never_reaches_error_or_snapshot(self):
        for command, payload, method in (
            ('account_connect_deepseek', {'apiKey': KEY, 'contextEnabled': True}, self.service.connect_deepseek),
            ('account_select_provider', {'provider': 'deepseek'}, self.service.select_provider),
            ('account_refresh_balance', {}, self.service.refresh_balance),
        ):
            method.side_effect = RuntimeError(KEY)
            with self.subTest(command=command), self.assertRaises(DesktopActionError) as caught:
                self.actions.dispatch(command, payload)
            self.assertNotIn(KEY, str(caught.exception))
            self.assertNotIn(KEY, str(self.actions.snapshot()))

    def test_selecting_unconfigured_provider_event_replaces_pending_feedback(self):
        window = self.window
        window._desktop_account_message = '正在应用已保存的翻译服务，验证成功后启用…'
        window.controller.cloud_configured = False
        window.controller.paused = True
        window.enable_configured_cloud = Mock()
        window.account_button = NS(configure=Mock())
        window.credential_dialog = None
        window.status_var = NS(set=Mock())
        window.refresh_service_status = Mock()
        self.service.current = None
        self.service.state = 'unconfigured'
        self.service.poll.return_value = [{'kind': 'selected', 'provider': 'deepseek', 'account': 'unconfigured'}]
        MainWindow._account_events(window)
        self.assertEqual(window._desktop_account_message, '已切换翻译服务，请配置当前服务的个人密钥。')
        window.enable_configured_cloud.assert_called_once_with()
        window.account_button.configure.assert_called_with(text='配置云翻译')
        window.clear_draft.assert_called_once_with()
        window.quick_input.cancel.assert_called_once_with()
        snapshot = self.actions.snapshot()['account']
        self.assertFalse(snapshot['configured'])
        self.assertEqual(snapshot['provider'], 'deepseek')


if __name__ == '__main__':
    unittest.main()
