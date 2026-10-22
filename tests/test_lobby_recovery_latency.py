"""Recovery timings use a virtual clock and synthetic native rings only."""
import struct
import threading
import unittest
from unittest.mock import patch

from hots_chat.acquisition.discovery import DiscoveryCancelled
from hots_chat.acquisition.lobby_native import LobbyDiscovery, LobbyReader
from hots_chat.acquisition.lobby_source import LobbySource
from hots_chat.acquisition.memory import GameProcess
from test_lobby_native import FixtureMetadata, LobbyMemory


FILETIME = 134053920000000000


class RecoveryGame(LobbyMemory):
    path = r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'

    def __init__(self):
        super().__init__()
        self.closed = self.scan_passes = 0

    def read(self, address, count):
        if address == self.image_base and count == 2:
            return b'MZ'
        return super().read(address, count)

    def regions(self):
        yield from super().regions()
        self.scan_passes += 1

    def close(self):
        self.closed += 1

    def ring(self, root, records):
        """Each tuple supplies literal record text and native FILETIME."""
        for index, (text, filetime) in enumerate(records):
            node = root + index * 0x80
            following = node + 0x80 if index + 1 < len(records) else root | 1
            body = text.encode('utf-8')
            assert len(body) <= 7
            self.put(node - 8, struct.pack('<3Q', self.layout.message_table, following, 2))
            self.put(node + 16, struct.pack('<2Q', self.party, self.sender))
            self.put(node + 32, struct.pack('<2I', (len(body) << 2) | 1, 0) + body.ljust(8, b'\0'))
            self.put(node + 48, struct.pack('<Q', filetime))


class VirtualStop:
    def __init__(self, clock, deadline, advance):
        self.clock, self.deadline, self.advance = clock, deadline, advance
        self.stopped = False
        self.waits = []

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, timeout):
        self.waits.append(timeout)
        self.clock[0] += timeout
        self.advance(self.clock[0])
        if self.clock[0] >= self.deadline:
            self.stopped = True
        return self.stopped


class LobbyRecoveryLatencyTests(unittest.TestCase):
    def run_source(self, game, advance=lambda now:None, deadline=12.):
        clock, events = [0.], []
        source = LobbySource(
            process_finder=lambda:[GameProcess(1, game.path, 98348)],
            game_factory=lambda pid:game,
            module_base_finder=lambda pid:game.image_base,
            clock=lambda:clock[0],
            wall_clock=lambda:(FILETIME - 116444736000000000) / 10000000,
            reader_factory=lambda memory, root, image_base, **options:LobbyReader(
                memory, root, image_base, metadata_factory=FixtureMetadata, **options))
        stop = VirtualStop(clock, deadline, advance)
        source._stop = stop
        source._run(lambda event:events.append((clock[0], event)))
        self.assertEqual(game.closed, 1)
        return events, stop

    def chats(self, events):
        return [(now, event.payload.text) for now, event in events if event.kind == 'chat']

    def test_readable_old_ring_does_not_hide_updates_to_an_inventoried_ring(self):
        game = RecoveryGame()
        first, second = game.base + 0x1008, game.base + 0x80008
        game.ring(first, [('old-a', FILETIME)])
        game.ring(second, [('old-b', FILETIME)])
        changed = []
        def advance(now):
            if now >= 2 and not changed:
                changed.append(now)
                game.ring(second, [('old-b', FILETIME), ('new-b', FILETIME + 10)])
        events, stop = self.run_source(game, advance)
        received = self.chats(events)
        self.assertEqual([text for _, text in received], ['new-b'])
        self.assertLessEqual(received[0][0] - changed[0], .6)
        self.assertLessEqual(game.scan_passes, 4)

    def test_new_ring_is_found_without_replaying_records_older_than_native_baseline(self):
        game = RecoveryGame()
        first, second = game.base + 0x1008, game.base + 0x80008
        game.ring(first, [('old-a', FILETIME)])
        changed = []
        def advance(now):
            if now >= 3 and not changed:
                changed.append(now)
                game.ring(second, [('ancient', FILETIME - 10), ('same', FILETIME + 20), ('same', FILETIME + 21)])
        events, stop = self.run_source(game, advance, deadline=16)
        received = self.chats(events)
        self.assertEqual([text for _, text in received], ['same', 'same'])
        self.assertLessEqual(received[0][0] - changed[0], 7)

    def test_switching_back_uses_each_ring_cursor_and_preserves_identical_records(self):
        game = RecoveryGame()
        first, second = game.base + 0x1008, game.base + 0x80008
        game.ring(first, [('old-a', FILETIME)])
        game.ring(second, [('old-b', FILETIME)])
        changed = set()
        def advance(now):
            if now >= 2 and 2 not in changed:
                changed.add(2)
                game.ring(second, [('old-b', FILETIME), ('same', FILETIME + 10), ('same', FILETIME + 11)])
            if now >= 4 and 4 not in changed:
                changed.add(4)
                game.ring(first, [('old-a', FILETIME), ('back', FILETIME + 20)])
        events, stop = self.run_source(game, advance)
        self.assertEqual([text for _, text in self.chats(events)], ['same', 'same', 'back'])

    def test_neighborhood_search_does_not_start_in_an_unreadable_alignment_gap(self):
        class OffsetGame(RecoveryGame):
            base = 0x118000
        game = OffsetGame()
        first, second = game.base + 0x1008, game.base + 0x80008
        game.ring(first, [('old', FILETIME)])
        changed = []
        def advance(now):
            if now >= 3 and not changed:
                changed.append(now)
                game.ring(second, [('new', FILETIME + 10)])
        events, stop = self.run_source(game, advance)
        received = self.chats(events)
        self.assertEqual([text for _, text in received], ['new'])
        self.assertLessEqual(received[0][0] - changed[0], 1.1)

    def test_invalid_old_timestamp_cannot_hide_future_valid_messages(self):
        game = RecoveryGame()
        first, second = game.base + 0x1008, game.base + 0x80008
        game.ring(first, [('old', FILETIME), ('noise', 0xffffffffffffffff)])
        game.put(first + 0x80 + 16, struct.pack('<Q', game.unsupported))
        changed = []
        def advance(now):
            if now >= 3 and not changed:
                changed.append(now)
                game.ring(second, [('new', FILETIME + 10)])
        events, stop = self.run_source(game, advance)
        self.assertEqual([text for _, text in self.chats(events)], ['new'])

    def test_transient_link_mutation_retries_the_validated_cursor_without_five_second_delay(self):
        game = RecoveryGame()
        root = game.base + 0x1008
        game.ring(root, [('old', FILETIME)])
        changed = set()
        def advance(now):
            if now >= 1 and 1 not in changed:
                changed.add(1)
                game.put(root, b'\0' * 8)
            if now >= 1.5 and 2 not in changed:
                changed.add(2)
                game.ring(root, [('old', FILETIME), ('new', FILETIME + 10)])
        events, stop = self.run_source(game, advance)
        received = self.chats(events)
        self.assertEqual([text for _, text in received], ['new'])
        self.assertLessEqual(received[0][0], 2.1)

    def test_incremental_search_yields_without_half_second_per_chunk_penalty(self):
        class DistantGame(RecoveryGame):
            base = 0x40000000
            zeros = b'\0' * 1048576
            def read(self, address, count):
                if 0x10000 <= address < 0x10000 + 720 * 1024 * 1024:
                    self.calls.append((address, count))
                    return self.zeros[:count]
                return super().read(address, count)
            def regions(self):
                yield 0x10000, 720 * 1024 * 1024, 4
                yield from super().regions()
        game = DistantGame()
        game.ring(game.base + 0x1008, [('old', FILETIME)])
        # Hold CPU time constant to measure only the source's scheduled waits;
        # the scanner's independent 64-MiB byte budget still forces 12 steps.
        with patch('hots_chat.acquisition.lobby_native.time.perf_counter', return_value=0):
            events, stop = self.run_source(game, deadline=2)
        connected = [now for now, event in events if event.kind == 'status']
        self.assertTrue(connected)
        self.assertLessEqual(connected[0], 1.5)
        self.assertTrue(all(count <= 1048576 for _, count in game.calls))

    def test_empty_scans_keep_backoff_and_stop_releases_handle(self):
        game = RecoveryGame()
        events, stop = self.run_source(game, deadline=30)
        self.assertEqual(self.chats(events), [])
        self.assertLessEqual(game.scan_passes, 7)
        reads = len(game.calls)
        stop.set()
        self.assertEqual(len(game.calls), reads)

    def test_connecting_and_inventory_share_one_full_scan_step_per_round(self):
        class MiddleGame(RecoveryGame):
            base = 0x4000000
            zeros = b'\0' * 1048576
            def read(self, address, count):
                if (0x10000 <= address < 0x10000 + 63 * 1048576
                        or 0x8000000 <= address < 0x8000000 + 63 * 1048576):
                    self.calls.append((address, count))
                    return self.zeros[:count]
                return super().read(address, count)
            def regions(self):
                yield 0x10000, 63 * 1048576, 4
                yield self.base, len(self.data), 4
                yield 0x8000000, 63 * 1048576, 4
        game = MiddleGame()
        game.ring(game.base + 0x1008, [('old', FILETIME)])
        with patch('hots_chat.acquisition.lobby_native.time.perf_counter', return_value=0):
            events, stop = self.run_source(game, deadline=.05)
        self.assertLessEqual(sum(count for _, count in game.calls), 70 * 1048576)

    def test_long_idle_uses_small_neighborhoods_and_backed_off_full_passes(self):
        game = RecoveryGame()
        game.ring(game.base + 0x1008, [('old', FILETIME)])
        events, stop = self.run_source(game, deadline=60)
        self.assertEqual(self.chats(events), [])
        self.assertLessEqual(game.scan_passes, 6)
        self.assertLessEqual(sum(count for _, count in game.calls), 70 * 1048576)

    def test_probe_does_not_reset_active_batch_cycle_guard(self):
        game = RecoveryGame()
        root = game.base + 0x1008
        game.ring(root, [('old', FILETIME)])
        changed = set()
        def advance(now):
            if now >= 2 and 1 not in changed:
                changed.add(1)
                game.ring(root, [('old', FILETIME)] + [
                    (f'n{i:02d}', FILETIME + i + 1) for i in range(65)])
            if now >= 2.5 and 2 not in changed:
                changed.add(2)
                game.put(root + 65 * 0x80, struct.pack('<Q', root + 0x80))
        events, stop = self.run_source(game, advance)
        texts = [text for _, text in self.chats(events)]
        self.assertEqual(texts, [f'n{i:02d}' for i in range(64)])

    def test_active_old_ring_cannot_starve_unknown_ring_discovery(self):
        game = RecoveryGame()
        first, second = game.base + 0x1008, game.base + 0x80008
        rows = [('old', FILETIME)]
        game.ring(first, rows)
        changed = []
        def advance(now):
            index = int(now * 2)
            if index >= len(rows):
                rows.append((f'a{index:02d}', FILETIME + index))
                game.ring(first, rows)
            if now >= 3.05 and not changed:
                changed.append(now)
                game.ring(second, [('new-b', FILETIME + 100)])
        events, stop = self.run_source(game, advance, deadline=15)
        received = self.chats(events)
        texts = [text for _, text in received]
        self.assertIn('new-b', texts)
        self.assertEqual(len(texts), len(set(texts)))
        self.assertLessEqual(next(now for now, text in received if text == 'new-b'), 6.5)
        self.assertEqual([text for text in texts if text != 'new-b'], [f'a{i:02d}' for i in range(1, 30)])

    def test_switching_rings_preserves_an_unfinished_batch_cycle_guard(self):
        game = RecoveryGame()
        first, second = game.base + 0x1008, game.base + 0x80008
        game.ring(first, [('old-a', FILETIME)])
        game.ring(second, [('old-b', FILETIME)])
        changed = set()
        def advance(now):
            if now >= 2 and 1 not in changed:
                changed.add(1)
                game.ring(first, [('old-a', FILETIME)] + [
                    (f'n{i:03d}', FILETIME + i + 1) for i in range(129)])
            if now >= 2.5 and 2 not in changed:
                changed.add(2)
                game.ring(second, [('old-b', FILETIME), ('new-b', FILETIME + 1000)])
            if now >= 3.5 and 3 not in changed:
                changed.add(3)
                game.put(first + 129 * 0x80, struct.pack('<Q', first + 0x80))
        events, stop = self.run_source(game, advance)
        texts = [text for _, text in self.chats(events)]
        self.assertEqual(texts.count('new-b'), 1)
        self.assertEqual([text for text in texts if text != 'new-b'], [f'n{i:03d}' for i in range(128)])


class LobbyRecoveryBudgetTests(unittest.TestCase):
    def test_known_unchanged_ring_checks_only_saved_tail_without_region_scan(self):
        game = RecoveryGame()
        root = game.base + 0x1008
        game.ring(root, [('old', FILETIME)])
        reader = LobbyReader(game, root, game.image_base)
        reader.poll()
        game.calls.clear()
        scanner = LobbyDiscovery(game, threading.Event(), game.image_base,
                                 ignore_snapshots={root:reader.cursor}, preferred_roots=[root], regions=())
        self.assertIsNone(scanner.step())
        self.assertTrue(scanner.finished)
        self.assertEqual(game.scan_passes, 0)
        self.assertLessEqual(sum(count for _, count in game.calls), 128)

    def test_long_changed_ring_validation_resumes_with_bounded_work_and_cancellation(self):
        game = LobbyMemory()
        root = game.records([('队伍', 'old')] * 520)
        reader = LobbyReader(game, root, game.image_base)
        while reader.poll().retry:
            pass
        cursor = reader.cursor
        game.records([('队伍', 'old')] * 520 + [('队伍', 'new')])
        cancel = threading.Event()
        scanner = LobbyDiscovery(game, cancel, game.image_base,
                                 ignore_snapshots={root:cursor}, preferred_roots=[root], regions=())
        game.calls.clear()
        self.assertIsNone(scanner.step(time_budget=60))
        self.assertFalse(scanner.finished)
        self.assertLessEqual(sum(count for _, count in game.calls), 262144)
        self.assertLessEqual(len(game.calls), 1600)
        cancel.set()
        calls = len(game.calls)
        with self.assertRaises(DiscoveryCancelled):
            scanner.step()
        self.assertEqual(len(game.calls), calls)
        cancel.clear()
        game.calls.clear()
        location = scanner.step(time_budget=60)
        self.assertEqual(location.root, root)
        self.assertEqual(location.cursor[0], game.links[-1])
        self.assertLessEqual(sum(count for _, count in game.calls), 262144)

    def test_native_baseline_cutoff_expires_after_recovery(self):
        game = RecoveryGame()
        root = game.base + 0x1008
        game.ring(root, [('old', FILETIME), ('new', FILETIME + 10)])
        reader = LobbyReader(game, root, game.image_base, metadata_factory=FixtureMetadata,
                             include_history=True, after_filetime=FILETIME)
        self.assertEqual([message.text for message in reader.poll().messages], ['new'])
        # A later record with an equal native timestamp still has its own
        # identity and must pass normal cursor tracking after recovery.
        game.ring(root, [('old', FILETIME), ('new', FILETIME + 10), ('same', FILETIME)])
        self.assertEqual([message.text for message in reader.poll().messages], ['same'])
        self.assertEqual(reader.poll().messages, [])


if __name__ == '__main__':
    unittest.main()
