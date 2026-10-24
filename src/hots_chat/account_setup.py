"""用户主动配置云服务；后台验证、测试后加密保存并启用。"""
from contextlib import nullcontext
import queue
import re
import threading
import uuid

from .models import TranslationJob
from .translation.base import TranslationError


_STAGES = {
    'waiting': '等待旧请求结束', 'identity': '身份验证',
    'client': '初始化翻译组件', 'translation': '翻译测试',
    'save': '加密保存', 'activate': '启用翻译', 'remove': '删除本机密钥',
}
_TRANSLATION_ERRORS = {
    'permission': '缺少翻译权限，请为这把 Key 所属的 RAM 用户添加 alimt:TranslateGeneral 权限。',
    'service': '请确认主账号已开通机器翻译通用版，并检查服务状态。',
    'billing': '翻译服务未开通或账号欠费，请检查服务状态和账单。',
    'authentication': '请核对 ID、Secret、Key 状态及 Windows 系统时间。',
    'timeout': '连接或等待翻译接口超时，请检查网络后重试。',
    'network': '无法连接翻译接口，请检查网络、代理或安全软件后重试。',
    'sdk': '翻译组件缺失，请重新完整解压发行包，保留 _internal 文件夹。',
    'rate_limit': '请求受到云端限流，请稍后手动重试。',
    'business': '云端未返回有效译文，请检查翻译服务状态后重试。',
}
_KNOWN_CODES = frozenset(('10001','10009','10010','10011','10012','10013',
    'Forbidden.NoPermission','Forbidden.RAM','InvalidAccessKeyId.NotFound',
    'SignatureDoesNotMatch','RequestTimeTooSkewed','SDK.ConnectTimeout','SDK.ReadTimeout',
    'Throttling','Throttling.User'))


class UnconfiguredEngine:
    provider_id='unconfigured'
    config_version='1'

    def translate(self, job):
        raise TranslationError('configuration','请先在设置中配置自己的阿里云密钥。')

    def close(self): pass


class AccountSetup:
    def __init__(self, controller, store, identity, engine_factory, saved=None, *,
                 credential_validator=None, diagnostic_secrets=None, provider_id='aliyun',
                 activate=None, unconfigured_factory=None, transaction=None):
        self.controller=controller
        self.store=store
        self.identity=identity
        self.engine_factory=engine_factory
        self.provider_id=provider_id
        self._credential_validator=credential_validator
        self._diagnostic_secrets=diagnostic_secrets
        self._activate=activate
        self._unconfigured_factory=unconfigured_factory or UnconfiguredEngine
        self._transaction=transaction or nullcontext
        self.current=saved
        self.state='ready' if saved else 'unconfigured'
        self._candidate=None
        self._account=None
        self._events=queue.Queue(16)
        self._lock=threading.RLock()
        self._token=threading.Event()
        self._thread=None
        self._closed=False
        self._resume_paused=True
        self._session_active=False
        self._stage='waiting'
        self._request_ref=uuid.uuid4().hex
        self._diagnostic_pending=False
        self._diagnostic_terminal=False
        if saved:self._register_diagnostic_secrets(saved.credentials,_diagnostic=False)

    def _trace(self, outcome, *, stage=None, **fields):
        recorder=getattr(self.controller,'diagnostics',None)
        if recorder is None:return
        try:
            recorder.record('configuration.progress',source=self.provider_id,
                operation='configuration',request_ref=self._request_ref,
                stage=stage or self._stage,outcome=outcome,
                generation=getattr(self.controller,'generation',0),**fields)
        except Exception:
            pass

    def _register_diagnostic_secrets(self,credentials,*,_diagnostic=True):
        recorder=getattr(self.controller,'diagnostics',None)
        if recorder is not None:
            try:
                secrets=(self._diagnostic_secrets(credentials) if self._diagnostic_secrets is not None
                         else (credentials.access_key_id,credentials.access_key_secret))
                recorder.register_secrets(*secrets)
            except Exception:
                # No credential-derived fields are emitted when redaction registration fails.
                if _diagnostic:self._trace('degraded',stage='credentials',reason='secret_registration_failed')
                return
            if _diagnostic:self._trace('provided',stage='credentials',
                key_id_last4=(credentials.access_key_id[-4:]
                              if hasattr(credentials,'access_key_id') else ''))

    @property
    def busy(self):
        return bool(self._thread and self._thread.is_alive())

    def _emit(self, kind, *, _diagnostic=True, **values):
        terminal=kind in ('ready','removed','selected','error','cancelled')
        if _diagnostic and not (terminal and self._diagnostic_terminal):
            self._trace(kind,stage=values.get('stage',self._stage),
                        reason='closed' if kind=='cancelled' and self._closed else
                               'requested' if kind=='cancelled' else '')
        if terminal:self._diagnostic_terminal=True
        try:self._events.put_nowait({'kind':kind,**values})
        except queue.Full:pass

    def poll(self):
        result=[]
        while True:
            try:result.append(self._events.get_nowait())
            except queue.Empty:return result

    def _start(self, work):
        with self._lock:
            if self.busy or self._closed:
                self._trace('rejected',reason='closed' if self._closed else 'busy')
                raise ValueError('配置任务仍在处理，请稍后重试。')
            if not self._diagnostic_pending:self._request_ref=uuid.uuid4().hex
            self._diagnostic_pending=False
            self._diagnostic_terminal=False
            self._trace('accepted')
            self.poll()
            self._token=threading.Event()
            token=self._token
            self._thread=threading.Thread(target=self._run,args=(work,token),name='account-setup',daemon=True)
            try:self._thread.start()
            except Exception:
                self._trace('error',reason='thread_start_failed')
                self._diagnostic_terminal=True
                self._thread=None
                raise

    def _run(self, work, token):
        self._trace('start')
        try:
            work(token)
        except Exception as failure:
            code=getattr(failure,'provider_code',None)
            if not token.is_set():
                self._trace('error',reason='configuration_failed',error_type=type(failure).__name__,
                    provider_code=code if isinstance(code,str) and (code in _KNOWN_CODES
                        or re.fullmatch(r'HTTP_[1-5][0-9]{2}',code)) else '')
            # Never expose provider errors, SDK request headers, or credentials.
            with self._lock:
                if not token.is_set():
                    self.state='identified' if self._candidate and self._account else 'error'
                    self._emit('error',_diagnostic=False,stage=self._stage,message=self._failure_message(failure))
        finally:
            if token.is_set():self._emit('cancelled')
            elif not self._diagnostic_terminal:
                self._trace('completed',reason='identified' if self.state=='identified' else 'work_completed')
                self._diagnostic_terminal=True

    def _progress(self, stage):
        self._stage=stage
        self._emit('progress',stage=stage,message='正在'+_STAGES[stage]+'…')

    def _failure_message(self, failure):
        # The provider, filesystem and SDK can embed credentials in exceptions.
        # Select fixed text by type/stage instead of forwarding exception strings.
        stage=_STAGES[self._stage]
        details={
            'waiting':'旧请求尚未结束，请稍后重试。',
            'identity':'请核对 ID 与 Secret 是否来自同一把 Key，并检查 Key 状态、网络及系统时间。',
            'client':_TRANSLATION_ERRORS['sdk'],
            'translation':'翻译测试未完成，请检查翻译服务、网络和权限后重试。',
            'save':'译文测试已完成，但本机加密保存失败；请检查用户数据目录权限或安全软件。',
            'activate':'配置尚未启用，请关闭配置窗口后重试。',
            'remove':'本机密钥无法删除，请检查用户数据目录权限后重试。',
        }[self._stage]
        if self.provider_id=='deepseek':
            details={
                'waiting':'旧请求尚未结束，请稍后重试。',
                'identity':'DeepSeek 密钥验证失败，请检查 API Key、账户状态和网络。',
                'client':'DeepSeek 翻译组件初始化失败，请检查安装文件。',
                'translation':'DeepSeek 翻译测试未完成，请检查余额、密钥状态和网络。',
                'save':'翻译测试已完成，但本机加密保存失败，请检查用户数据目录权限。',
                'activate':'DeepSeek 尚未启用，请关闭配置窗口后重试。',
                'remove':'本机 DeepSeek 密钥无法删除，请检查用户数据目录权限。',
            }[self._stage]
            if isinstance(failure,TranslationError):
                details={
                    'authentication':'DeepSeek API Key 无效或已失效，请重新填写。',
                    'billing':'DeepSeek 账户余额不足或服务不可用，请检查官方控制台。',
                    'rate_limit':'DeepSeek 请求受到限流，请稍后手动重试。',
                    'timeout':'DeepSeek 请求超时，请检查网络后重试。',
                    'network':'无法连接 DeepSeek，请检查网络后重试。',
                }.get(failure.kind,details)
        elif isinstance(failure, TranslationError):
            details=_TRANSLATION_ERRORS.get(failure.kind,details)
        code=getattr(failure,'provider_code',None)
        suffix=f'（错误码：{code}）' if isinstance(code,str) and code in _KNOWN_CODES else ''
        return f'配置未完成 · {stage}：{details}{suffix} 可修改后重新连接。'

    def _wait_idle(self, token):
        with self.controller.condition:
            idle=self.controller.condition.wait_for(
                lambda: self.controller.stopped or self.controller.idle, timeout=13)
        if not idle or self.controller.stopped:raise ValueError('旧请求尚未结束。')
        return not token.is_set()

    def _begin(self, credentials):
        if self._credential_validator is not None:
            self._credential_validator(credentials)
        elif not all(isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_./+\-=]{1,256}',value)
                     for value in (credentials.access_key_id,credentials.access_key_secret)):
            raise ValueError('请填写有效的 AccessKey ID 与 Secret。')
        if self.busy or self._closed:
            self._trace('rejected',reason='closed' if self._closed else 'busy')
            raise ValueError('配置任务仍在处理或已关闭，请稍后重试。')
        self._request_ref=uuid.uuid4().hex
        self._diagnostic_pending=True
        self._diagnostic_terminal=False
        self._register_diagnostic_secrets(credentials)
        if not self._session_active:self._resume_paused=self.controller.paused
        self._session_active=True
        self.controller.hold_configuration(True)
        self.state='identifying'
        self._candidate=self._account=None
        self._stage='waiting'

    def _identify_work(self, credentials, token):
        self._progress('waiting')
        if not self._wait_idle(token):return None
        self._progress('identity')
        account=self.identity.resolve(credentials)
        with self._lock:
            if token.is_set():return None
            self._candidate,self._account=credentials,account
            self.state='identified'
            self._emit('identified',account=account)
            return account

    def identify(self, credentials):
        with self._lock:
            self._begin(credentials)
            self._start(lambda token:self._identify_work(credentials,token))

    def connect(self, credentials):
        """Verify identity, test translation, encrypt and enable in one action."""
        def work(token):
            account=self._identify_work(credentials,token)
            if account is None or token.is_set():return
            self._test_work(credentials,account,token)
        with self._lock:
            self._begin(credentials)
            self._start(work)

    def test_and_save(self):
        with self._lock:
            if self.busy:raise ValueError('配置任务仍在处理，请稍后重试。')
            if self.state!='identified' or not self._candidate or not self._account:
                raise ValueError('请先验证密钥所属账号。')
            credentials,account=self._candidate,self._account
            self.state='testing'
        self._start(lambda token:self._test_work(credentials,account,token))

    def _test_work(self, credentials, account, token):
        if token.is_set() or not self._wait_idle(token):return
        self.state='testing'
        engine=None;installed=False
        try:
            if token.is_set():return
            job=TranslationJob(uuid.uuid4().hex,None,self.controller.generation,'你好','zh','en','manual')
            if token.is_set():return
            self._progress('client')
            engine=self.engine_factory(credentials)
            if token.is_set():return
            self._progress('translation')
            result=engine.translate(job)
            if not isinstance(result.translated,str) or not result.translated.strip():
                raise TranslationError('business','无有效译文')
            with self._lock:
                if token.is_set() or self.controller.stopped:return
                with self.controller.condition:
                    if self.controller.stopped or not self.controller.idle:
                        raise ValueError('配置切换尚未就绪。')
                    self._progress('save')
                    with self._transaction():
                        self.store.save(credentials,account)
                        self._progress('activate')
                        if self._activate is not None:self._activate(engine,account)
                        else:self.controller.replace_engine(engine,account)
                self.controller.cloud_configured=True
                from .credentials import SavedCredentials
                self.current=SavedCredentials(credentials,account,self.provider_id)
                self.state='ready'
                self._session_active=False
                self.controller.hold_configuration(False)
                self.controller.pause_translation(False)
                self._candidate=self._account=None
                installed=True
                self._emit('ready',account=account,translation=result.translated)
        finally:
            if engine is not None and not installed:
                try:engine.close()
                except Exception:pass

    def cancel(self):
        with self._lock:
            restore=self._session_active
            if restore or self.busy:
                self._trace('cancellation_requested',reason='closed' if self._closed else 'requested')
            self._token.set()
            self.poll()
            self._candidate=self._account=None
            self.state='ready' if self.current else 'unconfigured'
            self._session_active=False
            self.controller.hold_configuration(False)
            if restore and not self._closed:
                self.controller.pause_translation(self._resume_paused)

    def delete(self):
        with self._lock:
            if self.busy:raise ValueError('请等待当前配置任务结束后再删除。')
            if not self._session_active:self._resume_paused=self.controller.paused
            self._session_active=True
            self.controller.hold_configuration(True)
            self.state='removing'
        def work(token):
            self._progress('remove')
            if not self._wait_idle(token):return
            with self._lock:
                if token.is_set():return
                with self.controller.condition:
                    with self._transaction():
                        self.store.delete()
                        engine=self._unconfigured_factory()
                        if self._activate is not None:self._activate(engine,'unconfigured')
                        else:self.controller.replace_engine(engine,'unconfigured')
                self.controller.cloud_configured=False
                self.current=None
                self._candidate=self._account=None
                self.state='unconfigured'
                self._session_active=False
                self.controller.hold_configuration(False)
                self._emit('removed')
        self._start(work)

    def close(self):
        with self._lock:
            self._closed=True
            self.cancel()

    def join(self, timeout=None):
        """Explicit shutdown wait for diagnostics/tests; UI close stays nonblocking."""
        worker=self._thread
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout)
