from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class AppPaths:
    env_file: Path
    ledger_file: Path
    language_file: Path
    preferences_file: Path

    @property
    def credentials_file(self):
        return self.ledger_file.parent / 'credentials.dpapi'

    @classmethod
    def from_root(cls, root: Path):
        root = root.resolve()
        # 保留既有目录，产品改名后继续使用原凭据、账本和偏好。
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share')) / 'HotsChatHelper'
        return cls(root / '.env', base / 'usage.sqlite3', base / 'language.sqlite3', base / 'preferences.json')
