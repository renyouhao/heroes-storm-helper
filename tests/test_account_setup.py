import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from hots_chat.account_setup import AccountSetup, UnconfiguredEngine
from hots_chat.config import Credentials, ConfigError
from hots_chat.controller import ChatController
from hots_chat.models import TranslationResult
from hots_chat.usage import BudgetLedger


class MemoryStore:
    def __init__(self): self.value=None
    def load(self): return self.value
    def save(self, credentials, account):
        self.value=SimpleNamespace(credentials=credentials, account=account)
    def delete(self): self.value=None


class Source:
    def stop(self): pass


class Engine:
    provider_id='test';config_version='1'
    def __init__(self, credentials): self.calls=[];self.release=threading.Event();self.release.set()
    def translate(self, job):
        self.calls.append(job); self.release.wait(3)
        return TranslationResult(job.job_id,'Hello',None,None,0)
    def close(self): pass


class AccountSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ledger=BudgetLedger(Path(self.tmp.name)/'usage.db')
        self.controller=ChatController(Source(),UnconfiguredEngine(),self.ledger,'unconfigured')
        self.controller.pause_translation(True);self.addCleanup(self.controller.close)
        self.store=MemoryStore();self.engines=[]
        def factory(credentials):
            engine=Engine(credentials);self.engines.append(engine);return engine
        self.setup=AccountSetup(self.controller,self.store,SimpleNamespace(resolve=lambda c:'123456789'),factory)
        self.addCleanup(self.setup.close)
        self.credentials=Credentials('test_id','test_secret')

    def wait_for(self, expected):
        end=time.monotonic()+4
        events=[]
        while time.monotonic()<end:
            events.extend(self.setup.poll())
            if any(e['kind']==expected for e in events):return events
            time.sleep(.01)
        self.fail((expected,events))

    def test_no_cloud_request_until_identity_and_baseline_are_confirmed(self):
        self.setup.identify(self.credentials)
        self.wait_for('identified')
        self.assertEqual(self.engines,[])
        self.assertIsNone(self.store.value)
        self.setup.test_and_save(0)
        self.wait_for('ready')
        self.assertEqual(self.controller.account,'123456789')
        self.assertEqual(len(self.engines[0].calls),1)
        self.assertEqual(self.ledger.snapshot('123456789',self.controller.clock()).local_reserved,2)
        self.assertEqual(self.store.value.account,'123456789')

    def test_unknown_baseline_prevents_test_network_and_save(self):
        self.setup.identify(self.credentials);self.wait_for('identified')
        with self.assertRaises(ValueError):self.setup.test_and_save(None)
        self.assertEqual(self.engines,[])
        self.assertIsNone(self.store.value)

    def test_late_identity_after_cancel_does_not_install_credentials(self):
        entered=threading.Event();release=threading.Event()
        def resolve(credentials): entered.set();release.wait(3);return '123456789'
        self.setup.identity=SimpleNamespace(resolve=resolve)
        self.setup.identify(self.credentials);self.assertTrue(entered.wait(2))
        self.setup.cancel();release.set()
        self.wait_for('cancelled')
        self.assertIsNone(self.store.value)
        self.assertEqual(self.controller.account,'unconfigured')

    def test_failed_test_is_accounted_and_never_saves_key(self):
        self.setup.identify(self.credentials);self.wait_for('identified')
        def fail(job):raise RuntimeError('provider text must stay private')
        def factory(credentials):
            engine=Engine(credentials);engine.translate=fail;return engine
        self.setup.engine_factory=factory
        self.setup.test_and_save(0)
        events=self.wait_for('error')
        self.assertEqual(self.ledger.snapshot('123456789',self.controller.clock()).local_reserved,2)
        self.assertIsNone(self.store.value)
        self.assertNotIn('provider text',str(events))

    def test_remove_key_disables_cloud_but_preserves_usage(self):
        self.setup.identify(self.credentials);self.wait_for('identified')
        self.setup.test_and_save(0);self.wait_for('ready')
        self.setup.delete();self.wait_for('removed')
        self.assertIsNone(self.store.value)
        self.assertTrue(self.controller.paused)
        self.assertEqual(self.ledger.snapshot('123456789',self.controller.clock()).local_reserved,2)

    def test_cancel_during_network_keeps_reservation_and_discards_late_success(self):
        self.setup.identify(self.credentials);self.wait_for('identified')
        engine=Engine(self.credentials);engine.release.clear()
        self.setup.engine_factory=lambda c:engine
        self.setup.test_and_save(0)
        deadline=time.monotonic()+2
        while not engine.calls and time.monotonic()<deadline:time.sleep(.01)
        self.assertTrue(engine.calls)
        self.setup.cancel();engine.release.set();self.wait_for('cancelled')
        self.assertIsNone(self.store.value)
        self.assertEqual(self.controller.account,'unconfigured')
        self.assertEqual(self.ledger.snapshot('123456789',self.controller.clock()).local_reserved,2)

    def test_closing_successful_dialog_does_not_disable_new_configuration(self):
        self.setup.identify(self.credentials);self.wait_for('identified')
        self.setup.test_and_save(0);self.wait_for('ready')
        self.controller.pause_translation(False)
        self.setup.cancel()
        self.assertFalse(self.controller.paused)

    def test_rotating_same_account_key_keeps_prior_reserved_usage(self):
        self.setup.identify(self.credentials);self.wait_for('identified')
        self.setup.test_and_save(0);self.wait_for('ready')
        self.setup.identify(Credentials('other_id','other_secret'));self.wait_for('identified')
        self.setup.test_and_save(0);self.wait_for('ready')
        self.assertEqual(self.ledger.snapshot('123456789',self.controller.clock()).local_reserved,4)

    def test_reidentifying_then_cancel_restores_original_cloud_state(self):
        self.controller.pause_translation(False)
        self.setup.identify(self.credentials);self.wait_for('identified')
        self.setup.identify(self.credentials);self.wait_for('identified')
        self.setup.cancel()
        self.assertFalse(self.controller.paused)

    def test_usage_confirmation_cannot_reenable_old_engine_during_configuration(self):
        self.setup.identify(self.credentials);self.wait_for('identified')
        self.controller.pause_translation(False)
        self.assertTrue(self.controller.paused)
        self.setup.cancel()
        self.assertFalse(self.controller.configuration_held)


class StartupKeyTests(unittest.TestCase):
    def test_missing_key_starts_without_project_env_fallback(self):
        from hots_chat.app import build_controller
        from hots_chat.paths import AppPaths
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            # Presence of an old developer .env must not silently enable cloud.
            (p/'.env').write_text('this must not be loaded',encoding='utf-8')
            c=build_controller(AppPaths(p/'.env',p/'u.db',p/'l.db',p/'p.json'))
            try:
                self.assertFalse(c.cloud_configured)
                self.assertTrue(c.paused)
                self.assertEqual(c.account,'unconfigured')
                self.assertTrue(c.idle)
            finally:c.close()
