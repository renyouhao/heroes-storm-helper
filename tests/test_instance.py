import unittest
import uuid
from hots_chat.instance import SingleInstance

class InstanceTests(unittest.TestCase):
    def test_second_instance_exits_without_network(self):
        name='Local\\HotsChatTest'+uuid.uuid4().hex
        a=SingleInstance(name); b=SingleInstance(name)
        try:
            self.assertTrue(a.acquire())
            self.assertFalse(b.acquire())
        finally:
            a.close(); b.close()
        c=SingleInstance(name)
        self.assertTrue(c.acquire()); c.close()
