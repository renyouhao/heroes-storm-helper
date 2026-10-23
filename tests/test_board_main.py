from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from hots_chat.ui.main_window import MainWindow
from hots_chat.window_settings import WindowSettings


class BoardMainTests(unittest.TestCase):
    def window(self):
        window = MainWindow.__new__(MainWindow)
        window.closed=False
        window.settings=WindowSettings()
        window._talent_runtime_enabled=True
        window.board_helper=SimpleNamespace(set_enabled=Mock(return_value=True),set_suspended=Mock(),busy=False)
        window.board_panel=SimpleNamespace(apply_settings=Mock(),update_status=Mock())
        window.talent_helper=SimpleNamespace(set_suspended=Mock())
        window._layout_metrics=None
        window.save_settings=Mock()
        return window

    def test_preference_defaults_off_and_rejects_non_boolean_without_losing_theme(self):
        self.assertFalse(WindowSettings().board_broadcast_enabled)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'preferences.json'
            path.write_text('{"board_broadcast_enabled":"yes","font_size":15}',encoding='utf8')
            settings=WindowSettings.load(path)
            self.assertFalse(settings.board_broadcast_enabled)
            self.assertEqual(settings.font_size,15)

    def test_enabling_requires_saved_preference_and_demo_never_starts(self):
        window=self.window()
        window.save_settings.side_effect=OSError('fixture')
        with self.assertRaises(OSError):window.apply_board_config(True)
        window.board_helper.set_enabled.assert_not_called()
        self.assertFalse(window.settings.board_broadcast_enabled)
        window.save_settings.side_effect=None
        window._talent_runtime_enabled=False
        window.apply_board_config(True)
        window.board_helper.set_enabled.assert_called_with(False)

    def test_disabled_gate_closes_before_failing_save(self):
        window=self.window()
        window.settings.board_broadcast_enabled=True
        window.save_settings.side_effect=OSError('fixture')
        with self.assertRaises(OSError):window.apply_board_config(False)
        window.board_helper.set_enabled.assert_called_with(False)
        self.assertFalse(window.settings.board_broadcast_enabled)

    def test_board_sender_suspends_talent_but_not_itself(self):
        window=self.window()
        window.board_helper.busy=True
        window._sync_talent_gate()
        window.talent_helper.set_suspended.assert_called_with(True)
        window.board_helper.set_suspended.assert_called_with(False)
        window._update_installing=True
        window._sync_talent_gate()
        window.board_helper.set_suspended.assert_called_with(True)


if __name__=='__main__':unittest.main()
