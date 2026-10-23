import time
import unittest
from datetime import datetime, timezone

from hots_chat.controller import ChatController
from hots_chat.models import ChatEvent
from tests.test_controller import Engine, Source


class ControllerConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.now = lambda: datetime(2026, 10, 20, tzinfo=timezone.utc)
        self.engine = Engine()
        self.controller = ChatController(Source(), self.engine, 'main', self.now)
        self.addCleanup(self.controller.close)

    def wait_idle(self):
        deadline = time.monotonic() + 3
        while not self.controller.idle and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(self.controller.idle)

    def test_configuration_hold_rejects_ordinary_resume_and_release_stays_paused(self):
        self.assertTrue(callable(getattr(self.controller, 'hold_configuration', None)),
                        '控制器缺少配置暂停锁接口')
        self.controller.hold_configuration(True)
        self.controller.pause_translation(False)
        self.assertTrue(self.controller.configuration_held)
        self.assertTrue(self.controller.paused)
        self.controller.on_chat(ChatEvent('1', 0, '团队', 'tester', '1:00', 'hello'))
        self.controller.submit_manual('集合', 'ko')
        self.assertTrue(self.controller.idle)
        self.assertEqual(self.engine.calls, [])
        self.controller.hold_configuration(False)
        self.assertFalse(self.controller.configuration_held)
        self.assertTrue(self.controller.paused)
        self.controller.pause_translation(False)
        self.controller.submit_manual('集合', 'ko')
        self.wait_idle()
        self.assertEqual([job.text for job in self.engine.calls], ['集合'])

    def test_configuration_hold_cancels_waiting_but_completes_existing_network_request(self):
        self.engine.release.clear()
        self.addCleanup(self.engine.release.set)
        self.controller.on_chat(ChatEvent('1', 0, '团队', 'tester', '1:00', 'first'))
        self.assertTrue(self.engine.entered.wait(1))
        self.controller.submit_manual('撤退', 'en')
        self.controller.hold_configuration(True)
        self.controller.pause_translation(False)
        self.engine.release.set()
        self.wait_idle()
        self.assertEqual([job.text for job in self.engine.calls], ['first'])

    def test_configuration_hold_allows_manual_local_resolution_and_cached_result(self):
        self.controller.submit_manual('集合', 'ko')
        self.wait_idle()
        self.controller.drain_events()
        self.controller.set_local_resolver(lambda text, target: 'Retreat' if text == '撤退' else None)
        self.controller.hold_configuration(True)
        cached = self.controller.submit_manual('集合', 'ko')
        local = self.controller.submit_manual('撤退', 'en')
        self.assertEqual([job.text for job in self.engine.calls], ['集合'])
        results = {event.payload['job_id']: event.payload for event in self.controller.drain_events()
                   if event.kind == 'manual_result'}
        self.assertEqual(results[cached]['provenance'], '缓存')
        self.assertEqual(results[local]['translated'], 'Retreat')

    def test_replacement_during_hold_survives_safe_old_engine_cleanup_failure(self):
        def fail_close():
            raise RuntimeError('secret=should-never-appear')

        self.addCleanup(setattr, self.engine, 'close', self.engine.close)
        self.engine.close = fail_close
        self.controller.hold_configuration(True)
        replacement = Engine()
        self.controller.replace_engine(replacement, 'verified-account')
        self.assertIs(self.controller.engine, replacement)
        self.assertEqual(self.controller.account, 'verified-account')
        self.assertTrue(self.controller.configuration_held)
        self.assertTrue(self.controller.paused)
        statuses = [event.payload for event in self.controller.drain_events() if event.kind == 'status']
        self.assertTrue(any('清理' in status for status in statuses))
        self.assertNotIn('should-never-appear', str(statuses))


if __name__ == '__main__':
    unittest.main()
