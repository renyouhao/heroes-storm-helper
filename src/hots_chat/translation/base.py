from typing import Protocol
from ..models import TranslationJob, TranslationResult


class TranslationError(RuntimeError):
    def __init__(self, kind, safe_message, request_id=None, provider_code=None):
        super().__init__(safe_message)
        self.kind, self.request_id = kind, request_id
        self.provider_code = provider_code


class TranslationEngine(Protocol):
    provider_id: str
    config_version: str
    def translate(self, job: TranslationJob) -> TranslationResult: ...
    def close(self) -> None: ...


class ProviderRegistry:
    real_providers=('aliyun',)
    def __init__(self):self.factories={}
    def inject(self,name,factory):self.factories[name]=factory
    def create(self,name,credentials):
        if name in self.factories:return self.factories[name](credentials)
        if name!='aliyun':raise ValueError('未实现该翻译引擎；不会自动切换。')
        from .aliyun import AliyunEngine
        return AliyunEngine(credentials)
