"""连续开箱状态与生命周期；只使用假后端和可控时钟。"""
import importlib
import ctypes
import queue
import threading
import unittest


class Backend:
    def __init__(self):
        self.target = (11, 22)
        self.modified = False
        self.sent = []
        self.failure = None
        self.held = False

    def space_down(self):
        return self.held

    def foreground_target(self):
        return self.target

    def modifiers_down(self):
        return self.modified

    def send_space(self, target, *, allowed=None):
        if allowed is not None and not allowed():
            return False
        if self.failure:
            raise self.failure
        self.sent.append(target)
        return True


class LootSessionTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module('hots_chat.loot')
        except ImportError:
            self.fail('尚未提供隔离的开箱状态机')
        self.backend = Backend()
        self.now = [10.]
        self.events = []
        self.session = self.module.LootSession(self.backend, self.events.append,
                                               clock=lambda: self.now[0])
        self.session.prepare()

    def space(self):
        result = self.session.key(0x20, True, 0)
        self.session.key(0x20, False, 0)
        return result

    def test_prepare_does_not_send_until_space_and_second_press_pauses(self):
        self.assertEqual(self.session.status.state, 'ready')
        self.session.tick()
        self.assertFalse(self.backend.sent)
        self.assertTrue(self.space())
        self.session.tick()
        self.assertEqual(self.backend.sent, [(11, 22)])
        self.assertTrue(self.space())
        self.now[0] += 2
        self.session.tick()
        self.assertEqual(self.session.status.state, 'paused')
        self.assertEqual(len(self.backend.sent), 1)
        self.space()
        self.session.tick()
        self.assertEqual(len(self.backend.sent), 2)

    def test_holding_space_never_repeatedly_toggles(self):
        self.assertTrue(self.session.key(0x20, True, 0))
        for _ in range(20):
            self.assertTrue(self.session.key(0x20, True, 0))
        self.assertEqual(self.session.status.state, 'running')
        self.assertTrue(self.session.key(0x20, False, 0))
        self.space()
        self.assertEqual(self.session.status.state, 'paused')

    def test_self_injected_space_cannot_toggle_or_release_physical_guard(self):
        self.session.key(0x20, True, 0)
        for down in (True, False):
            self.assertFalse(self.session.key(0x20, down, self.module.INPUT_MARKER))
        self.assertTrue(self.session.key(0x20, True, 0))
        self.assertEqual(self.session.status.state, 'running')

    def test_other_keyboard_input_ends_mode_and_is_not_swallowed(self):
        self.space()
        self.assertFalse(self.session.key(ord('A'), True, 0))
        self.now[0] += 2
        self.assertFalse(self.session.tick())
        self.assertEqual(self.session.status.state, 'idle')
        self.assertFalse(self.backend.sent)

    def test_mouse_only_cancels_after_start_and_escape_also_cancels_preparation(self):
        self.session.mouse()
        self.assertEqual(self.session.status.state, 'ready')
        self.space()
        self.session.mouse()
        self.assertEqual(self.session.status.state, 'idle')
        self.session.prepare()
        self.session.key(0x1B, True, 0)
        self.assertFalse(self.session.tick())

    def test_non_game_space_passes_through_without_starting(self):
        self.backend.target = None
        self.assertFalse(self.space())
        self.session.tick()
        self.assertEqual(self.session.status.state, 'ready')
        self.assertFalse(self.backend.sent)

    def test_modified_space_cannot_start(self):
        self.backend.modified = True
        self.assertFalse(self.space())
        self.session.tick()
        self.assertFalse(self.backend.sent)

    def test_foreground_loss_ends_running_and_cannot_auto_resume(self):
        self.space()
        self.backend.target = None
        self.assertFalse(self.session.tick())
        self.backend.target = (11, 22)
        self.now[0] += 2
        self.session.tick()
        self.assertFalse(self.backend.sent)
        self.assertEqual(self.session.status.state, 'idle')

    def test_same_game_other_window_or_process_is_not_a_valid_resume_target(self):
        for other in ((12, 22), (11, 23)):
            with self.subTest(other=other):
                self.backend.target = (11, 22)
                self.session.prepare()
                self.space()
                self.space()
                self.backend.target = other
                self.assertFalse(self.session.tick())
                self.assertEqual(self.session.status.state, 'idle')

    def test_late_tick_sends_one_pair_and_does_not_catch_up(self):
        self.space()
        self.session.tick()
        self.now[0] += 60
        self.session.tick()
        self.session.tick()
        self.assertEqual(len(self.backend.sent), 2)
        self.now[0] += .89
        self.session.tick()
        self.assertEqual(len(self.backend.sent), 2)
        self.now[0] += .02
        self.session.tick()
        self.assertEqual(len(self.backend.sent), 3)

    def test_slow_send_still_leaves_a_full_interval_after_completion(self):
        original = self.backend.send_space

        def slow(target, **kwargs):
            self.now[0] += 1.
            return original(target, **kwargs)

        self.backend.send_space = slow
        self.space()
        self.session.tick()
        self.session.tick()
        self.assertEqual(len(self.backend.sent), 1)

    def test_start_waits_for_display_confirmation_and_a_fresh_space_press(self):
        allowed = [False]
        self.session = self.module.LootSession(self.backend, self.events.append,
                                               clock=lambda: self.now[0], can_start=lambda: allowed[0])
        self.session.prepare()
        self.assertFalse(self.session.key(0x20, True, 0))
        self.session.tick()
        self.assertFalse(self.backend.sent)
        allowed[0] = True
        self.assertFalse(self.session.key(0x20, True, 0), '确认时不能把已按住的空格当作新按下')
        self.session.key(0x20, False, 0)
        self.assertTrue(self.space())
        self.session.tick()
        self.assertEqual(len(self.backend.sent), 1)

    def test_send_failure_is_visible_and_never_retried_automatically(self):
        self.backend.failure = OSError('input blocked')
        self.space()
        self.assertFalse(self.session.tick())
        self.assertEqual(self.session.status.state, 'error')
        self.assertIn('input blocked', self.session.status.detail)
        self.backend.failure = None
        self.now[0] += 5
        self.session.tick()
        self.assertFalse(self.backend.sent)

    def test_preparation_expires_after_thirty_seconds(self):
        self.now[0] += 30
        self.assertFalse(self.session.tick())
        self.assertEqual(self.session.status.state, 'idle')
        self.assertFalse(self.space())

    def test_space_after_expiry_does_not_bypass_the_preparation_deadline(self):
        self.now[0] += 30
        self.assertFalse(self.space())
        self.assertFalse(self.session.tick())
        self.assertFalse(self.backend.sent)

    def test_preparing_while_space_is_already_held_waits_for_a_fresh_press(self):
        self.backend.held = True
        self.session.prepare()
        self.assertFalse(self.session.key(0x20, True, 0))
        self.assertEqual(self.session.status.state, 'ready')
        self.session.key(0x20, False, 0)
        self.assertTrue(self.space())
        self.assertEqual(self.session.status.state, 'running')

    def test_ending_while_space_held_suppresses_repeats_until_release(self):
        self.session.key(0x20, True, 0)
        self.session.cancel()
        self.assertTrue(self.session.tick())
        self.assertTrue(self.session.key(0x20, True, 0))
        self.assertTrue(self.session.key(0x20, False, 0))
        self.assertFalse(self.session.tick())
        self.assertFalse(self.backend.sent)


class LootServiceTests(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module('hots_chat.loot')
        except ImportError:
            self.fail('尚未提供按需启动的开箱服务')

    def test_default_startup_and_close_never_create_native_backend(self):
        calls = []
        helper = self.module().LootHelper(backend_factory=lambda: calls.append(1))
        self.assertFalse(helper.active)
        helper.close()
        self.assertFalse(calls)

    def test_backend_initialization_error_is_reported_without_live_thread(self):
        def blocked():
            raise OSError('hook unavailable')
        helper = self.module().LootHelper(backend_factory=blocked)
        self.addCleanup(helper.close)
        self.assertTrue(helper.prepare())
        helper.thread.join(2)
        self.assertFalse(helper.active)
        status = helper.drain_status()[-1]
        self.assertEqual(status.state, 'error')
        self.assertIn('hook unavailable', status.detail)

    def test_close_wakes_backend_and_prevents_new_preparation(self):
        started = threading.Event()
        stopped = threading.Event()

        class WaitingBackend(Backend):
            def run(self, key, mouse, tick, ready, stop_event):
                ready()
                started.set()
                stopped.wait(2)

            def stop(self):
                stopped.set()

        helper = self.module().LootHelper(backend_factory=WaitingBackend)
        self.assertTrue(helper.prepare())
        self.assertTrue(started.wait(2))
        self.assertFalse(helper.prepare(), '不能叠加两个钩子服务')
        helper.close()
        self.assertTrue(stopped.is_set())
        self.assertFalse(helper.active)
        self.assertFalse(helper.prepare())

    def test_cancel_filters_queued_active_status_before_the_ui_reads_it(self):
        started, finish = threading.Event(), threading.Event()

        class DelayedBackend(Backend):
            def run(self, key, mouse, tick, ready, stop_event):
                ready()
                started.set()
                finish.wait(2)
                key(0x20, True, 0)
                tick()
                key(0x20, False, 0)

            def stop(self):
                finish.set()

        helper = self.module().LootHelper(backend_factory=DelayedBackend)
        self.addCleanup(helper.close)
        helper.prepare()
        self.assertTrue(started.wait(2))
        helper.cancel()
        finish.set()
        helper.thread.join(2)
        self.assertFalse(helper.active)
        states = [status.state for status in helper.drain_status()]
        self.assertNotIn('ready', states, '取消后迟到的就绪状态不能重新显示浮条')
        self.assertNotIn('running', states)
        self.assertIn('idle', states)

    def test_worker_requires_the_current_ui_confirmation_before_sending(self):
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
                    key(0x20, True, 0)
                    key(0x20, False, 0)
                    tick()
                    completed.set()

            def stop(self):
                commands.put(None)

        backend = ControlledBackend()
        helper = self.module().LootHelper(backend_factory=lambda: backend)
        self.addCleanup(helper.close)
        helper.prepare()
        self.assertTrue(started.wait(2))
        ready = helper.drain_status()[-1]

        def press():
            completed.clear()
            commands.put('press')
            self.assertTrue(completed.wait(2))

        press()
        self.assertFalse(backend.sent, 'UI尚未显示就绪，worker不得开始')
        helper.confirm_ready(ready.generation - 1)
        press()
        self.assertFalse(backend.sent, '旧会话的显示确认不能解锁新会话')
        helper.confirm_ready(ready.generation)
        press()
        self.assertEqual(len(backend.sent), 1)

    def test_new_preparation_discards_unread_statuses_from_the_previous_session(self):
        class ImmediateBackend(Backend):
            def run(self, key, mouse, tick, ready, stop_event):
                ready()

            def stop(self):
                pass

        helper = self.module().LootHelper(backend_factory=ImmediateBackend)
        self.addCleanup(helper.close)
        helper.prepare()
        helper.thread.join(2)
        helper.prepare()
        helper.thread.join(2)
        statuses = helper.drain_status()
        self.assertTrue(statuses)
        self.assertEqual({status.generation for status in statuses}, {2})


class LootNativeIntegrationTests(unittest.TestCase):
    def test_cancel_during_native_foreground_query_prevents_the_send(self):
        from hots_chat import loot_native as native
        from tests.test_loot_native import FakeKernel, FakeUser
        user, kernel = FakeUser(), FakeKernel()
        backend = native.Win32LootBackend(user=user, kernel=kernel)
        cancelled = threading.Event()
        target = backend.foreground_target()
        kernel.during_query = cancelled.set
        self.assertFalse(backend.send_space(target, allowed=lambda: not cancelled.is_set()))
        self.assertFalse(user.sent)

    def test_real_adapter_and_state_machine_ignore_injected_feedback_and_release_hooks(self):
        from hots_chat import loot, loot_native as native
        from tests.test_loot_native import FakeKernel, FakeUser, Function
        self.assertEqual(loot.INPUT_MARKER, native.INPUT_MARKER)
        user, kernel = FakeUser(), FakeKernel()
        backend = native.Win32LootBackend(user=user, kernel=kernel)
        now, statuses, injected_results = [10.], [], []
        session = loot.LootSession(backend, statuses.append, clock=lambda: now[0])

        def key(vk, down, extra=0):
            event = native.KBDLLHOOKSTRUCT(vk, 0, 0, 0, extra)
            return user.hooks[13](0, 0x100 if down else 0x101, ctypes.addressof(event))

        original_send = user.SendInput

        def send(count, inputs, size):
            inserted = original_send(count, inputs, size)
            # 模拟 Windows 把 SendInput 再投递给低级键盘钩子的真实反馈路径。
            for i in range(count):
                event = inputs[i].ki
                injected_results.append(key(event.wVk, not bool(event.dwFlags & 2), event.dwExtraInfo))
            return inserted

        user.SendInput = Function(send)

        def press():
            self.assertEqual(key(0x20, True), 1)
            self.assertEqual(key(0x20, False), 1)

        def advance():
            now[0] += 1.

        def other_input():
            self.assertEqual(key(ord('A'), True), 57)

        user.messages = [press, (0x113, user.timer), advance, (0x113, user.timer),
                         press, advance, (0x113, user.timer), other_input, (0x113, user.timer)]
        backend.run(session.key, session.mouse, session.tick, session.prepare, threading.Event())
        self.assertEqual(len(user.sent), 2)
        self.assertEqual(injected_results, [57] * 4, '自己的空格事件必须穿过钩子而不切换状态')
        self.assertEqual([status.state for status in statuses], ['ready', 'running', 'paused', 'idle'])
        self.assertEqual(len(user.unhooked), 2)
        self.assertEqual(user.killed, [user.timer])
        self.assertEqual(len(kernel.opened), len(kernel.closed))
