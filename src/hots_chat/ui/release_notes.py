"""发布说明的有限 Markdown 排版模型；只处理文本，不加载外部内容。"""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class NoteSpan:
    text: str
    styles: tuple[str, ...] = ()


@dataclass(frozen=True)
class NoteBlock:
    kind: str
    spans: tuple[NoteSpan, ...]
    level: int = 0
    marker: str = ''
    depth: int = 0


_HEADING = re.compile(r'^\s{0,3}(#{1,6})\s+(.+?)\s*$')
_LIST = re.compile(r'^(\s*)([-+*]|\d{1,9}[.)])\s+(.+)$')
_FENCE = re.compile(r'^\s*(`{3,}|~{3,})[^`~]*$')
_INLINE = re.compile(
    r'(?P<ticks>`+)(?P<code>.+?)(?P=ticks)'
    r'|\*\*(?P<strong>.+?)\*\*|__(?P<strong_alt>.+?)__'
    r'|(?<!\w)\*(?P<emphasis>[^*]+?)\*(?!\w)'
    r'|(?<!\w)_(?P<emphasis_alt>[^_]+?)_(?!\w)'
    r'|\[(?P<label>[^]\n]+)\]\((?P<url>[^)\n]+)\)'
)


def inline_spans(text, styles=()):
    """粗体、斜体、行内代码及链接文字；HTML 始终作为普通文本保留。"""
    spans = []
    start = 0
    for match in _INLINE.finditer(text):
        if match.start() > start:
            spans.append(NoteSpan(text[start:match.start()], styles))
        if match.group('code') is not None:
            spans.append(NoteSpan(match.group('code'), styles + ('code',)))
        elif match.group('label') is not None:
            spans.extend(inline_spans(match.group('label'), styles))
            spans.append(NoteSpan('（' + match.group('url') + '）', styles))
        else:
            strong = match.group('strong') or match.group('strong_alt')
            content = strong or match.group('emphasis') or match.group('emphasis_alt')
            spans.extend(inline_spans(content, styles + (('strong' if strong else 'emphasis'),)))
        start = match.end()
    if start < len(text):
        spans.append(NoteSpan(text[start:], styles))
    return tuple(spans)


def _east_asian(char):
    return ('\u2e80' <= char <= '\u9fff' or '\uac00' <= char <= '\ud7af'
            or '\uf900' <= char <= '\ufaff')


def join_soft_lines(lines):
    """还原作者为了源码宽度插入的换行，避免拆开的中文词被加入空格。"""
    result = []
    previous = ''
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if previous:
            left, right = previous[-1], line[0]
            close_punctuation = '，。！？；：、）》】」』…,.!?;:)]}'
            opening_punctuation = '（《【「『([{'
            separator = '' if ((_east_asian(left) and _east_asian(right))
                               or right in close_punctuation or left in opening_punctuation) else ' '
            result.append(separator)
        result.append(line)
        previous = line
    return ''.join(result)


def parse_release_notes(source):
    """保留标题、条目、空行分隔的段落与代码块，不实现完整 Markdown。"""
    blocks = []
    lines = []
    kind, marker, depth = 'paragraph', '', 0
    fence = None

    def flush():
        nonlocal lines
        if lines:
            text = '\n'.join(lines) if kind == 'code' else join_soft_lines(lines)
            spans = (NoteSpan(text, ('code',)),) if kind == 'code' else inline_spans(text)
            blocks.append(NoteBlock(kind, spans, marker=marker, depth=depth))
            lines = []

    for line in source.splitlines():
        fence_match = _FENCE.match(line)
        if fence is not None:
            if line.strip() == fence:
                flush()
                fence, kind = None, 'paragraph'
            else:
                lines.append(line)
            continue
        if fence_match:
            flush()
            fence, kind, marker, depth = fence_match.group(1), 'code', '', 0
            continue
        if not line.strip():
            flush()
            kind, marker, depth = 'paragraph', '', 0
            continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            title = re.sub(r'\s+#+$', '', heading.group(2))
            blocks.append(NoteBlock('heading', inline_spans(title), level=len(heading.group(1))))
            kind, marker, depth = 'paragraph', '', 0
            continue
        item = _LIST.match(line)
        if item:
            flush()
            kind = 'list'
            marker = '•' if item.group(2) in ('-', '+', '*') else item.group(2)
            depth = min(4, len(item.group(1).expandtabs(4)) // 2)
            lines.append(item.group(3))
            continue
        lines.append(line)
    flush()
    return tuple(blocks)
