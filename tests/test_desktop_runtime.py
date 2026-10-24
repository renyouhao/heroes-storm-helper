"""Desktop bridge routing plus an isolated, real Tk subprocess smoke test."""
from collections import OrderedDict
import multiprocessing
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hots_chat.desktop_protocol import ProtocolError
from hots_chat.desktop_runtime import TkRuntime, run_native
from hots_chat.models import AppEvent, ChatEvent
from hots_chat.ui.viewmodel import AppViewModel, MessageView
from hots_chat.window_settings import WindowSettings


class Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Input:
    def __init__(self):
        self.text = ''

    def get(self, *args):
        return self.text


def fake_window():
    vm = AppViewModel()
    controller = SimpleNamespace(generation=0, is_demo=False, cloud_configured=True,
                                 phrases=SimpleNamespace(list=lambda: []),
                                 set_channels=Mock(), retry_message=Mock(), diagnostics=None)
    w = SimpleNamespace(root=SimpleNamespace(winfo_children=lambda: [], after_idle=Mock()),
                        vm=vm, controller=controller, settings=WindowSettings(),
                        input=Input(), closed=False, automatic=Var(True), target=Var('en'),
                        channel=Var('全部聊天'), capture_state=Var('采集：未连接'),
                        status_var=Var('就绪'), manual_source=Var(''),
                        quick_input=SimpleNamespace(_visible=False, cancel=Mock()))
    for name in ('loot', 'repeat', 'talent', 'board'):
        setattr(w, name + '_panel', SimpleNamespace(_state='idle', _detail=''))
    for name in ('copy', 'toggle_automatic', 'target_changed', 'apply_repeat_config',
                 'cancel_loot', 'cancel_repeat', 'prepare_loot', 'prepare_repeat',
                 'apply_talent_config', 'apply_board_config', 'retry_talent', 'retry_board',
                 'open_quick_input', 'window_options', 'configure_account', 'open_updates', 'close'):
        setattr(w, name, Mock())

    def use_phrase(text):
        w.input.text = text
        vm.expected_job = None
        vm.copy_text = ''

    def translate():
        vm.expected_job = 'job-1'

    w.use_phrase = Mock(side_effect=use_phrase)
    w.translate = Mock(side_effect=translate)
    return w


class DesktopRuntimeTests(unittest.TestCase):
    def test_manual_failure_stays_with_its_job_until_edit_or_retry(self):
        w = fake_window()
        runtime = TkRuntime(w)
        runtime.dispatch('translate', {'text': '你好', 'target': 'en'})
        w.vm.apply(AppEvent('error', 0, {'job_id': 'job-1', 'error': '网络超时，请重试。'}))
        w.vm.apply(AppEvent('status', 0, '游戏已连接'))
        self.assertEqual(runtime.snapshot()['manualState'], 'failed')
        self.assertEqual(runtime.snapshot()['manualError'], '网络超时，请重试。')
        runtime.dispatch('draft', {'text': '新内容'})
        self.assertEqual(runtime.snapshot()['manualState'], 'idle')
        self.assertEqual(runtime.snapshot()['manualError'], '')

    def test_old_job_or_generation_error_never_overwrites_current_manual_task(self):
        w = fake_window()
        runtime = TkRuntime(w)
        runtime.dispatch('translate', {'text': '你好', 'target': 'en'})
        w.vm.apply(AppEvent('error', 0, {'job_id': 'old-job', 'error': '旧错误'}))
        self.assertEqual(runtime.snapshot()['manualState'], 'translating')
        w.vm.apply(AppEvent('error', -1, {'job_id': 'job-1', 'error': '旧连接错误'}))
        self.assertEqual(runtime.snapshot()['manualState'], 'translating')
        w.vm.apply(AppEvent('manual_result', 0, {'job_id': 'job-1', 'translated': 'Hello'}))
        w.vm.apply(AppEvent('error', 0, {'job_id': 'job-1', 'error': '迟到错误'}))
        self.assertEqual(runtime.snapshot()['manualState'], 'done')

    def test_helper_snapshot_preserves_actual_running_and_recent_result(self):
        w = fake_window()
        w.board_panel._running = False
        w.board_panel.last_result = Var('最近结果：请核对聊天框，不自动补发。')
        helper = TkRuntime(w).snapshot()['helpers']['board']
        self.assertFalse(helper['running'])
        self.assertIn('核对聊天框', helper['lastResult'])

    def test_management_is_a_snapshot_and_prepare_never_opens_a_native_dialog(self):
        w = fake_window()
        runtime = TkRuntime(w)
        self.assertEqual(runtime.snapshot()['management']['preferences']['font_size'], 12)
        runtime.dispatch('ui_prepare')
        w.quick_input.cancel.assert_called_once_with()
        w.window_options.assert_not_called()
        w.configure_account.assert_not_called()
        w.open_updates.assert_not_called()

    def test_update_hint_only_indicates_a_confirmed_available_update(self):
        from hots_chat.update_service import UpdateStatus
        w = fake_window()
        runtime = TkRuntime(w)
        self.assertFalse(runtime.snapshot()['updateAvailable'])
        release = SimpleNamespace(version='2.1.0')
        for state in ('idle', 'checking', 'current', 'error', 'cancelled'):
            with self.subTest(state=state):
                w.update_service = SimpleNamespace(latest=UpdateStatus(state, release=release))
                self.assertFalse(runtime.snapshot()['updateAvailable'])
        for state in ('available', 'downloading', 'downloaded', 'preparing', 'restarting'):
            with self.subTest(state=state):
                w.update_service = SimpleNamespace(latest=UpdateStatus(state, release=release))
                self.assertTrue(runtime.snapshot()['updateAvailable'])
                w.update_service.latest = UpdateStatus(state)
                self.assertFalse(runtime.snapshot()['updateAvailable'])

    def test_update_hint_uses_confirmed_service_result_across_transient_states(self):
        from hots_chat.update_service import UpdateStatus
        w = fake_window()
        runtime = TkRuntime(w)
        for state in ('checking', 'error', 'cancelled'):
            with self.subTest(state=state):
                w.update_service = SimpleNamespace(latest=UpdateStatus(state), has_available_update=True)
                self.assertTrue(runtime.snapshot()['updateAvailable'])
                w.update_service.has_available_update = False
                self.assertFalse(runtime.snapshot()['updateAvailable'])

    def test_native_entry_enables_only_normal_startup_checks(self):
        for demo in (False, True):
            window = SimpleNamespace(closed=False, setup_timer=None, hotkeys=SimpleNamespace(start=Mock()),
                                     close=Mock())
            with self.subTest(demo=demo), \
                    patch('hots_chat.display_runtime.enable_process_dpi_awareness'), \
                    patch('hots_chat.display_runtime.TkDisplayRuntime'), \
                    patch('hots_chat.app.build_controller', return_value=Mock()), \
                    patch('hots_chat.diagnostics.DiagnosticRecorder'), \
                    patch('hots_chat.diagnostic_hooks.DiagnosticHooks'), \
                    patch('hots_chat.window_settings.WindowSettings.load', return_value=WindowSettings()), \
                    patch('tkinter.Tk'), patch('hots_chat.ui.main_window.create_app', return_value=window) as create, \
                    patch('hots_chat.desktop_runtime.TkRuntime'):
                run_native(Mock(), demo=demo)
                self.assertEqual(create.call_args.kwargs['enable_update_check'], not demo)

    def test_legacy_runtime_theme_snapshot_is_lavender_without_changing_draft(self):
        w = fake_window()
        w.input.text = '保留尚未提交的草稿'
        runtime = TkRuntime(w)
        for legacy in ('moonlight', 'graphite', 'mint', 'amethyst', 'ice', 'dark', 'unknown'):
            with self.subTest(legacy=legacy):
                w.settings.color_theme = legacy
                snapshot = runtime.snapshot()
                self.assertEqual(snapshot['theme'], 'lavender')
                self.assertEqual(snapshot['draft'], '保留尚未提交的草稿')
                self.assertEqual(snapshot['settings']['font_size'], w.settings.font_size)

    def test_snapshot_explicitly_contains_only_public_product_data(self):
        w = fake_window()
        w.controller.credentials = 'private-credential-sentinel'
        w.controller.account = 'private-account-sentinel'
        w.controller.engine = SimpleNamespace(config_version='private-config-sentinel')
        event = ChatEvent('m1', 0, '团队', 'Raven', '14:21', 'hello')
        w.vm.messages = OrderedDict(m1=MessageView(event, '你好'))
        runtime = TkRuntime(w)
        snapshot = runtime.snapshot()
        self.assertEqual(snapshot['messages'][0]['translation'], '你好')
        self.assertFalse(snapshot['messages'][0]['historical'])
        self.assertEqual(snapshot['channelFilter'], '全部聊天')
        self.assertEqual(snapshot['helpers']['repeat']['interval'], 20)
        self.assertNotIn('private-', str(snapshot))
        w.vm.generation = 1
        self.assertTrue(runtime.snapshot()['messages'][0]['historical'])

    def test_translation_result_and_draft_changes_invalidate_copy_state(self):
        w = fake_window()
        runtime = TkRuntime(w)
        runtime.dispatch('translate', {'text': '你好', 'target': 'en'})
        self.assertTrue(runtime.snapshot()['manualBusy'])
        w.vm.apply(AppEvent('manual_result', 0,
                            {'job_id': 'job-1', 'translated': 'Hello', 'original': '你好'}))
        self.assertFalse(runtime.snapshot()['manualBusy'])
        self.assertEqual(runtime.snapshot()['previewSource'], '你好')
        runtime.dispatch('copy')
        w.copy.assert_called_once_with()
        runtime.dispatch('draft', {'text': '新的中文'})
        self.assertEqual(w.input.text, '新的中文')
        self.assertEqual(runtime.snapshot()['preview'], '')
        with self.assertRaises(ProtocolError):
            runtime.dispatch('copy')

    def test_manual_error_and_generation_stop_busy_indicator(self):
        w = fake_window()
        runtime = TkRuntime(w)
        runtime.dispatch('translate', {'text': '你好', 'target': 'en'})
        w.vm.apply(AppEvent('error', 0, {'job_id': 'job-1', 'error': '翻译失败'}))
        self.assertFalse(runtime.snapshot()['manualBusy'])
        runtime.dispatch('translate', {'text': '你好', 'target': 'en'})
        w.vm.apply(AppEvent('generation', 1, '重新连接'))
        self.assertFalse(runtime.snapshot()['manualBusy'])

    def test_controls_route_to_real_main_window_operations(self):
        w = fake_window()
        runtime = TkRuntime(w)
        runtime.dispatch('set_channel', {'value': '仅团队'})
        w.controller.set_channels.assert_called_once_with(frozenset({'团队'}))
        runtime.dispatch('set_auto', {'enabled': False})
        self.assertFalse(w.automatic.get())
        w.toggle_automatic.assert_called_once_with()
        runtime.dispatch('set_language', {'target': 'ko'})
        self.assertEqual(w.target.get(), 'ko')
        w.target_changed.assert_called_once_with()
        runtime.dispatch('repeat_config', {'action': 'YS', 'interval': 100})
        w.apply_repeat_config.assert_called_once_with('YS', 100)
        runtime.dispatch('talent_toggle', {'enabled': True})
        w.apply_talent_config.assert_called_once_with(True)
        runtime.dispatch('board_toggle', {'enabled': False})
        w.apply_board_config.assert_called_once_with(False)
        runtime.dispatch('helper_retry', {'source': 'talent'})
        w.retry_talent.assert_called_once_with()
        runtime.dispatch('helper_retry', {'source': 'board'})
        w.retry_board.assert_called_once_with()

    def test_demo_rejects_game_input_and_cloud_configuration(self):
        w = fake_window()
        w.controller.is_demo = True
        runtime = TkRuntime(w)
        for command in ('loot_prepare', 'repeat_prepare', 'open_cloud'):
            with self.subTest(command=command), self.assertRaises(ProtocolError):
                runtime.dispatch(command)
        w.prepare_loot.assert_not_called()
        w.prepare_repeat.assert_not_called()
        w.configure_account.assert_not_called()

    def test_dialogs_acknowledge_before_open_and_stop_other_input(self):
        w = fake_window()
        runtime = TkRuntime(w)
        self.assertEqual(runtime.dispatch('open_settings'), {'opened': True})
        w.cancel_loot.assert_called_once_with()
        w.cancel_repeat.assert_called_once_with()
        w.window_options.assert_not_called()
        with self.assertRaises(ProtocolError):
            runtime.dispatch('open_settings')
        callback = w.root.after_idle.call_args.args[0]
        callback()
        w.window_options.assert_called_once_with()


@unittest.skipUnless(os.name == 'nt', 'Windows native desktop runtime')
class DesktopRuntimeIntegrationTests(unittest.TestCase):
    def start_runtime(self):
        ctx = multiprocessing.get_context('spawn')
        parent, child = ctx.Pipe()
        process = ctx.Process(target=run_native, args=(child, True))
        process.start()
        child.close()

        def cleanup():
            parent.close()
            process.join(8)
            if process.is_alive():
                process.terminate()
                process.join(5)

        self.addCleanup(cleanup)
        return parent, process

    def request(self, pipe, command, payload=None):
        pipe.send({'request_id': 'test', 'command': command, 'payload': payload or {}})
        self.assertTrue(pipe.poll(15), 'native runtime did not respond')
        response = pipe.recv()
        self.assertTrue(response['ok'], response)
        return response['result']

    def test_real_isolated_demo_messages_translation_phrases_and_clean_shutdown(self):
        from hots_chat.paths import AppPaths
        actual_preferences = AppPaths.from_root(Path.cwd()).preferences_file
        original = actual_preferences.read_bytes() if actual_preferences.exists() else None
        pipe, process = self.start_runtime()
        deadline = time.monotonic() + 8
        snapshot = self.request(pipe, 'snapshot')
        while (len(snapshot['messages']) != 4 or not snapshot['preview']
               or any(not message['translation'] for message in snapshot['messages'])):
            self.assertLess(time.monotonic(), deadline, snapshot)
            time.sleep(.1)
            snapshot = self.request(pipe, 'snapshot')
        self.assertTrue(snapshot['isDemo'])
        self.assertIn('离线演示', snapshot['captureState'])
        self.assertEqual(snapshot['messages'][0]['sender'], 'Raven')
        self.assertEqual(snapshot['messages'][1]['translation'], '等我一下，大招还有 10 秒。')
        self.assertIn("Let's wait", snapshot['preview'])
        self.assertEqual(snapshot['settings']['hotkeys'], {})
        phrase = snapshot['phrases'][0]
        self.request(pipe, 'phrase_favorite', {'id': phrase['id']})
        self.request(pipe, 'phrase_use', {'id': phrase['id']})
        snapshot = self.request(pipe, 'snapshot')
        self.assertEqual(snapshot['draft'], phrase['source_text'])
        self.assertEqual(snapshot['preview'], '')
        self.request(pipe, 'set_language', {'target': 'ko'})
        self.request(pipe, 'set_auto', {'enabled': False})
        self.request(pipe, 'repeat_config', {'action': 'YS', 'interval': 120})
        snapshot = self.request(pipe, 'snapshot')
        self.assertEqual(snapshot['targetLanguage'], 'ko')
        self.assertFalse(snapshot['autoTranslate'])
        self.assertEqual(snapshot['helpers']['repeat']['interval'], 120)
        details = self.request(pipe, 'message_details', {'id': 'demo-0'})
        self.assertEqual(details['sender'], 'Raven')
        self.request(pipe, 'shutdown')
        process.join(8)
        self.assertFalse(process.is_alive())
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(actual_preferences.read_bytes() if actual_preferences.exists() else None, original)

    def test_pipe_disconnect_closes_real_native_process(self):
        pipe, process = self.start_runtime()
        self.request(pipe, 'snapshot')
        pipe.close()
        process.join(8)
        self.assertFalse(process.is_alive())
        self.assertEqual(process.exitcode, 0)

    def test_settings_and_phrase_editor_visible_while_owner_stays_withdrawn(self):
        import tkinter as tk
        from hots_chat.app import build_controller
        from hots_chat.paths import AppPaths
        from hots_chat.ui.main_window import create_app
        with tempfile.TemporaryDirectory(prefix='hots-desktop-dialog-test-') as folder:
            base = Path(folder)
            paths = AppPaths(base / '.env', base / 'language.sqlite3', base / 'preferences.json')
            controller = build_controller(paths, demo=True)
            root = tk.Tk()
            root.withdraw()
            settings = WindowSettings(hotkeys={}, overlay_enabled=False)
            window = create_app(root, controller, settings)
            runtime = TkRuntime(window)
            try:
                runtime.dispatch('open_settings')
                root.update()
                dialogs = list(runtime._active_dialogs())
                self.assertEqual(len(dialogs), 1)
                self.assertEqual(dialogs[0].state(), 'normal')
                self.assertEqual(str(dialogs[0].transient()), '')
                self.assertEqual(root.state(), 'withdrawn')
                dialogs[0].destroy()
                runtime.dispatch('phrase_edit')
                root.update()
                dialogs = list(runtime._active_dialogs())
                self.assertEqual(len(dialogs), 1)
                self.assertEqual(dialogs[0].state(), 'normal')
                self.assertEqual(root.state(), 'withdrawn')
                dialogs[0].destroy()
            finally:
                window.close()
                controller.close()
