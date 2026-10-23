"""面板worker与hook隔离，所有输入和游戏读取均为替身。"""
from dataclasses import replace
import importlib
import gc
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

from hots_chat.loot_native import GameTarget
from hots_chat.talent_broadcast import StatHitSnapshot


def until(predicate, timeout=2.):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():return True
        time.sleep(.005)
    return False


class BoardServiceTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('hots_chat.board_service')
        self.reads, self.sent = [], []
        self.target = GameTarget(22, 11)
        self.hooks = threading.Event()
        outer = self
        class Backend:
            def foreground_target(self):return outer.target
            def double_click_settings(self):return 500, (4, 4)
            def input_idle(self):return True
            def run(self, key, mouse, tick, ready, stopped):
                self.key, self.mouse = key, mouse
                ready();outer.hooks.set();stopped.wait(3)
            def stop(self):pass
        self.backend = Backend()
        class Reader:
            def poll(self, pid, hwnd, now):
                outer.reads.append(threading.current_thread().name)
                hit = StatHitSnapshot(pid=pid,hwnd=hwnd,match_token='match',player_slot=2,
                    team_relation='ally',metric_id='hero_damage',value=12345,rect=(100,100,150,150),
                    observed_at=now,valid_until=now+1,panel_visible=True,mapping_trusted=True,
                    player_name='玩家',hero_name='安度因')
                return SimpleNamespace(pid=pid,hwnd=hwnd,match_token='match',status='available',
                                       hits=(hit,),chat_active=False,menu_active=False)
            def close(self):pass
        self.reader = Reader()
        def sender(action, *, allowed):
            if allowed():outer.sent.append(action);return 'sent'
            return 'failed'
        self.helper = self.module.BoardHelper(backend_factory=lambda:self.backend,
            reader_factory=lambda backend:self.reader, sender_factory=lambda backend, reader:sender)
        self.addCleanup(self.helper.close)

    def enable(self):
        self.assertTrue(self.helper.set_enabled(True))
        self.assertTrue(self.hooks.wait(2))

    def click(self):
        self.backend.mouse(120,120,True,'left',False)
        self.backend.mouse(120,120,False,'left',False)

    def test_disabled_and_enabled_idle_do_not_read_game(self):
        self.assertIsNone(self.helper.thread)
        self.enable()
        time.sleep(.1)
        self.assertEqual(self.reads, [])
        self.assertEqual(self.sent, [])

    def test_double_click_reads_off_hook_and_sends_only_clicked_metric(self):
        self.enable();self.click()
        self.assertTrue(until(lambda:len(self.reads)>=1))
        self.click()
        self.assertTrue(until(lambda:len(self.sent)==1))
        self.assertEqual(self.sent[0].message, '【我方】玩家（安度因）｜英雄伤害：12,345')
        self.assertTrue(all(name=='board-worker' for name in self.reads))

    def test_injected_clicks_and_two_overlapping_candidates_never_send(self):
        self.enable()
        self.backend.mouse(120,120,True,'left',True)
        self.assertEqual(self.reads, [])
        original = self.reader.poll
        def overlapping(pid,hwnd,now):
            snapshot = original(pid,hwnd,now)
            snapshot.hits += (replace(snapshot.hits[0],player_slot=6,team_relation='enemy'),)
            return snapshot
        self.reader.poll=overlapping
        self.click();self.click()
        self.assertTrue(until(lambda:len(self.reads)>=2))
        self.assertEqual(self.sent, [])

    def test_other_user_key_cancels_between_clicks(self):
        self.enable();self.click()
        self.assertTrue(until(lambda:len(self.reads)>=1))
        self.assertFalse(self.backend.key(0x53,True,0))
        self.click()
        self.assertTrue(until(lambda:len(self.reads)>=2))
        self.assertEqual(self.sent, [])

    def test_disabling_during_slow_read_prevents_send(self):
        self.enable()
        started, finish = threading.Event(), threading.Event()
        original = self.reader.poll
        def slow(pid,hwnd,now):
            started.set();finish.wait(2);return original(pid,hwnd,now)
        self.reader.poll=slow
        self.click();self.assertTrue(started.wait(2))
        self.helper.set_enabled(False,wait=False)
        finish.set();self.helper.thread.join(2)
        self.assertEqual(self.sent, [])
        self.assertFalse(self.helper.busy)

    def test_key_queued_during_second_down_read_invalidates_action_before_cancel_is_drained(self):
        self.enable();self.click()
        self.assertTrue(until(lambda:len(self.reads)>=1))
        started, finish = threading.Event(), threading.Event()
        original = self.reader.poll

        def slow(pid,hwnd,now):
            started.set();finish.wait(2)
            return original(pid,hwnd,now)

        self.reader.poll = slow
        self.addCleanup(finish.set)
        self.backend.mouse(120,120,True,'left',False)
        self.assertTrue(started.wait(2))
        self.backend.mouse(120,120,False,'left',False)
        self.backend.key(0x53,True,0);self.backend.key(0x53,False,0)
        finish.set()
        self.assertTrue(until(lambda:self.helper._inputs.empty()))
        time.sleep(.03)
        self.assertEqual(self.sent, [])

    def test_two_clicks_already_queued_preserve_first_click_history(self):
        self.enable()
        self.click();self.click()
        self.assertTrue(until(lambda:len(self.sent)==1))

    def test_reenable_preserves_rate_limits_and_refreshes_double_click_settings(self):
        now = [100.]
        self.helper.clock = lambda:now[0]
        self.enable();self.click()
        self.assertTrue(until(lambda:len(self.reads)>=1))
        now[0]=100.2;self.click()
        self.assertTrue(until(lambda:len(self.sent)==1))
        self.helper.set_enabled(False);self.helper.thread.join(2)
        self.hooks.clear()
        self.backend.double_click_settings = lambda:(100,(8,8))
        self.enable()
        now[0]=100.3;self.click();self.click()
        self.assertTrue(until(lambda:self.helper._inputs.empty()))
        time.sleep(.02)
        self.assertEqual(len(self.sent),1)
        # Beyond both rate limits, the refreshed 100ms setting must reject 200ms.
        now[0]=103.5;self.click()
        self.assertTrue(until(lambda:self.helper._inputs.empty()))
        now[0]=103.7;self.click()
        self.assertTrue(until(lambda:self.helper._inputs.empty()))
        time.sleep(.02)
        self.assertEqual(len(self.sent),1)
        now[0]=104.;self.click()
        self.assertTrue(until(lambda:self.helper._inputs.empty()))
        now[0]=104.05;self.click()
        self.assertTrue(until(lambda:len(self.sent)==2))

    def test_right_click_middle_click_and_wheel_cancel_without_reading(self):
        self.enable()
        for button in ('right','middle','other'):
            self.backend.mouse(120,120,True,button,False)
            self.backend.mouse(120,120,False,button,False)
        self.assertTrue(until(lambda:self.helper._inputs.empty()))
        time.sleep(.03)
        self.assertEqual(self.reads,[])
        self.assertEqual(self.sent,[])

    def test_overflow_during_slow_second_read_invalidates_original_revision(self):
        self.enable();self.click()
        self.assertTrue(until(lambda:len(self.reads)>=1))
        started, finish = threading.Event(), threading.Event()
        original = self.reader.poll

        def slow(pid,hwnd,now):
            started.set();finish.wait(2)
            return original(pid,hwnd,now)

        self.reader.poll=slow
        self.addCleanup(finish.set)
        self.backend.mouse(120,120,True,'left',False)
        self.assertTrue(started.wait(2))
        # Releases alone do not change the revision; overflowing their bounded
        # queue must invalidate the pending action before the cancel is read.
        for _ in range(70):self.backend.mouse(120,120,False,'left',False)
        self.assertLessEqual(self.helper._inputs.qsize(),64)
        finish.set()
        self.assertTrue(until(lambda:self.helper._inputs.empty()))
        time.sleep(.03)
        self.assertEqual(self.sent,[])

    def test_suspension_during_slow_second_read_cannot_be_undone_before_cancel_drains(self):
        self.enable();self.click()
        self.assertTrue(until(lambda:len(self.reads)>=1))
        started, finish = threading.Event(), threading.Event()
        original = self.reader.poll

        def slow(pid,hwnd,now):
            started.set();finish.wait(2)
            return original(pid,hwnd,now)

        self.reader.poll=slow
        self.addCleanup(finish.set)
        self.backend.mouse(120,120,True,'left',False)
        self.assertTrue(started.wait(2))
        self.backend.mouse(120,120,False,'left',False)
        self.helper.set_suspended(True);self.helper.set_suspended(False)
        finish.set()
        self.assertTrue(until(lambda:self.helper._inputs.empty()))
        time.sleep(.03)
        self.assertEqual(self.sent,[])

    def test_reader_close_error_still_disables_worker_and_allows_real_restart(self):
        close_calls = []
        thread_errors = []

        def close_reader():
            close_calls.append('closed')
            if len(close_calls)==1:raise OSError('inert close failure')

        self.reader.close = close_reader
        self.enable()
        previous = self.helper.thread
        with patch('threading.excepthook',lambda args:thread_errors.append(args.exc_value)):
            # Model the native hook loop ending without a user disable call.
            self.helper._stop.set()
            previous.join(2)
        self.assertFalse(previous.is_alive())
        self.assertFalse(self.helper._enabled)
        self.assertFalse(self.helper.busy)
        self.assertEqual(thread_errors,[])
        self.hooks.clear()
        self.assertTrue(self.helper.set_enabled(True))
        self.assertIsNot(self.helper.thread,previous)
        self.assertTrue(self.hooks.wait(2))

    def test_backend_stop_error_does_not_skip_disabled_status_reader_close_or_closed_flag(self):
        close_calls, thread_errors = [], []
        self.reader.close = lambda:close_calls.append('closed')

        def stop_backend():raise OSError('inert native stop failure')

        self.backend.stop=stop_backend
        self.enable()
        with patch('threading.excepthook',lambda args:thread_errors.append(args.exc_value)):
            self.assertTrue(self.helper.set_enabled(False,wait=False))
            self.assertTrue(self.helper._stop.is_set())
            self.assertFalse(self.helper._enabled)
            self.assertEqual(self.helper.drain_status()[-1].state,'disabled')
            self.helper.thread.join(2)
        self.assertEqual(close_calls,['closed'])
        self.assertFalse(self.helper.busy)
        self.assertEqual(thread_errors,[])
        self.helper.close()
        self.assertTrue(self.helper._closed)

    def test_close_marks_closed_even_if_disable_has_an_unexpected_failure(self):
        self.enable()
        original = self.helper.set_enabled

        def broken_disable(value,**kwargs):
            original(value,**kwargs)
            raise RuntimeError('inert lifecycle failure')

        with patch.object(self.helper,'set_enabled',broken_disable):
            with self.assertRaises(RuntimeError):self.helper.close()
        self.assertTrue(self.helper._closed)

    def test_close_detaches_ui_owner_before_worker_can_collect_its_cycle(self):
        gc.collect()
        enabled = gc.isenabled()
        gc.disable()
        finalized = []

        class Owner:
            def busy(self):return False

        try:
            owner = Owner()
            owner.helper = self.module.BoardHelper(busy_provider=owner.busy)
            watched = weakref.ref(owner)
            weakref.finalize(owner,lambda:finalized.append(threading.get_ident()))
            owner.helper.close()
            self.assertTrue(owner.helper.busy_provider())
            del owner
            worker = threading.Thread(target=gc.collect,name='inert-gc-worker')
            worker.start();worker.join(2)
            self.assertIsNone(watched())
            self.assertEqual(finalized,[threading.main_thread().ident])
        finally:
            if enabled:gc.enable()

    def test_optional_diagnostics_only_records_state_changes_without_message_content(self):
        events = []

        class Recorder:
            def record(self,event,**fields):events.append((event,fields))

        helper = self.module.BoardHelper(diagnostics=Recorder())
        self.addCleanup(helper.close)
        helper._emit('sent','PRIVATE PLAYER: PRIVATE MESSAGE')
        helper._emit('sent','ANOTHER PRIVATE MESSAGE')
        self.assertEqual([fields['outcome'] for event,fields in events],['disabled','sent'])
        for event,fields in events:
            self.assertEqual(event,'hotkey.state')
            self.assertEqual(set(fields),{'stage','outcome','generation'})
            self.assertEqual(fields['stage'],'broadcast')
            self.assertNotIn('PRIVATE',str(fields))


if __name__ == '__main__':unittest.main()
