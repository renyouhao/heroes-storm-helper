from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from hots_chat.config import ConfigError, Credentials
from hots_chat.identity import IdentityClient


KEYS = Credentials('fictional-id', 'fictional-secret')
ACCOUNT = '1968132000123456'
NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


class IdentityTests(unittest.TestCase):
    def client(self, transport):
        return IdentityClient(transport=transport, timeout=3,
                              clock=lambda: NOW, nonce_factory=lambda: 'offline-nonce')

    def test_returns_account_not_ram_user_and_signs_current_v3_headers(self):
        calls = []
        def transport(headers, timeout):
            calls.append((headers, timeout))
            return json.dumps({'AccountId': ACCOUNT, 'UserId': 'different-ram-user',
                               'IdentityType': 'RAMUser', 'RequestId': 'offline-id',
                               'PrincipalId': 'different-principal'}).encode()
        self.assertEqual(self.client(transport).resolve(KEYS), ACCOUNT)
        self.assertEqual(len(calls), 1)
        headers, timeout = calls[0]
        self.assertEqual(timeout, 3)
        self.assertEqual(headers['x-acs-action'], 'GetCallerIdentity')
        self.assertEqual(headers['x-acs-version'], '2015-04-01')
        self.assertEqual(headers['x-acs-date'], '2026-10-20T00:00:00Z')
        self.assertEqual(headers['host'], 'sts.aliyuncs.com')
        # Independently cross-checked with Alibaba Cloud's installed Tea V3 signer.
        self.assertEqual(headers['Authorization'],
            'ACS3-HMAC-SHA256 Credential=fictional-id,'
            'SignedHeaders=host;x-acs-action;x-acs-content-sha256;x-acs-date;'
            'x-acs-signature-nonce;x-acs-version,'
            'Signature=5a0bdde7a073ccd3ac0e98ab3cb44cc2bf045445f37403e1b24766f5acb82894')
        self.assertNotIn('fictional-secret', str(headers))

    def test_timeout_is_one_attempt_and_exception_is_redacted(self):
        calls = []
        def fail(headers, timeout):
            calls.append(headers)
            raise TimeoutError('fictional-secret')
        with self.assertRaises(ConfigError) as caught:
            self.client(fail).resolve(KEYS)
        self.assertEqual(len(calls), 1)
        self.assertNotIn('fictional-secret', str(caught.exception))

    def test_rejects_business_error_missing_malformed_or_oversized_identity(self):
        responses = [b'null', b'[]', b'\xff', b'{}', b'x' * 65537,
                     b'{"AccountId":"1","AccountId":"2"}',
                     json.dumps({'AccountId': ACCOUNT, 'Code': 'AccessDenied',
                                 'Message': 'fictional-secret'}).encode()]
        responses += [json.dumps({'AccountId': value}).encode()
                      for value in ('main', '', ' 123', '0', '1' * 33, 123, None)]
        for response in responses:
            with self.subTest(response_size=len(response)):
                with self.assertRaises(ConfigError) as caught:
                    self.client(lambda headers, timeout: response).resolve(KEYS)
                self.assertNotIn('fictional-secret', str(caught.exception))

    def test_invalid_keys_do_not_reach_network(self):
        def forbidden(headers, timeout):
            self.fail('invalid credentials reached network')
        for keys in (Credentials('', 'secret'), Credentials('id', 'bad\nsecret')):
            with self.assertRaises(ConfigError):
                self.client(forbidden).resolve(keys)

    def test_native_https_transport_posts_once_without_redirect_or_query(self):
        class Response:
            status = 200
            def read(self, maximum):
                self.maximum = maximum
                return json.dumps({'AccountId': ACCOUNT}).encode()
        class Connection:
            def __init__(self, host, timeout):
                self.host, self.timeout, self.calls = host, timeout, []
                connections.append(self)
            def request(self, method, path, body, headers):
                self.calls.append((method, path, body, headers))
            def getresponse(self):
                return Response()
            def close(self):
                self.closed = True
        connections = []
        with patch('hots_chat.identity.HTTPSConnection', Connection):
            self.assertEqual(IdentityClient().resolve(KEYS), ACCOUNT)
        self.assertEqual(len(connections), 1)
        conn = connections[0]
        self.assertEqual(conn.host, 'sts.aliyuncs.com')
        self.assertEqual(conn.calls[0][:3], ('POST', '/', b''))
        self.assertTrue(conn.closed)

    def test_native_http_failure_never_follows_redirect_or_retries(self):
        class Connection:
            def __init__(self, *args, **kwargs):
                calls.append(self)
            def request(self, *args, **kwargs):
                pass
            def getresponse(self):
                return type('Response', (), {'status': 302})()
            def close(self):
                self.closed = True
        calls = []
        with patch('hots_chat.identity.HTTPSConnection', Connection):
            with self.assertRaises(ConfigError):
                IdentityClient().resolve(KEYS)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].closed)


if __name__ == '__main__':
    unittest.main()
