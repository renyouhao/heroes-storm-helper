"""离线状态与时钟回归：禁止使用真实游戏或翻译服务。"""
from collections import OrderedDict
import threading
import unittest

from hots_chat.controller import ChatController
from hots_chat.models import AppEvent, ChatEvent, TranslationResult
from hots_chat.translation.base import TranslationError
from hots_chat.ui.overlay_window import OverlayHistory
from hots_chat.ui.viewmodel import AppViewModel, MessageView


def message(key='a', *, generation=1, translated='', text='help'):
    return MessageView(ChatEvent(key, generation, '团队', '队友', '1:00', text),
                       engine_translation=translated)


class SubtitleLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.history = OverlayHistory()
        self.row = message()
        self.messages = OrderedDict(a=self.row)
        self.history.sync(self.messages, 1, now=10, timeout=6)

    def test_translation_near_original_expiry_has_three_seconds_to_read(self):
        self.row.engine_translation = '支援'
        self.history.sync(self.messages, 1, now=15, timeout=6)
        self.assertEqual(self.history.next_expiry, 18)
        self.history.expire(17.9)
        self.assertEqual(self.history.entries['a'].message.engine_translation, '支援')
        self.history.expire(18)
        self.assertFalse(self.history.entries)

    def test_slow_first_translation_can_show_after_original_expires(self):
        self.history.expire(16)
        self.assertFalse(self.history.entries)
        self.row.engine_translation = '支援'
        self.history.sync(self.messages, 1, now=18, timeout=6)
        self.assertEqual(self.history.entries['a'].message.engine_translation, '支援')
        self.assertEqual(self.history.next_expiry, 21)

    def test_translation_admitted_before_age_limit_has_full_reading_time_without_replay(self):
        self.history.expire(16)
        self.row.engine_translation = '支援'
        self.history.sync(self.messages, 1, now=24, timeout=6)
        self.assertEqual(self.history.next_expiry, 27)
        self.history.expire(27)
        self.history.sync(self.messages, 1, now=28, timeout=6)
        self.assertFalse(self.history.entries)

    def test_translation_arriving_at_or_after_total_age_does_not_replay(self):
        for arrival in (25, 26, 100):
            with self.subTest(arrival=arrival):
                history = OverlayHistory()
                row = message()
                messages = OrderedDict(a=row)
                history.sync(messages, 1, now=10, timeout=6)
                history.expire(16)
                row.engine_translation = '迟到'
                history.sync(messages, 1, now=arrival, timeout=6)
                self.assertFalse(history.entries)

    def test_duplicate_result_and_sync_cannot_extend_translation(self):
        self.row.engine_translation = '支援'
        self.history.sync(self.messages, 1, now=15, timeout=6)
        for now in (16, 17):
            self.row.engine_translation += '!'
            self.history.sync(self.messages, 1, now=now, timeout=6)
        self.assertEqual(self.history.next_expiry, 18)
        self.history.expire(18)
        self.history.sync(self.messages, 1, now=19, timeout=6)
        self.assertFalse(self.history.entries)

    def test_duplicate_spam_cannot_restart_original_or_reading_deadline(self):
        self.history.expire(16)
        self.messages['b'] = message('b')
        self.history.sync(self.messages, 1, now=18, timeout=6)
        self.assertFalse(self.history.entries)
        self.row.engine_translation = '支援'
        self.history.sync(self.messages, 1, now=19, timeout=6)
        self.assertEqual(self.history.next_expiry, 22)
        self.messages['c'] = message('c', translated='重复译文')
        self.history.sync(self.messages, 1, now=21, timeout=6)
        self.assertEqual(self.history.next_expiry, 22)
        self.history.expire(25)
        self.messages['d'] = message('d', translated='刷屏')
        self.history.sync(self.messages, 1, now=26, timeout=6)
        self.assertEqual(list(self.history.entries),['d'])
        self.assertEqual(self.history.next_expiry,32)

    def test_real_new_message_after_fresh_window_does_not_borrow_historical_translation(self):
        self.row.engine_translation='旧译文'
        self.history.sync(self.messages,1,now=11,timeout=6)
        self.history.expire(16)
        self.messages['b']=message('b')
        self.history.sync(self.messages,1,now=20,timeout=6)
        self.assertFalse(self.history.entries,'原新鲜窗口内同文不能续命')
        fresh=message('c')
        self.messages['c']=fresh
        self.history.sync(self.messages,1,now=26,timeout=6)
        self.assertEqual(list(self.history.entries),['c'])
        self.assertIs(self.history.entries['c'].message,fresh)
        self.assertFalse(self.history.entries['c'].message.engine_translation)
        self.history.sync(self.messages,1,now=27,timeout=6)
        self.assertIs(self.history.entries['c'].message,fresh)
        self.assertEqual(self.history.next_expiry,32)

    def test_disable_and_generation_change_reject_slow_translation(self):
        self.history.sync(self.messages, 1, now=12, timeout=6, enabled=False)
        self.row.engine_translation = '迟到'
        self.history.sync(self.messages, 1, now=18, timeout=6)
        self.assertFalse(self.history.entries)
        self.history.sync(self.messages, 2, now=19, timeout=6)
        self.assertFalse(self.history.entries)

    def test_retained_untranslated_candidates_and_seen_keys_stay_bounded(self):
        messages = OrderedDict()
        history = OverlayHistory()
        for index in range(1000):
            key = str(index)
            messages[key] = message(key, text=key)
            if len(messages) > 500:
                messages.popitem(last=False)
            history.sync(messages, 1, now=10 + index / 1000, timeout=6)
        self.assertLessEqual(len(history._candidates), 500)
        self.assertLessEqual(len(history._seen), 500)
        self.assertLessEqual(len(history._retired), 500)
        self.assertLessEqual(len(history.entries), 3)
        history.expire(30)
        self.assertFalse(history._candidates)

    def test_suppressed_duplicate_translation_cannot_restart_reading_time(self):
        self.history.expire(16)
        duplicate=message('b',translated='防刷屏消息的译文')
        duplicate.suppressed_reason='发送过快'
        self.messages['b']=duplicate
        self.history.sync(self.messages,1,now=18,timeout=6)
        self.assertFalse(self.history.entries)
        self.row.engine_translation='正常译文'
        self.history.sync(self.messages,1,now=19,timeout=6)
        self.assertEqual(self.history.next_expiry,22)


class TranslationStateTests(unittest.TestCase):
    def test_vm_distinguishes_untranslated_queue_failure_and_result(self):
        vm = AppViewModel()
        vm.apply(AppEvent('chat', 0, message(generation=0).event))
        row = vm.messages['a']
        self.assertEqual(row.status, '未翻译')
        self.assertEqual(row.translation_state, 'untranslated')
        vm.apply(AppEvent('translation_state', 0, {'message_id': 'a', 'state': 'queued', 'reason': ''}))
        self.assertEqual((row.translation_state, row.status), ('queued', '排队中'))
        vm.apply(AppEvent('error', 0, {'message_id': 'a', 'error': '网络超时'}))
        self.assertEqual((row.translation_state, row.translation_reason), ('failed', '网络超时'))
        vm.apply(AppEvent('result', 0, {'message_id': 'a', 'engine_translation': '支援'}))
        self.assertEqual((row.translation_state, row.translation_reason), ('translated', ''))

    def test_state_event_cannot_change_old_generation_or_missing_messages(self):
        vm = AppViewModel()
        vm.apply(AppEvent('chat', 0, message(generation=0).event))
        vm.apply(AppEvent('generation', 1, '换局'))
        result = vm.apply(AppEvent('translation_state', 1,
                                  {'message_id': 'a', 'state': 'queued', 'reason': ''}))
        self.assertEqual(result, 'missing_message')
        self.assertEqual(vm.messages['a'].status, '未翻译')


class OfflineSource:
    def stop(self):
        pass


class OfflineEngine:
    provider_id = 'offline'
    config_version = '1'

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.failure = None

    def translate(self, job):
        self.entered.set()
        if not self.release.wait(2):
            raise RuntimeError('离线测试未释放请求')
        if self.failure:
            raise self.failure
        return TranslationResult(job.job_id, '支援', 'en', None, 0)

    def close(self):
        pass


class ControllerTranslationStateTests(unittest.TestCase):
    def setUp(self):
        self.engine = OfflineEngine()
        self.controller = ChatController(OfflineSource(), self.engine, 'offline')
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.engine.release.set()
        self.controller.close()

    def test_skipped_route_has_specific_reason_without_queuing_cloud_request(self):
        for setting, value, text, expected in (
                ('automatic_paused', True, 'help', '自动翻译已暂停'),
                ('paused', True, 'help', '云翻译已关闭'),
                ('configuration_held', True, 'help', '配置处理中'),
                ('cloud_configured', False, 'help', '翻译服务未配置'),
                ('automatic_paused', False, '日本語かな', '不支持的语言')):
            with self.subTest(setting=setting):
                self.controller.automatic_paused = False
                self.controller.paused = False
                self.controller.configuration_held = False
                self.controller.cloud_configured = True
                setattr(self.controller, setting, value)
                self.controller.on_chat(message(generation=0, text=text).event)
                states = [event.payload for event in self.controller.drain_events()
                          if event.kind == 'translation_state']
                self.assertEqual(states, [{'message_id': 'a', 'state': 'skipped', 'reason': expected}])
                self.assertFalse(self.engine.entered.is_set())

    def test_queue_active_failure_and_retry_transition_reach_viewmodel(self):
        self.engine.failure = TranslationError('timeout', '网络超时')
        self.controller.on_chat(message(generation=0).event)
        self.assertTrue(self.engine.entered.wait(2))
        vm = AppViewModel()
        states = []
        for event in self.controller.drain_events():
            vm.apply(event)
            if event.kind == 'translation_state':
                states.append(event.payload['state'])
        self.assertEqual(states, ['queued', 'translating'])
        self.assertEqual(vm.messages['a'].translation_state, 'translating')
        self.engine.release.set()
        with self.controller.condition:
            self.assertTrue(self.controller.condition.wait_for(lambda: self.controller.idle, 2))
        for event in self.controller.drain_events():
            vm.apply(event)
        self.assertEqual(vm.messages['a'].translation_state, 'failed')
        self.engine.failure = None
        self.controller.retry_message('a')
        with self.controller.condition:
            self.assertTrue(self.controller.condition.wait_for(lambda: self.controller.idle, 2))
        for event in self.controller.drain_events():
            vm.apply(event)
        self.assertEqual((vm.messages['a'].translation_state, vm.messages['a'].engine_translation),
                         ('translated', '支援'))
