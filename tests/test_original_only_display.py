"""无需翻译的原文保持静默；只使用离线引擎和屏幕外自有 Tk 窗口。"""
import tkinter as tk
from tkinter import font as tkfont
import unittest

from hots_chat.controller import ChatController
from hots_chat.layout import DisplayMetrics
from hots_chat.models import AppEvent, ChatEvent, TranslationResult
from hots_chat.ui.chat_feed import ChatFeed
from hots_chat.ui.overlay_window import OverlayWindow
from hots_chat.ui.theme import apply_theme
from hots_chat.ui.viewmodel import AppViewModel
from hots_chat.window_settings import WindowSettings


ORIGINAL_ONLY = ('集合', '中文 123，注意！😀', '123', '!? 😀',
                 '✌️ 1️⃣ 👩🏽\u200d💻', '[表情]', '')


class OfflineSource:
    def stop(self):
        pass


class OfflineEngine:
    provider_id = 'offline'
    config_version = '1'

    def __init__(self):
        self.calls = []

    def translate(self, job):
        self.calls.append(job)
        return TranslationResult(job.job_id, '集合支援', job.source, None, 0)

    def close(self):
        pass


class OfflineNative:
    def set_overlay_mode(self, window, editing):
        pass

    def show_no_activate(self, window):
        window.deiconify()


class ControllerFixture:
    def setUp(self):
        super().setUp()
        self.engine = OfflineEngine()
        self.controller = ChatController(OfflineSource(), self.engine, 'offline')
        self.addCleanup(self.controller.close)

    @staticmethod
    def chat(text, key='a'):
        return ChatEvent(key, 0, '团队', '队友', '1:23', text)

    def receive(self, text, key='a'):
        self.controller.on_chat(self.chat(text, key))
        with self.controller.condition:
            self.assertTrue(self.controller.condition.wait_for(lambda: self.controller.idle, 2))
        return self.controller.drain_events()

    @staticmethod
    def apply_events(events):
        vm = AppViewModel()
        for event in events:
            vm.apply(event)
        return vm


class OriginalOnlyControllerTests(ControllerFixture, unittest.TestCase):
    def test_chinese_numbers_symbols_and_emoji_have_a_distinct_silent_route(self):
        for text in ORIGINAL_ONLY:
            with self.subTest(text=text):
                events = self.receive(text)
                self.assertEqual([event.payload for event in events if event.kind == 'translation_state'],
                                 [{'message_id': 'a', 'state': 'original_only', 'reason': ''}])
                self.assertFalse(any(event.kind in ('error', 'result') for event in events))
        self.assertEqual(self.engine.calls, [])

    def test_original_only_route_precedes_configuration_and_pause_states(self):
        gates = ('cloud_configured', 'configuration_held', 'paused', 'automatic_paused')
        for gate in gates:
            for text in ORIGINAL_ONLY:
                with self.subTest(gate=gate, text=text):
                    self.controller.cloud_configured = True
                    self.controller.configuration_held = False
                    self.controller.paused = False
                    self.controller.automatic_paused = False
                    setattr(self.controller, gate, gate != 'cloud_configured')
                    vm = self.apply_events(self.receive(text))
                    row = vm.messages['a']
                    self.assertEqual((row.translation_state, row.translation_reason, row.status,
                                      row.translation_label), ('original_only', '', '', ''))
        self.assertEqual(self.engine.calls, [])

    def test_supported_mixed_text_still_translates_with_queue_and_active_states(self):
        for key, (text, source) in enumerate((('集合 BAD 😀', 'en'), ('가자 中文 2!', 'ko'))):
            with self.subTest(text=text):
                events = self.receive(text, str(key))
                self.assertEqual([event.payload['state'] for event in events
                                  if event.kind == 'translation_state'], ['queued', 'translating'])
                vm = self.apply_events(events)
                self.assertEqual(vm.messages[str(key)].engine_translation, '集合支援')
                self.assertEqual((self.engine.calls[-1].source, self.engine.calls[-1].text),
                                 (source, text))
        self.assertEqual(len(self.engine.calls), 2)

    def test_unsupported_letters_keep_a_specific_reason_without_cloud(self):
        for text in ('日本語かな', 'ありがとう', 'BAD schön', 'BAD scho\u0308n', '가자 あ', 'é'):
            with self.subTest(text=text):
                vm = self.apply_events(self.receive(text))
                row = vm.messages['a']
                self.assertEqual(row.original, text)
                self.assertEqual((row.translation_state, row.status), ('skipped', '不支持的语言'))
        self.assertEqual(self.engine.calls, [])

    def test_chat_event_alone_is_silent_before_the_route_event_is_drained(self):
        for text in ORIGINAL_ONLY:
            with self.subTest(text=text):
                vm = AppViewModel()
                vm.apply(AppEvent('chat', 0, self.chat(text)))
                row = vm.messages['a']
                self.assertEqual((row.translation_state, row.status, row.translation_label),
                                 ('original_only', '', ''))

    def test_explicit_original_only_state_does_not_fall_back_to_untranslated(self):
        vm = AppViewModel()
        vm.apply(AppEvent('chat', 0, self.chat('help')))
        vm.apply(AppEvent('translation_state', 0,
                          {'message_id': 'a', 'state': 'original_only', 'reason': ''}))
        self.assertEqual((vm.messages['a'].status, vm.messages['a'].translation_label), ('', ''))


class OriginalOnlyFeedTests(ControllerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.root = tk.Tk()
        self.root.geometry('640x480+10000+10000')
        self.addCleanup(self.root.destroy)
        settings = WindowSettings()
        apply_theme(self.root, settings)
        self.feed = ChatFeed(self.root, font=tkfont.Font(root=self.root, size=12), settings=settings)
        self.feed.pack(fill='both', expand=True)

    def render(self, vm):
        self.feed.sync(vm.messages, vm.generation)
        self.root.update_idletasks()
        return self.feed.body.get('1.0', 'end-1c')

    def test_original_only_rows_render_metadata_and_body_without_status_or_blank_line(self):
        vm = AppViewModel()
        expected = ''
        for key, text in enumerate(ORIGINAL_ONLY[:-1]):
            for event in self.receive(text, str(key)):
                vm.apply(event)
            expected += '队友   团队  ·  1:23\n' + text + '\n'
            self.assertEqual(self.render(vm), expected)
        self.assertEqual(self.feed.body.tag_ranges('status'), ())
        self.assertEqual(self.engine.calls, [])

    def test_empty_status_does_not_create_a_status_line(self):
        self.feed.insert('', 'end', iid='a', values=('123 😀', '', ''))
        self.root.update_idletasks()
        self.assertEqual(self.feed.body.get('1.0', 'end-1c'), '123 😀\n')
        self.assertEqual(self.feed.body.tag_ranges('status'), ())

    def test_english_queue_translating_and_error_status_remain_visible(self):
        vm = AppViewModel()
        vm.apply(AppEvent('chat', 0, self.chat('help 中文')))
        for state, label in (('queued', '排队中'), ('translating', '翻译中')):
            with self.subTest(state=state):
                vm.apply(AppEvent('translation_state', 0,
                                  {'message_id': 'a', 'state': state, 'reason': ''}))
                self.assertEqual(self.render(vm), '队友   团队  ·  1:23\nhelp 中文\n' + label + '\n')
        vm.apply(AppEvent('error', 0, {'message_id': 'a', 'error': '网络超时'}))
        self.assertEqual(self.render(vm), '队友   团队  ·  1:23\nhelp 中文\n网络超时\n')

    def test_translated_mixed_message_keeps_original_and_metadata(self):
        vm = self.apply_events(self.receive('가자 中文'))
        self.assertEqual(self.render(vm), '队友   团队  ·  1:23\n集合支援\n原文  가자 中文\n')


class OriginalOnlyOverlayTests(ControllerFixture, unittest.TestCase):
    def overlay(self, style):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        settings = WindowSettings(overlay_style=style, overlay_placement='custom',
                                  overlay_x=10400, overlay_y=10400, overlay_width=440)
        overlay = OverlayWindow(root, settings, clock=lambda: 10,
                                native=OfflineNative(),
                                metrics=DisplayMetrics((10000, 10000, 11920, 11000), 1))
        self.addCleanup(overlay.close)
        return root, overlay

    @staticmethod
    def visible_text(overlay):
        if overlay.settings.overlay_style == 'subtitle':
            return [overlay.subtitle_canvas.itemcget(item, 'text')
                    for item in overlay.subtitle_canvas.find_withtag('foreground')]
        return [row['translation'].cget('text') for row in overlay.rows
                if row['frame'].winfo_manager()]

    def test_panel_and_subtitle_show_original_once_without_translation_label_or_extra_colon(self):
        for style in ('panel', 'subtitle'):
            with self.subTest(style=style):
                root, overlay = self.overlay(style)
                vm = self.apply_events(self.receive('集合 2，注意！😀'))
                overlay.sync(vm.messages, 0)
                root.update_idletasks()
                self.assertEqual(self.visible_text(overlay), ['队友：集合 2，注意！😀'])

    def test_panel_and_subtitle_preserve_english_queue_and_failure_labels(self):
        for style in ('panel', 'subtitle'):
            with self.subTest(style=style):
                root, overlay = self.overlay(style)
                vm = AppViewModel()
                vm.apply(AppEvent('chat', 0, self.chat('help 中文')))
                for state, reason, label in (('queued', '', '排队中'), ('translating', '', '翻译中'),
                                             ('failed', '网络超时', '翻译失败')):
                    vm.apply(AppEvent('translation_state', 0,
                                      {'message_id': 'a', 'state': state, 'reason': reason}))
                    overlay.sync(vm.messages, 0)
                    root.update_idletasks()
                    self.assertEqual(self.visible_text(overlay), ['队友：' + label + '：help 中文'])


if __name__ == '__main__':
    unittest.main()
