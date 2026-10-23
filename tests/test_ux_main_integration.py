"""主窗对辅助运行状态和游戏内反馈的离线接线。"""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from hots_chat.ui.main_window import MainWindow
from hots_chat.window_settings import WindowSettings


class HelperFeedbackIntegrationTests(unittest.TestCase):
    def window(self):
        window = MainWindow.__new__(MainWindow)
        window.settings = WindowSettings(talent_enabled=True, board_broadcast_enabled=True)
        window.talent_helper = NS(running=False)
        window.board_helper = NS(running=True)
        window.talent_panel = NS(update_status=Mock(), set_running=Mock())
        window.board_panel = NS(update_status=Mock(), set_running=Mock())
        window.helper_notice = NS(show=Mock(return_value=True), clear=Mock())
        window.controller = NS(generation=7)
        window._feedback_allowed = Mock(return_value=True)
        window._notice_target = None
        return window

    def test_talent_failure_keeps_preference_and_exposes_stopped_runtime(self):
        window = self.window()
        status = NS(state='error', detail='天赋辅助已停止', tier_id=3)
        window.update_helper_status('talent', status)
        self.assertTrue(window.settings.talent_enabled)
        window.talent_panel.set_running.assert_called_once_with(False)
        window.talent_panel.update_status.assert_called_once_with('error', '天赋辅助已停止', 3)
        window.helper_notice.show.assert_called_once_with('talent', 'error', '天赋辅助已停止', generation=7,dedupe_key=3)

    def test_other_foreground_never_receives_game_notice(self):
        window = self.window()
        window._feedback_allowed.return_value = False
        window.update_helper_status('board', NS(state='error', detail='未提交，按 Esc 取消'))
        window.board_panel.update_status.assert_called_once()
        window.helper_notice.show.assert_not_called()

    def test_normal_success_does_not_probe_foreground_or_show_notice(self):
        window = self.window()
        window.update_helper_status('talent', NS(state='confirmed', detail='已确认', tier_id=1))
        window._feedback_allowed.assert_not_called()
        window.helper_notice.show.assert_not_called()

    def test_retry_preserves_preference_and_does_not_save_or_resend_old_action(self):
        window = self.window()
        window._talent_runtime_enabled = True
        window._update_installing = False
        window._sync_talent_gate = Mock()
        window.talent_helper.set_enabled = Mock(return_value=True)
        window.save_settings = Mock()
        window.retry_talent()
        window.talent_helper.set_enabled.assert_called_once_with(True)
        window.save_settings.assert_not_called()
        self.assertTrue(window.settings.talent_enabled)

    def test_enabling_saved_cloud_refreshes_status_after_resuming(self):
        window=MainWindow.__new__(MainWindow)
        window.controller=NS(cloud_configured=True,configuration_held=False,paused=True)
        window.controller.pause_translation=lambda paused:setattr(window.controller,'paused',paused)
        window.automatic=NS(get=lambda:True)
        window.cloud=NS(set=Mock())
        window.service_label=NS(configure=Mock())
        window.enable_configured_cloud()
        self.assertIn('已启用',window.service_label.configure.call_args.kwargs['text'])


if __name__ == '__main__':
    unittest.main()
