import threading
import time
import unittest

from hots_chat.acquisition.lobby_source import LobbySource
from hots_chat.acquisition.lobby_native import LobbyReader
from hots_chat.acquisition.history import PollResult
from hots_chat.acquisition.memory import GameProcess
from test_lobby_native import LobbyMemory, FixtureMetadata


class LobbyGame(LobbyMemory):
    path = r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'
    def __init__(self):
        super().__init__(); self.closed = 0; self.fail_reads = False
    def read(self, address, count):
        if self.fail_reads:raise OSError('unreadable game')
        if address == self.image_base and count == 2:return b'MZ'
        return super().read(address, count)
    def close(self):self.closed += 1


class LobbySourceTests(unittest.TestCase):
    def wait_for(self, predicate):
        end = time.monotonic() + 3
        while not predicate() and time.monotonic() < end:time.sleep(.01)
        self.assertTrue(predicate(), '大厅采集未达到预期状态')

    def setup_source(self, game=None, processes=None, factory=None, reader_factory=None):
        game = game or LobbyGame()
        processes = processes if processes is not None else [GameProcess(1, game.path, 98348)]
        clock, events, opened = [100.], [], []
        def create(pid):
            opened.append(pid)
            return factory(pid) if factory else game
        source = LobbySource(process_finder=lambda:list(processes), game_factory=create,
                             module_base_finder=lambda pid:game.image_base, clock=lambda:clock[0],
                             wall_clock=lambda:(134053920000000000 - 116444736000000000) / 10000000,
                             reader_factory=reader_factory or (lambda memory, root, image_base, **options:
                             LobbyReader(memory, root, image_base, metadata_factory=FixtureMetadata, **options)))
        source.start(events.append); self.addCleanup(source.stop)
        return source, game, processes, clock, events, opened

    def connected(self, events):return any(e.kind == 'status' and str(e.payload).startswith('已连接大厅') for e in events)
    def chats(self, events):return [e.payload.text for e in events if e.kind == 'chat']

    def test_captures_lobby_without_a_match_and_initial_history_is_not_replayed(self):
        game = LobbyGame(); game.records([('队伍', 'baseline')])
        source, game, processes, clock, events, opened = self.setup_source(game)
        self.wait_for(lambda:self.connected(events))
        self.assertEqual(self.chats(events), [])
        game.records([('队伍', 'baseline'), ('密语', 'hello')])
        self.wait_for(lambda:self.chats(events) == ['hello'])
        self.assertTrue(source.running)
        self.assertEqual(opened, [1])

    def test_empty_lobby_keeps_handle_and_connects_when_chat_appears(self):
        source, game, processes, clock, events, opened = self.setup_source()
        self.wait_for(lambda:any(e.kind == 'capture_state' and '等待' in str(e.payload) for e in events))
        self.assertEqual(game.closed, 0)
        game.records([('队伍', 'baseline')])
        def connected_after_clock_tick():
            # The first empty scan may still be finishing when the row appears.
            # Keep virtual time moving past whichever retry deadline it sets.
            clock[0] += 6.
            return self.connected(events)
        self.wait_for(connected_after_clock_tick)
        self.assertEqual(opened, [1]); self.assertEqual(self.chats(events), [])

    def test_reconnect_resumes_cursor_and_does_not_repeat_seen_messages(self):
        game = LobbyGame(); game.records([('队伍', 'baseline')])
        source, game, processes, clock, events, opened = self.setup_source(game)
        self.wait_for(lambda:self.connected(events))
        game.records([('队伍', 'baseline'), ('密语', 'seen')]); self.wait_for(lambda:self.chats(events) == ['seen'])
        source.reconnect()
        self.wait_for(lambda:len([e for e in events if e.kind == 'status' and str(e.payload).startswith('已连接大厅')]) >= 2)
        game.records([('队伍', 'baseline'), ('密语', 'seen'), ('队伍', 'new')])
        self.wait_for(lambda:self.chats(events) == ['seen', 'new'])
        self.assertEqual(opened, [1])

    def test_game_restart_uses_new_baseline_and_releases_old_handle(self):
        first, second = LobbyGame(), LobbyGame()
        first.records([('队伍', 'old')]); second.records([('密语', 'new baseline')])
        source, game, processes, clock, events, opened = self.setup_source(first, factory=lambda pid:first if pid == 1 else second)
        self.wait_for(lambda:self.connected(events)); processes[:] = [GameProcess(2, second.path, 98348)]
        self.wait_for(lambda:opened == [1, 2] and self.connected(events))
        self.assertEqual(first.closed, 1); self.assertEqual(self.chats(events), [])
        second.records([('密语', 'new baseline'), ('队伍', 'after restart')])
        self.wait_for(lambda:self.chats(events) == ['after restart'])

    def test_hard_error_releases_handle_but_manual_reconnect_skips_backoff(self):
        first, second = LobbyGame(), LobbyGame()
        first.records([('队伍', 'old')]); second.records([('密语', 'baseline')])
        calls = []
        def create(pid):
            calls.append(pid)
            return first if len(calls) == 1 else second
        source, game, processes, clock, events, opened = self.setup_source(first, factory=create)
        self.wait_for(lambda:self.connected(events)); first.fail_reads = True
        self.wait_for(lambda:first.closed == 1)
        clock[0] = 101.; source.reconnect()
        self.wait_for(lambda:len(opened) == 2 and len([e for e in events if e.kind == 'status' and str(e.payload).startswith('已连接大厅')]) >= 2)
        self.assertEqual(self.chats(events), [])

    def test_stop_during_wait_releases_handle_and_performs_no_later_reads(self):
        source, game, processes, clock, events, opened = self.setup_source()
        self.wait_for(lambda:bool(opened)); source.stop()
        self.assertFalse(source.running); self.assertEqual(game.closed, 1)
        reads = len(game.calls); game.records([('队伍', 'after stop')]); time.sleep(.6)
        self.assertEqual(len(game.calls), reads); self.assertEqual(self.chats(events), [])

    def test_changing_initial_snapshot_eventually_reports_connected(self):
        game = LobbyGame(); game.records([('队伍', 'baseline')])
        class ChangingReader(LobbyReader):
            def __init__(self, *args, **options):
                super().__init__(*args, metadata_factory=FixtureMetadata, **options)
                self.first = True
            def poll(self):
                if self.first:
                    self.first = False
                    return PollResult([], False, True, 16)
                return super().poll()
        source, game, processes, clock, events, opened = self.setup_source(game, reader_factory=ChangingReader)
        self.wait_for(lambda:self.connected(events))
        self.assertEqual(self.chats(events), [])

    def test_bad_game_image_is_closed_before_retrying_a_fresh_handle(self):
        class BadGame(LobbyGame):
            def read(self, address, count):
                return b'NO' if address == self.image_base and count == 2 else super().read(address, count)
        bad, good = BadGame(), LobbyGame(); good.records([('队伍', 'baseline')]); calls = []
        def create(pid):
            calls.append(pid)
            return bad if len(calls) == 1 else good
        source, game, processes, clock, events, opened = self.setup_source(bad, factory=create)
        self.wait_for(lambda:bad.closed == 1)
        clock[0] = 161.
        self.wait_for(lambda:self.connected(events))
        self.assertEqual(opened, [1, 1]); self.assertEqual(self.chats(events), [])


if __name__ == '__main__':unittest.main()
