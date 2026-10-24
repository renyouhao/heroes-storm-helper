"""诊断使用合成游戏/窗口，验证真实 ZIP 字段且不触碰实机。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import zipfile

from hots_chat.diagnostics import DiagnosticRecorder
from hots_chat.acquisition.memory import GameProcess
from tests import test_board_source as source_tests
from tests import test_board_service as service_tests
from tests import test_board_native as native_tests


class ReaderDiagnosticsTests(unittest.TestCase):
    setUp = source_tests.BoardReaderTests.setUp
    tearDown = source_tests.BoardReaderTests.tearDown
    poll = source_tests.BoardReaderTests.poll
    def test_diagnostics_distinguish_raw_cells_and_rejected_coordinates(self):
        self.reader.viewport_converter = lambda *_: None
        snapshot = self.poll()
        self.assertEqual(snapshot.hits, ())
        info = self.reader.last_diagnostic
        self.assertEqual(info['build'], 98348)
        self.assertEqual(info['raw_talent_count'], 1)
        self.assertEqual(info['raw_stat_count'], 0)
        self.assertEqual(info['coordinate_rejected'], 1)
        self.assertEqual(info['hit_count'], 0)
        self.assertGreater(info['render_width'], 0)
        self.assertGreater(info['render_height'], 0)
        self.assertEqual(info['reason'], '所有候选格的坐标转换均失败')

    def test_diagnostics_keep_identity_failure_and_clear_old_render_size(self):
        self.poll()
        self.binding = None
        self.assertEqual(self.poll().status, 'unknown')
        info = self.reader.last_diagnostic
        self.assertEqual(info['stage'], 'identity')
        self.assertEqual(info['reason'], '本地玩家身份尚未确认')
        self.assertEqual(info['build'], 98348)
        self.assertNotIn('render_width', info)

    def test_raw_exception_text_is_not_diagnostic_reason(self):
        def fail(*_):
            raise OSError(5, 'PRIVATE-PATH PRIVATE-NAME')
        self.reader._capture = fail
        self.poll()
        self.assertEqual(self.reader.last_diagnostic['error_type'], 'OSError')
        self.assertNotIn('PRIVATE', json.dumps(self.reader.last_diagnostic))

    def test_broadcast_preserves_native_identity_failure_detail(self):
        from hots_chat.acquisition.talent_source import TalentReader
        self.reader._reader = TalentReader(
            process_finder=lambda:[GameProcess(123, self.memory.path, 98348)],
            game_factory=lambda _:self.memory, module_base_finder=lambda _:self.memory.base,
            creation_token_finder=lambda _:self.memory.creation)
        self.memory.put(self.memory.base + 0x35c8cd9, b'\x00')
        self.poll()
        self.assertEqual(self.reader.last_diagnostic['identity_reason'], 'gameplay talent context is inactive')
        self.assertEqual(self.reader.last_diagnostic['stage'], 'identity')

    def test_actual_unsupported_build_is_available_before_memory_is_opened(self):
        self.reader._reader.process_finder = lambda:[GameProcess(123, self.memory.path, 98349)]
        self.assertEqual(self.poll().status, 'unknown')
        info = self.reader.last_diagnostic
        self.assertEqual(info['build'], 98349)
        self.assertEqual(info['expected_build'], 98348)
        self.assertEqual(info['stage'], 'connect')
        self.assertEqual(info['read_calls'], 0)
        self.assertNotIn('render_width', info)

    def test_invalid_window_does_not_reuse_previous_process_diagnostics(self):
        self.poll()
        self.reader.poll(123, 0, 10.)
        self.assertNotIn('build', self.reader.last_diagnostic)
        self.assertEqual(self.reader.last_diagnostic['reason'], '目标窗口或采样时间不正确')


class ViewportDiagnosticsTests(unittest.TestCase):
    setUp = native_tests.BoardNativeTests.setUp
    client_rect = staticmethod(native_tests.BoardNativeTests.client_rect)
    client_to_screen = staticmethod(native_tests.BoardNativeTests.client_to_screen)
    def test_2k_client_and_aspect_rejection_are_explained(self):
        def client_rect(hwnd, pointer):
            pointer._obj.left = pointer._obj.top = 0
            pointer._obj.right, pointer._obj.bottom = 2560, 1440
            return 1
        self.user.GetClientRect.implementation = client_rect
        self.assertIsNotNone(self.backend.viewport_converter(
            self.user.hwnd, (120., 90., 240., 180.), (1920., 1080.)))
        self.assertEqual(self.backend.last_viewport['client_width'], 2560)
        self.assertEqual(self.backend.last_viewport['coordinate_reason'], 'ok')
        self.assertIsNone(self.backend.viewport_converter(
            self.user.hwnd, (120., 90., 240., 180.), (1600., 1200.)))
        self.assertEqual(self.backend.last_viewport['coordinate_reason'], 'aspect_mismatch')
        self.assertEqual(self.backend.last_viewport['render_width'], 1600)


class ServiceDiagnosticsTests(unittest.TestCase):
    enable = service_tests.BoardServiceTests.enable
    click = service_tests.BoardServiceTests.click
    def setUp(self):
        service_tests.BoardServiceTests.setUp(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.recorder = DiagnosticRecorder(Path(self.tmp.name) / 'logs', enabled=True)
        self.addCleanup(self.recorder.close)
        self.helper.diagnostics = self.recorder

    def rows(self):
        target = self.recorder.export_report(Path(self.tmp.name) / 'report.zip')
        with zipfile.ZipFile(target) as archive:
            return [json.loads(line) for name in archive.namelist() if name.startswith('helper.log')
                    for line in archive.read(name).decode().splitlines()]

    def test_each_failed_click_keeps_read_reason_and_hit_result_in_zip(self):
        self.reader.last_diagnostic = dict(build=98349, expected_build=98348,
            stage='connect', outcome='unknown', reason='当前游戏版本尚未验证')
        self.reader.poll = lambda *_: SimpleNamespace(status='unknown', reason='PRIVATE-NAME',
            hits=(), match_token='', chat_active=None, menu_active=None)
        self.backend.inspect_viewport = lambda hwnd: dict(client_width=2560, client_height=1440)
        self.enable()
        self.click()
        self.assertTrue(service_tests.until(lambda: any(
            row['event'] == 'helper.click' for row in self.rows())))
        self.click()
        self.assertTrue(service_tests.until(lambda: len([
            row for row in self.rows() if row['event'] == 'helper.click']) >= 2))
        rows = self.rows()
        reads = [row for row in rows if row['event'] == 'helper.read']
        self.assertEqual(reads[-1]['build'], 98349)
        self.assertEqual(reads[-1]['reason'], '当前游戏版本尚未验证')
        self.assertEqual(reads[-1]['client_width'], 2560)
        self.assertEqual(reads[-1]['chat_state'], 'unknown')
        clicks = [row for row in rows if row['event'] == 'helper.click']
        self.assertEqual(clicks[-1]['outcome'], 'no_hit')
        self.assertEqual(clicks[-1]['candidate_count'], 0)
        self.assertNotIn('PRIVATE-NAME', json.dumps(rows))
        self.assertEqual(self.sent, [])

    def test_same_state_with_changed_reason_is_logged(self):
        self.helper._emit('cancelled', '当前没有可信的可播报项目。')
        self.helper._emit('cancelled', '游戏聊天框仍打开，请先按 Esc 关闭，再重新双击。')
        rows = [r for r in self.rows() if r.get('stage') == 'broadcast']
        self.assertEqual(len(rows), 2)
        self.assertIn('聊天框', rows[-1]['reason'])

    def test_recording_failure_does_not_stop_broadcast(self):
        class BrokenRecorder:
            def record(self, *args, **kwargs):
                raise RuntimeError('diagnostics failed')
        self.helper.diagnostics = BrokenRecorder()
        self.enable(); self.click(); self.click()
        self.assertTrue(service_tests.until(lambda: len(self.sent) == 1))

    def test_disabled_diagnostics_do_not_query_extra_window_geometry(self):
        self.recorder.set_enabled(False)
        calls = []
        self.backend.inspect_viewport = lambda hwnd: calls.append(hwnd) or {}
        self.enable(); self.click(); self.click()
        self.assertTrue(service_tests.until(lambda: len(self.sent) == 1))
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
