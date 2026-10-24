"""启动保护回归；不打开真实弹窗、WebView、Tk 或游戏。"""
from pathlib import Path
import os
import runpy
import stat
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hots_chat import startup

REAL_SHOW_MESSAGE = startup._show_startup_message


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.show = patch.object(startup, '_show_startup_message').start()
        self.addCleanup(patch.stopall)

    def frozen(self, root):
        return patch.multiple(sys, platform='win32', frozen=True, _MEIPASS=str(root), create=True)

    def bundle(self, root):
        paths = [root / name for name in startup.BUNDLED_MANAGED_DLLS]
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'library content must not change')
        return paths

    def test_all_three_marked_libraries_are_prepared_before_application_import(self):
        with TemporaryDirectory(prefix='启动 空格 ') as tmp:
            root = Path(tmp)
            libraries = self.bundle(root)
            for path in libraries:
                Path(str(path) + ':Zone.Identifier').write_text('[ZoneTransfer]\nZoneId=3\n')
            other = root / 'other.dll'
            other.write_bytes(b'not ours')
            other_marker = Path(str(other) + ':Zone.Identifier')
            other_marker.write_text('[ZoneTransfer]\nZoneId=3\n')
            def import_main():
                self.assertTrue(all(not Path(str(path) + ':Zone.Identifier').exists() for path in libraries))
                self.assertTrue(all(path.read_bytes() == b'library content must not change' for path in libraries))
                self.assertTrue(other_marker.exists())
                return lambda: 0
            with self.frozen(root), patch.object(startup, '_write_startup_log') as log:
                self.assertEqual(startup.run(import_main), 0)
            log.assert_not_called()
            self.show.assert_not_called()

    def test_no_download_marker_and_missing_optional_files_are_noops(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.bundle(root)
            with self.frozen(root):
                self.assertEqual(startup.prepare_bundled_runtime(), ())
            with self.frozen(root / 'missing'):
                self.assertEqual(startup.prepare_bundled_runtime(), ())

    def test_source_and_non_windows_modes_do_not_touch_files(self):
        with patch.object(startup, '_bundled_file', side_effect=AssertionError('unexpected file access')):
            with patch.object(sys, 'frozen', False, create=True), patch.object(sys, 'platform', 'win32'):
                self.assertEqual(startup.prepare_bundled_runtime(), ())
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'platform', 'linux'):
                self.assertEqual(startup.prepare_bundled_runtime(), ())

    def test_permission_failure_reports_full_directory_guidance_and_preserves_cause(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.bundle(root)
            failure = PermissionError('read-only library')
            with self.frozen(root), patch.object(Path, 'unlink', side_effect=failure):
                with self.assertRaises(startup.StartupPreparationError) as caught:
                    startup.prepare_bundled_runtime()
            self.assertIs(caught.exception.__cause__, failure)
            with self.frozen(root), patch.object(Path, 'unlink', side_effect=failure), \
                    patch.dict(os.environ, {'LOCALAPPDATA': str(root / 'local')}):
                self.assertEqual(startup.run(lambda: self.fail('must not import app')), 2)
            message = self.show.call_args.args[0]
            self.assertIn('完整应用目录', message)
            self.assertIn('只解除 EXE', message)
            log = (root / 'local' / 'HotsChatHelper' / 'startup.log').read_text(encoding='utf-8')
            self.assertIn('PermissionError', log)
            self.assertIn('StartupPreparationError', log)

    def test_bundle_escape_is_rejected_without_touching_download_marker(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(Path, 'resolve', side_effect=[root, root.parent / 'outside']), \
                    patch.object(Path, 'unlink') as unlink:
                with self.assertRaisesRegex(startup.StartupPreparationError, '超出'):
                    startup._bundled_file(root, startup.BUNDLED_MANAGED_DLLS[0])
            unlink.assert_not_called()

    def test_reparse_parent_and_bundle_root_are_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            libraries = self.bundle(root)
            original_lstat = Path.lstat
            for parent in (libraries[0].parent, root):
                def lstat(path, *args, **kwargs):
                    actual = original_lstat(path, *args, **kwargs)
                    if path == parent:
                        return SimpleNamespace(st_mode=actual.st_mode,
                                               st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
                    return actual
                with self.subTest(parent=parent), self.frozen(root), patch.object(Path, 'lstat', lstat):
                    with self.assertRaisesRegex(startup.StartupPreparationError, '目录联接'):
                        startup.prepare_bundled_runtime()

    def test_import_and_main_errors_are_reported_without_a_console(self):
        for factory in (Mock(side_effect=ImportError('dependency failed')),
                        lambda: Mock(side_effect=RuntimeError('runtime failed'))):
            with self.subTest(factory=factory), patch.object(startup, 'prepare_bundled_runtime'), \
                    patch.object(startup, '_write_startup_log', return_value=Path('启动日志.log')), \
                    patch.object(sys, 'stderr', None):
                self.assertEqual(startup.run(factory), 2)
                message = self.show.call_args.args[0]
                self.assertIn('启动日志.log', message)
                self.assertNotIn('dependency failed', message)
                self.assertNotIn('runtime failed', message)

    def test_normal_system_exit_is_not_misreported_as_failure(self):
        with patch.object(startup, 'prepare_bundled_runtime'), \
                patch.object(startup, '_write_startup_log') as log:
            with self.assertRaises(SystemExit) as caught:
                startup.run(lambda: Mock(side_effect=SystemExit(0)))
        self.assertEqual(caught.exception.code, 0)
        log.assert_not_called()
        self.show.assert_not_called()

    def test_log_has_exception_chain_no_locals_and_overwrites_with_a_fixed_limit(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {'LOCALAPPDATA': tmp}):
            private_local = 'must not record frame locals'
            try:
                try:
                    raise ValueError('original CLR failure')
                except ValueError as failure:
                    raise RuntimeError('desktop context') from failure
            except RuntimeError as error:
                path = startup.report_startup_failure(error)
            content = path.read_text(encoding='utf-8')
            self.assertIn('original CLR failure', content)
            self.assertIn('desktop context', content)
            self.assertNotIn(private_local, content)
            for text in ('中文', 'a中文', '中文abc', 'a中文b字'):
                startup.report_startup_failure(RuntimeError(text * startup.STARTUP_LOG_LIMIT))
                self.assertLessEqual(path.stat().st_size, startup.STARTUP_LOG_LIMIT)
                path.read_text(encoding='utf-8')
            startup.report_startup_failure(RuntimeError('latest only'))
            self.assertNotIn('original CLR failure', path.read_text(encoding='utf-8'))
            self.assertLess(path.stat().st_size, 1000)

    def test_log_write_failure_still_displays_recovery_steps(self):
        with patch.object(startup, '_write_startup_log', side_effect=OSError('disk full')):
            self.assertIsNone(startup.report_startup_failure(RuntimeError('sensitive internal detail')))
        message = self.show.call_args.args[0]
        self.assertIn('未能写入', message)
        self.assertIn('_internal', message)
        self.assertNotIn('sensitive internal detail', message)

    def test_message_box_is_independent_of_tk_and_webview(self):
        with patch.object(sys, 'platform', 'win32'), patch.object(startup.ctypes, 'windll', create=True) as windll:
            REAL_SHOW_MESSAGE('安全提示')
            windll.user32.MessageBoxW.assert_called_once_with(None, '安全提示', '风暴英雄助手 · 启动失败', 0x10)

    def test_caught_desktop_failure_is_reported_once_with_its_original_chain(self):
        from hots_chat.app import main
        from hots_chat.desktop import DesktopError
        try:
            try:
                raise RuntimeError('original assembly failure')
            except RuntimeError as failure:
                raise DesktopError('安全的桌面启动提示') from failure
        except DesktopError as failure:
            error = failure
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {'LOCALAPPDATA': tmp}), \
                patch('hots_chat.display_runtime.enable_process_dpi_awareness'), \
                patch('hots_chat.desktop.run_desktop', side_effect=error):
            self.assertEqual(main([]), 2)
            log = Path(tmp) / 'HotsChatHelper' / 'startup.log'
            self.assertIn('original assembly failure', log.read_text(encoding='utf-8'))
        self.show.assert_called_once()
        self.assertIn('安全的桌面启动提示', self.show.call_args.args[0])

    def test_desktop_keeps_original_failure_without_claiming_webview2_is_missing(self):
        from hots_chat import desktop
        error = RuntimeError('original CLR failure')
        webview = SimpleNamespace(create_window=Mock(side_effect=error))
        mutex, client = Mock(), Mock()
        with patch.dict(sys.modules, {'webview': webview}), \
                patch.object(desktop, 'frontend_entry', return_value=Path('index.html')), \
                patch.object(desktop, 'webview2_available', return_value=True), \
                patch.object(desktop, 'SingleInstance', return_value=mutex), \
                patch.object(desktop, 'start_native', return_value=client):
            with self.assertRaises(desktop.DesktopError) as caught:
                desktop.run_desktop()
        self.assertIs(caught.exception.__cause__, error)
        self.assertIn('启动日志', str(caught.exception))
        self.assertNotIn('请确认已安装', str(caught.exception))
        client.close.assert_called_once()
        mutex.close.assert_called_once()

    def test_frozen_child_exits_before_importing_application_bootstrap(self):
        launcher = Path(__file__).resolve().parents[1] / 'packaging' / 'launcher.py'
        with patch('multiprocessing.freeze_support', side_effect=SystemExit(0)), \
                patch.object(startup, 'run') as run:
            with self.assertRaises(SystemExit):
                runpy.run_path(str(launcher), run_name='__main__')
            run.assert_not_called()

    def test_main_launcher_freeze_support_precedes_bootstrap(self):
        launcher = Path(__file__).resolve().parents[1] / 'packaging' / 'launcher.py'
        calls = []
        with patch('multiprocessing.freeze_support', side_effect=lambda: calls.append('freeze')), \
                patch.object(startup, 'run', side_effect=lambda: calls.append('run') or 0):
            with self.assertRaises(SystemExit) as caught:
                runpy.run_path(str(launcher), run_name='__main__')
        self.assertEqual(caught.exception.code, 0)
        self.assertEqual(calls, ['freeze', 'run'])


if __name__ == '__main__':
    unittest.main()
