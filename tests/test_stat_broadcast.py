"""统计单格播报；只用可信数值夹具，不接触游戏或真实输入。"""
from dataclasses import replace
import importlib
import unittest


class StatBroadcastTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('hots_chat.talent_broadcast')
        self.assertTrue(hasattr(self.module, 'StatHitSnapshot'), '尚未提供独立统计命中快照')
        self.hit = self.module.StatHitSnapshot(
            pid=11, hwnd=22, match_token='match-A', player_slot=15,
            team_relation='ally', metric_id='hero_damage', value=12345,
            rect=(100, 200, 160, 260), observed_at=10., valid_until=11.,
            panel_visible=True, mapping_trusted=True,
            player_name='测试玩家', hero_name='吉安娜')
        self.context = self.module.InputContext(
            pid=11, hwnd=22, match_token='match-A', foreground=True)
        self.session = self.module.TalentBroadcastSession(
            config=self.module.TalentBroadcastConfig(enabled=True),
            double_click_ms=500, double_click_size=(4, 4))

    def click(self, hit, now):
        options = dict(x=120, y=220, hit=hit, context=self.context, now=now)
        self.assertIs(self.session.mouse(down=True, **options), False)
        self.assertIs(self.session.mouse(down=False, **options), False)

    def ready(self, hit=None, start=10.):
        hit = self.hit if hit is None else hit
        self.click(hit, start)
        self.click(hit, start + .2)
        return self.session.tick(hit, self.context, start + .2)

    def send(self, action, hit=None, now=10.2):
        return self.session.dispatch(action, lambda request, **kwargs: 'sent',
                                     self.hit if hit is None else hit, self.context, now)

    def test_only_clicked_metric_is_formatted_with_thousands_separator(self):
        self.assertEqual(self.module.format_stat_message(self.hit),
                         '【我方】测试玩家（吉安娜）｜英雄伤害：12,345')
        self.assertNotIn('天赋', self.module.format_stat_message(self.hit))
        self.assertNotIn('治疗', self.module.format_stat_message(self.hit))

    def test_each_allowed_metric_has_one_explicit_label_and_correct_unit(self):
        cases = (
            ('solo_kills', '击杀：12次'), ('assists', '助攻：12次'),
            ('deaths', '死亡：12次'), ('hero_damage', '英雄伤害：12'),
            ('siege_damage', '攻城伤害：12'), ('healing', '治疗：12'),
            ('self_healing', '自我治疗：12'), ('experience', '经验贡献：12'))
        for metric, suffix in cases:
            with self.subTest(metric=metric):
                self.assertEqual(self.module.format_stat_message(
                    replace(self.hit, metric_id=metric, value=12)),
                    '【我方】测试玩家（吉安娜）｜' + suffix)

    def test_zero_is_valid_and_missing_values_are_not_coerced_to_zero(self):
        self.assertEqual(self.module.format_stat_message(replace(self.hit, value=0)),
                         '【我方】测试玩家（吉安娜）｜英雄伤害：0')
        for value in (None, '-', '', '0', True, False, -1, 1.5, float('nan'), float('inf'), 2**32):
            with self.subTest(value=value):
                self.assertIsNone(self.module.format_stat_message(replace(self.hit, value=value)))
        self.assertIsNotNone(self.module.format_stat_message(replace(self.hit, value=2**32 - 1)))

    def test_unknown_metrics_and_untrusted_identity_are_rejected(self):
        for changes in ({'metric_id': 'kills'}, {'metric_id': 'all_stats'},
                        {'metric_id': ''}, {'mapping_trusted': False},
                        {'team_relation': 'neutral'}, {'team_relation': 'unknown'},
                        {'player_name': '', 'hero_name': ''}):
            with self.subTest(changes=changes):
                self.assertIsNone(self.module.format_stat_message(replace(self.hit, **changes)))

    def test_text_sanitization_and_explicit_hero_alias_are_shared(self):
        text = self.module.format_stat_message(replace(
            self.hit, player_name='<c val="red">玩家</c>\n/all\x00\u202e'))
        self.assertEqual(text, '【我方】玩家（吉安娜）｜英雄伤害：12,345')
        self.assertEqual(self.module.format_stat_message(replace(self.hit, player_name='')),
                         '【我方】英雄：吉安娜｜英雄伤害：12,345')
        text = self.module.format_stat_message(replace(
            self.hit, player_name='玩' * 1000, hero_name='英' * 1000))
        self.assertLessEqual(len(text), 150)
        self.assertTrue(text.endswith('｜英雄伤害：12,345'))

    def test_stat_uses_distinct_action_without_fake_talent_tier_or_id(self):
        action = self.ready()
        self.assertIsNotNone(action)
        self.assertEqual((action.kind, action.metric_id, action.talent_id),
                         ('stat', 'hero_damage', ''))
        self.assertEqual(action.player_slot, 15)
        self.assertEqual(action.channel, 'team')
        self.assertEqual(self.send(action).state, 'sent')

    def test_stats_remain_disabled_by_default(self):
        self.session = self.module.TalentBroadcastSession(
            double_click_ms=500, double_click_size=(4, 4))
        self.assertIsNone(self.ready())
        self.assertEqual(self.session.status.state, 'disabled')

    def test_unknown_stale_invisible_or_invalid_slots_cannot_arm(self):
        for changes in ({'value': None}, {'mapping_trusted': False},
                        {'observed_at': 8.}, {'panel_visible': False},
                        {'player_slot': 16}, {'player_slot': -1}, {'player_slot': True}):
            with self.subTest(changes=changes):
                self.session.cancel()
                self.assertIsNone(self.ready(replace(self.hit, **changes)))
        self.session.cancel()
        self.assertIsNotNone(self.ready(replace(self.hit, player_slot=0)))

    def test_changing_stat_value_between_clicks_uses_second_click_value(self):
        self.click(self.hit, 10.)
        changed = replace(self.hit, value=12346)
        self.click(changed, 10.2)
        action = self.session.tick(changed, self.context, 10.2)
        self.assertIsNotNone(action)
        self.assertEqual(action.message, '【我方】测试玩家（吉安娜）｜英雄伤害：12,346')

    def test_changing_target_or_display_identity_between_clicks_cannot_send(self):
        for changes in ({'metric_id': 'siege_damage'}, {'player_slot': 10},
                        {'rect': (101, 200, 161, 260)}, {'team_relation': 'enemy'},
                        {'player_name': '另一玩家'}, {'hero_name': '克罗米'}):
            with self.subTest(changes=changes):
                self.session.cancel()
                self.click(self.hit, 10.)
                changed = replace(self.hit, **changes)
                self.click(changed, 10.2)
                self.assertIsNone(self.session.tick(changed, self.context, 10.2))

    def test_value_change_after_offer_keeps_original_action_and_frozen_message(self):
        action = self.ready()
        calls = []
        def send(request, **kwargs):
            calls.append(request)
            return 'sent'
        status = self.session.dispatch(action, send, replace(self.hit, value=12346),
                                       self.context, 10.3)
        self.assertEqual(status.state, 'sent')
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0], action)
        self.assertEqual(action.message, '【我方】测试玩家（吉安娜）｜英雄伤害：12,345')

    def test_continuously_changing_stat_refreshes_until_offer_then_freezes(self):
        self.click(self.hit, 10.)
        second_down = replace(self.hit, value=12346)
        options = dict(x=120, y=220, hit=second_down, context=self.context, now=10.2)
        self.assertIs(self.session.mouse(down=True, **options), False)
        held = replace(self.hit, value=12347)
        self.assertIsNone(self.session.tick(held, self.context, 10.21))
        released = replace(self.hit, value=12348)
        self.assertIs(self.session.mouse(x=120, y=220, down=False, hit=released,
                                         context=self.context, now=10.22), False)
        latest = replace(self.hit, value=12349)
        action = self.session.tick(latest, self.context, 10.23)
        self.assertIsNotNone(action)
        self.assertEqual(action.message, '【我方】测试玩家（吉安娜）｜英雄伤害：12,349')
        after_offer = replace(self.hit, value=12350)
        self.assertIsNone(self.session.tick(after_offer, self.context, 10.24))
        calls = []
        def send(request, **kwargs):
            calls.append(request)
            return 'sent'
        status = self.session.dispatch(action, send, replace(self.hit, value=12351),
                                       self.context, 10.25)
        self.assertEqual(status.state, 'sent')
        self.assertIs(calls[0], action)
        self.assertEqual(calls[0].message, '【我方】测试玩家（吉安娜）｜英雄伤害：12,349')

    def test_busy_pending_refreshes_to_latest_stat_when_offered(self):
        self.context = replace(self.context, quick_input_busy=True)
        self.assertIsNone(self.ready())
        self.assertIsNone(self.session.tick(replace(self.hit, value=12346), self.context, 10.3))
        self.context = replace(self.context, quick_input_busy=False)
        action = self.session.tick(replace(self.hit, value=12347), self.context, 10.4)
        self.assertIsNotNone(action)
        self.assertEqual(action.message, '【我方】测试玩家（吉安娜）｜英雄伤害：12,347')

    def test_target_page_or_display_identity_change_after_offer_still_cancels(self):
        for changes in ({'player_slot': 10}, {'team_relation': 'enemy'},
                        {'metric_id': 'siege_damage'}, {'rect': (101, 200, 161, 260)},
                        {'player_name': '另一玩家'}, {'hero_name': '克罗米'},
                        {'panel_visible': False}, {'match_token': 'match-B'},
                        {'pid': 12}, {'hwnd': 23}, {'mapping_trusted': False}):
            with self.subTest(changes=changes):
                self.session.cancel()
                action = self.ready()
                calls = []
                def send(request, **kwargs):
                    calls.append(request)
                    return 'sent'
                status = self.session.dispatch(action, send, replace(self.hit, **changes),
                                               self.context, 10.3)
                self.assertEqual(status.state, 'cancelled')
                self.assertEqual(calls, [])

    def test_unknown_value_after_offer_does_not_become_a_frozen_valid_zero(self):
        for value in (None, '-', True, -1):
            with self.subTest(value=value):
                self.session.cancel()
                action = self.ready()
                calls = []
                def send(request, **kwargs):
                    calls.append(request)
                    return 'sent'
                status = self.session.dispatch(action, send, replace(self.hit, value=value),
                                               self.context, 10.3)
                self.assertEqual(status.state, 'cancelled')
                self.assertEqual(calls, [])

    def test_new_value_does_not_bypass_same_player_same_metric_three_second_dedupe(self):
        self.send(self.ready())
        changed = replace(self.hit, observed_at=12.1, valid_until=13.1, value=55555)
        self.assertIsNone(self.ready(changed, 12.1))
        changed = replace(changed, observed_at=13.1, valid_until=14.1)
        self.assertIsNotNone(self.ready(changed, 13.1))

    def test_two_second_global_limit_is_shared_with_talent_broadcasts(self):
        self.send(self.ready())
        talent = self.module.HitSnapshot(
            pid=11, hwnd=22, match_token='match-A', player_slot=15,
            team_relation='ally', tier_level=13, talent_id='FixtureTalent',
            talent_name='测试天赋', effect_summary='测试关键效果。',
            rect=(100, 200, 160, 260), observed_at=11., valid_until=12.,
            panel_visible=True, selected=True, mapping_trusted=True,
            player_name='测试玩家', hero_name='吉安娜')
        self.assertIsNone(self.ready(talent, 11.))
        talent = replace(talent, observed_at=12., valid_until=13.)
        action = self.ready(talent, 12.)
        self.assertEqual((action.kind, action.talent_id, action.metric_id),
                         ('talent', 'FixtureTalent', ''))
        self.assertEqual(self.send(action, talent, 12.2).state, 'sent')
        fresh_stat = replace(self.hit, player_slot=10, observed_at=13., valid_until=14.)
        self.assertIsNone(self.ready(fresh_stat, 13.))

    def test_metric_and_talent_ids_do_not_share_three_second_dedupe_key(self):
        self.send(self.ready())
        talent = self.module.HitSnapshot(
            pid=11, hwnd=22, match_token='match-A', player_slot=15,
            team_relation='ally', tier_level=13, talent_id='hero_damage',
            talent_name='测试天赋', effect_summary='测试关键效果。',
            rect=(100, 200, 160, 260), observed_at=12., valid_until=13.,
            panel_visible=True, selected=True, mapping_trusted=True,
            player_name='测试玩家', hero_name='吉安娜')
        self.assertIsNotNone(self.ready(talent, 12.))

    def test_failed_stat_send_is_not_retried(self):
        action = self.ready()
        calls = []
        def send(request, **kwargs):
            calls.append(request)
            raise OSError('partial stat send')
        self.session.dispatch(action, send, self.hit, self.context, 10.2)
        self.session.dispatch(action, send, self.hit, self.context, 10.3)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(self.ready(replace(self.hit, observed_at=10.4, valid_until=11.4), 10.4))

    def test_single_stat_double_click_is_deferred_until_second_release(self):
        self.click(self.hit,10.)
        options = dict(x=120,y=220,hit=self.hit,context=self.context,now=10.2,event_at=10.15)
        self.session.mouse(down=True,**options)
        self.assertIsNone(self.session.tick(self.hit,self.context,10.2))
        self.session.mouse(down=False,**options)
        action = self.session.tick(self.hit,self.context,10.21)
        self.assertIsNotNone(action)
        self.assertEqual((action.kind,action.metric_id),('stat','hero_damage'))

    def test_stat_rejects_equal_float_and_boolean_context_process_ids(self):
        for hit_changes, context_changes in (({}, {'pid':11.0}), ({}, {'hwnd':22.0}),
                                              ({'pid':1}, {'pid':True}), ({'hwnd':1}, {'hwnd':True})):
            with self.subTest(context=context_changes):
                self.session.cancel()
                hit = replace(self.hit,**hit_changes)
                self.context = replace(self.context,pid=hit.pid,hwnd=hit.hwnd)
                self.context = replace(self.context,**context_changes)
                self.assertIsNone(self.ready(hit))


if __name__ == '__main__':
    unittest.main()
