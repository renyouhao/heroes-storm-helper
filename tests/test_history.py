import struct
import unittest

try:
    import hots_chat.acquisition.history as reader
except ModuleNotFoundError:
    reader = None


def chat_line(body):
    return '<s val="ChatDisplayTimeStamp">24:54</s><sp/><c val="3184FF">[团队]<c val="3184FF"><w><s val="ChatDisplayHeroIcon"><img path="hero.dds"/></s>Hunter</w></c>: </c>' + body


class FakeMemory:
    root = 0x10000

    def __init__(self):
        self.blocks = {self.root: struct.pack('<QQ', self.root | 1, self.root | 1)}
        self.calls = []

    def read(self, address, count):
        self.calls.append((address, count))
        for base, data in self.blocks.items():
            if base <= address < base + len(data):
                return data[address-base:address-base+count]
        return b''

    def regions(self):
        yield from [(base,len(data),4) for base,data in sorted(self.blocks.items())]

    def set_nodes(self, messages):
        nodes = [0x20000 + index * 0x10000 for index in range(len(messages))]
        self.blocks[self.root] = struct.pack('<QQ', nodes[-1] if nodes else self.root | 1, nodes[0] if nodes else self.root | 1)
        for index, (node, message) in enumerate(zip(nodes, messages)):
            data = message.encode('utf-8')
            older = nodes[index-1] if index else self.root | 1
            newer = nodes[index+1] if index+1 < len(nodes) else self.root | 1
            self.blocks[node] = struct.pack('<QQQ', older, newer, len(data)*4+1) + data + b'\x00'
        return nodes


class HistoryReaderTests(unittest.TestCase):
    def test_nonempty_history_cleared_signals_relocation_instead_of_waiting_forever(self):
        memory=FakeMemory();memory.set_nodes([chat_line('old')])
        poller=self.make_reader(memory,include_history=False);poller.poll()
        memory.set_nodes([])
        with self.assertRaises(reader.HistoryInvalid):poller.poll()

    def test_crossing_one_hour_does_not_drop_new_messages(self):
        memory=FakeMemory()
        baseline=chat_line('baseline').replace('24:54','59:59',1)
        memory.set_nodes([baseline])
        poller=self.make_reader(memory,include_history=False)
        poller.poll()
        message=chat_line('hello 안녕하세요 你好').replace('24:54','01:00:00',1)
        memory.set_nodes([baseline,message,message])
        result=poller.poll()
        self.assertEqual([m.text for m in result.messages],['hello 안녕하세요 你好']*2)
        self.assertFalse(result.gap)
        self.assertEqual(poller.poll().messages,[])

    def make_reader(self, memory, include_history=True):
        self.assertIsNotNone(reader, 'Bounded history reader has not been implemented')
        return reader.HistoryReader(memory, memory.root, include_history=include_history)

    def test_tagged_sentinel_and_chronological_order(self):
        memory = FakeMemory(); memory.set_nodes([chat_line('old'), chat_line('new')])
        result = self.make_reader(memory).poll()
        self.assertEqual([m.text for m in result.messages], ['old', 'new'])

    def test_identical_messages_at_different_nodes_are_both_delivered(self):
        memory = FakeMemory(); memory.set_nodes([chat_line('baseline')])
        poller = self.make_reader(memory, include_history=False); self.assertEqual(poller.poll().messages, [])
        memory.set_nodes([chat_line('baseline'), chat_line('same'), chat_line('same')])
        self.assertEqual([m.text for m in poller.poll().messages], ['same', 'same'])
        self.assertEqual(poller.poll().messages, [])

    def test_unchanged_head_does_not_traverse_older_messages(self):
        memory = FakeMemory(); nodes=memory.set_nodes([chat_line('old'),chat_line('baseline')]); poller = self.make_reader(memory)
        poller.poll(); memory.calls.clear(); poller.poll()
        self.assertTrue(memory.calls)
        self.assertTrue(all(address in (memory.root,nodes[-1],nodes[-1]+24) for address,count in memory.calls))
        self.assertLessEqual(len(memory.calls),6)

    def test_reused_head_address_does_not_hide_new_messages(self):
        memory=FakeMemory(); memory.set_nodes([chat_line('old-a'),chat_line('old-b')]); poller=self.make_reader(memory,include_history=False)
        poller.poll(); memory.set_nodes([chat_line('new-a'),chat_line('new-b')])
        result=poller.poll()
        self.assertEqual([m.text for m in result.messages],['new-a','new-b'])
        self.assertTrue(result.gap)

    def test_broken_link_cycle_is_rejected(self):
        memory=FakeMemory(); nodes=memory.set_nodes([chat_line('a'),chat_line('b')])
        block=bytearray(memory.blocks[nodes[-1]]); struct.pack_into('<Q',block,0,nodes[-1]); memory.blocks[nodes[-1]]=bytes(block)
        poller=self.make_reader(memory)
        with self.assertRaises(reader.HistoryInvalid): poller.poll()

    def test_malformed_whisper_without_contact_is_not_collected(self):
        private='<s val="ChatDisplayTimeStamp">24:54</s><sp/><c val="#ColorBattleNetWhisper">' + 'private'*200
        memory=FakeMemory(); nodes=memory.set_nodes([private,chat_line('team')])
        result=self.make_reader(memory).poll()
        self.assertEqual([m.text for m in result.messages], ['team'])
        self.assertFalse(any(address==nodes[0]+24 and count>96 for address,count in memory.calls))


if __name__ == '__main__': unittest.main()
