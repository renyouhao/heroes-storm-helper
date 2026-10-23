"""天赋播报业务契约；可信快照为测试夹具，不代表接入游戏映射。"""
from dataclasses import replace
import importlib
import unittest


class TalentBroadcastTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module('hots_chat.talent_broadcast')
        except ImportError:
            self.fail('尚未提供隔离的天赋播报业务模块')
        self.hit = self.module.HitSnapshot(
            pid=11, hwnd=22, match_token='match-A', player_slot=3,
            team_relation='enemy', tier_level=13, talent_id='FrostArmor',
            talent_name='寒冰护甲', effect_summary='受到普攻时获得物理护甲。',
            rect=(100, 200, 160, 260), observed_at=10., valid_until=11.,
            panel_visible=True, selected=True, mapping_trusted=True,
            player_name='测试玩家', hero_name='吉安娜',
            advice='先用低伤普攻消耗护甲', advice_verified=True)
        self.context = self.module.InputContext(
            pid=11, hwnd=22, match_token='match-A', foreground=True)
        self.session = self.module.TalentBroadcastSession(
            config=self.module.TalentBroadcastConfig(enabled=True),
            double_click_ms=500, double_click_size=(4, 4))

    def click(self, now=10., *, hit=None, context=None, x=120, y=220, **kwargs):
        options = dict(hit=self.hit if hit is None else hit,
                       context=self.context if context is None else context,
                       now=now, x=x, y=y, **kwargs)
        self.assertIs(self.session.mouse(down=True, **options), False)
        self.assertIs(self.session.mouse(down=False, **options), False)

    def ready(self, *, hit=None, context=None, start=10.):
        self.click(start, hit=hit, context=context)
        self.click(start + .2, hit=hit, context=context)
        return self.session.tick(self.hit if hit is None else hit,
                                 self.context if context is None else context,
                                 start + .2)

    def sender(self, action, *, allowed):
        self.assertTrue(allowed())
        return 'sent'

    def dispatch(self, action, now=10.2, **kwargs):
        return self.session.dispatch(action, kwargs.pop('sender', self.sender),
                                     self.hit, self.context, now, **kwargs)

    def test_known_talent_formats_compact_team_callout_without_default_advice(self):
        text = self.module.format_talent_message(self.hit)
        self.assertEqual(text, '【敌方】测试玩家（吉安娜）｜13级天赋「寒冰护甲」：受到普攻时获得物理护甲。')
        self.assertNotIn('消耗', text)

    def test_ally_and_verified_advice_are_explicit_opt_in(self):
        text = self.module.format_talent_message(
            replace(self.hit, team_relation='ally'), include_advice=True)
        self.assertTrue(text.startswith('【我方】'))
        self.assertIn('；建议：先用低伤普攻消耗护甲', text)
        text = self.module.format_talent_message(
            replace(self.hit, advice_verified=False), include_advice=True)
        self.assertNotIn('消耗', text)

    def test_missing_nickname_uses_explicit_hero_alias(self):
        text = self.module.format_talent_message(replace(self.hit, player_name=''))
        self.assertTrue(text.startswith('【敌方】英雄：吉安娜｜'))

    def test_unknown_or_unselected_mapping_never_creates_text(self):
        for changes in ({'selected': False}, {'mapping_trusted': False},
                        {'talent_id': ''}, {'talent_name': '未知'},
                        {'effect_summary': ''}, {'team_relation': 'unknown'},
                        {'tier_level': 0}, {'tier_level': 101},
                        {'player_name': '', 'hero_name': ''}):
            with self.subTest(changes=changes):
                self.assertIsNone(self.module.format_talent_message(replace(self.hit, **changes)))

    def test_trusted_chromie_level_five_selection_can_be_broadcast(self):
        hit = replace(self.hit, hero_name='克罗米', tier_level=5,
                      talent_id='ChromieFixtureTalent', talent_name='测试用天赋',
                      effect_summary='测试用已核实效果。')
        self.assertEqual(self.module.format_talent_message(hit),
                         '【敌方】测试玩家（克罗米）｜5级天赋「测试用天赋」：测试用已核实效果。')
        self.assertIsNotNone(self.ready(hit=hit))

    def test_tier_level_range_is_bounded_without_overriding_trusted_hero_mapping(self):
        for level in (1, 2, 5, 8, 11, 14, 18, 100):
            with self.subTest(level=level):
                self.assertIsNotNone(self.module.format_talent_message(
                    replace(self.hit, tier_level=level)))
        for level in (-1, 0, 101, True, 1.5, '5'):
            with self.subTest(level=level):
                self.assertIsNone(self.module.format_talent_message(
                    replace(self.hit, tier_level=level)))

    def test_native_player_slots_ten_and_fifteen_can_use_trusted_row_bindings(self):
        for slot in (10, 15):
            with self.subTest(slot=slot):
                self.session.cancel()
                action = self.ready(hit=replace(self.hit, player_slot=slot))
                self.assertIsNotNone(action)
                self.assertEqual(action.player_slot, slot)
        self.session.cancel()
        self.assertIsNone(self.ready(hit=replace(self.hit, player_slot=16)))
        self.session.cancel()
        self.assertIsNone(self.ready(hit=replace(self.hit, player_slot=15, mapping_trusted=False)))

    def test_untrusted_text_loses_markup_controls_lines_and_slash_commands(self):
        text = self.module.format_talent_message(replace(
            self.hit, player_name='<c val="ff0000">玩家</c>\r\n/all\x00\u202e',
            talent_name='|cffaabbcc护甲|r',
            effect_summary='<img path="x"/>获得护甲。\n/team 其余长正文'))
        self.assertIn('玩家（吉安娜）', text)
        self.assertIn('「护甲」：获得护甲。', text)
        for forbidden in ('\r', '\n', '\x00', '\u202e', '<', '>', '|c', '|r', '/', '/team', '/all'):
            self.assertNotIn(forbidden, text)

    def test_long_fields_are_bounded_while_preserving_identity_and_effect(self):
        text = self.module.format_talent_message(replace(
            self.hit, player_name='玩' * 1000, hero_name='英' * 1000,
            talent_name='天' * 1000, effect_summary='效' * 10000,
            advice='建' * 1000), include_advice=True)
        self.assertLessEqual(len(text), 150)
        self.assertIn('13级天赋「', text)
        self.assertIn('效', text)

    def test_default_disabled_does_not_arm_or_dispatch(self):
        self.session = self.module.TalentBroadcastSession(
            double_click_ms=500, double_click_size=(4, 4))
        self.assertIsNone(self.ready())
        self.assertEqual(self.session.status.state, 'disabled')

    def test_valid_double_click_offers_one_team_action_without_sending(self):
        self.click()
        self.assertIsNone(self.session.tick(self.hit, self.context, 10.))
        self.click(10.2)
        action = self.session.tick(self.hit, self.context, 10.2)
        self.assertIsNotNone(action)
        self.assertEqual((action.pid, action.hwnd, action.match_token), (11, 22, 'match-A'))
        self.assertEqual(action.channel, 'team')
        self.assertIn('寒冰护甲', action.message)
        self.assertIsNone(self.session.tick(self.hit, self.context, 10.21))

    def test_second_mouse_down_waits_for_physical_release_before_offer(self):
        self.click(10.)
        options = dict(x=120,y=220,hit=self.hit,context=self.context,now=10.2)
        self.session.mouse(down=True, **options)
        self.assertIsNone(self.session.tick(self.hit,self.context,10.2))
        self.assertEqual(self.session.status.state, 'deferred')
        self.session.mouse(down=False, **options)
        self.assertIsNotNone(self.session.tick(self.hit,self.context,10.21))

    def test_held_second_press_expires_without_ever_offering_action(self):
        self.click(10.)
        self.session.mouse(x=120,y=220,down=True,hit=self.hit,context=self.context,now=10.2)
        self.assertIsNone(self.session.tick(self.hit,self.context,10.2))
        self.assertIsNone(self.session.tick(self.hit,self.context,11.01))
        self.assertEqual(self.session.status.state, 'cancelled')
        self.session.mouse(x=120,y=220,down=False,hit=self.hit,context=self.context,now=11.01)
        self.assertIsNone(self.session.tick(self.hit,self.context,11.01))

    def test_raw_mouse_time_prevents_slow_first_read_from_compressing_interval(self):
        self.click(10.2,event_at=10.)
        self.click(10.68,event_at=10.52)
        self.assertIsNone(self.session.tick(self.hit,self.context,10.68))

    def test_raw_mouse_time_preserves_valid_pair_despite_slower_second_read(self):
        self.click(10.01,event_at=10.)
        self.click(10.6,event_at=10.45)
        self.assertIsNotNone(self.session.tick(self.hit,self.context,10.6))

    def test_unknown_future_negative_or_stale_event_time_disarms_pending_pair(self):
        for event_at in (None,):
            self.session.cancel()
            self.click(10.,event_at=event_at)
            self.click(10.2,event_at=event_at)
            self.assertIsNotNone(self.session.tick(self.hit,self.context,10.2))
        for event_at in (10.21,9.94,-.1,float('nan'),float('inf'),True,'10.2'):
            with self.subTest(event_at=event_at):
                self.session.cancel()
                self.click(10.)
                self.click(10.2,event_at=event_at)
                self.assertIsNone(self.session.tick(self.hit,self.context,10.2))

    def test_event_time_does_not_extend_snapshot_freshness_or_pending_deadline(self):
        stale = replace(self.hit,valid_until=10.55)
        self.click(10.2,hit=stale,event_at=10.)
        self.click(10.6,hit=stale,event_at=10.4)
        self.assertIsNone(self.session.tick(stale,self.context,10.6))

    def test_double_click_requires_windows_interval_and_movement_rectangle(self):
        for second, x, y in ((10.501, 120, 220), (10.2, 123, 220),
                             (10.2, 120, 223), (9.9, 120, 220)):
            with self.subTest(second=second, x=x, y=y):
                self.session.cancel()
                self.click(10.)
                self.click(second, x=x, y=y)
                self.assertIsNone(self.session.tick(self.hit, self.context, 10.6))

    def test_double_click_must_hit_same_player_talent_and_actual_rectangle(self):
        for changes in ({'player_slot': 4}, {'talent_id': 'Other'},
                        {'tier_level': 16}, {'match_token': 'match-B'},
                        {'rect': (101, 200, 161, 260)}):
            with self.subTest(changes=changes):
                self.session.cancel()
                self.click()
                other = replace(self.hit, **changes)
                context = replace(self.context, match_token=other.match_token)
                self.click(10.2, hit=other, context=context)
                self.assertIsNone(self.session.tick(other, context, 10.2))
        self.session.cancel()
        self.click(x=160)
        self.click(10.2, x=160)
        self.assertIsNone(self.session.tick(self.hit, self.context, 10.2))

    def test_held_or_injected_mouse_down_is_not_a_second_click(self):
        arguments = dict(x=120, y=220, hit=self.hit, context=self.context)
        self.session.mouse(down=True, now=10., **arguments)
        self.session.mouse(down=False, injected=True, now=10.1, **arguments)
        self.session.mouse(down=True, now=10.2, **arguments)
        self.assertIsNone(self.session.tick(self.hit, self.context, 10.2))

    def test_mouse_original_events_and_other_buttons_are_never_swallowed(self):
        for button in ('left', 'right', 'middle'):
            self.click(button=button)
        self.assertIsNone(self.session.tick(self.hit, self.context, 10.))

    def test_invalid_stale_future_or_non_game_hits_fail_closed(self):
        for changes in ({'panel_visible': False}, {'selected': False},
                        {'mapping_trusted': False}, {'observed_at': 10.5},
                        {'valid_until': 9.9}, {'pid': 0}, {'hwnd': 0},
                        {'match_token': ''}, {'player_slot': -1},
                        {'rect': (100, 200, 100, 260)}, {'rect': (True, 1, 4, 8)},
                        {'observed_at': float('nan')}, {'valid_until': float('inf')}):
            with self.subTest(changes=changes):
                self.session.cancel()
                hit = replace(self.hit, **changes)
                self.assertIsNone(self.ready(hit=hit))

    def test_untrusted_or_aged_hit_cannot_reach_sender_even_with_long_expiry(self):
        hit = replace(self.hit, observed_at=8., valid_until=1000.)
        self.assertIsNone(self.ready(hit=hit))

    def test_focus_chat_menu_and_changed_game_cancel_pending_action(self):
        for changes in ({'foreground': False}, {'chat_active': True},
                        {'menu_active': True}, {'pid': 12}, {'hwnd': 23},
                        {'match_token': 'match-B'}):
            with self.subTest(changes=changes):
                self.session.cancel()
                self.ready()
                context = replace(self.context, **changes)
                self.assertIsNone(self.session.tick(self.hit, context, 10.3))
                self.assertIsNone(self.session.tick(self.hit, self.context, 10.4))

    def test_each_input_owner_defers_until_idle_and_never_past_expiry(self):
        for field in ('loot_busy', 'repeat_busy', 'quick_input_busy'):
            with self.subTest(field=field):
                self.session.cancel()
                busy = replace(self.context, **{field: True})
                self.assertIsNone(self.ready(context=busy))
                self.assertEqual(self.session.status.state, 'deferred')
                self.assertIsNotNone(self.session.tick(self.hit, self.context, 10.3))
                self.session.cancel()
                self.ready(context=busy)
                self.assertIsNone(self.session.tick(self.hit, self.context, 11.01))

    def test_dispatch_revalidates_current_hit_context_and_final_allowed_gate(self):
        for changes in ({'selected': False}, {'panel_visible': False},
                        {'talent_id': 'Changed'}):
            with self.subTest(changes=changes):
                action = self.ready()
                def must_not_send(*args, **kwargs):
                    self.fail('过期目标不应进入发送边界')
                status = self.session.dispatch(action, must_not_send,
                                               replace(self.hit, **changes), self.context, 10.2)
                self.assertEqual(status.state, 'cancelled')
        action = self.ready()
        status = self.dispatch(action, allowed=lambda: False)
        self.assertEqual(status.state, 'cancelled')

    def test_started_action_is_consumed_even_if_sender_fails_or_returns_deferred(self):
        for outcome in ('failed', 'deferred', 'exception'):
            with self.subTest(outcome=outcome):
                self.session = self.module.TalentBroadcastSession(
                    config=self.module.TalentBroadcastConfig(enabled=True),
                    double_click_ms=500, double_click_size=(4, 4))
                action = self.ready()
                attempts = []
                def send(request, *, allowed):
                    attempts.append(request)
                    self.assertIsNone(self.session.tick(self.hit, self.context, 10.2))
                    if outcome == 'exception':
                        raise OSError('partial send')
                    return outcome
                self.assertEqual(self.dispatch(action, sender=send).state, 'error')
                self.dispatch(action, sender=send)
                self.assertEqual(len(attempts), 1)
                self.assertIsNone(self.ready(start=10.4))

    def test_sender_gate_is_invalidated_by_disable_or_cancel_during_send(self):
        for operation in ('cancel', 'stop'):
            with self.subTest(operation=operation):
                self.session = self.module.TalentBroadcastSession(
                    config=self.module.TalentBroadcastConfig(enabled=True),
                    double_click_ms=500, double_click_size=(4, 4))
                action = self.ready()
                gates = []
                def send(request, *, allowed):
                    gates.append(allowed())
                    getattr(self.session, operation)()
                    gates.append(allowed())
                    return 'failed'
                self.dispatch(action, sender=send)
                self.assertEqual(gates, [True, False])

    def test_focus_loss_observed_between_clicks_disarms_first_click(self):
        self.click()
        self.session.tick(self.hit, replace(self.context, foreground=False), 10.1)
        self.click(10.2)
        self.assertIsNone(self.session.tick(self.hit, self.context, 10.2))

    def test_new_physical_click_cancels_already_offered_action(self):
        action = self.ready()
        self.click(10.3, hit=replace(self.hit, player_slot=4))
        calls = []
        def send(request, *, allowed):
            calls.append(request)
            return 'sent'
        self.dispatch(action, sender=send, now=10.3)
        self.assertEqual(calls, [])

    def test_unknown_context_flags_and_boolean_process_ids_are_unsafe(self):
        for changes in ({'chat_active': None}, {'menu_active': None},
                        {'loot_busy': None}, {'repeat_busy': None},
                        {'quick_input_busy': None}, {'pid': True}, {'hwnd': True}):
            with self.subTest(changes=changes):
                self.session.cancel()
                context = replace(self.context, **changes)
                self.assertIsNone(self.ready(context=context))

    def test_context_numeric_ids_must_be_real_integers_even_when_equal(self):
        for changes in ({'pid':11.0}, {'hwnd':22.0}):
            with self.subTest(changes=changes):
                self.session.cancel()
                self.assertIsNone(self.ready(context=replace(self.context,**changes)))

    def test_reenable_after_unobserved_release_does_not_retain_old_mouse_hold(self):
        self.session.mouse(x=120,y=220,down=True,hit=self.hit,context=self.context,now=10.)
        # The hook is removed while disabled, so the physical release is not observed.
        self.session.stop()
        self.session.configure(self.module.TalentBroadcastConfig(enabled=True))
        self.assertIsNotNone(self.ready())

    def test_duplicate_player_talent_is_blocked_for_three_seconds(self):
        action = self.ready()
        self.assertEqual(self.dispatch(action).state, 'sent')
        self.hit = replace(self.hit, observed_at=12., valid_until=13.)
        self.assertIsNone(self.ready(start=12.))
        self.hit = replace(self.hit, observed_at=13.1, valid_until=14.1)
        self.assertIsNotNone(self.ready(start=13.1))

    def test_global_rate_limit_applies_to_different_players_and_new_matches(self):
        action = self.ready()
        self.dispatch(action)
        self.hit = replace(self.hit, player_slot=4, observed_at=11., valid_until=12.)
        self.assertIsNone(self.ready(start=11.))
        self.hit = replace(self.hit, observed_at=12., valid_until=13., match_token='match-B')
        self.context = replace(self.context, match_token='match-B')
        self.assertIsNotNone(self.ready(start=12.))

    def test_disable_reenable_does_not_clear_recent_dedupe(self):
        self.dispatch(self.ready())
        self.session.stop()
        self.session.configure(self.module.TalentBroadcastConfig(enabled=True))
        self.hit = replace(self.hit, observed_at=10.4, valid_until=11.4)
        self.assertIsNone(self.ready(start=10.4))

    def test_disabling_cancels_offered_action_and_stale_action_cannot_send(self):
        action = self.ready()
        self.session.stop()
        self.session.configure(self.module.TalentBroadcastConfig(enabled=True))
        def must_not_send(*args, **kwargs):
            self.fail('停用前的动作不应继续发送')
        self.assertEqual(self.dispatch(action, sender=must_not_send).state, 'cancelled')


if __name__ == '__main__':
    unittest.main()
