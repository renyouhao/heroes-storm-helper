import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hots_chat.controller import ChatController
from hots_chat.models import AppEvent, ChatEvent
from hots_chat.usage import BudgetLedger
from tests.test_controller import Engine, Source


class SpamControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = lambda: datetime(2026, 10, 20, tzinfo=timezone.utc)
        self.ledger = BudgetLedger(Path(self.tmp.name) / 'usage.db', 100000)
        self.ledger.confirm_baseline('main', '2026-10', 0, self.now())
        self.engine = Engine()
        self.controller = ChatController(Source(), self.engine, self.ledger, 'main', self.now)
        self.addCleanup(self.controller.close)

    def chat(self, identifier, text, sender='tester', channel='团队', generation=0):
        return ChatEvent(str(identifier), generation, channel, sender, '1:00', text)

    def wait_idle(self):
        deadline = time.monotonic() + 3
        while not self.controller.idle and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(self.controller.idle)

    def use_monotonic_clock(self):
        self.controller.close()
        self.tick = 0.0
        self.controller = ChatController(Source(), self.engine, self.ledger, 'main', self.now,
                                         monotonic=lambda: self.tick)
        self.addCleanup(self.controller.close)

    def test_fourth_distinct_sender_candidate_is_suppressed_but_other_sender_proceeds(self):
        self.engine.release.clear()
        self.controller.on_chat(self.chat(1, 'first'))
        self.assertTrue(self.engine.entered.wait(1))
        for identifier, text in [(2, 'second'), (3, 'third'), (4, 'fourth')]:
            self.controller.on_chat(self.chat(identifier, text))
        self.controller.on_chat(self.chat(5, 'normal', sender='teammate'))
        self.engine.release.set()
        self.wait_idle()
        events = self.controller.drain_events()
        self.assertEqual([job.text for job in self.engine.calls], ['first', 'second', 'third', 'normal'])
        self.assertEqual(len([event for event in events if event.kind == 'chat']), 5)
        self.assertEqual([(event.payload['message_id'], event.payload['reason'])
                          for event in events if event.kind == 'suppressed'], [('4', 'sender_rate')])

    def test_shared_and_cached_duplicates_preserve_originals_without_using_sender_slots(self):
        self.engine.release.clear()
        self.controller.on_chat(self.chat(1, 'first'))
        self.assertTrue(self.engine.entered.wait(1))
        for identifier in range(2, 9):
            self.controller.on_chat(self.chat(identifier, 'first'))
        self.controller.on_chat(self.chat(9, 'second'))
        self.controller.on_chat(self.chat(10, 'third'))
        self.controller.on_chat(self.chat(11, 'fourth'))
        self.engine.release.set()
        self.wait_idle()
        self.controller.on_chat(self.chat(12, 'first'))
        events = self.controller.drain_events()
        self.assertEqual([job.text for job in self.engine.calls], ['first', 'second', 'third'])
        self.assertEqual(len([event for event in events if event.kind == 'chat']), 12)
        self.assertEqual(len([event for event in events if event.kind == 'result']), 11)
        self.assertEqual(self.ledger.snapshot('main', self.now()).local_reserved, 16)

    def test_repeated_failed_cloud_candidates_use_sender_slots_without_harming_other_senders(self):
        from hots_chat.translation.base import TranslationError

        self.use_monotonic_clock()

        def fail(job):
            self.engine.calls.append(job)
            raise TranslationError('timeout', '网络超时')

        self.engine.translate = fail
        for identifier in range(1, 7):
            self.controller.on_chat(self.chat(identifier, 'hello'))
            self.wait_idle()
        self.assertEqual(len(self.engine.calls), 3)
        self.controller.on_chat(self.chat(7, 'normal', sender='teammate'))
        self.wait_idle()
        self.assertEqual([job.text for job in self.engine.calls], ['hello', 'hello', 'hello', 'normal'])
        self.assertEqual(self.ledger.snapshot('main', self.now()).local_reserved, 21)
        events = self.controller.drain_events()
        self.assertEqual(len([event for event in events if event.kind == 'chat']), 7)
        suppressed = [event.payload for event in events if event.kind == 'suppressed']
        self.assertEqual([(item['message_id'], item['reason']) for item in suppressed],
                         [('4', 'sender_rate'), ('5', 'sender_rate'), ('6', 'sender_rate')])
        self.tick = 10
        self.controller.on_chat(self.chat(8, 'hello'))
        self.wait_idle()
        self.assertEqual(len(self.engine.calls), 5)

    def test_global_character_budget_does_not_block_manual_or_shorter_remaining_candidate(self):
        self.engine.release.clear()
        self.controller.on_chat(self.chat(1, 'a' * 1500, sender='one'))
        self.assertTrue(self.engine.entered.wait(1))
        self.controller.on_chat(self.chat(2, 'b' * 1499, sender='two'))
        self.controller.on_chat(self.chat(3, 'too big', sender='three'))
        self.controller.on_chat(self.chat(4, 'c', sender='four'))
        self.controller.submit_manual('集合', 'ko')
        self.engine.release.set()
        self.wait_idle()
        self.assertEqual([job.text for job in self.engine.calls], ['a' * 1500, '集合', 'b' * 1499, 'c'])
        self.assertEqual(self.ledger.snapshot('main', self.now()).local_reserved, 3002)
        suppressed = [event.payload for event in self.controller.drain_events() if event.kind == 'suppressed']
        self.assertEqual([(item['message_id'], item['reason']) for item in suppressed], [('3', 'global_characters')])

    def test_automatic_waiting_queue_has_twenty_slots_and_manual_priority(self):
        self.engine.release.clear()
        self.controller.on_chat(self.chat('active', 'active', sender='active'))
        self.assertTrue(self.engine.entered.wait(1))
        for identifier in range(21):
            self.controller.on_chat(self.chat(identifier, 'hello ' + str(identifier), sender=str(identifier)))
        self.controller.submit_manual('撤退', 'en')
        self.engine.release.set()
        self.wait_idle()
        self.assertEqual(len(self.engine.calls), 22)
        self.assertEqual(self.engine.calls[1].text, '撤退')
        self.assertFalse(any(job.text == 'hello 20' for job in self.engine.calls))
        suppressed = [event.payload for event in self.controller.drain_events() if event.kind == 'suppressed']
        self.assertEqual([(item['message_id'], item['reason']) for item in suppressed], [('20', 'queue_full')])

    def test_explicit_retry_bypasses_sender_limit_and_receives_queue_priority(self):
        self.engine.release.clear()
        self.controller.on_chat(self.chat(1, 'first'))
        self.assertTrue(self.engine.entered.wait(1))
        for identifier, text in [(2, 'second'), (3, 'third'), (4, 'fourth')]:
            self.controller.on_chat(self.chat(identifier, text))
        retry_id = self.controller.retry_message('4')
        self.engine.release.set()
        self.wait_idle()
        self.assertEqual([job.text for job in self.engine.calls], ['first', 'fourth', 'second', 'third'])
        results = [event.payload for event in self.controller.drain_events() if event.kind == 'result']
        self.assertTrue(any(item['job_id'] == retry_id and item['message_id'] == '4' for item in results))

    def test_explicit_retry_bypasses_global_characters_but_obeys_total_pause_and_ledger(self):
        self.controller.on_chat(self.chat(1, 'a' * 3000, sender='one'))
        self.wait_idle()
        self.controller.on_chat(self.chat(2, 'fourth', sender='two'))
        self.wait_idle()
        self.assertEqual(len(self.engine.calls), 1)
        self.controller.pause_translation(True)
        self.controller.retry_message('2')
        self.assertEqual(len(self.engine.calls), 1)
        self.controller.pause_translation(False)
        self.ledger.limit = 3000
        self.controller.retry_message('2')
        self.wait_idle()
        self.assertEqual(len(self.engine.calls), 1)
        self.ledger.limit = 100000
        self.controller.retry_message('2')
        self.wait_idle()
        self.assertEqual([job.text for job in self.engine.calls], ['a' * 3000, 'fourth'])

    def test_expired_automatic_waiting_job_never_reserves_or_calls_cloud(self):
        self.use_monotonic_clock()
        self.engine.release.clear()
        self.controller.on_chat(self.chat(1, 'first'))
        self.assertTrue(self.engine.entered.wait(1))
        self.controller.on_chat(self.chat(2, 'expired', sender='old'))
        self.controller.submit_manual('集合', 'ko')
        self.tick = 8.0
        self.controller.on_chat(self.chat(3, 'fresh', sender='new'))
        self.engine.release.set()
        self.wait_idle()
        self.assertEqual([job.text for job in self.engine.calls], ['first', '集合', 'fresh'])
        self.assertEqual(self.ledger.snapshot('main', self.now()).local_reserved, 12)
        suppressed = [event.payload for event in self.controller.drain_events() if event.kind == 'suppressed']
        self.assertEqual([(item['message_id'], item['reason']) for item in suppressed], [('2', 'expired')])

    def test_shared_duplicate_does_not_renew_automatic_expiry(self):
        self.use_monotonic_clock()
        self.engine.release.clear()
        self.controller.on_chat(self.chat(1, 'first'))
        self.assertTrue(self.engine.entered.wait(1))
        self.controller.on_chat(self.chat(2, 'old'))
        self.tick = 7.9
        self.controller.on_chat(self.chat(3, 'old'))
        self.tick = 8.0
        self.engine.release.set()
        self.wait_idle()
        self.assertEqual([job.text for job in self.engine.calls], ['first'])
        suppressed = [event.payload['message_id'] for event in self.controller.drain_events()
                      if event.kind == 'suppressed']
        self.assertEqual(suppressed, ['2', '3'])

    def test_expired_waiting_content_does_not_spend_global_character_budget(self):
        self.use_monotonic_clock()
        self.engine.release.clear()
        self.controller.on_chat(self.chat(1, 'a', sender='active'))
        self.assertTrue(self.engine.entered.wait(1))
        self.controller.on_chat(self.chat(2, 'b' * 2999, sender='expired'))
        self.tick = 8
        self.controller.on_chat(self.chat(3, 'fresh normal', sender='normal'))
        self.engine.release.set()
        self.wait_idle()
        self.assertEqual([job.text for job in self.engine.calls], ['a', 'fresh normal'])
        self.assertEqual(self.ledger.snapshot('main', self.now()).local_reserved, 13)

    def test_explicit_retry_promotes_shared_waiting_request_and_bypasses_expiry(self):
        self.use_monotonic_clock()
        self.engine.release.clear()
        self.controller.on_chat(self.chat(1, 'first'))
        self.assertTrue(self.engine.entered.wait(1))
        self.controller.on_chat(self.chat(2, 'second'))
        retry_id = self.controller.retry_message('2')
        self.tick = 9
        self.engine.release.set()
        self.wait_idle()
        self.assertEqual([job.text for job in self.engine.calls], ['first', 'second'])
        self.assertTrue(any(event.kind == 'result' and event.payload['job_id'] == retry_id
                            for event in self.controller.drain_events()))

    def test_sender_window_is_per_channel_and_reopens_at_ten_seconds(self):
        self.use_monotonic_clock()
        for identifier, text in [(1, 'one'), (2, 'two'), (3, 'three')]:
            self.controller.on_chat(self.chat(identifier, text))
            self.wait_idle()
        self.controller.on_chat(self.chat(4, 'different channel', channel='所有玩家'))
        self.wait_idle()
        self.tick = 9.999
        self.controller.on_chat(self.chat(5, 'before boundary'))
        self.wait_idle()
        self.tick = 10
        self.controller.on_chat(self.chat(6, 'after boundary'))
        self.wait_idle()
        self.assertEqual([job.text for job in self.engine.calls],
                         ['one', 'two', 'three', 'different channel', 'after boundary'])

    def test_reconnect_preserves_sender_window_until_expiry_and_rejects_old_retry(self):
        self.use_monotonic_clock()
        for identifier, text in [(1, 'one'), (2, 'two'), (3, 'three')]:
            self.controller.on_chat(self.chat(identifier, text))
            self.wait_idle()
        self.controller.handle_source_event(AppEvent('generation', 1, 'next'))
        with self.assertRaises(ValueError):
            self.controller.retry_message('1')
        self.controller.on_chat(self.chat(4, 'four', generation=1))
        self.wait_idle()
        self.assertEqual([job.text for job in self.engine.calls], ['one', 'two', 'three'])
        self.controller.on_chat(self.chat(5, 'normal', sender='teammate', generation=1))
        self.wait_idle()
        self.tick = 9.999
        self.controller.on_chat(self.chat(6, 'before boundary', generation=1))
        self.wait_idle()
        self.assertEqual([job.text for job in self.engine.calls], ['one', 'two', 'three', 'normal'])
        self.tick = 10
        self.controller.on_chat(self.chat(7, 'after boundary', generation=1))
        self.wait_idle()
        self.assertEqual([job.text for job in self.engine.calls],
                         ['one', 'two', 'three', 'normal', 'after boundary'])
        suppressed = [event.payload for event in self.controller.drain_events() if event.kind == 'suppressed']
        self.assertEqual([(item['message_id'], item['reason']) for item in suppressed],
                         [('4', 'sender_rate'), ('6', 'sender_rate')])

    def test_reconnect_preserves_global_character_window_until_natural_expiry(self):
        self.use_monotonic_clock()
        self.controller.on_chat(self.chat(1, 'a' * 3000, sender='one'))
        self.wait_idle()
        self.controller.on_chat(self.chat(2, 'b', sender='two'))
        self.wait_idle()
        self.assertEqual(len(self.engine.calls), 1)
        self.controller.handle_source_event(AppEvent('generation', 1, 'reconnect'))
        self.controller.on_chat(self.chat(3, 'c' * 3000, sender='three', generation=1))
        self.wait_idle()
        self.assertEqual(len(self.engine.calls), 1)
        self.assertEqual(self.ledger.snapshot('main', self.now()).local_reserved, 3000)
        self.tick = 59.999
        self.controller.handle_source_event(AppEvent('generation', 2, 'relocate'))
        self.controller.on_chat(self.chat(4, 'before expiry', sender='four', generation=2))
        self.wait_idle()
        self.assertEqual(len(self.engine.calls), 1)
        self.tick = 60
        self.controller.handle_source_event(AppEvent('generation', 3, 'next game'))
        self.controller.on_chat(self.chat(5, 'd' * 3000, sender='five', generation=3))
        self.wait_idle()
        self.assertEqual([job.text for job in self.engine.calls], ['a' * 3000, 'd' * 3000])
        self.assertEqual(self.ledger.snapshot('main', self.now()).local_reserved, 6000)
        suppressed = [event.payload for event in self.controller.drain_events() if event.kind == 'suppressed']
        self.assertEqual([(item['message_id'], item['reason']) for item in suppressed],
                         [('2', 'global_characters'), ('3', 'global_characters'), ('4', 'global_characters')])

    def test_queue_rejection_does_not_consume_automatic_character_budget(self):
        self.controller.pending.automatic_capacity = 1
        self.engine.release.clear()
        self.controller.on_chat(self.chat(1, 'first'))
        self.assertTrue(self.engine.entered.wait(1))
        self.controller.on_chat(self.chat(2, 'b', sender='two'))
        self.controller.on_chat(self.chat(3, 'c' * 1000, sender='three'))
        self.engine.release.set()
        self.wait_idle()
        self.controller.on_chat(self.chat(4, 'd' * 2990, sender='four'))
        self.wait_idle()
        self.assertEqual([job.text for job in self.engine.calls], ['first', 'b', 'd' * 2990])

    def test_replace_engine_requires_idle_and_clears_cached_results(self):
        self.engine.release.clear()
        self.controller.on_chat(self.chat(1, 'first'))
        self.assertTrue(self.engine.entered.wait(1))
        replacement = Engine()
        with self.assertRaises(ValueError):
            self.controller.replace_engine(replacement, 'new-account')
        self.assertIs(self.controller.engine, self.engine)
        self.assertEqual(self.controller.account, 'main')
        self.engine.release.set()
        self.wait_idle()
        closed = []
        self.engine.close = lambda: closed.append(True)
        self.ledger.confirm_baseline('new-account', '2026-10', 0, self.now())
        self.controller.replace_engine(replacement, 'new-account')
        self.controller.on_chat(self.chat(2, 'first'))
        self.wait_idle()
        self.assertEqual([job.text for job in replacement.calls], ['first'])
        self.assertEqual(closed, [True])
        self.assertEqual(self.ledger.snapshot('main', self.now()).local_reserved, 5)
        self.assertEqual(self.ledger.snapshot('new-account', self.now()).local_reserved, 5)

    def test_closed_controller_rejects_late_engine_installation(self):
        self.controller.close()
        with self.assertRaises(ValueError):
            self.controller.replace_engine(Engine(), 'late-account')
        self.assertIs(self.controller.engine, self.engine)


if __name__ == '__main__':
    unittest.main()
