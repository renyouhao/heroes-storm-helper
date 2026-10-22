"""动作选择、逐键调度及偏好；只使用虚构目标和原生 API。"""
import json
from pathlib import Path
import tempfile
import unittest

from hots_chat import repeat
from hots_chat.repeat_native import Win32RepeatBackend
from hots_chat.window_settings import WindowSettings
from tests.test_direct_repeat import DirectBackend, ControlledBackend
from tests.test_loot_native import FakeKernel, FakeUser
from tests.test_repeat_native import FakeWinMM


class ActionBackend(DirectBackend):
    def send_repeat_key(self, vk, target, *, allowed=None):
        if allowed is not None and not allowed():
            return False
        if self.failure:
            raise self.failure
        self.sent.append((vk, target))
        return True


class RepeatActionSessionTests(unittest.TestCase):
    def session(self, *, action='YS', interval_ms=200, backend=None):
        self.backend = backend or ActionBackend()
        self.now = [10.]
        self.confirmed = [False]
        self.allowed = [True]
        self.events = []
        session = repeat.DirectRepeatSession(
            self.backend, self.events.append, clock=lambda: self.now[0],
            action=action, interval_ms=interval_ms,
            can_start=lambda: self.confirmed[0], can_send=lambda: self.allowed[0],
            next_generation=lambda: len(self.events) + 1)
        session.listen()
        return session

    def press(self, session, vk=0x77):
        down = session.key(vk, True, 0)
        self.assertEqual(session.key(vk, False, 0), down)
        return down

    def start(self, session):
        self.assertTrue(self.press(session))
        session.tick()
        self.assertEqual(self.backend.sent, [], '浮条确认之前不能发任何键')
        self.confirmed[0] = True
        session.tick()

    def test_ys_alternates_one_released_key_per_interval_after_confirmation(self):
        session = self.session(interval_ms=230)
        self.start(session)
        for timestamp in (10.229, 10.230, 10.459, 10.460):
            self.now[0] = timestamp
            session.tick()
        self.assertEqual(self.backend.sent, [(0x59, (11, 22)), (0x53, (11, 22)),
                                              (0x59, (11, 22))])
        self.assertIn('Y+S', self.events[-1].detail)
        self.assertIn('F8', self.events[-1].detail)

    def test_late_tick_sends_only_next_key_and_does_not_catch_up(self):
        session = self.session()
        self.start(session)
        self.now[0] = 13.
        session.tick()
        session.tick()
        self.now[0] = 13.199
        session.tick()
        self.assertEqual([vk for vk, _ in self.backend.sent], [0x59, 0x53])
        self.now[0] = 13.200
        session.tick()
        self.assertEqual([vk for vk, _ in self.backend.sent], [0x59, 0x53, 0x59])

    def test_slow_send_waits_full_interval_and_keeps_sequence(self):
        session = self.session()
        original = self.backend.send_repeat_key
        def send(vk, target, *, allowed):
            self.now[0] += .3
            return original(vk, target, allowed=allowed)
        self.backend.send_repeat_key = send
        self.start(session)
        self.now[0] = 10.499
        session.tick()
        self.assertEqual(len(self.backend.sent), 1)
        self.now[0] = 10.500
        session.tick()
        self.assertEqual([vk for vk, _ in self.backend.sent], [0x59, 0x53])

    def test_cancel_between_y_and_s_stops_and_new_f8_restarts_at_y(self):
        session = self.session()
        self.start(session)
        self.press(session)
        self.now[0] = 15.
        session.tick()
        self.assertEqual(len(self.backend.sent), 1)
        self.press(session)
        session.tick()
        self.assertEqual([vk for vk, _ in self.backend.sent], [0x59, 0x59])

    def test_ys_reuses_existing_cancel_and_send_gate_before_next_key(self):
        for cancel in ('enter', 'escape', 'modifier', 'focus', 'gate'):
            with self.subTest(cancel=cancel):
                session = self.session()
                self.start(session)
                if cancel == 'enter': self.press(session, 0x0D)
                elif cancel == 'escape': self.press(session, 0x1B)
                elif cancel == 'modifier': self.press(session, 0x11)
                elif cancel == 'focus': self.backend.target = None
                else: self.allowed[0] = False
                self.now[0] = 11.
                session.tick()
                self.assertEqual(session.status.state, 'idle')
                self.assertEqual([vk for vk, _ in self.backend.sent], [0x59])

    def test_original_s_mode_uses_legacy_backend_and_twenty_ms_even_with_custom_ys_interval(self):
        session = self.session(action='S', interval_ms=5000, backend=DirectBackend())
        self.start(session)
        self.now[0] = 10.019; session.tick()
        self.assertEqual(len(self.backend.sent), 1)
        self.now[0] = 10.020; session.tick()
        self.assertEqual(self.backend.sent, [(11, 22), (11, 22)])

    def test_unsupported_ys_backend_fails_without_sending_fallback_s(self):
        session = self.session(backend=DirectBackend())
        self.start(session)
        self.assertEqual(session.status.state, 'error')
        self.assertEqual(self.backend.sent, [])

    def test_invalid_config_is_rejected_before_listening_or_sending(self):
        for action, interval in (('Y', 200), ('Y+S', 200), (None, 200),
                                 ('YS', 19), ('YS', 5001), ('YS', True),
                                 ('YS', ''), ('YS', '200'), ('YS', 200.5)):
            with self.subTest(action=action, interval=interval), self.assertRaises(ValueError):
                repeat.RepeatSession(ActionBackend(), lambda status: None,
                                     action=action, interval_ms=interval)


class RepeatActionNativeTests(unittest.TestCase):
    def setUp(self):
        self.user, self.kernel = FakeUser(), FakeKernel()
        self.backend = Win32RepeatBackend(user=self.user, kernel=self.kernel, winmm=FakeWinMM())

    def test_ys_key_capability_sends_marked_y_and_s_down_up_pairs(self):
        target = self.backend.foreground_target()
        self.assertTrue(self.backend.send_repeat_key(0x59, target))
        self.assertTrue(self.backend.send_repeat_key(0x53, target))
        self.assertEqual(self.user.sent, [
            (2, 40, [(1, 0x59, 0, 0x484F5453), (1, 0x59, 2, 0x484F5453)]),
            (2, 40, [(1, 0x53, 0, 0x484F5453), (1, 0x53, 2, 0x484F5453)])])

    def test_ys_key_failure_only_releases_the_failed_key(self):
        for vk in (0x59, 0x53):
            with self.subTest(vk=vk):
                self.user.sent.clear()
                self.user.send_results = [1, 1]
                with self.assertRaises(OSError):
                    self.backend.send_repeat_key(vk, self.backend.foreground_target())
                self.assertEqual(self.user.sent[1], (1, 40, [(1, vk, 2, 0x484F5453)]))

    def test_ys_single_key_obeys_target_and_cancellation_fence(self):
        target = self.backend.foreground_target()
        self.assertFalse(self.backend.send_repeat_key(0x59, target, allowed=lambda: False))
        self.user.hwnd += 1
        self.assertFalse(self.backend.send_repeat_key(0x53, target))
        self.assertEqual(self.user.sent, [])


class RepeatActionSettingsTests(unittest.TestCase):
    def test_new_defaults_and_saved_choice_roundtrip(self):
        settings = WindowSettings()
        self.assertEqual(getattr(settings, 'repeat_action', None), 'S')
        self.assertEqual(getattr(settings, 'repeat_interval_ms', None), 200)
        settings.repeat_action, settings.repeat_interval_ms = 'YS', 375
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'
            settings.save(path)
            loaded = WindowSettings.load(path)
            self.assertEqual((loaded.repeat_action, loaded.repeat_interval_ms), ('YS', 375))

    def test_old_preferences_keep_other_values_and_default_to_s_and_200(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'
            path.write_text('{"font_size":17,"color_theme":"mint"}', encoding='utf-8')
            loaded = WindowSettings.load(path)
            self.assertEqual((getattr(loaded, 'repeat_action', None),
                              getattr(loaded, 'repeat_interval_ms', None)), ('S', 200))
            self.assertEqual((loaded.font_size, loaded.color_theme), (17, 'mint'))

    def test_corrupt_action_preferences_fall_back_without_erasing_unrelated_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'
            for action, interval in (('Y', 400), (None, 200), ('YS', 0),
                                     ('YS', 5001), ('YS', True), ('YS', '200')):
                with self.subTest(action=action, interval=interval):
                    path.write_text(json.dumps(dict(repeat_action=action, repeat_interval_ms=interval,
                                                    font_size=17)), encoding='utf-8')
                    loaded = WindowSettings.load(path)
                    self.assertEqual((getattr(loaded, 'repeat_action', None),
                                      getattr(loaded, 'repeat_interval_ms', None)), ('S', 200))
                    self.assertEqual(loaded.font_size, 17)


class RepeatActionHelperTests(unittest.TestCase):
    def test_reconfigure_ends_old_action_before_installing_one_new_listener(self):
        backends = []
        class Backend(ControlledBackend, ActionBackend):
            pass
        def factory():
            backend = Backend()
            backends.append(backend)
            return backend
        helper = repeat.RepeatHelper(backend_factory=factory)
        self.addCleanup(helper.close)
        self.assertTrue(helper.start_listening())
        self.assertTrue(backends[0].started.wait(2))
        backends[0].command('press')
        old = helper.drain_status()[-1]
        helper.confirm_ready(old.generation)
        backends[0].command('tick')
        old_thread = helper.thread
        self.assertTrue(helper.start_listening(action='YS', interval_ms=400))
        self.assertFalse(old_thread.is_alive())
        self.assertTrue(backends[1].started.wait(2))
        self.assertFalse(helper.active)
        backends[1].command('tick')
        self.assertEqual(backends[1].sent, [])
        self.assertFalse(helper.confirm_ready(old.generation))
        backends[1].command('press')
        requested = helper.drain_status()[-1]
        helper.confirm_ready(requested.generation)
        backends[1].command('tick')
        self.assertEqual(backends[1].sent, [(0x59, (11, 22))])
        current = helper.thread
        self.assertTrue(helper.start_listening(action='YS', interval_ms=400))
        self.assertIs(helper.thread, current)


if __name__ == '__main__': unittest.main()
