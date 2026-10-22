import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hots_chat.config import ConfigError, Credentials
from hots_chat.credentials import CredentialStore


ACCOUNT = '1968132000123456'
KEYS = Credentials('fictional-id', 'fictional-secret')


class MemoryProtector:
    """Only the external encryption boundary is replaced in format tests."""
    def __init__(self):
        self.documents = {}

    def protect(self, data):
        token = b'ciphertext-' + str(len(self.documents)).encode('ascii')
        self.documents[token] = data
        return token

    def unprotect(self, data):
        return self.documents[data]


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'credentials.dat'
        self.protector = MemoryProtector()
        self.store = CredentialStore(self.path, protector=self.protector)

    def test_absent_store_is_unconfigured_and_never_imports_env(self):
        (self.path.parent / '.env').write_text('invalid-do-not-read', encoding='utf-8')
        self.assertIsNone(self.store.load())

    def test_saved_document_roundtrips_without_plaintext_on_disk_or_repr(self):
        self.store.save(KEYS, ACCOUNT)
        saved = self.store.load()
        self.assertEqual(saved.credentials, KEYS)
        self.assertEqual(saved.account, ACCOUNT)
        for private in ('fictional-id', 'fictional-secret', ACCOUNT):
            self.assertNotIn(private.encode(), self.path.read_bytes())
        self.assertNotIn('fictional-secret', repr(saved))
        self.assertNotIn('fictional-id', repr(saved))

    def test_delete_is_idempotent_and_clears_configuration(self):
        self.store.save(KEYS, ACCOUNT)
        self.store.delete()
        self.store.delete()
        self.assertIsNone(self.store.load())

    def test_corrupt_oversized_and_unreadable_store_fail_closed(self):
        for data in (b'', b'not-a-credential-file', b'x' * 65537):
            with self.subTest(size=len(data)):
                self.path.write_bytes(data)
                with self.assertRaises(ConfigError):
                    self.store.load()
        self.path.unlink()
        self.path.mkdir()
        with self.assertRaises(ConfigError):
            self.store.load()

    def test_invalid_credentials_and_account_cannot_replace_previous_configuration(self):
        self.store.save(KEYS, ACCOUNT)
        cases = [(Credentials('', 'fictional-secret'), ACCOUNT),
                 (Credentials('fictional-id', 'a\nsecret'), ACCOUNT),
                 (Credentials('a' * 257, 'secret'), ACCOUNT),
                 (KEYS, 'main'), (KEYS, ' 123'), (KEYS, '0'),
                 (KEYS, '1' * 33), (KEYS, 123)]
        for credentials, account in cases:
            with self.subTest(credentials=repr(credentials), account=account):
                with self.assertRaises(ConfigError):
                    self.store.save(credentials, account)
                self.assertEqual(self.store.load().credentials, KEYS)

    def test_decrypted_structure_integrity_and_size_are_validated(self):
        self.store.save(KEYS, ACCOUNT)
        token = next(iter(self.protector.documents))
        malformed = [b'{}', b'[]', b'null', b'\xff', b'x' * 16385,
                     b'{"version":1,"version":1}',
                     json.dumps({'version': True, 'account': ACCOUNT,
                                 'access_key_id': 'id', 'access_key_secret': 'secret'}).encode(),
                     json.dumps({'version': 1, 'account': 'main',
                                 'access_key_id': 'id', 'access_key_secret': 'secret'}).encode(),
                     json.dumps({'version': 1, 'account': ACCOUNT,
                                 'access_key_id': 'id', 'access_key_secret': 'secret',
                                 'extra': 'field'}).encode()]
        for document in malformed:
            self.protector.documents[token] = b'HCRED1' + hashlib.sha256(document).digest() + document
            with self.assertRaises(ConfigError):
                self.store.load()
        self.protector.documents[token] = b'HCRED1' + bytes(32) + b'{}'
        with self.assertRaises(ConfigError):
            self.store.load()

    def test_protection_and_replace_failures_are_safe_and_atomic(self):
        self.store.save(KEYS, ACCOUNT)
        original = self.path.read_bytes()
        with patch('hots_chat.credentials.os.replace', side_effect=OSError('fictional-secret')):
            with self.assertRaises(ConfigError) as caught:
                self.store.save(Credentials('next-id', 'next-secret'), ACCOUNT)
        self.assertNotIn('fictional-secret', str(caught.exception))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        with patch.object(self.protector, 'unprotect', side_effect=RuntimeError('fictional-secret')):
            with self.assertRaises(ConfigError) as caught:
                self.store.load()
        self.assertNotIn('fictional-secret', str(caught.exception))

    def test_encrypt_failure_does_not_create_partial_configuration(self):
        with patch.object(self.protector, 'protect', side_effect=RuntimeError('fictional-secret')):
            with self.assertRaises(ConfigError) as caught:
                self.store.save(KEYS, ACCOUNT)
        self.assertNotIn('fictional-secret', str(caught.exception))
        self.assertIsNone(self.store.load())
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_delete_permission_error_is_redacted(self):
        self.store.save(KEYS, ACCOUNT)
        with patch('hots_chat.credentials.Path.unlink', side_effect=PermissionError('fictional-secret')):
            with self.assertRaises(ConfigError) as caught:
                self.store.delete()
        self.assertNotIn('fictional-secret', str(caught.exception))
        self.assertEqual(self.store.load().credentials, KEYS)

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI integration')
    def test_windows_current_user_dpapi_roundtrip_and_tamper_rejection(self):
        store = CredentialStore(self.path)
        store.save(KEYS, ACCOUNT)
        self.assertEqual(CredentialStore(self.path).load().credentials, KEYS)
        data = self.path.read_bytes()
        self.assertNotIn(b'fictional-secret', data)
        self.path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        with self.assertRaises(ConfigError):
            store.load()


if __name__ == '__main__':
    unittest.main()
