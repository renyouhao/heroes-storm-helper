"""游戏图片表情在统一入口规范；只使用合成内存和离线翻译引擎。"""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
import unittest

from hots_chat.acquisition.lobby_native import LobbyReader
from hots_chat.acquisition.lobby_source import LobbySource
from hots_chat.acquisition.memory import GameProcess
from hots_chat.acquisition.persistent import PersistentChatSource
from hots_chat.controller import ChatController
from hots_chat.models import ChatEvent, TranslationResult
from hots_chat.translation.base import TranslationError
from hots_chat.ui.viewmodel import AppViewModel
from hots_chat.usage import BudgetLedger
from test_history import chat_line
from test_lobby_native import FixtureMetadata
from test_persistent_flow import IndependentGameHandle, SharedGameMemory


IMAGE = '<img path="Assets\\Textures\\emoticon-happy.dds" width="20" height="20"/>'


class OfflineSource:
    def start(self, emit):
        self.emit = emit

    def stop(self):
        pass


class RecordingEngine:
    provider_id = 'offline-emote-test'
    config_version = '1'

    def __init__(self):
        self.calls = []
        self.fail_next = False

    def translate(self, job):
        self.calls.append(job)
        if self.fail_next:
            self.fail_next = False
            raise TranslationError('timeout', '合成网络超时')
        return TranslationResult(job.job_id, '合成译文', job.source, None, 0)

    def close(self):
        pass


class ChatEmoteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.now = datetime(2026, 10, 22, tzinfo=timezone.utc)
        self.ledger = BudgetLedger(Path(self.temporary.name) / 'usage.db')
        self.ledger.confirm_baseline('main', '2026-10', 0, self.now)
        self.engine = RecordingEngine()
        self.controller = ChatController(OfflineSource(), self.engine, self.ledger, 'main', lambda: self.now)
        self.addCleanup(self.controller.close)

    def wait_for_translation(self):
        with self.controller.condition:
            self.assertTrue(self.controller.condition.wait_for(lambda: self.controller.idle, timeout=3))

    def chat(self, text, identifier='message', channel='团队'):
        return ChatEvent(identifier, self.controller.generation, channel, '合成用户', '01:02', text)

    def test_four_channels_of_image_only_chat_never_reserve_or_translate(self):
        for channel in ('团队', '所有玩家', '队伍', '密语'):
            self.controller.on_chat(self.chat(IMAGE + IMAGE, channel, channel))
        self.wait_for_translation()
        self.assertEqual([event.text for event in self.controller.messages.values()], ['[表情][表情]'] * 4)
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.ledger.snapshot('main', self.now).local_reserved, 0)

    def test_chinese_with_image_does_not_route_asset_path_as_english(self):
        self.controller.on_chat(self.chat('集合 ' + IMAGE))
        self.wait_for_translation()
        self.assertEqual(self.controller.messages['message'].text, '集合 [表情]')
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.ledger.snapshot('main', self.now).local_reserved, 0)

    def test_mixed_english_and_korean_keep_language_and_send_cleaned_text(self):
        self.controller.on_chat(self.chat('go ' + IMAGE + ' now', 'english'))
        self.controller.on_chat(self.chat('같이 ' + IMAGE + ' 가요', 'korean'))
        self.wait_for_translation()
        self.assertEqual([(job.text, job.source) for job in self.engine.calls],
                         [('go [表情] now', 'en'), ('같이 [表情] 가요', 'ko')])
        self.assertEqual(self.ledger.snapshot('main', self.now).local_reserved, 21)

    def test_original_context_ui_and_retry_share_cleaned_frozen_event(self):
        self.engine.fail_next = True
        original = ChatEvent('message', 0, '密语', '<img path="sender.dds"/>合成用户', '01:02',
                             'go ' + IMAGE + ' now')
        self.controller.on_chat(original)
        self.wait_for_translation()
        self.controller.retry_message('message')
        self.wait_for_translation()
        stored = self.controller.messages['message']
        self.assertEqual(stored, ChatEvent('message', 0, '密语', '<img path="sender.dds"/>合成用户',
                                          '01:02', 'go [表情] now'))
        self.assertEqual(original.text, 'go ' + IMAGE + ' now')
        self.assertIs(self.controller.context.recent()[0], stored)
        view = AppViewModel()
        events = self.controller.drain_events()
        for event in events:
            view.apply(event)
        self.assertIs(view.messages['message'].event, stored)
        self.assertEqual(view.messages['message'].original, 'go [表情] now')
        self.assertEqual([job.text for job in self.engine.calls], ['go [表情] now'] * 2)
        self.assertEqual([event.payload['original'] for event in events if event.kind == 'result'],
                         ['go [表情] now'])
        self.assertEqual(self.ledger.snapshot('main', self.now).local_reserved, 22)

    def test_image_only_chat_cannot_be_retried_as_asset_path_text(self):
        self.controller.on_chat(self.chat(IMAGE))
        self.wait_for_translation()
        with self.assertRaisesRegex(ValueError, '仅支持英文和韩文'):
            self.controller.retry_message('message')
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.ledger.snapshot('main', self.now).local_reserved, 0)

    def test_valid_game_images_handle_case_quotes_and_adjacent_tags(self):
        self.controller.pause_translation(True)
        cases = [
            ('a<IMG PATH="assets/face.dds">b', 'a[表情]b'),
            ("a<img src='assets/face.dds'/>b", 'a[表情]b'),
            ('<img path="assets/a>b.dds" width="20"/> tail', '[表情] tail'),
            ("<img alt='a>b' SRC=face.dds />", '[表情]'),
            ('<img\tpath = "face.dds"\n/>', '[表情]'),
            ('😀' + IMAGE + '❤️', '😀[表情]❤️'),
            ('<keep this literal> &amp; ' + IMAGE, '<keep this literal> &amp; [表情]'),
        ]
        for index, (text, expected) in enumerate(cases):
            with self.subTest(text=text):
                self.controller.on_chat(self.chat(text, str(index)))
                self.assertEqual(self.controller.messages[str(index)].text, expected)

    def test_non_image_literals_and_malformed_tags_keep_original_event(self):
        self.controller.pause_translation(True)
        cases = [
            'plain English and 中文 without markup',
            'hello <keep this literal> &amp; &lt;b&gt;',
            '😀❤️🧑🏽‍💻',
            '<img is cool>',
            '<img>',
            '<img alt="src=face.dds">',
            '<image path="face.dds"/>',
            '</img path="face.dds">',
            '<imgpath="face.dds">',
            '<img path="">',
            '<img path="face.dds" broken>',
            '<img path="face.dds>',
            '<img path="face.dds"',
            '&lt;img path="face.dds"/&gt;',
        ]
        for index, text in enumerate(cases):
            with self.subTest(text=text):
                original = self.chat(text, str(index))
                self.controller.on_chat(original)
                self.assertIs(self.controller.messages[str(index)], original)

    def test_original_byte_limit_cannot_be_bypassed_by_shrinking_image_tag(self):
        self.controller.on_chat(self.chat('go <img path="' + '图' * 1400 + '.dds"/>'))
        self.wait_for_translation()
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.ledger.snapshot('main', self.now).local_reserved, 0)
        self.assertTrue(any(event.kind == 'error' and '过长' in event.payload['error']
                            for event in self.controller.drain_events()))

    def test_retry_rejects_overlong_original_image_body_without_reserving_or_sending(self):
        original = self.chat('go <img path="' + '图' * 1400 + '.dds"/>')
        self.controller.on_chat(original)
        self.wait_for_translation()
        with self.assertRaisesRegex(ValueError, '^消息过长，仅显示原文。$'):
            self.controller.retry_message(original.message_id)
        self.wait_for_translation()
        self.assertIs(self.controller.messages[original.message_id], original)
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.ledger.snapshot('main', self.now).local_reserved, 0)

    def test_retry_accepts_text_at_the_original_byte_limit(self):
        self.controller.pause_translation(True)
        self.controller.on_chat(self.chat('x' * 4096))
        self.controller.pause_translation(False)
        self.controller.retry_message('message')
        self.wait_for_translation()
        self.assertEqual([job.text for job in self.engine.calls], ['x' * 4096])
        self.assertEqual(self.ledger.snapshot('main', self.now).local_reserved, 4096)

    def test_overlong_and_malformed_text_processing_stays_bounded(self):
        self.controller.pause_translation(True)
        overlong = '<img path="face.dds"/>' * 100000
        original = self.chat(overlong, 'overlong')
        started = time.monotonic()
        self.controller.on_chat(original)
        for index in range(20):
            malformed = '<img ' * 800
            self.controller.on_chat(self.chat(malformed, str(index)))
        self.assertIs(self.controller.messages['overlong'], original)
        self.assertLess(time.monotonic() - started, 2, '对原始超长正文直接保留，畸形标签须有界处理')

    def test_manual_translation_retains_explicit_user_input(self):
        self.controller.submit_manual('集合 ' + IMAGE, 'en')
        self.wait_for_translation()
        self.assertEqual([job.text for job in self.engine.calls], ['集合 ' + IMAGE])

    def test_real_lobby_and_match_sources_normalize_before_controller_consumers(self):
        backend = SharedGameMemory()
        baseline = [('队伍', 'old party baseline'), ('密语', 'old whisper baseline')]
        backend.set_lobby(baseline)
        match = [chat_line(IMAGE), chat_line(IMAGE).replace('[团队]', '[所有玩家]', 1)]
        backend.enter_match(match)

        def lobby_factory(**options):
            return LobbySource(
                **options, module_base_finder=lambda pid: backend.lobby.image_base,
                wall_clock=lambda: (134053920010000000 - 116444736000000000) / 10000000,
                reader_factory=lambda memory, root, image_base, **reader_options: LobbyReader(
                    memory, root, image_base, metadata_factory=FixtureMetadata, **reader_options))

        source = PersistentChatSource(
            process_finder=lambda: [GameProcess(42, IndependentGameHandle.path, 98348)],
            game_factory=lambda pid: IndependentGameHandle(backend, pid),
            clock=backend.now, lobby_factory=lobby_factory)
        self.controller.source = source
        self.controller.start_receiving()
        observed = []

        def drain_until(predicate):
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                observed.extend(self.controller.drain_events())
                if predicate():
                    return
                time.sleep(.01)
            self.fail('合成生产来源未及时送达聊天')

        drain_until(lambda: any(event.kind == 'status' and '已连接大厅' in str(event.payload)
                                 for event in observed) and len(self.controller.messages) == 2)
        lobby = baseline + [('队伍', IMAGE), ('密语', IMAGE)]
        backend.set_lobby(lobby)
        drain_until(lambda: len(self.controller.messages) == 4)
        self.wait_for_translation()
        self.assertEqual({event.channel: event.text for event in self.controller.messages.values()},
                         {'团队': '[表情]', '所有玩家': '[表情]', '队伍': '[表情]', '密语': '[表情]'})
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.ledger.snapshot('main', self.now).local_reserved, 0)

        backend.set_lobby(lobby + [('密语', 'go ' + IMAGE + ' now')])
        backend.update_match(match + [chat_line('같이 ' + IMAGE + ' 가요')])
        drain_until(lambda: len(self.controller.messages) == 6)
        self.wait_for_translation()
        self.assertEqual({(job.text, job.source) for job in self.engine.calls},
                         {('go [表情] now', 'en'), ('같이 [表情] 가요', 'ko')})
        self.assertEqual(self.ledger.snapshot('main', self.now).local_reserved, 21)


if __name__ == '__main__':
    unittest.main()
