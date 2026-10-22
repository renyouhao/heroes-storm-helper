"""采集、翻译与界面共用的游戏内文字频道。"""

SUPPORTED_CHANNELS = frozenset(('团队', '队伍', '密语', '所有玩家'))

CHANNEL_PRESETS = {
    '全部聊天': SUPPORTED_CHANNELS,
    '团队与所有玩家': frozenset(('团队', '所有玩家')),
    '仅团队': frozenset(('团队',)),
    '仅队伍': frozenset(('队伍',)),
    '仅密语': frozenset(('密语',)),
    '仅所有玩家': frozenset(('所有玩家',)),
}
