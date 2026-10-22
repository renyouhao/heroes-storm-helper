"""STS GetCallerIdentity using the documented ACS3-HMAC-SHA256 signature."""
from datetime import datetime, timezone
import hashlib
import hmac
from http.client import HTTPSConnection
import math
import uuid

from .config import ConfigError
from .credentials import strict_json, valid_account, validate_credentials


_HOST = 'sts.aliyuncs.com'
_MAX_RESPONSE = 65536


def _post_identity(headers, timeout):
    connection = HTTPSConnection(_HOST, timeout=timeout)
    try:
        # http.client neither follows redirects nor retries failed requests.
        connection.request('POST', '/', body=b'', headers=headers)
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('identity HTTP failure')
        return response.read(_MAX_RESPONSE + 1)
    finally:
        connection.close()


class IdentityClient:
    def __init__(self, *, transport=None, timeout=8, clock=None, nonce_factory=None):
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ConfigError('身份验证超时设置无效。')
        self._transport = transport if transport is not None else _post_identity
        self._timeout = timeout
        self._clock = clock if clock is not None else lambda: datetime.now(timezone.utc)
        self._nonce_factory = nonce_factory if nonce_factory is not None else lambda: uuid.uuid4().hex

    def resolve(self, credentials):
        try:
            validate_credentials(credentials)
            now = self._clock()
            if now.tzinfo is None:
                raise ValueError('timezone required')
            payload_hash = hashlib.sha256(b'').hexdigest()
            headers = {'host': _HOST, 'x-acs-action': 'GetCallerIdentity',
                       'x-acs-content-sha256': payload_hash,
                       'x-acs-date': now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                       'x-acs-signature-nonce': self._nonce_factory(),
                       'x-acs-version': '2015-04-01'}
            names = sorted(headers)
            signed_headers = ';'.join(names)
            canonical_headers = ''.join(f'{name}:{headers[name].strip()}\n' for name in names)
            canonical = f'POST\n/\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}'
            to_sign = 'ACS3-HMAC-SHA256\n' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()
            signature = hmac.new(credentials.access_key_secret.encode('utf-8'),
                                 to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
            headers['Authorization'] = (f'ACS3-HMAC-SHA256 Credential={credentials.access_key_id},'
                                        f'SignedHeaders={signed_headers},Signature={signature}')
            headers['accept'] = 'application/json'
            raw = self._transport(headers, self._timeout)
            if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE:
                raise ValueError('invalid identity response size')
            response = strict_json(raw)
            if (not isinstance(response, dict) or 'Code' in response or 'code' in response
                    or not valid_account(response.get('AccountId'))):
                raise ValueError('invalid caller identity')
            return response['AccountId']
        except Exception:
            raise ConfigError('无法验证阿里云所属主账号，请检查自己的密钥、网络和系统时间后重试。') from None
