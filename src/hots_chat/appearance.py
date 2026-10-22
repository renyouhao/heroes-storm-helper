"""不依赖界面工具包的主题标识与旧偏好迁移。"""

DEFAULT_THEME = 'graphite'
THEME_NAMES = {'moonlight': '月白蓝', 'graphite': '石墨紫', 'mint': '雾青'}
LEGACY_THEMES = {'amethyst': 'graphite', 'ice': 'moonlight'}


def normalize_theme(value):
    """未知主题只回退配色，不丢弃同一份配置里的位置和快捷键。"""
    if not isinstance(value, str):
        return DEFAULT_THEME
    value = LEGACY_THEMES.get(value, value)
    return value if value in THEME_NAMES else DEFAULT_THEME
