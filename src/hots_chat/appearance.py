"""不依赖界面工具包的主题标识与旧偏好迁移。"""

DEFAULT_THEME = 'lavender'
THEME_NAMES = {'lavender': '柔雾紫'}


def normalize_theme(value):
    """统一使用柔雾紫；旧主题和未知值只迁移配色，不重置其余偏好。"""
    return DEFAULT_THEME
