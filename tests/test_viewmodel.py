import unittest
from hots_chat.models import ChatEvent,AppEvent
from hots_chat.ui.viewmodel import AppViewModel

class ViewModelTests(unittest.TestCase):
    def test_model_caps_at_500_and_updates_by_id(self):
        vm=AppViewModel()
        for i in range(510): vm.apply(AppEvent('chat',0,ChatEvent(str(i),0,'团队','tester','1:00','hello')))
        vm.apply(AppEvent('result',0,{'message_id':'509','translated':'你好','engine_translation':'你好'}))
        self.assertEqual(len(vm.messages),500)
        self.assertEqual(vm.messages['509'].engine_translation,'你好')
        self.assertFalse(vm.can_translate)
    def test_copy_uses_translation_and_stale_generation_rejected(self):
        vm=AppViewModel(); vm.expected_job='new'
        vm.apply(AppEvent('manual_result',0,{'job_id':'old','translated':'wrong'}))
        self.assertEqual(vm.copy_text,'')
        vm.apply(AppEvent('manual_result',0,{'job_id':'new','translated':'right','provenance':'缓存'}))
        self.assertEqual(vm.copy_text,'right')
        vm.apply(AppEvent('generation',1,'reset'))
        vm.apply(AppEvent('manual_result',0,{'job_id':'new','translated':'stale'}))
        self.assertEqual(vm.copy_text,'')
        self.assertIsNone(vm.expected_job)
        self.assertEqual(vm.manual_provenance,'')

    def test_generation_preserves_original_and_translation(self):
        vm=AppViewModel()
        vm.apply(AppEvent('chat',0,ChatEvent('1',0,'团队','tester','0:01','hello')))
        vm.apply(AppEvent('result',0,{'message_id':'1','engine_translation':'您好','detected_language':'en'}))

        vm.apply(AppEvent('generation',1,'重新连接'))
        vm.apply(AppEvent('chat',1,ChatEvent('2',1,'团队','tester','0:02','thanks')))

        self.assertEqual([(m.original,m.engine_translation) for m in vm.messages.values()],
                         [('hello','您好'),('thanks','')])
        self.assertEqual(vm.generation,1)
        self.assertEqual(vm.status,'重新连接')

    def test_new_generation_result_and_error_cannot_modify_old_message(self):
        vm=AppViewModel()
        vm.apply(AppEvent('chat',0,ChatEvent('1',0,'团队','tester','0:01','hello')))
        vm.apply(AppEvent('result',0,{'message_id':'1','engine_translation':'你好'}))
        vm.apply(AppEvent('generation',1,'重新连接'))

        vm.apply(AppEvent('result',1,{'message_id':'1','engine_translation':'不应覆盖'}))
        vm.apply(AppEvent('error',1,{'message_id':'1','error':'不应更改历史状态'}))

        self.assertEqual(len(vm.messages),1)
        message=next(iter(vm.messages.values()))
        self.assertEqual(message.engine_translation,'你好')
        self.assertEqual(message.status,'已翻译')

    def test_reused_message_id_preserves_both_generations_and_rejects_late_events(self):
        vm=AppViewModel()
        vm.apply(AppEvent('chat',0,ChatEvent('shared',0,'团队','tester','0:01','old text')))
        vm.apply(AppEvent('result',0,{'message_id':'shared','engine_translation':'旧译文'}))
        vm.apply(AppEvent('chat',0,ChatEvent('middle',0,'团队','tester','0:01','middle text')))
        vm.apply(AppEvent('generation',1,'重新连接'))
        vm.apply(AppEvent('chat',1,ChatEvent('shared',1,'团队','tester','0:02','new text')))
        vm.apply(AppEvent('result',1,{'message_id':'shared','engine_translation':'新译文'}))
        vm.apply(AppEvent('chat',0,ChatEvent('late',0,'团队','tester','0:03','迟到原文')))
        vm.apply(AppEvent('result',0,{'message_id':'shared','engine_translation':'迟到译文'}))
        vm.apply(AppEvent('error',0,{'message_id':'shared','error':'迟到错误'}))

        self.assertEqual([(m.event.generation,m.original,m.engine_translation,m.status)
                          for m in vm.messages.values()],
                         [(0,'old text','旧译文','已翻译'),(0,'middle text','','原文'),
                          (1,'new text','新译文','已翻译')])
        self.assertEqual(vm.messages['shared'].original,'new text')

    def test_late_generation_cannot_reactivate_stale_events(self):
        vm=AppViewModel()
        vm.apply(AppEvent('generation',2,'当前连接'))
        vm.apply(AppEvent('chat',2,ChatEvent('current',2,'团队','tester','0:01','current text')))
        vm.expected_job='current-job';vm.copy_text='当前预览';vm.manual_provenance='缓存'

        vm.apply(AppEvent('generation',1,'旧连接'))
        vm.apply(AppEvent('chat',1,ChatEvent('stale',1,'团队','tester','0:00','迟到原文')))

        self.assertEqual(vm.generation,2)
        self.assertEqual(list(vm.messages),['current'])
        self.assertEqual(vm.status,'当前连接')
        self.assertEqual((vm.expected_job,vm.copy_text,vm.manual_provenance),
                         ('current-job','当前预览','缓存'))

    def test_history_limit_is_shared_across_generations(self):
        vm=AppViewModel()
        for i in range(499):
            vm.apply(AppEvent('chat',0,ChatEvent(str(i),0,'团队','tester','0:01',f'old {i}')))
        vm.apply(AppEvent('generation',1,'重新连接'))
        vm.apply(AppEvent('chat',1,ChatEvent('499',1,'团队','tester','0:02','new 499')))
        vm.apply(AppEvent('chat',1,ChatEvent('500',1,'团队','tester','0:03','new 500')))

        self.assertEqual(len(vm.messages),500)
        self.assertEqual(next(iter(vm.messages.values())).original,'old 1')
        self.assertEqual(next(reversed(vm.messages.values())).original,'new 500')
