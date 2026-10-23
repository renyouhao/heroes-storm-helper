"""只规范游戏正文中的图片表情，保留其他字面文本。"""
import re


_MAX_BODY_BYTES = 4096
_IMAGE_TAG = re.compile(r'''<img(?=[\t\n\f\r /])(?:[^<>"']|"[^"<]*"|'[^'<]*')*>''', re.IGNORECASE)
_ATTRIBUTE = re.compile(
    r'''[\t\n\f\r ]+([a-z_:][a-z0-9_.:-]*)[\t\n\f\r ]*=[\t\n\f\r ]*'''
    r'''(?:"([^"<]*)"|'([^'<]*)'|([^\t\n\f\r <>"'=`]+))''', re.IGNORECASE)


def _replace_image(match):
    tag = match.group()
    attributes = tag[4:-1].rstrip()
    if attributes.endswith('/'):
        attributes = attributes[:-1].rstrip()
    position, has_resource = 0, False
    while position < len(attributes):
        attribute = _ATTRIBUTE.match(attributes, position)
        if attribute is None:
            return tag
        name, *values = attribute.groups()
        if name.lower() in ('path', 'src') and any(value and value.strip() for value in values):
            has_resource = True
        position = attribute.end()
    return '[表情]' if has_resource else tag


def normalize_chat_text(text):
    """仅处理原始正文安全上限内、带 path/src 的完整 img 标签。"""
    if len(text) > _MAX_BODY_BYTES or '<' not in text or '<img' not in text.lower():
        return text
    # 先检查原始字节数，不能靠缩短资源标签绕过正文的云请求上限。
    if len(text.encode('utf-8')) > _MAX_BODY_BYTES:
        return text
    return _IMAGE_TAG.sub(_replace_image, text)


def character_count(text):
    """按 UTF-16 代码单元统计文本，用于请求长度与短时限流。"""
    return max(len(text), len(text.encode('utf-16-le')) // 2)
