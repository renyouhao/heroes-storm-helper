"""冷启动空历史后的恢复时序；仅FakeMemory，不访问游戏或界面。"""
import threading
import time
import unittest
from unittest.mock import patch

from test_history import FakeMemory, chat_line
from hots_chat.acquisition.discovery import discover
from hots_chat.acquisition.history import HistoryInvalid
from hots_chat.acquisition.memory import GameProcess
from hots_chat.acquisition.source import ChatSource


class WaitingGame(FakeMemory):
    path = r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'

    def __init__(self):
        super().__init__()
        self.closed = 0
        self.region_scans = 0
        self.fail_reads = False

    def regions(self):
        self.region_scans += 1
        yield from super().regions()

    def read(self, address, count):
        if self.fail_reads:
            raise OSError('simulated unreadable game process')
        return super().read(address, count)

    def close(self):
        self.closed += 1


class SourceInitialRecoveryTests(unittest.TestCase):
    def wait_for(self, predicate, message, seconds=3):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(predicate(), message)

    def source_waiting_for_first_chat(self):
        game = WaitingGame()
        clock = [100.]
        events, opened = [], []
        processes = [GameProcess(1, game.path, 98348)]
        initial_failed = threading.Event()

        def initial_scan(memory, cancelled, **options):
            try:
                return discover(memory, cancelled, **options)
            except HistoryInvalid:
                initial_failed.set()
                raise

        def factory(pid):
            opened.append(pid)
            return game

        source = ChatSource(process_finder=lambda: list(processes),
                            game_factory=factory, clock=lambda: clock[0])
        scan_patch = patch('hots_chat.acquisition.source.discover', side_effect=initial_scan)
        scan_patch.start()
        self.addCleanup(scan_patch.stop)
        source.start(events.append)
        self.addCleanup(source.stop)
        self.wait_for(lambda: initial_failed.is_set() and
                      len([e for e in events if e.kind == 'capture_state']) >= 2,
                      '空历史初次扫描应失败并进入等待状态')
        return source, game, clock, events, opened, processes

    def test_chat_appearing_within_60_seconds_connects_without_reopening_and_backfills_once(self):
        source, game, clock, events, opened, processes = self.source_waiting_for_first_chat()
        self.assertEqual(game.closed, 0, '无聊天不是进程错误，应保留只读句柄继续发现')
        baseline = chat_line('first text before connection')
        game.set_nodes([baseline])
        clock[0] = 110.
        self.wait_for(lambda: any(e.kind == 'status' and str(e.payload).startswith('已连接')
                                 for e in events),
                      '首条聊天在60秒内出现时应快速接入，不能继续冷却到下一次全量扫描')
        self.assertEqual(opened, [1], '同一有效进程不能因空历史反复打开')
        self.wait_for(lambda: any(e.kind == 'chat' and e.payload.text == 'first text before connection'
                                 for e in events), '首次接入应补收到促成连接的首条聊天')
        self.assertEqual([e.payload.text for e in events if e.kind == 'chat'], ['first text before connection'])
        game.set_nodes([baseline, chat_line('new after connection')])
        self.wait_for(lambda: any(e.kind == 'chat' and e.payload.text == 'new after connection'
                                 for e in events), '连接后应收到新增消息')
        time.sleep(.6)
        self.assertEqual([e.payload.text for e in events if e.kind == 'chat'],
                         ['first text before connection', 'new after connection'])

    def test_process_exit_during_initial_wait_closes_handle_and_new_pid_connects(self):
        source, game, clock, events, opened, processes = self.source_waiting_for_first_chat()
        self.assertEqual(game.closed, 0, '空历史等待期间应保留有效句柄')
        processes.clear()
        self.wait_for(lambda: game.closed == 1, '进程消失必须释放等待中的只读句柄')
        self.wait_for(lambda: any(e.kind == 'capture_state' and e.payload == '等待游戏'
                                 for e in events), '进程消失后应回到等待游戏')
        # 模拟新进程使用同一fake对象；实际工厂允许返回新句柄。
        game.set_nodes([chat_line('replacement process baseline')])
        clock[0] = 111.
        processes.append(GameProcess(2, game.path, 98348))
        self.wait_for(lambda: any(e.kind == 'status' and str(e.payload).startswith('已连接')
                                 for e in events), '新PID不应继承旧PID的60秒冷却')
        self.assertEqual(opened, [1, 2])
        self.wait_for(lambda: any(e.kind == 'chat' and e.payload.text == 'replacement process baseline'
                                 for e in events), '新进程首次连接也应补收已有聊天')
        self.assertEqual([e.payload.text for e in events if e.kind == 'chat'], ['replacement process baseline'])

    def test_hard_read_error_closes_handle_and_later_attempt_can_connect(self):
        failed, replacement = WaitingGame(), WaitingGame()
        failed.fail_reads = True
        replacement.set_nodes([chat_line('replacement baseline')])
        opened, events = [], []
        clock = [100.]

        def factory(pid):
            opened.append(pid)
            return failed if len(opened) == 1 else replacement

        source = ChatSource(process_finder=lambda: [GameProcess(1, failed.path, 98348)],
                            game_factory=factory, clock=lambda: clock[0])
        source.start(events.append)
        self.addCleanup(source.stop)
        self.wait_for(lambda: failed.closed == 1, '真正读取失败必须释放失效句柄')
        clock[0] = 161.
        self.wait_for(lambda: any(e.kind == 'status' and str(e.payload).startswith('已连接')
                                 for e in events), '硬错误后的新句柄应能重新校准，不能卡死在空历史等待')
        self.assertEqual(len(opened), 2)
        self.wait_for(lambda: any(e.kind == 'chat' and e.payload.text == 'replacement baseline'
                                 for e in events), '硬错误重试成功后应补收尚未交付的聊天')
        self.assertEqual([e.payload.text for e in events if e.kind == 'chat'], ['replacement baseline'])

    def test_hard_error_during_initial_wait_releases_preserved_handle(self):
        source, game, clock, events, opened, processes = self.source_waiting_for_first_chat()
        self.assertEqual(game.closed, 0, '无聊天应保留句柄，后续硬错误才能与普通等待区分')
        game.fail_reads = True
        clock[0] = 110.
        self.wait_for(lambda: game.closed == 1, '等待期间发生真正OSError必须关闭失效句柄')
        self.assertEqual(opened, [1], '硬故障自动重试仍须退避，不能立即循环开句柄')

    def test_manual_reconnect_bypasses_hard_error_backoff_before_60_seconds(self):
        failed, replacement = WaitingGame(), WaitingGame()
        failed.fail_reads = True
        replacement.set_nodes([chat_line('manual reconnect baseline')])
        opened, events = [], []
        clock = [100.]

        def factory(pid):
            opened.append(pid)
            return failed if len(opened) == 1 else replacement

        source = ChatSource(process_finder=lambda: [GameProcess(1, failed.path, 98348)],
                            game_factory=factory, clock=lambda: clock[0])
        source.start(events.append)
        self.addCleanup(source.stop)
        self.wait_for(lambda: failed.closed == 1, '硬错误应已关闭第一个句柄')
        clock[0] = 101.
        source.reconnect()
        self.wait_for(lambda: any(e.kind == 'status' and str(e.payload).startswith('已连接')
                                 for e in events), '显式重新连接应立即尝试，不能仍被旧60秒退避拦住')
        self.assertEqual(len(opened), 2)
        self.wait_for(lambda: any(e.kind == 'chat' and e.payload.text == 'manual reconnect baseline'
                                 for e in events), '手动重连在没有已提交游标时应补收首批聊天')
        self.assertEqual([e.payload.text for e in events if e.kind == 'chat'], ['manual reconnect baseline'])

    def test_stop_during_initial_wait_releases_handle_and_never_reads_again(self):
        source, game, clock, events, opened, processes = self.source_waiting_for_first_chat()
        source.stop()
        self.assertFalse(source.running)
        self.assertGreaterEqual(game.closed, 1)
        reads_after_stop = len(game.calls)
        game.set_nodes([chat_line('must not be read after stop')])
        clock[0] = 170.
        time.sleep(.6)
        self.assertEqual(len(game.calls), reads_after_stop, '用户取消后不得再读内存')
        self.assertFalse(any(e.kind == 'chat' for e in events))


if __name__ == '__main__':
    unittest.main()
