"""Provider setup links use fixed native-browser destinations without changing accounts."""
import unittest
from unittest.mock import patch

from hots_chat.desktop_actions import DesktopActionError, DesktopActions, validate_action
from hots_chat.desktop_protocol import ProtocolError, validate_command
from tests.test_desktop_actions import fake_window


class ProviderPortalTests(unittest.TestCase):
    def test_only_fixed_provider_fields_cross_protocol(self):
        for provider in ('aliyun','deepseek'):
            self.assertEqual(validate_command('account_open_provider_portal', {'provider':provider}), {'provider':provider})
        for payload in ({}, {'provider':'other'}, {'provider':[]}, {'provider':True},
                        {'provider':'deepseek','url':'https://example.com'},
                        {'provider':'deepseek','contextEnabled':True}):
            with self.subTest(payload=payload),self.assertRaises(ProtocolError):
                validate_command('account_open_provider_portal',payload)

    def test_each_portal_opens_fixed_url_without_touching_account_or_draft(self):
        for provider,url in (('aliyun','https://mt.console.aliyun.com/service'),
                             ('deepseek','https://platform.deepseek.com/api_keys')):
            window=fake_window()
            actions=DesktopActions(window)
            self.addCleanup(actions.close)
            with patch('webbrowser.open',return_value=True) as browser:
                result=actions.dispatch('account_open_provider_portal',{'provider':provider})
            browser.assert_called_once_with(url)
            self.assertIn('已请求',result['message'])
            window.clear_draft.assert_not_called()
            window.quick_input.cancel.assert_not_called()
            window.controller.account_setup.connect.assert_not_called()
            window.controller.account_setup.cancel.assert_not_called()
            window.controller.account_setup.delete.assert_not_called()

    def test_browser_failure_has_exact_manual_url_and_no_false_success(self):
        window=fake_window()
        actions=DesktopActions(window)
        self.addCleanup(actions.close)
        for outcome in (False,OSError('untrusted browser details')):
            options={'side_effect':outcome} if isinstance(outcome,Exception) else {'return_value':outcome}
            with patch('webbrowser.open',**options),self.assertRaises(DesktopActionError) as caught:
                actions.dispatch('account_open_provider_portal',{'provider':'deepseek'})
            self.assertIn('https://platform.deepseek.com/api_keys',str(caught.exception))
            self.assertNotIn('untrusted',str(caught.exception))

    def test_demo_does_not_launch_real_account_portal(self):
        window=fake_window()
        window.controller.is_demo=True
        actions=DesktopActions(window)
        self.addCleanup(actions.close)
        with patch('webbrowser.open') as browser,self.assertRaises(DesktopActionError):
            actions.dispatch('account_open_provider_portal',{'provider':'deepseek'})
        browser.assert_not_called()


if __name__ == '__main__':
    unittest.main()
