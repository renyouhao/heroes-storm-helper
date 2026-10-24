"""Separate encrypted translation profiles and explicit provider activation."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid

from .account_setup import AccountSetup, UnconfiguredEngine
from .config import ConfigError, Credentials
from .credentials import CredentialStore, SavedCredentials, strict_json, validate_credentials


PROVIDERS = ('aliyun', 'deepseek')
_LABELS = {'aliyun': '阿里云机器翻译', 'deepseek': 'DeepSeek'}
_BALANCE_FIELDS = ('total_balance', 'granted_balance', 'topped_up_balance')
_RECOVERY_ERROR = '上次账户配置未完整提交，请在账户设置中重新选择或连接服务。'


def _provider(value):
    if value not in PROVIDERS:
        raise ValueError('请选择阿里云或 DeepSeek 翻译服务。')
    return value


def _deepseek_credentials(api_key, context_enabled=True):
    from .translation.deepseek import DeepSeekCredentials
    return DeepSeekCredentials(api_key, context_enabled=context_enabled)


def _validate_deepseek(credentials):
    from .translation.deepseek import DeepSeekCredentials, validate_api_key
    if not isinstance(credentials, DeepSeekCredentials) or type(credentials.context_enabled) is not bool:
        raise ValueError('请填写有效的 DeepSeek API Key。')
    validate_api_key(credentials.api_key)


def _secrets(credentials):
    if isinstance(credentials, Credentials):
        return credentials.access_key_id, credentials.access_key_secret
    return (credentials.api_key,)


def _saved_provider(saved):
    return 'aliyun' if isinstance(saved.credentials, Credentials) else 'deepseek'


def _masked(value):
    return value[:3] + '…' + value[-4:] if len(value) >= 10 else '••••'


def _balance_payload(value):
    if (not isinstance(value, dict) or type(value.get('is_available')) is not bool
            or not isinstance(value.get('balance_infos'), list) or len(value['balance_infos']) > 16):
        raise ValueError('invalid balance')
    infos, currencies = [], set()
    for info in value['balance_infos']:
        if not isinstance(info, dict) or info.get('currency') not in ('CNY', 'USD'):
            raise ValueError('invalid currency')
        currency = info['currency']
        if currency in currencies:
            raise ValueError('duplicate currency')
        currencies.add(currency)
        clean = {'currency': currency}
        for field in _BALANCE_FIELDS:
            amount = info.get(field)
            if (not isinstance(amount, str) or len(amount) > 128
                    or re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?', amount) is None):
                raise ValueError('invalid amount')
            clean[field] = amount
        infos.append(clean)
    return {'is_available': value['is_available'], 'balance_infos': infos}


class _DeepSeekStore(CredentialStore):
    _file_magic = b'HOTS-DEEPSEEK-CREDENTIALS\x00\x01'
    _document_magic = b'HDSEEK1'

    def load(self):
        try:
            values = self._read_values()
            if values is None:
                return None
            if (not isinstance(values, dict)
                    or set(values) != {'version', 'provider', 'account', 'api_key', 'context_enabled'}
                    or type(values['version']) is not int or values['version'] != 1
                    or values['provider'] != 'deepseek' or values['account'] != 'deepseek'
                    or type(values['context_enabled']) is not bool):
                raise ValueError('invalid DeepSeek document')
            credentials = _deepseek_credentials(values['api_key'], values['context_enabled'])
            return SavedCredentials(credentials, 'deepseek', 'deepseek')
        except Exception:
            raise ConfigError('本机 DeepSeek 凭据无法安全读取或已损坏，请重新配置。') from None

    def save(self, credentials, account='deepseek'):
        try:
            _validate_deepseek(credentials)
            if account != 'deepseek':
                raise ValueError('invalid account')
            self._write_values({'version': 1, 'provider': 'deepseek', 'account': 'deepseek',
                                'api_key': credentials.api_key, 'context_enabled': credentials.context_enabled})
        except Exception:
            raise ConfigError('本机 DeepSeek 凭据无法安全保存，请检查用户数据目录后重试。') from None


class ProviderCredentialStore:
    """The Aliyun v1 file stays untouched when a DeepSeek profile is saved."""

    def __init__(self, legacy_path: Path, *, protector=None, legacy_store=None):
        self.path = Path(legacy_path)
        self.aliyun = legacy_store if legacy_store is not None else CredentialStore(self.path, protector=protector)
        self.deepseek = _DeepSeekStore(self.path.parent / 'deepseek.dpapi', protector=protector)
        self.selection_path = self.path.parent / 'translation-provider.json'
        self.pending_path = self.path.parent / 'translation-profile.pending.json'
        self.selection_error = ''
        try:
            self._selected_provider = self._read_selection()
        except ConfigError:
            # A display default keeps account settings reachable; load_active still
            # refuses to enable any saved key until an explicit successful selection.
            self._selected_provider = 'aliyun'
            self.selection_error = '本机翻译服务选择无法读取，请在账户设置中重新选择。'
        if self.pending_path.exists():
            self.selection_error = _RECOVERY_ERROR
        self.revision = 0

    def _read_selection(self):
        try:
            try:
                with self.selection_path.open('rb') as stream:
                    data = stream.read(1025)
            except FileNotFoundError:
                return 'aliyun'
            values = strict_json(data)
            if len(data) > 1024 or not isinstance(values, dict) or set(values) != {'provider'}:
                raise ValueError('invalid selection')
            return _provider(values['provider'])
        except Exception:
            raise ConfigError('本机翻译服务选择无法读取，请在账户设置中重新选择。') from None

    @property
    def selected_provider(self):
        return self._selected_provider

    def _store(self, provider):
        return self.aliyun if _provider(provider) == 'aliyun' else self.deepseek

    def load_active(self):
        if self.selection_error or self.pending_path.exists():
            raise ConfigError(self.selection_error or _RECOVERY_ERROR)
        return self.load_provider(self.selected_provider)

    def load(self):
        return self.load_active()

    def load_provider(self, provider):
        return self._store(provider).load()

    def save_provider(self, provider, credentials, account):
        self._store(provider).save(credentials, account)
        self.revision += 1

    def save(self, credentials, account):
        self.save_provider(self.selected_provider, credentials, account)

    def delete_provider(self, provider):
        self._store(provider).delete()
        self.revision += 1

    def delete(self):
        self.delete_provider(self.selected_provider)

    def select_provider(self, provider):
        provider = _provider(provider)
        payload = json.dumps({'provider': provider}, separators=(',', ':')).encode('utf-8')
        self._write_selection(payload)
        self._selected_provider = provider
        self.selection_error = ''
        self.revision += 1

    def _selection_checkpoint(self):
        try:
            with self.selection_path.open('rb') as stream:
                payload = stream.read(1025)
        except FileNotFoundError:
            payload = None
        except OSError:
            # Preserve a disabled selection if an unreadable old document cannot
            # be captured before an explicitly requested recovery attempt.
            payload = b'{}' if self.selection_error else None
        return self.selected_provider, self.selection_error, payload

    def _restore_selection(self, checkpoint):
        provider, error, payload = checkpoint
        if payload is None:
            try:
                self.selection_path.unlink(missing_ok=True)
            except OSError:
                raise ConfigError('本机翻译服务选择无法恢复，请在账户设置中重新选择。') from None
        else:
            self._write_selection(payload)
        self._selected_provider, self.selection_error = provider, error
        self.revision += 1

    def _write_selection(self, payload):
        self._atomic_write(self.selection_path, payload, '本机翻译服务选择无法保存，请检查用户数据目录后重试。')

    @staticmethod
    def _atomic_write(path, payload, message):
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except Exception:
            raise ConfigError(message) from None
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    def _profile_checkpoint(self, provider):
        path = self.path if provider == 'aliyun' else self.deepseek.path
        try:
            with path.open('rb') as stream:
                payload = stream.read(65537)
            if len(payload) > 65536:
                raise ValueError('profile too large to checkpoint')
            return path, payload
        except FileNotFoundError:
            return path, None
        except Exception:
            raise ConfigError('原账户凭据无法安全备份，本次配置尚未提交。') from None

    def _restore_profile(self, checkpoint):
        path, payload = checkpoint
        try:
            if payload is None:
                path.unlink(missing_ok=True)
            else:
                self._atomic_write(path, payload, '原账户凭据无法恢复，请重新配置。')
            self.revision += 1
        except Exception:
            raise ConfigError('原账户凭据无法恢复，请重新配置。') from None

    @contextmanager
    def activation_transaction(self, provider, controller):
        """Keep a durable disabled marker until profile and selection agree."""
        profile = self._profile_checkpoint(_provider(provider))
        selection = self._selection_checkpoint()
        previous_guard = self.pending_path.exists()
        previous_engine = controller.engine
        self._atomic_write(self.pending_path, json.dumps({'provider': provider}).encode('utf-8'),
                           '账户配置保护状态无法保存，本次配置尚未提交。')
        try:
            yield
            self.pending_path.unlink()
        except Exception:
            restored = True
            for restore, checkpoint in ((self._restore_profile, profile), (self._restore_selection, selection)):
                try:
                    restore(checkpoint)
                except Exception:
                    restored = False
            if restored and not previous_guard:
                try:
                    self.pending_path.unlink(missing_ok=True)
                except Exception:
                    restored = False
            if not restored or controller.engine is not previous_engine:
                self.selection_error = _RECOVERY_ERROR
                controller.cloud_configured = False
                controller.hold_configuration(True)
                controller.pause_translation(True)
            if not restored:
                raise ConfigError('账户配置恢复未完成，云翻译保持禁用，请重新选择或连接服务。') from None
            raise


class _ProfileStore:
    def __init__(self, store, provider):
        self.store, self.provider = store, provider

    def save(self, credentials, account):
        self.store.save_provider(self.provider, credentials, account)

    def delete(self):
        self.store.delete_provider(self.provider)


class _DeepSeekIdentity:
    def __init__(self, factory):
        self.factory = factory

    def resolve(self, credentials):
        engine = self.factory(credentials)
        try:
            _balance_payload(engine.get_balance())
            return 'deepseek'
        finally:
            engine.close()


class ProviderUnconfiguredEngine(UnconfiguredEngine):
    def __init__(self, provider):
        self.provider_id = _provider(provider)

    def translate(self, job):
        from .translation.base import TranslationError
        raise TranslationError('configuration', '请先配置当前翻译服务的个人密钥。')


class TranslationAccounts:
    """Reuse AccountSetup's idle/verify/test/save/activate lifecycle for both providers."""

    def __init__(self, controller, store, identity, aliyun_factory, deepseek_factory, saved=None):
        self.controller, self.store = controller, store
        self._identity = identity
        self.aliyun_factory, self.deepseek_factory = aliyun_factory, deepseek_factory
        self._lock = threading.RLock()
        self._closed = False
        self._pending_provider = None
        self._balance_epoch = 0
        self._balance_thread = None
        self._balance_threads = set()
        self._balance_request_ref = None
        self._balance = self._empty_balance()
        self._profiles_revision = None
        self._profiles = {}
        self._setup = self._make_setup(store.selected_provider, saved)

    @staticmethod
    def _empty_balance():
        return {'provider': 'deepseek', 'busy': False, 'error': '', 'as_of': None,
                'is_available': None, 'balance_infos': []}

    @property
    def identity(self):
        return self._identity

    @identity.setter
    def identity(self, value):
        self._identity = value
        if self._setup.provider_id == 'aliyun':
            self._setup.identity = value

    @property
    def engine_factory(self):
        return self._setup.engine_factory

    @engine_factory.setter
    def engine_factory(self, value):
        self._setup.engine_factory = value
        if self._setup.provider_id == 'aliyun':
            self.aliyun_factory = value
        else:
            self.deepseek_factory = value

    @property
    def current(self):
        if not getattr(self.controller, 'cloud_configured', True):
            return None
        saved = self._setup.current
        actual = getattr(self.controller.engine, 'provider_id', None)
        if saved is not None and actual in PROVIDERS and actual != _saved_provider(saved):
            return None
        return saved

    @property
    def state(self):
        return self._setup.state

    @property
    def busy(self):
        return self._setup.busy

    @property
    def selected_provider(self):
        return self.store.selected_provider

    @property
    def pending_provider(self):
        return self._pending_provider if self._setup._session_active else None

    def _make_setup(self, provider, saved):
        def activate(engine, account):
            self.store.select_provider(provider)
            self.controller.replace_engine(engine, account)
        return AccountSetup(
            self.controller, _ProfileStore(self.store, provider),
            self._identity if provider == 'aliyun' else _DeepSeekIdentity(self.deepseek_factory),
            self.aliyun_factory if provider == 'aliyun' else self.deepseek_factory,
            saved, credential_validator=validate_credentials if provider == 'aliyun' else _validate_deepseek,
            diagnostic_secrets=_secrets, provider_id=provider, activate=activate,
            unconfigured_factory=lambda: ProviderUnconfiguredEngine(provider),
            transaction=lambda: self.store.activation_transaction(provider, self.controller),
        )

    def _trace_balance(self, outcome, request_ref, *, reason='', **fields):
        recorder=getattr(self.controller,'diagnostics',None)
        if recorder is None:return
        try:
            recorder.record('configuration.progress',source='deepseek',operation='balance',
                request_ref=request_ref,stage='balance',outcome=outcome,reason=reason,
                generation=getattr(self.controller,'generation',0),**fields)
        except Exception:
            pass

    def _invalidate_balance(self, reason='invalidated'):
        if self._balance['busy'] and self._balance_request_ref:
            # Invalidates the UI operation; an in-flight HTTP request may still finish.
            self._trace_balance('cancelled',self._balance_request_ref,reason=reason)
        self._balance_request_ref = None
        self._balance_epoch += 1
        self._balance = self._empty_balance()

    def _prepare(self, provider):
        _provider(provider)
        if self._closed or self.busy:
            raise ValueError('配置任务仍在处理或已关闭，请稍后重试。')
        saved = self.current
        if self._setup._session_active:
            self._setup._resume_paused = True
            self._setup.cancel()
        self._invalidate_balance('provider_changed' if provider!=self.selected_provider else 'configuration_changed')
        self._pending_provider = provider
        self._setup = self._make_setup(provider, saved)

    def connect(self, credentials):
        validate_credentials(credentials)
        with self._lock:
            self._prepare('aliyun')
            self._setup.connect(credentials)

    def identify(self, credentials):
        validate_credentials(credentials)
        with self._lock:
            self._prepare('aliyun')
            self._setup.identify(credentials)

    def test_and_save(self):
        with self._lock:
            self._setup.test_and_save()

    def connect_deepseek(self, api_key, context_enabled=True):
        credentials = _deepseek_credentials(api_key, context_enabled)
        with self._lock:
            self._prepare('deepseek')
            self._setup.connect(credentials)

    def select_provider(self, provider, context_enabled=None):
        provider = _provider(provider)
        if context_enabled is not None and type(context_enabled) is not bool:
            raise ValueError('上下文开关必须为布尔值。')
        if provider == 'aliyun' and context_enabled:
            raise ValueError('阿里云机器翻译暂不发送聊天上下文。')
        with self._lock:
            if self._closed or self.busy:
                raise ValueError('配置任务仍在处理或已关闭，请稍后重试。')
            self._prepare(provider)
            setup = self._setup
            setup._session_active = True
            setup._resume_paused = True
            self.controller.hold_configuration(True)
            setup.state = 'identifying'

            def select_saved_or_unconfigured(token):
                setup._progress('waiting')
                if not setup._wait_idle(token):
                    return
                setup._progress('identity')
                saved = self.store.load_provider(provider)
                if token.is_set():
                    return
                if saved is not None:
                    credentials = saved.credentials
                    if provider == 'deepseek' and context_enabled is not None:
                        credentials = replace(credentials, context_enabled=context_enabled)
                    setup._credential_validator(credentials)
                    setup._register_diagnostic_secrets(credentials)
                    account = setup._identify_work(credentials, token)
                    if account is not None and not token.is_set():
                        setup._test_work(credentials, account, token)
                    return
                with setup._lock:
                    if token.is_set() or self.controller.stopped:
                        return
                    with self.controller.condition:
                        with setup._transaction():
                            setup._activate(ProviderUnconfiguredEngine(provider), 'unconfigured')
                    setup.current = None
                    self.controller.cloud_configured = False
                    setup.state = 'unconfigured'
                    setup._session_active = False
                    self.controller.hold_configuration(False)
                    self.controller.pause_translation(True)
                    setup._emit('selected', provider=provider, account='unconfigured')

            setup._start(select_saved_or_unconfigured)

    def poll(self):
        return self._setup.poll()

    def cancel(self):
        with self._lock:
            # Cancelling a provider/key change never silently resumes the old paid key.
            if self._setup._session_active:
                self._setup._resume_paused = True
            self._setup.cancel()
            self._pending_provider = None
            self._invalidate_balance('configuration_cancelled')

    def delete(self):
        with self._lock:
            provider = self.selected_provider
            self._prepare(provider)
            self._setup.delete()

    def profiles_snapshot(self):
        with self._lock:
            revision = self.store.revision
            if self._profiles_revision != revision:
                profiles = {}
                for provider in PROVIDERS:
                    try:
                        saved = self.store.load_provider(provider)
                        error = ''
                    except ConfigError:
                        saved, error = None, '本机账户无法读取，请重新配置。'
                    credentials = saved.credentials if saved else None
                    key = (credentials.access_key_id if provider == 'aliyun' else credentials.api_key) if credentials else ''
                    profiles[provider] = {
                        'configured': saved is not None, 'maskedKey': _masked(key) if key else '',
                        'label': _LABELS[provider],
                        'context_enabled': bool(credentials.context_enabled) if provider == 'deepseek' and credentials else False,
                        'error': error,
                    }
                self._profiles, self._profiles_revision = profiles, revision
            return {'selected_provider': self.selected_provider, 'pending_provider': self.pending_provider,
                    'selection_error': self.store.selection_error,
                    'profiles': {provider: dict(profile) for provider, profile in self._profiles.items()}}

    def refresh_balance(self):
        with self._lock:
            request_ref=uuid.uuid4().hex
            if self._closed:
                self._trace_balance('rejected',request_ref,reason='closed')
                return False
            if self._balance['busy']:
                self._trace_balance('rejected',request_ref,reason='busy')
                return False
            saved = self.current
            if (self.busy or self._setup._session_active or saved is None
                    or _saved_provider(saved) != 'deepseek'
                    or getattr(self.controller.engine, 'provider_id', None) != 'deepseek'):
                self._balance = self._empty_balance()
                self._balance['error'] = '请先连接当前 DeepSeek 账户，再查询余额。'
                self._trace_balance('rejected',request_ref,
                    reason='configuration_busy' if self.busy or self._setup._session_active else 'unconfigured')
                return False
            epoch = self._balance_epoch
            credentials = saved.credentials
            self._balance_request_ref=request_ref
            self._balance.update(busy=True, error='')
            self._trace_balance('accepted',request_ref)

            def query():
                engine, payload, error = None, None, ''
                failure_reason=failure_type=''
                stage='client'
                self._trace_balance('start',request_ref)
                try:
                    engine = self.deepseek_factory(credentials)
                    stage='request'
                    value=engine.get_balance()
                    stage='validate'
                    payload = _balance_payload(value)
                except Exception as failure:
                    error = 'DeepSeek 余额查询失败，请检查密钥、账户状态和网络后重试。'
                    failure_type=type(failure).__name__
                    kind=getattr(failure,'kind',None)
                    failure_reason=('invalid_balance' if stage=='validate' else
                        kind if kind in ('authentication','billing','permission','timeout',
                                         'rate_limit','network','service','business') else 'query_failed')
                finally:
                    if engine is not None:
                        try:
                            engine.close()
                        except Exception:
                            pass
                with self._lock:
                    self._balance_threads.discard(threading.current_thread())
                    if self._closed or epoch != self._balance_epoch or self.current is not saved:
                        self._trace_balance('stale',request_ref,reason='closed' if self._closed else
                            'invalidated' if epoch!=self._balance_epoch else 'profile_changed')
                        return
                    self._balance.update(busy=False, error=error)
                    self._balance_request_ref=None
                    if payload is not None:
                        self._balance.update(payload, as_of=datetime.now(timezone.utc).isoformat())
                        self._trace_balance('result',request_ref,reason='query_completed')
                    else:
                        self._trace_balance('error',request_ref,reason=failure_reason,error_type=failure_type)

            self._balance_thread = threading.Thread(target=query, name='deepseek-balance', daemon=True)
            self._balance_threads.add(self._balance_thread)
            try:
                self._balance_thread.start()
            except Exception:
                self._balance_threads.discard(self._balance_thread)
                self._balance.update(busy=False, error='余额查询未启动，请稍后重试。')
                self._balance_request_ref=None
                self._trace_balance('error',request_ref,reason='thread_start_failed')
                return False
            return True

    def balance_snapshot(self):
        with self._lock:
            return {**self._balance, 'balance_infos': [dict(info) for info in self._balance['balance_infos']]}

    def close(self):
        with self._lock:
            self._closed = True
            self._invalidate_balance('closed')
            self._setup.close()

    def join(self, timeout=None):
        """Wait only when an isolated caller explicitly needs completed cleanup."""
        deadline = None if timeout is None else time.monotonic() + max(0, timeout)
        with self._lock:
            setup = self._setup
            workers = tuple(self._balance_threads)
        setup.join(timeout)
        for worker in workers:
            if worker is threading.current_thread():
                continue
            remaining = None if deadline is None else max(0, deadline - time.monotonic())
            worker.join(remaining)
