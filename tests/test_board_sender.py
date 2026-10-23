"""团队消息发送状态机：只使用内存替身，不发送真实输入。"""
from dataclasses import replace
import importlib
from types import SimpleNamespace
import unittest

from hots_chat.loot_native import GameTarget


class BoardSenderTests(unittest.TestCase):
    def setUp(self):
        module = importlib.import_module('hots_chat.board_sender')
        self.module = module
        self.target = GameTarget(123, 456)
        self.action = SimpleNamespace(pid=456, hwnd=123, match_token='match', message='【我方】玩家｜死亡：0 次', channel='team')
        self.state = module.ChatInputState(456, 123, 'match', False, False, None, None)
        self.calls = []
        outer = self
        class Backend:
            def foreground_target(self):return outer.target
            def open_team_chat(self, target, *, allowed):
                outer.calls.append('open')
                outer.state = replace(outer.state, chat_active=True, channel='team', text='')
                return allowed()
            def type_unicode(self, text, target, *, allowed):
                outer.calls.append(('text', text))
                outer.state = replace(outer.state, text=text)
                return allowed()
            def submit_chat(self, target, *, allowed):
                outer.calls.append('submit')
                outer.state = replace(outer.state, chat_active=False, text='')
                return allowed()
        self.backend = Backend()
        self.clock_value = 0.
        def sleep(seconds):self.clock_value += seconds
        self.sender = module.TeamChatSender(self.backend, inspect=lambda:self.state,
                                            clock=lambda:self.clock_value, sleep=sleep)

    def test_only_confirmed_team_empty_editor_can_receive_unicode_and_submit(self):
        self.assertEqual(self.sender.send(self.action), 'sent')
        self.assertEqual(self.calls, ['open', ('text', self.action.message), 'submit'])

    def test_unknown_or_busy_initial_input_never_opens_chat(self):
        for changes in ({'chat_active':None}, {'menu_active':None}, {'chat_active':True},
                        {'menu_active':True}, {'match_token':'other'}):
            with self.subTest(changes=changes):
                original = self.state
                self.state = replace(self.state, **changes)
                self.assertEqual(self.sender.send(self.action), 'failed')
                self.assertEqual(self.calls, [])
                self.state = original

    def test_wrong_channel_or_existing_text_is_never_overwritten(self):
        for changes in ({'channel':'whisper'}, {'text':'用户尚未发送的消息'}, {'text':None}):
            with self.subTest(changes=changes):
                self.setUp()
                opener = self.backend.open_team_chat
                def open_wrong(target, *, allowed):
                    result = opener(target, allowed=allowed)
                    self.state = replace(self.state, **changes)
                    return result
                self.backend.open_team_chat = open_wrong
                self.assertEqual(self.sender.send(self.action), 'failed')
                self.assertEqual(self.calls, ['open'])

    def test_user_text_or_focus_change_before_submit_cancels(self):
        for change in ('text', 'focus', 'generation'):
            with self.subTest(change=change):
                self.setUp()
                writer = self.backend.type_unicode
                def write_then_change(text, target, *, allowed):
                    result = writer(text, target, allowed=allowed)
                    if change == 'text':self.state = replace(self.state, text=text+'x')
                    elif change == 'focus':self.target = None
                    else:self.state = replace(self.state, match_token='new')
                    return result
                self.backend.type_unicode = write_then_change
                self.assertEqual(self.sender.send(self.action), 'failed')
                self.assertNotIn('submit', self.calls)

    def test_cancel_during_open_or_unconfirmed_input_does_not_retry(self):
        permit = [True]
        opener = self.backend.open_team_chat
        def cancel_after_open(target, *, allowed):
            result = opener(target, allowed=allowed);permit[0]=False;return result
        self.backend.open_team_chat = cancel_after_open
        self.assertEqual(self.sender.send(self.action, allowed=lambda:permit[0]), 'failed')
        self.assertEqual(self.calls, ['open'])

    def test_slash_commands_newlines_and_non_team_actions_are_rejected(self):
        for message, channel in (('/w someone text','team'), ('a\nb','team'), ('okay','whisper'), ('x'*241,'team')):
            with self.subTest(message=message):
                self.action.message, self.action.channel = message, channel
                self.assertEqual(self.sender.send(self.action), 'failed')
        self.assertEqual(self.calls, [])


if __name__ == '__main__':unittest.main()
