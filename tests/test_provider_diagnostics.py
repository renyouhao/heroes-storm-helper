"""Provider operations export correlated metadata without secrets or balance amounts."""
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from hots_chat.controller import ChatController
from hots_chat.diagnostics import DiagnosticRecorder
from hots_chat.provider_accounts import ProviderCredentialStore, TranslationAccounts
from test_provider_accounts import ALI, BALANCE, DS_KEY, Engine, MemoryProtector, Source


class ProviderDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.base=Path(self.temporary.name)
        self.recorder=DiagnosticRecorder(self.base/'logs',enabled=True)
        self.controller=ChatController(Source(),Engine('aliyun',ALI),'123456',diagnostics=self.recorder)
        self.controller.cloud_configured=True
        self.store=ProviderCredentialStore(self.base/'credentials.dpapi',protector=MemoryProtector())
        self.store.save_provider('aliyun',ALI,'123456')
        self.controls=SimpleNamespace()
        self.accounts=TranslationAccounts(self.controller,self.store,
            SimpleNamespace(resolve=lambda credentials:'123456'),
            lambda credentials:Engine('aliyun',credentials),
            lambda credentials:Engine('deepseek',credentials,self.controls),
            saved=self.store.load_active())
        self.network=patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden'))
        self.network.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        release=getattr(self.controls,'balance_release',None)
        if release is not None:release.set()
        try:
            self.accounts.close()
            self.accounts.join(3)
        finally:
            self.controller.diagnostics=self.recorder
            self.controller.close()
            self.recorder.close()
            self.network.stop()
            self.temporary.cleanup()

    def wait(self,predicate):
        deadline=time.monotonic()+3
        while not predicate() and time.monotonic()<deadline:time.sleep(.005)
        self.assertTrue(predicate())

    def connect(self):
        self.accounts.connect_deepseek(DS_KEY)
        self.wait(lambda:not self.accounts.busy)
        self.assertEqual(self.accounts.state,'ready',self.accounts.poll())

    def rows(self,operation=None):
        path=self.recorder.export_report(self.base/'report.zip')
        with zipfile.ZipFile(path) as archive:
            rows=[json.loads(line) for name in archive.namelist() if name.startswith('helper.log')
                  for line in archive.read(name).decode('utf-8').splitlines()]
        if operation is not None:
            rows=[row for row in rows if row.get('event')=='configuration.progress'
                  and row.get('operation')==operation]
        self.assertNotIn(DS_KEY,json.dumps(rows))
        self.assertNotIn(ALI.access_key_secret,json.dumps(rows))
        return rows

    def block_balance(self):
        self.controls.balance_entered=threading.Event()
        self.controls.balance_release=threading.Event()
        self.assertTrue(self.accounts.refresh_balance())
        self.assertTrue(self.controls.balance_entered.wait(1))

    def test_configuration_progress_shares_provider_and_request_id_and_exports_redaction(self):
        self.connect()
        self.recorder.record('message.received',original='synthetic '+DS_KEY)
        rows=[row for row in self.rows('configuration') if row['source']=='deepseek']
        self.assertEqual(len({row['request_ref'] for row in rows}),1)
        self.assertEqual([row['outcome'] for row in rows if row['outcome'] in ('accepted','start','ready')],
                         ['accepted','start','ready'])
        self.assertEqual([row['stage'] for row in rows if row['outcome']=='progress'],
                         ['waiting','identity','client','translation','save','activate'])
        self.assertTrue(all(row['request_ref'] for row in rows))
        self.assertIn('[redacted]',json.dumps(self.rows()))

    def test_configuration_error_and_cancelled_request_export_fixed_terminal_reasons(self):
        self.controls.balance_error=DS_KEY
        self.accounts.connect_deepseek(DS_KEY)
        self.wait(lambda:not self.accounts.busy)
        rows=[row for row in self.rows('configuration') if row['source']=='deepseek']
        failure=next(row for row in rows if row['outcome']=='error')
        self.assertEqual(failure['reason'],'configuration_failed')
        self.assertEqual(failure['error_type'],'RuntimeError')
        self.assertEqual(failure['request_ref'],next(row['request_ref'] for row in rows if row['outcome']=='accepted'))
        del self.controls.balance_error
        self.controls.balance_entered=threading.Event()
        self.controls.balance_release=threading.Event()
        self.accounts.connect_deepseek(DS_KEY)
        self.assertTrue(self.controls.balance_entered.wait(1))
        self.accounts.cancel()
        self.controls.balance_release.set()
        self.wait(lambda:not self.accounts.busy)
        cancelled=[row for row in self.rows('configuration') if row['outcome']=='cancelled']
        self.assertEqual(len(cancelled),1)
        self.assertEqual(cancelled[0]['reason'],'requested')
        self.assertNotEqual(cancelled[0]['request_ref'],failure['request_ref'])
        self.controls.balance_entered=threading.Event()
        self.controls.balance_release=threading.Event()
        self.accounts.connect_deepseek(DS_KEY)
        self.assertTrue(self.controls.balance_entered.wait(1))
        self.accounts.close()
        self.controls.balance_release.set()
        self.wait(lambda:not self.accounts.busy)
        closed=[row for row in self.rows('configuration')
                if row['outcome']=='cancelled' and row['reason']=='closed']
        self.assertEqual(len(closed),1)

    def test_configuration_thread_start_failure_exports_correlated_failure(self):
        with patch('hots_chat.account_setup.threading.Thread.start',side_effect=RuntimeError(DS_KEY)):
            with self.assertRaises(RuntimeError):self.accounts.connect_deepseek(DS_KEY)
        rows=[row for row in self.rows('configuration') if row['source']=='deepseek']
        self.assertEqual([row['outcome'] for row in rows if row['outcome'] in ('accepted','start','error')],
                         ['accepted','error'])
        self.assertEqual(rows[-1]['reason'],'thread_start_failed')
        self.assertEqual(len({row['request_ref'] for row in rows}),1)

    def test_balance_success_rejections_and_errors_export_without_amounts(self):
        self.assertFalse(self.accounts.refresh_balance())
        self.connect()
        self.block_balance()
        self.assertFalse(self.accounts.refresh_balance())
        self.controls.balance_release.set()
        self.wait(lambda:not self.accounts.balance_snapshot()['busy'])
        rows=self.rows('balance')
        self.assertEqual([row['reason'] for row in rows if row['outcome']=='rejected'],['unconfigured','busy'])
        result=next(row for row in rows if row['outcome']=='result')
        self.assertEqual([row['outcome'] for row in rows if row['request_ref']==result['request_ref']],
                         ['accepted','start','result'])
        for value in BALANCE['balance_infos'][0].values():
            if value!='CNY':self.assertNotIn(json.dumps(value),json.dumps(rows))
        self.controls.balance={'is_available':True,'balance_infos':[{'currency':'CNY','total_balance':DS_KEY}]}
        self.assertTrue(self.accounts.refresh_balance())
        self.wait(lambda:not self.accounts.balance_snapshot()['busy'])
        self.assertEqual(self.rows('balance')[-1]['reason'],'invalid_balance')
        self.controls.balance_error=DS_KEY
        self.assertTrue(self.accounts.refresh_balance())
        self.wait(lambda:not self.accounts.balance_snapshot()['busy'])
        self.assertEqual(self.rows('balance')[-1]['reason'],'query_failed')

    def test_provider_switch_invalidates_local_query_and_logs_late_result_as_stale(self):
        self.connect()
        self.block_balance()
        self.accounts.select_provider('aliyun')
        self.wait(lambda:not self.accounts.busy)
        self.controls.balance_release.set()
        self.wait(lambda:not self.accounts._balance_thread.is_alive())
        rows=self.rows('balance')
        self.assertEqual([row['outcome'] for row in rows],['accepted','start','cancelled','stale'])
        self.assertEqual(rows[-2]['reason'],'provider_changed')
        self.assertEqual(rows[-1]['reason'],'invalidated')
        self.assertEqual(len({row['request_ref'] for row in rows}),1)
        self.assertEqual(self.accounts.balance_snapshot()['balance_infos'],[])

    def test_close_and_thread_start_failure_have_correlated_terminal_records(self):
        self.connect()
        with patch('hots_chat.provider_accounts.threading.Thread.start',side_effect=RuntimeError(DS_KEY)):
            self.assertFalse(self.accounts.refresh_balance())
        failed=self.rows('balance')[-1]
        self.assertEqual((failed['outcome'],failed['reason']),('error','thread_start_failed'))
        self.block_balance()
        self.accounts.close()
        self.controls.balance_release.set()
        self.wait(lambda:not self.accounts._balance_thread.is_alive())
        self.assertFalse(self.accounts.refresh_balance())
        rows=self.rows('balance')
        self.assertEqual(rows[-1]['reason'],'closed')
        cancelled=next(row for row in rows if row['outcome']=='cancelled')
        self.assertEqual(cancelled['reason'],'closed')
        self.assertTrue(any(row['outcome']=='stale' and row['request_ref']==cancelled['request_ref']
                            and row['reason']=='closed' for row in rows))

    def test_failed_secret_registration_exports_only_safe_metadata_and_does_not_block_configuration(self):
        recorder=self.recorder
        class RegistrationFailure:
            def register_secrets(self,*values):raise RuntimeError(DS_KEY)
            def record(self,*args,**fields):return recorder.record(*args,**fields)
            def snapshot(self):return recorder.snapshot()
        self.controller.diagnostics=RegistrationFailure()
        self.controls.balance_error=DS_KEY
        self.accounts.connect_deepseek(DS_KEY)
        self.wait(lambda:not self.accounts.busy)
        self.assertEqual(self.accounts.state,'error')
        self.assertTrue(any(row['outcome']=='error' for row in self.rows('configuration')))
        del self.controls.balance_error
        self.connect()
        self.assertTrue(self.accounts.refresh_balance())
        self.wait(lambda:not self.accounts.balance_snapshot()['busy'])
        rows=self.rows('configuration')
        degraded=[row for row in rows if row['outcome']=='degraded']
        self.assertTrue(degraded)
        self.assertTrue(all(row['reason']=='secret_registration_failed' and 'key_id_last4' not in row
                            for row in degraded))
        class BrokenRecorder(RegistrationFailure):
            def record(self,*args,**fields):raise RuntimeError(DS_KEY)
        self.controller.diagnostics=BrokenRecorder()
        self.assertTrue(self.accounts.refresh_balance())
        self.wait(lambda:not self.accounts.balance_snapshot()['busy'])
        self.assertEqual(self.accounts.balance_snapshot()['balance_infos'],BALANCE['balance_infos'])
        self.accounts.select_provider('aliyun')
        self.wait(lambda:not self.accounts.busy)
        self.assertEqual(self.accounts.state,'ready')
        self.assertEqual(self.controller.engine.provider_id,'aliyun')


if __name__=='__main__':unittest.main()
