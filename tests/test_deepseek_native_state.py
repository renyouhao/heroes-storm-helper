"""Native account callbacks with plain fakes; never create or automate a window."""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from hots_chat.ui.credential_dialog import CredentialDialog
from hots_chat.ui.main_window import MainWindow


class Value:
    def __init__(self, value=''):
        self.value=value

    def get(self):
        return self.value

    def set(self, value):
        self.value=value


def balance():
    return {'busy': False, 'error': '', 'is_available': True,
            'as_of': '2026-10-24T02:00:00+00:00',
            'balance_infos': [{'currency': 'CNY', 'total_balance': '12.50',
                              'granted_balance': '2.50', 'topped_up_balance': '10.00'}]}


class DeepSeekNativeStateTests(unittest.TestCase):
    def service(self):
        return NS(selected_provider='deepseek', busy=False, poll=Mock(return_value=[]),
                  refresh_balance=Mock(return_value=True), balance_snapshot=Mock(return_value=balance()))

    def test_quota_uses_async_balance_without_browser_and_handles_unstarted_query(self):
        service=self.service()
        window=NS(controller=NS(account_setup=service), status_var=Value())
        with patch('webbrowser.open') as browser:
            self.assertTrue(MainWindow.open_cloud_quota(window))
            service.refresh_balance.return_value=False
            self.assertFalse(MainWindow.open_cloud_quota(window))
            self.assertIn('未启动', window.status_var.get())
            service.balance_snapshot.return_value['busy']=True
            self.assertTrue(MainWindow.open_cloud_quota(window))
            browser.assert_not_called()

    def test_balance_completion_reaches_dialog_without_an_account_poll_event(self):
        service=self.service()
        window=NS(controller=NS(account_setup=service), status_var=Value(),
                  credential_dialog=NS(_show_balance=Mock()))
        MainWindow._account_events(window)
        window.credential_dialog._show_balance.assert_called_once_with()
        self.assertIn('12.50', window.status_var.get())
        service.poll.assert_called_once_with()

    def test_native_balance_shows_all_amounts_time_and_unavailability(self):
        dialog=CredentialDialog.__new__(CredentialDialog)
        dialog.service=self.service()
        dialog.balance_button=NS(configure=Mock())
        dialog.balance_status=Value()
        dialog._show_balance()
        for value in ('12.50', '2.50', '10.00', '2026-10-24 10:00:00', '北京时间'):
            self.assertIn(value, dialog.balance_status.get())
        dialog.service.balance_snapshot.return_value['is_available']=False
        dialog._show_balance()
        self.assertIn('不可用', dialog.balance_status.get())
        dialog.service.balance_snapshot.return_value['busy']=True
        dialog._show_balance()
        self.assertIn('正在查询', dialog.balance_status.get())
        dialog.balance_button.configure.assert_called_with(state='disabled')

    def test_native_unstarted_balance_query_does_not_claim_loading(self):
        dialog=CredentialDialog.__new__(CredentialDialog)
        dialog.service=self.service()
        dialog.service.refresh_balance.return_value=False
        dialog.balance_status=Value()
        dialog.refresh_balance()
        self.assertIn('未启动', dialog.balance_status.get())


if __name__ == '__main__':
    unittest.main()
