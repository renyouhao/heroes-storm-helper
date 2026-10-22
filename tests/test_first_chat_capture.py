"""首句补收行为，全部为合成内存，不读取真实游戏或调用云服务。"""
import struct
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from hots_chat.acquisition.history import HistoryReader
from hots_chat.acquisition.memory import GameProcess
from hots_chat.acquisition.source import ChatSource
from hots_chat.controller import ChatController
from hots_chat.models import TranslationResult
from hots_chat.usage import BudgetLedger
from test_history import FakeMemory, chat_line
from test_lobby_recovery_latency import VirtualStop
from test_party_whisper_acquisition import party_out, whisper_out


class FirstChatGame(FakeMemory):
    path = r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'

    def __init__(self):
        super().__init__()
        self.closed = False

    def close(self):
        self.closed = True


class FirstChatCaptureTests(unittest.TestCase):
    def run_source(self, game, advance=lambda now: None, deadline=4., discovery=None):
        clock, events = [0.], []
        source = ChatSource(process_finder=lambda: [GameProcess(1, game.path, 98348)],
                            game_factory=lambda pid: game, clock=lambda: clock[0])
        source._stop = VirtualStop(clock, deadline, advance)
        with patch('hots_chat.acquisition.source.discover',
                   side_effect=discovery or (lambda *args: SimpleNamespace(root=game.root))):
            source._run(events.append)
        self.assertTrue(game.closed)
        return source, events

    def chats(self, events):
        return [event.payload.text for event in events if event.kind == 'chat']

    def test_first_hi_is_delivered_on_first_connection_without_a_second_message(self):
        game = FirstChatGame()
        game.set_nodes([chat_line('hi')])
        _, events = self.run_source(game)
        self.assertEqual(self.chats(events), ['hi'])

    def test_multiple_messages_arriving_before_connection_keep_order_and_identical_rows(self):
        game = FirstChatGame()
        game.set_nodes([chat_line('hi'), chat_line('hi'), chat_line('hello')])
        _, events = self.run_source(game)
        self.assertEqual(self.chats(events), ['hi', 'hi', 'hello'])

    def test_initial_backfill_is_limited_to_latest_five_supported_messages(self):
        game = FirstChatGame()
        lines = [chat_line(str(index)) for index in range(9)]
        # An unsupported system row must not occupy a slot in the chat limit.
        unsupported = '<s val="ChatDisplayTimeStamp">24:54</s><sp/>system notice'
        game.set_nodes(lines + [unsupported])
        _, events = self.run_source(game)
        self.assertEqual(self.chats(events), ['4', '5', '6', '7', '8'])

    def test_native_owned_party_and_whisper_rows_do_not_displace_first_team_message(self):
        game = FirstChatGame()
        game.set_nodes([chat_line('hi')] + [party_out('p'), whisper_out('w')] * 4)
        _, events = self.run_source(game)
        self.assertEqual(self.chats(events), ['hi'])

    def test_new_message_after_initial_backfill_is_not_duplicated_or_dropped(self):
        game = FirstChatGame()
        game.set_nodes([chat_line('hi')])
        changed = []

        def advance(now):
            if now >= 1 and not changed:
                changed.append(now)
                game.set_nodes([chat_line('hi'), chat_line('hello')])

        _, events = self.run_source(game, advance)
        self.assertEqual(self.chats(events), ['hi', 'hello'])

    def test_unknown_root_after_scene_change_also_has_a_bounded_initial_backfill(self):
        class NextGame(FirstChatGame):
            root = 0x900000

        game, following = FirstChatGame(), NextGame()
        game.set_nodes([chat_line('hi')])
        following.set_nodes([chat_line(str(index)) for index in range(9)])
        changed = []

        def advance(now):
            if now >= 1 and not changed:
                changed.append(now)
                game.blocks = following.blocks

        _, events = self.run_source(game, advance)
        self.assertEqual(self.chats(events), ['hi', '4', '5', '6', '7', '8'])

    def test_reconnect_resumes_committed_cursor_instead_of_repeating_initial_backfill(self):
        game = FirstChatGame()
        game.set_nodes([chat_line('hi')])
        clock, events = [0.], []
        source = ChatSource(process_finder=lambda: [GameProcess(1, game.path, 98348)],
                            game_factory=lambda pid: game, clock=lambda: clock[0])
        changed = []

        def advance(now):
            if now >= 1 and not changed:
                changed.append(now)
                source.reconnect()
                game.set_nodes([chat_line('hi'), chat_line('hi')])

        source._stop = VirtualStop(clock, 5., advance)
        source._run(events.append)
        self.assertEqual(self.chats(events), ['hi', 'hi'])

    def test_message_appended_between_discovery_and_initial_poll_is_included(self):
        game = FirstChatGame()
        game.set_nodes([chat_line('hi')])

        def discovery(*args):
            game.set_nodes([chat_line('hi'), chat_line('hello')])
            return SimpleNamespace(root=game.root)

        _, events = self.run_source(game, discovery=discovery)
        self.assertEqual(self.chats(events), ['hi', 'hello'])

    def test_census_publishes_new_root_before_committing_its_cursor(self):
        game = FirstChatGame()
        game.set_nodes([chat_line('hi')])
        other = FirstChatGame()
        other.set_nodes([chat_line('second')])
        delta = 0x100000
        for address, raw in other.blocks.items():
            count = 2 if address == other.root else 3
            values = list(struct.unpack('<' + 'Q' * count, raw[:count * 8]))
            values[0] += delta
            values[1] += delta
            game.blocks[address + delta] = struct.pack('<' + 'Q' * count, *values) + raw[count * 8:]

        class Census:
            def __init__(self, *args, **options):
                self.ignore_snapshots = options.get('ignore_snapshots', {})
                self.finished = False

            def step(self):
                root = game.root + delta
                if root not in self.ignore_snapshots:
                    return SimpleNamespace(root=root)
                self.finished = True
                return None

        with patch('hots_chat.acquisition.source.IncrementalDiscovery', Census):
            _, events = self.run_source(game)
        self.assertEqual(self.chats(events), ['hi', 'second'])

    def test_changing_initial_snapshot_never_publishes_a_partial_batch(self):
        game = FirstChatGame()
        game.set_nodes([chat_line('hi')])
        raw_read = game.read
        reads = [0]

        def read(address, count):
            value = raw_read(address, count)
            if address == game.root and count == 16:
                reads[0] += 1
                if reads[0] == 2:
                    return struct.pack('<QQ', game.root | 1, game.root | 1)
            return value

        game.read = read
        _, events = self.run_source(game, deadline=7.)
        self.assertEqual(self.chats(events), ['hi'])

    def test_census_of_current_root_does_not_deliver_the_same_new_record_twice(self):
        game = FirstChatGame()
        game.set_nodes([chat_line('hi')])

        class CurrentRootCensus:
            def __init__(self, *args, **options):
                self.ignore_snapshots = options.get('ignore_snapshots', {})
                self.finished = False
                self.calls = 0

            def step(self):
                self.calls += 1
                if self.calls == 1:
                    game.set_nodes([chat_line('hi'), chat_line('hello')])
                    return SimpleNamespace(root=game.root)
                self.finished = True
                return None

        with patch('hots_chat.acquisition.source.IncrementalDiscovery', CurrentRootCensus):
            _, events = self.run_source(game)
        self.assertEqual(self.chats(events), ['hi', 'hello'])

    def test_same_pid_hard_error_recovers_more_than_five_unread_messages_from_cursor(self):
        class FailingGame(FirstChatGame):
            fail_reads = False

            def read(self, address, count):
                if self.fail_reads:
                    raise OSError('synthetic process read failure')
                return super().read(address, count)

        game = FailingGame()
        game.set_nodes([chat_line('hi')])
        clock, events, opened, changed = [0.], [], [], set()

        def factory(pid):
            opened.append(pid)
            return game

        def advance(now):
            if now >= 1 and 1 not in changed:
                changed.add(1)
                game.fail_reads = True
            if now >= 2 and 2 not in changed:
                changed.add(2)
                game.fail_reads = False
                game.set_nodes([chat_line('hi')] + [chat_line(str(i)) for i in range(9)])

        source = ChatSource(process_finder=lambda: [GameProcess(1, game.path, 98348)],
                            game_factory=factory, clock=lambda: clock[0])
        source._stop = VirtualStop(clock, 66., advance)
        with patch('hots_chat.acquisition.source.discover', return_value=SimpleNamespace(root=game.root)):
            source._run(events.append)
        self.assertEqual(opened, [1, 1])
        self.assertEqual(self.chats(events), ['hi', '0', '1', '2', '3', '4', '5', '6', '7', '8'])

    def test_first_hi_reaches_translation_once_without_a_second_game_message(self):
        game = FirstChatGame()
        game.set_nodes([chat_line('hi')])
        source, source_events = self.run_source(game)

        class Engine:
            provider_id = 'offline-first-message-test'
            config_version = '1'

            def __init__(self):
                self.calls = []

            def translate(self, job):
                self.calls.append((job.text, job.source, job.target))
                return TranslationResult(job.job_id, '你好', 'en', None, 0)

            def close(self):
                pass

        engine = Engine()
        now = lambda: datetime(2026, 10, 21, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            ledger = BudgetLedger(Path(folder) / 'synthetic-usage.db', 100)
            ledger.confirm_baseline('synthetic', '2026-10', 0, now())
            controller = ChatController(source, engine, ledger, 'synthetic', now)
            try:
                for event in source_events:
                    controller.handle_source_event(event)
                with controller.condition:
                    self.assertTrue(controller.condition.wait_for(lambda: controller.idle, timeout=3))
                results = [event.payload for event in controller.drain_events() if event.kind == 'result']
                self.assertEqual([(row['original'], row['translated']) for row in results], [('hi', '你好')])
                self.assertEqual(engine.calls, [('hi', 'en', 'zh')])
                self.assertEqual(ledger.snapshot('synthetic', now()).local_reserved, 2)
            finally:
                controller.close()


if __name__ == '__main__':
    unittest.main()
