"""后台调度测试使用受控 reader/backend，不碰真实进程或键盘。"""
import threading
import unittest
from types import SimpleNamespace

from hots_chat.loot_native import GameTarget
from hots_chat.talent_service import TalentHelper


class Session:
    def __init__(self, backend):
        self.backend = backend
        self.enabled = False
        self.attempts = 0
        self.polled = threading.Event()
    def configure(self, config):self.enabled = config.enabled
    def stop(self):self.enabled = False
    def poll(self, snapshot, now, target, *, allowed, validate=None):
        if allowed() and (validate is None or validate()):self.attempts += 1
        self.polled.set()
        return SimpleNamespace(state='ready', detail='fake', tier_id=None)


class TalentHelperTests(unittest.TestCase):
    def make(self, *, blocked=False):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        calls = []
        backend = SimpleNamespace(foreground_target=lambda: GameTarget(10, 42))
        class Reader:
            def poll(self, pid):
                calls.append(('read', pid)); entered.set()
                if blocked:release.wait(2)
                return SimpleNamespace(pid=pid,hero_id='Chromie',status='available',generation=('game',1),
                                       pending_tier=1,selected=((1,-1),))
            def close(self):calls.append(('close',)); finished.set()
        def reader():calls.append(('reader_factory',)); return Reader()
        def native():calls.append(('backend_factory',)); return backend
        helper = TalentHelper(reader_factory=reader, backend_factory=native, session_factory=Session)
        self.addCleanup(helper.close)
        self.addCleanup(release.set)
        return helper, calls, entered, release, finished

    def test_disabled_constructs_nothing_and_drains_only_latest_status(self):
        helper, calls, *_ = self.make()
        self.assertIsNone(helper.thread)
        self.assertEqual(calls, [])
        self.assertEqual(helper.drain_status()[-1].state, 'disabled')

    def test_disable_during_read_fences_dispatch_then_closes_reader(self):
        helper, calls, entered, release, finished = self.make(blocked=True)
        self.assertTrue(helper.set_enabled(True))
        self.assertTrue(entered.wait(2))
        helper.set_enabled(False, wait=False)
        release.set()
        self.assertTrue(finished.wait(2))
        self.assertEqual(helper._session.attempts, 0)
        self.assertEqual(helper.drain_status()[-1].state, 'disabled')
        self.assertIn(('read', 42), calls)

    def test_suspended_loop_never_opens_sending_gate(self):
        helper, _, entered, release, finished = self.make(blocked=True)
        helper.set_suspended(True)
        helper.set_enabled(True)
        self.assertTrue(entered.wait(2))
        release.set()
        helper.set_enabled(False)
        self.assertTrue(finished.wait(2))
        self.assertEqual(helper._session.attempts, 0)

    def test_unchanged_trusted_snapshot_can_dispatch(self):
        helper, _, entered, release, _ = self.make(blocked=True)
        helper.set_enabled(True)
        self.assertTrue(entered.wait(2))
        release.set()
        self.assertTrue(helper._session.polled.wait(2))
        helper.set_enabled(False)
        self.assertGreater(helper._session.attempts, 0)

    def test_returning_to_lobby_between_snapshot_and_dispatch_is_rejected(self):
        reads = []
        def poll(pid):
            reads.append(pid)
            return SimpleNamespace(pid=pid,hero_id='Chromie',status='available' if len(reads)==1 else 'unknown',
                                   generation=('game',1),pending_tier=1,selected=((1,-1),))
        helper = TalentHelper(reader_factory=lambda:SimpleNamespace(poll=poll, close=lambda:None),
                              backend_factory=lambda:SimpleNamespace(foreground_target=lambda:GameTarget(1,42)),
                              session_factory=Session)
        self.addCleanup(helper.close)
        helper.set_enabled(True)
        # wait for construction without timing sleeps
        for _ in range(200):
            if helper._session is not None:break
            threading.Event().wait(.005)
        self.assertTrue(helper._session.polled.wait(2))
        helper.set_enabled(False)
        self.assertGreaterEqual(len(reads), 2)
        self.assertEqual(helper._session.attempts, 0)

    def test_reenable_preserves_session_and_old_events_are_rejected(self):
        helper, _, entered, release, finished = self.make(blocked=True)
        helper.set_enabled(True)
        self.assertTrue(entered.wait(2))
        session = helper._session
        old = helper.drain_status()[-1]
        release.set(); helper.set_enabled(False)
        self.assertFalse(helper.accepts_status(old))
        helper.set_enabled(True)
        self.assertIs(helper._session, session)
        helper.close()
        self.assertFalse(helper.set_enabled(True))

    def test_disable_and_suspend_are_visible_inside_blocked_dispatch(self):
        for disable in (False, True):
            entered, release = threading.Event(), threading.Event()
            gates = []
            class BlockedSession(Session):
                def poll(self, snapshot, now, target, *, allowed, validate=None):
                    entered.set(); release.wait(2)
                    gates.append(allowed())
                    return SimpleNamespace(state='ready', detail='', tier_id=None)
            helper = TalentHelper(
                reader_factory=lambda:SimpleNamespace(poll=lambda pid:SimpleNamespace(pid=pid),close=lambda:None),
                backend_factory=lambda:SimpleNamespace(foreground_target=lambda:GameTarget(1, 42)),
                session_factory=BlockedSession)
            self.addCleanup(helper.close); self.addCleanup(release.set)
            helper.set_enabled(True)
            self.assertTrue(entered.wait(2))
            completed = threading.Event()
            def cancel():
                if disable:helper.set_enabled(False, wait=False)
                else:helper.set_suspended(True)
                completed.set()
            thread = threading.Thread(target=cancel)
            thread.start()
            self.assertTrue(completed.wait(.3), '取消入口不应等待输入调用持有的锁')
            release.set(); thread.join(2); helper.close()
            self.assertEqual(gates, [False])
            self.assertEqual(helper.drain_status()[-1].state, 'disabled')

    def test_retired_worker_cannot_publish_new_generation_status(self):
        helper, *_ = self.make()
        helper._emit('waiting', '旧工作线程', source_generation=-1)
        self.assertEqual(helper.drain_status()[-1].state, 'disabled')

    def test_concurrent_status_publication_is_bounded(self):
        helper, *_ = self.make()
        errors = []
        def publish(prefix):
            try:
                for i in range(200):helper._emit('ready', f'{prefix}{i}')
            except Exception as failure:errors.append(failure)
        threads = [threading.Thread(target=publish,args=(str(i),)) for i in range(3)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(2)
        self.assertEqual(errors, [])
        self.assertEqual(len(helper.drain_status()), 1)

    def test_unknown_state_backs_off_reconnect_but_active_state_keeps_fast_polling(self):
        from hots_chat.acquisition.talent_source import TalentSnapshot, TalentTier
        for status in ('unknown', 'idle'):
            waits, reads = [], []
            class Stop:
                stopped = False
                def is_set(self):return self.stopped
                def wait(self, delay):waits.append(delay); self.stopped = True
            snapshot = TalentSnapshot(42, ('process', 'match'), 'Chromie', 1,
                                      (TalentTier(1, 1, 0, True),), None, status)
            def poll(pid):reads.append(pid); return snapshot
            helper = TalentHelper(reader_factory=lambda:SimpleNamespace(poll=poll,close=lambda:None),
                                  backend_factory=lambda:SimpleNamespace(foreground_target=lambda:GameTarget(1,42)))
            helper._enabled = True; helper._generation = 1
            helper._run(1, Stop())
            self.assertEqual(reads, [42])
            if status == 'unknown':self.assertGreaterEqual(waits[0], .25)
            else:self.assertLessEqual(waits[0], .05)

    def test_real_native_boundary_rejects_disable_and_focus_change_during_queries(self):
        from hots_chat.acquisition.talent_source import TalentSnapshot, TalentTier
        from hots_chat.talent_native import Win32TalentBackend
        from tests.test_loot_native import FakeUser, FakeKernel
        for action in ('disable', 'focus'):
            with self.subTest(action=action):
                user, kernel = FakeUser(), FakeKernel()
                done = threading.Event()
                reads, outcomes = [], []
                snapshot = TalentSnapshot(user.pid, ('process', 'match'), 'Chromie', 1,
                                          (TalentTier(1, 1, -1, True),), 1, 'available')
                class Reader:
                    def poll(self, pid):
                        reads.append(pid)
                        if action == 'focus' and len(reads) == 2:user.hwnd += 1
                        return snapshot
                    def close(self):pass
                class Backend(Win32TalentBackend):
                    def select_favorite(self, *args, **kwargs):
                        result = super().select_favorite(*args, **kwargs)
                        outcomes.append(result)
                        helper.set_suspended(True)
                        done.set()
                        return result
                native = Backend(user=user, kernel=kernel)
                helper = TalentHelper(reader_factory=Reader, backend_factory=lambda:native)
                self.addCleanup(helper.close)
                calls = []
                original = user.GetAsyncKeyState.implementation
                def physical(vk):
                    calls.append(vk)
                    if action == 'disable' and len(calls) == 25:
                        helper.set_enabled(False, wait=False)
                    return original(vk)
                user.GetAsyncKeyState.implementation = physical
                helper.set_enabled(True)
                self.assertTrue(done.wait(2))
                helper.close()
                self.assertEqual(outcomes, ['deferred'])
                self.assertEqual(user.sent, [])
