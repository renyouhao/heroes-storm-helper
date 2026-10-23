"""团队播报的可核验发送阶段；只由用户双击产生的显式动作调用。"""
from dataclasses import dataclass
import time
import unicodedata

from .loot_native import GameTarget


@dataclass(frozen=True)
class ChatInputState:
    pid: int
    hwnd: int
    match_token: str
    chat_active: bool | None
    menu_active: bool | None
    channel: str | None
    text: str | None


class TeamChatSender:
    def __init__(self, backend, *, inspect, clock=time.monotonic, sleep=time.sleep):
        self.backend, self.inspect = backend, inspect
        self.clock, self.sleep = clock, sleep

    def send(self, action, *, allowed=lambda:True):
        """打开团队框→核对空白→输入→核对原文→提交；任何未知或部分失败不重试。

        不使用剪贴板、不清空用户输入。失败时可能留下助手自己未提交的文本，
        保留让用户查看或按Esc取消；不以额外Esc影响用户新打开的界面。
        """
        text = getattr(action, 'message', None)
        if (getattr(action, 'channel', None) != 'team' or not isinstance(text, str)
                or not text.strip() or len(text) > 240 or text.lstrip().startswith(('/', '／'))
                or any(unicodedata.category(c).startswith('C') or c in '\r\n' for c in text)):
            return 'failed'
        target = GameTarget(action.hwnd, action.pid)

        def gate():
            return bool(allowed() and self.backend.foreground_target() == target and allowed())

        def state():
            if not gate():return None
            value = self.inspect()
            if (not isinstance(value, ChatInputState)
                    or (value.pid, value.hwnd, value.match_token)
                    != (action.pid, action.hwnd, action.match_token)
                    or type(value.chat_active) is not bool or value.menu_active is not False
                    or not gate()):
                return None
            return value

        def wait_for(predicate):
            deadline = self.clock() + .4
            for _ in range(41):
                current = state()
                if current is None:return None
                if predicate(current):return current
                if self.clock() >= deadline:return None
                self.sleep(.01)
            return None

        try:
            initial = state()
            if initial is None or initial.chat_active:return 'failed'
            if not self.backend.open_team_chat(target, allowed=gate):return 'failed'
            opened = wait_for(lambda current:current.chat_active is True
                              and current.channel == 'team' and current.text == '')
            if opened is None:return 'failed'
            if not self.backend.type_unicode(text, target, allowed=gate):return 'failed'
            typed = wait_for(lambda current:current.chat_active is True
                             and current.channel == 'team' and current.text == text)
            if typed is None:return 'failed'
            # 回读可能跨线程取消、用户开始输入或换局；提交前重新确认完整原文。
            current = state()
            if (current is None or current.chat_active is not True
                    or current.channel != 'team' or current.text != text):return 'failed'
            if not self.backend.submit_chat(target, allowed=gate):return 'failed'
            return 'sent' if wait_for(lambda current:current.chat_active is False) else 'failed'
        except (OSError, ValueError, RuntimeError, TypeError, AttributeError):
            return 'failed'
