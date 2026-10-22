import tempfile
import threading
import time
import unittest
from pathlib import Path
from datetime import datetime,timezone
from hots_chat.controller import ChatController
from hots_chat.models import ChatEvent,TranslationResult,AppEvent
from hots_chat.usage import BudgetLedger


class Source:
    def start(self,emit): self.emit=emit
    def stop(self): pass
    def reconnect(self): pass


class Engine:
    provider_id='fake'; config_version='1'
    def __init__(self):
        self.calls=[]; self.entered=threading.Event(); self.release=threading.Event(); self.release.set()
    def translate(self,j):
        self.calls.append(j); self.entered.set(); self.release.wait(2)
        return TranslationResult(j.job_id,'译:'+j.text,'en',None,1)
    def close(self): pass


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.now=lambda:datetime(2026,10,19,tzinfo=timezone.utc)
        self.ledger=BudgetLedger(Path(self.tmp.name)/'u.db',100)
        self.engine=Engine(); self.source=Source()
        self.c=ChatController(self.source,self.engine,self.ledger,'main',self.now)
        self.addCleanup(self.c.close)
    def chat(self,i,text='hello',generation=0):
        return ChatEvent(str(i),generation,'团队','tester','1:00',text)
    def confirm(self): self.ledger.confirm_baseline('main','2026-10',0,self.now())
    def test_stale_generation_cannot_clear_current_context(self):
        self.c.pause_translation(True)
        self.c.handle_source_event(AppEvent('generation',3,'current'))
        self.c.on_chat(self.chat('current',generation=3))
        self.c.handle_source_event(AppEvent('generation',2,'late'))
        self.assertEqual(self.c.generation,3)
        self.assertIn('current',self.c.messages)
        self.assertEqual(len(self.c.context.recent()),1)

    def test_start_after_multiple_stops_advances_source_generation_floor(self):
        self.source.generation=1;self.source.running=False
        self.c.generation=5
        self.c.start_receiving()
        self.assertGreaterEqual(self.source.generation,5)
    def wait(self,predicate):
        deadline=time.monotonic()+3
        while not predicate() and time.monotonic()<deadline: time.sleep(.01)
        self.assertTrue(predicate())
    def test_duplicate_text_preserves_two_messages_but_one_request(self):
        self.confirm(); self.engine.release.clear()
        self.c.on_chat(self.chat(1)); self.assertTrue(self.engine.entered.wait(2))
        self.c.on_chat(self.chat(2)); self.engine.release.set()
        self.wait(lambda:self.c.idle)
        events=self.c.drain_events()
        self.assertEqual(len([e for e in events if e.kind=='chat']),2)
        self.assertEqual(len([e for e in events if e.kind=='result']),2)
        self.assertEqual(len(self.engine.calls),1)
    def test_budget_failure_makes_zero_network_calls(self):
        self.c.on_chat(self.chat(1)); self.wait(lambda:self.c.idle)
        self.assertFalse(self.engine.calls)
        self.assertTrue(any(e.kind=='error' for e in self.c.drain_events()))
    def test_new_generation_drops_stale_result(self):
        self.confirm(); self.engine.release.clear()
        self.c.on_chat(self.chat(1)); self.assertTrue(self.engine.entered.wait(2))
        self.source.start(lambda e:None)
        self.c.handle_source_event(AppEvent('generation',1,'reconnect'))
        self.engine.release.set(); self.wait(lambda:self.c.idle)
        self.assertFalse(any(e.kind=='result' for e in self.c.drain_events()))
    def test_manual_preempts_waiting_auto_but_not_inflight(self):
        self.confirm(); self.engine.release.clear()
        self.c.on_chat(self.chat(1,'first')); self.assertTrue(self.engine.entered.wait(2))
        self.c.on_chat(self.chat(2,'second'))
        self.c.submit_manual('集合','ko'); self.engine.release.set()
        self.wait(lambda:self.c.idle)
        self.assertEqual([j.text for j in self.engine.calls],['first','集合','second'])
    def test_pause_still_receives_originals(self):
        self.c.pause_translation(True); self.c.on_chat(self.chat(1))
        self.assertFalse(self.engine.calls)
        self.assertTrue(any(e.kind=='chat' for e in self.c.drain_events()))
    def test_automatic_pause_receives_originals_and_keeps_manual_translation_available(self):
        self.confirm()
        self.assertFalse(getattr(self.c,'automatic_paused',False))
        self.c.pause_automatic(True)
        self.c.on_chat(self.chat(1))
        job_id=self.c.submit_manual('集合','ko')
        self.wait(lambda:self.c.idle)
        events=self.c.drain_events()
        self.assertTrue(self.c.automatic_paused)
        self.assertFalse(self.c.paused)
        self.assertEqual([j.text for j in self.engine.calls],['集合'])
        self.assertTrue(any(e.kind=='chat' for e in events))
        self.assertTrue(any(e.kind=='manual_result' and e.payload['job_id']==job_id for e in events))
        self.assertEqual(self.ledger.snapshot('main',self.now()).local_reserved,2)
    def test_automatic_pause_drops_only_queued_auto_and_settles_inflight_request(self):
        self.confirm();self.engine.release.clear()
        self.c.on_chat(self.chat(1,'first'));self.assertTrue(self.engine.entered.wait(2))
        self.c.on_chat(self.chat(2,'second'));self.c.submit_manual('集合','ko')
        self.c.pause_automatic(True)
        self.engine.release.set();self.wait(lambda:self.c.idle)
        events=self.c.drain_events()
        self.assertEqual([j.text for j in self.engine.calls],['first','集合'])
        self.assertTrue(any(e.kind=='result' and e.payload['message_id']=='1' for e in events))
        self.assertFalse(any(e.kind=='result' and e.payload['message_id']=='2' for e in events))
        self.assertEqual(self.ledger.snapshot('main',self.now()).local_reserved,7)
    def test_automatic_pause_preserves_manual_subscriber_sharing_a_queued_key(self):
        self.confirm();self.engine.release.clear()
        self.c.on_chat(self.chat(1,'first'));self.assertTrue(self.engine.entered.wait(2))
        self.c.on_chat(self.chat(2,'shared'))
        # The scheduler coalesces by translation arguments, independently of kind.
        with self.c.condition:manual_id=self.c._submit('shared','en','zh','manual',None)
        self.c.pause_automatic(True)
        self.engine.release.set();self.wait(lambda:self.c.idle)
        events=self.c.drain_events()
        self.assertEqual([j.text for j in self.engine.calls],['first','shared'])
        self.assertEqual(self.engine.calls[-1].kind,'manual')
        self.assertTrue(any(e.kind=='manual_result' and e.payload['job_id']==manual_id for e in events))
        self.assertFalse(any(e.kind=='result' and e.payload['message_id']=='2' for e in events))
    def test_automatic_pause_rejects_auto_retry_and_resume_does_not_replay_chat(self):
        self.confirm();self.c.pause_automatic(True)
        self.c.on_chat(self.chat(1,'before resume'));self.c.retry_message('1')
        self.assertFalse(self.engine.calls)
        self.c.pause_automatic(False)
        self.c.on_chat(self.chat(2,'after resume'));self.wait(lambda:self.c.idle)
        self.assertEqual([j.text for j in self.engine.calls],['after resume'])
    def test_automatic_resume_does_not_override_global_cloud_pause(self):
        self.confirm();self.c.pause_translation(True)
        self.c.pause_automatic(True);self.c.pause_automatic(False)
        self.c.submit_manual('集合','ko');self.c.on_chat(self.chat(1))
        self.assertTrue(self.c.paused)
        self.assertFalse(self.engine.calls)
    def test_manual_during_automatic_pause_still_requires_budget_baseline(self):
        self.c.pause_automatic(True);self.c.submit_manual('集合','ko')
        self.wait(lambda:self.c.idle)
        self.assertFalse(self.engine.calls)
        self.assertTrue(any(e.kind=='error' for e in self.c.drain_events()))
    def test_channel_selection_applies_to_new_messages(self):
        self.c.set_channels({'团队'})
        self.c.on_chat(ChatEvent('1',0,'所有玩家','t','1:00','hello'))
        self.assertFalse(any(e.kind=='chat' for e in self.c.drain_events()))

    def test_party_whisper_default_receives_and_translates_both_channels(self):
        self.confirm()
        for identifier,channel,text in (('party','队伍','group up'),('whisper','密语','come here')):
            self.c.on_chat(ChatEvent(identifier,0,channel,'tester','1:00',text))
        self.wait(lambda:self.c.idle)
        events=self.c.drain_events()
        self.assertEqual({e.payload.channel for e in events if e.kind=='chat'},{'队伍','密语'})
        self.assertEqual({e.payload['message_id'] for e in events if e.kind=='result'},{'party','whisper'})
        self.assertEqual({job.text for job in self.engine.calls},{'group up','come here'})

    def test_party_whisper_selection_excludes_other_channels_without_clearing_history(self):
        self.c.pause_translation(True)
        self.c.set_channels({'密语'})
        self.c.on_chat(ChatEvent('party',0,'队伍','tester','1:00','group up'))
        self.c.on_chat(ChatEvent('private',0,'密语','tester','1:00','hello'))
        self.assertEqual(set(self.c.messages),{'private'})
        self.c.set_channels({'队伍'})
        self.c.on_chat(ChatEvent('party2',0,'队伍','tester','1:01','come here'))
        self.c.on_chat(ChatEvent('private2',0,'密语','tester','1:01','hi'))
        self.assertEqual(set(self.c.messages),{'private','party2'})

    def test_party_whisper_invalid_channel_does_not_change_current_selection(self):
        self.c.set_channels({'队伍','密语'})
        for channels in (set(),{'公会'},{'队伍','未知'}):
            with self.assertRaises(ValueError):self.c.set_channels(channels)
            self.assertEqual(self.c.allowed_channels,{'队伍','密语'})
    def test_same_text_after_generation_cannot_reuse_old_flight(self):
        self.confirm();self.engine.release.clear()
        self.c.on_chat(self.chat(1));self.assertTrue(self.engine.entered.wait(2))
        self.c.handle_source_event(AppEvent('generation',1,'reset'))
        self.c.on_chat(self.chat(2,generation=1))
        self.engine.release.set();self.wait(lambda:self.c.idle)
        results=[e for e in self.c.drain_events() if e.kind=='result']
        self.assertEqual(len(results),1)
        self.assertEqual(results[0].payload['message_id'],'2')
        self.assertEqual(len(self.engine.calls),2,'新连接代必须独立订阅，不能借用旧在途结果')
    def test_local_resolution_no_usage(self):
        self.c.set_local_resolver(lambda text,target:'local')
        self.c.submit_manual('集合','ko')
        self.assertFalse(self.engine.calls)
        self.assertEqual(self.ledger.snapshot('main',self.now()).local_reserved,0)
    def test_failed_request_keeps_usage_and_can_retry(self):
        from hots_chat.translation.base import TranslationError
        self.confirm()
        def fail(job):self.engine.calls.append(job);raise TranslationError('timeout','网络超时')
        self.engine.translate=fail
        self.c.on_chat(self.chat(1));self.wait(lambda:self.c.idle)
        self.c.retry_message('1');self.wait(lambda:self.c.idle)
        self.assertEqual(len(self.engine.calls),2)
        self.assertEqual(self.ledger.snapshot('main',self.now()).local_reserved,10)
    def test_full_queue_manual_displaces_waiting_auto(self):
        self.confirm();self.engine.release.clear();self.c.pending.capacity=1
        self.c.on_chat(self.chat(1,'first'));self.assertTrue(self.engine.entered.wait(2))
        self.c.on_chat(self.chat(2,'second'));self.c.submit_manual('撤退','ko')
        self.engine.release.set();self.wait(lambda:self.c.idle)
        self.assertEqual([j.text for j in self.engine.calls],['first','撤退'])
        self.assertTrue(any(e.kind=='suppressed' and e.payload.get('message_id')=='2'
                            and e.payload.get('reason')=='queue_full' for e in self.c.drain_events()))
    def test_settlement_failure_stops_waiting_network(self):
        from hots_chat.usage import BudgetError
        self.confirm();self.engine.release.clear()
        original=self.ledger.settle
        attempts=[]
        def settle(*args):
            attempts.append(1)
            if len(attempts)==1:raise BudgetError('simulated disk failure')
            return original(*args)
        self.ledger.settle=settle
        self.c.on_chat(self.chat(1,'first'));self.assertTrue(self.engine.entered.wait(2))
        self.c.on_chat(self.chat(2,'second'));self.engine.release.set()
        self.wait(lambda:self.c.idle)
        self.assertTrue(self.c.paused)
        self.assertEqual([j.text for j in self.engine.calls],['first'])
    def test_queue_and_event_overflow_visible(self):
        self.c.pause_translation(True)
        for i in range(1100): self.c.on_chat(self.chat(i))
        self.assertTrue(any(e.kind=='gap' for e in self.c.drain_events()))
