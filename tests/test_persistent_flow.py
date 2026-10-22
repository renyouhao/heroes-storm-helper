"""真实双来源的大厅→对局→大厅联动；只使用合成内存，不访问游戏或云。"""
import threading
import unittest
from collections import Counter

from hots_chat.acquisition.lobby_native import LobbyReader
from hots_chat.acquisition.lobby_source import LobbySource
from hots_chat.acquisition.memory import GameProcess
from hots_chat.acquisition.persistent import PersistentChatSource
from hots_chat.acquisition.source import ChatSource
from test_history import FakeMemory, chat_line
from test_lobby_native import FixtureMetadata, LobbyMemory
from test_party_whisper_acquisition import party_out, whisper_out


class SharedGameMemory:
    """同一模拟进程内并存的 native 大厅历史与随场景出现的 timestamp 历史。"""

    def __init__(self):
        self.condition = threading.Condition(threading.RLock())
        self.lobby = LobbyMemory()
        self.match = None
        self.clock = 100.
        self.reads = Counter()

    def set_lobby(self, entries):
        with self.condition:
            self.lobby.records(entries)

    def enter_match(self, entries):
        with self.condition:
            self.match = FakeMemory()
            self.match.set_nodes(entries)
            self.clock += 6

    def update_match(self, entries):
        with self.condition:
            self.match.set_nodes(entries)

    def leave_match(self):
        with self.condition:
            self.match = None

    def now(self):
        with self.condition:
            return self.clock

    def read(self, address, count, owner):
        with self.condition:
            self.reads[owner] += 1
            self.condition.notify_all()
            if address == self.lobby.image_base and count == 2:
                return b'MZ'
            if self.match is not None and address < self.lobby.base:
                return self.match.read(address, count)
            return self.lobby.read(address, count)

    def regions(self):
        with self.condition:
            match = list(self.match.regions()) if self.match is not None else []
            return match + list(self.lobby.regions())


class IndependentGameHandle:
    path = r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'

    def __init__(self, backend, pid):
        self.backend, self.pid = backend, pid
        self.owner = threading.current_thread().name
        self.read_buffer = bytearray(1024 * 1024)
        self.closed = 0

    def read(self, address, count):
        if self.closed:
            raise OSError('synthetic handle is closed')
        data = self.backend.read(address, count, self.owner)
        self.read_buffer[:len(data)] = data
        return bytes(self.read_buffer[:len(data)])

    def regions(self):
        yield from self.backend.regions()

    def close(self):
        self.closed += 1


class PersistentFlowTests(unittest.TestCase):
    def test_lobby_match_lobby_flow_keeps_order_cursors_generation_and_independent_handles(self):
        backend = SharedGameMemory()
        baseline = [('队伍', 'old party baseline'), ('密语', 'old whisper baseline')]
        backend.set_lobby(baseline)
        handles, events = [], []
        received = threading.Condition()

        def game_factory(pid):
            handle = IndependentGameHandle(backend, pid)
            with backend.condition:
                handles.append(handle)
            return handle

        def lobby_factory(**options):
            return LobbySource(
                **options, module_base_finder=lambda pid: backend.lobby.image_base,
                wall_clock=lambda: (134053920010000000 - 116444736000000000) / 10000000,
                reader_factory=lambda memory, root, image_base, **reader_options: LobbyReader(
                    memory, root, image_base, metadata_factory=FixtureMetadata, **reader_options))

        def emit(event):
            with received:
                events.append(event)
                received.notify_all()

        def chats():
            return [event.payload for event in events if event.kind == 'chat']

        def wait_for(predicate, message):
            with received:
                self.assertTrue(received.wait_for(predicate, timeout=4), message)

        source = PersistentChatSource(
            process_finder=lambda: [GameProcess(42, IndependentGameHandle.path, 98348)],
            game_factory=game_factory, clock=backend.now,
            match_factory=ChatSource, lobby_factory=lobby_factory)
        source.start(emit)
        try:
            wait_for(lambda: any(event.kind == 'status' and '已连接大厅' in str(event.payload)
                                 for event in events), '大厅须在没有对局历史时独立建立基线')
            wait_for(lambda: any(event.kind == 'capture_state' and '等待对局聊天' in str(event.payload)
                                 for event in events), '对局空历史须进入自动等待')
            self.assertEqual(chats(), [], '两个来源首次接入不能回放旧历史')
            generation = source.generation
            lobby_generation = source.lobby_source.generation

            live_lobby = baseline + [('队伍', 'party ready'), ('密语', 'hello lobby'),
                                     ('密语', 'same private'), ('密语', 'same private')]
            backend.set_lobby(live_lobby)
            wait_for(lambda: len(chats()) == 4, '大厅新增队伍、密语与连续同文均须按序收到')

            match_baseline = chat_line('match baseline before connection')
            backend.enter_match([match_baseline])
            wait_for(lambda: any(event.kind == 'status' and '对局：已连接' in str(event.payload)
                                 for event in events), '进入对局后须自动接入 timestamp 历史')
            self.assertEqual(len(chats()), 5, '首次接入对局也补收首条，已有大厅消息保留')
            connected_match_generation = source.match_source.generation
            # Simulate both representations of the same party/whisper record.
            # Native lobby owns these channels, even during a running match.
            backend.update_match([match_baseline, party_out('party ready'), whisper_out('hello lobby'),
                                  chat_line('team attack'), chat_line('team attack')])
            wait_for(lambda: len(chats()) == 7, '对局团队同文两条须保留，跨格式队伍/密语不得重复')

            backend.leave_match()
            wait_for(lambda: source.match_source.generation > connected_match_generation and
                     any(event.kind == 'capture_state' and '等待对局聊天' in str(event.payload)
                         for event in events), '离开对局后须在同一句柄上自动等待下一局')
            backend.set_lobby(live_lobby + [('密语', 'back in lobby')])
            wait_for(lambda: len(chats()) == 8, '回大厅后须继续从原生游标接收新密语')
            with backend.condition:
                previous_reads = backend.reads['lobby-source']
                self.assertTrue(backend.condition.wait_for(
                    lambda: backend.reads['lobby-source'] > previous_reads, timeout=2),
                    '再轮询一次原生历史，确认旧消息不会重放')

            self.assertEqual([(chat.channel, chat.text) for chat in chats()], [
                ('队伍', 'party ready'), ('密语', 'hello lobby'),
                ('密语', 'same private'), ('密语', 'same private'),
                ('团队', 'match baseline before connection'),
                ('团队', 'team attack'), ('团队', 'team attack'), ('密语', 'back in lobby')])
            self.assertEqual(len({chat.message_id for chat in chats()}), 8)
            self.assertEqual({chat.generation for chat in chats()}, {generation})
            self.assertEqual(source.generation, generation, '换场景不能清掉另一来源的会话代')
            self.assertEqual(source.lobby_source.generation, lobby_generation)
            self.assertEqual(len([event for event in events if event.kind == 'generation']), 1)
            self.assertEqual(len(handles), 2, '整个场景切换过程只允许每个worker各开一次句柄')
            self.assertEqual({handle.owner for handle in handles}, {'chat-source', 'lobby-source'})
            self.assertTrue(all(handle.backend is backend and handle.pid == 42 for handle in handles))
            self.assertIsNot(handles[0], handles[1])
            self.assertIsNot(handles[0].read_buffer, handles[1].read_buffer)
            self.assertEqual([handle.closed for handle in handles], [0, 0])
            self.assertTrue(source.match_source.running and source.lobby_source.running)
        finally:
            source.stop()
        self.assertEqual([handle.closed for handle in handles], [1, 1])
        self.assertFalse(source.running)


if __name__ == '__main__':
    unittest.main()
