from types import SimpleNamespace as NS
import unittest
from hots_chat.config import Credentials
from hots_chat.models import TranslationJob
from hots_chat.translation.aliyun import AliyunEngine
from hots_chat.translation.base import TranslationError


class AliyunTests(unittest.TestCase):
    def test_known_business_codes_provide_safe_actionable_categories(self):
        cases = [('10009', 'permission'), ('10010', 'service'), ('10013', 'billing'),
                 ('10001', 'timeout')]
        for code, kind in cases:
            with self.subTest(code=code):
                client = NS(translate_general_with_options=lambda *args: NS(
                    body=NS(code=code, message='fictional-secret', data=None)))
                engine = AliyunEngine(Credentials('fake', 'fictional-secret'), client_factory=lambda config: client)
                with self.assertRaises(TranslationError) as caught:
                    engine.translate(TranslationJob('1', None, 1, 'hi', 'en', 'zh', 'auto'))
                self.assertEqual(caught.exception.kind, kind)
                self.assertEqual(caught.exception.provider_code, code)
                self.assertNotIn('fictional-secret', str(caught.exception))

    def test_known_sdk_permission_exception_is_not_misreported_as_network(self):
        class PermissionFailure(RuntimeError):
            code = 'Forbidden.NoPermission'
        def fail(*args): raise PermissionFailure('fictional-secret')
        engine = AliyunEngine(Credentials('fake', 'fictional-secret'),
                             client_factory=lambda config: NS(translate_general_with_options=fail))
        with self.assertRaises(TranslationError) as caught:
            engine.translate(TranslationJob('1', None, 1, 'hi', 'en', 'zh', 'auto'))
        self.assertEqual(caught.exception.kind, 'permission')
        self.assertNotIn('fictional-secret', str(caught.exception))

    def test_parameters_body_only_and_single_call(self):
        calls = []
        class Client:
            def translate_general_with_options(self, request, runtime):
                calls.append((request,runtime))
                return NS(body=NS(code=200, data=NS(translated='你好',detected_language='ko'),request_id='safe-id'))
        engine = AliyunEngine(Credentials('fictional','fictional-secret'),client_factory=lambda config: Client())
        self.assertEqual(calls, [])
        for source,target in [('auto','zh'),('zh','en'),('zh','ko')]:
            result = engine.translate(TranslationJob('1',None,1,'hello',source,target,'manual'))
            request,runtime = calls[-1]
            self.assertEqual(request.source_text,'hello')
            self.assertEqual((request.source_language,request.target_language),(source,target))
            self.assertEqual(request.scene,'general')
            self.assertIsNone(request.context)
            self.assertFalse(runtime.autoretry)
            self.assertEqual(result.translated,'你好')
        self.assertEqual(len(calls),3)

    def test_business_error_and_exception_redact(self):
        for failure in (False,True):
            class Client:
                def translate_general_with_options(self,*args):
                    if failure: raise RuntimeError('fictional-secret')
                    return NS(body=NS(code=400,message='fictional-secret'))
            e = AliyunEngine(Credentials('fake','fictional-secret'),client_factory=lambda c:Client())
            with self.assertRaises(TranslationError) as ctx:
                e.translate(TranslationJob('1',None,1,'hi','auto','zh','auto'))
            self.assertNotIn('fictional-secret',str(ctx.exception))
