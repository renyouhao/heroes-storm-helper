"""关闭聊天时间显示时仍识别已知对局格式；全部使用合成内存。"""
import struct
import threading
import unittest
import json
from pathlib import Path
import tempfile
import zipfile

from hots_chat.acquisition.discovery import (DiscoveryPending, IncrementalDiscovery,
                                             _HistoryPrefixMatcher, discover)
from hots_chat.acquisition.history import HistoryReader
from hots_chat.acquisition.memory import GameProcess
from hots_chat.acquisition.parsing import parse_display_chat
from hots_chat.acquisition.source import ChatSource
from hots_chat.diagnostics import DiagnosticRecorder
from test_history import FakeMemory, chat_line
from test_lobby_recovery_latency import VirtualStop


def without_timestamp(body='hello', channel='团队', *, spacer=False):
    line = chat_line(body).split('<sp/>', 1)[1].replace('[团队]', '[' + channel + ']', 1)
    return ('<sp/>' if spacer else '') + line


class TimestampOptionalCaptureTests(unittest.TestCase):
    def test_known_team_and_all_formats_parse_without_a_timestamp(self):
        body = 'hello 안녕하세요 <literal> &amp; &lt;b&gt;x&lt;/b&gt;'
        for channel in ('团队', '所有玩家'):
            for spacer in (False, True):
                with self.subTest(channel=channel, spacer=spacer):
                    result = parse_display_chat(without_timestamp(body, channel, spacer=spacer))
                    self.assertIsNotNone(result)
                    self.assertEqual((result.time, result.channel, result.sender), ('', channel, 'Hunter'))
                    self.assertEqual(result.text, 'hello 안녕하세요 <literal> & <b>x</b>')

    def test_initial_discovery_finds_supported_history_with_timestamp_display_off(self):
        for spacer in (False, True):
            with self.subTest(spacer=spacer):
                memory = FakeMemory()
                memory.set_nodes([without_timestamp(spacer=spacer)])
                result = discover(memory, threading.Event(), channels=('团队', '所有玩家'))
                self.assertEqual(result.root, memory.root)

    def test_incremental_discovery_finds_supported_history_without_a_timestamp(self):
        memory = FakeMemory()
        memory.set_nodes([without_timestamp('first', channel='所有玩家')])
        scanner = IncrementalDiscovery(memory, threading.Event(), channels=('团队', '所有玩家'))
        result = scanner.step(time_budget=60)
        self.assertIsNotNone(result)
        self.assertEqual(result.root, memory.root)

    def test_mixed_history_keeps_order_and_actual_timestamp_availability(self):
        memory = FakeMemory()
        memory.set_nodes([chat_line('timed'), without_timestamp('untimed'),
                          without_timestamp('spaced', spacer=True)])
        result = HistoryReader(memory, memory.root, include_history=True,
                               channels=('团队', '所有玩家')).poll()
        self.assertEqual([(row.time, row.text) for row in result.messages],
                         [('24:54', 'timed'), ('', 'untimed'), ('', 'spaced')])

    def test_timestamp_display_changes_do_not_duplicate_or_drop_new_messages(self):
        memory = FakeMemory()
        lines = [chat_line('baseline')]
        memory.set_nodes(lines)
        reader = HistoryReader(memory, memory.root, include_history=True,
                               channels=('团队', '所有玩家'))
        self.assertEqual([row.text for row in reader.poll().messages], ['baseline'])
        for next_line, text in ((without_timestamp('off'), 'off'),
                                (chat_line('on again'), 'on again'),
                                (without_timestamp('off again', spacer=True), 'off again')):
            lines.append(next_line)
            memory.set_nodes(lines)
            self.assertEqual([row.text for row in reader.poll().messages], [text])
            self.assertEqual(reader.poll().messages, [])

    def test_source_delivers_first_untimed_message_and_display_changes_once_each(self):
        class Game(FakeMemory):
            path = r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'
            closed = False

            def close(self):
                self.closed = True

        game, clock, events = Game(), [0.], []
        lines = [without_timestamp('first untimed')]
        game.set_nodes(lines)
        changed = set()

        def advance(now):
            if now >= 1 and 1 not in changed:
                changed.add(1)
                lines.append(chat_line('now timed'))
                game.set_nodes(lines)
            if now >= 2 and 2 not in changed:
                changed.add(2)
                lines.append(without_timestamp('untimed again', spacer=True))
                game.set_nodes(lines)

        source = ChatSource(process_finder=lambda: [GameProcess(1, game.path, 98348)],
                            game_factory=lambda pid: game, clock=lambda: clock[0])
        source._stop = VirtualStop(clock, 3., advance)
        source._run(events.append)
        self.assertTrue(game.closed)
        self.assertEqual([event.payload.text for event in events if event.kind == 'chat'],
                         ['first untimed', 'now timed', 'untimed again'])

    def test_untimed_source_evidence_and_new_fields_survive_real_zip_export(self):
        class Game(FakeMemory):
            path = r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'

            def close(self):
                pass

        game, clock, events = Game(), [0.], []
        game.set_nodes([without_timestamp('private-export-body').replace('Hunter', 'private-export-sender')])
        with tempfile.TemporaryDirectory() as folder:
            recorder = DiagnosticRecorder(Path(folder) / 'logs', enabled=True,
                                          monotonic=lambda: clock[0])
            try:
                source = ChatSource(process_finder=lambda: [GameProcess(1, game.path, 98348)],
                                    game_factory=lambda pid: game, clock=lambda: clock[0])
                source.diagnostics = recorder
                source._stop = VirtualStop(clock, 1.5, lambda now: None)
                source._run(events.append)
                target = recorder.export_report(Path(folder) / 'report.zip')
                with zipfile.ZipFile(target) as archive:
                    rows = [json.loads(line) for name in archive.namelist() if name.startswith('helper.log')
                            for line in archive.read(name).decode('utf-8').splitlines()]
            finally:
                recorder.close()
        captured = [row for row in rows if row.get('event') == 'capture.state'
                    and row.get('source') == 'match']
        initial = next(row for row in captured
                       if row.get('stage') == 'initial' and row.get('outcome') == 'candidate_found')
        attached = next(row for row in captured
                        if row.get('stage') == 'attach' and row.get('outcome') == 'messages')
        for row in (initial, attached):
            self.assertEqual(row['capture_schema'], 2)
            self.assertGreater(row['untimestamped_message_count'], 0)
            self.assertEqual(row['timestamped_message_count'], 0)
            self.assertGreater(row['scan_id'], 0)
        self.assertEqual(attached['message_count'], 1)
        self.assertEqual(initial['scan_id'], attached['scan_id'])
        self.assertEqual([event.payload.text for event in events if event.kind == 'chat'],
                         ['private-export-body'])
        self.assertNotIn('private-export-', json.dumps(captured))

    def test_non_candidate_and_fake_color_blocks_do_not_trigger_candidate_reads(self):
        fake_heads = (b'<c val="FF0000">[System] ' +
                      '<c val="FF0000">[综合] '.encode('utf-8') +
                      b'<c val="' + b'x' * 33 + '">[团队] '.encode('utf-8') +
                      b'<c val="FF0000">\n' + '[团队] '.encode('utf-8'))
        block_size = 1024 * 1024
        data = (fake_heads * (block_size // len(fake_heads) + 1))[:block_size]
        matcher = _HistoryPrefixMatcher()
        self.assertEqual(list(matcher.feed(0x80000, data)), [])
        self.assertLessEqual(len(matcher.tail), 95, '扫描只保留有界跨块元数据')

        memory = FakeMemory()
        memory.blocks = {0x80000: data}
        scanner = IncrementalDiscovery(memory, threading.Event(), channels=('团队', '所有玩家'))
        self.assertIsNone(scanner.step(byte_budget=block_size, time_budget=60))
        self.assertTrue(scanner.finished)
        self.assertEqual(scanner.diagnostic_snapshot()['candidate_count'], 0)
        self.assertEqual(memory.calls, [(0x80000, block_size)],
                         '大量无关颜色标签不得触发逐标签的历史校验读取')

    def test_non_contiguous_regions_do_not_join_partial_chat_prefixes(self):
        data = without_timestamp('region boundary').encode('utf-8')
        split, base = 19, 0x20000
        matcher = _HistoryPrefixMatcher()
        self.assertEqual(list(matcher.feed(base, data[:split])), [])
        self.assertEqual(list(matcher.feed(base + split + 8, data[split:])), [],
                         '不连续内存区域不能拼成虚构聊天头')
        self.assertEqual(list(matcher.feed(0x40000, data)), [0x40000])
        self.assertEqual(list(matcher.feed(0x40000 + len(data), b' trailing body')), [],
                         '跨块保留窗口不得重复产生已交付候选')

    def test_untimed_prefix_split_between_scan_steps_is_preserved(self):
        memory = FakeMemory()
        node, = memory.set_nodes([without_timestamp('step boundary', spacer=True)])
        memory.regions = lambda: iter([(node + 24, len(memory.blocks[node]) - 24, 4)])
        scanner = IncrementalDiscovery(memory, threading.Event(), channels=('团队', '所有玩家'))
        result = None
        for _ in range(32):
            result = scanner.step(byte_budget=7, time_budget=60)
            if result is not None or scanner.finished:
                break
        self.assertIsNotNone(result)
        self.assertEqual(result.root, memory.root)

    def test_untimed_prefix_crossing_one_mebibyte_scan_block_is_found(self):
        memory = FakeMemory()
        base, block_size = 0x100000, 1024 * 1024
        node = base + block_size - 32
        line = without_timestamp('block boundary').encode('utf-8')
        header = struct.pack('<QQQ', memory.root | 1, memory.root | 1, len(line) * 4 + 1)
        memory.blocks = {
            memory.root: struct.pack('<QQ', node, node),
            base: b'x' * (node - base) + header + line + b'\0',
        }
        memory.regions = lambda: iter([(base, len(memory.blocks[base]), 4)])
        scanner = IncrementalDiscovery(memory, threading.Event(), channels=('团队', '所有玩家'))
        self.assertIsNone(scanner.step(byte_budget=block_size, time_budget=60))
        self.assertFalse(scanner.finished)
        result = scanner.step(byte_budget=block_size, time_budget=60)
        self.assertIsNotNone(result)
        self.assertEqual(result.root, memory.root)

    def test_unsupported_metadata_does_not_force_body_reads_or_hide_an_older_team_row(self):
        memory = FakeMemory()
        team = without_timestamp('valid team')
        unsupported = without_timestamp('private-body-' * 150, channel='综合')
        _, unsupported_node = memory.set_nodes([team, unsupported])
        result = HistoryReader(memory, memory.root, include_history=True,
                               channels=('团队', '所有玩家')).poll()
        self.assertEqual([row.text for row in result.messages], ['valid team'])
        self.assertFalse(any(address == unsupported_node + 24 and count > 96
                             for address, count in memory.calls))

    def test_plain_input_buffer_and_unsupported_channel_do_not_become_history(self):
        memory = FakeMemory()
        raw = ('draft ' + without_timestamp('input text')).encode('utf-8')
        memory.blocks = {0x80000: raw}
        with self.assertRaises(DiscoveryPending):
            discover(memory, threading.Event(), channels=('团队', '所有玩家'))
        self.assertIsNone(parse_display_chat(without_timestamp(channel='综合')))

    def test_valid_looking_team_markup_inside_system_body_is_not_a_chat_header(self):
        forged = '<c val="#ColorSystem">system notice: ' + without_timestamp('forged')
        self.assertIsNone(parse_display_chat(forged))
        memory = FakeMemory()
        memory.set_nodes([forged])
        with self.assertRaises(DiscoveryPending):
            discover(memory, threading.Event(), channels=('团队', '所有玩家'))

    def test_supported_message_body_remains_literal_and_does_not_create_extra_messages(self):
        literal = without_timestamp('inner body')
        outer = without_timestamp('before ' + literal)
        parsed = parse_display_chat(outer)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.text, 'before ' + literal)
        memory = FakeMemory()
        memory.set_nodes([outer])
        self.assertEqual([row.text for row in HistoryReader(memory, memory.root, include_history=True).poll().messages],
                         ['before ' + literal])

    def test_malformed_timestamp_is_not_silently_reinterpreted_as_absent(self):
        malformed = '<s val="ChatDisplayTimeStamp">bad-clock</s><sp/>' + without_timestamp()
        self.assertIsNone(parse_display_chat(malformed))


if __name__ == '__main__':
    unittest.main()
