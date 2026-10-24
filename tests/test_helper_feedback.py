"""辅助反馈离线验证：只使用自建隐藏Tk控件，不读游戏或发送系统输入。"""
import importlib
import subprocess
import sys
import textwrap
import threading
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hots_chat.layout import DisplayMetrics
from hots_chat.window_settings import WindowSettings
from hots_chat.ui.talent_panel import TalentPanel
from hots_chat.ui.board_panel import BoardPanel


class OfflineNative:
    def __init__(self):
        self.styles = []
        self.shows = []

    def set_overlay_mode(self, window, editing=False):
        self.styles.append(editing)

    def show_no_activate(self, window):
        # 保持测试窗口隐藏；记录组件是否请求了不激活展示。
        self.shows.append(window)


class HelperResourceLifecycleTests(unittest.TestCase):
    def test_closed_components_do_not_defer_tk_cleanup_to_worker_gc(self):
        self.assert_worker_gc_safe((('talent', 'close'), ('talent', 'parent'),
                                   ('board', 'close'), ('board', 'parent'), ('notice', 'close')))

    def test_repeat_and_loot_panels_release_tk_resources_before_worker_gc(self):
        self.assert_worker_gc_safe((('repeat', 'close'), ('repeat', 'parent'),
                                   ('loot', 'close'), ('loot', 'parent')))

    def test_diagnostics_panel_releases_tk_resources_before_worker_gc(self):
        self.assert_worker_gc_safe((('diagnostics', 'destroy'), ('diagnostics', 'parent')))

    def assert_worker_gc_safe(self, cases):
        script = textwrap.dedent('''
            import gc
            from pathlib import Path
            import sys
            import tempfile
            import threading
            import tkinter as tk
            import weakref
            from types import SimpleNamespace
            from hots_chat.layout import DisplayMetrics
            from hots_chat.window_settings import WindowSettings
            from hots_chat.ui.talent_panel import TalentPanel
            from hots_chat.ui.board_panel import BoardPanel
            from hots_chat.ui.helper_notice import HelperNotice
            from hots_chat.ui.repeat_panel import RepeatPanel
            from hots_chat.ui.loot_panel import LootPanel
            from hots_chat.ui.diagnostics_panel import DiagnosticsPanel
            from hots_chat.diagnostics import DiagnosticRecorder

            def create_and_close():
                root = tk.Tk()
                root.withdraw()
                settings = WindowSettings(talent_enabled=True, board_broadcast_enabled=True)
                metrics = DisplayMetrics((10000, 10000, 11920, 11080), 1)
                native = SimpleNamespace(set_overlay_mode=lambda *args, **kwargs: None,
                                         show_no_activate=lambda *args: None)
                recorder = directory = None
                if sys.argv[1] == 'notice':
                    component = HelperNotice(root, settings, metrics=metrics, native=native)
                    assert component.show('talent', 'blocked', 'Please select manually')
                elif sys.argv[1] in ('repeat', 'loot'):
                    cls = RepeatPanel if sys.argv[1] == 'repeat' else LootPanel
                    component = cls(root, settings=settings, metrics=metrics, native=native,
                                    on_prepare=lambda: None, on_cancel=lambda: None)
                    component.update_status('running')
                elif sys.argv[1] == 'diagnostics':
                    directory = tempfile.TemporaryDirectory()
                    recorder = DiagnosticRecorder(Path(directory.name) / 'logs', enabled=True)
                    component = DiagnosticsPanel(root, recorder)
                    component.refresh_status()
                else:
                    cls = TalentPanel if sys.argv[1] == 'talent' else BoardPanel
                    component = cls(root, settings=settings, metrics=metrics,
                                    on_toggle=lambda value: None, on_retry=lambda: None)
                    component.set_running(True)
                    component.update_status('error', 'Offline error')
                references = weakref.ref(root), weakref.ref(component)
                if sys.argv[2] == 'close':
                    component.close()
                    component.close()
                elif sys.argv[2] == 'destroy':
                    component.destroy()
                root.destroy()
                if sys.argv[1] != 'diagnostics':
                    component.close()
                if sys.argv[1] != 'notice':
                    component.destroy()  # 父窗口递归销毁后再次调用也必须幂等。
                if recorder is not None:
                    recorder.close()
                    assert not recorder.thread.is_alive(), 'diagnostics writer did not stop'
                    directory.cleanup()
                return references

            gc.collect()
            references = create_and_close()
            worker = threading.Thread(target=gc.collect)
            worker.start()
            worker.join(10)
            assert not worker.is_alive(), 'worker gc did not finish'
            assert all(reference() is None for reference in references), 'closed resources retained'
        ''')
        for component, mode in cases:
            with self.subTest(component=component, mode=mode):
                result = subprocess.run([sys.executable, '-c', script, component, mode],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stderr, '')


class HelperPanelFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.settings = WindowSettings(talent_enabled=True, board_broadcast_enabled=True)
        self.metrics = DisplayMetrics((10000, 10000, 11920, 11080), 1)

    def panel(self, cls, retry=None):
        panel = cls(self.root, settings=self.settings, metrics=self.metrics,
                    on_toggle=lambda value: None, on_retry=retry)
        self.addCleanup(panel.close)
        return panel

    def test_saved_enabled_preference_survives_stop_and_retry_does_not_toggle(self):
        for cls in (TalentPanel, BoardPanel):
            with self.subTest(panel=cls.__name__):
                retries = []
                panel = self.panel(cls, lambda: retries.append(True))
                panel.set_running(False)
                self.assertTrue(panel.enabled.get())
                self.assertIn('运行已停止', panel.title.get())
                self.assertTrue(panel.retry_button.winfo_manager())
                panel.retry_button.invoke()
                self.assertEqual(retries, [True])
                panel.set_running(True)
                self.assertFalse(panel.retry_button.winfo_manager())
                panel.close()
                panel.retry_button.invoke()
                self.assertEqual(retries, [True])

    def test_status_errors_can_require_manual_action_without_claiming_worker_stopped(self):
        panel = self.panel(TalentPanel)
        panel.set_running(True)
        panel.update_status('blocked', '本层未选中，请手动选择。', tier_id=41)
        self.assertIn('手动', panel.title.get())
        self.assertNotIn('运行已停止', panel.title.get())
        panel.update_status('confirming', '等待回读。', tier_id=41)
        self.assertIn('确认', panel.title.get())
        self.assertNotIn('41', panel.detail.get(), '内部层标识不应作为天赋等级呈现')
        self.assertNotIn('20级', panel.detail.get())
        self.assertIn('手动', panel.last_result.get())

    def test_identical_runtime_and_status_do_not_update_tk_variables(self):
        for cls in (TalentPanel, BoardPanel):
            with self.subTest(panel=cls.__name__):
                panel = self.panel(cls)
                panel.set_running(True)
                panel.update_status('deferred', '等待其他输入操作结束。')
                writes = []
                panel.title.trace_add('write', lambda *args: writes.append('title'))
                panel.detail.trace_add('write', lambda *args: writes.append('detail'))
                for _ in range(20):
                    panel.set_running(True)
                    panel.update_status('deferred', '等待其他输入操作结束。')
                self.assertEqual(writes, [])


class HelperRuntimeFeedbackTests(unittest.TestCase):
    def test_board_exception_is_stopped_before_error_is_published(self):
        from hots_chat.board_service import BoardHelper
        running_at_error = []

        class Recorder:
            def record(self, event, **fields):
                if event == 'hotkey.state' and fields.get('outcome') == 'error':
                    running_at_error.append(helper.running)

        def fail_startup():
            raise OSError('离线连接失败')

        helper = BoardHelper(backend_factory=fail_startup, diagnostics=Recorder())
        self.addCleanup(helper.close)
        helper.set_enabled(True)
        helper.thread.join(2)
        self.assertEqual(running_at_error, [False],
                         '停止错误发布时，主窗必须已能观察到运行停止并显示重连入口')
        self.assertEqual(helper.drain_status()[-1].state, 'error')

    def test_talent_startup_failure_reports_stopped_and_single_retry_restarts(self):
        from hots_chat.talent_service import TalentHelper
        attempts = []
        entered = threading.Event()
        def backend():
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError('离线连接失败')
            return SimpleNamespace(foreground_target=lambda: None)
        class Reader:
            def __init__(self): entered.set()
            def close(self): pass
        helper = TalentHelper(backend_factory=backend, reader_factory=Reader)
        self.addCleanup(helper.close)
        helper.set_enabled(True)
        helper.thread.join(2)
        self.assertFalse(helper.running)
        self.assertIn('已停止', helper.drain_status()[-1].detail)
        self.assertTrue(helper.set_enabled(True))
        self.assertTrue(entered.wait(2))
        self.assertTrue(helper.running)
        helper.set_enabled(False)
        self.assertFalse(helper.running)

    def test_unexpected_board_hook_exit_reports_error_and_runtime_stopped(self):
        from hots_chat.board_service import BoardHelper
        backend = SimpleNamespace(double_click_settings=lambda: (500, (4, 4)),
            run=lambda *args: None, stop=lambda: None)
        helper = BoardHelper(backend_factory=lambda: backend,
            reader_factory=lambda native: SimpleNamespace(close=lambda: None),
            sender_factory=lambda native, reader: lambda *args, **kwargs: 'sent')
        self.addCleanup(helper.close)
        helper.set_enabled(True)
        helper.thread.join(2)
        self.assertFalse(helper.running)
        status = helper.drain_status()[-1]
        self.assertEqual(status.state, 'error')
        self.assertIn('重新连接', status.detail)
        self.assertNotIn('关闭后', status.detail)


class HelperNoticeTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.settings = WindowSettings(talent_enabled=True, board_broadcast_enabled=True)
        self.metrics = DisplayMetrics((10000, 10000, 11920, 11080), 1)
        self.native = OfflineNative()
        try:
            module = importlib.import_module('hots_chat.ui.helper_notice')
        except ImportError:
            self.fail('尚未提供游戏内关键结果的被动提示组件')
        self.notice = module.HelperNotice(self.root, self.settings,
                                         metrics=self.metrics, native=self.native)
        self.addCleanup(self.notice.close)

    def timers(self):
        return set(self.root.tk.call('after', 'info'))

    def test_disabled_and_successful_states_create_no_window_or_native_resources(self):
        children = tuple(self.root.winfo_children())
        for source, state in (('talent', 'confirmed'), ('board', 'sent'),
                              ('talent', 'waiting'), ('board', 'cancelled')):
            self.assertFalse(self.notice.show(source, state, '常规状态'))
        self.settings.talent_enabled = self.settings.board_broadcast_enabled = False
        self.assertFalse(self.notice.show('talent', 'blocked', '请手选'))
        self.assertFalse(self.notice.show('board', 'error', '未提交'))
        self.assertEqual(tuple(self.root.winfo_children()), children)
        self.assertEqual(self.native.styles, [])
        self.assertEqual(self.timers(), set())

    def test_key_failure_uses_passive_window_deduplicates_and_owns_one_timer(self):
        self.assertTrue(self.notice.show('talent', 'blocked', '本层请手选', generation=7))
        window = self.notice.window
        timers = self.timers()
        self.assertEqual(len(timers), 1)
        for _ in range(20):
            self.assertFalse(self.notice.show('talent', 'blocked', '本层请手选', generation=7))
        self.assertEqual(self.timers(), timers)
        self.assertEqual(self.native.styles, [False])
        self.assertEqual(len(self.native.shows), 1)
        self.assertTrue(self.notice.show('board', 'error', '文字未提交', generation=3))
        self.assertIs(self.notice.window, window)
        self.assertEqual(len(self.timers()), 1)

    def test_notice_stays_above_subtitle_bottom_anchor(self):
        self.notice.show('talent', 'blocked', '请手动检查本层')
        size, x, y = self.notice.window.geometry().split('+')
        width, height = map(int, size.split('x'))
        # 工作区为10000..11080，70%底边为10756，保留字幕所在86%区域。
        self.assertEqual(int(y) + height, 10756)
        self.assertGreaterEqual(int(x), 10000)
        self.assertLessEqual(int(x) + width, 11920)

    def test_same_failure_on_a_new_talent_tier_is_not_silenced(self):
        self.assertTrue(self.notice.show('talent','blocked','本层请手选',generation=7,dedupe_key=1))
        self.assertFalse(self.notice.show('talent','blocked','本层请手选',generation=7,dedupe_key=1))
        self.assertTrue(self.notice.show('talent','blocked','本层请手选',generation=7,dedupe_key=2))
        self.assertEqual(len(self.timers()),1)

    def test_expiry_clear_and_close_reclaim_timers_and_allow_new_match(self):
        with patch.object(type(self.notice), 'DURATION_MS', 1):
            self.notice.show('board', 'error', '可能已发送，请核对聊天记录', generation=1)
        time.sleep(.01)
        self.root.update()
        self.assertFalse(self.notice.visible)
        self.assertEqual(self.timers(), set())
        self.assertFalse(self.notice.show('board', 'error', '可能已发送，请核对聊天记录', generation=1))
        self.notice.clear()
        self.assertTrue(self.notice.show('board', 'error', '可能已发送，请核对聊天记录', generation=1))
        window = self.notice.window
        self.notice.close()
        self.assertFalse(window.winfo_exists())
        self.assertEqual(self.timers(), set())
        self.assertFalse(self.notice.show('talent', 'error', '关闭后的消息'))

    def test_settings_disable_hides_visible_notice_and_clears_timer(self):
        self.notice.show('talent', 'blocked', '请手选')
        self.settings.talent_enabled = False
        self.notice.apply_settings(self.settings)
        self.assertFalse(self.notice.visible)
        self.assertEqual(self.timers(), set())

    def test_clear_cancels_live_timer_and_duplicate_history_is_bounded(self):
        with patch.object(type(self.notice), 'MAX_SEEN', 3):
            for tier in (1, 2, 3, 4):
                self.assertTrue(self.notice.show('talent', 'blocked', f'层 {tier} 请手选'))
                self.assertEqual(len(self.timers()), 1)
            # 第1条已被有界历史淘汰；最近第4条仍应去重。
            self.assertFalse(self.notice.show('talent', 'blocked', '层 4 请手选'))
            self.assertTrue(self.notice.show('talent', 'blocked', '层 1 请手选'))
        self.notice.clear()
        self.assertFalse(self.notice.visible)
        self.assertEqual(self.timers(), set())

    def test_default_native_is_not_constructed_until_key_failure(self):
        module = importlib.import_module('hots_chat.ui.helper_notice')
        with patch.object(module, 'Win32Window', side_effect=AssertionError('不应初始化原生接口')):
            notice = module.HelperNotice(self.root, self.settings, metrics=self.metrics)
            self.addCleanup(notice.close)
            self.assertFalse(notice.show('talent', 'confirmed', '已确认'))
            self.settings.talent_enabled = False
            self.assertFalse(notice.show('talent', 'error', '已关闭'))
            self.assertIsNone(notice.window)

    def test_parent_destroy_before_notice_close_is_safe(self):
        parent = tk.Toplevel(self.root)
        parent.withdraw()
        module = importlib.import_module('hots_chat.ui.helper_notice')
        notice = module.HelperNotice(parent, self.settings, metrics=self.metrics, native=self.native)
        self.addCleanup(notice.close)
        notice.show('talent', 'blocked', '请手选')
        parent.destroy()
        notice.close()
        self.assertFalse(notice.visible)
        self.assertFalse(notice.show('talent', 'error', '过期状态'))


if __name__ == '__main__':
    unittest.main()
