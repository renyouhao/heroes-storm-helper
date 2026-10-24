import html
import re
from dataclasses import dataclass
from ..channels import DISPLAY_CHANNELS, SUPPORTED_CHANNELS


@dataclass(frozen=True)
class ParsedChat:
    time: str
    channel: str
    sender: str
    text: str


_TIMESTAMP = r'<s val="ChatDisplayTimeStamp">(?P<time>\d+:\d{2}(?::\d{2})?)</s><sp/>'
TIMESTAMP_PREFIX = b'<s val="ChatDisplayTimeStamp">'
HISTORY_PREFIXES = (TIMESTAMP_PREFIX, b'<c val="', b'<sp/><c val="')
_UNTIMED_HEADER = re.compile(r'(?:<sp/>)?<c val="[^"<>\r\n]{1,32}">\[('
    + '|'.join(re.escape(channel) for channel in DISPLAY_CHANNELS) + r')\]')
_COLOR_CHANNEL = re.compile(r'(?:<sp/>)?<c val="[^"<>\r\n]{1,32}">\[([^\[\]<>\r\n]{1,32})\]')
_PARTY_OPEN = '<c val="#ColorBattleNetParty">'
_WHISPER_OPEN = '<c val="#ColorBattleNetWhisper">'
_CHANNEL = re.compile(r'\[([^\[\]<>\r\n]{1,32})\]')


def _sender_text(value):
    return html.unescape(re.sub(r'<[^>]*>', '', value)).strip()


def has_history_prefix(prefix):
    """Allow known inline headers while list validation proves the owner."""
    return prefix.startswith(HISTORY_PREFIXES)


def _chat_content(source):
    stamp = re.match(_TIMESTAMP, source)
    if stamp:
        return stamp['time'], source[stamp.end():]
    # Untimed messages need an anchored team/all header, never a channel found
    # later inside a system line or user body. Malformed timestamps stay invalid.
    if _UNTIMED_HEADER.match(source):
        return '', source[5:] if source.startswith('<sp/>') else source
    return None


def chat_prefix_rejection_reason(prefix):
    """Classify bounded metadata using fixed reasons, never copying its text."""
    parts = _chat_content(prefix)
    if parts is None:
        if prefix.startswith(('<c val="', '<sp/><c val="')):
            return 'channel_unsupported' if _COLOR_CHANNEL.match(prefix) else 'message_format_rejected'
        return 'timestamp_format_rejected'
    _, content = parts
    if content.startswith(_WHISPER_OPEN):
        return (None if re.match(re.escape(_WHISPER_OPEN) + r'[^<>]*<w>', content)
                else 'message_format_rejected')
    if content.startswith(_PARTY_OPEN):
        header = content[len(_PARTY_OPEN):]
        if re.match(r'对\s*\[', header):
            return None
    channel = _CHANNEL.search(content)
    if not channel:
        return 'message_format_rejected'
    if html.unescape(channel[1]) not in SUPPORTED_CHANNELS or html.unescape(channel[1]) == '密语':
        return 'channel_unsupported'
    return None


def has_allowed_chat_prefix(prefix):
    """Inspect only bounded metadata before HistoryReader chooses to read a body."""
    return chat_prefix_rejection_reason(prefix) is None


def parse_display_chat(source):
    """Accept known game chat structures; retain literal markup in message text."""
    if len(source) > 65536:
        return None
    parts = _chat_content(source)
    if parts is None:
        return None
    timestamp, content = parts
    if content.startswith(_WHISPER_OPEN):
        whisper = re.fullmatch(re.escape(_WHISPER_OPEN)
            + r'(?P<before>[^<>\r\n]*)<w>(?P<sender>.*?)</w>'
              r'(?P<after>[^<>\r\n]*[:：]\s*)<sp/></c>(?P<body>.*)',
            content, re.DOTALL)
        if not whisper:
            return None
        sender = _sender_text(whisper['sender'])
        if not sender:
            return None
        before, after = whisper['before'], whisper['after'].lstrip()
        if ('[' in before) != after.startswith(']'):
            return None
        if re.match(r'对\s*\[', before):
            sender = '我 → ' + sender
        return ParsedChat(timestamp, '密语', sender, html.unescape(whisper['body']))
    if content.startswith(_PARTY_OPEN):
        outgoing = re.fullmatch(re.escape(_PARTY_OPEN)
            + r'对\s*\[队伍\]\s*说[:：]\s*<sp/></c>(?P<body>.*)', content, re.DOTALL)
        if outgoing:
            return ParsedChat(timestamp, '队伍', '我', html.unescape(outgoing['body']))
    classic = re.fullmatch(r'(?P<prefix>.*?)<w>(?P<sender>.*?)</w></c>: </c>(?P<body>.*)',
                          content, re.DOTALL)
    if not classic:
        return None
    channel = _CHANNEL.search(classic['prefix'])
    if not channel:
        return None
    channel = html.unescape(channel[1])
    if channel not in SUPPORTED_CHANNELS or channel == '密语':
        return None
    sender = _sender_text(classic['sender'])
    if not sender:
        return None
    return ParsedChat(timestamp, channel, sender, html.unescape(classic['body']))
