"""Explicit cloud check using disposable data; never imports credentials into normal app data."""
import json
from pathlib import Path
import re
import tempfile
import time

from . import __version__
from .app import build_controller
from .config import Credentials
from .credentials import CredentialStore, validate_credentials
from .paths import AppPaths


_STAGES = frozenset(('waiting', 'identity', 'client', 'translation',
                     'save', 'activate', 'remove'))
_PROVIDER_CODES = frozenset(('10001', '10009', '10010', '10011', '10012', '10013',
    'Forbidden.NoPermission', 'Forbidden.RAM', 'InvalidAccessKeyId.NotFound',
    'SignatureDoesNotMatch', 'RequestTimeTooSkewed', 'SDK.ConnectTimeout',
    'SDK.ReadTimeout', 'Throttling', 'Throttling.User'))
_TIMEOUT_SECONDS = 60


class _CheckFailure(Exception):
    def __init__(self, kind, stage):
        self.kind, self.stage = kind, stage


def _read_export(path):
    try:
        with path.open('rb') as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeError:
            text = raw.decode('gb18030')
        values = {}
        for line in text.splitlines():
            if not line.strip():
                continue
            match = re.fullmatch(r'AccessKey[ \t]+(ID|Secret)(?:[ \t]*[:：=][ \t]*|[ \t]+)(\S+)[ \t]*',
                                 line.strip(), re.IGNORECASE)
            if not match:
                if re.match(r'AccessKey[ \t]+(?:ID|Secret)\b', line.strip(), re.IGNORECASE):
                    raise ValueError
                # Alibaba's export also contains login and security-device metadata.
                continue
            if match[1].lower() in values:
                raise ValueError
            values[match[1].lower()] = match[2]
        if set(values) != {'id', 'secret'}:
            raise ValueError
        credentials = Credentials(values['id'], values['secret'])
        validate_credentials(credentials)
        return credentials
    except Exception:
        raise _CheckFailure('credentials_invalid', 'read') from None


class _CountedEngine:
    """Count translation attempts at the engine boundary without changing the SDK."""
    def __init__(self, engine, report):
        self._engine, self._report = engine, report
        self.provider_id = engine.provider_id
        self.config_version = engine.config_version

    def translate(self, job):
        self._report['cloud_requests_started'] += 1
        self._report['translation_characters'] += len(job.text)
        return self._engine.translate(job)

    def close(self):
        self._engine.close()


def _configuration_result(setup, report, deadline):
    ready = False
    failed = None
    stage = 'waiting'
    while True:
        busy = setup.busy
        for event in setup.poll():
            kind = event.get('kind')
            candidate = event.get('stage')
            if candidate in _STAGES:
                stage = candidate
            if kind == 'progress' and candidate in _STAGES:
                report['events'].append({'kind': 'progress', 'stage': stage})
            elif kind == 'ready':
                ready = True
                report['events'].append({'kind': 'ready', 'stage': 'activate'})
            elif kind == 'error':
                failed = stage
                safe_event = {'kind': 'error', 'stage': stage, 'code': 'configuration_failed'}
                message = event.get('message')
                if isinstance(message, str):
                    for code in sorted(_PROVIDER_CODES):
                        if f'错误码：{code}）' in message:
                            safe_event['code'] = code
                            break
                report['events'].append(safe_event)
        if not busy:
            if failed:
                raise _CheckFailure('configuration_failed', failed)
            if ready and setup.state == 'ready':
                return
            raise _CheckFailure('configuration_failed', stage)
        if time.monotonic() >= deadline:
            raise _CheckFailure('timeout', stage)
        time.sleep(.01)


def _manual_result(controller, job_id, deadline):
    result_seen = False
    while True:
        for event in controller.drain_events():
            if event.kind not in ('manual_result', 'error') or not isinstance(event.payload, dict):
                continue
            if event.payload.get('job_id') != job_id:
                continue
            if event.kind == 'error':
                raise _CheckFailure('translation_failed', 'reload_translation')
            translated = event.payload.get('translated')
            if not isinstance(translated, str) or not translated.strip():
                raise _CheckFailure('translation_failed', 'reload_translation')
            result_seen = True
        if result_seen and controller.idle:
            return
        if time.monotonic() >= deadline:
            raise _CheckFailure('timeout', 'reload_translation')
        time.sleep(.01)


def _close_controller(controller):
    # Cancellation blocks late saving; join before removing the backing directory.
    setup = controller.account_setup
    setup.close()
    # Its identity/SDK operations have explicit network timeouts. Do not remove
    # files underneath a late completion, even after the diagnostic deadline.
    setup.join()
    controller.close()
    if controller.thread.is_alive():
        raise _CheckFailure('shutdown_failed', 'cleanup')


def check_cloud_configuration(credentials_path: Path, output: Path) -> int:
    """Run two paid, two-character translations only after an explicit CLI request.

    STS identity verification is separate from the translation attempt count.
    The original export is read only; all saved credentials are temporary.
    No SDK text, translated text, account, key, or input path enters the report.
    """
    started = time.monotonic()
    deadline = started + _TIMEOUT_SECONDS
    report = {
        'version': __version__, 'passed': False, 'fresh_user': False,
        'credentials_encrypted': False, 'credentials_reloaded': False,
        'restarted_translation_succeeded': False,
        'cloud_requests_started': 0, 'translation_characters': 0,
        'elapsed_ms': {'initial_configuration': 0, 'reload_translation': 0, 'total': 0},
        'events': [], 'isolated_data_removed': False,
        'real_user_configuration_modified': False,
        'game_access': False, 'user_ui_automated': False,
    }
    controllers = []
    temporary = None
    base = None
    stage = 'read'
    try:
        credentials_path, output = Path(credentials_path), Path(output)
        # A user-selected report must never destroy the only credential export.
        if (credentials_path.resolve() == output.resolve()
                or (output.exists() and credentials_path.exists() and output.samefile(credentials_path))):
            return 2
        credentials = _read_export(credentials_path)
        temporary = tempfile.TemporaryDirectory(prefix='hots-cloud-check-')
        base = Path(temporary.name)
        paths = AppPaths(base / '.env', base / 'language.sqlite3',
                         base / 'preferences.json')
        stage = 'startup'
        controller = build_controller(paths)
        controllers.append(controller)
        report['fresh_user'] = (not controller.cloud_configured and controller.paused
                                and controller.account == 'unconfigured'
                                and not paths.credentials_file.exists())
        if not report['fresh_user']:
            raise _CheckFailure('isolation_failed', stage)

        setup = controller.account_setup
        factory = setup.engine_factory
        setup.engine_factory = lambda candidate: _CountedEngine(factory(candidate), report)
        phase_started = time.monotonic()
        stage = 'waiting'
        try:
            setup.connect(credentials)
            _configuration_result(setup, report, deadline)
        finally:
            report['elapsed_ms']['initial_configuration'] = round((time.monotonic() - phase_started) * 1000)

        stage = 'save'
        encrypted = paths.credentials_file.read_bytes()
        report['credentials_encrypted'] = bool(encrypted) and all(
            value.encode('utf-8') not in encrypted
            for value in (credentials.access_key_id, credentials.access_key_secret))
        saved = CredentialStore(paths.credentials_file).load()
        if (not report['credentials_encrypted'] or saved is None
                or saved.credentials != credentials or saved.account != controller.account):
            raise _CheckFailure('credential_verification_failed', stage)
        original_account = saved.account
        if not controller.cloud_configured:
            raise _CheckFailure('configuration_verification_failed', 'activate')
        _close_controller(controller)
        controllers.remove(controller)

        stage = 'reload'
        phase_started = time.monotonic()
        try:
            controller = build_controller(paths)
            controllers.append(controller)
            reloaded = controller.account_setup.current
            report['credentials_reloaded'] = bool(controller.cloud_configured and reloaded
                and controller.account == original_account and reloaded.account == original_account
                and reloaded.credentials == credentials)
            if not report['credentials_reloaded']:
                raise _CheckFailure('credential_verification_failed', stage)
            controller.engine = _CountedEngine(controller.engine, report)
            stage = 'reload_translation'
            job_id = controller.submit_manual('你好', 'en')
            _manual_result(controller, job_id, deadline)
            if report['cloud_requests_started'] != 2 or report['translation_characters'] != 4:
                raise _CheckFailure('translation_verification_failed', 'reload_translation')
            report['restarted_translation_succeeded'] = True
        finally:
            report['elapsed_ms']['reload_translation'] = round((time.monotonic() - phase_started) * 1000)
        report['passed'] = True
    except _CheckFailure as failure:
        report['error'] = {'type': failure.kind, 'stage': failure.stage}
    except Exception:
        report['error'] = {'type': 'diagnostic_failed', 'stage': stage}
    finally:
        for controller in controllers:
            try:
                _close_controller(controller)
            except Exception:
                report['passed'] = False
                report['error'] = {'type': 'shutdown_failed', 'stage': 'cleanup'}
        try:
            if temporary is not None:
                temporary.cleanup()
            report['isolated_data_removed'] = base is None or not base.exists()
        except Exception:
            report['passed'] = False
            report['error'] = {'type': 'cleanup_failed', 'stage': 'cleanup'}
        report['elapsed_ms']['total'] = round((time.monotonic() - started) * 1000)
    if not report['isolated_data_removed']:
        report['passed'] = False
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        return 2
    return 0 if report['passed'] else 2
