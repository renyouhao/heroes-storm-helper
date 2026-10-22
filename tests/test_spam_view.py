import unittest
from hots_chat.models import AppEvent,ChatEvent
from hots_chat.ui.viewmodel import AppViewModel


class SpamViewTests(unittest.TestCase):
    def test_suppression_keeps_original_row_and_indicates_it_can_be_retried(self):
        vm=AppViewModel()
        vm.apply(AppEvent('chat',0,ChatEvent('a',0,'团队','t','1:00','hello')))
        vm.apply(AppEvent('suppressed',0,{'message_id':'a','reason':'sender_rate'}))
        self.assertEqual(vm.messages['a'].original,'hello')
        self.assertEqual(vm.messages['a'].suppressed_reason,'sender_rate')
        self.assertIn('防刷屏',vm.messages['a'].status)
        vm.apply(AppEvent('result',0,{'message_id':'a','engine_translation':'你好','provenance':'缓存'}))
        self.assertEqual(vm.messages['a'].suppressed_reason,'')
        self.assertEqual(vm.messages['a'].engine_translation,'你好')

    def test_stale_suppression_cannot_modify_current_message(self):
        vm=AppViewModel()
        vm.apply(AppEvent('generation',2,'new'))
        vm.apply(AppEvent('chat',2,ChatEvent('a',2,'团队','t','1:00','hello')))
        vm.apply(AppEvent('suppressed',1,{'message_id':'a','reason':'sender_rate'}))
        self.assertEqual(vm.messages['a'].status,'原文')
