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
        self.assertFalse(hasattr(vm, 'usage_snapshot'))
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
                         [(0,'old text','旧译文','已翻译'),(0,'middle text','','未翻译'),
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


class TranslationConfigurationTests(unittest.TestCase):
    def test_configured_translation_does_not_require_a_usage_ledger(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from hots_chat.ui.main_window import MainWindow
        window = MainWindow.__new__(MainWindow)
        window.controller = SimpleNamespace(cloud_configured=True, configuration_held=False,
                                            pause_translation=Mock())
        window.cloud = Mock()
        window.automatic = SimpleNamespace(get=lambda: True)
        window.service_label = Mock()
        self.assertTrue(callable(getattr(window, "enable_configured_cloud", None)))
        window.enable_configured_cloud()
        window.cloud.set.assert_called_once_with(True)
        window.controller.pause_translation.assert_called_once_with(False)
        self.assertIn('已启用',window.service_label.configure.call_args.kwargs['text'])

    def test_missing_credentials_keeps_original_text_mode(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from hots_chat.ui.main_window import MainWindow
        window = MainWindow.__new__(MainWindow)
        window.controller = SimpleNamespace(cloud_configured=False, configuration_held=False,
                                            pause_translation=Mock())
        window.cloud = Mock()
        window.service_label = Mock()
        self.assertTrue(callable(getattr(window, "enable_configured_cloud", None)))
        window.enable_configured_cloud()
        window.cloud.set.assert_called_once_with(False)
        window.controller.pause_translation.assert_called_once_with(True)
        window.service_label.configure.assert_called_with(text='翻译服务：未配置 · 接收原文')

    def test_configuration_hold_keeps_cloud_switch_disabled(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from hots_chat.ui.main_window import MainWindow
        window = MainWindow.__new__(MainWindow)
        window.controller = SimpleNamespace(cloud_configured=True, configuration_held=True,
                                            pause_translation=Mock())
        window.cloud = Mock(get=lambda: True)
        window.service_label = Mock()
        window.toggle_cloud()
        window.cloud.set.assert_called_once_with(False)
        window.controller.pause_translation.assert_called_once_with(True)


class CloudQuotaLinkTests(unittest.TestCase):
    def window(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from hots_chat.ui.main_window import MainWindow
        window = MainWindow.__new__(MainWindow)
        # No engine, identity, or account service exists: this is only a browser link.
        window.controller = SimpleNamespace(cloud_configured=False)
        window.status_var = Mock()
        self.assertTrue(callable(getattr(window, 'open_cloud_quota', None)))
        return window

    def test_unconfigured_user_can_open_exact_console_url_without_cloud_calls(self):
        from unittest.mock import patch
        window = self.window()
        with patch('webbrowser.open', return_value=True) as browser:
            self.assertTrue(window.open_cloud_quota())
        browser.assert_called_once_with('https://mt.console.aliyun.com/service')
        text = window.status_var.set.call_args.args[0]
        self.assertIn('登录', text)
        self.assertNotIn('查询成功', text)

    def test_browser_failure_shows_manual_url_and_allows_retry(self):
        from unittest.mock import patch
        for failure in (False, OSError('no browser')):
            with self.subTest(failure=failure):
                window = self.window()
                kwargs = {'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure}
                with patch('webbrowser.open', **kwargs):
                    self.assertFalse(window.open_cloud_quota())
                text = window.status_var.set.call_args.args[0]
                self.assertIn('https://mt.console.aliyun.com/service', text)
                self.assertIn('手动', text)
                with patch('webbrowser.open', return_value=True) as browser:
                    self.assertTrue(window.open_cloud_quota())
                browser.assert_called_once_with('https://mt.console.aliyun.com/service')
