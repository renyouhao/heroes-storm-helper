import unittest
import struct
from test_chat_history_reader import FakeMemory, chat_line

try:
    import discover_chat_history as discovery
except ModuleNotFoundError:
    discovery=None


class DiscoveryTests(unittest.TestCase):
    def locate(self,memory,node):
        self.assertIsNotNone(discovery,'Automatic history discovery has not been implemented')
        return discovery.locate_root(memory,node)

    def test_finds_root_from_an_interior_message_without_fixed_addresses(self):
        memory=FakeMemory(); nodes=memory.set_nodes([chat_line('a'),chat_line('b'),chat_line('c')])
        self.assertEqual(self.locate(memory,nodes[1]),memory.root)

    def test_rejects_a_forged_sentinel_tail(self):
        self.assertIsNotNone(discovery,'Automatic history discovery has not been implemented')
        memory=FakeMemory(); nodes=memory.set_nodes([chat_line('a'),chat_line('b')])
        memory.blocks[memory.root]=struct.pack('<QQ',nodes[-1],nodes[-1])
        with self.assertRaises(Exception) as context: self.locate(memory,nodes[1])
        self.assertIsInstance(context.exception,discovery.HistoryInvalid)

    def test_rejects_a_plain_input_buffer(self):
        self.assertIsNotNone(discovery,'Automatic history discovery has not been implemented')
        memory=FakeMemory(); nodes=memory.set_nodes(['HOTS_CHAT_TEST_20261019_C9D5 buffer growth test'])
        with self.assertRaises(Exception) as context: self.locate(memory,nodes[0])
        self.assertIsInstance(context.exception,discovery.HistoryInvalid)


if __name__=='__main__': unittest.main()
