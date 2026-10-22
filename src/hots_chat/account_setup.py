"""用户主动配置云服务；后台验证，云测试与正式请求共用预算账本。"""
import queue
import re
import threading
import uuid

from .models import TranslationJob
from .translation.base import TranslationError
from .usage import BudgetError, BudgetExceeded


_STAGES = {
    'waiting': '等待旧请求结算', 'identity': '身份验证', 'usage': '用量确认',
    'client': '初始化翻译组件', 'translation': '翻译测试', 'settlement': '用量记账',
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
    def __init__(self, controller, store, identity, engine_factory, saved=None):
        self.controller=controller
        self.store=store
        self.identity=identity
        self.engine_factory=engine_factory
        self.current=saved
        self.state='ready' if saved else 'unconfigured'
        self._candidate=None
        self._account=None
        self._month=None
        self._events=queue.Queue(16)
        self._lock=threading.RLock()
        self._token=threading.Event()
        self._thread=None
        self._closed=False
        self._resume_paused=True
        self._session_active=False
        self._stage='waiting'

    @property
    def busy(self):
        return bool(self._thread and self._thread.is_alive())

    def _emit(self, kind, **values):
        try:self._events.put_nowait({'kind':kind,**values})
        except queue.Full:pass

    def poll(self):
        result=[]
        while True:
            try:result.append(self._events.get_nowait())
            except queue.Empty:return result

    def _start(self, work):
        with self._lock:
            if self.busy or self._closed:raise ValueError('配置任务仍在处理，请稍后重试。')
            self.poll()
            self._token=threading.Event()
            token=self._token
            self._thread=threading.Thread(target=self._run,args=(work,token),name='account-setup',daemon=True)
            self._thread.start()

    def _run(self, work, token):
        try:
            work(token)
        except Exception as failure:
            # Never expose provider errors, SDK request headers, or credentials.
            with self._lock:
                if not token.is_set():
                    self.state='identified' if self._candidate and self._account else 'error'
                    self._emit('error',stage=self._stage,message=self._failure_message(failure))
        finally:
            if token.is_set():self._emit('cancelled')

    def _progress(self, stage):
        self._stage=stage
        self._emit('progress',stage=stage,message='正在'+_STAGES[stage]+'…')

    def _failure_message(self, failure):
        # The provider, filesystem and SDK can embed credentials in exceptions.
        # Select fixed text by type/stage instead of forwarding exception strings.
        stage=_STAGES[self._stage]
        details={
            'waiting':'旧请求尚未结算，请稍后重试。',
            'identity':'请核对 ID 与 Secret 是否来自同一把 Key，并检查 Key 状态、网络及系统时间。',
            'usage':'请检查本月其他用量、系统时间及本机用量记录；同月用量不能调低。',
            'client':_TRANSLATION_ERRORS['sdk'],
            'translation':'翻译测试未完成，请检查翻译服务、网络和权限后重试。',
            'settlement':'本机用量记录无法安全写入，请检查用户数据目录权限和系统时间。',
            'save':'译文测试已完成，但本机加密保存失败；请检查用户数据目录权限或安全软件。',
            'activate':'配置尚未启用，请关闭配置窗口后重试。',
            'remove':'本机密钥无法删除，请检查用户数据目录权限后重试。',
        }[self._stage]
        if isinstance(failure, BudgetExceeded):details='本月可用额度不足，请核对其他用量和本机已预留字符。'
        elif isinstance(failure, BudgetError):
            details='请检查本月其他用量、系统时间及本机用量记录；同月用量不能调低。'
        elif isinstance(failure, TranslationError):
            details=_TRANSLATION_ERRORS.get(failure.kind,details)
        code=getattr(failure,'provider_code',None)
        suffix=f'（错误码：{code}）' if isinstance(code,str) and code in _KNOWN_CODES else ''
        return f'配置未完成 · {stage}：{details}{suffix} 已预留的测试用量会保留，可修改后重新连接。'

    def _wait_idle(self, token):
        with self.controller.condition:
            idle=self.controller.condition.wait_for(
                lambda: self.controller.stopped or self.controller.idle, timeout=13)
        if not idle or self.controller.stopped:raise ValueError('旧请求尚未结算。')
        return not token.is_set()

    def _begin(self, credentials):
        if not all(isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_./+\-=]{1,256}',value)
                   for value in (credentials.access_key_id,credentials.access_key_secret)):
            raise ValueError('请填写有效的 AccessKey ID 与 Secret。')
        if self.busy or self._closed:raise ValueError('配置任务仍在处理或已关闭，请稍后重试。')
        if not self._session_active:self._resume_paused=self.controller.paused
        self._session_active=True
        self.controller.hold_configuration(True)
        self.state='identifying'
        self._candidate=self._account=self._month=None
        self._stage='waiting'

    def _identify_work(self, credentials, token):
        self._progress('waiting')
        if not self._wait_idle(token):return None
        self._progress('identity')
        account=self.identity.resolve(credentials)
        with self._lock:
            if token.is_set():return None
            self._progress('usage')
            migrated=self.controller.ledger.bind_account(account,self.controller.clock())
            snapshot=self.controller.ledger.snapshot(account,self.controller.clock())
            self._candidate,self._account,self._month=credentials,account,snapshot.month
            self.state='identified'
            self._emit('identified',account=account,month=snapshot.month,
                       external=snapshot.external_used,confirmed=snapshot.confirmed,
                       reserved=snapshot.local_reserved,migrated=migrated)
            return snapshot

    def identify(self, credentials):
        with self._lock:
            self._begin(credentials)
            self._start(lambda token:self._identify_work(credentials,token))

    def connect(self, credentials, external_used=None):
        """A single explicit action verifies, tests, encrypts and enables the key.

        An untouched usage field uses this verified account's existing baseline;
        a new account starts at zero. No cloud usage lookup is implied.
        """
        if external_used is not None and (type(external_used) is not int or not 0<=external_used<=2**63-1):
            raise ValueError('本月其他用量请输入非负整数。')
        def work(token):
            snapshot=self._identify_work(credentials,token)
            if snapshot is None or token.is_set():return
            used=snapshot.external_used if external_used is None else external_used
            self._test_work(credentials,self._account,snapshot.month,used,token)
        with self._lock:
            self._begin(credentials)
            self._start(work)

    def test_and_save(self, external_used):
        if type(external_used) is not int or external_used<0:
            raise ValueError('请先核对并填写本月其他用量，未知时取消。')
        with self._lock:
            if self.busy:raise ValueError('配置任务仍在处理，请稍后重试。')
            if self.state!='identified' or not self._candidate or not self._account:
                raise ValueError('请先验证密钥所属账号。')
            credentials,account,month=self._candidate,self._account,self._month
            self.state='testing'

        self._start(lambda token:self._test_work(credentials,account,month,external_used,token))

    def _test_work(self, credentials, account, month, external_used, token):
        if token.is_set() or not self._wait_idle(token):return
        self.state='testing'
        self._progress('usage')
        self.controller.ledger.confirm_baseline(account,month,external_used,self.controller.clock())
        reservation=None;engine=None;status='error';installed=False
        try:
            if token.is_set():return
            job=TranslationJob(uuid.uuid4().hex,None,self.controller.generation,'你好','zh','en','manual')
            reservation=self.controller.ledger.reserve(account,job.job_id,job.text,self.controller.clock())
            if token.is_set():status='cancelled';return
            self._progress('client')
            engine=self.engine_factory(credentials)
            if token.is_set():status='cancelled';return
            self._progress('translation')
            result=engine.translate(job)
            if not isinstance(result.translated,str) or not result.translated.strip():
                raise TranslationError('business','无有效译文')
            status='ok'
            self._progress('settlement')
            self.controller.ledger.settle(reservation,status,self.controller.clock())
            reservation=None
            with self._lock:
                if token.is_set() or self.controller.stopped:return
                with self.controller.condition:
                    if self.controller.stopped or not self.controller.idle:
                        raise ValueError('配置切换尚未就绪。')
                    self._progress('save')
                    self.store.save(credentials,account)
                    self._progress('activate')
                    self.controller.replace_engine(engine,account)
                self.controller.cloud_configured=True
                from .credentials import SavedCredentials
                self.current=SavedCredentials(credentials,account)
                self.state='ready'
                self._session_active=False
                self.controller.hold_configuration(False)
                self.controller.pause_translation(False)
                self._candidate=self._account=self._month=None
                installed=True
                self._emit('ready',account=account,translation=result.translated,external=external_used)
        finally:
            try:
                if reservation:self.controller.ledger.settle(reservation,status,self.controller.clock())
            finally:
                if engine is not None and not installed:
                    try:engine.close()
                    except Exception:pass

    def cancel(self):
        with self._lock:
            restore=self._session_active
            self._token.set()
            self.poll()
            self._candidate=self._account=self._month=None
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
                self.store.delete()
                self.controller.replace_engine(UnconfiguredEngine(),'unconfigured')
                self.controller.cloud_configured=False
                self.current=None
                self._candidate=self._account=self._month=None
                self.state='unconfigured'
                self._session_active=False
                self.controller.hold_configuration(False)
                self._emit('removed')
        self._start(work)

    def close(self):
        with self._lock:
            self._closed=True
            self.cancel()
