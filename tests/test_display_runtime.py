"""DPI API用隔离替身；实际进程DPI切换与隐藏Tk只在专用子进程执行。"""
import json
import subprocess
import sys
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    from hots_chat import display_runtime as runtime
except ImportError:
    runtime = None


class NativeFunction:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.result(*args) if callable(self.result) else self.result


class FakeTk:
    def __init__(self, scaling=4 / 3):
        self.scaling = scaling
        self.writes = []
        self.font_writes = []

    def call(self, *args):
        if args == ('tk', 'scaling'):
            return self.scaling
        if args[:2] == ('tk', 'scaling'):
            self.scaling = args[2]
            self.writes.append(args[2])
        elif args == ('font', 'names'):
            return ('TkDefaultFont', 'custom_font')
        elif args[:2] == ('font', 'configure'):
            if len(args) == 4:
                return 12 if args[2] == 'TkDefaultFont' else -18
            self.font_writes.append(args)


def fake_root(scaling=4 / 3):
    return SimpleNamespace(tk=FakeTk(scaling), winfo_id=lambda: 123,
                           winfo_screenwidth=lambda: 1920,
                           winfo_screenheight=lambda: 1080)


class DisplayRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(runtime, '需要独立的DPI运行时模块')

    def libraries(self, user=None, shcore=None):
        def load(name):
            value = {'user32': user, 'shcore': shcore}.get(name)
            if value is None:
                raise OSError('API unavailable')
            return value
        return patch.object(runtime, '_load_library', side_effect=load)

    def test_early_request_selects_per_monitor_v2(self):
        setter = NativeFunction(True)
        with self.libraries(SimpleNamespace(SetProcessDpiAwarenessContext=setter)):
            self.assertEqual(runtime.enable_process_dpi_awareness(), 'per-monitor-v2')
        self.assertEqual(setter.calls, [(-4,)])

    def test_manifest_per_monitor_v2_is_reported_when_request_is_denied(self):
        user = SimpleNamespace(SetProcessDpiAwarenessContext=NativeFunction(False),
                               GetThreadDpiAwarenessContext=NativeFunction(88),
                               AreDpiAwarenessContextsEqual=NativeFunction(True),
                               GetAwarenessFromDpiAwarenessContext=NativeFunction(2))
        with self.libraries(user):
            self.assertEqual(runtime.enable_process_dpi_awareness(), 'per-monitor-v2')

    def test_locked_system_mode_is_reported_without_claiming_per_monitor(self):
        user = SimpleNamespace(SetProcessDpiAwarenessContext=NativeFunction(False),
                               GetThreadDpiAwarenessContext=NativeFunction(88),
                               AreDpiAwarenessContextsEqual=NativeFunction(False),
                               GetAwarenessFromDpiAwarenessContext=NativeFunction(1))
        with self.libraries(user):
            self.assertEqual(runtime.enable_process_dpi_awareness(), 'system')

    def test_older_windows_falls_back_to_per_monitor_then_system(self):
        for result, expected in ((0, 'per-monitor'), (-1, 'system')):
            with self.subTest(result=result):
                user = SimpleNamespace(SetProcessDPIAware=NativeFunction(True))
                shcore = SimpleNamespace(SetProcessDpiAwareness=NativeFunction(result))
                with self.libraries(user, shcore):
                    self.assertEqual(runtime.enable_process_dpi_awareness(), expected)

    def test_legacy_preconfigured_system_mode_is_reported_after_denied_request(self):
        user = SimpleNamespace(SetProcessDPIAware=NativeFunction(False),
                               IsProcessDPIAware=NativeFunction(True))
        with self.libraries(user):
            self.assertEqual(runtime.enable_process_dpi_awareness(), 'system')

    def test_shcore_preconfigured_per_monitor_mode_is_not_misreported_as_system(self):
        def current_awareness(process, output):
            output._obj.value = 2
            return 0
        user = SimpleNamespace(IsProcessDPIAware=NativeFunction(True))
        shcore = SimpleNamespace(GetProcessDpiAwareness=NativeFunction(current_awareness),
                                 SetProcessDpiAwareness=NativeFunction(-2147024891))
        with self.libraries(user, shcore):
            self.assertEqual(runtime.enable_process_dpi_awareness(), 'per-monitor')

    def test_missing_windows_apis_are_safe(self):
        with self.libraries():
            self.assertEqual(runtime.enable_process_dpi_awareness(), 'unavailable')

    def test_window_dpi_prefers_native_window_over_system_and_tk(self):
        get_window = NativeFunction(144)
        user = SimpleNamespace(GetDpiForWindow=get_window, GetDpiForSystem=NativeFunction(96))
        with self.libraries(user):
            self.assertEqual(runtime.window_dpi(fake_root(8 / 3)), 144)
        self.assertEqual(get_window.calls, [(123,)])

    def test_zero_window_dpi_falls_back_to_system(self):
        user = SimpleNamespace(GetDpiForWindow=NativeFunction(0), GetDpiForSystem=NativeFunction(120))
        with self.libraries(user):
            self.assertEqual(runtime.window_dpi(fake_root()), 120)

    def test_missing_native_dpi_uses_tk_then_96(self):
        for scaling, expected in ((8 / 3, 192), (0, 96), (float('nan'), 96)):
            with self.subTest(scaling=scaling), self.libraries():
                self.assertEqual(runtime.window_dpi(fake_root(scaling)), expected)

    def test_zero_native_system_dpi_uses_tk(self):
        user = SimpleNamespace(GetDpiForWindow=NativeFunction(0), GetDpiForSystem=NativeFunction(0))
        with self.libraries(user):
            self.assertEqual(runtime.window_dpi(fake_root(2)), 144)

    def test_dpi_change_sets_points_scaling_and_refreshes_existing_font_cache_once(self):
        root = fake_root()
        values = iter((96, 192, 192, 120))
        display = runtime.TkDisplayRuntime(root, dpi_reader=lambda root: next(values),
                                          work_areas=lambda: [(0, 0, 3840, 2080)])
        scales = [display.refresh().scale for _ in range(4)]
        self.assertEqual(scales, [1, 2, 2, 1.25])
        self.assertEqual(root.tk.writes, [4 / 3, 8 / 3, 5 / 3])
        self.assertEqual(len(root.tk.font_writes), 6)
        self.assertEqual(root.tk.font_writes[-2:], [
            ('font', 'configure', 'TkDefaultFont', '-size', 12),
            ('font', 'configure', 'custom_font', '-size', -18)])

    def test_work_area_change_does_not_reconfigure_unchanged_dpi(self):
        root = fake_root()
        areas = iter(([(0, 0, 3840, 2080)], [(0, 0, 1920, 1040)]))
        display = runtime.TkDisplayRuntime(root, dpi_reader=lambda root: 144,
                                          work_areas=lambda: next(areas))
        first, second = display.refresh(), display.refresh()
        self.assertEqual(first.work_area, (0, 0, 3840, 2080))
        self.assertEqual(second.work_area, (0, 0, 1920, 1040))
        self.assertEqual(second.scale, 1.5)
        self.assertEqual(root.tk.writes, [2])

    def test_invalid_work_areas_use_screen_then_preserve_last_valid_area(self):
        for area in ([], [(0, 0, 0, 100)], [('bad', 0, 100, 100)], [None]):
            with self.subTest(area=area):
                root = fake_root()
                display = runtime.TkDisplayRuntime(root, dpi_reader=lambda root: 96,
                                                  work_areas=lambda: area)
                self.assertEqual(display.refresh().work_area, (0, 0, 1920, 1080))
        areas = iter(([(100, 50, 2100, 1100)], []))
        display = runtime.TkDisplayRuntime(fake_root(), dpi_reader=lambda root: 96,
                                          work_areas=lambda: next(areas))
        self.assertEqual(display.refresh(), display.refresh())

    def test_invalid_dpi_preserves_last_good_scale(self):
        values = iter((120, 0, float('nan')))
        display = runtime.TkDisplayRuntime(fake_root(), dpi_reader=lambda root: next(values),
                                          work_areas=lambda: [(0, 0, 1920, 1040)])
        self.assertEqual([display.refresh().scale for _ in range(3)], [1.25] * 3)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows专用隔离子进程')
    def test_real_hidden_tk_font_metrics_follow_simulated_dpi_without_multiplication(self):
        script = textwrap.dedent('''
            import json
            import tkinter as tk
            from tkinter import font
            from hots_chat.display_runtime import enable_process_dpi_awareness, TkDisplayRuntime
            mode = enable_process_dpi_awareness()
            root = tk.Tk()
            root.withdraw()
            try:
                named = font.Font(root=root, family='Arial', size=12)
                values = iter((96, 192, 192, 120, 96))
                runtime = TkDisplayRuntime(root, dpi_reader=lambda root: next(values),
                                           work_areas=lambda: [(0, 0, 3840, 2080)])
                results = []
                for _ in range(5):
                    metrics = runtime.refresh()
                    root.update_idletasks()
                    results.append([metrics.scale, named.cget('size'), named.metrics('linespace')])
                print(json.dumps({'mode': mode, 'results': results}))
            finally:
                root.destroy()
        ''')
        completed = subprocess.run([sys.executable, '-c', script], capture_output=True,
                                   text=True, timeout=30, check=True)
        values = json.loads(completed.stdout)['results']
        self.assertEqual([value[:2] for value in values], [[1, 12], [2, 12], [2, 12], [1.25, 12], [1, 12]])
        self.assertGreater(values[1][2], values[0][2] * 1.6)
        self.assertLess(values[1][2], values[0][2] * 2.4)
        self.assertEqual(values[1][2], values[2][2])
        self.assertGreater(values[3][2], values[0][2])
        self.assertEqual(values[0][2], values[4][2])


if __name__ == '__main__':
    unittest.main()
