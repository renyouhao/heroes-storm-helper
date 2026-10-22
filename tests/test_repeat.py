"""S连按状态与生命周期；所有时钟、窗口和输入均为离线替身。"""
import importlib
import queue
import threading
import unittest


class Backend:
    def __init__(self):
        self.target = (11, 22)
        self.modified = False
        self.held = False
        self.sent = []
        self.failure = None

    def toggle_down(self, vk):
        return self.held

    def foreground_target(self):
        return self.target

    def modifiers_down(self):
        return self.modified

    def send_s(self, target, *, allowed=None):
        if allowed is not None and not allowed():
            return False
        if self.failure:
            raise self.failure
        self.sent.append(target)
        return True


def module(test):
    try:
        return importlib.import_module('hots_chat.repeat')
    except ImportError as failure:
        test.fail('尚未提供S连按状态机：' + str(failure))


class RepeatSessionTests(unittest.TestCase):
    def setUp(self):
        self.module = module(self)
        self.backend = Backend()
        self.now = [10.]
        self.events = []
        self.session = self.module.RepeatSession(
            self.backend, self.events.append, clock=lambda: self.now[0])
        self.session.prepare()

    def press(self, vk=0x77):
        result = self.session.key(vk, True, 0)
        self.assertEqual(self.session.key(vk, False, 0), result)
        return result

    def test_ready_never_sends_until_fresh_toggle_then_pauses_and_resumes(self):
        self.session.tick()
        self.assertEqual(self.backend.sent, [])
        self.assertTrue(self.press())
        self.session.tick()
        self.assertEqual(self.backend.sent, [(11, 22)])
        self.press()
        self.now[0] += 1.
        self.session.tick()
        self.assertEqual(self.session.status.state, 'paused')
        self.assertEqual(len(self.backend.sent), 1)
        self.press()
        self.session.tick()
        self.assertEqual(len(self.backend.sent), 2)

    def test_tick_uses_twenty_ms_interval_and_never_catches_up_after_delay(self):
        self.press()
        self.session.tick()
        self.now[0] += .019
        self.session.tick()
        self.assertEqual(len(self.backend.sent), 1)
        self.now[0] += .002
        self.session.tick()
        self.assertEqual(len(self.backend.sent), 2)
        self.now[0] += 60.
        self.session.tick()
        self.session.tick()
        self.assertEqual(len(self.backend.sent), 3)

    def test_ten_ms_ticks_keep_fast_send_starts_twenty_ms_apart(self):
        original = self.backend.send_s
        starts = []
        def fast(*args, **kwargs):
            starts.append(self.now[0])
            self.now[0] += .0001
            return original(*args, **kwargs)
        self.backend.send_s = fast
        self.press()
        for step in range(100):
            self.now[0] = 10. + step * .010
            self.session.tick()
        self.assertEqual(len(starts), 50)
        for previous, current in zip(starts, starts[1:]):
            self.assertAlmostEqual(current - previous, .020, places=7)

    def test_fast_send_cadence_is_independent_of_monotonic_clock_origin(self):
        for base, query_time in ((0., 0.), (10., 0.), (1000., 0.), (100000., 0.),
                                 (0., .0001), (10., .0001),
                                 (1000., .0001), (100000., .0001)):
            with self.subTest(base=base, query_time=query_time):
                self.now[0] = base
                backend = Backend()
                starts = []
                original = backend.send_s
                def fast(*args, **kwargs):
                    starts.append(self.now[0])
                    self.now[0] += .0001
                    return original(*args, **kwargs)
                def foreground():
                    self.now[0] += query_time
                    return backend.target
                backend.send_s = fast
                backend.foreground_target = foreground
                session = self.module.RepeatSession(
                    backend, self.events.append, clock=lambda: self.now[0])
                session.prepare()
                session.key(0x77, True, 0)
                session.key(0x77, False, 0)
                for step in range(100):
                    self.now[0] = base + step * .010
                    session.tick()
                self.assertEqual(len(starts), 50)
                for previous, current in zip(starts, starts[1:]):
                    self.assertAlmostEqual(current - previous, .020, places=7)

    def test_foreground_query_time_does_not_quantize_fast_sends_to_thirty_ms(self):
        original_send = self.backend.send_s
        starts = []
        def foreground():
            self.now[0] += .0001
            return self.backend.target
        def fast(*args, **kwargs):
            starts.append(self.now[0])
            self.now[0] += .0001
            return original_send(*args, **kwargs)
        self.backend.foreground_target = foreground
        self.backend.send_s = fast
        self.press()
        for step in range(100):
            self.now[0] = 10. + step * .010
            self.session.tick()
        self.assertEqual(len(starts), 50)
        for previous, current in zip(starts, starts[1:]):
            self.assertAlmostEqual(current - previous, .020, places=7)

    def test_slow_send_starts_a_full_interval_after_completion(self):
        original = self.backend.send_s
        def slow(*args, **kwargs):
            self.now[0] += .03
            return original(*args, **kwargs)
        self.backend.send_s = slow
        self.press()
        self.session.tick()
        self.session.tick()
        self.now[0] += .019
        self.session.tick()
        self.assertEqual(len(self.backend.sent), 1)

    def test_send_taking_exactly_one_interval_waits_again_instead_of_immediately_repeating(self):
        original = self.backend.send_s
        def slow(*args, **kwargs):
            self.now[0] += .020
            return original(*args, **kwargs)
        self.backend.send_s = slow
        self.press()
        self.session.tick()
        self.session.tick()
        self.assertEqual(len(self.backend.sent), 1)
        self.now[0] += .019
        self.session.tick()
        self.assertEqual(len(self.backend.sent), 1)

    def test_slow_send_boundary_is_independent_of_monotonic_clock_origin(self):
        for base in (0., 10., 1000., 100000.):
            for duration in (.020, .030):
                with self.subTest(base=base, duration=duration):
                    self.now[0] = base
                    backend = Backend()
                    original = backend.send_s
                    def slow(*args, **kwargs):
                        self.now[0] += duration
                        return original(*args, **kwargs)
                    backend.send_s = slow
                    session = self.module.RepeatSession(
                        backend, self.events.append, clock=lambda: self.now[0])
                    session.prepare()
                    session.key(0x77, True, 0)
                    session.key(0x77, False, 0)
                    session.tick()
                    session.tick()
                    self.now[0] += .019
                    session.tick()
                    self.assertEqual(len(backend.sent), 1)
                    self.now[0] += .002
                    session.tick()
                    self.assertEqual(len(backend.sent), 2)
    def test_long_hold_toggles_once_and_injected_events_do_not_release_guard(self):
        self.assertTrue(self.session.key(0x77, True, 0))
        for _ in range(10):
            self.assertTrue(self.session.key(0x77, True, 0))
        for down in (True, False):
            self.assertFalse(self.session.key(0x77, down, self.module.INPUT_MARKER))
        self.assertTrue(self.session.key(0x77, True, 0))
        self.assertEqual(self.session.status.state, 'running')
        self.assertTrue(self.session.key(0x77, False, 0))
        self.press()
        self.assertEqual(self.session.status.state, 'paused')

    def test_preheld_toggle_and_unconfirmed_ui_each_require_release_then_new_press(self):
        self.backend.held = True
        confirmed = [False]
        self.session = self.module.RepeatSession(
            self.backend, self.events.append, clock=lambda: self.now[0],
            can_start=lambda: confirmed[0])
        self.session.prepare()
        self.assertFalse(self.session.key(0x77, True, 0))
        self.session.key(0x77, False, 0)
        self.assertFalse(self.session.key(0x77, True, 0))
        confirmed[0] = True
        self.assertFalse(self.session.key(0x77, True, 0))
        self.session.key(0x77, False, 0)
        self.assertTrue(self.press())

    def test_normal_game_keys_and_mouse_allow_continuous_s(self):
        self.press()
        for vk in (ord('Q'), ord('W'), ord('E'), ord('R'), ord('S')):
            self.assertFalse(self.session.key(vk, True, 0))
            self.assertFalse(self.session.key(vk, False, 0))
        self.session.mouse()
        self.session.tick()
        self.assertEqual(self.session.status.state, 'running')
        self.assertEqual(len(self.backend.sent), 1)

    def test_enter_escape_and_control_combinations_end_ready_running_and_paused(self):
        for state in ('ready', 'running', 'paused'):
            for vk in (0x0D, 0x1B, 0x11, 0xA2, 0x12, 0x5B):
                with self.subTest(state=state, vk=vk):
                    self.session.prepare()
                    if state != 'ready':
                        self.press()
                    if state == 'paused':
                        self.press()
                    self.assertFalse(self.session.key(vk, True, 0))
                    self.assertFalse(self.session.tick())
                    self.assertEqual(self.session.status.state, 'idle')
        self.session.prepare()
        self.backend.modified = True
        self.assertFalse(self.session.key(ord('T'), True, 0))
        self.assertEqual(self.session.status.state, 'idle')

    def test_modified_toggle_ends_without_consuming_the_combination(self):
        self.backend.modified = True
        self.assertFalse(self.press())
        self.assertFalse(self.session.tick())
        self.assertEqual(self.session.status.state, 'idle')

    def test_modified_toggle_also_ends_before_ui_confirmation(self):
        self.session = self.module.RepeatSession(
            self.backend, self.events.append, clock=lambda: self.now[0], can_start=lambda: False)
        self.session.prepare()
        self.backend.modified = True
        self.assertFalse(self.press())
        self.assertEqual(self.session.status.state, 'idle')

    def test_ready_may_switch_to_game_but_non_game_toggle_is_not_captured(self):
        self.backend.target = None
        self.assertFalse(self.press())
        self.assertTrue(self.session.tick())
        self.backend.target = (11, 22)
        self.assertTrue(self.press())

    def test_foreground_loss_or_other_hwnd_or_pid_ends_running_and_paused_permanently(self):
        for paused in (False, True):
            for target in (None, (12, 22), (11, 23)):
                with self.subTest(paused=paused, target=target):
                    self.backend.target = (11, 22)
                    self.session.prepare()
                    self.press()
                    if paused:
                        self.press()
                    self.backend.target = target
                    self.assertFalse(self.session.tick())
                    self.backend.target = (11, 22)
                    self.assertFalse(self.press())
                    self.assertEqual(self.session.status.state, 'idle')
        self.assertEqual(self.backend.sent, [])

    def test_cancel_keeps_captured_toggle_pair_until_its_physical_release(self):
        self.assertTrue(self.session.key(0x77, True, 0))
        self.session.cancel()
        self.assertTrue(self.session.tick())
        self.assertTrue(self.session.key(0x77, True, 0))
        self.assertTrue(self.session.key(0x77, False, 0))
        self.assertFalse(self.session.tick())
        self.assertEqual(self.backend.sent, [])

    def test_preparation_deadline_cannot_be_bypassed_by_delayed_toggle(self):
        self.now[0] += 30.
        self.assertFalse(self.press())
        self.assertFalse(self.session.tick())
        self.assertEqual(self.session.status.state, 'idle')

    def test_failed_send_stops_without_automatic_retry(self):
        self.backend.failure = OSError('blocked')
        self.press()
        self.assertFalse(self.session.tick())
        self.assertEqual(self.session.status.state, 'error')
        self.assertIn('blocked', self.session.status.detail)
        self.backend.failure = None
        self.now[0] += 1.
        self.session.tick()
        self.assertEqual(self.backend.sent, [])

    def test_custom_function_key_is_the_only_toggle(self):
        self.session = self.module.RepeatSession(
            self.backend, self.events.append, binding=' f12 ', clock=lambda: self.now[0])
        self.session.prepare()
        self.assertFalse(self.press())
        self.assertTrue(self.press(0x7B))

    def test_parser_accepts_only_f1_through_f12(self):
        for text, expected in (('f1', 0x70), (' F8 ', 0x77), ('F12', 0x7B)):
            self.assertEqual(self.module.parse_repeat_key(text), expected)
        for invalid in ('F0', 'F13', 'Ctrl+F8', 'S', '', 'F01', None, 8):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.module.parse_repeat_key(invalid)


class RepeatHelperTests(unittest.TestCase):
    def helper(self, backend_factory):
        helper = module(self).RepeatHelper(backend_factory=backend_factory)
        self.addCleanup(helper.close)
        return helper

    def test_default_startup_does_not_construct_native_backend(self):
        calls = []
        helper = self.helper(lambda: calls.append(1))
        self.assertFalse(helper.active)
        helper.close()
        self.assertEqual(calls, [])
        self.assertFalse(helper.prepare())

    def test_backend_failure_reports_error_and_invalid_binding_never_starts_thread(self):
        def fail():
            raise OSError('hook unavailable')
        helper = self.helper(fail)
        with self.assertRaises(ValueError):
            helper.prepare('Ctrl+F8')
        self.assertFalse(helper.active)
        self.assertTrue(helper.prepare())
        helper.thread.join(2)
        self.assertEqual(helper.drain_status()[-1].state, 'error')

    def test_ui_confirmation_and_generation_filter_cannot_start_old_or_cancelled_session(self):
        started, completed = threading.Event(), threading.Event()
        commands = queue.Queue()
        class ControlledBackend(Backend):
            def run(self, key, mouse, tick, ready, stop_event):
                ready()
                started.set()
                while not stop_event.is_set():
                    command = commands.get(timeout=2)
                    if command is None:
                        break
                    key(0x77, True, 0)
                    key(0x77, False, 0)
                    tick()
                    completed.set()
            def stop(self):
                commands.put(None)
        backend = ControlledBackend()
        helper = self.helper(lambda: backend)
        helper.prepare()
        self.assertTrue(started.wait(2))
        ready = helper.drain_status()[-1]
        def press():
            completed.clear()
            commands.put('press')
            self.assertTrue(completed.wait(2))
        press()
        helper.confirm_ready(ready.generation - 1)
        press()
        self.assertEqual(backend.sent, [])
        helper.confirm_ready(ready.generation)
        press()
        self.assertEqual(len(backend.sent), 1)
        helper.cancel()
        helper.confirm_ready(ready.generation)
        press()
        self.assertEqual(len(backend.sent), 1)
        self.assertNotIn('running', [s.state for s in helper.drain_status()])

    def test_close_stops_backend_prevents_overlap_and_waits_for_worker(self):
        started, stopped = threading.Event(), threading.Event()
        class WaitingBackend(Backend):
            def run(self, key, mouse, tick, ready, stop_event):
                ready()
                started.set()
                stopped.wait(2)
            def stop(self):
                stopped.set()
        helper = self.helper(WaitingBackend)
        self.assertTrue(helper.prepare())
        self.assertTrue(started.wait(2))
        self.assertFalse(helper.prepare())
        helper.close()
        self.assertFalse(helper.active)
        self.assertFalse(helper.prepare())

    def test_cancel_is_a_fence_for_an_in_progress_send(self):
        ready, begin, entered, release, finished = [threading.Event() for _ in range(5)]
        class SlowBackend(Backend):
            def send_s(self, target, *, allowed=None):
                entered.set()
                release.wait(2)
                return super().send_s(target, allowed=allowed)
            def run(self, key, mouse, tick, on_ready, stop_event):
                on_ready()
                ready.set()
                begin.wait(2)
                key(0x77, True, 0)
                key(0x77, False, 0)
                tick()
            def stop(self):
                release.set()
        backend = SlowBackend()
        helper = self.helper(lambda: backend)
        helper.prepare()
        self.assertTrue(ready.wait(2))
        helper.confirm_ready(helper.drain_status()[-1].generation)
        begin.set()
        self.assertTrue(entered.wait(2))
        cancel = threading.Thread(target=lambda: (helper.cancel(), finished.set()))
        cancel.start()
        self.assertFalse(finished.wait(.03), '取消返回前必须越过发送栅栏')
        release.set()
        cancel.join(2)
        helper.thread.join(2)
        self.assertTrue(finished.is_set())
        self.assertEqual(backend.sent, [])

    def test_new_preparation_discards_old_unread_status(self):
        class ImmediateBackend(Backend):
            def run(self, key, mouse, tick, ready, stop_event):
                ready()
            def stop(self):
                pass
        helper = self.helper(ImmediateBackend)
        for _ in range(2):
            self.assertTrue(helper.prepare())
            helper.thread.join(2)
        self.assertEqual({s.generation for s in helper.drain_status()}, {2})


if __name__ == '__main__':
    unittest.main()
