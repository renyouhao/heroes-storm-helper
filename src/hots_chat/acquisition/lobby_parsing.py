"""Parse the observed lobby chat envelopes without interpreting message markup."""

import html
import re

from .parsing import ParsedChat


_ATTRIBUTE = r'[^"<>\r\n{}]+'
_ANCHOR_OPEN = rf'<a name="{_ATTRIBUTE}" href="{_ATTRIBUTE}">'
_NAME = r'[^\[\]<>\r\n{}]+'
_WHISPER = re.compile(
    r'<s val="BattleChatWhisper">' + _ANCHOR_OPEN
    + rf'(?:\[(?P<incoming>{_NAME})\] ?悄悄地说：'
      rf'|悄悄地告诉\[(?P<outgoing>{_NAME})\]说：)'
      r'</a> (?P<body>.*)</s>',
    re.DOTALL,
)
_PARTY = re.compile(
    r'<s val="BattleChatParty">' + _ANCHOR_OPEN + r'\[队伍\]</a> '
    + _ANCHOR_OPEN + rf'(?P<sender>{_NAME}):</a> (?P<body>.*)</s>',
    re.DOTALL,
)


def parse_lobby_chat(source, time=''):
    """Accept observed Chinese lobby party/whisper headers and decode entities once.

    Lobby rows do not contain timestamps, so ``time`` belongs to the caller.
    Only the one observed space between the final anchor and body is consumed.
    The final closing style belongs to the envelope; other body tags are literal.
    """
    if len(source) > 65536:
        return None
    whisper = _WHISPER.fullmatch(source)
    if whisper:
        sender = html.unescape(whisper['incoming'] or whisper['outgoing']).strip()
        if not sender or '{' in sender or '}' in sender:
            return None
        if whisper['outgoing'] is not None:
            sender = '我 → ' + sender
        return ParsedChat(time, '密语', sender, html.unescape(whisper['body']))
    party = _PARTY.fullmatch(source)
    if party:
        sender = html.unescape(party['sender']).strip()
        if not sender or '{' in sender or '}' in sender:
            return None
        return ParsedChat(time, '队伍', sender, html.unescape(party['body']))
    return None
