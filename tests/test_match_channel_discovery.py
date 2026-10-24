"""对局来源必须定位到自己的频道；只使用合成内存和虚拟时间。"""
import struct
import threading
import unittest

from hots_chat.acquisition.discovery import IncrementalDiscovery
from hots_chat.acquisition.memory import GameProcess
from hots_chat.acquisition.source import ChatSource
from test_history import FakeMemory, chat_line
from test_lobby_recovery_latency import VirtualStop
from test_party_whisper_acquisition import party_out, whisper_out


class ConcurrentHistories:
    path = r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'

    def __init__(self):
        self.histories = {}

    def set_history(self, slot, lines):
        memory = FakeMemory()
        memory.set_nodes(lines)
        blocks = {}
        delta = slot * 0x1000000
        for address, data in memory.blocks.items():
            count = 2 if address == memory.root else 3
            values = list(struct.unpack('<' + 'Q' * count, data[:count * 8]))
            values[0] += delta
            values[1] += delta
            blocks[address + delta] = struct.pack('<' + 'Q' * count, *values) + data[count * 8:]
        self.histories[slot] = blocks

    def read(self, address, count):
        for blocks in self.histories.values():
            for base, data in blocks.items():
                if base <= address < base + len(data):
                    return data[address - base:address - base + count]
        return b''

    def regions(self):
        for blocks in list(self.histories.values()):
            for address, data in sorted(blocks.items()):
                yield address, len(data), 4

    def close(self):
        pass


class MatchChannelDiscoveryTests(unittest.TestCase):
    def run_source(self, game, advance=lambda now: None, deadline=12):
        clock, arrivals = [0.], []
        source = ChatSource(
            process_finder=lambda: [GameProcess(1, game.path, 98348)],
            game_factory=lambda pid: game, clock=lambda: clock[0])
        source._stop = VirtualStop(clock, deadline, advance)

        def receive(event):
            if event.kind == 'chat':
                arrivals.append((clock[0], event.payload.text))

        source._run(receive)
        return arrivals

    def test_lobby_only_history_does_not_delay_first_new_match_message(self):
        for lobby_line in (party_out, whisper_out):
            with self.subTest(channel=lobby_line.__name__):
                game = ConcurrentHistories()
                game.set_history(0, [lobby_line('lobby baseline')])

                def advance(now):
                    if now >= 1 and 1 not in game.histories:
                        game.set_history(1, [chat_line('first team message')])

                arrivals = self.run_source(game, advance)
                self.assertEqual([text for _, text in arrivals], ['first team message'])
                self.assertLess(arrivals[0][0], 10, '大厅记录不应让首次对局接入等待30秒探测')

    def test_same_history_becoming_a_match_is_rechecked_after_lobby_baseline(self):
        game = ConcurrentHistories()
        game.set_history(0, [party_out('lobby baseline')])
        appended = []

        def advance(now):
            if now >= 1 and not appended:
                appended.append(True)
                game.set_history(0, [party_out('lobby baseline'), chat_line('team in same tree')])

        arrivals = self.run_source(game, advance)
        self.assertEqual([text for _, text in arrivals], ['team in same tree'])

    def test_active_old_history_does_not_prevent_discovery_of_new_match(self):
        game = ConcurrentHistories()
        old_lines = [chat_line('old baseline')]
        game.set_history(0, old_lines)
        last_second = [0]

        def advance(now):
            second = int(now)
            if second > last_second[0]:
                last_second[0] = second
                old_lines.append(chat_line('old stream ' + str(second)))
                game.set_history(0, old_lines)
            if now >= 1 and 1 not in game.histories:
                game.set_history(1, [chat_line('new match first')])

        arrivals = self.run_source(game, advance, deadline=35)
        self.assertEqual([text for _, text in arrivals].count('new match first'), 1,
                         '旧记录持续追加消息不能永久阻止新对局的发现')

    def test_party_updates_on_an_old_mixed_tree_do_not_steal_active_match_reader(self):
        game = ConcurrentHistories()
        active = [chat_line('active baseline')]
        mixed = [chat_line('historical team'), party_out('party baseline')]
        game.set_history(0, active)
        game.set_history(1, mixed)
        last_second = [0]

        def advance(now):
            second = int(now)
            if second > last_second[0]:
                last_second[0] = second
                active.append(chat_line('active ' + str(second)))
                mixed.append(party_out('party ' + str(second)))
                game.set_history(0, active)
                game.set_history(1, mixed)

        arrivals = self.run_source(game, advance, deadline=36)
        texts = [text for _, text in arrivals]
        self.assertIn('active 35', texts, '只有大厅消息变化的旧树不应抢走健康的对局reader')
        self.assertEqual(texts.count('historical team'), 1)


class IncrementalMatchChannelDiscoveryTests(unittest.TestCase):
    def test_team_append_is_found_before_scan_of_party_only_root_finishes(self):
        memory = FakeMemory()
        party, team = party_out('party baseline'), chat_line('first team message')
        node, = memory.set_nodes([party])
        party_bytes = len(party.encode('utf-8'))
        # The second node occupies a later part of the same ongoing scan.
        regions = [(node + 24, party_bytes, 4),
                   (node + 0x10000 + 24, len(team.encode('utf-8')), 4)]
        memory.regions = lambda: iter(regions)
        scanner = IncrementalDiscovery(memory, threading.Event(), channels=('团队', '所有玩家'))

        self.assertIsNone(scanner.step(byte_budget=party_bytes, time_budget=60))
        self.assertFalse(scanner.finished)
        memory.set_nodes([party, team])

        result = scanner.step(time_budget=60)

        self.assertIsNotNone(result, '同一轮扫描中追加的团队消息不应等到下一轮扫描')
        self.assertEqual(result.root, memory.root)

    def test_reused_head_becoming_team_is_found_during_the_same_scan(self):
        memory = FakeMemory()
        oldest = party_out('party baseline')
        team = chat_line('same message body')
        party = team.replace('[团队]', '[队伍]', 1)
        nodes = memory.set_nodes([oldest, party])
        oldest_bytes = len(oldest.encode('utf-8'))
        regions = [(nodes[0] + 24, oldest_bytes, 4),
                   (nodes[1] + 24, len(party.encode('utf-8')), 4)]
        memory.regions = lambda: iter(regions)
        scanner = IncrementalDiscovery(memory, threading.Event(), channels=('团队', '所有玩家'))

        self.assertIsNone(scanner.step(byte_budget=oldest_bytes, time_budget=60))
        self.assertFalse(scanner.finished)
        root_before = memory.read(memory.root, 16)
        head_before = memory.read(nodes[1], 24)
        memory.set_nodes([oldest, team])
        self.assertEqual(memory.read(memory.root, 16), root_before)
        self.assertEqual(memory.read(nodes[1], 24), head_before,
                         '复用头节点且字符串长度不变，必须检查内容变化')

        result = scanner.step(time_budget=60)

        self.assertIsNotNone(result, '头地址和长度不变时也应发现新出现的团队消息')
        self.assertEqual(result.root, memory.root)


if __name__ == '__main__':
    unittest.main()
