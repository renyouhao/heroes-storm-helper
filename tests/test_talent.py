"""自动天赋纯状态测试；不读进程，不发送系统输入。"""
import importlib
import unittest
from dataclasses import replace

from hots_chat.acquisition.talent_source import TalentSnapshot, TalentTier
from hots_chat.loot_native import GameTarget


class Backend:
    def __init__(self):
        self.calls = []
        self.result = 'sent'
        self.before_send = None

    def select_favorite(self, target, *, allowed):
        if self.before_send:
            self.before_send()
        if not allowed():
            return 'deferred'
        self.calls.append(target)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def snapshot(*, generation=('process-1', 'match-1'), selected=(-1, -1),
             pending=1, status='available', pid=789):
    return TalentSnapshot(pid, generation, 'Chromie', 2,
                          tuple(TalentTier(i + 1, level, value, True)
                                for i, (level, value) in enumerate(zip((1, 2), selected))),
                          pending, status)


class TalentTests(unittest.TestCase):
    def setUp(self):
        try:
            self.talent = importlib.import_module('hots_chat.talent')
        except ImportError as error:
            self.fail('尚未提供自动天赋状态机：' + str(error))
        self.backend = Backend()
        self.session = self.talent.TalentSession(
            self.backend, config=self.talent.TalentConfig(enabled=True))
        self.target = GameTarget(123, 789)

    def poll(self, value=None, now=0., target='default', allowed=lambda: True):
        return self.session.poll(value if value is not None else snapshot(), now,
                                 self.target if target == 'default' else target,
                                 allowed=allowed)

    def test_default_off_never_reads_snapshot_or_calls_backend(self):
        class Unreadable:
            def __getattribute__(self, key):
                raise AssertionError('disabled session inspected snapshot')
        session = self.talent.TalentSession(self.backend)
        self.assertEqual(session.poll(Unreadable(), 0., self.target).state, 'disabled')
        self.assertEqual(self.backend.calls, [])

    def test_pending_sends_once_then_waits_for_selected_change(self):
        self.assertEqual(self.poll().state, 'waiting')
        self.assertEqual(self.poll(now=2.99).state, 'waiting')
        self.assertEqual(self.backend.calls, [self.target])
        result = self.poll(snapshot(selected=(4, -1), pending=2), 3.)
        self.assertEqual(result.state, 'confirmed')
        self.assertEqual(result.tier_id, 1)
        self.assertNotIn('爱心天赋已选', result.detail)
        self.assertEqual(self.backend.calls, [self.target])

    def test_timeout_blocks_same_tier_without_repeated_input(self):
        self.poll()
        result = self.poll(now=3.)
        self.assertEqual(result.state, 'blocked')
        self.assertIn('爱心', result.detail)
        self.assertIn('快捷键', result.detail)
        self.assertEqual(self.poll(now=99.).state, 'blocked')
        self.assertEqual(self.backend.calls, [self.target])

    def test_confirmation_after_timeout_is_still_observed(self):
        self.poll()
        self.poll(now=3.)
        self.assertEqual(self.poll(snapshot(selected=(4, -1), pending=2), 9.).state,
                         'confirmed')

    def test_unknown_and_short_tier_read_do_not_clear_attempt(self):
        self.poll()
        self.assertEqual(self.poll(TalentSnapshot(789), 5.).state, 'unknown')
        short = replace(snapshot(), tiers=())
        self.assertEqual(self.poll(short, 6.).state, 'unknown')
        self.assertEqual(self.poll(now=7.).state, 'blocked')
        self.assertEqual(self.backend.calls, [self.target])

    def test_unknown_generation_cannot_clear_attempt(self):
        self.poll()
        unknown = replace(snapshot(generation=('process-2', 'match-2')), status='unknown')
        self.poll(unknown, 1.)
        self.poll(now=4.)
        self.assertEqual(self.backend.calls, [self.target])

    def test_disable_enable_and_stop_keep_same_match_attempt(self):
        self.poll()
        self.session.stop()
        self.assertEqual(self.poll(now=1.).state, 'disabled')
        self.session.configure(self.talent.TalentConfig(enabled=True))
        self.assertEqual(self.poll(now=4.).state, 'blocked')
        self.assertEqual(self.backend.calls, [self.target])

    def test_new_trusted_generation_allows_once_but_old_snapshot_is_stale(self):
        first = snapshot()
        self.poll(first)
        second = snapshot(generation=('process-1', 'match-2'))
        self.assertEqual(self.poll(second, 1.).state, 'waiting')
        self.assertEqual(self.poll(first, 2.).state, 'unknown')
        self.assertEqual(self.poll(second, 3.).state, 'waiting')
        self.assertEqual(self.backend.calls, [self.target, self.target])

    def test_unknown_and_idle_do_not_accidentally_retry_already_attempted_layer(self):
        self.poll()
        self.poll(TalentSnapshot(None), 1.)
        self.poll(snapshot(pending=None, status='idle'), 2.)
        self.poll(now=4.)
        self.assertEqual(self.backend.calls, [self.target])

    def test_focus_loss_and_input_exclusion_defer_without_consuming_attempt(self):
        self.assertEqual(self.poll(target=None).state, 'deferred')
        self.assertEqual(self.poll(allowed=lambda: False).state, 'deferred')
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.poll(now=1.).state, 'waiting')
        self.assertEqual(self.backend.calls, [self.target])

    def test_snapshot_pid_must_match_foreground_target(self):
        self.assertEqual(self.poll(snapshot(pid=555)).state, 'deferred')
        self.assertEqual(self.backend.calls, [])

    def test_other_process_snapshot_cannot_retire_current_match_and_clear_attempt(self):
        current = snapshot()
        self.poll(current)
        other = snapshot(pid=555, generation=('process-2', 'match-2'))
        self.assertEqual(self.poll(other, 1.).state, 'deferred')
        self.assertEqual(self.poll(current, 4.).state, 'blocked')
        self.assertEqual(self.backend.calls, [self.target])

    def test_native_deferred_keeps_pending_for_later_poll(self):
        self.backend.result = 'deferred'
        self.assertEqual(self.poll().state, 'deferred')
        self.backend.result = 'sent'
        self.assertEqual(self.poll(now=1.).state, 'waiting')
        self.assertEqual(len(self.backend.calls), 2)

    def test_stop_during_send_gate_prevents_input(self):
        self.backend.before_send = self.session.stop
        self.assertEqual(self.poll().state, 'disabled')
        self.assertEqual(self.backend.calls, [])

    def test_configuration_change_invalidates_inflight_send_gate(self):
        def toggle():
            self.session.configure(self.talent.TalentConfig(enabled=False))
            self.session.configure(self.talent.TalentConfig(enabled=True))
        self.backend.before_send = toggle
        self.assertEqual(self.poll().state, 'deferred')
        self.assertEqual(self.backend.calls, [])

    def test_already_manually_selected_pending_tier_does_not_send(self):
        self.assertEqual(self.poll(snapshot(selected=(0, -1), pending=1)).state, 'idle')
        self.assertEqual(self.backend.calls, [])

    def test_multiple_pending_tiers_advance_only_after_previous_confirmation(self):
        self.poll()
        self.assertEqual(self.poll(snapshot(pending=2), 1.).state, 'waiting')
        self.assertEqual(self.backend.calls, [self.target])
        self.assertEqual(self.poll(snapshot(selected=(8, -1), pending=2), 2.).state,
                         'confirmed')
        self.assertEqual(self.poll(snapshot(selected=(8, -1), pending=2), 2.1).state,
                         'waiting')
        self.assertEqual(self.backend.calls, [self.target, self.target])

    def test_partial_input_failure_never_retries_this_tier(self):
        for failure in ('failed', OSError('partial send')):
            with self.subTest(failure=failure):
                self.setUp()
                self.backend.result = failure
                self.assertEqual(self.poll().state, 'error')
                self.poll(now=99.)
                self.assertEqual(self.backend.calls, [self.target])

    def test_confirmation_can_be_read_while_foreground_is_lost(self):
        self.poll()
        result = self.poll(snapshot(selected=(0, -1), pending=2), 1., target=None)
        self.assertEqual(result.state, 'confirmed')

    def test_invalid_config_rejected(self):
        for kwargs in ({'enabled': 1}, {'confirm_timeout': 0},
                       {'confirm_timeout': float('nan')}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.talent.TalentConfig(**kwargs)


if __name__ == '__main__':
    unittest.main()
