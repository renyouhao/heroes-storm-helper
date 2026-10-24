"""Diagnostics use synthetic game memory; never inspect a live process."""
import threading
import unittest
from types import SimpleNamespace

from hots_chat.acquisition.memory import GameProcess
from hots_chat.acquisition.talent_source import TalentReader
from hots_chat.talent_service import TalentHelper
from tests.test_talent_source import Binding, TalentMemory
from tests.test_talent_identity import IdentityMemory
from hots_chat.acquisition.talent_identity import resolve_local_identity


class TalentReadDiagnosticsTests(unittest.TestCase):
    def make_reader(self, **options):
        memory = TalentMemory()
        defaults = dict(binding_provider=lambda *args: Binding(),
                        process_finder=lambda: [GameProcess(123, memory.path, 98348)],
                        game_factory=lambda pid: memory,
                        module_base_finder=lambda pid: memory.base,
                        creation_token_finder=lambda game: game.creation)
        defaults.update(options)
        reader = TalentReader(**defaults)
        self.addCleanup(reader.close)
        return reader, memory

    def test_unsupported_build_is_reported_before_game_open(self):
        reader, _ = self.make_reader(process_finder=lambda: [GameProcess(123, 'private-path', 99999)])
        snapshot = reader.poll(123)
        self.assertEqual(snapshot.status, 'unknown')
        self.assertEqual(reader.last_diagnostic['build'], 99999)
        self.assertEqual(reader.last_diagnostic['expected_build'], 98348)
        self.assertEqual(reader.last_diagnostic['stage'], 'connect')
        self.assertEqual(reader.last_diagnostic['reason'], '当前游戏版本尚未验证')
        self.assertEqual(reader.last_diagnostic['read_calls'], 0)

    def test_identity_failure_and_cached_connect_have_current_context(self):
        reader, _ = self.make_reader()
        self.assertEqual(reader.poll(123).status, 'available')
        self.assertEqual(reader.last_diagnostic['outcome'], 'available')
        self.assertGreater(reader.last_diagnostic['read_bytes'], 0)
        reader.binding_provider = lambda *args: None
        reader.poll(123)
        self.assertEqual(reader.last_diagnostic['build'], 98348)
        self.assertEqual(reader.last_diagnostic['stage'], 'identity')
        self.assertEqual(reader.last_diagnostic['reason'], '本地玩家身份尚未确认')
        reader.process_finder = lambda: []
        reader.poll(123)
        self.assertNotIn('build', reader.last_diagnostic)
        self.assertEqual(reader.last_diagnostic['stage'], 'connect')

    def test_talent_failure_retains_stage_and_bounded_read_counts(self):
        reader, memory = self.make_reader()
        memory.put(memory.base + 0x35c8cd9, b'\x00')
        reader.poll(123)
        self.assertEqual(reader.last_diagnostic['stage'], 'talent')
        self.assertEqual(reader.last_diagnostic['reason'], '当前场景没有可用天赋状态')
        self.assertEqual(reader.last_diagnostic['read_calls'], reader.last_read_calls)

    def test_os_error_never_leaks_exception_text(self):
        failure = OSError('private-path user chat token')
        failure.winerror = 5
        def open_game(pid):
            raise failure
        reader, _ = self.make_reader(game_factory=open_game)
        snapshot = reader.poll(123)
        self.assertEqual(reader.last_diagnostic['error_type'], 'OSError')
        self.assertEqual(reader.last_diagnostic['winerror'], 5)
        self.assertEqual(reader.last_diagnostic['reason'], '游戏读取失败')
        self.assertNotIn('private-path', str(reader.last_diagnostic))
        self.assertNotIn('private-path', snapshot.reason)

    def test_direct_connect_clears_old_failure_and_reports_cached_build(self):
        reader, _ = self.make_reader()
        reader.poll(123)
        reader.last_diagnostic.update(reason='stale', error_type='OSError', winerror=5)
        reader._connect(123)
        self.assertEqual(reader.last_diagnostic['build'], 98348)
        self.assertEqual(reader.last_diagnostic['stage'], 'connect')
        self.assertNotIn('reason', reader.last_diagnostic)
        self.assertNotIn('winerror', reader.last_diagnostic)


class HelperReadDiagnosticsTests(unittest.TestCase):
    def make_helper(self, recorder):
        helper = TalentHelper(diagnostics=recorder)
        self.addCleanup(helper.close)
        return helper

    def test_signature_changes_and_diagnostics_reenable_emit_first_snapshot(self):
        events = []
        recorder = SimpleNamespace(enabled=True, record=lambda event, **fields: events.append((event, fields)))
        recorder.snapshot = lambda: {'enabled': recorder.enabled}
        helper = self.make_helper(recorder)
        reader = SimpleNamespace(last_diagnostic=dict(stage='identity', reason='本地玩家身份尚未确认', build=98348))
        snapshot = SimpleNamespace(status='unknown', reason='private text')
        helper._record_read(reader, snapshot)
        helper._record_read(reader, snapshot)
        self.assertEqual(len([e for e in events if e[0] == 'helper.read']), 1)
        recorder.enabled = False
        helper._record_read(reader, snapshot)
        recorder.enabled = True
        helper._record_read(reader, snapshot)
        records = [fields for event, fields in events if event == 'helper.read']
        self.assertEqual(len(records), 2)
        self.assertEqual(records[-1]['source'], 'talent')
        self.assertNotIn('private text', str(records))
        reader.last_diagnostic['reason'] = '玩家身份绑定不完整'
        helper._record_read(reader, snapshot)
        self.assertEqual(len([e for e in events if e[0] == 'helper.read']), 3)

    def test_throwing_recorder_cannot_break_reads(self):
        def record(event, **fields):
            if event == 'helper.read': raise RuntimeError('recorder failed')
        helper = self.make_helper(SimpleNamespace(record=record))
        helper._record_read(SimpleNamespace(last_diagnostic={}), SimpleNamespace(status='unknown'))

    def test_worker_records_actual_poll(self):
        observed = threading.Event()
        rows = []
        def record(event, **fields):
            if event == 'helper.read':
                rows.append(fields)
                observed.set()
        reader = SimpleNamespace(last_diagnostic=dict(stage='identity', reason='本地玩家身份尚未确认'),
                                 poll=lambda pid: SimpleNamespace(status='unknown'), close=lambda: None)
        helper = TalentHelper(diagnostics=SimpleNamespace(record=record), reader_factory=lambda: reader,
                              backend_factory=lambda: SimpleNamespace(foreground_target=lambda: SimpleNamespace(pid=123)),
                              session_factory=lambda backend: SimpleNamespace(configure=lambda config: None,
                                  stop=lambda: None, poll=lambda *args, **kwargs: SimpleNamespace(state='unknown', detail='', tier_id=None)))
        self.addCleanup(helper.close)
        helper.set_enabled(True)
        self.assertTrue(observed.wait(2))
        helper.set_enabled(False)
        self.assertEqual(rows[0]['outcome'], 'unknown')

    def test_worker_failure_logs_only_type_and_error_code(self):
        observed = threading.Event()
        rows = []
        def record(event, **fields):
            if event == 'helper.read':
                rows.append(fields)
                observed.set()
        failure = OSError('private path and chat text')
        failure.winerror = 5
        def backend():
            raise failure
        helper = TalentHelper(diagnostics=SimpleNamespace(record=record), backend_factory=backend)
        self.addCleanup(helper.close)
        helper.set_enabled(True)
        self.assertTrue(observed.wait(2))
        helper.thread.join(2)
        self.assertEqual(rows[0]['stage'], 'worker')
        self.assertEqual(rows[0]['error_type'], 'OSError')
        self.assertEqual(rows[0]['winerror'], 5)
        self.assertNotIn('private', str(rows))

    def test_poll_context_includes_generation_and_foreground(self):
        rows = []
        recorder = SimpleNamespace(record=lambda event, **fields: rows.append((event, fields)))
        helper = self.make_helper(recorder)
        helper._generation = 7
        reader = SimpleNamespace(last_diagnostic={})
        helper._record_read(reader, SimpleNamespace(status='unknown'), target=SimpleNamespace(pid=123))
        fields = [fields for event, fields in rows if event == 'helper.read'][-1]
        self.assertEqual(fields['generation'], 7)
        self.assertEqual(fields['foreground_state'], 'true')
        helper._record_read(reader, SimpleNamespace(status='unknown'), target=None)
        self.assertEqual(rows[-1][1]['foreground_state'], 'false')


class IdentityReadDiagnosticsTests(unittest.TestCase):
    def test_failures_report_fixed_identity_detail_and_keep_read_sequence(self):
        for failure, reason in (('guard', 'gameplay talent context is inactive'),
                                ('mode', 'unverified gameplay mode or local-player slot'),
                                ('short', 'incomplete identity read')):
            with self.subTest(failure=failure):
                game = IdentityMemory()
                if failure == 'guard': game.put(game.base + 0x35c8cd9, b'\x00')
                if failure == 'mode': game.header(flags=0x10)
                if failure == 'short': game.short_at = game.root
                self.assertIsNone(resolve_local_identity(game, game.base, game.token))
                original_calls = list(game.calls)
                game.calls.clear()
                diagnostic = {}
                self.assertIsNone(resolve_local_identity(game, game.base, game.token, diagnostic=diagnostic))
                self.assertEqual(diagnostic['identity_reason'], reason)
                self.assertEqual(original_calls, game.calls)

    def test_os_error_and_arbitrary_value_error_never_expose_raw_text(self):
        for failure in (OSError('private chat path 0x12345678'), ValueError('private chat path')):
            with self.subTest(error=type(failure).__name__):
                failure.winerror = 5
                def read(address, size): raise failure
                game = IdentityMemory()
                game.read = read
                diagnostic = {}
                self.assertIsNone(resolve_local_identity(game, game.base, game.token, diagnostic=diagnostic))
                self.assertEqual(diagnostic['identity_reason'], 'identity read failed')
                self.assertEqual(diagnostic['identity_error_type'], type(failure).__name__)
                self.assertEqual(diagnostic['winerror'], 5)
                self.assertNotIn('private', str(diagnostic))
                self.assertNotIn('0x12345678', str(diagnostic))

    def test_default_reader_merges_identity_detail_without_changing_main_reason(self):
        memory = TalentMemory()
        reader = TalentReader(process_finder=lambda: [GameProcess(123, memory.path, 98348)],
                              game_factory=lambda pid: memory,
                              module_base_finder=lambda pid: memory.base,
                              creation_token_finder=lambda game: game.creation)
        self.addCleanup(reader.close)
        memory.put(memory.base + 0x35c8cd9, b'\x00')
        snapshot = reader.poll(123)
        self.assertEqual(snapshot.reason, '本地玩家身份尚未确认')
        self.assertEqual(reader.last_diagnostic['identity_reason'], 'gameplay talent context is inactive')

    def test_default_reader_identity_short_read_is_specific(self):
        memory = TalentMemory()
        memory.short_at = memory.base + 0x35c8cd9
        reader = TalentReader(process_finder=lambda: [GameProcess(123, memory.path, 98348)],
                              game_factory=lambda pid: memory,
                              module_base_finder=lambda pid: memory.base,
                              creation_token_finder=lambda game: game.creation)
        self.addCleanup(reader.close)
        reader.poll(123)
        self.assertEqual(reader.last_diagnostic['identity_reason'], 'incomplete identity read')


if __name__ == '__main__':
    unittest.main()
