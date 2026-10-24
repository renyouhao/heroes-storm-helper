"""采集、翻译与界面共用的游戏内文字频道。"""

# Display history is shared by hero draft and a running match. Native Battle.net
# history continues to own party and whispers so the same text is not emitted twice.
DISPLAY_CHANNELS = ('团队', '征召团队', '所有玩家')
TEAM_CHANNELS = frozenset(('团队', '征召团队'))
SUPPORTED_CHANNELS = frozenset((*DISPLAY_CHANNELS, '队伍', '密语'))

CHANNEL_PRESETS = {
    '全部聊天': SUPPORTED_CHANNELS,
    '团队与所有玩家': frozenset(DISPLAY_CHANNELS),
    '仅团队': TEAM_CHANNELS,
    '仅征召团队': frozenset(('征召团队',)),
    '仅队伍': frozenset(('队伍',)),
    '仅密语': frozenset(('密语',)),
    '仅所有玩家': frozenset(('所有玩家',)),
}
