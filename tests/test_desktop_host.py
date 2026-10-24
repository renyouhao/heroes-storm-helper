"""桌面壳请求关联、退出与允许命令；不创建真实 WebView 或访问游戏。"""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from hots_chat.desktop import DesktopApi,DesktopError,NativeClient,frontend_entry


class HostRequestTests(unittest.TestCase):
    def client(self,replies):
        connection=Mock()
        process=Mock()
        process.is_alive.return_value=True
        connection.poll.return_value=True
        connection.recv.side_effect=replies
        return NativeClient(connection,process,timeout=.01),connection,process

    def test_late_result_never_satisfies_a_different_request(self):
        client,connection,_=self.client([
            {'request_id':'old','ok':True,'result':'previous'},
            {'request_id':'new','ok':True,'result':'current'},
        ])
        with patch('hots_chat.desktop.uuid.uuid4',return_value=SimpleNamespace(hex='new')):
            self.assertEqual(client.request('snapshot')['result'],'current')
        connection.send.assert_called_once_with({'request_id':'new','command':'snapshot','payload':{}})

    def test_timeout_does_not_resend_or_claim_success(self):
        client,connection,_=self.client([])
        connection.poll.return_value=False
        with self.assertRaisesRegex(DesktopError,'不会自动重试'):
            client.request('translate',{'text':'你好','target':'en'})
        self.assertEqual(connection.send.call_count,1)

    def test_disconnected_service_is_reported_to_ui_without_traceback(self):
        client,connection,_=self.client([])
        connection.recv.side_effect=EOFError
        api=DesktopApi(client)
        result=api.snapshot()
        self.assertFalse(result['connected'])
        self.assertIn('连接已断开',result['error'])

    def test_close_is_idempotent_and_waits_for_native_exit(self):
        client,connection,process=self.client([{'request_id':'stop','ok':True}])
        process.is_alive.side_effect=[True,True,False]
        with patch('hots_chat.desktop.uuid.uuid4',return_value=SimpleNamespace(hex='stop')):
            client.close()
            client.close()
        connection.close.assert_called_once()
        process.join.assert_called_once_with(timeout=6)
        process.terminate.assert_not_called()

    def test_frontend_entry_requires_built_assets(self):
        with TemporaryDirectory() as folder:
            with self.assertRaises(DesktopError):frontend_entry(folder)
            expected=Path(folder)/'frontend'/'dist'/'index.html'
            expected.parent.mkdir(parents=True)
            expected.write_text('<!doctype html>',encoding='utf-8')
            self.assertEqual(frontend_entry(folder),expected)

    def test_webview_methods_have_no_arbitrary_python_dispatch(self):
        client=Mock()
        api=DesktopApi(client)
        for name in ('__class__','shutdown','snapshot','eval','open_url'):
            with self.subTest(name=name):self.assertFalse(api.command(name,{})['ok'])
        client.request.assert_not_called()
        self.assertFalse(api.command('translate','bad')['ok'])


if __name__=='__main__':unittest.main()
