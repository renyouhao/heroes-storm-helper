from dataclasses import replace
from pathlib import Path
from datetime import datetime,timezone
import tempfile
import time
import unittest
from hots_chat.app import build_controller
from hots_chat.paths import AppPaths
from hots_chat.models import AppEvent,ChatEvent
from hots_chat.ui.viewmodel import AppViewModel


class EndToEndTests(unittest.TestCase):
    def test_translation_and_reviewed_phrases_offline_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);c=build_controller(AppPaths(p/'.env',p/'u.db',p/'l.db',p/'p.json'),True)
            try:
                now=datetime(2026,10,19,tzinfo=timezone.utc);c.clock=lambda:now
                c.ledger.confirm_baseline('main','2026-10',0,now)
                c.start_receiving();c.drain_events()
                deadline=time.monotonic()+3
                while not c.idle and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue(c.idle)
                self.assertEqual(len(c.context.recent()),3)
                phrase=c.phrases.list()[0];c.phrases.save(replace(phrase,reviewed=True))
                before=c.ledger.snapshot('main',now).local_reserved
                job=c.submit_manual(phrase.source_text,'ko')
                events=c.drain_events()
                self.assertTrue(any(e.kind=='manual_result' and e.payload['job_id']==job for e in events))
                self.assertEqual(c.ledger.snapshot('main',now).local_reserved,before)
                vm=AppViewModel()
                vm.apply(AppEvent('chat',0,ChatEvent('x',0,'团队','synthetic','1:00','ㄱㄱ <b>')))
                self.assertEqual(vm.messages['x'].original,'ㄱㄱ <b>')
                vm.apply(AppEvent('result',0,{'message_id':'x','engine_translation':'行动 <b>'}))
                self.assertEqual(vm.messages['x'].engine_translation,'行动 <b>')
                self.assertEqual(vm.messages['x'].original,'ㄱㄱ <b>')
                export=c.phrases.export_json()
                self.assertNotIn('ACCESS_KEY',export)
                c.handle_source_event(AppEvent('generation',1,'reset'))
                self.assertFalse(c.context.recent())
            finally:c.close()
            self.assertFalse(c.thread.is_alive())
