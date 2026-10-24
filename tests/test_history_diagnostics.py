"""聊天读取诊断只使用已有读取结果，不记录文本、频道名或地址。"""
import json
import struct
import unittest

from hots_chat.acquisition.history import HistoryCleared, HistoryInvalid, HistoryReader
from hots_chat.acquisition.parsing import chat_prefix_rejection_reason, has_allowed_chat_prefix
from test_history import FakeMemory, chat_line
from test_party_whisper_acquisition import party_out


class HistoryDiagnosticTests(unittest.TestCase):
    def read_line(self, line, **options):
        memory = FakeMemory()
        node, = memory.set_nodes([line])
        reader = HistoryReader(memory, memory.root, include_history=True, **options)
        result = reader.poll()
        calls = list(memory.calls)
        snapshot = reader.diagnostic_snapshot()
        self.assertEqual(memory.calls, calls, '计数快照不得增加进程读取')
        self.assertTrue(all(type(value) is int for value in snapshot.values()))
        return memory, node, reader, result, snapshot

    def test_prefix_rejection_counts_distinguish_timestamp_channel_and_structure(self):
        secret = 'private-body-' * 100
        cases = (
            (chat_line(secret).replace('24:54', 'bad-clock'), 'timestamp_format_rejected_count'),
            (chat_line(secret).replace('[团队]', '[秘密频道]'), 'channel_unsupported_count'),
            ('<s val="ChatDisplayTimeStamp">24:54</s><sp/><c val="#ColorSystem">' + secret,
             'message_format_rejected_count'),
        )
        for line, field in cases:
            with self.subTest(field=field):
                memory, node, _, result, stats = self.read_line(line)
                self.assertEqual(result.messages, [])
                self.assertEqual(stats['nodes_checked_count'], 1)
                self.assertEqual(stats['prefix_rejected_count'], 1)
                self.assertEqual(stats[field], 1)
                self.assertEqual(stats['parsed_message_count'], 0)
                self.assertFalse(any(address == node + 24 and count > 96
                                     for address, count in memory.calls))
                text = json.dumps(stats)
                for sensitive in ('private-body', '秘密频道', str(node)):
                    self.assertNotIn(sensitive, text)

    def test_supported_prefix_with_bad_full_message_counts_format_rejection(self):
        line = chat_line('private body').replace('</w></c>: </c>', '</w></c>:</c>')
        _, _, _, result, stats = self.read_line(line)
        self.assertEqual(result.messages, [])
        self.assertEqual(stats['prefix_rejected_count'], 0)
        self.assertEqual(stats['message_format_rejected_count'], 1)

    def test_match_channel_filter_is_distinct_from_unsupported_client_channel(self):
        _, _, _, result, stats = self.read_line(party_out('private party'), channels=('团队', '所有玩家'))
        self.assertEqual(result.messages, [])
        self.assertEqual(stats['parsed_message_count'], 1)
        self.assertEqual(stats['channel_filtered_count'], 1)
        self.assertEqual(stats['channel_unsupported_count'], 0)
        self.assertEqual(stats['prefix_rejected_count'], 0)

    def test_successful_message_preserves_read_sequence_and_snapshot_is_a_copy(self):
        line = chat_line('private body and private nickname').replace('Hunter', 'private-sender')
        memory, node, reader, result, stats = self.read_line(line)
        self.assertEqual([row.text for row in result.messages], ['private body and private nickname'])
        self.assertEqual(memory.calls, [(memory.root, 16), (node, 24), (node + 24, 96),
                                        (node + 24, len(line.encode('utf-8'))), (memory.root, 16)])
        self.assertEqual(stats['parsed_message_count'], 1)
        stats['parsed_message_count'] = -1
        self.assertEqual(reader.diagnostic_snapshot()['parsed_message_count'], 1)
        reader.poll()
        self.assertEqual(reader.diagnostic_snapshot()['nodes_checked_count'], 2,
                         '计数代表实际检查，包含重复轮询，不代表消息交付次数')

    def test_fixed_prefix_reasons_preserve_allowed_prefix_behavior(self):
        for prefix, expected in (
            (chat_line('hello'), None),
            (party_out('hello'), None),
            (chat_line('hello').replace('[团队]', '[Team]'), 'channel_unsupported'),
            ('not timestamped private input', 'timestamp_format_rejected'),
            ('<s val="ChatDisplayTimeStamp">24:54</s><sp/>system notice', 'message_format_rejected'),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(chat_prefix_rejection_reason(prefix), expected)
                self.assertEqual(has_allowed_chat_prefix(prefix), expected is None)

    def test_read_and_layout_failures_have_fixed_reasons(self):
        self.assertEqual(HistoryInvalid('private failure').reason, 'history_invalid')
        self.assertEqual(str(HistoryInvalid('private failure')), 'private failure')
        with self.assertRaises(HistoryInvalid) as invalid_root:
            HistoryReader(FakeMemory(), 1)
        self.assertEqual(invalid_root.exception.reason, 'invalid_root')

        for case, reason in (('address', 'invalid_address'), ('budget', 'read_budget'),
                             ('short_read', 'read_unavailable'), ('layout', 'invalid_layout'),
                             ('timestamp', 'timestamp_missing'), ('cycle', 'invalid_links'),
                             ('nodes', 'node_budget'), ('utf8', 'invalid_utf8')):
            with self.subTest(case=case):
                memory = FakeMemory()
                nodes = memory.set_nodes([chat_line('first'), chat_line('private last')])
                options = {}
                if case == 'budget':
                    options['byte_budget'] = 16
                elif case == 'short_read':
                    memory.blocks.clear()
                elif case == 'layout':
                    block = bytearray(memory.blocks[nodes[-1]])
                    struct.pack_into('<Q', block, 16, 0)
                    memory.blocks[nodes[-1]] = bytes(block)
                elif case == 'timestamp':
                    memory.set_nodes(['not timestamped private string'])
                elif case == 'cycle':
                    block = bytearray(memory.blocks[nodes[-1]])
                    struct.pack_into('<Q', block, 0, nodes[-1])
                    memory.blocks[nodes[-1]] = bytes(block)
                elif case == 'nodes':
                    options['max_nodes'] = 1
                elif case == 'utf8':
                    block = bytearray(memory.blocks[nodes[-1]])
                    block[-2] = 255
                    memory.blocks[nodes[-1]] = bytes(block)
                reader = HistoryReader(memory, memory.root, include_history=True, **options)
                with self.assertRaises(HistoryInvalid) as failure:
                    if case == 'address':
                        reader.read(0, 1)
                    else:
                        reader.poll()
                self.assertEqual(failure.exception.reason, reason)

    def test_cleared_history_has_distinct_reason(self):
        memory = FakeMemory()
        memory.set_nodes([chat_line('old')])
        reader = HistoryReader(memory, memory.root, include_history=True)
        reader.poll()
        memory.set_nodes([])
        with self.assertRaises(HistoryCleared) as failure:
            reader.poll()
        self.assertEqual(failure.exception.reason, 'history_cleared')


if __name__ == '__main__':
    unittest.main()
