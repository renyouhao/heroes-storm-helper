import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from test_history import FakeMemory,chat_line
from hots_chat.acquisition.source import ChatSource
from hots_chat.acquisition.memory import GameProcess
from hots_chat.acquisition.discovery import discover, DiscoveryCancelled
from hots_chat.acquisition.history import HistoryReader,PollResult


class AcquisitionTests(unittest.TestCase):
    def test_initial_baseline_change_recovers_without_reopening_and_backfills_once(self):
        class Game(FakeMemory):
            path=r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'
            closed=False
            def close(self):self.closed=True
        game=Game();baseline=chat_line('baseline');game.set_nodes([baseline])
        events=[];opened=[];polls=[0]
        class Scanner:
            finished=False
            def __init__(self,*a,**k):self.ignore_snapshots=k.get('ignore_snapshots',{})
            def step(self):
                if game.root in self.ignore_snapshots:self.finished=True;return None
                return SimpleNamespace(root=game.root)
        def factory(pid):opened.append(pid);return game
        original_poll=HistoryReader.poll
        def changing_poll(reader):
            polls[0]+=1
            if polls[0]==1:return PollResult([],False,True,16)
            return original_poll(reader)
        source=ChatSource(process_finder=lambda:[GameProcess(1,Game.path,98348)],game_factory=factory,clock=lambda:100.)
        with patch('hots_chat.acquisition.source.discover',return_value=SimpleNamespace(root=game.root)),patch('hots_chat.acquisition.source.IncrementalDiscovery',Scanner),patch.object(HistoryReader,'poll',changing_poll):
            source.start(events.append)
            try:
                self.wait(lambda:any(e.kind=='status' and str(e.payload).startswith('已连接') for e in events))
                self.wait(lambda:any(e.kind=='chat' and e.payload.text=='baseline' for e in events))
                self.assertEqual(opened,[1])
                self.assertFalse(game.closed)
                self.assertEqual([e.payload.text for e in events if e.kind=='chat'],['baseline'],'首次读取重试后应补收首批聊天且只交付一次')
                game.set_nodes([baseline,chat_line('new after baseline')])
                self.wait(lambda:any(e.kind=='chat' and e.payload.text=='new after baseline' for e in events))
                self.assertEqual([e.payload.text for e in events if e.kind=='chat'],['baseline','new after baseline'])
            finally:source.stop()

    def test_capture_started_before_game_automatically_connects_after_process_appears(self):
        class Game(FakeMemory):
            path=r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'
            def close(self):pass
        game=Game();baseline=chat_line('baseline');game.set_nodes([baseline])
        processes=[];events=[];opened=[]
        def factory(pid):opened.append(pid);return game
        source=ChatSource(process_finder=lambda:list(processes),game_factory=factory,clock=lambda:100.)
        with patch('hots_chat.acquisition.source.discover',return_value=SimpleNamespace(root=game.root)):
            source.start(events.append)
            try:
                self.wait(lambda:any(e.kind=='capture_state' and e.payload=='等待游戏' for e in events))
                self.assertFalse(opened)
                processes.append(GameProcess(1,Game.path,98348))
                self.wait(lambda:any(e.kind=='status' and str(e.payload).startswith('已连接') for e in events))
                self.wait(lambda:any(e.kind=='chat' and e.payload.text=='baseline' for e in events))
                self.assertEqual([e.payload.text for e in events if e.kind=='chat'],['baseline'],'游戏出现后首次接入应补收已有聊天')
                game.set_nodes([baseline,chat_line('new after startup')])
                self.wait(lambda:any(e.kind=='chat' and e.payload.text=='new after startup' for e in events))
                self.assertEqual(opened,[1])
                self.assertEqual([e.payload.text for e in events if e.kind=='chat'],['baseline','new after startup'])
            finally:source.stop()

    def test_disappearing_probe_candidate_does_not_disconnect_healthy_reader(self):
        class Game(FakeMemory):
            path=r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'
            def close(self):pass
        game=Game();baseline=chat_line('baseline');game.set_nodes([baseline]);events=[];probed=threading.Event()
        class Scanner:
            finished=False
            def __init__(self,*a,**k):self.calls=0;self.ignore_snapshots=k.get('ignore_snapshots',{})
            def step(self):
                self.calls+=1;probed.set()
                if self.calls==1:return SimpleNamespace(root=0x90000)
                self.finished=True;return None
        source=ChatSource(process_finder=lambda:[GameProcess(1,Game.path,98348)],game_factory=lambda p:game,clock=lambda:100.)
        with patch('hots_chat.acquisition.source.discover',return_value=SimpleNamespace(root=game.root)),patch('hots_chat.acquisition.source.IncrementalDiscovery',Scanner):
            source.start(events.append)
            try:
                self.assertTrue(probed.wait(2))
                time.sleep(.1)
                game.set_nodes([baseline,chat_line('still active')])
                self.wait(lambda:any(e.kind=='chat' and e.payload.text=='still active' for e in events))
                self.assertEqual(source.generation,1)
                self.assertEqual([e.payload.text for e in events if e.kind=='chat'],['baseline','still active'])
            finally:source.stop()

    def test_idle_probe_backfills_preexisting_roots_once_and_finds_new_match(self):
        def shifted(delta,body):
            memory=FakeMemory();memory.set_nodes([chat_line(body)])
            blocks={}
            import struct
            for address,raw in memory.blocks.items():
                count=2 if address==memory.root else 3
                values=list(struct.unpack('<'+'Q'*count,raw[:count*8]))
                values[0]+=delta;values[1]+=delta
                blocks[address+delta]=struct.pack('<'+'Q'*count,*values)+raw[count*8:]
            return memory.root+delta,blocks
        current,current_blocks=shifted(0,'current baseline')
        stale,stale_blocks=shifted(0x100000,'preexisting stale history')
        newer,new_blocks=shifted(0x200000,'first new match message')
        available=[False];clock=[100.];scanners=[];events=[]
        class Game:
            path=r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'
            def close(self):pass
            def read(self,address,count):
                blocks={**current_blocks,**stale_blocks,**(new_blocks if available[0] else {})}
                for base,data in blocks.items():
                    if base<=address<base+len(data):return data[address-base:address-base+count]
                return b''
        class Scanner:
            finished=False
            calls=0
            def __init__(self,*a,ignore_snapshots=None,**k):self.ignore_snapshots=ignore_snapshots;scanners.append(self)
            def step(self):
                self.calls+=1
                if stale not in self.ignore_snapshots:return SimpleNamespace(root=stale)
                if available[0] and newer not in self.ignore_snapshots:return SimpleNamespace(root=newer)
                self.finished=True;return None
        game=Game()
        source=ChatSource(process_finder=lambda:[GameProcess(1,Game.path,98348)],game_factory=lambda p:game,clock=lambda:clock[0])
        with patch('hots_chat.acquisition.source.discover',return_value=SimpleNamespace(root=current)),patch('hots_chat.acquisition.source.IncrementalDiscovery',Scanner):
            source.start(events.append)
            try:
                self.wait(lambda:bool(scanners) and scanners[0].finished)
                self.assertEqual([e.payload.text for e in events if e.kind=='chat'],['current baseline','preexisting stale history'],'首次盘点发现的未知聊天树也应补收一次')
                available[0]=True;clock[0]=140.
                self.wait(lambda:any(e.kind=='chat' and e.payload.text=='first new match message' for e in events))
                self.assertEqual([e.payload.text for e in events if e.kind=='chat'],['current baseline','preexisting stale history','first new match message'])
            finally:source.stop()

    def wait(self,predicate):
        end=time.monotonic()+3
        while not predicate() and time.monotonic()<end:time.sleep(.01)
        self.assertTrue(predicate())

    def test_same_process_new_chat_history_recovers_first_message_automatically(self):
        class NextMemory(FakeMemory):root=0x90000
        old,new=FakeMemory(),NextMemory()
        old.set_nodes([chat_line('old baseline')]);new.set_nodes([chat_line('first message in next match')])
        class Game:
            path=r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'
            current=old
            closed=False
            def read(self,*args):return self.current.read(*args)
            def close(self):self.closed=True
        game=Game();events=[];opened=[];scanners=[]
        class Scanner:
            finished=False
            def __init__(self,*args,**kwargs):
                self.ignore_snapshots=kwargs.get('ignore_snapshots',{});scanners.append(kwargs)
            def step(self):
                if game.current is old:self.finished=True;return None
                return SimpleNamespace(root=new.root)
        def factory(pid):opened.append(pid);return game
        source=ChatSource(process_finder=lambda:[GameProcess(1,Game.path,98348)],game_factory=factory,clock=lambda:100.)
        with patch('hots_chat.acquisition.source.discover',return_value=SimpleNamespace(root=old.root)),patch('hots_chat.acquisition.source.IncrementalDiscovery',Scanner,create=True):
            source.start(events.append)
            try:
                self.wait(lambda:any(e.kind=='status' and str(e.payload).startswith('已连接') for e in events))
                self.wait(lambda:any(e.kind=='chat' and e.payload.text=='old baseline' for e in events))
                game.current=new
                self.wait(lambda:any(e.kind=='chat' and e.payload.text=='first message in next match' for e in events))
                chats=[e.payload.text for e in events if e.kind=='chat']
                self.assertEqual(chats,['old baseline','first message in next match'])
                self.assertEqual(opened,[1],'同一进程换聊天对象不应关闭进程句柄再等待60秒')
                self.assertFalse(game.closed)
                self.assertTrue(scanners)
            finally:source.stop()

    def test_reconnect_to_same_root_uses_cursor_without_replaying_seen_messages(self):
        class Game(FakeMemory):
            path=r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'
            def close(self):pass
        game=Game();baseline=chat_line('baseline');game.set_nodes([baseline]);events=[]
        class Scanner:
            finished=False
            def __init__(self,*a,**k):self.ignore_snapshots=k.get('ignore_snapshots',{})
            def step(self):
                if game.root in self.ignore_snapshots:self.finished=True;return None
                return SimpleNamespace(root=game.root)
        source=ChatSource(process_finder=lambda:[GameProcess(1,Game.path,98348)],game_factory=lambda p:game,clock=lambda:100.)
        with patch('hots_chat.acquisition.source.discover',return_value=SimpleNamespace(root=game.root)),patch('hots_chat.acquisition.source.IncrementalDiscovery',Scanner,create=True):
            source.start(events.append)
            try:
                self.wait(lambda:any(e.kind=='status' and str(e.payload).startswith('已连接') for e in events))
                self.wait(lambda:any(e.kind=='chat' and e.payload.text=='baseline' for e in events))
                game.set_nodes([baseline,chat_line('seen')])
                self.wait(lambda:any(e.kind=='chat' and e.payload.text=='seen' for e in events))
                self.assertEqual([e.payload.text for e in events if e.kind=='chat'],['baseline','seen'])
                source.reconnect()
                self.wait(lambda:len([e for e in events if e.kind=='status' and str(e.payload).startswith('已连接')])==2)
                game.set_nodes([baseline,chat_line('seen'),chat_line('new after reconnect')])
                self.wait(lambda:any(e.kind=='chat' and e.payload.text=='new after reconnect' for e in events))
                self.assertEqual([e.payload.text for e in events if e.kind=='chat'],['baseline','seen','new after reconnect'])
            finally:source.stop()

    def test_cleared_root_reuse_can_deliver_identical_first_message_in_next_match(self):
        class Game(FakeMemory):
            path=r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'
            def close(self):pass
        game=Game();line=chat_line('same first message');game.set_nodes([line]);events=[]
        class Scanner:
            finished=False
            def __init__(self,*a,**k):self.ignore_snapshots=k.get('ignore_snapshots',{})
            def step(self):
                if game.root in self.ignore_snapshots:self.finished=True;return None
                return SimpleNamespace(root=game.root)
        source=ChatSource(process_finder=lambda:[GameProcess(1,Game.path,98348)],game_factory=lambda p:game,clock=lambda:100.)
        with patch('hots_chat.acquisition.source.discover',return_value=SimpleNamespace(root=game.root)),patch('hots_chat.acquisition.source.IncrementalDiscovery',Scanner):
            source.start(events.append)
            try:
                self.wait(lambda:any(e.kind=='status' and str(e.payload).startswith('已连接') for e in events))
                self.wait(lambda:len([e for e in events if e.kind=='chat'])==1)
                game.set_nodes([])
                self.wait(lambda:len([e for e in events if e.kind=='generation'])>=2)
                game.set_nodes([line])
                self.wait(lambda:len([e for e in events if e.kind=='chat'])==2)
                chats=[e.payload for e in events if e.kind=='chat']
                self.assertEqual([e.text for e in chats],['same first message','same first message'])
                self.assertGreater(chats[1].generation,chats[0].generation)
            finally:source.stop()

    def test_restart_discards_old_pid_cursor_and_backfills_new_process(self):
        class Game(FakeMemory):
            path=r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'
            closed=False
            def close(self):self.closed=True
        first,second=Game(),Game()
        first.set_nodes([chat_line('old history')]);second.set_nodes([chat_line('new baseline')])
        pid=[1];clock=[100.];events=[]
        source=ChatSource(process_finder=lambda:[GameProcess(pid[0],first.path,98348)],game_factory=lambda p:first if p==1 else second,clock=lambda:clock[0])
        with patch('hots_chat.acquisition.source.discover',return_value=SimpleNamespace(root=first.root)):
            source.start(events.append)
            try:
                self.wait(lambda:any(e.kind=='status' and str(e.payload).startswith('已连接') for e in events))
                self.wait(lambda:any(e.kind=='chat' and e.payload.text=='old history' for e in events))
                self.assertEqual([e.payload.text for e in events if e.kind=='chat'],['old history'])
                first.set_nodes([chat_line('old history'),chat_line('first new')])
                self.wait(lambda:any(e.kind=='chat' and e.payload.text=='first new' for e in events))
                pid[0]=2;clock[0]=101
                self.wait(lambda:len([e for e in events if e.kind=='status' and str(e.payload).startswith('已连接')])==2)
                self.wait(lambda:any(e.kind=='chat' and e.payload.text=='new baseline' for e in events))
                self.assertTrue(first.closed)
                second.set_nodes([chat_line('new baseline'),chat_line('second new')])
                self.wait(lambda:any(e.kind=='chat' and e.payload.text=='second new' for e in events))
                chats=[e.payload for e in events if e.kind=='chat']
                self.assertEqual([e.text for e in chats],['old history','first new','new baseline','second new'])
                self.assertGreater(chats[2].generation,chats[1].generation)
            finally:source.stop()
        self.assertTrue(second.closed)

    def test_selected_pid_expiry_returns_to_discovery(self):
        class Game(FakeMemory):
            path=r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'
            def close(self):pass
        game=Game();game.set_nodes([chat_line('baseline')]);pid=[1];clock=[0.];events=[]
        source=ChatSource(selected_pid=1,process_finder=lambda:[GameProcess(pid[0],Game.path,98348)],game_factory=lambda p:game,clock=lambda:clock[0])
        with patch('hots_chat.acquisition.source.discover',return_value=SimpleNamespace(root=game.root)):
            source.start(events.append)
            try:
                self.wait(lambda:any(e.kind=='status' and str(e.payload).startswith('已连接') for e in events))
                pid[0]=2;clock[0]=61
                self.wait(lambda:len([e for e in events if e.kind=='status' and str(e.payload).startswith('已连接')])>=2)
                self.assertIsNone(source.selected_pid)
            finally:source.stop()

    def test_discovery_retry_at_least_60_seconds(self):
        class Game:
            path=r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'
            def close(self):pass
        clock=[0.];source=ChatSource(process_finder=lambda:[GameProcess(1,Game.path,98348)],game_factory=lambda p:Game(),clock=lambda:clock[0])
        from hots_chat.acquisition.history import HistoryInvalid
        with patch('hots_chat.acquisition.source.discover',side_effect=HistoryInvalid('fake')) as discovery:
            source.start(lambda e:None)
            try:
                self.wait(lambda:discovery.call_count==1);time.sleep(.6)
                self.assertEqual(discovery.call_count,1)
                clock[0]=60;self.wait(lambda:discovery.call_count==2)
            finally:source.stop()
    def test_cancel_discovery(self):
        class Memory:
            def regions(self):
                yield 0x10000, 1024, 4
            def read(self, *args):
                raise AssertionError('cancelled discovery read memory')
        cancel = threading.Event(); cancel.set()
        with self.assertRaises(DiscoveryCancelled):
            discover(Memory(), cancel)

    def test_unsupported_build_never_scans(self):
        called = []
        source = ChatSource(process_finder=lambda: [GameProcess(1, 'fake', 1)], game_factory=lambda pid: called.append(pid))
        events = []
        source.start(events.append)
        time.sleep(.05)
        source.stop()
        self.assertFalse(called)
        self.assertTrue(any('版本' in str(e.payload) for e in events))

    def test_cancel_discovery_releases_handle(self):
        entered = threading.Event()
        class Memory:
            path = r'D:\game\Base98348\HeroesOfTheStorm_x64.exe'
            closed = False
            def close(self): self.closed = True
            def regions(self):
                entered.set()
                for i in range(10000): yield 0x10000 + i * 1048576, 1048576, 4
            def read(self, a, n): return bytes(n)
        m = Memory()
        source = ChatSource(process_finder=lambda: [GameProcess(1,m.path,98348)], game_factory=lambda p:m)
        source.start(lambda e:None)
        self.assertTrue(entered.wait(1))
        source.stop()
        self.assertTrue(m.closed)
        self.assertFalse(source.running)
