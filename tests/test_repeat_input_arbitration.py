"""跨线程输入互斥回归：外部发送持锁时，模拟消息泵仍须响应按键与计时。"""
import queue
import threading
import unittest

from hots_chat.repeat import INPUT_MARKER, RepeatHelper
from hots_chat.board_native import Win32BoardBackend
from tests.test_direct_repeat import DirectBackend
from tests.test_loot_native import FakeKernel, FakeUser, Function


class MessagePump(DirectBackend):
    def __init__(self):
        super().__init__()
        self.ready = threading.Event()
        self.commands = queue.Queue()

    def run_listening(self, key, mouse, tick, ready, stopped):
        self.callbacks = {'key': key, 'tick': tick}
        ready()
        self.ready.set()
        while not stopped.is_set():
            operation = self.commands.get()
            if operation is None:
                return
            callback, args, complete, result = operation
            try:
                result.append(self.callbacks[callback](*args))
            finally:
                complete.set()

    def submit(self, callback, *args):
        complete, result = threading.Event(), []
        self.commands.put((callback, args, complete, result))
        return complete, result

    def stop(self):
        self.commands.put(None)


class RepeatInputArbitrationTests(unittest.TestCase):
    def setUp(self):
        self.backend = MessagePump()
        self.helper = RepeatHelper(backend_factory=lambda: self.backend)
        self.addCleanup(self.helper.close)
        self.assertTrue(self.helper.start_listening())
        self.assertTrue(self.backend.ready.wait(2))

    def dispatch(self, callback, *args):
        complete, result = self.backend.submit(callback, *args)
        self.assertTrue(complete.wait(1), '模拟消息泵被外部输出栅栏堵住')
        self.assertEqual(len(result), 1)
        return result[0]

    def test_own_input_returns_while_another_sender_holds_the_output_fence(self):
        # 测试线程模拟board-worker，回调运行在独立repeat-helper线程。
        with self.helper._dispatch:
            self.assertFalse(self.dispatch('key', 0xE7, True, INPUT_MARKER))
            self.assertFalse(self.dispatch('key', 0x0D, False, INPUT_MARKER))
        self.assertFalse(self.helper.active)
        self.assertEqual(self.backend.sent, [])

    def test_timer_does_not_stall_the_message_pump_while_output_is_owned(self):
        with self.helper._dispatch:
            self.assertTrue(self.dispatch('tick'))
            self.assertFalse(self.dispatch('key', 0xE7, True, INPUT_MARKER))
        self.assertEqual(self.backend.sent, [])

    def test_f8_pair_is_processed_but_output_waits_for_the_other_sender(self):
        with self.helper._dispatch:
            self.assertTrue(self.dispatch('key', 0x77, True, 0))
            self.assertTrue(self.dispatch('key', 0x77, False, 0))
            requested = self.helper.drain_status()[-1]
            self.assertEqual(requested.state, 'requested')
            self.assertTrue(self.helper.confirm_ready(requested.generation))
            self.assertTrue(self.dispatch('tick'))
            self.assertEqual(self.backend.sent, [])
        self.assertTrue(self.dispatch('tick'))
        self.assertEqual(len(self.backend.sent), 1)
        # 没有丢失keyup，后续完整F8可结束，不需要多按一次。
        self.assertTrue(self.dispatch('key', 0x77, True, 0))
        self.assertTrue(self.dispatch('key', 0x77, False, 0))
        self.assertFalse(self.helper.active)

    def test_escape_cancels_a_request_while_the_output_fence_is_owned(self):
        self.dispatch('key', 0x77, True, 0)
        self.dispatch('key', 0x77, False, 0)
        with self.helper._dispatch:
            self.assertFalse(self.dispatch('key', 0x1B, True, 0))
            self.assertFalse(self.helper.active)
            self.assertTrue(self.dispatch('tick'))
        self.assertTrue(self.dispatch('tick'))
        self.assertEqual(self.backend.sent, [])

    def test_starting_listener_does_not_wait_for_an_existing_sender(self):
        backend = MessagePump()
        helper = RepeatHelper(backend_factory=lambda: backend)
        self.addCleanup(helper.close)
        with helper._dispatch:
            self.assertTrue(helper.start_listening())
            self.assertTrue(backend.ready.wait(1), '监听初始化不应阻塞已安装的钩子')
        self.assertEqual(backend.sent, [])

    def test_old_cancel_waiting_for_output_cannot_cancel_a_new_f8_request(self):
        self.dispatch('key', 0x77, True, 0)
        self.dispatch('key', 0x77, False, 0)
        old = self.helper.drain_status()[-1]
        result = []
        cancel = threading.Thread(target=lambda: result.append(self.helper.cancel(old.generation)))
        with self.helper._dispatch:
            cancel.start()
            self.assertTrue(self.helper._cancel.wait(1))
            self.dispatch('key', 0x77, True, 0)
            self.dispatch('key', 0x77, False, 0)
            fresh = self.helper.drain_status()[-1]
            self.assertGreater(fresh.generation, old.generation)
        cancel.join(1)
        self.assertFalse(cancel.is_alive())
        self.assertEqual(result, [False])
        self.assertTrue(self.helper.active)
        self.assertTrue(self.helper.confirm_ready(fresh.generation))
        self.dispatch('tick')
        self.assertEqual(len(self.backend.sent), 1)

    def test_unicode_batch_returns_from_cross_thread_hook_feedback_while_output_is_owned(self):
        user = FakeUser()
        for name in ('GetDoubleClickTime', 'GetSystemMetrics', 'GetClientRect',
                     'ClientToScreen', 'IsIconic'):
            setattr(user, name, Function(lambda *args: 0))
        native = Win32BoardBackend(user=user, kernel=FakeKernel())
        original = user.SendInput.implementation
        feedback = []

        def send(count, inputs, size):
            for index in range(count):
                event = inputs[index].ki
                feedback.append(self.dispatch('key', 0xE7, not bool(event.dwFlags & 2),
                                              int(event.dwExtraInfo)))
            return original(count, inputs, size)

        user.SendInput.implementation = send
        with self.helper._dispatch:
            # 先递送timer，防止仅修marker而漏掉已堵在timer中的消息泵。
            self.assertTrue(self.dispatch('tick'))
            self.assertTrue(native.type_unicode('甲' * 40, native.foreground_target()))
        self.assertEqual(feedback, [False] * 80)
        self.assertEqual(len(user.sent), 3)
        self.assertEqual(self.backend.sent, [])


if __name__ == '__main__':
    unittest.main()
