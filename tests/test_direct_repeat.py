"""直接功能键连按；仅虚构窗口、时钟与Win32 API，不发送真实按键。"""
import queue
import threading
import unittest
from unittest.mock import patch

from hots_chat import repeat
from hots_chat.repeat_native import Win32RepeatBackend
from tests.test_repeat import Backend
from tests.test_loot_native import FakeUser, FakeKernel
from tests.test_repeat_native import FakeWinMM


class DirectBackend(Backend):
    def __init__(self):
        super().__init__()
        self.periods = []

    def set_repeat_active(self, active):
        self.periods.append(active)


class DirectSessionTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(repeat, 'DirectRepeatSession'), '需要免准备的直接连按会话')
        self.backend = DirectBackend()
        self.events = []
        self.now = [10.]
        self.confirmed = [False]
        self.serial = 0
        def next_generation():
            self.serial += 1
            return self.serial
        self.session = repeat.DirectRepeatSession(
            self.backend, self.events.append, clock=lambda: self.now[0],
            can_start=lambda: self.confirmed[0], next_generation=next_generation)
        self.session.listen()

    def press(self, vk=0x77):
        result = self.session.key(vk, True, 0)
        self.assertEqual(self.session.key(vk, False, 0), result)
        return result

    def start(self):
        self.assertTrue(self.press())
        self.assertEqual(self.session.status.state, 'requested')
        self.confirmed[0] = True
        self.session.tick()

    def test_fresh_key_requests_and_waits_for_visible_ui_then_cancels_instead_of_pauses(self):
        self.assertTrue(self.session.tick())
        self.assertEqual(self.backend.sent, [])
        self.assertTrue(self.press())
        self.session.tick()
        self.assertEqual(self.session.status.state, 'requested')
        self.assertEqual(self.backend.sent, [])
        self.confirmed[0] = True
        self.session.tick()
        self.assertEqual(self.session.status.state, 'running')
        self.assertEqual(len(self.backend.sent), 1)
        self.assertTrue(self.press())
        self.assertEqual(self.session.status.state, 'idle')
        self.assertTrue(self.session.tick())
        self.assertEqual(self.backend.periods, [True, False])

    def test_second_key_before_confirmation_cancels_request_and_next_key_has_new_generation(self):
        self.press()
        first = self.session.status.generation
        self.press()
        self.assertEqual(self.session.status.state, 'idle')
        self.press()
        self.assertGreater(self.session.status.generation, first)
        self.assertEqual(self.backend.sent, [])

    def test_non_game_and_modified_key_are_never_consumed_or_started(self):
        self.backend.target = None
        self.assertFalse(self.press())
        self.backend.target = (11, 22)
        self.backend.modified = True
        self.assertFalse(self.press())
        self.assertEqual(self.session.status.state, 'idle')
        self.assertEqual(self.backend.sent, [])

    def test_f8_after_focus_loss_is_passed_through_and_cancels_old_run(self):
        self.start()
        self.backend.target = None
        self.assertFalse(self.press())
        self.assertEqual(self.session.status.state, 'idle')
        self.assertEqual(len(self.backend.sent), 1)

    def test_held_enter_does_not_block_a_fresh_f8_request(self):
        self.session.key(0x0D, True, 0)
        for _ in range(5):self.session.key(0x0D, True, 0)
        self.session.key(0x0D, False, 0)
        self.assertTrue(self.press())
        self.assertEqual(self.session.status.state, 'requested')
        self.press(0x0D)
        self.assertEqual(self.session.status.state, 'idle')
        self.assertTrue(self.press())

    def test_resolution_start_failure_stops_before_sending(self):
        def set_active(active):
            if active:raise OSError('fictional begin failure')
        self.backend.set_repeat_active = set_active
        self.press(); self.confirmed[0] = True
        self.assertFalse(self.session.tick())
        self.assertEqual(self.session.status.state, 'error')
        self.assertIn('begin failure', self.session.status.detail)
        self.assertEqual(self.backend.sent, [])

    def test_resolution_release_failure_is_reported_when_cancelled(self):
        self.start()
        def set_active(active):
            if not active:raise OSError('fictional end failure')
        self.backend.set_repeat_active = set_active
        self.session.cancel()
        self.assertEqual(self.session.status.state, 'error')
        self.assertIn('end failure', self.session.status.detail)
        self.assertFalse(self.session.tick())

    def test_held_key_and_self_injection_do_not_retoggle(self):
        self.assertTrue(self.session.key(0x77, True, 0))
        self.assertTrue(self.session.key(0x77, True, 0))
        self.assertFalse(self.session.key(0x77, False, repeat.INPUT_MARKER))
        self.assertTrue(self.session.key(0x77, True, 0))
        self.assertEqual(self.session.status.state, 'requested')
        self.assertTrue(self.session.key(0x77, False, 0))
        self.assertTrue(self.press())
        self.assertEqual(self.session.status.state, 'idle')

    def test_f8_starts_with_chat_open_and_sends_only_after_the_bottom_hint_is_confirmed(self):
        self.press(0x0D)
        self.assertTrue(self.press())
        self.assertEqual(self.events[-1].state, 'requested')
        self.assertIn('F8', self.events[-1].detail)
        self.assertIn('取消', self.events[-1].detail)
        self.session.tick()
        self.assertEqual(self.backend.sent, [])
        self.confirmed[0] = True
        self.session.tick()
        self.assertEqual(self.session.status.state, 'running')
        self.assertEqual(len(self.backend.sent), 1)
        self.assertTrue(self.press())
        self.assertEqual(self.session.status.state, 'idle')

    def test_enter_cancels_the_current_run_but_does_not_block_the_next_f8(self):
        self.start()
        self.press(0x0D)
        self.assertEqual(self.session.status.state, 'idle')
        self.assertTrue(self.press())
        self.assertEqual(self.session.status.state, 'requested')

    def test_escape_cancels_without_requiring_any_extra_reset_before_f8(self):
        self.start()
        self.press(0x1B)
        self.assertEqual(self.session.status.state, 'idle')
        self.assertTrue(self.press())
        self.assertEqual(self.session.status.state, 'requested')

    def test_repeated_chat_and_scene_changes_do_not_leave_a_stale_chat_block(self):
        for index in range(20):
            self.press(0x0D)
            self.backend.target = None
            self.session.tick()
            self.backend.target = (11, 22)
            self.assertTrue(self.press(), f'第{index + 1}次切回仍应能直接启动')
            self.assertEqual(self.session.status.state, 'requested')
            self.press(0x1B)

    def test_focus_exit_ends_run_without_automatic_resume_but_allows_fresh_key(self):
        self.start()
        self.backend.target = (12, 22)
        self.assertTrue(self.session.tick())
        self.assertEqual(self.session.status.state, 'idle')
        self.backend.target = (11, 22)
        self.now[0] += 1
        self.session.tick()
        self.assertEqual(len(self.backend.sent), 1)
        self.assertTrue(self.press())
        self.assertEqual(self.session.status.state, 'requested')

    def test_ui_wait_does_not_change_target_or_survive_chat(self):
        self.press()
        self.backend.target = (11, 23)
        self.confirmed[0] = True
        self.session.tick()
        self.assertEqual(self.session.status.state, 'idle')
        self.assertEqual(self.backend.sent, [])

    def test_running_timing_preserves_twenty_ms_and_mouse_game_keys(self):
        self.start()
        for step in range(1, 100):
            self.now[0] = 10 + step * .01
            self.session.key(ord('Q'), True, 0)
            self.session.mouse()
            self.session.tick()
        self.assertEqual(len(self.backend.sent), 50)


class ControlledBackend(DirectBackend):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.commands = queue.Queue()

    def run_listening(self, key, mouse, tick, ready, stopped):
        ready()
        self.started.set()
        while not stopped.is_set():
            try:
                command = self.commands.get(timeout=.1)
            except queue.Empty:
                continue
            if command is None:
                return
            name, complete = command
            if name == 'press':
                key(0x77, True, 0)
                key(0x77, False, 0)
            elif name == 'tick':
                tick()
            complete.set()

    def command(self, name):
        complete = threading.Event()
        self.commands.put((name, complete))
        if not complete.wait(2):
            raise AssertionError('模拟消息泵未完成')

    def stop(self):
        self.commands.put(None)


class DirectHelperTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(repeat.RepeatHelper, 'start_listening'), '需要常驻直接热键入口')
        self.backend = ControlledBackend()
        self.helper = repeat.RepeatHelper(backend_factory=lambda: self.backend)
        self.addCleanup(self.helper.close)
        self.assertTrue(self.helper.start_listening())
        self.assertTrue(self.backend.started.wait(2))

    def request(self):
        self.backend.command('press')
        return self.helper.drain_status()[-1]

    def test_idle_listener_is_not_an_active_input_mode_and_start_requires_generation_confirmation(self):
        self.assertTrue(self.helper.listening)
        self.assertFalse(self.helper.active)
        requested = self.request()
        self.assertEqual(requested.state, 'requested')
        self.assertTrue(self.helper.active)
        self.assertFalse(self.helper.confirm_ready(requested.generation - 1))
        self.backend.command('tick')
        self.assertEqual(self.backend.sent, [])
        self.assertTrue(self.helper.confirm_ready(requested.generation))
        self.backend.command('tick')
        self.assertEqual(len(self.backend.sent), 1)

    def test_old_confirm_and_old_cancel_cannot_affect_new_request(self):
        old = self.request()
        self.helper.cancel(generation=old.generation)
        fresh = self.request()
        self.assertGreater(fresh.generation, old.generation)
        self.assertFalse(self.helper.accepts_status(old))
        self.assertFalse(self.helper.confirm_ready(old.generation))
        self.helper.cancel(generation=old.generation)
        self.assertTrue(self.helper.active)
        self.assertTrue(self.helper.confirm_ready(fresh.generation))
        self.backend.command('tick')
        self.assertEqual(len(self.backend.sent), 1)

    def test_cancel_preserves_listener_closes_send_gate_and_allows_new_press(self):
        requested = self.request()
        self.helper.confirm_ready(requested.generation)
        self.backend.command('tick')
        self.helper.cancel()
        self.assertFalse(self.helper.active)
        self.assertTrue(self.helper.listening)
        self.backend.command('tick')
        self.assertEqual(len(self.backend.sent), 1)
        self.assertEqual(self.request().state, 'requested')

    def test_queued_cancelled_request_is_never_replayed_to_ui(self):
        self.backend.command('press')
        self.backend.command('press')
        self.assertEqual([s.state for s in self.helper.drain_status()], ['idle'])
        self.assertFalse(self.helper.active)

    def test_close_releases_listener_and_rejects_restart(self):
        self.helper.close()
        self.assertFalse(self.helper.listening)
        self.assertFalse(self.helper.active)
        self.assertFalse(self.helper.start_listening())

    def test_native_initialization_failure_is_not_discarded_as_an_old_status(self):
        def broken():raise OSError('fictional native failure')
        helper = repeat.RepeatHelper(backend_factory=broken)
        self.addCleanup(helper.close)
        self.assertTrue(helper.start_listening())
        helper.thread.join(2)
        statuses = helper.drain_status()
        self.assertEqual([status.state for status in statuses], ['error'])
        self.assertIn('native failure', statuses[0].detail)
        self.assertFalse(helper.listening)

    def test_cancel_resource_error_remains_visible_after_late_tick(self):
        requested = self.request()
        self.helper.confirm_ready(requested.generation)
        self.backend.command('tick')
        def set_active(active):
            if not active:raise OSError('fictional release failure')
        self.backend.set_repeat_active = set_active
        self.helper.cancel()
        self.backend.command('tick')
        statuses = self.helper.drain_status()
        self.assertEqual([status.state for status in statuses], ['error'])
        self.assertIn('release failure', statuses[0].detail)
        self.assertFalse(self.helper.active)


class DirectNativeTests(unittest.TestCase):
    def setUp(self):
        self.user, self.kernel, self.winmm = FakeUser(), FakeKernel(), FakeWinMM()
        self.backend = Win32RepeatBackend(user=self.user, kernel=self.kernel, winmm=self.winmm)

    def run_listener(self, ready=lambda: None):
        self.assertTrue(hasattr(self.backend, 'run_listening'), '需要空闲不申请高精度的监听消息泵')
        self.backend.run_listening(lambda *args: False, lambda: None, lambda: True,
                                   ready, threading.Event())

    def test_idle_does_not_request_high_resolution_and_running_pairs_it(self):
        self.run_listener()
        self.assertEqual(self.winmm.calls, [])
        def ready():
            self.backend.set_repeat_active(True)
            self.backend.set_repeat_active(True)
            self.backend.set_repeat_active(False)
        self.run_listener(ready)
        self.assertEqual(self.winmm.calls, [('begin', 1), ('end', 1)])

    def test_active_callback_failure_always_releases_resolution_and_hooks(self):
        def ready():
            self.backend.set_repeat_active(True)
            raise RuntimeError('primary failure')
        with self.assertRaisesRegex(RuntimeError, 'primary failure'):
            self.run_listener(ready)
        self.assertEqual(self.winmm.calls, [('begin', 1), ('end', 1)])
        self.assertEqual(len(self.user.unhooked), 2)

    def test_idle_listener_does_not_load_winmm(self):
        self.backend._winmm = None
        with patch('ctypes.WinDLL', side_effect=AssertionError('空闲不能加载WinMM')):
            self.run_listener()

