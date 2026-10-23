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

    def scripted_inspection(self, phase, snapshots):
        values = iter(snapshots)
        fallback = self.sender.inspect

        def inspect():
            if phase():
                try:
                    changes = next(values)
                except StopIteration:
                    return fallback()
                return replace(self.state, **changes)
            return fallback()

        self.sender.inspect = inspect

    def test_transient_input_flags_after_typing_wait_without_retyping_or_resubmitting(self):
        self.scripted_inspection(lambda: len(self.calls) == 2,
                                 [{'chat_active': None, 'menu_active': None}, {}])
        self.assertEqual(self.sender.send(self.action), 'sent')
        self.assertEqual(self.calls, ['open', ('text', self.action.message), 'submit'])
        self.assertGreaterEqual(self.clock_value, .01)

    def test_unknown_last_check_before_submit_is_reobserved(self):
        self.scripted_inspection(lambda: len(self.calls) == 2,
                                 [{}, {'chat_active': None}, {}])
        self.assertEqual(self.sender.send(self.action), 'sent')
        self.assertEqual(self.calls.count('submit'), 1)

    def test_unknown_after_submit_only_rechecks_and_never_sends_another_enter(self):
        self.scripted_inspection(lambda: len(self.calls) == 3,
                                 [{'chat_active': None, 'menu_active': None}, {}])
        self.assertEqual(self.sender.send(self.action), 'sent')
        self.assertEqual(self.calls.count('submit'), 1)

    def test_permanent_unknown_has_a_bounded_failure_and_keeps_the_draft(self):
        fallback = self.sender.inspect
        self.sender.inspect = lambda: (replace(self.state, chat_active=None)
                                       if len(self.calls) == 2 else fallback())
        self.assertEqual(self.sender.send(self.action), 'failed')
        self.assertNotIn('submit', self.calls)
        self.assertLessEqual(self.clock_value, .41)
        self.assertEqual(self.state.text, self.action.message)

    def test_confirmed_context_change_during_unknown_reobservation_stops_immediately(self):
        self.scripted_inspection(lambda: len(self.calls) == 2,
                                 [{'chat_active': None}, {'match_token': 'new'}, {}])
        self.assertEqual(self.sender.send(self.action), 'failed')
        self.assertNotIn('submit', self.calls)
        self.assertLessEqual(self.clock_value, .01)

    def test_read_finishing_after_the_deadline_cannot_authorize_submit(self):
        fallback = self.sender.inspect

        def slow():
            if len(self.calls) == 2:
                self.clock_value += .5
            return fallback()

        self.sender.inspect = slow
        self.assertEqual(self.sender.send(self.action), 'failed')
        self.assertNotIn('submit', self.calls)

    def test_detailed_failure_explains_a_leftover_chat_box(self):
        self.state = replace(self.state, chat_active=True, channel='team', text='保留的草稿')
        result = self.sender.send_result(self.action)
        self.assertEqual(result.state, 'failed')
        self.assertEqual(result.code, 'chat_open')
        self.assertIn('Esc', result.detail)
        self.assertEqual(self.calls, [])

    def test_post_submit_unknown_reports_ambiguity_instead_of_suggesting_a_retry(self):
        fallback = self.sender.inspect
        self.sender.inspect = lambda: (replace(self.state, chat_active=None)
                                       if len(self.calls) == 3 else fallback())
        result = self.sender.send_result(self.action)
        self.assertEqual(result.state, 'failed')
        self.assertEqual(result.code, 'send_unconfirmed')
        self.assertIn('可能已经发送', result.detail)
        self.assertEqual(self.calls.count('submit'), 1)

    def test_failure_diagnostics_record_phase_and_code_without_body(self):
        events = []

        class Recorder:
            def record(self, event, **fields):
                events.append((event, fields))

        self.action.message = 'PRIVATE MESSAGE'
        self.sender.diagnostics = Recorder()
        self.state = replace(self.state, chat_active=True, channel='team', text='PRIVATE DRAFT')
        self.sender.send_result(self.action)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], 'hotkey.state')
        self.assertEqual(events[0][1]['stage'], 'broadcast.initial')
        self.assertEqual(events[0][1]['error_code'], 'chat_open')
        self.assertNotIn('PRIVATE', str(events))

    def test_cancelling_during_transient_read_stops_without_a_submit(self):
        permit = [True]
        fallback = self.sender.inspect
        self.sender.inspect = lambda: (replace(self.state, chat_active=None)
                                       if len(self.calls) == 2 else fallback())
        sleep = self.sender.sleep

        def cancel(seconds):
            sleep(seconds)
            permit[0] = False

        self.sender.sleep = cancel
        result = self.sender.send_result(self.action, allowed=lambda: permit[0])
        self.assertEqual(result.code, 'input_cancelled')
        self.assertNotIn('submit', self.calls)
        self.assertEqual(self.clock_value, .01)

    def test_known_menu_or_wrong_channel_after_unknown_is_not_waited_away(self):
        for changes, code in (({'menu_active': True}, 'menu_open'),
                              ({'channel': 'whisper'}, 'wrong_channel')):
            with self.subTest(code=code):
                self.setUp()
                self.scripted_inspection(lambda: len(self.calls) == 2,
                                         [{'chat_active': None}, changes, {}])
                result = self.sender.send_result(self.action)
                self.assertEqual(result.code, code)
                self.assertNotIn('submit', self.calls)
                self.assertEqual(self.clock_value, .01)

    def test_partial_submit_is_uncertain_and_never_repeats_enter(self):
        submit = self.backend.submit_chat

        def partial(target, *, allowed):
            submit(target, allowed=allowed)
            return False

        self.backend.submit_chat = partial
        result = self.sender.send_result(self.action)
        self.assertEqual(result.code, 'send_unconfirmed')
        self.assertIn('可能已经发送', result.detail)
        self.assertEqual(self.calls.count('submit'), 1)

    def test_stable_success_adds_no_polling_sleep(self):
        self.assertEqual(self.sender.send(self.action), 'sent')
        self.assertEqual(self.clock_value, 0)


if __name__ == '__main__':unittest.main()
