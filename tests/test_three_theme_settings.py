"""设置页的事务与可达性；只创建屏幕外测试窗口和临时配置。"""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import font as tkfont, ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hots_chat.app import build_controller
from hots_chat.layout import DisplayMetrics
from hots_chat.models import AppEvent, ChatEvent
from hots_chat.paths import AppPaths
from hots_chat.ui.main_window import create_app
from hots_chat.ui.quick_input import _Submission
from hots_chat.ui.settings_dialog import SettingsDialog
from hots_chat.ui.theme import apply_theme
from hots_chat.window_settings import WindowSettings
from tests.test_scaled_popup_layout import OfflineOverlayNative


class SettingValue:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class SettingsTransactionTests(unittest.TestCase):
    def fixture(self, theme='graphite'):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'settings.json'
        settings = WindowSettings(color_theme='graphite', overlay_placement='custom',
                                  overlay_x=260, overlay_y=400, overlay_anchor=(.2, .4),
                                  overlay_bottom_anchor=.55, hotkeys={'focus': 'Ctrl+T'})
        settings.save(path)
        dialog = SettingsDialog.__new__(SettingsDialog)
        names = ('overlay_enabled', 'overlay_original', 'overlay_font_size', 'overlay_alpha',
                 'overlay_width', 'overlay_timeout', 'font_size', 'alpha', 'topmost', 'locked')
        dialog.values = {name: SettingValue(getattr(settings, name)) for name in names}
        dialog.values['color_theme'] = SettingValue(theme)
        dialog.bindings = {name: SettingValue(value) for name, value in settings.hotkeys.items()}
        dialog.compact = SettingValue(False)
        destroyed = []
        dialog.dialog = SimpleNamespace(destroy=lambda: destroyed.append(True))
        applied = []
        dialog.window = SimpleNamespace(settings=settings, apply_settings=lambda: applied.append(settings.color_theme),
                                        save_settings=lambda: settings.save(path))
        return dialog, settings, path, destroyed, applied

    def test_every_theme_can_be_saved_by_canonical_choice_or_visible_label(self):
        for choice, expected in (('moonlight', 'moonlight'), ('graphite', 'graphite'),
                                 ('mint', 'mint'), ('月白蓝', 'moonlight'),
                                 ('石墨紫', 'graphite'), ('雾青', 'mint')):
            with self.subTest(choice=choice):
                dialog, settings, path, destroyed, applied = self.fixture(choice)
                with patch('hots_chat.ui.settings_dialog.messagebox.showerror') as errors:
                    self.assertTrue(dialog.save())
                self.assertEqual(settings.color_theme, expected)
                self.assertIn('"color_theme": "' + expected + '"', path.read_text(encoding='utf-8'))
                self.assertEqual(applied, [expected])
                self.assertEqual(destroyed, [True])
                errors.assert_not_called()

    def test_invalid_number_or_duplicate_hotkey_can_be_corrected_before_retry(self):
        for invalid in ('number', 'duplicate', 'syntax'):
            with self.subTest(invalid=invalid):
                dialog, settings, path, destroyed, applied = self.fixture('moonlight')
                original = path.read_bytes()
                if invalid == 'number':
                    dialog.values['overlay_alpha'].set('nan')
                elif invalid == 'duplicate':
                    dialog.bindings['copy'] = SettingValue('CTRL+t')
                else:
                    dialog.bindings['copy'] = SettingValue('T')
                with patch('hots_chat.ui.settings_dialog.messagebox.showerror'):
                    self.assertFalse(dialog.save())
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(settings.color_theme, 'graphite')
                    self.assertEqual(applied, [])
                    self.assertEqual(destroyed, [])
                    dialog.values['overlay_alpha'].set('.75')
                    dialog.bindings.pop('copy', None)
                    self.assertTrue(dialog.save())
                self.assertEqual(settings.color_theme, 'moonlight')

    def test_disk_failure_restores_mutable_and_position_fields_then_retries(self):
        dialog, settings, path, destroyed, applied = self.fixture('mint')
        previous, original, identity = deepcopy(asdict(settings)), path.read_bytes(), id(settings)

        def preset():
            settings.overlay_placement = 'chat'
            settings.overlay_x = 40
            settings.overlay_y = 600
            settings.overlay_anchor = None
            settings.overlay_bottom_anchor = None

        def apply():
            # Layout application may normalize coordinates and mutable hotkeys.
            settings.x += 1
            settings.hotkeys['copy'] = 'Ctrl+C'
            applied.append(settings.color_theme)

        dialog.window.apply_settings = apply
        with patch('hots_chat.ui.settings_dialog.messagebox.showerror'):
            with patch.object(Path, 'write_text', side_effect=OSError('disk unavailable')):
                self.assertFalse(dialog.save(before_apply=preset))
            self.assertEqual(id(settings), identity)
            self.assertEqual(asdict(settings), previous)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(destroyed, [])
            self.assertTrue(dialog.save(before_apply=preset))
        self.assertEqual(settings.color_theme, 'mint')
        self.assertEqual(settings.overlay_placement, 'chat')

    def test_apply_failure_restores_persisted_configuration_before_retry(self):
        dialog, settings, path, destroyed, applied = self.fixture('mint')
        previous, stored = deepcopy(asdict(settings)), path.read_bytes()
        applications = []

        def apply():
            applications.append(settings.color_theme)
            if len(applications) == 1:
                settings.x = 999
                raise ValueError('UI application failed')
            applied.append(settings.color_theme)

        dialog.window.apply_settings = apply
        with patch('hots_chat.ui.settings_dialog.messagebox.showerror'):
            self.assertFalse(dialog.save())
        self.assertEqual(asdict(settings), previous)
        self.assertEqual(path.read_bytes(), stored)
        self.assertEqual(destroyed, [])
        self.assertTrue(dialog.save())
        self.assertEqual(WindowSettings.load(path).color_theme, 'mint')


class SettingsPageTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.geometry('640x480+10000+10000')
        self.root.deiconify()
        self.addCleanup(self.root.destroy)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'settings.json'
        self.settings = WindowSettings(color_theme='graphite', font_size=32, hotkeys={'focus': 'Ctrl+T'})
        self.settings.save(self.path)
        self.actions = []
        self.window = SimpleNamespace(
            root=self.root, settings=self.settings,
            _layout_metrics=DisplayMetrics((10000, 10000, 10640, 10480), 2),
            controller=SimpleNamespace(account_setup=object(), source=SimpleNamespace(
                reconnect=lambda: self.actions.append('reconnect'))),
            configure_account=lambda: self.actions.append('account'),
            open_cloud_quota=lambda: self.actions.append('quota'),
            stop=lambda: self.actions.append('stop'), start=lambda: self.actions.append('start'),
            choose_process=lambda: self.actions.append('process'),
            apply_settings=lambda: self.actions.append('apply'),
            save_settings=lambda: self.settings.save(self.path))
        apply_theme(self.root, self.settings)

    def widgets(self, parent):
        for child in parent.winfo_children():
            yield child
            yield from self.widgets(child)

    def test_theme_cards_discard_without_mutating_shared_settings_or_disk(self):
        before, stored = deepcopy(asdict(self.settings)), self.path.read_bytes()
        dialog = SettingsDialog(self.window)
        self.assertEqual(set(dialog.theme_cards), {'moonlight', 'graphite', 'mint'})
        dialog.select_theme('moonlight')
        dialog.values['font_size'].set('20')
        self.assertEqual(dialog.values['color_theme'].get(), 'moonlight')
        self.assertEqual(asdict(self.settings), before)
        dialog.cancel()
        self.assertEqual(asdict(self.settings), before)
        self.assertEqual(self.path.read_bytes(), stored)
        self.assertEqual(self.actions, [])

    def test_clicking_palette_swatch_selects_its_theme(self):
        dialog = SettingsDialog(self.window)
        self.root.update()
        card = dialog.theme_cards['moonlight']
        swatch = next(widget for widget in self.widgets(card)
                      if isinstance(widget, tk.Frame) and not widget.winfo_children())
        dialog.select_theme('mint')
        swatch.event_generate('<Button-1>', x=1, y=1)
        self.assertEqual(dialog.values['color_theme'].get(), 'moonlight')
        dialog.cancel()

    def test_each_theme_radio_choice_is_saved_to_temporary_settings(self):
        for theme in ('moonlight', 'graphite', 'mint'):
            with self.subTest(theme=theme):
                dialog = SettingsDialog(self.window)
                choice = next(widget for widget in self.widgets(dialog.theme_cards[theme])
                              if isinstance(widget, tk.Radiobutton))
                choice.invoke()
                self.assertTrue(dialog.save())
                self.assertEqual(self.settings.color_theme, theme)
                self.assertEqual(WindowSettings.load(self.path).color_theme, theme)

    def test_small_high_dpi_window_keeps_actions_inside_and_pages_scrollable(self):
        default_font = tkfont.nametofont('TkDefaultFont', self.root)
        previous_size = default_font.cget('size')
        self.addCleanup(lambda: default_font.configure(size=previous_size))
        default_font.configure(size=32)
        previous_scale = self.root.tk.call('tk', 'scaling')
        self.addCleanup(lambda: self.root.tk.call('tk', 'scaling', previous_scale))
        for width, height, scale in ((960, 540, 1), (1920, 1080, 2)):
            with self.subTest(work_area=(width, height), scale=scale):
                self.root.tk.call('tk', 'scaling', scale * 96 / 72)
                self.window._layout_metrics = DisplayMetrics((10000, 10000, 10000 + width, 10000 + height), scale)
                dialog = SettingsDialog(self.window)
                self.root.update()
                self.assertEqual(len(dialog.notebook.tabs()), 4)
                self.assertTrue(all(dialog.dialog.resizable()))
                self.assertLessEqual(dialog.dialog.winfo_width(), width)
                self.assertLessEqual(dialog.dialog.winfo_height(), height)
                buttons = {str(w.cget('text')): w for w in self.widgets(dialog.dialog)
                           if isinstance(w, ttk.Button)}
                for label in ('取消', '保存设置'):
                    button = buttons[label]
                    self.assertGreaterEqual(button.winfo_rootx(), dialog.dialog.winfo_rootx())
                    self.assertLessEqual(button.winfo_rootx() + button.winfo_width(),
                                         dialog.dialog.winfo_rootx() + dialog.dialog.winfo_width())
                    self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                         dialog.dialog.winfo_rooty() + dialog.dialog.winfo_height())
                for page, canvas in dialog.scroll_canvases.items():
                    dialog.notebook.select(page)
                    self.root.update()
                    self.assertGreater(canvas.bbox('all')[3], canvas.winfo_height())
                    canvas.yview_moveto(1)
                    self.assertGreater(canvas.yview()[0], 0)
                dialog.cancel()

    def test_account_and_connection_actions_remain_reachable(self):
        dialog = SettingsDialog(self.window)
        buttons = {str(w.cget('text')): w for w in self.widgets(dialog.dialog)
                   if isinstance(w, ttk.Button)}
        for label in ('个人阿里云密钥', '查询剩余额度', '手动重新连接',
                      '停止采集', '恢复采集', '选择游戏进程'):
            buttons[label].invoke()
        self.assertEqual(self.actions, ['account', 'quota', 'reconnect', 'stop', 'start', 'process'])
        dialog.cancel()


class RuntimeSettingsTransactionTests(unittest.TestCase):
    """真实历史与浮窗承担运行态断言；系统热键、网络和原生窗口均隔离。"""
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(folder.name)
        self.path = base / 'settings.json'
        controller = build_controller(AppPaths(base / '.env', base / 'language.db', self.path), True)
        root = tk.Tk()
        root.withdraw()
        self.metrics = DisplayMetrics((10000, 10000, 11920, 11020), 1)
        with patch('hots_chat.ui.main_window.screen_bounds', return_value=[self.metrics.work_area]), \
             patch('hots_chat.ui.overlay_window.Win32Window', return_value=OfflineOverlayNative()):
            self.window = create_app(root, controller, WindowSettings(x=10000, y=10000, hotkeys={}))
        self.addCleanup(self.window.close)
        window = self.window
        window._read_display_metrics = lambda: self.metrics
        window.overlay._clock = lambda: 100
        window.input.insert('1.0', '主窗尚未提交的草稿')
        window.vm.apply(AppEvent('chat', 0, ChatEvent('kept', 0, '团队', '队友', '0:01', 'help')))
        window.overlay.sync(window.vm.messages, 0)
        window.quick_input._native = SimpleNamespace(detach_ime=lambda: None,
                                                     foreground_window=lambda: 0)
        window.quick_input._create_window()
        window.quick_input.text.set('快捷输入尚未提交的草稿')
        window.quick_input._pending = _Submission('pending', 0, window.quick_input._revision,
                                                 window.quick_input.text.get(), 'ko')
        window.save_settings()

    def dialog(self):
        window = self.window
        dialog = SettingsDialog.__new__(SettingsDialog)
        names = ('overlay_enabled', 'overlay_original', 'overlay_font_size', 'overlay_alpha',
                 'overlay_width', 'overlay_timeout', 'font_size', 'alpha', 'topmost', 'locked',
                 'color_theme')
        dialog.values = {name: SettingValue(getattr(window.settings, name)) for name in names}
        dialog.bindings = {}
        dialog.compact = SettingValue(False)
        dialog.window = window
        self.destroyed = []
        dialog.dialog = SimpleNamespace(destroy=lambda: self.destroyed.append(True))
        return dialog

    def test_disk_failure_preserves_real_overlay_history_drafts_and_pending_then_retries(self):
        window = self.window
        dialog = self.dialog()
        dialog.values['overlay_enabled'].set(False)
        dialog.values['color_theme'].set('mint')
        previous = deepcopy(asdict(window.settings))
        stored = self.path.read_bytes()
        entries = list(window.overlay.history.entries.items())
        deadline = window.overlay.history.next_expiry
        visible = window.overlay._visible
        pending = window.quick_input._pending
        with patch.object(window, 'save_settings', side_effect=OSError('disk unavailable')), \
             patch('hots_chat.ui.settings_dialog.messagebox.showerror'):
            self.assertFalse(dialog.save())
        self.assertEqual(asdict(window.settings), previous)
        self.assertEqual(self.path.read_bytes(), stored)
        self.assertEqual(list(window.overlay.history.entries.items()), entries)
        self.assertEqual(window.overlay.history.next_expiry, deadline)
        self.assertEqual(window.overlay._visible, visible)
        self.assertEqual(window.input.get('1.0', 'end-1c'), '主窗尚未提交的草稿')
        self.assertEqual(list(window.vm.messages), ['kept'])
        self.assertEqual(window.quick_input.text.get(), '快捷输入尚未提交的草稿')
        self.assertIs(window.quick_input._pending, pending)
        self.assertEqual(self.destroyed, [])
        self.assertTrue(dialog.save())
        self.assertFalse(window.settings.overlay_enabled)
        self.assertEqual(list(window.overlay.history.entries), [])
        self.assertFalse(window.overlay._visible)

    def test_preset_disk_failure_does_not_move_existing_overlay(self):
        window = self.window
        window.settings.overlay_placement = 'custom'
        window.settings.overlay_x, window.settings.overlay_y = 10260, 10400
        window.settings.overlay_anchor = None
        window.settings.overlay_bottom_anchor = None
        window.overlay.apply_settings(self.metrics)
        window.root.update_idletasks()
        window.save_settings()
        dialog = self.dialog()
        previous = deepcopy(asdict(window.settings))
        geometry = window.overlay.window.geometry()
        entry = next(iter(window.overlay.history.entries.values()))
        with patch.object(window, 'save_settings', side_effect=OSError('disk unavailable')), \
             patch('hots_chat.ui.settings_dialog.messagebox.showerror'):
            self.assertFalse(dialog.save(before_apply=window.overlay.use_chat_preset))
        self.assertEqual(asdict(window.settings), previous)
        self.assertEqual(window.overlay.window.geometry(), geometry)
        self.assertIs(next(iter(window.overlay.history.entries.values())), entry)

    def test_successful_preset_persists_actual_layout_normalization(self):
        window = self.window
        dialog = self.dialog()
        dialog.values['overlay_font_size'].set('32')
        dialog.values['overlay_width'].set('900')
        dialog.values['font_size'].set('32')
        dialog.compact.set(True)
        self.assertTrue(dialog.save(before_apply=window.overlay.use_chat_preset))
        stored = WindowSettings.load(self.path)
        for name in ('x', 'y', 'main_anchor', 'overlay_x', 'overlay_y', 'overlay_anchor',
                     'overlay_bottom_anchor', 'overlay_width', 'font_size', 'overlay_font_size'):
            self.assertEqual(getattr(stored, name), getattr(window.settings, name), name)
        self.assertEqual(stored.overlay_placement, 'chat')

    def test_message_expiring_during_failed_save_is_not_revived(self):
        window = self.window
        dialog = self.dialog()
        dialog.values['overlay_enabled'].set(False)
        deadline = 100 + window.settings.overlay_timeout
        self.assertEqual(window.overlay.history.next_expiry, deadline)

        def fail_after_expiry():
            window.overlay._clock = lambda: deadline + 1
            raise OSError('disk unavailable')

        with patch.object(window, 'save_settings', side_effect=fail_after_expiry), \
             patch('hots_chat.ui.settings_dialog.messagebox.showerror'):
            self.assertFalse(dialog.save())
        self.assertTrue(window.settings.overlay_enabled)
        self.assertEqual(list(window.overlay.history.entries), [])
        self.assertIsNone(window.overlay.history.next_expiry)
        self.assertFalse(window.overlay._visible)
        window.overlay.sync(window.vm.messages, 0)
        self.assertEqual(list(window.overlay.history.entries), [], '已到期消息不能因回滚重新取得展示资格')

    def test_apply_failure_restores_real_overlay_and_original_disk(self):
        window = self.window
        dialog = self.dialog()
        dialog.values['overlay_enabled'].set(False)
        previous = deepcopy(asdict(window.settings))
        stored = self.path.read_bytes()
        entries = list(window.overlay.history.entries.items())
        deadline = window.overlay.history.next_expiry
        original_apply = window.apply_settings
        calls = []

        def fail_after_apply():
            original_apply()
            calls.append(True)
            if len(calls) == 1:
                raise ValueError('UI application failed')

        with patch.object(window, 'apply_settings', side_effect=fail_after_apply), \
             patch('hots_chat.ui.settings_dialog.messagebox.showerror'):
            self.assertFalse(dialog.save())
        self.assertEqual(asdict(window.settings), previous)
        self.assertEqual(self.path.read_bytes(), stored)
        self.assertEqual(list(window.overlay.history.entries.items()), entries)
        self.assertEqual(window.overlay.history.next_expiry, deadline)
        self.assertTrue(window.overlay._visible)


if __name__ == '__main__':
    unittest.main()
