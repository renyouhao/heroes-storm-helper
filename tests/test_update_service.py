"""升级后台任务只用受控函数，无网络，无现用目录写入。"""
import threading
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from hots_chat.update_service import UpdateService, UpdateStatus
from hots_chat import __version__
from hots_chat.updater import download_update, discard_update
from tests.test_updater import Response, archive_fixture, check_fixture, release_payload


class UpdateServiceTests(unittest.TestCase):
    def owned_stage(self, root):
        data = archive_fixture()
        release = check_fixture(release_payload(data)).release
        stage = download_update(release, staging_root=root,
                                opener=lambda request, **kwargs: Response(data, request.full_url))
        return release, stage

    def ready_service(self, root, stage, **kwargs):
        service = UpdateService(check=lambda version: SimpleNamespace(available=True, release=stage.release),
                                download=lambda *args, **kw: stage, staging_root=root, **kwargs)
        self.addCleanup(service.close)
        service.check(); service.thread.join(2)
        service.download(); service.thread.join(2)
        return service

    def test_idle_has_no_thread_network_or_directory_access(self):
        calls = []
        service = UpdateService(check=lambda version:calls.append(version))
        self.addCleanup(service.close)
        self.assertIsNone(service.thread)
        self.assertEqual(service.latest.state, 'idle')
        self.assertEqual(calls, [])

    def test_startup_check_is_background_once_and_never_downloads(self):
        started, finish = threading.Event(), threading.Event()
        calls, downloads = [], []
        def check(version):
            calls.append((version, threading.current_thread()))
            started.set(); finish.wait(2)
            return SimpleNamespace(available=True, release=SimpleNamespace(version='99.0'))
        service = UpdateService(check=check, download=lambda *args, **kw: downloads.append(args))
        self.addCleanup(service.close); self.addCleanup(finish.set)
        self.assertTrue(service.check_on_startup())
        self.assertTrue(started.wait(2))
        self.assertFalse(service.check_on_startup())
        self.assertFalse(service.check())
        self.assertFalse(service.has_available_update)
        finish.set(); service.thread.join(2)
        self.assertTrue(service.has_available_update)
        self.assertFalse(service.check_on_startup())
        self.assertEqual(len(calls), 1)
        self.assertIsNot(calls[0][1], threading.current_thread())
        self.assertEqual(downloads, [])

    def test_startup_check_defers_to_manual_work_and_does_not_discard_a_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, stage = self.owned_stage(root)
            service = self.ready_service(root, stage)
            status, worker = service.latest, service.thread
            self.assertFalse(service.check_on_startup())
            self.assertIs(service.latest, status)
            self.assertIs(service.thread, worker)
            self.assertTrue(stage.workspace.exists())

    def test_manual_check_started_first_is_not_duplicated_by_startup(self):
        started, finish = threading.Event(), threading.Event()
        calls = []
        def check(version):
            calls.append(version)
            started.set(); finish.wait(2)
            return SimpleNamespace(available=False, release=object())
        service = UpdateService(check=check)
        self.addCleanup(service.close); self.addCleanup(finish.set)
        self.assertTrue(service.check())
        self.assertTrue(started.wait(2))
        self.assertFalse(service.check_on_startup())
        finish.set(); service.thread.join(2)
        self.assertFalse(service.check_on_startup())
        self.assertEqual(len(calls), 1)
        self.assertFalse(service.has_available_update)

    def test_failed_startup_check_is_silent_retryable_manually_and_does_not_light_hint(self):
        calls = []
        def check(version):
            calls.append(version)
            raise OSError('offline')
        service = UpdateService(check=check)
        self.addCleanup(service.close)
        service.check_on_startup(); service.thread.join(2)
        self.assertEqual(service.latest.state, 'error')
        self.assertFalse(service.has_available_update)
        self.assertFalse(service.check_on_startup())
        self.assertTrue(service.check())
        service.thread.join(2)
        self.assertEqual(len(calls), 2)

    def test_known_update_survives_recheck_failure_and_cancel_until_successful_current(self):
        release = SimpleNamespace(version='99.0')
        result = [SimpleNamespace(available=True, release=release)]
        started, finish = threading.Event(), threading.Event()
        def check(version):
            value = result[0]
            if isinstance(value, Exception):raise value
            if value == 'wait':
                started.set(); finish.wait(2)
                return SimpleNamespace(available=False, release=release)
            return value
        service = UpdateService(check=check)
        self.addCleanup(service.close); self.addCleanup(finish.set)
        service.check_on_startup(); service.thread.join(2)
        self.assertTrue(service.has_available_update)
        result[0] = OSError('offline')
        service.check(); service.thread.join(2)
        self.assertEqual(service.latest.state, 'error')
        self.assertTrue(service.has_available_update)
        result[0] = 'wait'
        service.check(); self.assertTrue(started.wait(2))
        self.assertTrue(service.has_available_update)
        service.cancel(); finish.set(); service.thread.join(2)
        self.assertTrue(service.has_available_update)
        result[0] = SimpleNamespace(available=False, release=None)
        service.check(); service.thread.join(2)
        self.assertTrue(service.has_available_update, '没有发行信息不能否定已确认的新版')
        result[0] = SimpleNamespace(available=False, release=release)
        service.check(); service.thread.join(2)
        self.assertFalse(service.has_available_update)

    def test_close_before_or_during_startup_prevents_network_or_late_hint(self):
        service = UpdateService(check=lambda version:self.fail('closed service must not check'))
        service.close()
        self.assertFalse(service.check_on_startup())
        self.assertIsNone(service.thread)
        started, finish = threading.Event(), threading.Event()
        def check(version):
            started.set(); finish.wait(2)
            return SimpleNamespace(available=True, release=object())
        service = UpdateService(check=check)
        self.addCleanup(service.close); self.addCleanup(finish.set)
        service.check_on_startup(); self.assertTrue(started.wait(2))
        previous = service.latest
        service.close(wait=False); finish.set(); service.thread.join(2)
        self.assertIs(service.latest, previous)
        self.assertFalse(service.has_available_update)

    def test_check_download_and_progress_have_distinct_states(self):
        release = SimpleNamespace(version='1.2.0', notes='新增内容')
        staged = SimpleNamespace(bundle_dir='fake')
        calls = []
        def check(version):
            calls.append(('check', version))
            return SimpleNamespace(available=True, release=release)
        def download(value, **kwargs):
            calls.append(('download', value))
            kwargs['progress'](30, 100)
            return staged
        service = UpdateService(check=check, download=download)
        self.addCleanup(service.close)
        self.assertTrue(service.check())
        service.thread.join(2)
        self.assertEqual(service.latest.state, 'available')
        self.assertTrue(service.download())
        service.thread.join(2)
        self.assertEqual(service.latest.state, 'downloaded')
        self.assertIs(service.latest.staged, staged)
        self.assertEqual(calls, [('check', __version__), ('download', release)])

    def test_cancelled_late_check_cannot_overwrite_status_or_start_download(self):
        started, release = threading.Event(), threading.Event()
        def check(version):
            started.set(); release.wait(2)
            return SimpleNamespace(available=True, release=SimpleNamespace(version='9.0'))
        service = UpdateService(check=check)
        self.addCleanup(service.close); self.addCleanup(release.set)
        service.check(); self.assertTrue(started.wait(2))
        service.cancel()
        release.set(); service.thread.join(2)
        self.assertEqual(service.latest.state, 'cancelled')
        self.assertFalse(service.download())

    def test_close_cancels_download_and_rejects_further_actions(self):
        started, finish = threading.Event(), threading.Event()
        cancellation = []
        def download(value, **kwargs):
            started.set(); finish.wait(2)
            cancellation.append(kwargs['cancel'].is_set())
            return None
        service = UpdateService(check=lambda version:SimpleNamespace(available=True, release=object()),
                                download=download)
        service.check(); service.thread.join(2); service.download()
        self.assertTrue(started.wait(2))
        service.close(wait=False); finish.set(); service.thread.join(2)
        self.assertEqual(cancellation, [True])
        self.assertFalse(service.check())
        self.assertFalse(service.download())

    def test_failed_network_is_retryable_and_does_not_offer_installation(self):
        def fail(version):raise OSError('fake network failure')
        service = UpdateService(check=fail)
        self.addCleanup(service.close)
        service.check(); service.thread.join(2)
        self.assertEqual(service.latest.state, 'error')
        self.assertIsNone(service.latest.staged)
        self.assertTrue(service.check())
        service.thread.join(2)

    def test_install_runs_only_after_verified_download_and_returns_ready_to_close(self):
        calls = []
        service = UpdateService(check=lambda version:SimpleNamespace(available=True,release=object()),
                                download=lambda release,**kwargs:object())
        self.addCleanup(service.close)
        install = lambda staged:calls.append(staged)
        self.assertFalse(service.install(install))
        service.check(); service.thread.join(2)
        self.assertFalse(service.install(install))
        service.download(); service.thread.join(2)
        staged = service.latest.staged
        self.assertTrue(service.install(install))
        service.thread.join(2)
        self.assertEqual(calls, [staged])
        self.assertEqual(service.latest.state, 'restarting')

    def test_rechecking_reclaims_previous_owned_stage_on_background_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, stage = self.owned_stage(root)
            started, finish = threading.Event(), threading.Event()
            cleanup_threads = []

            def discard(staged, **kwargs):
                cleanup_threads.append(threading.current_thread())
                started.set(); finish.wait(2)
                return discard_update(staged, **kwargs)

            service = self.ready_service(root, stage, discard=discard)
            self.addCleanup(finish.set)
            self.assertTrue(service.check())
            self.assertTrue(started.wait(2))
            self.assertTrue(stage.workspace.exists())
            self.assertNotEqual(cleanup_threads, [threading.current_thread()])
            finish.set(); service.thread.join(2); service.cleanup_thread.join(2)
            self.assertFalse(stage.workspace.exists())

    def test_cancelled_late_success_is_discarded_without_publishing_downloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release, stage = self.owned_stage(root)
            started, finish = threading.Event(), threading.Event()

            def download(*args, **kwargs):
                started.set(); finish.wait(2)
                return stage

            service = UpdateService(check=lambda version: SimpleNamespace(available=True, release=release),
                                    download=download, staging_root=root)
            self.addCleanup(service.close); self.addCleanup(finish.set)
            service.check(); service.thread.join(2)
            service.download(); self.assertTrue(started.wait(2))
            service.cancel(); finish.set(); service.thread.join(2)
            service.cleanup_thread.join(2)
            self.assertEqual(service.latest.state, 'cancelled')
            self.assertFalse(stage.workspace.exists())

    def test_close_without_wait_schedules_cleanup_and_returns_before_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, stage = self.owned_stage(root)
            started, finish = threading.Event(), threading.Event()

            def discard(staged, **kwargs):
                started.set(); finish.wait(2)
                return discard_update(staged, **kwargs)

            service = self.ready_service(root, stage, discard=discard)
            self.addCleanup(finish.set)
            service.close(wait=False)
            self.assertTrue(started.wait(2))
            self.assertTrue(stage.workspace.exists())
            finish.set(); service.cleanup_thread.join(2)
            self.assertFalse(stage.workspace.exists())

    def test_preparing_transfers_stage_and_close_cannot_remove_helpers_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, stage = self.owned_stage(root)
            service = self.ready_service(root, stage)
            started, finish = threading.Event(), threading.Event()

            def prepare(staged):
                started.set(); finish.wait(2)

            self.addCleanup(finish.set)
            service.install(prepare); self.assertTrue(started.wait(2))
            self.assertFalse(service.cancel())
            service.close(wait=False); finish.set(); service.thread.join(2)
            if service.cleanup_thread is not None:service.cleanup_thread.join(2)
            self.assertTrue(stage.archive_path.exists())

    def test_preparation_progress_is_visible_while_worker_waits_and_cannot_replace_terminal_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, stage = self.owned_stage(root)
            service = self.ready_service(root, stage)
            started, finish = threading.Event(), threading.Event()

            def prepare(staged):
                service.report_install_progress('正在复制独立升级程序及运行库…')
                started.set(); finish.wait(2)

            self.addCleanup(finish.set)
            self.assertTrue(service.install(prepare))
            self.assertTrue(started.wait(2))
            self.assertEqual(service.latest.state, 'preparing')
            self.assertIn('复制', service.latest.detail)
            self.assertTrue(service.busy)
            finish.set(); service.thread.join(2)
            self.assertEqual(service.latest.state, 'restarting')
            terminal = service.latest
            self.assertFalse(service.report_install_progress('迟到的校验进度'))
            self.assertIs(service.latest, terminal)

    def test_closed_service_rejects_late_preparation_progress(self):
        service = UpdateService()
        service._status = UpdateStatus(state='preparing')
        service.close(wait=False)
        status = service.latest
        self.assertFalse(service.report_install_progress('迟到的复制进度'))
        self.assertIs(service.latest, status)
