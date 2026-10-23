"""升级后台任务只用受控函数，无网络，无现用目录写入。"""
import threading
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from hots_chat.update_service import UpdateService
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
