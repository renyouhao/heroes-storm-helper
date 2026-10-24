"""Current Windows user protected credentials; no environment fallback."""
from dataclasses import dataclass, field
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import tempfile

from .config import ConfigError, Credentials


_FILE_MAGIC = b'HOTS-CREDENTIALS\x00\x01'
_DOCUMENT_MAGIC = b'HCRED1'
_MAX_FILE = 65536
_MAX_DOCUMENT = 16384


@dataclass(frozen=True)
class SavedCredentials:
    credentials: object = field(repr=False)
    account: str
    provider_id: str = 'aliyun'


def valid_account(account):
    return isinstance(account, str) and re.fullmatch(r'[1-9][0-9]{0,31}', account) is not None


def validate_credentials(credentials):
    if not isinstance(credentials, Credentials) or any(
        not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9_./+\-=]{1,256}', value) is None
        for value in (credentials.access_key_id, credentials.access_key_secret)
    ):
        raise ConfigError('访问密钥格式无效，请检查 ID 和 Secret。')


def strict_json(data):
    def unique_pairs(pairs):
        values = {}
        for key, value in pairs:
            if key in values:
                raise ValueError('duplicate field')
            values[key] = value
        return values
    return json.loads(data.decode('utf-8'), object_pairs_hook=unique_pairs)


class _DataBlob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


class _WindowsDPAPI:
    def _transform(self, data, decrypt):
        if os.name != 'nt':
            raise OSError('Windows required')
        crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        function.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                             wintypes.DWORD, ctypes.POINTER(_DataBlob)]
        function.restype = wintypes.BOOL
        buffer = ctypes.create_string_buffer(data)
        incoming = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        outgoing = _DataBlob()
        try:
            # UI_FORBIDDEN only: deliberately omit CRYPTPROTECT_LOCAL_MACHINE.
            if not function(ctypes.byref(incoming), None, None, None, None, 1,
                            ctypes.byref(outgoing)):
                raise OSError('DPAPI failed')
            if not outgoing.cbData or outgoing.cbData > _MAX_FILE:
                raise ValueError('invalid DPAPI output')
            return ctypes.string_at(outgoing.pbData, outgoing.cbData)
        finally:
            ctypes.memset(buffer, 0, len(buffer))
            if outgoing.pbData:
                ctypes.memset(outgoing.pbData, 0, outgoing.cbData)
                kernel32.LocalFree(outgoing.pbData)

    def protect(self, data):
        return self._transform(data, False)

    def unprotect(self, data):
        return self._transform(data, True)


class CredentialStore:
    _file_magic = _FILE_MAGIC
    _document_magic = _DOCUMENT_MAGIC

    def __init__(self, path: Path, *, protector=None):
        self.path = Path(path)
        self._protector = protector if protector is not None else _WindowsDPAPI()

    def _read_values(self):
        try:
            with self.path.open('rb') as stream:
                encrypted = stream.read(_MAX_FILE + 1)
        except FileNotFoundError:
            return None
        if len(encrypted) > _MAX_FILE or not encrypted.startswith(self._file_magic):
            raise ValueError('invalid credential file')
        document = self._protector.unprotect(encrypted[len(self._file_magic):])
        prefix_size = len(self._document_magic) + 32
        if (not isinstance(document, bytes) or len(document) > _MAX_DOCUMENT + prefix_size
                or not document.startswith(self._document_magic) or len(document) <= prefix_size):
            raise ValueError('invalid protected document')
        digest = document[len(self._document_magic):prefix_size]
        payload = document[prefix_size:]
        if not hmac.compare_digest(digest, hashlib.sha256(payload).digest()):
            raise ValueError('document integrity failure')
        values = strict_json(payload)
        if values is None:
            raise ValueError('invalid protected document')
        return values

    def load(self):
        try:
            values = self._read_values()
            if values is None:
                return None
            if (not isinstance(values, dict)
                    or set(values) != {'version', 'account', 'access_key_id', 'access_key_secret'}
                    or type(values['version']) is not int or values['version'] != 1
                    or not valid_account(values['account'])):
                raise ValueError('invalid credential structure')
            credentials = Credentials(values['access_key_id'], values['access_key_secret'])
            validate_credentials(credentials)
            return SavedCredentials(credentials, values['account'])
        except Exception:
            raise ConfigError('本机凭据无法安全读取或已损坏，请重新配置自己的密钥。') from None

    def save(self, credentials, account):
        try:
            validate_credentials(credentials)
            if not valid_account(account):
                raise ValueError('invalid account')
            self._write_values({'version': 1, 'account': account,
                                'access_key_id': credentials.access_key_id,
                                'access_key_secret': credentials.access_key_secret})
        except Exception:
            raise ConfigError('本机凭据无法安全保存，请检查用户数据目录后重试。') from None

    def _write_values(self, values):
        temporary = None
        try:
            payload = json.dumps(values, separators=(',', ':')).encode('utf-8')
            if len(payload) > _MAX_DOCUMENT:
                raise ValueError('protected document too large')
            document = self._document_magic + hashlib.sha256(payload).digest() + payload
            encrypted = self._protector.protect(document)
            if not isinstance(encrypted, bytes) or not encrypted or len(encrypted) + len(self._file_magic) > _MAX_FILE:
                raise ValueError('invalid protected output')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=self.path.name + '.', suffix='.tmp', dir=self.path.parent)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(self._file_magic + encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    def delete(self):
        try:
            self.path.unlink(missing_ok=True)
        except Exception:
            raise ConfigError('本机凭据无法删除，请检查用户数据目录后重试。') from None
