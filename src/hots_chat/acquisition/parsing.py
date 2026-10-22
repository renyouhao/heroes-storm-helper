import html
import re
from dataclasses import dataclass
from ..channels import SUPPORTED_CHANNELS


@dataclass(frozen=True)
class ParsedChat:
    time: str
    channel: str
    sender: str
    text: str


_TIMESTAMP = r'<s val="ChatDisplayTimeStamp">(?P<time>\d+:\d{2}(?::\d{2})?)</s><sp/>'
_PARTY_OPEN = '<c val="#ColorBattleNetParty">'
_WHISPER_OPEN = '<c val="#ColorBattleNetWhisper">'
_CHANNEL = re.compile(r'\[([^\[\]<>\r\n]{1,32})\]')


def _sender_text(value):
    return html.unescape(re.sub(r'<[^>]*>', '', value)).strip()


def has_allowed_chat_prefix(prefix):
    """Inspect only bounded metadata before HistoryReader chooses to read a body."""
    stamp = re.match(_TIMESTAMP, prefix)
    if not stamp:
        return False
    content = prefix[stamp.end():]
    if content.startswith(_WHISPER_OPEN):
        return re.match(re.escape(_WHISPER_OPEN) + r'[^<>]*<w>', content) is not None
    if content.startswith(_PARTY_OPEN):
        header = content[len(_PARTY_OPEN):]
        if re.match(r'对\s*\[', header):
            return True
    channel = _CHANNEL.search(content)
    return bool(channel and html.unescape(channel[1]) in SUPPORTED_CHANNELS
                and html.unescape(channel[1]) != '密语')


def parse_display_chat(source):
    """Accept known game chat structures; retain literal markup in message text."""
    if len(source) > 65536:
        return None
    stamp = re.match(_TIMESTAMP, source)
    if not stamp:
        return None
    content = source[stamp.end():]
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
        return ParsedChat(stamp['time'], '密语', sender, html.unescape(whisper['body']))
    if content.startswith(_PARTY_OPEN):
        outgoing = re.fullmatch(re.escape(_PARTY_OPEN)
            + r'对\s*\[队伍\]\s*说[:：]\s*<sp/></c>(?P<body>.*)', content, re.DOTALL)
        if outgoing:
            return ParsedChat(stamp['time'], '队伍', '我', html.unescape(outgoing['body']))
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
    return ParsedChat(stamp['time'], channel, sender, html.unescape(classic['body']))
