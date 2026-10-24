"""升级跨进程交接回归：不创建窗口，不启动或替换真实发行版。"""
from contextlib import ExitStack
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

from hots_chat.desktop import DesktopApi, NativeClient, start_native
from hots_chat.desktop_runtime import run_native
from hots_chat.ui.main_window import MainWindow
from hots_chat.update_service import UpdateService, UpdateStatus


def update_snapshot(state):
    return {'management': {'update': {'state': state}}}


class UpdateOwnerTests(unittest.TestCase):
    def test_spawn_identifies_the_host_as_the_process_that_must_exit(self):
        context = Mock()
        parent, child = Mock(), Mock()
        context.Pipe.return_value = (parent, child)
        with patch('hots_chat.desktop.multiprocessing.get_context', return_value=context), \
                patch('hots_chat.desktop.os.getpid', return_value=8123):
            client = start_native()
        configuration = context.Process.call_args
        self.assertIs(configuration.kwargs['target'], run_native)
        self.assertEqual(configuration.kwargs['kwargs']['update_owner_pid'], 8123)
        self.assertIs(client.connection, parent)
        context.Process.return_value.start.assert_called_once()
        child.close.assert_called_once()

    def test_native_runtime_keeps_host_ownership_available_to_the_window(self):
        root, controller, connection = Mock(), Mock(), Mock()
        window = SimpleNamespace(closed=False, setup_timer=None, hotkeys=Mock())
        window.close = Mock(side_effect=lambda: setattr(window, 'closed', True))
        observed = []
        root.mainloop.side_effect = lambda: observed.append((
            window._update_owner_pid, window._update_exit_managed_by_host, window.closed))
        with ExitStack() as stack:
            stack.enter_context(patch('hots_chat.app.build_controller', return_value=controller))
            stack.enter_context(patch('hots_chat.display_runtime.enable_process_dpi_awareness'))
            stack.enter_context(patch('hots_chat.display_runtime.TkDisplayRuntime'))
            stack.enter_context(patch('tkinter.Tk', return_value=root))
            stack.enter_context(patch('hots_chat.window_settings.WindowSettings.load', return_value=Mock()))
            stack.enter_context(patch('hots_chat.ui.main_window.create_app', return_value=window))
            stack.enter_context(patch('hots_chat.desktop_runtime.TkRuntime'))
            run_native(connection, demo=True, update_owner_pid=8123)
        self.assertEqual(observed, [(8123, True, False)])
        connection.send.assert_not_called()  # No swallowed startup failure.
        window.close.assert_called_once()
        connection.close.assert_called_once()

    def test_background_preparation_binds_the_host_and_publishes_progress(self):
        window = MainWindow.__new__(MainWindow)
        window._update_owner_pid = 8123
        service = window.update_service = UpdateService()
        self.addCleanup(service.close)
        staged, plan = object(), object()
        service._status = UpdateStatus(state='downloaded', staged=staged)
        observed = []

        def prepare(received, *, pid, progress):
            observed.append((received, pid))
            progress('正在复制独立升级程序…')
            observed.append(service.latest.detail)
            return plan

        with patch('hots_chat.update_install.prepare_install', side_effect=prepare), \
                patch('hots_chat.update_install.launch_install') as launch:
            self.assertTrue(service.install(window._prepare_update))
            service.thread.join(2)
            self.assertFalse(service.thread.is_alive())
            launch.assert_called_once_with(plan)
        self.assertEqual(observed, [(staged, 8123), '正在复制独立升级程序…'])
        self.assertEqual(service.latest.state, 'restarting')


class UpdateShutdownTests(unittest.TestCase):
    def test_native_remains_alive_until_host_has_observed_readiness(self):
        window = MainWindow.__new__(MainWindow)
        window._update_installing = True
        window._update_exit_managed_by_host = True
        window.update_service = SimpleNamespace(latest=UpdateStatus(state='restarting'))
        window.close = Mock()
        for _ in range(3):
            window._pump_events()
        window.close.assert_not_called()
        self.assertEqual(window.update_service.latest.state, 'restarting')

    def test_legacy_window_still_exits_when_helper_is_ready(self):
        window = MainWindow.__new__(MainWindow)
        window._update_installing = True
        window.update_service = SimpleNamespace(latest=UpdateStatus(state='restarting'))
        window.close = Mock()
        window._pump_events()
        window.close.assert_called_once()

    def test_host_closes_once_only_after_ready_state_is_observed(self):
        client = Mock()
        client.request.side_effect = [
            {'ok': True, 'result': update_snapshot(state)}
            for state in ('preparing', 'restarting', 'restarting')
        ]
        api = DesktopApi(client)
        api._window = Mock()
        api.snapshot()
        api._window.destroy.assert_not_called()
        self.assertEqual(api.snapshot()['management']['update']['state'], 'restarting')
        api.snapshot()
        api._window.destroy.assert_called_once()

    def test_synchronous_window_close_can_shutdown_native_without_request_lock_deadlock(self):
        """Exercise real NativeClient locks, including closed -> close -> request."""
        commands, joins = [], []
        process = SimpleNamespace(alive=True)
        process.is_alive = lambda: process.alive
        process.join = lambda timeout: joins.append(timeout)
        process.terminate = Mock()

        class Connection:
            def send(self, request):
                self.request = request
                commands.append(request['command'])

            def poll(self, timeout):
                return True

            def recv(self):
                if self.request['command'] == 'shutdown':
                    process.alive = False
                    result = {'closed': True}
                else:
                    result = update_snapshot('restarting')
                return {'request_id': self.request['request_id'], 'ok': True, 'result': result}

            close = Mock()

        connection = Connection()
        client = NativeClient(connection, process, timeout=.01)
        api = DesktopApi(client)
        api._window = SimpleNamespace(destroy=client.close)
        outcome = []
        worker = threading.Thread(target=lambda: outcome.append(api.snapshot()), daemon=True)
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive(), 'window.destroy called while the IPC request lock was held')
        self.assertEqual(commands, ['snapshot', 'shutdown'])
        self.assertEqual(joins, [6])
        self.assertFalse(process.alive)
        self.assertEqual(outcome, [update_snapshot('restarting')])
        connection.close.assert_called_once()
        process.terminate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
