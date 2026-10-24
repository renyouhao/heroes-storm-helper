"""原生征召入口的行为与导出证据；全部使用合成内存，不连接游戏或云。"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from hots_chat.acquisition.history import HistoryInvalid
from hots_chat.acquisition.lobby_metadata import LobbyMetadata
from hots_chat.acquisition.lobby_native import LobbyReader
from hots_chat.acquisition.lobby_source import LobbySource
from hots_chat.acquisition.memory import GameProcess
from hots_chat.acquisition.persistent import PersistentChatSource
from hots_chat.controller import ChatController
from hots_chat.diagnostics import DiagnosticRecorder
from hots_chat.models import TranslationResult
from test_lobby_metadata import MetadataMemory
from test_lobby_native import FixtureMetadata, LobbyMemory
from test_lobby_recovery_latency import FILETIME, VirtualStop
from test_persistent_chat import ControlledWorker, PROCESS


@dataclass(frozen=True)
class DraftSnapshot:
    identity: tuple = ('synthetic-room', 1)
    after_filetime: int = FILETIME


class FakeDraftContext:
    """只代替已验证的房间元数据边界，不模拟或猜测真实游戏偏移。"""
    def __init__(self, snapshot=None):
        self.current = snapshot
        self.last_reason = 'draft_team_verified' if snapshot is not None else 'not_draft_team'
        self.observations = 0
        self.verifications = 0
        self.valid = True

    def observe(self):
        self.observations += 1
        return self.current

    def verify(self, snapshot, memory=None):
        self.verifications += 1
        return self.valid and snapshot is not None and snapshot is self.current

    def diagnostic_snapshot(self):
        return {}


class DraftMemory(LobbyMemory):
    path = PROCESS.path

    def __init__(self):
        super().__init__()
        self.draft = self.base + 0x20400
        self.second_sender = self.base + 0x20500
        self.put(self.draft, struct.pack('<Q', self.layout.lobby_table))
        self.closed = 0
        self.on_body_read = None
        self.on_payload_read = None

    def records(self, entries):
        """条目为频道、正文、可选原生发送者、可选 FILETIME。"""
        root = super().records([(entry[0], entry[1]) for entry in entries])
        for index, entry in enumerate(entries):
            if entry[0] == 'system':
                continue
            node = self.links[index]
            if entry[0] == '征召团队':
                self.put(node + 16, struct.pack('<Q', self.draft))
            if len(entry) > 2 and entry[2] is not None:
                self.put(node + 24, struct.pack('<Q', entry[2]))
            filetime = entry[3] if len(entry) > 3 else FILETIME + (index + 1) * 10
            self.put(node + 48, struct.pack('<Q', filetime))
        return root

    def read(self, address, count):
        if address == self.image_base and count == 2:
            self.calls.append((address, count))
            return b'MZ'
        data = super().read(address, count)
        if self.on_payload_read is not None and count == 40 and address in (node + 16 for node in self.links):
            callback, self.on_payload_read = self.on_payload_read, None
            callback()
        if self.on_body_read is not None and self.base + 0x40000 <= address < self.base + 0x80000:
            callback, self.on_body_read = self.on_body_read, None
            callback()
        return data

    def close(self):
        self.closed += 1

    def body_reads(self, index):
        pointer = self.base + 0x40000 + index * 4096
        return [(address, count) for address, count in self.calls if address == pointer]


class DraftMetadata(FixtureMetadata):
    def __init__(self, memory, image_base, draft_context=None):
        super().__init__(memory, image_base)

    def resolve(self, conversation, sender):
        table = struct.unpack('<Q', self.memory.read(conversation, 8))[0]
        if table == self.layout.lobby_table:
            return '征召团队', '合成队友'
        return super().resolve(conversation, sender)


class NativeDraftReaderTests(unittest.TestCase):
    def reader(self, game, root, context, **options):
        options.setdefault('include_history', True)
        options.setdefault('after_filetime', FILETIME)
        return LobbyReader(game, root, game.image_base, metadata_factory=DraftMetadata,
                           draft_context=context, **options)

    def test_verified_native_draft_is_received_once_without_display_markup(self):
        game = DraftMemory()
        root = game.records([('征召团队', 'ban healer please')])
        context = FakeDraftContext(DraftSnapshot())
        reader = self.reader(game, root, context)
        self.assertEqual([(row.channel, row.sender, row.text) for row in reader.poll().messages],
                         [('征召团队', '合成队友', 'ban healer please')])
        self.assertEqual(reader.poll().messages, [])
        self.assertGreater(context.verifications, 0)
        self.assertEqual(reader.diagnostic_snapshot()['native_parsed_count'], 1)

    def test_raw_sender_is_part_of_record_identity_even_when_body_and_name_match(self):
        game = DraftMemory()
        first = ('征召团队', 'same synthetic body', game.sender, FILETIME + 10)
        root = game.records([first])
        reader = self.reader(game, root, FakeDraftContext(DraftSnapshot()))
        self.assertEqual(len(reader.poll().messages), 1)
        game.records([('征召团队', first[1], game.second_sender, first[3])])
        self.assertEqual([row.text for row in reader.poll().messages], [first[1]])
        self.assertEqual(reader.poll().messages, [])

    def test_no_current_context_ordinary_lobby_and_tournament_never_read_body(self):
        # ordinary/tour/未知状态由真正的上下文解析器收敛为 current=None。
        for reason in ('not_in_lobby', 'ordinary_lobby', 'tournament_draft', 'unknown_mode'):
            with self.subTest(reason=reason):
                game = DraftMemory()
                root = game.records([('征召团队', 'private unsupported draft body')])
                context = FakeDraftContext()
                context.last_reason = reason
                reader = self.reader(game, root, context)
                self.assertEqual(reader.poll().messages, [])
                self.assertEqual(game.body_reads(0), [])
                self.assertGreater(reader.diagnostic_snapshot()['native_draft_rejected_count'], 0)

    def test_unknown_conversation_never_reads_body_even_in_verified_draft_room(self):
        game = DraftMemory()
        root = game.records([('综合', 'private unknown channel body')])
        reader = self.reader(game, root, FakeDraftContext(DraftSnapshot()))
        self.assertEqual(reader.poll().messages, [])
        self.assertEqual(game.body_reads(0), [])

    def test_context_time_boundary_prevents_old_lobby_history_becoming_draft(self):
        game = DraftMemory()
        root = game.records([('征召团队', 'private old lobby history', None, FILETIME + 10),
                             ('征召团队', 'fresh draft message', None, FILETIME + 21)])
        context = FakeDraftContext(DraftSnapshot(after_filetime=FILETIME + 20))
        reader = self.reader(game, root, context)
        self.assertEqual([row.text for row in reader.poll().messages], ['fresh draft message'])
        self.assertEqual(game.body_reads(0), [])
        self.assertTrue(game.body_reads(1))

    def test_room_change_after_body_read_cannot_commit_old_context(self):
        game = DraftMemory()
        root = game.records([('征召团队', 'old room pending message')])
        context = FakeDraftContext(DraftSnapshot())
        reader = self.reader(game, root, context)
        game.on_body_read = lambda: setattr(context, 'current',
            DraftSnapshot(('synthetic-room', 2), FILETIME + 20))
        result = reader.poll()
        self.assertEqual(result.messages, [])
        self.assertTrue(result.retry)
        self.assertIsNone(reader.cursor, '上下文 fence 失败不能提交游标')
        game.records([('征召团队', 'old room pending message', None, FILETIME + 10),
                      ('征召团队', 'new room fresh message', None, FILETIME + 30)])
        self.assertEqual([row.text for row in reader.poll().messages], ['new room fresh message'])

    def test_context_verification_failure_before_parse_never_reads_body(self):
        game = DraftMemory()
        root = game.records([('征召团队', 'private changed room body')])
        context = FakeDraftContext(DraftSnapshot())
        context.valid = False
        self.assertEqual(self.reader(game, root, context).poll().messages, [])
        self.assertEqual(game.body_reads(0), [])

    def test_suspended_baseline_preserves_room_fence_across_poll_budgets(self):
        game = DraftMemory()
        entries = [('征召团队', 'first old room pending', None, FILETIME + 10),
                   ('征召团队', 'second old room pending', None, FILETIME + 20)]
        root = game.records(entries)
        context = FakeDraftContext(DraftSnapshot())
        reader = self.reader(game, root, context, max_nodes=1)
        self.assertTrue(reader.poll().retry)
        context.current = DraftSnapshot(('synthetic-room', 2), FILETIME + 25)
        for _ in range(2):
            self.assertEqual(reader.poll().messages, [])
        self.assertIsNone(reader.cursor)
        game.records(entries + [('征召团队', 'fresh room message', None, FILETIME + 30)])
        received = []
        for _ in range(8):
            result = reader.poll()
            received.extend(row.text for row in result.messages)
            if not result.retry:
                break
        self.assertEqual(received, ['fresh room message'])
        self.assertEqual(game.body_reads(1), [], '跨poll发现房间变化后不能继续读取旧消息正文')

    def test_discovery_context_fence_rejection_keeps_its_specific_reason(self):
        game = DraftMemory()
        root = game.records([('征召团队', 'synthetic discovery row')])
        context = FakeDraftContext(DraftSnapshot())
        reader = self.reader(game, root, context)
        inspection = reader.inspect_steps()
        next(inspection)
        context.current = DraftSnapshot(('synthetic-room', 2), FILETIME + 20)
        with self.assertRaises(StopIteration):
            next(inspection)
        self.assertEqual(reader.last_rejection_reason, 'native_draft_context_changed')

    def test_record_reuse_with_unchanged_link_cannot_mix_old_sender_and_new_body(self):
        game = DraftMemory()
        root = game.records([('征召团队', 'old draft message', game.sender, FILETIME + 10)])
        context = FakeDraftContext(DraftSnapshot())
        snapshot = context.current
        reader = self.reader(game, root, context)
        # Reader 已拿到旧payload；游戏随后复用同一地址和link。
        game.on_payload_read = lambda: game.records([
            ('征召团队', 'new draft message', game.second_sender, FILETIME + 20)])
        result = reader.poll()
        self.assertEqual(result.messages, [])
        self.assertTrue(result.retry)
        self.assertIsNone(reader.cursor)
        self.assertIs(context.current, snapshot, '记录变化不应销毁仍然可信的房间上下文')
        self.assertEqual([row.text for row in reader.poll().messages], ['new draft message'])
        self.assertEqual(reader.poll().messages, [])

    def test_unresolved_sender_keeps_verified_draft_text_with_unknown_name(self):
        class MissingSender(DraftMetadata):
            def resolve(self, conversation, sender):
                raise HistoryInvalid('synthetic sender expired')
        game = DraftMemory()
        root = game.records([('征召团队', 'still capture unknown sender')])
        reader = LobbyReader(game, root, game.image_base, metadata_factory=MissingSender,
            draft_context=FakeDraftContext(DraftSnapshot()), include_history=True,
            after_filetime=FILETIME)
        self.assertEqual([(row.channel, row.sender, row.text) for row in reader.poll().messages],
                         [('征召团队', '发送者未知', 'still capture unknown sender')])

    def test_native_draft_metadata_uses_verified_presence_identity(self):
        memory = MetadataMemory()
        conversation = memory.allocate(8)
        memory.put(conversation, '<Q', memory.base + 0x28e9540)
        metadata = LobbyMetadata(memory, memory.base,
                                 draft_context=FakeDraftContext(DraftSnapshot()))
        self.assertEqual(metadata.resolve(conversation, memory.self_sender), ('征召团队', '玩家甲'))
        self.assertEqual(metadata.resolve(conversation, memory.contact_sender), ('征召团队', '好友显示'))
        memory.put(memory.self_sender + 8, '<I', 404)
        metadata.begin_poll()
        with self.assertRaises(HistoryInvalid):
            metadata.resolve(conversation, memory.self_sender)


class DraftTranslationEngine:
    provider_id, config_version = 'offline-native-draft', '1'

    def __init__(self):
        self.calls = []

    def translate(self, job):
        self.calls.append(job)
        return TranslationResult(job.job_id, '请禁用治疗英雄', 'en', None, 0)

    def close(self):
        pass


class NativeDraftFlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def run_native(self, game, context, *, recorder=None, emit=None):
        clock, events = [0.], []
        source = LobbySource(process_finder=lambda: [GameProcess(PROCESS.pid, game.path, 98348)],
            game_factory=lambda pid: game, module_base_finder=lambda pid: game.image_base,
            clock=lambda: clock[0], wall_clock=lambda: (FILETIME - 116444736000000000) / 10000000,
            reader_factory=lambda memory, root, image_base, **options: LobbyReader(
                memory, root, image_base, metadata_factory=DraftMetadata, **options),
            draft_context_factory=lambda *args, **kwargs: context)
        source.diagnostics = recorder
        source._stop = VirtualStop(clock, 1., lambda now: None)
        def collect(event):
            events.append(event)
            if emit is not None:
                emit(event)
        with patch('hots_chat.acquisition.lobby_native.time.perf_counter', return_value=0):
            source._run(collect)
        return source, events

    def export_rows(self, recorder):
        target = recorder.export_report(self.base / 'native-draft.zip')
        with zipfile.ZipFile(target) as archive:
            return [json.loads(line) for name in archive.namelist() if name.startswith('helper.log')
                    for line in archive.read(name).decode('utf-8').splitlines() if line]

    def test_lobby_is_only_owner_of_draft_but_identical_native_messages_are_preserved(self):
        source = PersistentChatSource(process_finder=lambda: [PROCESS],
            match_factory=ControlledWorker, lobby_factory=ControlledWorker)
        self.addCleanup(source.stop)
        events = []
        source.start(events.append)
        source.match_source.connect()
        source.lobby_source.connect()
        source.match_source.chat('same body', '征召团队')
        source.lobby_source.chat('same body', '征召团队')
        source.lobby_source.chat('same body', '征召团队')
        source.match_source.chat('normal team', '团队')
        source.lobby_source.chat('normal team', '团队')
        chats = [event.payload for event in events if event.kind == 'chat']
        self.assertEqual([(row.channel, row.text) for row in chats],
                         [('征召团队', 'same body'), ('征召团队', 'same body'), ('团队', 'normal team')])
        self.assertEqual(len({row.message_id for row in chats}), 3)

    def test_native_read_to_translation_and_ui_ids_survive_real_diagnostic_zip(self):
        from hots_chat.ui.main_window import MainWindow
        from hots_chat.ui.viewmodel import AppViewModel
        recorder = DiagnosticRecorder(self.base / 'logs', enabled=True)
        self.addCleanup(recorder.close)
        recorder.register_secrets('synthetic-private-key')
        source = PersistentChatSource(process_finder=lambda: [PROCESS],
            match_factory=ControlledWorker, lobby_factory=ControlledWorker, diagnostics=recorder)
        engine = DraftTranslationEngine()
        controller = ChatController(source, engine, 'offline-account', diagnostics=recorder,
            clock=lambda: datetime(2026, 10, 24, tzinfo=timezone.utc))
        self.addCleanup(controller.close)
        controller.start_receiving()
        source.match_source.connect()
        source.lobby_source.connect()
        game = DraftMemory()
        game.records([('综合', 'private-unsupported-body synthetic-private-key'),
                      ('征召团队', 'ban healer please')])
        native, events = self.run_native(game, FakeDraftContext(DraftSnapshot()), recorder=recorder,
                                        emit=source.lobby_source.sinks[-1])
        self.assertEqual([event.payload.text for event in events if event.kind == 'chat'],
                         ['ban healer please'])
        self.assertEqual(game.body_reads(0), [])
        window = MainWindow.__new__(MainWindow)
        window.controller, window.vm = controller, AppViewModel()
        window.loot_helper = SimpleNamespace(drain_status=lambda: [])
        window._account_events = window._guard_helper_notice = window._sync_talent_gate = lambda: None
        window.screen_ticks = 0
        window.render_manual_preview = window.show_selected = lambda: None
        window.capture_state = window.status_var = Mock()
        window.tree = Mock()
        window.tree.get_children.return_value = ()
        window.tree.exists.return_value = False
        window._rendered_rows = {}
        window._pump_events()
        with controller.condition:
            self.assertTrue(controller.condition.wait_for(lambda: controller.idle, timeout=3))
        window._pump_events()
        self.assertEqual([job.text for job in engine.calls], ['ban healer please'])
        self.assertEqual(len(window.vm.messages), 1)
        identifier, view = next(iter(window.vm.messages.items()))
        self.assertEqual(view.engine_translation, '请禁用治疗英雄')
        rows = self.export_rows(recorder)
        received = next(row for row in rows if row.get('event') == 'message.received')
        self.assertEqual((received['message_id'], received['channel']), (identifier, '征召团队'))
        merged = next(row for row in rows if row.get('stage') == 'merge' and row.get('outcome') == 'forwarded')
        published = next(row for row in rows if row.get('stage') == 'publish' and row.get('outcome') == 'forwarded')
        self.assertEqual(merged['message_id'], identifier)
        self.assertEqual(published['source_message_id'], merged['source_message_id'])
        self.assertTrue(any(row.get('stage') == 'poll' and row.get('native_draft_count', 0) > 0
                            and row.get('native_parsed_count', 0) > 0 for row in rows))
        for event in ('translation.submitted', 'translation.request', 'translation.result'):
            self.assertTrue(any(row.get('event') == event and row.get('message_id') == identifier for row in rows), event)
        self.assertTrue(any(row.get('event') == 'ui.apply' and row.get('stage') == 'result'
                            and row.get('message_id') == identifier and row.get('outcome') == 'applied' for row in rows))
        self.assertEqual(native.diagnostic_snapshot()['native_lobby_schema'], 2)
        self.assertNotIn('synthetic-private-key', json.dumps(rows))
        self.assertNotIn('private-unsupported-body', json.dumps(rows))

    def test_diagnostic_switch_and_recorder_failure_do_not_change_native_reads(self):
        observations = []
        for mode in ('absent', 'disabled', 'enabled', 'failed'):
            game = DraftMemory()
            game.records([('征召团队', 'ban healer please')])
            recorder = None
            if mode in ('disabled', 'enabled'):
                recorder = DiagnosticRecorder(self.base / mode, enabled=mode == 'enabled')
                self.addCleanup(recorder.close)
            elif mode == 'failed':
                def fail(*args, **kwargs):
                    raise OSError('synthetic private logger error')
                recorder = SimpleNamespace(snapshot=lambda: {'enabled': True}, record=fail)
            context = FakeDraftContext(DraftSnapshot())
            _source, events = self.run_native(game, context, recorder=recorder)
            observations.append((list(game.calls), context.observations, context.verifications,
                [event.payload.text for event in events if event.kind == 'chat']))
        self.assertTrue(all(item == observations[0] for item in observations))
        self.assertEqual(observations[0][-1], ['ban healer please'])


if __name__ == '__main__':
    unittest.main()
