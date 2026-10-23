"""天赋辅助配置与真实 Tk 控件，全部离线，不打开游戏或发送输入。"""
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest

from hots_chat.window_settings import WindowSettings
from hots_chat.layout import DisplayMetrics


class TalentSettingsTests(unittest.TestCase):
    def test_disabled_by_default_and_round_trip(self):
        settings = WindowSettings()
        self.assertFalse(settings.talent_enabled)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'
            settings.talent_enabled = True
            settings.save(path)
            self.assertTrue(WindowSettings.load(path).talent_enabled)

    def test_invalid_opt_in_does_not_enable_or_reset_other_preferences(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'
            for value in ('true', 1, None, [], {}):
                path.write_text(json.dumps({'talent_enabled': value, 'font_size': 16}))
                result = WindowSettings.load(path)
                self.assertFalse(result.talent_enabled)
                self.assertEqual(result.font_size, 16)


class TalentPanelTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def test_toggle_updates_only_after_success_and_displays_failure(self):
        from hots_chat.ui.talent_panel import TalentPanel
        settings = WindowSettings()
        calls = []
        def apply(enabled):
            calls.append(enabled)
            if len(calls) == 2:
                raise OSError('无法保存设置')
            settings.talent_enabled = enabled
        panel = TalentPanel(self.root, settings=settings,
                            metrics=DisplayMetrics((0, 0, 1920, 1080), 1), on_toggle=apply)
        self.assertFalse(panel.enabled.get())
        panel.toggle.invoke()
        self.assertEqual(calls, [True])
        self.assertTrue(settings.talent_enabled)
        panel.toggle.invoke()
        self.assertTrue(panel.enabled.get())
        self.assertIn('未保存', panel.detail.get())

    def test_theme_refresh_does_not_restart_or_hide_unverified_identity(self):
        from hots_chat.ui.talent_panel import TalentPanel
        settings = WindowSettings()
        calls = []
        panel = TalentPanel(self.root, settings=settings,
                            metrics=DisplayMetrics((0, 0, 1920, 1080), 1), on_toggle=calls.append)
        panel.update_status('waiting', '本地玩家身份尚未确认，暂不发送按键。')
        settings.color_theme = 'cyan'
        panel.apply_settings(settings)
        self.assertEqual(calls, [])
        self.assertIn('身份', panel.detail.get())
        panel.close()
        panel.update_status('confirmed', '旧消息')
        self.assertNotEqual(panel.detail.get(), '旧消息')

    def test_confirmed_result_survives_next_idle_poll(self):
        from hots_chat.ui.talent_panel import TalentPanel
        panel = TalentPanel(self.root, settings=WindowSettings(),
                            metrics=DisplayMetrics((0, 0, 1920, 1080), 1), on_toggle=lambda value:None)
        panel.update_status('confirmed', '已回读确认：该层已选择。')
        panel.update_status('idle', '当前没有待选天赋。')
        self.assertIn('该层已选择', panel.last_result.get())
