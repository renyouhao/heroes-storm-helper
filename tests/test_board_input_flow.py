"""原生鼠标回调到播报会话的集成边界；全部 Win32 API、读取和发送均为替身。"""
import ctypes
import queue
import threading
from types import SimpleNamespace
import unittest

from hots_chat.board_native import Win32BoardBackend
from hots_chat.board_service import BoardHelper
from hots_chat.loot_native import INPUT_MARKER, KBDLLHOOKSTRUCT, MSLLHOOKSTRUCT
from hots_chat.talent_broadcast import StatHitSnapshot
from tests.test_loot_native import FakeKernel, FakeUser, Function


class MessagePumpUser(FakeUser):
    """通过队列在真正的 hook 线程递送事件，退出消息也使用同一队列。"""

    def __init__(self):
        super().__init__()
        self.pending = queue.Queue()
        self.GetMessageW = Function(self.get_message)
        self.PostThreadMessageW = Function(self.post_message)
        self.GetDoubleClickTime = Function(lambda: 500)
        self.GetSystemMetrics = Function(lambda index: 6)
        self.GetClientRect = Function(lambda *args: 1)
        self.ClientToScreen = Function(lambda *args: 1)
        self.IsIconic = Function(lambda hwnd: 0)

    def get_message(self, pointer, hwnd, low, high):
        while True:
            item = self.pending.get()
            if callable(item):
                item()
                continue
            pointer._obj.message, pointer._obj.wParam = item
            return 0 if item[0] == 0x12 else 1

    def post_message(self, thread, message, wparam, lparam):
        self.posted.append((thread, message))
        self.pending.put((message, wparam))
        return 1


class ControlledClock:
    """事件间隔由测试显式推进，不依赖线程调度速度判断双击。"""

    def __init__(self):
        self.value = 100.
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.value

    def advance(self):
        with self.lock:
            self.value += .05


class SnapshotReader:
    def __init__(self):
        self.lock = threading.Lock()
        self.status, self.match_token = 'available', 'match-A'
        self.panel_visible = True
        self.reads = []
        self.closed = False

    def change_match(self, *, status, match_token):
        with self.lock:
            self.status, self.match_token = status, match_token

    def poll(self, pid, hwnd, now):
        with self.lock:
            self.reads.append((threading.current_thread().name, self.status,
                               self.match_token, self.panel_visible))
            hits = ()
            if self.status == 'available':
                hits = (StatHitSnapshot(
                    pid=pid, hwnd=hwnd, match_token=self.match_token, player_slot=2,
                    team_relation='ally', metric_id='hero_damage', value=12345,
                    rect=(100, 100, 150, 150), observed_at=now, valid_until=now+1,
                    panel_visible=self.panel_visible, mapping_trusted=True,
                    player_name='玩家', hero_name='安度因'),)
            return SimpleNamespace(pid=pid, hwnd=hwnd, status=self.status,
                                   match_token=self.match_token, hits=hits,
                                   panel_visible=self.panel_visible,
                                   chat_active=False, menu_active=False)

    def close(self):
        self.closed = True


class StateRecorder:
    def __init__(self):
        self.states = []
        self.condition = threading.Condition()

    def record(self, event, **fields):
        with self.condition:
            self.states.append(fields['outcome'])
            self.condition.notify_all()

    def position(self):
        with self.condition:
            return len(self.states)

    def wait(self, state, after):
        with self.condition:
            return self.condition.wait_for(lambda: state in self.states[after:], timeout=2)


class BoardInputFlowTests(unittest.TestCase):
    def setUp(self):
        self.user = MessagePumpUser()
        self.backend = Win32BoardBackend(user=self.user, kernel=FakeKernel())
        self.reader, self.clock, self.recorder = SnapshotReader(), ControlledClock(), StateRecorder()
        self.sent = []

        def sender(action, *, allowed):
            if not allowed():
                return 'failed'
            self.sent.append(action)
            return 'sent'

        self.helper = BoardHelper(
            backend_factory=lambda: self.backend,
            reader_factory=lambda backend: self.reader,
            sender_factory=lambda backend, reader: sender,
            clock=self.clock, diagnostics=self.recorder)
        self.addCleanup(self.close_helper)
        self.assertTrue(self.helper.set_enabled(True))
        self.assertTrue(self.recorder.wait('idle', 0), self.recorder.states)

    def close_helper(self):
        self.helper.close()
        if self.helper.thread is not None:
            self.helper.thread.join(2)
            self.assertFalse(self.helper.thread.is_alive())
        self.assertEqual(self.user.sent, [], '集成测试不得调用原生 SendInput')
        self.assertTrue(self.reader.closed)
        self.assertEqual(self.backend._callbacks, [])

    def on_hook_thread(self, operation):
        delivered = threading.Event()
        result, errors = [], []

        def deliver():
            try:
                self.clock.advance()
                result.append(operation())
            except BaseException as error:
                errors.append(error)
            finally:
                delivered.set()

        self.user.pending.put(deliver)
        self.assertTrue(delivered.wait(2), '假消息泵未递送事件')
        if errors:
            raise errors[0]
        self.assertEqual(result, [57], '原鼠标和键盘事件必须透传')

    def mouse(self, down, *, flags=1, marker=0x12345):
        def dispatch():
            if down:
                self.user.keys.add(1)
            else:
                self.user.keys.discard(1)
            event = MSLLHOOKSTRUCT()
            event.pt.x, event.pt.y, event.flags = 120, 120, flags
            event.dwExtraInfo = marker
            return self.user.hooks[14](0, 0x201 if down else 0x202,
                                       ctypes.addressof(event))

        self.on_hook_thread(dispatch)

    def click(self, **kwargs):
        self.mouse(True, **kwargs)
        self.mouse(False, **kwargs)

    def expect_state(self, state, after):
        self.assertTrue(self.recorder.wait(state, after), self.recorder.states)

    def cancel_barrier(self):
        """键盘取消事件不读游戏；其状态发布证明前面的输入队列已处理。"""
        after = self.recorder.position()

        def dispatch():
            event = KBDLLHOOKSTRUCT(0x53, 0, 0, 0, 0)
            return self.user.hooks[13](0, 0x100, ctypes.addressof(event))

        self.on_hook_thread(dispatch)
        self.expect_state('cancelled', after)

    def test_external_injected_double_click_reaches_fake_sender_once(self):
        after = self.recorder.position()
        self.click(flags=1, marker=0x12345)
        self.click(flags=1, marker=0x12345)
        self.expect_state('sent', after)
        self.cancel_barrier()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual((self.sent[0].kind, self.sent[0].metric_id,
                          self.sent[0].match_token), ('stat', 'hero_damage', 'match-A'))
        self.assertEqual({read[0] for read in self.reader.reads}, {'board-worker'})
        self.assertEqual(self.user.sent, [])

    def assert_own_double_click_ignored(self, flags):
        self.click(flags=flags, marker=INPUT_MARKER)
        self.click(flags=flags, marker=INPUT_MARKER)
        self.cancel_barrier()
        self.assertEqual(self.reader.reads, [])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.user.sent, [])

    def test_own_marker_without_injection_flag_does_not_read_or_send(self):
        self.assert_own_double_click_ignored(flags=0)

    def test_own_marker_with_injection_flag_does_not_read_or_send(self):
        self.assert_own_double_click_ignored(flags=1)

    def assert_match_change_cancels_pending_and_requires_new_pair(self, status, match_token):
        after = self.recorder.position()
        self.click()
        self.expect_state('armed', after)
        after = self.recorder.position()
        self.mouse(True)
        self.expect_state('pending', after)
        self.assertEqual(self.sent, [])

        self.reader.change_match(status=status, match_token=match_token)
        after = self.recorder.position()
        self.mouse(False)
        self.expect_state('cancelled', after)
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.helper._session._pending)
        self.assertTrue(all(read[3] for read in self.reader.reads),
                        '本例必须在 Tab 面板始终可见时验证对局身份变化')

        self.reader.change_match(status='available', match_token='match-B')
        after = self.recorder.position()
        self.click()
        self.expect_state('armed', after)
        self.assertEqual(self.sent, [], '新局的第一次点击不能复用旧局点击')
        self.assertIsNone(self.helper._session._pending)
        after = self.recorder.position()
        self.click()
        self.expect_state('sent', after)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0].match_token, 'match-B')
        self.assertEqual(self.user.sent, [])

    def test_visible_panel_with_unknown_match_cancels_old_pending(self):
        self.assert_match_change_cancels_pending_and_requires_new_pair('unknown', '')

    def test_visible_panel_with_new_match_token_cancels_old_pending(self):
        self.assert_match_change_cancels_pending_and_requires_new_pair('available', 'match-B')


if __name__ == '__main__':
    unittest.main()
