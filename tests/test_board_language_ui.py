"""播报语言配置的持久、IPC、原生界面与真实 ZIP 验收；没有游戏边界。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import tkinter as tk
import unittest
from unittest.mock import Mock
import zipfile

from hots_chat.desktop_protocol import ProtocolError, validate_command
from hots_chat.desktop_runtime import TkRuntime
from hots_chat.diagnostics import DiagnosticRecorder
from hots_chat.layout import DisplayMetrics
from hots_chat.ui.board_panel import BoardPanel
from hots_chat.ui.main_window import MainWindow
from hots_chat.window_settings import WindowSettings


def language_window(settings=None,diagnostics=None):
    window=MainWindow.__new__(MainWindow)
    window.settings=settings or WindowSettings()
    window.controller=NS(diagnostics=diagnostics)
    window.board_helper=NS(set_language=Mock(return_value=True))
    window.board_panel=NS(apply_settings=Mock())
    window._layout_metrics=DisplayMetrics((10000,10000,11920,11040),1)
    window.save_settings=Mock()
    return window


class BoardLanguageSettingsTests(unittest.TestCase):
    def test_default_roundtrip_and_invalid_language_preserve_other_preferences(self):
        self.assertEqual(WindowSettings().board_output_language,'zh')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'prefs.json'
            WindowSettings(board_output_language='en',font_size=19).save(path)
            loaded=WindowSettings.load(path)
            self.assertEqual(loaded.board_output_language,'en')
            self.assertEqual(loaded.font_size,19)
            for invalid in ('ko',None,True,{'language':'en'}):
                path.write_text(json.dumps({'board_output_language':invalid,'font_size':19}),encoding='utf-8')
                loaded=WindowSettings.load(path)
                self.assertEqual(loaded.board_output_language,'zh')
                self.assertEqual(loaded.font_size,19)

    def test_selection_saves_and_applies_one_correlated_request(self):
        recorder=Mock()
        window=language_window(diagnostics=recorder)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'prefs.json'
            window.save_settings.side_effect=lambda:window.settings.save(path)
            self.assertTrue(window.apply_board_language('en'))
            self.assertEqual(WindowSettings.load(path).board_output_language,'en')
        self.assertEqual(window.board_helper.set_language.call_args.args,('en',))
        request=window.board_helper.set_language.call_args.kwargs['request_ref']
        self.assertTrue(request)
        events=[call.kwargs for call in recorder.record.call_args_list]
        self.assertEqual([event['outcome'] for event in events],['started','saved','applied'])
        self.assertTrue(all(event['request_ref']==request for event in events))
        self.assertTrue(all(event['target_language']=='en' for event in events))

    def test_same_language_and_rejected_language_have_no_side_effects(self):
        window=language_window()
        window.apply_board_language('zh')
        for invalid in ('ko',None,True):
            with self.subTest(language=invalid),self.assertRaises(ValueError):window.apply_board_language(invalid)
        window._update_installing=True
        with self.assertRaises(ValueError):window.apply_board_language('en')
        window.board_helper.set_language.assert_not_called()
        window.save_settings.assert_not_called()

    def test_save_failure_keeps_runtime_language_and_hides_private_exception(self):
        window=language_window()
        window.save_settings.side_effect=OSError('private-storage-path')
        with self.assertRaises(OSError) as failure:window.apply_board_language('en')
        self.assertNotIn('private-',str(failure.exception))
        self.assertEqual(window.settings.board_output_language,'zh')
        window.board_helper.set_language.assert_not_called()

    def test_runtime_rejection_restores_saved_settings_and_previous_language(self):
        window=language_window()
        window.board_helper.set_language.side_effect=[False,True]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'prefs.json'
            window.save_settings.side_effect=lambda:window.settings.save(path)
            with self.assertRaises(OSError):window.apply_board_language('en')
            self.assertEqual(WindowSettings.load(path).board_output_language,'zh')
        self.assertEqual(window.settings.board_output_language,'zh')
        self.assertEqual([call.args[0] for call in window.board_helper.set_language.call_args_list],['en','zh'])

    def test_diagnostic_failure_does_not_block_configuration(self):
        window=language_window(diagnostics=NS(record=Mock(side_effect=OSError('logger failed'))))
        self.assertTrue(window.apply_board_language('en'))
        self.assertEqual(window.settings.board_output_language,'en')

    def test_real_zip_retains_language_success_failure_and_rollback_associations(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder=DiagnosticRecorder(Path(directory)/'logs',enabled=True)
            try:
                window=language_window(diagnostics=recorder)
                window.apply_board_language('en')
                window.save_settings.side_effect=OSError('private-save-path')
                with self.assertRaises(OSError):window.apply_board_language('zh')
                window.save_settings.side_effect=None
                window.board_helper.set_language.side_effect=[False,True]
                with self.assertRaises(OSError):window.apply_board_language('zh')
                window.save_settings.side_effect=[None,OSError('private-rollback-path')]
                window.board_helper.set_language.side_effect=[False,False]
                with self.assertRaises(OSError):window.apply_board_language('zh')
                target=recorder.export_report(Path(directory)/'language.zip')
                with zipfile.ZipFile(target) as archive:
                    rows=[json.loads(line) for name in archive.namelist() if '.log' in name
                          for line in archive.read(name).decode('utf-8').splitlines() if line]
            finally:recorder.close()
        rows=[row for row in rows if row.get('source')=='broadcast' and row.get('operation')=='language']
        self.assertEqual([row['target_language'] for row in rows[:3]],['en','en','en'])
        first_request=rows[0]['request_ref']
        self.assertEqual([row['outcome'] for row in rows if row['request_ref']==first_request],['started','saved','applied'])
        self.assertTrue({'save_failed','apply_failed','previous_language_retained','previous_language_restored',
                         'rollback_save_failed','rollback_incomplete'}
                        <={row['reason'] for row in rows})
        self.assertNotIn('private-',json.dumps(rows))

    def test_language_protocol_routes_only_explicit_supported_selection(self):
        from test_desktop_runtime import fake_window
        for language in ('zh','en'):
            self.assertEqual(validate_command('board_language',{'language':language}),{'language':language})
        for payload in ({},{'language':'ko'},{'language':True},{'language':'en','enabled':True}):
            with self.subTest(payload=payload),self.assertRaises(ProtocolError):validate_command('board_language',payload)
        window=fake_window()
        window.apply_board_language=Mock(side_effect=lambda language:setattr(window.settings,'board_output_language',language))
        runtime=TkRuntime(window)
        self.addCleanup(runtime.actions.close)
        response=runtime.dispatch('board_language',{'language':'en'})
        window.apply_board_language.assert_called_once_with('en')
        self.assertIn('English',response['message'])
        self.assertEqual(runtime.snapshot()['helpers']['board']['outputLanguage'],'en')


class BoardLanguagePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):cls.root.destroy()

    def panel(self,settings=None,callback=None):
        settings=settings or WindowSettings()
        panel=BoardPanel(self.root,settings=settings,metrics=DisplayMetrics((10000,10000,11920,11040)),
                         on_toggle=Mock(),on_language=callback)
        self.addCleanup(panel.destroy)
        return panel

    def test_english_preference_initializes_label_and_preview_without_triggering_change(self):
        callback=Mock()
        panel=self.panel(WindowSettings(board_output_language='en'),callback)
        self.assertEqual(panel.language.get(),'English')
        self.assertIn('[Ally]',panel.preview.get())
        self.assertIn('Hero damage',panel.preview.get())
        callback.assert_not_called()
        self.assertEqual(str(panel.language_choice.cget('state')),'readonly')

    def test_native_selection_uses_the_same_saved_configuration_entry(self):
        window=language_window()
        panel=self.panel(window.settings,window.apply_board_language)
        window.board_panel=panel
        panel.language.set('English')
        panel._change_language()
        self.assertEqual(window.settings.board_output_language,'en')
        self.assertIn('Hero damage',panel.preview.get())
        self.assertIn('已保存',panel.language_feedback.get())
        window.save_settings.assert_called_once()

    def test_native_save_failure_restores_selection_and_shows_local_feedback(self):
        panel=self.panel(callback=Mock(side_effect=OSError('private-path')))
        panel.language.set('English')
        panel._change_language()
        self.assertEqual(panel.language.get(),'中文')
        self.assertIn('保留原选择',panel.language_feedback.get())
        self.assertIn('英雄伤害',panel.preview.get())
        self.assertNotIn('private-',panel.language_feedback.get())

    def test_destroy_releases_page_variables_on_the_ui_thread(self):
        panel=self.panel(callback=Mock())
        panel.destroy()
        self.assertIsNone(panel.language)
        self.assertIsNone(panel.language_feedback)
        self.assertIsNone(panel.preview)


if __name__=='__main__':unittest.main()
