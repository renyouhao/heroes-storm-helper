"""双来源协调仅用可控 worker，不读取游戏、云服务或界面。"""
import importlib
import importlib.util
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hots_chat.acquisition.memory import GameProcess
from hots_chat.acquisition.source import ChatSource
from hots_chat.controller import ChatController
from hots_chat.models import AppEvent, ChatEvent, TranslationResult
from hots_chat.usage import BudgetLedger


PROCESS = GameProcess(10, r'D:\game\Base98348\HeroesOfTheStorm_x64.exe', 98348)


class ControlledWorker:
    """代替具有外部内存副作用的来源；事件仍由真实协调器处理。"""

    def __init__(self, selected_pid=None, process_finder=None, game_factory=None, clock=None):
        self.selected_pid = selected_pid
        self.process_finder = process_finder
        self.game_factory = game_factory
        self.clock = clock
        self.generation = 0
        self.running = False
        self.starts = self.stops = self.reconnects = 0
        self.sinks = []

    def start(self, emit):
        self.running = True
        self.starts += 1
        self.sinks.append(emit)

    def stop(self):
        self.running = False
        self.stops += 1
        self.send('capture_state', '已停止')

    def reconnect(self):
        self.reconnects += 1

    def observe(self):
        return self.process_finder()

    def transition(self, state='已连接'):
        self.generation += 1
        self.send('generation', '子来源恢复')
        self.send('capture_state', state)

    def connect(self):
        self.observe()
        self.transition()

    def send(self, kind, payload, generation=None):
        generation = self.generation if generation is None else generation
        self.sinks[-1](AppEvent(kind, generation, payload))

    def chat(self, text, channel='团队', generation=None, identifier='same-id'):
        generation = self.generation if generation is None else generation
        self.send('chat', ChatEvent(identifier, generation, channel, '朋友', '00:01', text), generation)


class PersistentChatTests(unittest.TestCase):
    def source(self, processes=None, **kwargs):
        spec = importlib.util.find_spec('hots_chat.acquisition.persistent')
        self.assertIsNotNone(spec, '缺少双来源持续采集协调器')
        cls = importlib.import_module('hots_chat.acquisition.persistent').PersistentChatSource
        processes = [PROCESS] if processes is None else processes
        kwargs.setdefault('match_factory', ControlledWorker)
        kwargs.setdefault('lobby_factory', ControlledWorker)
        kwargs.setdefault('process_finder', lambda: list(processes))
        source = cls(**kwargs)
        self.addCleanup(source.stop)
        events = []
        source.start(events.append)
        return source, source.match_source, source.lobby_source, events

    @staticmethod
    def chats(events):
        return [event.payload for event in events if event.kind == 'chat']

    def test_both_sources_keep_receiving_when_one_waits_or_errors(self):
        source, match, lobby, events = self.source()
        match.connect(); lobby.connect()
        generation = source.generation
        match.transition('等待对局聊天，自动恢复')
        match.send('error', '对局读取暂时不可用')
        lobby.chat('still private', '密语')
        match.transition()
        match.chat('back in match')
        self.assertTrue(source.running)
        self.assertEqual(source.generation, generation)
        self.assertEqual([chat.text for chat in self.chats(events)], ['still private', 'back in match'])
        self.assertEqual({chat.generation for chat in self.chats(events)}, {generation})

    def test_channel_ownership_prevents_cross_format_duplicates_without_text_deduplication(self):
        source, match, lobby, events = self.source()
        match.connect(); lobby.connect()
        for channel in ('团队', '所有玩家', '队伍', '密语'):
            match.chat('same text', channel)
            lobby.chat('same text', channel)
        lobby.chat('same text', '密语')
        chats = self.chats(events)
        self.assertEqual([chat.channel for chat in chats], ['团队', '所有玩家', '队伍', '密语', '密语'])
        self.assertEqual(len({chat.message_id for chat in chats}), 5)

    def test_late_child_generation_events_cannot_reenter_current_stream(self):
        source, match, lobby, events = self.source()
        match.connect(); lobby.connect()
        previous = lobby.generation
        lobby.transition()
        lobby.chat('late', '密语', generation=previous)
        lobby.send('status', 'late status', generation=previous)
        lobby.send('generation', 'late transition', generation=previous)
        lobby.chat('current', '密语')
        self.assertEqual([chat.text for chat in self.chats(events)], ['current'])
        self.assertFalse(any(event.payload == 'late status' for event in events))

    def test_process_replacement_fences_lagging_worker_and_advances_only_once(self):
        processes = [PROCESS]
        source, match, lobby, events = self.source(processes)
        match.connect(); lobby.connect()
        initial = source.generation
        processes[:] = [GameProcess(20, PROCESS.path, PROCESS.build)]
        match.observe(); match.transition()
        match.chat('new match')
        lobby.chat('old lobby', '密语')
        lobby.observe(); lobby.transition()
        lobby.chat('new lobby', '密语')
        self.assertEqual(source.generation, initial + 1)
        self.assertEqual([chat.text for chat in self.chats(events)], ['new match', 'new lobby'])
        self.assertEqual({chat.generation for chat in self.chats(events)}, {initial + 1})

    def test_same_pid_path_or_build_replacement_requires_a_new_worker_session(self):
        for replacement in (
                GameProcess(10, r'E:\other\Base98348\HeroesOfTheStorm_x64.exe', 98348),
                GameProcess(10, PROCESS.path, 99999)):
            with self.subTest(replacement=replacement):
                processes = [PROCESS]
                source, match, lobby, events = self.source(processes)
                match.connect(); lobby.connect()
                initial = source.generation
                processes[:] = [replacement]
                # A one-poll absence makes the legacy worker close its old PID handle.
                self.assertEqual(match.observe(), [])
                match.chat('old handle')
                self.assertEqual(match.observe(), [replacement])
                match.transition(); match.chat('new handle')
                self.assertEqual(source.generation, initial + 1)
                self.assertEqual([chat.text for chat in self.chats(events)], ['new handle'])

    def test_process_disappearance_and_return_are_distinct_sessions(self):
        processes = [PROCESS]
        source, match, lobby, events = self.source(processes)
        match.connect(); lobby.connect()
        initial = source.generation
        processes.clear()
        match.observe(); match.transition('等待游戏')
        lobby.chat('exited process', '密语')
        processes.append(PROCESS)
        lobby.observe(); lobby.transition()
        lobby.observe(); lobby.chat('new session', '密语')
        self.assertEqual(source.generation, initial + 2)
        self.assertEqual([chat.text for chat in self.chats(events)], ['new session'])

    def test_worker_missing_a_disappearance_cannot_reuse_its_old_same_pid_handle(self):
        processes = [PROCESS]
        source, match, lobby, events = self.source(processes)
        match.connect(); lobby.connect()
        processes.clear()
        match.observe(); match.transition('等待游戏')
        processes.append(PROCESS)
        match.observe(); match.transition()
        self.assertEqual(lobby.observe(), [], '错过消失的来源必须先释放旧会话句柄')
        lobby.chat('late old session', '密语')
        lobby.transition('等待重新连接')
        self.assertEqual(lobby.observe(), [PROCESS])
        lobby.chat('new session', '密语')
        self.assertEqual([chat.text for chat in self.chats(events)], ['new session'])

    def test_reconnect_exception_in_one_source_does_not_block_the_healthy_source(self):
        source, match, lobby, events = self.source()
        match.connect(); lobby.connect()

        def failed_reconnect():
            raise OSError('unavailable match worker')

        match.reconnect = failed_reconnect
        source.reconnect()
        self.assertEqual(lobby.reconnects, 1)
        lobby.chat('healthy source', '密语')
        self.assertEqual([chat.text for chat in self.chats(events)], ['healthy source'])

    def test_stop_attempts_both_workers_even_if_one_stop_raises(self):
        source, match, lobby, events = self.source()
        match.connect(); lobby.connect()

        def failed_stop():
            match.running = False
            raise OSError('already closed match handle')

        match.stop = failed_stop
        source.stop()
        self.assertFalse(source.running)
        self.assertFalse(lobby.running)
        count = len(events)
        lobby.chat('late after failure', '密语')
        self.assertEqual(len(events), count)

    def test_running_reflects_a_worker_still_exiting_after_its_stop_timeout(self):
        source, match, lobby, events = self.source()
        match.connect(); lobby.connect()
        match.stop = lambda: None  # Models the existing worker's bounded join timeout.
        source.stop()
        self.assertTrue(source.running, '只要任一子线程还活动，running就应反映事实')
        count = len(events)
        match.chat('blocked while exiting')
        self.assertEqual(len(events), count)
        match.running = False
        self.assertFalse(source.running)

    def test_start_after_real_lobby_stop_timeout_restarts_it_after_the_old_thread_exits(self):
        from hots_chat.acquisition.lobby_source import LobbySource

        entered, release, restarted_poll = threading.Event(), threading.Event(), threading.Event()
        calls = []

        def process_finder():
            calls.append(threading.current_thread())
            if len(calls) == 1:
                entered.set()
                release.wait(10)
            else:
                restarted_poll.set()
            return []

        source, match, lobby, events = self.source(
            process_finder=process_finder, lobby_factory=LobbySource)
        try:
            self.assertTrue(entered.wait(2))
            old_thread = lobby.thread
            source.stop()  # Real LobbySource joins for three seconds, then times out.
            self.assertTrue(lobby.running)
            source.start(events.append)
            restart_count = len(events)
            release.set()
            self.assertTrue(restarted_poll.wait(2), '旧线程退出后必须补启动大厅来源')
            self.assertIsNot(lobby.thread, old_thread)
            self.assertTrue(lobby.running)
            deadline = time.monotonic() + 2
            while (not any(event.kind == 'capture_state' and '大厅：等待游戏' in str(event.payload)
                           for event in events[restart_count:]) and time.monotonic() < deadline):
                time.sleep(.01)
            self.assertTrue(any(event.kind == 'capture_state' and '大厅：等待游戏' in str(event.payload)
                                for event in events[restart_count:]), '新线程必须使用本轮回调发布状态')
        finally:
            release.set()
            source.stop()

    def test_user_stop_cancels_a_deferred_restart_of_a_worker_still_exiting(self):
        source, match, lobby, events = self.source()
        match.connect(); lobby.connect()
        lobby.stop = lambda: None
        source.stop()
        source.start(events.append)
        source.stop()
        after_stop = len(events)
        lobby.running = False
        restarted = threading.Event()
        original_start = lobby.start

        def observed_start(emit):
            original_start(emit)
            restarted.set()

        lobby.start = observed_start
        self.assertFalse(restarted.wait(.2), '用户停止后不能补启动上一次尚未退出的来源')
        self.assertFalse(source.running)
        self.assertEqual(len(events), after_stop)

    def test_selection_updates_both_workers_and_fences_the_previous_target(self):
        other = GameProcess(20, PROCESS.path, PROCESS.build)
        processes = [PROCESS, other]
        source, match, lobby, events = self.source(processes, selected_pid=10)
        match.connect(); lobby.connect()
        initial = source.generation
        source.selected_pid = 20
        self.assertEqual((match.selected_pid, lobby.selected_pid), (20, 20))
        lobby.chat('old selection', '密语')
        self.assertEqual(match.observe(), [other])
        match.transition(); match.chat('selected')
        self.assertEqual(source.selected_pid, 20)
        self.assertEqual([chat.text for chat in self.chats(events)], ['selected'])
        self.assertGreaterEqual(source.generation, initial + 1)
        self.assertEqual(source.process_finder(), [PROCESS, other], '设置页仍须看到所有候选进程')

    def test_selected_process_exit_restores_auto_selection_for_both_workers(self):
        processes = [PROCESS]
        source, match, lobby, events = self.source(processes, selected_pid=10)
        match.connect(); lobby.connect()
        initial = source.generation
        processes.clear()
        self.assertEqual(match.observe(), [])
        match.transition('等待游戏')
        lobby.chat('old selected process', '密语')
        self.assertIsNone(source.selected_pid, '所选进程退出后须恢复旧来源的自动选择行为')
        self.assertEqual((match.selected_pid, lobby.selected_pid), (None, None))
        replacement = GameProcess(20, PROCESS.path, PROCESS.build)
        processes.append(replacement)
        self.assertEqual(match.observe(), [replacement])
        match.transition(); match.chat('new automatic match')
        self.assertEqual(lobby.observe(), [replacement])
        lobby.transition(); lobby.chat('new automatic lobby', '密语')
        self.assertEqual(source.generation, initial + 2)
        self.assertEqual([chat.text for chat in self.chats(events)],
                         ['new automatic match', 'new automatic lobby'])

    def test_manual_reconnect_does_not_cancel_the_other_source_generation(self):
        source, match, lobby, events = self.source()
        match.connect(); lobby.connect()
        initial = source.generation
        source.reconnect()
        self.assertEqual((match.reconnects, lobby.reconnects), (1, 1))
        match.transition(); lobby.chat('during match reconnect', '密语')
        lobby.transition(); match.chat('during lobby reconnect')
        self.assertEqual(source.generation, initial)
        self.assertEqual(len(self.chats(events)), 2)

    def test_start_is_idempotent_and_stop_fences_all_child_and_previous_run_events(self):
        source, match, lobby, events = self.source()
        match.connect(); lobby.connect()
        initial = source.generation
        source.start(events.append)
        self.assertEqual((match.starts, lobby.starts), (1, 1))
        stale_sink = lobby.sinks[-1]
        source.stop()
        stopped_count = len(events)
        match.chat('after stop'); lobby.chat('after stop', '密语')
        self.assertFalse(source.running)
        self.assertEqual((match.stops, lobby.stops), (1, 1))
        self.assertEqual(len(events), stopped_count)
        source.generation = initial + 5
        source.start(events.append)
        match.connect(); lobby.connect()
        stale_sink(AppEvent('chat', lobby.generation,
                            ChatEvent('old', lobby.generation, '密语', 'x', '', 'previous run')))
        lobby.chat('current run', '密语')
        self.assertEqual(source.generation, initial + 6)
        self.assertEqual([chat.text for chat in self.chats(events)], ['current run'])

    def test_waiting_or_child_shutdown_never_reports_the_whole_capture_as_stopped(self):
        source, match, lobby, events = self.source()
        match.connect(); lobby.connect()
        match.send('capture_state', '等待对局聊天，自动恢复')
        lobby.send('capture_state', '已停止')
        states = [str(event.payload) for event in events if event.kind == 'capture_state']
        self.assertNotEqual(states[-1], '已停止')
        self.assertIn('持续采集中', states[-1])
        self.assertIn('对局', states[-1])
        self.assertIn('大厅', states[-1])
        source.stop()
        self.assertEqual([event.payload for event in events if event.kind == 'capture_state'][-1], '已停止')

    def test_each_worker_opens_its_own_game_handle(self):
        opened = []

        class OpeningWorker(ControlledWorker):
            def start(self, emit):
                super().start(emit)
                self.handle = self.game_factory(10)

        def game_factory(pid):
            handle = type('Handle', (), {'pid': pid, 'read_buffer': object()})()
            opened.append(handle)
            return handle

        source, match, lobby, events = self.source(
            game_factory=game_factory, match_factory=OpeningWorker, lobby_factory=OpeningWorker)
        match.connect(); lobby.connect()
        self.assertEqual(len(opened), 2)
        self.assertIsNot(match.handle, lobby.handle)
        self.assertIsNot(match.handle.read_buffer, lobby.handle.read_buffer)

    def test_source_start_failure_keeps_the_healthy_worker_receiving(self):
        class FailingWorker(ControlledWorker):
            def start(self, emit):
                raise OSError('cannot start match worker')

        source, match, lobby, events = self.source(match_factory=FailingWorker)
        lobby.connect(); lobby.chat('healthy after startup failure', '密语')
        self.assertTrue(source.running)
        self.assertEqual([chat.text for chat in self.chats(events)], ['healthy after startup failure'])
        self.assertTrue(any(event.kind == 'status' and 'cannot start match worker' in event.payload
                            for event in events))

    def test_slow_older_process_enumeration_cannot_restore_an_obsolete_session(self):
        source, match, lobby, events = self.source()
        match.connect(); lobby.connect()
        initial = source.generation
        entered, release = threading.Event(), threading.Event()
        replacement = GameProcess(20, PROCESS.path, PROCESS.build)

        def process_finder():
            if threading.current_thread().name == 'old-enumeration':
                entered.set()
                release.wait(2)
                return [PROCESS]
            return [replacement]

        source.process_finder = process_finder
        slow = threading.Thread(target=lobby.observe, name='old-enumeration')
        slow.start()
        try:
            self.assertTrue(entered.wait(2))
            match.observe(); match.transition()
            release.set(); slow.join(2)
            lobby.chat('obsolete snapshot', '密语')
            match.chat('new snapshot')
            self.assertEqual(source.generation, initial + 1)
            self.assertEqual([chat.text for chat in self.chats(events)], ['new snapshot'])
        finally:
            release.set(); slow.join(2)

    def test_concurrent_workers_deliver_unique_message_ids(self):
        source, match, lobby, events = self.source()
        match.connect(); lobby.connect()
        threads = [threading.Thread(target=lambda: [match.chat('team') for _ in range(100)]),
                   threading.Thread(target=lambda: [lobby.chat('private', '密语') for _ in range(100)])]
        for thread in threads: thread.start()
        for thread in threads: thread.join(2)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(len(self.chats(events)), 200)
        self.assertEqual(len({chat.message_id for chat in self.chats(events)}), 200)

    def test_legacy_match_worker_uses_its_own_memory_and_filters_native_owned_channels(self):
        from test_history import chat_line
        from test_source_initial_recovery import WaitingGame

        game = WaitingGame()
        baseline = chat_line('baseline')
        game.set_nodes([baseline])
        source, match, lobby, events = self.source(
            match_factory=ChatSource, game_factory=lambda pid: game)

        def wait_for(predicate):
            deadline = time.monotonic() + 3
            while not predicate() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(predicate())

        wait_for(lambda: any(event.kind == 'status' and '已连接' in str(event.payload)
                            for event in events))
        self.assertEqual([chat.text for chat in self.chats(events)], ['baseline'],
                         '首次接入也补收最近对局消息')
        lobby.connect(); lobby.chat('party live', '队伍')
        game.set_nodes([baseline, chat_line('team live'),
                        chat_line('party live').replace('[团队]', '[队伍]', 1)])
        wait_for(lambda: any(chat.text == 'team live' for chat in self.chats(events)))
        self.assertEqual([(chat.channel, chat.text) for chat in self.chats(events)],
                         [('团队', 'baseline'), ('队伍', 'party live'), ('团队', 'team live')])
        source.stop()
        self.assertGreaterEqual(game.closed, 1)
        self.assertFalse(match.running)

    def test_source_recovery_keeps_inflight_translation_and_pause_resume_messages(self):
        source, match, lobby, events = self.source()
        entered, release = threading.Event(), threading.Event()

        class Engine:
            provider_id = 'offline-test'
            config_version = '1'

            def translate(self, job):
                entered.set()
                release.wait(2)
                return TranslationResult(job.job_id, '译文', 'en', None, 0)

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            now = lambda: datetime(2026, 10, 20, tzinfo=timezone.utc)
            ledger = BudgetLedger(Path(directory) / 'usage.db', 100)
            ledger.confirm_baseline('main', '2026-10', 0, now())
            controller = ChatController(source, Engine(), ledger, 'main', now)
            try:
                source.stop(); controller.start_receiving()
                match.connect(); lobby.connect()
                lobby.chat('hello private', '密语')
                controller.drain_events()
                self.assertTrue(entered.wait(2))
                match.transition('等待对局聊天，自动恢复')
                controller.drain_events()
                release.set()
                with controller.condition:
                    self.assertTrue(controller.condition.wait_for(lambda: controller.idle, timeout=2))
                delivered = controller.drain_events()
                self.assertEqual(len([event for event in delivered if event.kind == 'result']), 1)
                controller.pause_translation(True)
                lobby.chat('while paused', '密语')
                paused_events = controller.drain_events()
                self.assertEqual([event.payload.text for event in paused_events if event.kind == 'chat'], ['while paused'])
                controller.pause_translation(False)
                match.chat('after resume')
                resumed_events = controller.drain_events()
                self.assertEqual([event.payload.text for event in resumed_events if event.kind == 'chat'], ['after resume'])
                self.assertEqual(len(controller.context.recent()), 3)
            finally:
                release.set()
                controller.close()


if __name__ == '__main__':
    unittest.main()
