import unittest

from hots_chat.spam import AutomaticGuard


class AutomaticGuardTests(unittest.TestCase):
    def setUp(self):
        self.tick = 0.0
        self.guard = AutomaticGuard(monotonic=lambda: self.tick)

    def accept(self, sender, text, channel='团队'):
        reason = self.guard.reason(channel, sender, text)
        if reason is None:
            self.guard.record(channel, sender, text)
        return reason

    def test_character_budget_uses_utf16_conservative_count_and_reopens_at_sixty_seconds(self):
        self.assertIsNone(self.accept('one', 'a' * 2998))
        self.assertIsNone(self.accept('two', '😀'))
        self.assertEqual(self.accept('three', 'b'), 'global_characters')
        self.tick = 59.999
        self.assertEqual(self.accept('three', 'b'), 'global_characters')
        self.tick = 60
        self.assertIsNone(self.accept('three', 'b'))

    def test_sender_and_character_state_remain_bounded_under_many_names(self):
        accepted = sum(self.accept(str(index), 'a') is None for index in range(10000))
        self.assertEqual(accepted, 3000)
        self.assertLessEqual(len(self.guard.senders), 1024)
        self.assertLessEqual(len(self.guard.characters), 3000)
        self.tick = 60
        self.assertIsNone(self.accept('fresh', 'a'))
        self.assertEqual(len(self.guard.senders), 1)
        self.assertEqual(len(self.guard.characters), 1)


if __name__ == '__main__':
    unittest.main()
