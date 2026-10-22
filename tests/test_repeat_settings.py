"""S 连按偏好只用临时文件与隔离设置事务。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hots_chat.window_settings import WindowSettings
from tests import test_three_theme_settings as transactions


class RepeatSettingsTests(unittest.TestCase):
    def test_default_and_round_trip_only_save_binding_not_running_state(self):
        settings = WindowSettings()
        self.assertEqual(getattr(settings, 'repeat_hotkey', None), 'F8')
        settings.repeat_hotkey = 'F9'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'; settings.save(path)
            self.assertEqual(WindowSettings.load(path).repeat_hotkey, 'F9')
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertFalse(set(data) & {'repeat_enabled', 'repeat_running', 'repeat_prepared'})

    def test_invalid_binding_only_falls_back_without_erasing_existing_preferences(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'
            for binding in ('S', 'F13', 'Ctrl+T', None, 8):
                with self.subTest(binding=binding):
                    path.write_text(json.dumps({'repeat_hotkey': binding, 'font_size': 17,
                                                'color_theme': 'mint'}), encoding='utf-8')
                    settings = WindowSettings.load(path)
                    self.assertEqual(getattr(settings, 'repeat_hotkey', None), 'F8')
                    self.assertEqual((settings.font_size, settings.color_theme), (17, 'mint'))

    def test_lowercase_binding_is_normalized_when_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'
            path.write_text('{"repeat_hotkey":" f10 "}', encoding='utf-8')
            self.assertEqual(getattr(WindowSettings.load(path), 'repeat_hotkey', None), 'F10')

    def fixture(self):
        owner = transactions.SettingsTransactionTests(methodName='runTest')
        self.addCleanup(owner.doCleanups)
        dialog, settings, path, destroyed, applied = owner.fixture()
        dialog.values['repeat_hotkey'] = transactions.SettingValue('F9')
        return dialog, settings, path, destroyed, applied

    def test_settings_save_accepts_function_key_and_can_retry_invalid_input(self):
        dialog, settings, path, destroyed, applied = self.fixture()
        dialog.values['repeat_hotkey'].set('S')
        original = path.read_bytes()
        with patch('hots_chat.ui.settings_dialog.messagebox.showerror'):
            self.assertFalse(dialog.save())
            self.assertEqual(path.read_bytes(), original)
            dialog.values['repeat_hotkey'].set('f9')
            self.assertTrue(dialog.save())
        self.assertEqual(settings.repeat_hotkey, 'F9')
        self.assertEqual(WindowSettings.load(path).repeat_hotkey, 'F9')


if __name__ == '__main__':unittest.main()
