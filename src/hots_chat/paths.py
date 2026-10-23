from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class AppPaths:
    env_file: Path
    language_file: Path
    preferences_file: Path

    @property
    def credentials_file(self):
        return self.preferences_file.parent / 'credentials.dpapi'

    @property
    def diagnostics_dir(self):
        return self.preferences_file.parent / 'logs'

    @classmethod
    def from_root(cls, root: Path):
        root = root.resolve()
        # 保留既有目录，产品改名后继续使用原凭据和偏好。
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share')) / 'HotsChatHelper'
        return cls(root / '.env', base / 'language.sqlite3', base / 'preferences.json')
