import tempfile
import unittest
from pathlib import Path

from hots_chat.config import ConfigError, load_credentials
from hots_chat.paths import AppPaths


class ConfigTests(unittest.TestCase):
    def test_credentials_repr_redacts(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / '.env'
            p.write_text('ALIBABA_CLOUD_ACCESS_KEY_ID="fake-id"\nALIBABA_CLOUD_ACCESS_KEY_SECRET=fictional-secret\n', encoding='utf-8')
            c = load_credentials(p)
            self.assertEqual(c.access_key_id, 'fake-id')
            self.assertNotIn('fictional-secret', repr(c))
            self.assertNotIn('fake-id', repr(c))

    def test_env_missing_or_duplicate_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / '.env'
            for body in (b'', b'\xff', b'ALIBABA_CLOUD_ACCESS_KEY_ID=x\nALIBABA_CLOUD_ACCESS_KEY_ID=fictional-secret', b'ALIBABA_CLOUD_ACCESS_KEY_ID=$(secret)'):
                p.write_bytes(body)
                with self.assertRaises(ConfigError) as ctx:
                    load_credentials(p)
                self.assertNotIn('fictional-secret', str(ctx.exception))

    def test_paths_ignore_working_directory(self):
        root = Path(tempfile.gettempdir()) / 'synthetic-project'
        paths = AppPaths.from_root(root)
        self.assertEqual(paths.env_file, root.resolve() / '.env')
        self.assertIsInstance(paths.ledger_file, Path)

