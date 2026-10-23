import gc
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from hots_chat.diagnostics import DiagnosticRecorder
from hots_chat.ui.diagnostics_panel import DiagnosticsPanel


class DiagnosticUITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.close_root)
        clock = self.clock = SimpleNamespace(tick=0.0)
        self.recorder = DiagnosticRecorder(Path(self.tmp.name) / 'logs', monotonic=lambda: clock.tick, enabled=True)
        exports = self._export_threads = []
        start_thread = threading.Thread.start
        def start(thread):
            if thread.name == 'diagnostic-export':
                exports.append(thread)
            return start_thread(thread)
        observer = patch.object(threading.Thread, 'start', start)
        observer.start()
        self.addCleanup(observer.stop)
        self.panel = DiagnosticsPanel(self.root, self.recorder, root=self.root)
        self.panel.pack()
        self.root.update_idletasks()

    def close_root(self):
        root, recorder = self.root, getattr(self, 'recorder', None)
        try:
            for worker in getattr(self, '_export_threads', ()):
                worker.join(3)
                self.assertFalse(worker.is_alive(), '诊断导出线程未结束')
            if recorder is not None:
                recorder.close()
                if recorder.thread is not None:
                    self.assertFalse(recorder.thread.is_alive(), '诊断写入线程未结束')
        finally:
            root.update_idletasks()
            root.destroy()
            # destroy 移除 Tcl 控件，但 Python 控件/变量仍可能相互引用。
            # 用例先释放所有 Tk 字段，再由创建解释器的主线程回收这些环。
            self.panel = self.root = self.recorder = None
            gc.collect()

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertTrue(predicate())

    def test_export_writes_zip_and_reports_saved_path(self):
        self.recorder.record('app.start')
        target = Path(self.tmp.name) / 'report.zip'
        self.assertTrue(self.panel.start_export(target))
        self.assertFalse(self.panel.start_export(target))
        self.wait(lambda: not self.panel.busy)
        with zipfile.ZipFile(target) as archive:
            self.assertIn('summary.json', archive.namelist())
        self.assertIn(str(target), self.panel.status.get())
        self.assertFalse(self.panel.export_button.instate(['disabled']))

    def test_details_switch_expires_without_persisting_setting(self):
        self.panel.details_button.invoke()
        self.assertTrue(self.recorder.snapshot()['details_enabled'])
        self.clock.tick = 600
        self.panel.refresh_status()
        self.assertFalse(self.panel.details.get())
        self.assertFalse(self.recorder.snapshot()['details_enabled'])

    def test_export_failure_is_visible_and_can_be_retried(self):
        target = Path(self.tmp.name) / 'missing-folder' / 'report.zip'
        self.panel.start_export(target)
        self.wait(lambda: not self.panel.busy)
        self.assertIn('失败', self.panel.status.get())
        target = Path(self.tmp.name) / 'retry.zip'
        self.panel.start_export(target)
        self.wait(lambda: not self.panel.busy)
        self.assertTrue(target.is_file())

    def test_destroyed_panel_does_not_receive_worker_tk_callbacks(self):
        entered, release = threading.Event(), threading.Event()
        original = self.recorder.export_report
        def slow(destination):
            entered.set()
            release.wait(2)
            return original(destination)
        self.recorder.export_report = slow
        target = Path(self.tmp.name) / 'closed.zip'
        self.panel.start_export(target)
        self.assertTrue(entered.wait(1))
        self.panel.destroy()
        release.set()
        self.wait(target.is_file)
        self.root.update()
        self.assertTrue(self.recorder.snapshot()['enabled'])

    def test_default_off_can_be_enabled_and_disabled_from_settings(self):
        self.panel.destroy()
        self.recorder.close()
        self.recorder=DiagnosticRecorder(Path(self.tmp.name)/'off')
        self.panel=DiagnosticsPanel(self.root,self.recorder,root=self.root)
        self.assertFalse(self.panel.enabled_var.get())
        self.assertTrue(self.panel.details_button.instate(['disabled']))
        self.assertIsNone(self.recorder.thread)
        self.panel.enabled_button.invoke()
        self.assertTrue(self.recorder.snapshot()['enabled'])
        self.assertFalse(self.panel.details_button.instate(['disabled']))
        self.panel.enabled_button.invoke()
        self.assertFalse(self.recorder.snapshot()['enabled'])
