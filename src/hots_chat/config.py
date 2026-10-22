from dataclasses import dataclass, field
from pathlib import Path
import re


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Credentials:
    access_key_id: str = field(repr=False)
    access_key_secret: str = field(repr=False)


def load_credentials(path: Path) -> Credentials:
    names = ('ALIBABA_CLOUD_ACCESS_KEY_ID', 'ALIBABA_CLOUD_ACCESS_KEY_SECRET')
    values = {}
    try:
        if path.stat().st_size > 16384:
            raise ValueError
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, sep, value = line.partition('=')
            if not sep or key not in names or key in values:
                raise ValueError
            value = value.strip()
            if value[:1] in ('"', "'"):
                quote = value[0]
                end = value.find(quote, 1)
                if end < 0 or (value[end + 1:].strip() and not value[end + 1:].lstrip().startswith('#')):
                    raise ValueError
                value = value[1:end]
            else:
                value = value.split(' #', 1)[0].strip()
            if not re.fullmatch(r'[A-Za-z0-9_./+\-=]{1,256}', value):
                raise ValueError
            values[key] = value
        if set(values) != set(names):
            raise ValueError
    except (OSError, UnicodeError, ValueError):
        raise ConfigError('本机 .env 缺失或格式无效，请检查两个访问密钥字段。') from None
    return Credentials(values[names[0]], values[names[1]])
