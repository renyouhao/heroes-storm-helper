"""Allowlisted management actions for the desktop renderer; never create dialogs."""
from copy import deepcopy
from dataclasses import asdict, fields
from datetime import datetime
import math
from pathlib import Path
import threading
import uuid

from .config import Credentials
from .credentials import valid_account, validate_credentials
from .hotkeys import parse_binding, parse_function_key
from .phrases import Phrase, validate_phrase
from .ui.release_notes import parse_release_notes


class DesktopActionError(ValueError):
    """A fixed, user-facing error which never contains command input."""


ACTION_FIELDS = {
    'settings_save': ('values',),
    'update_check': (), 'update_download': (), 'update_cancel': (), 'update_install': (),
    'account_connect': ('accessKeyId', 'accessKeySecret'),
    'account_cancel': (), 'account_delete': (),
    'capture_reconnect': (), 'capture_stop': (), 'capture_start': (),
    'capture_processes': (), 'capture_select': ('pid',),
    'diagnostics_set': ('enabled',), 'diagnostics_export': (),
    'cloud_quota': (), 'overlay_action': ('action',),
    'phrase_save': ('phrase',), 'phrase_remove': ('id',),
}
ACTION_COMMANDS = frozenset(ACTION_FIELDS)
_NUMBERS = {
    'font_size': (10, 32, int, '内容字号'),
    'overlay_font_size': (10, 32, int, '译文字号'),
    'overlay_alpha': (.35, 1, float, '面板不透明度'),
    'overlay_width': (280, 900, int, '浮窗宽度'),
    'overlay_timeout': (5, 120, int, '消息停留秒数'),
}
_BOOLEANS = frozenset(('topmost', 'overlay_enabled', 'overlay_original'))
_EDITABLE = frozenset(_NUMBERS) | _BOOLEANS | {'mode', 'hotkeys', 'repeat_hotkey', 'overlay_style'}
PREFERENCE_FIELDS = tuple(sorted(_EDITABLE)) + (
    'target_language', 'auto_translate', 'color_theme', 'repeat_action',
    'repeat_interval_ms', 'repeat_s_interval_ms', 'talent_enabled', 'board_broadcast_enabled',
)


def _settings_values(values):
    if not isinstance(values, dict) or set(values) - _EDITABLE:
        raise DesktopActionError('设置字段无效；当前主窗口不支持透明度与位置锁定。')
    result = deepcopy(values)
    for key, value in result.items():
        if key in _NUMBERS:
            low, high, kind, label = _NUMBERS[key]
            valid_type = type(value) is int if kind is int else type(value) in (int, float)
            if not valid_type or not low <= value <= high or not math.isfinite(value):
                raise DesktopActionError(f'{label}请输入 {low}–{high} 范围内的数值。')
            result[key] = kind(value)
        elif key in _BOOLEANS and type(value) is not bool:
            raise DesktopActionError('设置开关必须为布尔值。')
        elif key == 'mode' and value not in ('normal', 'compact'):
            raise DesktopActionError('窗口模式只能选择标准或精简。')
        elif key == 'overlay_style' and value not in ('subtitle', 'panel'):
            raise DesktopActionError('请选择透明字幕或半透明面板。')
        elif key == 'repeat_hotkey':
            try:
                parse_function_key(value)
            except ValueError:
                raise DesktopActionError('连按快捷键请选择 F1–F12。') from None
            result[key] = value.strip().upper()
        elif key == 'hotkeys':
            if not isinstance(value, dict) or set(value) - {'focus', 'translate', 'copy'}:
                raise DesktopActionError('快捷键动作无效。')
            bindings, parsed = {}, set()
            for action, text in value.items():
                if not isinstance(text, str) or len(text) > 64:
                    raise DesktopActionError('快捷键格式无效。')
                text = text.strip()
                if not text:
                    continue
                try:
                    binding = parse_binding(text)
                except ValueError:
                    raise DesktopActionError('快捷键须使用修饰键与字母、数字或 F1–F12，例如 Ctrl+T。') from None
                if binding in parsed:
                    raise DesktopActionError('不同操作不能使用同一个快捷键。')
                parsed.add(binding)
                bindings[action] = text
            result[key] = bindings
    return result


def validate_action(name, payload=None):
    """Validate every field before any effect, including nested settings and phrases."""
    if not isinstance(name, str) or name not in ACTION_COMMANDS:
        raise DesktopActionError('不支持的管理操作。')
    payload = {} if payload is None else payload
    if not isinstance(payload, dict) or set(payload) != set(ACTION_FIELDS[name]):
        raise DesktopActionError('管理操作参数无效或不完整。')
    result = dict(payload)
    if name == 'settings_save':
        result['values'] = _settings_values(result['values'])
    elif name == 'account_connect':
        values = (result['accessKeyId'], result['accessKeySecret'])
        if any(not isinstance(value, str) or len(value) > 256 for value in values):
            raise DesktopActionError('请填写有效的 AccessKey ID 与 Secret。')
        credentials = Credentials(*(value.strip() for value in values))
        try:
            validate_credentials(credentials)
        except ValueError:
            raise DesktopActionError('请填写有效的 AccessKey ID 与 Secret。') from None
        result = {'accessKeyId': credentials.access_key_id, 'accessKeySecret': credentials.access_key_secret}
    elif name == 'capture_select':
        if type(result['pid']) is not int or not 1 <= result['pid'] <= 0xffffffff:
            raise DesktopActionError('请选择有效的游戏进程。')
    elif name == 'diagnostics_set' and type(result['enabled']) is not bool:
        raise DesktopActionError('诊断开关必须为布尔值。')
    elif name == 'overlay_action' and result['action'] not in ('edit', 'bottom', 'finish'):
        raise DesktopActionError('浮窗操作无效。')
    elif name == 'phrase_save':
        try:
            result['phrase'] = deepcopy(validate_phrase(result['phrase']))
        except (ValueError, TypeError, KeyError):
            raise DesktopActionError('短句字段无效，请填写分类、中文和至少一种译文。') from None
    elif name == 'phrase_remove':
        value = result['id']
        if not isinstance(value, str) or not value.strip() or len(value) > 1000 or '\x00' in value:
            raise DesktopActionError('短句编号无效。')
    return result


class _DiagnosticExport:
    """Worker-owned plain state; the background closure never retains a Tk object."""
    def __init__(self):
        self.lock = threading.Lock()
        self.busy = False
        self.closed = False
        self.path = ''
        self.message = ''

    def snapshot(self):
        with self.lock:
            return {'busy': self.busy, 'exportPath': self.path, 'message': self.message}

    def start(self, recorder):
        with self.lock:
            if self.closed or self.busy:
                raise DesktopActionError('诊断正在导出，请等待完成后再试。')
            destination = Path(recorder.directory) / (
                f'HotsHelper-diagnostics-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.zip')
            self.busy, self.path, self.message = True, '', '正在后台导出诊断日志…'

        def export():
            try:
                path = str(recorder.export_report(destination))
                message = '诊断日志已导出，请检查后自行分享。'
            except Exception:
                path, message = '', '诊断导出失败，请检查日志目录权限后重试。'
            with self.lock:
                self.busy = False
                if not self.closed:
                    self.path, self.message = path, message

        try:
            threading.Thread(target=export, name='desktop-diagnostic-export', daemon=True).start()
        except Exception:
            with self.lock:
                self.busy = False
            raise DesktopActionError('诊断导出暂时无法启动，请稍后重试。') from None

    def close(self):
        with self.lock:
            self.closed = True


class DesktopActions:
    def __init__(self, window):
        self.window = window
        self._processes = []
        self._export = _DiagnosticExport()
        self._notes_source = None
        self._note_blocks = []

    def snapshot(self):
        w = self.window
        preferences = {key: deepcopy(getattr(w.settings, key)) for key in PREFERENCE_FIELDS}
        preferences['overlay_editing'] = bool(getattr(getattr(w, 'overlay', None), '_editing', False))
        return {'preferences': preferences, 'update': self._update_snapshot(),
                'account': self._account_snapshot(), 'diagnostics': self._diagnostic_snapshot(),
                'processes': deepcopy(self._processes)}

    def _update_snapshot(self):
        service = getattr(self.window, 'update_service', None)
        status = getattr(service, 'latest', None)
        release = getattr(status, 'release', None)
        staged = getattr(status, 'staged', None)
        state = getattr(status, 'state', 'idle')
        notes = getattr(release, 'notes', '') or ''
        if notes != self._notes_source:
            self._notes_source = notes
            self._note_blocks = [asdict(block) for block in parse_release_notes(notes)]
        install_supported = bool(getattr(self.window, 'updates_can_install', False))
        detail = getattr(status, 'detail', '点击检查更新，查看最新正式版本。')
        if state == 'downloaded' and not install_supported:
            detail = '下载和校验已完成。当前从源码运行，请手动使用新发行包。'
        return {
            'currentVersion': getattr(service, 'version', ''),
            'state': state, 'detail': detail, 'version': getattr(release, 'version', ''),
            'notes': notes, 'noteBlocks': deepcopy(self._note_blocks),
            'downloaded': getattr(status, 'downloaded', 0), 'total': getattr(status, 'total', 0),
            'busy': bool(getattr(service, 'busy', False)),
            'canInstall': bool(state == 'downloaded' and staged is not None and install_supported),
            'archivePath': str(getattr(staged, 'archive_path', '')) if staged is not None else '',
        }

    def _account_snapshot(self):
        controller = self.window.controller
        service = getattr(controller, 'account_setup', None)
        current = getattr(service, 'current', None)
        credentials = getattr(current, 'credentials', None)
        key_id = getattr(credentials, 'access_key_id', '')
        account = getattr(current, 'account', '')
        if not valid_account(account):
            account = ''
        state = getattr(service, 'state', 'unconfigured')
        message = getattr(self.window, '_desktop_account_message', '')
        if not message:
            message = ('个人密钥已加密保存，翻译服务已配置。' if current is not None
                       else '填写个人密钥后连接；未配置时仍可接收原文。')
        return {'state': state, 'busy': bool(getattr(service, 'busy', False)),
                'configured': bool(getattr(controller, 'cloud_configured', False)),
                'maskedKey': ('••••' + key_id[-4:] if len(key_id) > 4 else '••••') if key_id else '',
                'account': account, 'message': message}

    def _diagnostic_snapshot(self):
        recorder = getattr(self.window.controller, 'diagnostics', None)
        source = recorder.snapshot() if recorder is not None else {}
        public_fields = ('enabled', 'write_error', 'dropped', 'max_total_bytes', 'overwritten',
                         'truncated', 'oversized', 'contains_body', 'incomplete')
        result = {key: source[key] for key in public_fields if key in source}
        result.update(available=recorder is not None, **self._export.snapshot())
        result.setdefault('enabled', False)
        return result

    def _commit_settings(self, values, before_apply=None):
        w = self.window
        previous = {field.name: deepcopy(getattr(w.settings, field.name)) for field in fields(w.settings)}
        runtime = w.capture_settings_state()

        def restore():
            for key, value in previous.items():
                setattr(w.settings, key, deepcopy(value))

        try:
            for key, value in values.items():
                setattr(w.settings, key, deepcopy(value))
            if before_apply is not None:
                before_apply()
            w.apply_settings()
            w.save_settings()
        except Exception:
            restore()
            try:
                w.apply_settings()
            except Exception:
                pass
            finally:
                try:
                    w.restore_settings_state(runtime)
                finally:
                    restore()
            raise DesktopActionError('设置未保存，已保留原偏好；请检查当前浮窗状态后重试。') from None

    def _account_service(self):
        if getattr(self.window.controller, 'is_demo', False):
            raise DesktopActionError('离线演示不读取或配置真实密钥，请使用正式模式。')
        service = getattr(self.window.controller, 'account_setup', None)
        if service is None:
            raise DesktopActionError('当前运行模式不支持配置个人密钥。')
        return service

    def _refresh_processes(self):
        processes = self.window.controller.source.process_finder()
        self._processes = [{'pid': process.pid, 'path': str(process.path),
                            'name': Path(process.path).name, 'build': process.build}
                           for process in processes]
        return processes

    def dispatch(self, name, payload=None):
        p = validate_action(name, payload)
        if getattr(self.window, 'closed', False):
            raise DesktopActionError('助手已经关闭。')
        try:
            return self._dispatch(name, p)
        except DesktopActionError:
            raise
        except Exception:
            # SDK/filesystem exceptions can contain input or private paths.
            raise DesktopActionError('操作未完成，请检查当前状态后重试。') from None

    def _dispatch(self, name, p):
        w = self.window
        if name == 'settings_save':
            self._commit_settings(p['values'])
            return {'message': '设置已保存。'}
        if name.startswith('update_'):
            service = getattr(w, 'update_service', None)
            if service is None:
                raise DesktopActionError('更新服务尚未就绪。')
            if name == 'update_install':
                if not self._update_snapshot()['canInstall']:
                    raise DesktopActionError('请先下载并校验更新；源码运行时请手动使用新发行包。')
                accepted = w.install_update()
            elif name == 'update_cancel':
                if service.latest.state not in ('checking', 'downloading'):
                    raise DesktopActionError('当前没有可取消的检查或下载任务。')
                accepted = service.cancel()
            else:
                accepted = service.check() if name == 'update_check' else service.download()
            if not accepted:
                raise DesktopActionError('上次任务尚未结束，或当前没有可执行的更新操作。')
            return {'message': {'update_check': '正在检查更新…', 'update_download': '正在下载并校验…',
                                'update_cancel': '更新任务已取消。', 'update_install': '正在准备重启并更新…'}[name]}
        if name.startswith('account_'):
            service = self._account_service()
            if name != 'account_cancel' and service.busy:
                raise DesktopActionError('配置任务仍在处理，请等待完成或取消。')
            if name == 'account_connect':
                w.clear_draft()
                w.quick_input.cancel()
                service.connect(Credentials(p['accessKeyId'], p['accessKeySecret']))
                message = '正在连接，验证、测试并加密保存；期间暂停其他云请求…'
            elif name == 'account_delete':
                w.clear_draft()
                w.quick_input.cancel()
                service.delete()
                message = '正在等待旧请求结束并删除本机凭据…'
            else:
                service.cancel()
                # Cancellation can consume a just-finished ready event. Reflect
                # current controller state without enabling a previously paused key.
                cloud = getattr(w, 'cloud', None)
                if cloud is not None:
                    cloud.set(bool(getattr(w.controller, 'cloud_configured', False)
                                   and not getattr(w.controller, 'paused', True)
                                   and not getattr(w.controller, 'configuration_held', False)))
                refresh = getattr(w, 'refresh_service_status', None)
                if refresh is not None:
                    refresh()
                button = getattr(w, 'account_button', None)
                if button is not None:
                    button.configure(text='个人密钥' if getattr(w.controller, 'cloud_configured', False) else '配置云翻译')
                message = '已取消尚未完成的配置；已完成的保存或删除不会撤销。'
            w._desktop_account_message = message
            return {'message': message}
        if name.startswith('capture_'):
            if name == 'capture_processes':
                self._refresh_processes()
                return {'processes': deepcopy(self._processes)}
            if name == 'capture_select':
                processes = self._refresh_processes()
                if p['pid'] not in {process.pid for process in processes}:
                    raise DesktopActionError('所选游戏进程已退出，请刷新列表后重新选择。')
                w.process_choice = tuple(process.pid for process in processes)
                w.controller.source.selected_pid = p['pid']
                w.controller.source.reconnect()
            elif name == 'capture_reconnect':
                w.controller.source.reconnect()
            elif name == 'capture_stop':
                w.stop()
            else:
                w.start()
            return {'message': '采集连接操作已执行，状态将自动更新。'}
        if name.startswith('diagnostics_'):
            recorder = getattr(w.controller, 'diagnostics', None)
            if recorder is None:
                raise DesktopActionError('当前运行模式未启用诊断服务。')
            if name == 'diagnostics_set':
                recorder.set_enabled(p['enabled'])
                return {'message': '诊断已开启，请复现问题后导出。' if p['enabled'] else '诊断已关闭；已有日志仍可导出。'}
            self._export.start(recorder)
            return {'message': '正在后台导出诊断日志…'}
        if name == 'cloud_quota':
            if not w.open_cloud_quota():
                raise DesktopActionError('无法打开默认浏览器，请手动访问阿里云控制台。')
            return {'message': '已请求打开阿里云控制台，请在浏览器中查看用量。'}
        if name == 'overlay_action':
            if p['action'] == 'finish':
                w.overlay.set_editing(False)
                w.save_settings()
            else:
                values = {'overlay_enabled': True}
                if p['action'] == 'bottom':
                    values['overlay_style'] = 'subtitle'
                self._commit_settings(values, w.overlay.use_bottom_preset if p['action'] == 'bottom' else None)
                if p['action'] == 'edit':
                    w.overlay.set_editing(True)
            return {'message': '浮窗布局已更新。'}
        book = getattr(w.controller, 'phrases', None)
        if book is None:
            raise DesktopActionError('短句资料尚未就绪。')
        if name == 'phrase_save':
            book.save(Phrase(**p['phrase']))
        else:
            if not any(phrase.id == p['id'] for phrase in book.list()):
                raise DesktopActionError('短句已删除或不存在。')
            book.delete(p['id'])
        panel = getattr(w, 'phrase_panel', None)
        if panel is not None:
            panel.changed()
        return {'message': '短句已保存。' if name == 'phrase_save' else '短句已删除。'}

    def close(self):
        self._export.close()
