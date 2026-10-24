"""征召选人阶段的已知显示历史结构；不猜测原生会话偏移，不访问游戏或云。"""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile

from hots_chat.acquisition.discovery import DiscoveryPending, IncrementalDiscovery, discover
from hots_chat.acquisition.history import HistoryReader
from hots_chat.acquisition.parsing import parse_display_chat
from hots_chat.acquisition.persistent import PersistentChatSource
from hots_chat.acquisition.source import ChatSource
from hots_chat.acquisition.memory import GameProcess
from hots_chat.channels import CHANNEL_PRESETS, DISPLAY_CHANNELS, SUPPORTED_CHANNELS
from hots_chat.controller import ChatController
from hots_chat.diagnostics import DiagnosticRecorder
from hots_chat.models import TranslationResult
from test_history import FakeMemory, chat_line
from test_lobby_recovery_latency import VirtualStop
from test_match_channel_discovery import ConcurrentHistories
from test_persistent_chat import ControlledWorker, PROCESS


def draft_line(body='ban healer please', *, timed=False, spacer=False):
    line = chat_line(body).replace('[团队]', '[征召团队]', 1)
    if not timed:
        line = line.split('<sp/>', 1)[1]
        if spacer:
            line = '<sp/>' + line
    return line


class DraftDisplayChatTests(unittest.TestCase):
    def test_draft_channel_parses_with_and_without_time_without_relabeling_team(self):
        for timed in (False, True):
            for spacer in (False, True):
                with self.subTest(timed=timed, spacer=spacer):
                    parsed = parse_display_chat(draft_line('hello &amp; <literal>', timed=timed, spacer=spacer))
                    self.assertIsNotNone(parsed)
                    self.assertEqual((parsed.channel, parsed.sender, parsed.text),
                                     ('征召团队', 'Hunter', 'hello & <literal>'))
                    self.assertEqual(parsed.time, '24:54' if timed else '')
        self.assertEqual(parse_display_chat(chat_line('normal team')).channel, '团队')

    def test_initial_and_incremental_discovery_accept_draft_history(self):
        for timed in (False, True):
            with self.subTest(timed=timed):
                memory = FakeMemory()
                memory.set_nodes([draft_line(timed=timed)])
                self.assertEqual(discover(memory, threading.Event(), channels=DISPLAY_CHANNELS).root,
                                 memory.root)
                candidate = IncrementalDiscovery(memory, threading.Event(), channels=DISPLAY_CHANNELS).step(time_budget=60)
                self.assertIsNotNone(candidate)
                self.assertEqual(candidate.root, memory.root)

    def test_unknown_draft_like_channel_and_system_body_cannot_become_draft_chat(self):
        hidden = '<c val="#ColorSystem">notice ' + draft_line('private embedded message')
        for line in (hidden, draft_line().replace('[征召团队]', '[征兆团队]', 1)):
            with self.subTest(line=line[:40]):
                self.assertIsNone(parse_display_chat(line))
                memory = FakeMemory()
                memory.set_nodes([line])
                with self.assertRaises(DiscoveryPending):
                    discover(memory, threading.Event(), channels=DISPLAY_CHANNELS)

    def test_unsupported_header_body_is_not_read_to_find_a_draft_label(self):
        memory = FakeMemory()
        system = '<c val="#ColorSystem">' + 'private' * 100 + draft_line('embedded')
        nodes = memory.set_nodes([draft_line('real draft'), system])
        result = HistoryReader(memory, memory.root, include_history=True, channels=DISPLAY_CHANNELS).poll()
        self.assertEqual([(row.channel, row.text) for row in result.messages], [('征召团队', 'real draft')])
        self.assertFalse(any(address == nodes[-1] + 24 and count > 96 for address, count in memory.calls))

    def test_draft_match_draft_transitions_deliver_each_message_once(self):
        game = ConcurrentHistories()
        game.set_history(0, [draft_line('draft first')])
        clock, events, changed = [0.], [], set()

        def advance(now):
            for at in (1, 2, 4, 6):
                if now < at or at in changed:
                    continue
                changed.add(at)
                if at == 1:
                    game.set_history(0, [draft_line('draft first'), draft_line('draft second')])
                elif at == 2:
                    game.set_history(0, [])
                    game.set_history(1, [chat_line('match first')])
                elif at == 4:
                    game.set_history(1, [chat_line('match first'), chat_line('match second')])
                else:
                    game.set_history(1, [])
                    game.set_history(2, [draft_line('next draft', spacer=True)])

        source = ChatSource(process_finder=lambda: [GameProcess(1, game.path, 98348)],
                            game_factory=lambda pid: game, clock=lambda: clock[0])
        source._stop = VirtualStop(clock, 10., advance)
        source._run(events.append)
        messages = [event.payload for event in events if event.kind == 'chat']
        self.assertEqual([(row.channel, row.text) for row in messages], [
            ('征召团队', 'draft first'), ('征召团队', 'draft second'),
            ('团队', 'match first'), ('团队', 'match second'), ('征召团队', 'next draft')])
        self.assertEqual(len({row.message_id for row in messages}), len(messages))

    def test_draft_is_in_team_presets_and_has_its_own_filter(self):
        self.assertIn('征召团队', SUPPORTED_CHANNELS)
        for name in ('全部聊天', '仅团队', '团队与所有玩家'):
            self.assertIn('征召团队', CHANNEL_PRESETS[name])
        self.assertEqual(CHANNEL_PRESETS['仅征召团队'], {'征召团队'})
        self.assertNotIn('征召团队', CHANNEL_PRESETS['仅队伍'])


class DraftTranslationEngine:
    provider_id = 'offline-draft-test'
    config_version = '1'

    def __init__(self):
        self.calls = []

    def translate(self, job):
        self.calls.append(job)
        return TranslationResult(job.job_id, '请禁用治疗英雄', 'en', None, 0)

    def close(self):
        pass


class DraftChatFlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.recorder = DiagnosticRecorder(self.base / 'logs', enabled=True)
        self.source = PersistentChatSource(process_finder=lambda: [PROCESS],
            match_factory=ControlledWorker, lobby_factory=ControlledWorker, diagnostics=self.recorder)
        self.engine = DraftTranslationEngine()
        self.controller = ChatController(self.source, self.engine, 'offline-account',
            clock=lambda: datetime(2026, 10, 24, tzinfo=timezone.utc), diagnostics=self.recorder)
        self.controller.start_receiving()
        self.source.match_source.connect()
        self.source.lobby_source.connect()
        self.controller.drain_events()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.controller.close()
        self.recorder.close()
        self.temporary.cleanup()

    def deliver(self, text, channel):
        self.source.match_source.chat(text, channel)
        self.controller.drain_events()
        with self.controller.condition:
            self.assertTrue(self.controller.condition.wait_for(lambda: self.controller.idle, timeout=3))
        return self.controller.drain_events()

    def export_rows(self):
        target = self.recorder.export_report(self.base / 'draft-chat.zip')
        with zipfile.ZipFile(target) as archive:
            return [json.loads(line) for name in archive.namelist() if name.startswith('helper.log')
                    for line in archive.read(name).decode('utf-8').splitlines()]

    def test_draft_message_reaches_translation_with_correlated_exported_evidence(self):
        results = [event.payload for event in self.deliver('ban healer please', '征召团队')
                   if event.kind == 'result']
        self.assertEqual([(row['original'], row['translated']) for row in results],
                         [('ban healer please', '请禁用治疗英雄')])
        rows = self.export_rows()
        received = next(row for row in rows if row.get('event') == 'message.received')
        self.assertEqual(received['channel'], '征召团队')
        identifier = received['message_id']
        merged = next(row for row in rows if row.get('stage') == 'merge' and row.get('outcome') == 'forwarded')
        self.assertEqual(merged['message_id'], identifier)
        for event in ('translation.submitted', 'translation.request', 'translation.result'):
            self.assertTrue(any(row.get('event') == event and row.get('message_id') == identifier for row in rows))
        self.assertEqual([job.text for job in self.engine.calls], ['ban healer please'])

    def test_draft_filter_rejects_regular_team_and_native_source_cannot_duplicate_draft(self):
        self.controller.set_channels(CHANNEL_PRESETS['仅征召团队'])
        self.source.lobby_source.chat('native should not own draft', '征召团队')
        self.deliver('normal team should be filtered', '团队')
        self.deliver('ban healer please', '征召团队')
        self.assertEqual([job.text for job in self.engine.calls], ['ban healer please'])
        rows = self.export_rows()
        self.assertTrue(any(row.get('source') == 'lobby' and row.get('stage') == 'merge'
                            and row.get('reason') == 'channel_filtered' for row in rows))
        self.assertTrue(any(row.get('stage') == 'controller' and row.get('reason') == 'channel_filtered'
                            for row in rows))


if __name__ == '__main__':
    unittest.main()
