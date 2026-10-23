"""团队播报的可核验发送阶段；只由用户双击产生的显式动作调用。"""
from dataclasses import dataclass
import time
import unicodedata

from .loot_native import GameTarget
from .talent_broadcast import BroadcastSendResult


@dataclass(frozen=True)
class ChatInputState:
    pid: int
    hwnd: int
    match_token: str
    chat_active: bool | None
    menu_active: bool | None
    channel: str | None
    text: str | None


class _InputStopped(Exception):
    def __init__(self, code, detail):
        self.code, self.detail = code, detail


class TeamChatSender:
    def __init__(self, backend, *, inspect, clock=time.monotonic, sleep=time.sleep, diagnostics=None):
        self.backend, self.inspect = backend, inspect
        self.clock, self.sleep = clock, sleep
        self.diagnostics = diagnostics

    def send(self, action, *, allowed=lambda:True):
        """兼容只需要sent/failed的调用者。"""
        return self.send_result(action, allowed=allowed).state

    def send_result(self, action, *, allowed=lambda:True):
        """未知观察可有界重读；真实输入各执行一次，任何部分失败不重发。

        不使用剪贴板、不清空用户输入。失败时可能留下助手自己未提交的文本，
        保留让用户查看或按Esc取消；不以额外Esc影响用户新打开的界面。
        """
        text = getattr(action, 'message', None)
        phase, submitted, reads = 'initial', False, 0
        started = self.clock()

        def finish(state, code, detail, *, error_code=None):
            if self.diagnostics is not None:
                try:
                    self.diagnostics.record('hotkey.state', stage='broadcast.'+phase, outcome=state,
                        error_code=error_code or code, source=getattr(action, 'kind', 'unknown'),
                        characters=len(text) if isinstance(text, str) else 0,
                        count=reads, elapsed_ms=round((self.clock()-started)*1000, 3))
                except Exception:
                    pass
            return BroadcastSendResult(state=state, code=code, detail=detail)

        def failed(code, detail):
            if submitted:
                return finish('failed', 'send_unconfirmed',
                    '发送按键已尝试，消息可能已经发送；请先检查聊天记录，不会自动补发。', error_code=code)
            return finish('failed', code, detail)

        if (getattr(action, 'channel', None) != 'team' or not isinstance(text, str)
                or not text.strip() or len(text) > 240 or text.lstrip().startswith(('/', '／'))
                or any(unicodedata.category(c).startswith('C') or c in '\r\n' for c in text)):
            return failed('invalid_action', '播报内容或频道无效，本次未发送。')
        target = GameTarget(action.hwnd, action.pid)

        def gate():
            return bool(allowed() and self.backend.foreground_target() == target and allowed())

        def state():
            nonlocal reads
            if not gate():
                raise _InputStopped('input_cancelled',
                    '前台、按键、鼠标或开关状态已变化，本次停止；若有草稿，请按 Esc 关闭。')
            reads += 1
            value = self.inspect()
            if (not isinstance(value, ChatInputState)
                    or (value.pid, value.hwnd, value.match_token)
                    != (action.pid, action.hwnd, action.match_token)):
                raise _InputStopped('identity_changed', '未确认同一对局，已停止本次播报；若有草稿，请按 Esc 关闭。')
            if not gate():
                raise _InputStopped('input_cancelled',
                    '检测到新的操作，本次停止；若有草稿，请按 Esc 关闭后重试。')
            if value.menu_active is True:
                raise _InputStopped('menu_open', '游戏菜单已打开，本次播报停止；关闭菜单后重新双击。')
            if (value.chat_active is not None and type(value.chat_active) is not bool
                    or value.menu_active is not None and type(value.menu_active) is not bool):
                raise _InputStopped('invalid_input_state', '输入状态无效，本次已停止。')
            if value.chat_active is True and value.channel not in (None, 'team'):
                raise _InputStopped('wrong_channel', '聊天框不是团队频道，本次停止，不覆盖现有草稿。')
            return value

        def wait_for(predicate, *, require_closed=False):
            deadline = self.clock() + .4
            for _ in range(41):
                current = state()
                now = self.clock()
                if now > deadline:return None
                if require_closed and current.chat_active is True:
                    raise _InputStopped('chat_open', '游戏聊天框仍打开，请先按 Esc 关闭，再重新双击。')
                if (type(current.chat_active) is bool and current.menu_active is False
                        and predicate(current)):
                    return current
                if now >= deadline:return None
                self.sleep(.01)
            return None

        try:
            initial = wait_for(lambda current:current.chat_active is False, require_closed=True)
            if initial is None:
                return failed('initial_unconfirmed', '暂未确认输入状态，本次未发送，请稍后重新双击。')
            phase = 'open'
            if not self.backend.open_team_chat(target, allowed=gate):
                return failed('open_failed', '未能完整打开团队聊天框；请松开按键并检查聊天框后重试。')
            phase = 'empty_editor'
            opened = wait_for(lambda current:current.chat_active is True
                              and current.channel == 'team' and current.text == '')
            if opened is None:
                return failed('empty_editor_unconfirmed', '未确认空白团队聊天框，未输入正文；请检查频道或按 Esc 后重试。')
            phase = 'type'
            if not self.backend.type_unicode(text, target, allowed=gate):
                return failed('type_failed', '正文输入未完整完成，已停止；请按 Esc 取消草稿后重试。')
            phase = 'verify_draft'
            typed = wait_for(lambda current:current.chat_active is True
                             and current.channel == 'team' and current.text == text)
            if typed is None:
                return failed('draft_unconfirmed', '未确认完整正文，未提交；请检查草稿或按 Esc 关闭后重试。')
            # 回读可能跨线程取消、用户开始输入或换局；提交前重新确认完整原文。
            phase = 'before_submit'
            current = wait_for(lambda current:current.chat_active is True
                               and current.channel == 'team' and current.text == text)
            if current is None:
                return failed('before_submit_unconfirmed', '提交前未确认原文，未提交；请按 Esc 关闭草稿后重试。')
            phase, submitted = 'submit', True
            if not self.backend.submit_chat(target, allowed=gate):
                return failed('submit_failed', '')
            phase = 'confirm'
            if wait_for(lambda current:current.chat_active is False) is None:
                return failed('confirm_timeout', '')
            return finish('sent', 'sent', '已发送到己方团队。')
        except _InputStopped as error:
            return failed(error.code, error.detail)
        except (OSError, ValueError, RuntimeError, TypeError, AttributeError):
            return failed('input_exception', '输入流程异常，已停止；请检查聊天框，有草稿时按 Esc 关闭。')
