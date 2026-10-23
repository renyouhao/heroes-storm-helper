"""设置页的诊断导出；后台只访问记录器，不访问任何Tk对象。"""
from datetime import datetime
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from .. import __version__


class DiagnosticsPanel(ttk.Frame):
    def __init__(self, parent, recorder, *, root=None):
        super().__init__(parent)
        self.recorder = recorder
        self._closed = self.busy = False
        self._results = queue.SimpleQueue()
        self._timer = None
        ttk.Label(self, text='诊断日志', style='Heading.TLabel').pack(anchor='w', pady=(0, 8))
        ttk.Label(self, text='默认关闭。遇到问题时先开启、复现，再导出 ZIP 自行发给开发者。',
                  wraplength=480, justify='left').pack(fill='x', pady=(0, 12))
        self.enabled_var=tk.BooleanVar(self,value=recorder.snapshot()['enabled'])
        self.enabled_button=ttk.Checkbutton(self,text='开启诊断日志（仅本次运行有效）',
                                          variable=self.enabled_var,command=self._set_enabled)
        self.enabled_button.pack(anchor='w',pady=(0,8))
        self.details = tk.BooleanVar(self, value=recorder.snapshot()['details_enabled'])
        self.details_button = ttk.Checkbutton(self, text='详细诊断：包含聊天原文和译文（10 分钟后关闭）',
                                             variable=self.details, command=self._set_details)
        self.details_button.pack(anchor='w', pady=(0, 8))
        ttk.Label(self, text='正文和译文各最多 256 字；已配置密钥会脱敏。分享前请检查正文中是否有其他私人信息。',
                  style='Muted.TLabel', wraplength=480, justify='left').pack(fill='x', pady=(0, 12))
        self.export_button = ttk.Button(self, text='一键导出诊断日志', command=self.start_export,
                                        style='Accent.TButton')
        self.export_button.pack(anchor='w')
        self.status = tk.StringVar(self, value='')
        ttk.Label(self, textvariable=self.status, wraplength=480, justify='left').pack(fill='x', pady=(10, 0))
        self.health = tk.StringVar(self, value='')
        ttk.Label(self, textvariable=self.health, style='Muted.TLabel', wraplength=480,
                  justify='left').pack(fill='x', pady=(10, 0))
        self.bind('<Destroy>', self._destroyed, add='+')
        self.refresh_status()
        self._timer = self.after(200, self._poll)

    def _set_details(self):
        self.recorder.enable_details(self.details.get())
        self.status.set('详细诊断已开启，日志会包含限长聊天正文。' if self.details.get() else '已恢复基础诊断。')
        self.refresh_status()

    def _set_enabled(self):
        self.recorder.set_enabled(self.enabled_var.get())
        self.status.set('诊断已开启，请复现问题后导出。' if self.enabled_var.get()
                        else '已停止记录，已有日志仍可导出；下次启动默认关闭。')
        self.refresh_status()

    def refresh_status(self):
        if self._closed:
            return
        snapshot = self.recorder.snapshot()
        self.enabled_var.set(snapshot['enabled'])
        self.details.set(snapshot['details_enabled'])
        self.details_button.state(['!disabled'] if snapshot['enabled'] else ['disabled'])
        state = ('日志写入不可用，请检查用户数据目录后重启助手。' if snapshot['write_error']
                 else '后台关键诊断已开启。' if snapshot['enabled'] else '诊断已关闭，不记录新事件。')
        self.health.set(state + f" 过载丢弃：{snapshot['dropped']} 条。日志最多约 10 MiB，保留 7 天。")

    def start_export(self, destination=None):
        if self._closed or self.busy:
            return False
        if destination is None:
            destination = filedialog.asksaveasfilename(parent=self, title='导出诊断日志',
                defaultextension='.zip', filetypes=[('ZIP 压缩包', '*.zip')],
                initialfile=f'HotsHelper-diagnostics-{__version__}-{datetime.now():%Y%m%d-%H%M%S}.zip')
        if not destination:
            return False
        self.busy = True
        self.export_button.state(['disabled'])
        self.status.set('正在后台导出，请稍候。')
        results, recorder = self._results, self.recorder
        def export():
            try:
                path = recorder.export_report(Path(destination))
                results.put((str(path), False))
            except Exception:
                results.put(('', True))
        threading.Thread(target=export, name='diagnostic-export', daemon=True).start()
        return True

    def _poll(self):
        self._timer = None
        if self._closed:
            return
        while True:
            try:
                path, failed = self._results.get_nowait()
            except queue.Empty:
                break
            self.busy = False
            self.export_button.state(['!disabled'])
            self.status.set('导出失败，请检查保存位置后重试。' if failed else '已保存：' + path)
        self.refresh_status()
        self._timer = self.after(200 if self.busy else 1000, self._poll)

    def _destroyed(self, event):
        if event.widget is not self:
            return
        self._closed = True
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None

    def destroy(self):
        if getattr(self, '_widget_destroyed', False):return
        self._widget_destroyed = True
        descendants = []
        pending = list(self.children.values())
        while pending:
            child = pending.pop()
            descendants.append(child)
            pending.extend(child.children.values())
        super().destroy()
        # Tk已销毁，解除按钮缓存的父级回边，避免四个变量拖到后台GC析构。
        for child in descendants:child.master = None
        self.master = None
