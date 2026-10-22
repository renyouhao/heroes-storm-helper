"""First-message regressions use synthetic rings and virtual time only."""
import unittest
from unittest.mock import patch

from hots_chat.acquisition.lobby_native import LobbyReader
from hots_chat.acquisition.lobby_source import LobbySource
from hots_chat.acquisition.memory import GameProcess
from test_lobby_native import FixtureMetadata
from test_lobby_recovery_latency import FILETIME, RecoveryGame, VirtualStop


START_SECONDS = (FILETIME - 116444736000000000) / 10000000


class LobbyFirstMessageTests(unittest.TestCase):
    def run_source(self, game, advance=lambda now, source:None, deadline=12., max_nodes=512,
                   events_out=None):
        clock, polls = [0.], []
        events = events_out if events_out is not None else []

        class MeasuredReader(LobbyReader):
            def poll(self):
                result = super().poll()
                polls.append(result.read_bytes)
                return result

        source = LobbySource(
            process_finder=lambda:[GameProcess(1, game.path, 98348)],
            game_factory=lambda pid:game,
            module_base_finder=lambda pid:game.image_base,
            clock=lambda:clock[0],
            wall_clock=lambda:START_SECONDS,
            reader_factory=lambda memory, root, image_base, **options:MeasuredReader(
                memory, root, image_base, metadata_factory=FixtureMetadata,
                max_nodes=max_nodes, **options))
        source._stop = VirtualStop(clock, deadline, lambda now:advance(now, source))
        source._run(events.append)
        self.assertEqual(game.closed, 1)
        self.assertTrue(all(count <= 262144 for count in polls))
        return [event.payload.text for event in events if event.kind == 'chat']

    def test_empty_lobby_emits_first_record_after_capture_started(self):
        game = RecoveryGame()
        changed = []

        def advance(now, source):
            if now >= 1 and not changed:
                changed.append(now)
                game.ring(game.base + 0x1008, [('first', FILETIME + 10)])

        self.assertEqual(self.run_source(game, advance), ['first'])

    def test_initial_snapshot_filters_history_but_emits_new_first_record(self):
        game = RecoveryGame()
        game.ring(game.base + 0x1008, [('old', FILETIME - 10), ('first', FILETIME + 10)])
        self.assertEqual(self.run_source(game), ['first'])

    def test_start_records_boundary_before_background_thread_runs(self):
        game = RecoveryGame()
        clock, wall, events = [0.], [START_SECONDS], []
        source = LobbySource(
            process_finder=lambda:[GameProcess(1, game.path, 98348)],
            game_factory=lambda pid:game,
            module_base_finder=lambda pid:game.image_base,
            clock=lambda:clock[0], wall_clock=lambda:wall[0],
            reader_factory=lambda memory, root, image_base, **options:LobbyReader(
                memory, root, image_base, metadata_factory=FixtureMetadata, **options))
        # Hold only thread scheduling; all acquisition code still runs below.
        with patch('hots_chat.acquisition.lobby_source.threading.Thread'):
            source.start(events.append)
            source._stop = VirtualStop(clock, 2., lambda now:None)
            wall[0] += 60
            game.ring(game.base + 0x1008, [('first', FILETIME + 10)])
            source._run(events.append)
        self.assertEqual([event.payload.text for event in events if event.kind == 'chat'], ['first'])

    def test_census_uses_capture_start_instead_of_other_rings_latest_time(self):
        game = RecoveryGame()
        game.ring(game.base + 0x1008, [('old-a', FILETIME), ('a-new', FILETIME + 30)])
        game.ring(game.base + 0x80008, [('old-b', FILETIME), ('b-new', FILETIME + 10)])
        self.assertEqual(self.run_source(game), ['a-new', 'b-new'])

    def test_census_resumes_partial_inventory_without_losing_first_message(self):
        game = RecoveryGame()
        game.ring(game.base + 0x1008, [('old-a', FILETIME)])
        expected = [f'n{i:03d}' for i in range(70)]
        game.ring(game.base + 0x80008,
                  [('old-b', FILETIME)] + [(text, FILETIME + i + 1) for i, text in enumerate(expected)])
        self.assertEqual(self.run_source(game, max_nodes=2), expected)

    def test_inventory_can_receive_an_append_while_its_bounded_poll_is_pending(self):
        game = RecoveryGame()
        first, second = game.base + 0x1008, game.base + 0x80008
        game.ring(first, [('old-a', FILETIME), ('a-new', FILETIME + 100)])
        rows = [('old-b', FILETIME)] + [(f'n{i:03d}', FILETIME + i + 1) for i in range(70)]
        game.ring(second, rows)
        changed = []

        def advance(now, source):
            if now >= .5 and not changed:
                changed.append(now)
                game.ring(second, rows + [('last', FILETIME + 71)])

        self.assertEqual(self.run_source(game, advance, max_nodes=2),
                         ['a-new'] + [f'n{i:03d}' for i in range(70)] + ['last'])

    def test_invalid_pending_inventory_does_not_block_a_third_new_ring(self):
        class ExpiringInventoryGame(RecoveryGame):
            expired = False

            def read(self, address, count):
                if self.expired and self.base + 0x80000 <= address < self.base + 0x90000:
                    return b''
                return super().read(address, count)

        game = ExpiringInventoryGame()
        game.ring(game.base + 0x1008, [('old-a', FILETIME)])
        game.ring(game.base + 0x80008,
                  [('old-b', FILETIME)] + [(f'n{i:03d}', FILETIME + i + 1) for i in range(70)])
        changed = set()

        def advance(now, source):
            if now >= .5:
                game.expired = True
            if now >= 2 and 'third' not in changed:
                changed.add('third')
                game.ring(game.base + 0xc0008, [('third', FILETIME + 100)])

        events = []
        self.assertEqual(self.run_source(game, advance, max_nodes=2, events_out=events), ['third'])
        self.assertEqual(len([event for event in events if event.kind == 'gap']), 1)

    def test_pending_inventory_oserror_still_releases_the_game_handle(self):
        class DisconnectedInventoryGame(RecoveryGame):
            disconnected = False

            def read(self, address, count):
                if self.disconnected and self.base + 0x80000 <= address < self.base + 0x90000:
                    raise OSError('synthetic process access failed')
                return super().read(address, count)

        game = DisconnectedInventoryGame()
        game.ring(game.base + 0x1008, [('old-a', FILETIME)])
        game.ring(game.base + 0x80008,
                  [('old-b', FILETIME)] + [(f'n{i:03d}', FILETIME + i + 1) for i in range(70)])
        closed_before_stop = []

        def advance(now, source):
            if now >= .5:
                game.disconnected = True
            if now >= 1 and not closed_before_stop:
                closed_before_stop.append(game.closed)

        self.assertEqual(self.run_source(game, advance, max_nodes=2), [])
        self.assertEqual(closed_before_stop, [1])

    def test_reconnect_retains_start_boundary_and_emits_only_unread_records(self):
        game = RecoveryGame()
        first, second = game.base + 0x1008, game.base + 0x80008
        game.ring(first, [('old', FILETIME), ('seen', FILETIME + 10)])
        changed = []

        def advance(now, source):
            if now >= 2 and not changed:
                changed.append(now)
                # The second ring has messages from before the reconnect,
                # but after the original capture start.
                game.ring(second, [('old-b', FILETIME), ('unread', FILETIME + 5)])
                game.ring(first, [('old', FILETIME), ('seen', FILETIME + 10), ('new', FILETIME + 20)])
                source.reconnect()

        self.assertEqual(self.run_source(game, advance), ['seen', 'new', 'unread'])

    def test_reused_tail_emits_first_new_record_and_preserves_equal_timestamp(self):
        game = RecoveryGame()
        root = game.base + 0x1008
        game.ring(root, [('old', FILETIME), ('seen', FILETIME + 10)])
        changed = []

        def advance(now, source):
            if now >= 2 and not changed:
                changed.append(now)
                # The previous tail's identity changes. Its replacement is
                # a distinct message even though its timestamp is unchanged.
                game.ring(root, [('old', FILETIME), ('first', FILETIME + 10)])

        self.assertEqual(self.run_source(game, advance), ['seen', 'first'])

    def test_gap_does_not_replay_committed_records_at_equal_timestamp(self):
        game = RecoveryGame()
        root = game.base + 0x1008
        game.ring(root, [('old', FILETIME), ('seen', FILETIME + 10), ('tail', FILETIME + 10)])
        changed = []

        def advance(now, source):
            if now >= 2 and not changed:
                changed.append(now)
                game.ring(root, [('old', FILETIME), ('seen', FILETIME + 10), ('first', FILETIME + 10)])

        self.assertEqual(self.run_source(game, advance), ['seen', 'tail', 'first'])

    def test_gap_does_not_reread_expired_body_of_committed_record(self):
        class ExpiringGame(RecoveryGame):
            expired = False

            def read(self, address, count):
                if self.expired and address == self.base + 0x41000:
                    return b''
                return super().read(address, count)

        game = ExpiringGame()
        game.records([('队伍', 'old'), ('队伍', 'seen-long'), ('队伍', 'tail')])
        changed = []

        def advance(now, source):
            if now >= 2 and not changed:
                changed.append(now)
                game.records([('队伍', 'old'), ('队伍', 'seen-long'), ('队伍', 'first')])
                game.expired = True

        self.assertEqual(self.run_source(game, advance), ['seen-long', 'tail', 'first'])


if __name__ == '__main__':
    unittest.main()
