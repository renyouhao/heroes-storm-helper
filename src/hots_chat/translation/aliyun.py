import time
from .base import TranslationError
from ..models import TranslationResult


_ERRORS = {
    '10001': ('timeout', '翻译请求超时，请检查网络后重试。'),
    '10009': ('permission', '缺少翻译权限，请为这把 Key 所属的 RAM 用户添加 alimt:TranslateGeneral 权限。'),
    '10010': ('service', '账号未开通机器翻译通用版，请先在阿里云控制台开通。'),
    '10011': ('service', '子账号翻译服务异常，请检查服务状态和 RAM 授权。'),
    '10012': ('service', '阿里云翻译服务调用失败，请稍后重试。'),
    '10013': ('billing', '翻译服务未开通或账号欠费，请检查服务状态和账单。'),
    'Forbidden.NoPermission': ('permission', '缺少翻译权限，请检查 RAM 授权。'),
    'Forbidden.RAM': ('permission', '翻译请求被 RAM 权限拒绝，请检查授权。'),
    'InvalidAccessKeyId.NotFound': ('authentication', 'AccessKey 无效，请核对 ID、Secret 和 Key 状态。'),
    'SignatureDoesNotMatch': ('authentication', '请求签名不匹配，请核对 ID 与 Secret 是否来自同一把 Key。'),
    'RequestTimeTooSkewed': ('authentication', '系统时间偏差过大，请校准 Windows 时间后重试。'),
    'SDK.ConnectTimeout': ('timeout', '连接翻译接口超时，请检查网络后重试。'),
    'SDK.ReadTimeout': ('timeout', '等待翻译结果超时，请检查网络后重试。'),
    'Throttling': ('rate_limit', '翻译请求过于频繁，请稍后手动重试。'),
    'Throttling.User': ('rate_limit', '翻译请求达到账号限流，请稍后手动重试。'),
}


def _provider_failure(code):
    # Only known codes/messages may cross the SDK boundary. Never echo its payload.
    if code in _ERRORS:
        kind, message = _ERRORS[code]
        return TranslationError(kind, message, provider_code=code)
    return TranslationError('business', '阿里云未返回有效译文，请检查服务状态后重试。')


class AliyunEngine:
    provider_id = 'aliyun'
    config_version = 'general-hangzhou-v1'

    def __init__(self, credentials, client_factory=None):
        self.credentials = credentials
        self.client_factory = client_factory
        self.client = None

    def translate(self, job):
        if job.source not in ('auto','zh','en','ko') or job.target not in ('zh','en','ko'):
            raise TranslationError('input','不支持该语言方向。')
        started = time.perf_counter()
        try:
            from alibabacloud_alimt20181012.client import Client
            from alibabacloud_alimt20181012.models import TranslateGeneralRequest
            from alibabacloud_tea_openapi.models import Config
            from alibabacloud_tea_util.models import RuntimeOptions
            if self.client is None:
                config = Config(access_key_id=self.credentials.access_key_id,
                                access_key_secret=self.credentials.access_key_secret,
                                endpoint='mt.cn-hangzhou.aliyuncs.com',region_id='cn-hangzhou',protocol='https')
                self.client = (self.client_factory or Client)(config)
            request = TranslateGeneralRequest(format_type='text',source_language=job.source,
                       target_language=job.target,source_text=job.text,scene='general')
            runtime = RuntimeOptions(connect_timeout=3000,read_timeout=8000,autoretry=False)
            response = self.client.translate_general_with_options(request,runtime)
            body = response.body
            if str(body.code) != '200':
                raise _provider_failure(str(body.code))
            if not body.data or not isinstance(body.data.translated,str) or not body.data.translated.strip():
                raise TranslationError('business','阿里云未返回有效译文，请稍后重试。')
            return TranslationResult(job.job_id,body.data.translated,getattr(body.data,'detected_language',None),
                                     getattr(body,'request_id',None),(time.perf_counter()-started)*1000)
        except TranslationError:
            raise
        except ImportError:
            raise TranslationError('sdk','翻译 SDK 依赖缺失，请重新完整解压发行包并保留 _internal 文件夹。') from None
        except Exception as failure:
            code = getattr(failure, 'code', None)
            if isinstance(code, str) and code in _ERRORS:
                raise _provider_failure(code) from None
            if isinstance(failure, TimeoutError):
                raise TranslationError('timeout','翻译请求超时，请检查网络后重试。') from None
            raise TranslationError('network','翻译请求失败或超时，请检查网络后手动重试。') from None

    def close(self):
        self.client = None
