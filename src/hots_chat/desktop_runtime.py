"""Tk business runtime hosted behind the React/WebView desktop shell.

This is an incremental migration: the existing controller, native input guards,
overlays and dialogs remain in an isolated process with a withdrawn Tk root.
All Tk access, including IPC dispatch, happens on that process's main thread.
"""
from dataclasses import asdict
from pathlib import Path
import tempfile

from .desktop_protocol import ProtocolError, validate_command, validate_request
from .appearance import DEFAULT_THEME
from .desktop_actions import DesktopActions, ACTION_COMMANDS, DesktopActionError
from .routing import incoming_source_language


_DEMO_MESSAGES = (
    ('Raven', '14:21', 'Take the camp first, then rotate bot.', '先拿营地，然后一起去下路。'),
    ('Jin', '14:22', 'My ult is up in 10 seconds.', '等我一下，大招还有 10 秒。'),
    ('Mochi', '14:22', 'Got it. I will check vision.', '收到，我先去看一下视野。'),
    ('Raven', '14:23', 'Let them use their abilities first.', '别急，等对面先交技能。'),
)


class DesktopDemoSource:
    selected_pid = None

    def process_finder(self):
        return []

    def start(self, emit):
        from .models import AppEvent, ChatEvent
        emit(AppEvent('capture_state', 0, '离线演示 · 未连接游戏'))
        for index, (sender, timestamp, original, _) in enumerate(_DEMO_MESSAGES):
            emit(AppEvent('chat', 0, ChatEvent(f'demo-{index}', 0, '团队', sender, timestamp, original)))

    def stop(self):
        pass

    def reconnect(self):
        pass


class DesktopDemoEngine:
    provider_id = 'desktop-demo'
    config_version = 'offline-v1'

    def translate(self, job):
        from .models import TranslationResult
        translations = {original: translated for _, _, original, translated in _DEMO_MESSAGES}
        if job.target == 'en' and job.text == '我们等大招好了再开团':
            translated = "Let's wait until our ult is ready, then start the fight."
        elif job.target == 'zh' and job.text in translations:
            translated = translations[job.text]
        else:
            translated = '[离线演示] ' + job.text
        return TranslationResult(job.job_id, translated, job.source, None, 0)

    def close(self):
        pass


class TkRuntime:
    """Adapt a MainWindow through its existing public operations."""

    def __init__(self, window):
        self.window = window
        self._manual_busy = False
        self._submitted_source = ''
        self._preview_source = ''
        self._dialog_pending = False
        self._dialog_running = False
        self._dialog_windows = set()
        self.actions = DesktopActions(window)
        self._manual_state = 'idle'
        self._manual_error = ''
        original_apply = window.vm.apply

        def observe(event):
            expected = window.vm.expected_job
            result = original_apply(event)
            if event.kind == 'generation' and result == 'applied':
                self._manual_busy = False
                self._preview_source = ''
                self._manual_state, self._manual_error = 'idle', ''
            if (event.kind == 'manual_result' and result == 'applied'
                    and event.payload.get('job_id') == expected):
                self._manual_busy = False
                self._preview_source = event.payload.get('original', self._submitted_source)
                self._manual_state, self._manual_error = 'done', ''
            if (event.kind == 'error' and isinstance(event.payload, dict)
                    and result == 'applied' and self._manual_state == 'translating'
                    and expected is not None and event.payload.get('job_id') == expected):
                self._manual_busy = False
                self._manual_state = 'failed'
                self._manual_error = str(event.payload.get('error', '翻译未完成，请重试或检查翻译服务。'))
            return result

        window.vm.apply = observe

    def _active_dialogs(self):
        """Track requested dialogs, never infer their role from creation time."""
        import tkinter as tk
        active = set()
        for dialog in self._dialog_windows:
            try:
                if dialog.winfo_exists():
                    active.add(dialog)
            except tk.TclError:
                pass
        self._dialog_windows = active
        return active

    def _foreground_dialogs(self, dialog):
        """A transient of a withdrawn Tk root otherwise stays invisible."""
        if dialog is None:
            return
        import tkinter as tk
        try:
            if not dialog.winfo_exists():
                return
            self._dialog_windows.add(dialog)
            dialog.transient('')
            dialog.deiconify()
            dialog.lift()
        except tk.TclError:
            pass

    def _open_dialog(self, callback):
        if self._dialog_pending or self._dialog_running:
            raise ProtocolError('请先完成当前窗口中的操作。')
        w = self.window
        w.cancel_loot()
        w.cancel_repeat()
        if w.quick_input._visible:
            w.quick_input.cancel()
        self._dialog_pending = True

        def open_later():
            self._dialog_pending = False
            if w.closed:
                return
            self._dialog_running = True
            try:
                self._foreground_dialogs(callback())
            except Exception as error:
                self.record_error('desktop.dialog', error)
                w.status_var.set('窗口操作未完成，请稍后重试；详情已写入本机诊断。')
            finally:
                self._dialog_running = False

        # A modal native confirmation/file picker may stay open for minutes.
        # Acknowledge IPC first, and let Tk's event loop own the dialog.
        w.root.after_idle(open_later)
        return {'opened': True}

    def record_error(self, stage, error):
        recorder = getattr(self.window.controller, 'diagnostics', None)
        if recorder is not None:
            recorder.record('runtime.exception', stage=stage, error_type=type(error).__name__)

    def _phrase(self, phrase_id):
        book = getattr(self.window.controller, 'phrases', None)
        if book is not None:
            for phrase in book.list():
                if phrase.id == phrase_id:
                    return phrase
        raise ProtocolError('短句已删除或不存在。')

    def _select_phrase(self, phrase_id):
        self._phrase(phrase_id)
        panel = self.window.phrase_panel
        panel.query.set('')
        panel.refresh()
        for index, phrase in enumerate(panel.items):
            if phrase.id == phrase_id:
                panel.listbox.selection_clear(0, 'end')
                panel.listbox.selection_set(index)
                panel.listbox.see(index)
                return phrase
        raise ProtocolError('短句已删除或不存在。')

    def _select_message(self, message_id):
        w = self.window
        if message_id not in w.vm.messages:
            raise ProtocolError('消息已过期。')
        w.tree.selection_set(message_id)
        w.show_selected()
        return w.vm.messages[message_id]

    def _set_draft(self, text):
        w = self.window
        if w.input.get('1.0', 'end-1c') != text:
            w.use_phrase(text)
            self._manual_busy = False
            self._preview_source = ''
            self._manual_state, self._manual_error = 'idle', ''

    def snapshot(self):
        w = self.window
        settings = w.settings
        vm = w.vm
        messages = [{
            'id': key, 'sender': message.event.sender, 'channel': message.event.channel,
            'timestamp': message.event.timestamp, 'original': message.original,
            'translation': message.engine_translation, 'status': message.translation_label,
            'historical': message.event.generation != vm.generation,
            'translationState': message.translation_state,
            'translationReason': message.translation_reason if message.translation_state != 'suppressed' else '消息频繁或队列繁忙，已保留原文。',
            'canRetry': (message.event.generation == vm.generation
                         and message.translation_state in ('failed', 'suppressed')
                         and len(message.original.encode('utf-8')) <= 4096
                         and incoming_source_language(message.original) is not None
                         and not getattr(w.controller, 'automatic_paused', False)),
        } for key, message in vm.messages.items()]
        helpers = {}
        for name in ('loot', 'repeat', 'talent', 'board'):
            panel = getattr(w, name + '_panel')
            helpers[name] = {'state': panel._state, 'detail': panel._detail}
            running = getattr(panel, '_running', None)
            if running is not None:
                helpers[name]['running'] = bool(running)
            last_result = getattr(panel, 'last_result', None)
            if last_result is not None:
                helpers[name]['lastResult'] = last_result.get()
        helpers['repeat'].update(action=settings.repeat_action,
                                 interval=(settings.repeat_s_interval_ms if settings.repeat_action == 'S'
                                           else settings.repeat_interval_ms),
                                 hotkey=settings.repeat_hotkey)
        helpers['talent']['enabled'] = settings.talent_enabled
        helpers['board']['enabled'] = settings.board_broadcast_enabled
        book = getattr(w.controller, 'phrases', None)
        update_service = getattr(w, 'update_service', None)
        update = getattr(update_service, 'latest', None)
        from .channels import CHANNEL_PRESETS
        return {
            'generation': vm.generation, 'messages': messages,
            'captureState': w.capture_state.get(),
            'serviceState': getattr(w, '_service_text', '翻译服务未配置'),
            'cloudConfigured': bool(getattr(w.controller, 'cloud_configured', False)),
            'autoTranslate': w.automatic.get(), 'targetLanguage': w.target.get(),
            'theme': DEFAULT_THEME, 'draft': w.input.get('1.0', 'end-1c'),
            'preview': vm.copy_text, 'manualSource': w.manual_source.get(),
            'manualBusy': self._manual_busy and bool(vm.expected_job),
            'manualState': self._manual_state, 'manualError': self._manual_error,
            'previewSource': self._preview_source if vm.copy_text else '',
            'channelFilter': w.channel.get(), 'channelOptions': list(CHANNEL_PRESETS),
            'status': w.status_var.get(), 'isDemo': bool(getattr(w.controller, 'is_demo', False)),
            'updateAvailable': bool(getattr(update_service, 'has_available_update',
                update is not None and update.release is not None and update.state in (
                    'available', 'downloading', 'downloaded', 'preparing', 'restarting'))),
            'helpers': helpers, 'phrases': [asdict(phrase) for phrase in book.list()] if book else [],
            'settings': {key: getattr(settings, key) for key in (
                'hotkeys', 'repeat_hotkey', 'repeat_action', 'repeat_interval_ms',
                'repeat_s_interval_ms', 'overlay_enabled', 'font_size', 'topmost',
                'alpha', 'mode', 'color_theme')},
            'nativeDialogOpen': self._dialog_pending or self._dialog_running or bool(self._active_dialogs()),
            'management': self.actions.snapshot(),
        }

    def dispatch(self, command, payload=None):
        p = validate_command(command, payload)
        w = self.window
        if command == 'snapshot':
            return self.snapshot()
        if command == 'shutdown':
            w.close()
            return {'closed': True}
        if w.closed:
            raise ProtocolError('助手已经关闭。')
        if command in ACTION_COMMANDS:
            try:
                return self.actions.dispatch(command, p)
            except DesktopActionError as error:
                raise ProtocolError(str(error)) from None
        if command == 'ui_prepare':
            w.cancel_loot()
            w.cancel_repeat()
            w.quick_input.cancel()
            return {}
        if command == 'draft':
            self._set_draft(p['text'])
        elif command == 'translate':
            self._set_draft(p['text'])
            if w.target.get() != p['target']:
                w.target.set(p['target'])
                w.target_changed()
            self._submitted_source = p['text']
            w.translate()
            self._manual_busy = bool(w.vm.expected_job)
            self._preview_source = ''
            self._manual_state, self._manual_error = 'translating', ''
        elif command == 'copy':
            if not w.vm.copy_text:
                raise ProtocolError('请先翻译当前输入内容。')
            w.copy()
        elif command == 'set_channel':
            from .channels import CHANNEL_PRESETS
            w.controller.set_channels(CHANNEL_PRESETS[p['value']])
            w.channel.set(p['value'])
        elif command == 'set_auto':
            w.automatic.set(p['enabled'])
            w.toggle_automatic()
        elif command == 'set_language':
            if w.target.get() != p['target']:
                w.target.set(p['target'])
                w.target_changed()
                self._manual_busy = False
                self._preview_source = ''
                self._manual_state, self._manual_error = 'idle', ''
        elif command == 'quick_input':
            w.open_quick_input()
        elif command == 'open_settings':
            return self._open_dialog(w.window_options)
        elif command == 'check_updates':
            return self._open_dialog(w.open_updates)
        elif command == 'open_cloud':
            if getattr(w.controller, 'is_demo', False):
                raise ProtocolError('离线演示不读取或配置真实密钥，请使用正式模式。')
            return self._open_dialog(w.configure_account)
        elif command in ('loot_prepare', 'repeat_prepare'):
            if getattr(w.controller, 'is_demo', False):
                raise ProtocolError('离线演示不会连接游戏或发送按键，请使用正式模式。')
            if command == 'loot_prepare':
                w.prepare_loot()
            else:
                w.prepare_repeat()
        elif command == 'loot_cancel':
            w.cancel_loot()
        elif command == 'repeat_cancel':
            w.cancel_repeat()
        elif command == 'repeat_config':
            w.apply_repeat_config(p['action'], p['interval'])
        elif command == 'talent_toggle':
            w.apply_talent_config(p['enabled'])
        elif command == 'board_toggle':
            w.apply_board_config(p['enabled'])
        elif command == 'helper_retry':
            if p['source'] == 'talent':
                w.retry_talent()
            else:
                w.retry_board()
        elif command == 'phrase_use':
            phrase = self._select_phrase(p['id'])
            self._set_draft(phrase.source_text)
        elif command == 'phrase_favorite':
            self._select_phrase(p['id'])
            w.phrase_panel.favorite()
        elif command == 'phrase_edit':
            phrase = self._phrase(p['id']) if p.get('id') else None
            return self._open_dialog(lambda: w.phrase_panel.edit(phrase))
        elif command == 'phrase_delete':
            self._select_phrase(p['id'])
            return self._open_dialog(w.phrase_panel.delete)
        elif command == 'phrase_import':
            return self._open_dialog(w.phrase_panel.import_file)
        elif command == 'phrase_export':
            self._select_phrase(p['id'])
            return self._open_dialog(w.phrase_panel.export_file)
        elif command == 'retry_message':
            message = self._select_message(p['id'])
            if message.event.generation != w.vm.generation:
                raise ProtocolError('历史连接消息无法重试。')
            # The controller validates channel, generation and translation route.
            w.controller.retry_message(p['id'])
        elif command == 'message_details':
            message = self._select_message(p['id'])
            return {'original': message.original, 'translation': message.engine_translation,
                    'sender': message.event.sender, 'channel': message.event.channel,
                    'timestamp': message.event.timestamp, 'status': message.status,
                    'reason': message.translation_reason, 'source': message.status if message.engine_translation else '',
                    'historical': message.event.generation != w.vm.generation}
        return {}


def run_native(connection, demo=False, *, update_owner_pid=None):
    """Multiprocessing target; never starts an HTTP listener or background Tk thread."""
    controller = root = window = recorder = hooks = temporary = runtime = None
    try:
        from .app import PROJECT_ROOT, build_controller
        from .paths import AppPaths
        from .display_runtime import enable_process_dpi_awareness, TkDisplayRuntime
        from .window_settings import WindowSettings
        enable_process_dpi_awareness()
        paths = AppPaths.from_root(PROJECT_ROOT)
        if demo:
            temporary = tempfile.TemporaryDirectory(prefix='hots-desktop-demo-')
            base = Path(temporary.name)
            paths = AppPaths(base / '.env', base / 'language.sqlite3', base / 'preferences.json')
        else:
            from .diagnostics import DiagnosticRecorder
            from .diagnostic_hooks import DiagnosticHooks
            recorder = DiagnosticRecorder(paths.diagnostics_dir)
            hooks = DiagnosticHooks(recorder)
            recorder.record('app.start', stage='desktop_native')
        controller = build_controller(paths, demo=demo, diagnostics=recorder)
        if demo:
            controller.source = DesktopDemoSource()
            controller.replace_engine(DesktopDemoEngine(), 'main')
        import tkinter as tk
        from .ui.main_window import create_app
        root = tk.Tk()
        root.withdraw()
        if hooks is not None:
            hooks.attach_root(root)
        settings = WindowSettings.load(paths.preferences_file)
        if demo:
            settings.hotkeys = {}
            settings.target_language = 'en'
            settings.overlay_enabled = False
        window = create_app(root, controller, settings, display=TkDisplayRuntime(root),
                            enable_repeat_hotkey=not demo, enable_talent_helper=not demo,
                            enable_update_check=not demo)
        # The host owns WebView2 files and the single-instance mutex. The updater
        # must wait for that process, while snapshots stay available until shutdown.
        window._update_owner_pid = update_owner_pid
        window._update_exit_managed_by_host = True
        if demo:
            # Editing settings during a demo must not register real hotkeys either.
            window.hotkeys.start = lambda bindings, emit: None
        root.withdraw()
        if window.setup_timer is not None:
            root.after_cancel(window.setup_timer)
            window.setup_timer = None
        runtime = TkRuntime(window)
        if demo:
            runtime.dispatch('translate', {'text': '我们等大招好了再开团', 'target': 'en'})

        def poll():
            if window.closed:
                return
            try:
                # Bound each tick to keep native state/overlay updates responsive.
                for _ in range(20):
                    if not connection.poll():
                        break
                    request = connection.recv()
                    request_id = request.get('request_id') if isinstance(request, dict) else None
                    try:
                        request_id, command, payload = validate_request(request)
                        result = runtime.dispatch(command, payload)
                        response = {'request_id': request_id, 'ok': True, 'result': result}
                    except ProtocolError as error:
                        response = {'request_id': request_id, 'ok': False, 'error': str(error)}
                    except Exception as error:
                        runtime.record_error('desktop.command', error)
                        response = {'request_id': request_id, 'ok': False,
                                    'error': '操作未完成，请检查当前状态或本机诊断。'}
                    connection.send(response)
                    if window.closed:
                        return
            except (EOFError, BrokenPipeError, OSError):
                window.close()
                return
            if not window.closed:
                root.after(40, poll)

        root.after(40, poll)
        root.mainloop()
    except Exception as error:
        if hooks is not None:
            hooks.capture('desktop_native', error)
        try:
            connection.send({'request_id': None, 'ok': False,
                             'error': '原生运行时启动失败，请查看本机诊断。'})
        except (OSError, EOFError):
            pass
    finally:
        if runtime is not None:
            runtime.actions.close()
        if window is not None and not window.closed:
            window.close()
        elif root is not None:
            try:
                root.destroy()
            except Exception:
                pass
        if controller is not None:
            if hasattr(controller, 'account_setup'):
                controller.account_setup.close()
            controller.close()
        if hooks is not None:
            hooks.close()
        if recorder is not None:
            recorder.record('app.stop', stage='desktop_native')
            recorder.close()
        if temporary is not None:
            temporary.cleanup()
        connection.close()
