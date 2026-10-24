import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess

from hots_chat import APP_NAME
from hots_chat.update_install import apply_update, launch_install, prepare_install
from hots_chat import update_install
from hots_chat.updater import UpdateError, download_update
from tests.test_updater import Response, archive_fixture, check_fixture, exe_fixture, release_payload


class InstallTests(unittest.TestCase):
    def setUp(self):
        # Windows places TemporaryDirectory beneath real LocalAppData. Isolate the
        # protection boundary too, so every target remains a disposable fixture.
        protected = tempfile.TemporaryDirectory()
        self.addCleanup(protected.cleanup)
        environment = patch.dict(os.environ, {'LOCALAPPDATA': str(Path(protected.name) / 'Local'),
                                             'APPDATA': str(Path(protected.name) / 'Roaming')})
        environment.start()
        self.addCleanup(environment.stop)

    def setup_case(self, tmp):
        base = Path(tmp)
        target = base / 'installed-helper'
        (target / '_internal').mkdir(parents=True)
        executable = target / (APP_NAME + '.exe')
        executable.write_bytes(exe_fixture())
        (target / '_internal/old.dll').write_bytes(b'old runtime')
        (target / 'old.txt').write_text('original')
        data = archive_fixture()
        release = check_fixture(release_payload(data)).release
        staged = download_update(release, staging_root=base / 'staging',
                                 opener=lambda request, **kw: Response(data, request.full_url))
        return target, executable, staged

    def prepare(self, target, executable, staged, **kwargs):
        # 这里固定测试 1.1 -> 1.2 的替换/回滚，不随源码发行版本变化。
        with patch.object(update_install,'__version__','1.1.0'):
            return prepare_install(staged, install_dir=target, executable=executable,
                                   frozen=True, pid=12345, process_token='fixture-token', **kwargs)

    def apply(self, plan, **kwargs):
        return apply_update(plan.path, helper_executable=plan.helper_executable,
                            wait_for_exit=lambda pid, token, timeout: None,
                            restart=lambda executable: None, **kwargs)

    def test_prepare_copies_helper_outside_target_and_does_not_replace_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            plan = self.prepare(target, executable, staged)
            self.assertTrue(plan.helper_executable.is_file())
            self.assertFalse(plan.helper_executable.is_relative_to(target))
            self.assertTrue((plan.helper_executable.parent / '_internal/old.dll').is_file())
            self.assertEqual((target / 'old.txt').read_text(), 'original')
            self.assertTrue(plan.path.is_file())

    def test_source_mode_and_executable_mismatch_cannot_replace_a_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            with self.assertRaises(UpdateError):
                prepare_install(staged, install_dir=target, executable=executable, frozen=False)
            with self.assertRaises(UpdateError):
                prepare_install(staged, install_dir=target.parent, executable=executable,
                                frozen=True, process_token='fixture-token')
            self.assertEqual((target / 'old.txt').read_text(), 'original')

    def test_modified_staged_bundle_is_rejected_before_preparing_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            (staged.bundle_dir / '_internal/python313.dll').write_bytes(b'tampered')
            with self.assertRaises(UpdateError):
                self.prepare(target, executable, staged)
            self.assertFalse((staged.workspace / 'helper').exists())

    def test_wait_finishes_before_rename_and_appdata_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            appdata = Path(tmp) / 'AppData/HotsChatHelper'
            appdata.mkdir(parents=True)
            (appdata / 'credentials.dpapi').write_bytes(b'private fixture')
            plan = self.prepare(target, executable, staged)
            state = {'waited': False, 'restarted': False}

            def wait(pid, token, timeout):
                self.assertEqual((pid, token), (12345, 'fixture-token'))
                self.assertEqual((target / 'old.txt').read_text(), 'original')
                state['waited'] = True

            def rename(source, destination):
                self.assertTrue(state['waited'])
                os.replace(source, destination)

            def restart(entry):
                self.assertEqual(entry, executable)
                self.assertTrue((target / '_internal/python313.dll').is_file())
                state['restarted'] = True

            status = apply_update(plan.path, helper_executable=plan.helper_executable,
                                  wait_for_exit=wait, rename=rename, restart=restart)
            self.assertEqual(status, 0)
            self.assertTrue(state['restarted'])
            self.assertFalse((target / 'old.txt').exists())
            self.assertEqual((plan.backup_dir / 'old.txt').read_text(), 'original')
            self.assertEqual((appdata / 'credentials.dpapi').read_bytes(), b'private fixture')

    def test_rename_failure_rolls_back_original_complete_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            plan = self.prepare(target, executable, staged)

            def rename(source, destination):
                if Path(source) == plan.candidate_dir:
                    raise PermissionError('fixture rename failure')
                os.replace(source, destination)

            self.assertEqual(self.apply(plan, rename=rename), 2)
            self.assertEqual((target / 'old.txt').read_text(), 'original')
            self.assertEqual((target / '_internal/old.dll').read_bytes(), b'old runtime')
            self.assertFalse(plan.backup_dir.exists())

    def test_wait_failure_does_not_move_running_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            plan = self.prepare(target, executable, staged)

            def timeout(*args):
                raise UpdateError('fixture wait timeout')

            self.assertEqual(apply_update(plan.path, helper_executable=plan.helper_executable,
                                         wait_for_exit=timeout, restart=lambda entry: self.fail('must not restart')), 2)
            self.assertEqual((target / 'old.txt').read_text(), 'original')
            self.assertFalse(plan.backup_dir.exists())

    def test_failed_new_start_restores_old_install_and_attempts_old_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            plan = self.prepare(target, executable, staged)
            calls = []

            def restart(entry):
                calls.append((entry.parent / 'old.txt').exists())
                if len(calls) == 1:
                    raise OSError('fixture restart failure')

            self.assertEqual(apply_update(plan.path, helper_executable=plan.helper_executable,
                                         wait_for_exit=lambda *args: None, restart=restart), 2)
            self.assertEqual(calls, [False, True])
            self.assertEqual((target / 'old.txt').read_text(), 'original')

    def test_modified_candidate_is_rejected_and_original_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            plan = self.prepare(target, executable, staged)
            (plan.candidate_dir / '_internal/python313.dll').write_bytes(b'changed')
            self.assertEqual(self.apply(plan), 2)
            self.assertEqual((target / 'old.txt').read_text(), 'original')

    def test_helper_running_from_install_or_forged_plan_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            plan = self.prepare(target, executable, staged)
            self.assertEqual(apply_update(plan.path, helper_executable=executable,
                                         wait_for_exit=lambda *args: self.fail('must not wait')), 2)
            payload = json.loads(plan.path.read_text(encoding='utf-8'))
            payload['target_dir'] = str(target.parent)
            plan.path.write_text(json.dumps(payload), encoding='utf-8')
            self.assertEqual(self.apply(plan), 2)
            self.assertEqual((target / 'old.txt').read_text(), 'original')

    def test_launch_only_starts_external_helper_and_waits_for_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            plan = self.prepare(target, executable, staged)
            calls = []
            process = object()

            def popen(command, **kwargs):
                calls.append(command)
                return process

            def ready(received_plan, received_process):
                self.assertEqual(received_plan.path, plan.path)
                self.assertIs(received_process, process)

            self.assertIs(launch_install(plan, popen=popen, wait_ready=ready), process)
            self.assertEqual(calls, [[str(plan.helper_executable), '--apply-update', str(plan.path)]])
            self.assertEqual((target / 'old.txt').read_text(), 'original')

    def test_update_refuses_appdata_target_even_if_it_looks_like_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            with patch.dict(os.environ, {'LOCALAPPDATA': str(target.parent)}), self.assertRaises(UpdateError):
                self.prepare(target, executable, staged)
            self.assertEqual((target / 'old.txt').read_text(), 'original')

    def test_update_refuses_game_binaries_inside_apparent_helper_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            nested = target / 'Versions/Base1'
            nested.mkdir(parents=True)
            (nested / 'HeroesOfTheStorm_x64.exe').write_bytes(b'inert game fixture')
            with self.assertRaises(UpdateError):
                self.prepare(target, executable, staged)
            self.assertFalse((staged.workspace / 'helper').exists())

    def test_second_helper_is_rejected_before_waiting_or_replacing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            plan = self.prepare(target, executable, staged)
            (plan.workspace / 'helper.lock').write_text('claimed')
            waited = []
            self.assertEqual(apply_update(plan.path, helper_executable=plan.helper_executable,
                                         wait_for_exit=lambda *args: waited.append(args), restart=lambda entry: None), 2)
            self.assertEqual(waited, [])
            self.assertEqual((target / 'old.txt').read_text(), 'original')

    def test_result_write_failure_after_started_new_app_does_not_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            plan = self.prepare(target, executable, staged)

            def restart(entry):
                # Simulate an antivirus or permissions problem affecting only the result file.
                (plan.workspace / 'result.json').mkdir()

            self.assertEqual(apply_update(plan.path, helper_executable=plan.helper_executable,
                                         wait_for_exit=lambda *args: None, restart=restart), 0)
            self.assertTrue((target / '_internal/python313.dll').is_file())
            self.assertTrue((plan.backup_dir / 'old.txt').exists())

    def test_modified_external_helper_is_not_launched(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            plan = self.prepare(target, executable, staged)
            plan.helper_executable.write_bytes(b'modified')
            launched = []
            with self.assertRaises(UpdateError):
                launch_install(plan, popen=lambda *args, **kw: launched.append(args), wait_ready=lambda *args: None)
            self.assertEqual(launched, [])

    def test_changed_candidate_after_parent_exit_restarts_original_without_swap(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            plan = self.prepare(target, executable, staged)
            restarts = []

            def wait(*args):
                (plan.candidate_dir / '_internal/python313.dll').write_bytes(b'changed after wait')

            self.assertEqual(apply_update(plan.path, helper_executable=plan.helper_executable,
                                         wait_for_exit=wait, restart=lambda entry: restarts.append(entry)), 2)
            self.assertEqual(restarts, [executable])
            self.assertEqual((target / 'old.txt').read_text(), 'original')

    def test_dead_helper_cannot_be_accepted_from_ready_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            plan = self.prepare(target, executable, staged)

            class DeadProcess:
                def poll(self):
                    return 2

            def popen(*args, **kwargs):
                plan.ready_path.write_text(plan.token, encoding='ascii')
                (plan.workspace / 'result.json').write_text('{"status":"failed"}', encoding='utf-8')
                return DeadProcess()

            with self.assertRaises(UpdateError):
                launch_install(plan, popen=popen)
            self.assertEqual((target / 'old.txt').read_text(), 'original')

    def test_early_new_process_exit_rolls_back_and_confirms_old_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            plan = self.prepare(target, executable, staged)

            class DeadProcess:
                def poll(self):
                    return 2

            class RunningProcess:
                def poll(self):
                    return None

                def wait(self, timeout):
                    raise subprocess.TimeoutExpired('inert fixture', timeout)

            def restart(entry):
                return RunningProcess() if (entry.parent / 'old.txt').exists() else DeadProcess()

            status = apply_update(plan.path, helper_executable=plan.helper_executable,
                                  wait_for_exit=lambda *args: None, restart=restart)
            self.assertEqual(status, 2)
            self.assertEqual((target / 'old.txt').read_text(), 'original')
            result = json.loads((plan.workspace / 'result.json').read_text(encoding='utf-8'))
            self.assertEqual(result['status'], 'failed')
            self.assertIsNone(result['rollback_error'])

    def test_live_new_process_must_survive_bounded_startup_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, executable, staged = self.setup_case(tmp)
            plan = self.prepare(target, executable, staged)
            probes = []

            class RunningProcess:
                def poll(self):
                    return None

                def wait(self, timeout):
                    probes.append(timeout)
                    raise subprocess.TimeoutExpired('inert fixture', timeout)

            self.assertEqual(apply_update(plan.path, helper_executable=plan.helper_executable,
                                         wait_for_exit=lambda *args: None, restart=lambda entry: RunningProcess()), 0)
            self.assertEqual(probes, [3])


class ProcessWaitTests(unittest.TestCase):
    class Kernel:
        def __init__(self, result=0):
            self.result = result
            self.waits = []
            self.closed = []

        def OpenProcess(self, access, inherit, pid):
            return 123

        def GetProcessTimes(self, handle, creation, *args):
            creation._obj.dwLowDateTime = 17
            creation._obj.dwHighDateTime = 1
            return 1

        def WaitForSingleObject(self, handle, timeout):
            self.waits.append((handle, timeout))
            return self.result

        def CloseHandle(self, handle):
            self.closed.append(handle)

    def test_reused_pid_does_not_wait_for_unrelated_process_and_closes_handle(self):
        kernel = self.Kernel()
        with patch.object(update_install, '_kernel', return_value=kernel):
            update_install._wait_for_exit(12345, 'different-creation-time', 120)
        self.assertEqual(kernel.waits, [])
        self.assertEqual(kernel.closed, [123])

    def test_matching_identity_waits_for_process_and_timeout_is_an_error(self):
        for result in (0, 258):
            kernel = self.Kernel(result)
            with patch.object(update_install, '_kernel', return_value=kernel):
                if result:
                    with self.assertRaises(UpdateError):
                        update_install._wait_for_exit(12345, '4294967313', 120)
                else:
                    update_install._wait_for_exit(12345, '4294967313', 120)
            self.assertEqual(kernel.waits, [(123, 120000)])
            self.assertEqual(kernel.closed, [123])

    def test_readiness_follows_process_identity_and_precedes_wait(self):
        kernel = self.Kernel()
        order = []
        original_times, original_wait = kernel.GetProcessTimes, kernel.WaitForSingleObject

        def times(*args):
            order.append('identity')
            return original_times(*args)

        def wait(*args):
            order.append('wait')
            return original_wait(*args)

        kernel.GetProcessTimes, kernel.WaitForSingleObject = times, wait
        with patch.object(update_install, '_kernel', return_value=kernel):
            update_install._wait_for_exit(12345, '4294967313', 120, on_ready=lambda: order.append('ready'))
        self.assertEqual(order, ['identity', 'ready', 'wait'])


if __name__ == '__main__':
    unittest.main()
