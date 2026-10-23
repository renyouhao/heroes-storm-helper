"""用户手动开启的一次天赋验证窗口；无云服务、无现用凭据/偏好访问。

运行示例：python tools/preview_talent_helper.py --report artifacts/天赋验证/自动选择实测.json
只在用户勾选后运行产品 helper；本工具的后端额外限制本进程最多发送一次组合键。
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading
import time
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hots_chat.acquisition.talent_source import TalentReader
from hots_chat.layout import display_metrics
from hots_chat.talent_native import Win32TalentBackend
from hots_chat.talent_service import TalentHelper
from hots_chat.ui.talent_panel import TalentPanel
from hots_chat.ui.theme import apply_theme
from hots_chat.window_settings import WindowSettings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    events, lock = [], threading.Lock()
    def record(kind, **data):
        with lock:
            if len(events) < 256:
                events.append(dict(kind=kind, utc=datetime.now(timezone.utc).isoformat(),
                                   monotonic=time.monotonic(), **data))
    class Recorder:
        def record(self, kind, **data):record(kind, **data)
    class Reader(TalentReader):
        last = None
        def poll(self, pid):
            result = super().poll(pid)
            key = (result.status, result.generation, result.hero_id, result.level, result.selected)
            if key != self.last:
                self.last = key
                record('snapshot', snapshot=asdict(result))
            return result
    class OnceBackend(Win32TalentBackend):
        attempted = False
        def select_favorite(self, target, *, allowed=None, validate=None):
            if self.attempted:return 'deferred'
            result = super().select_favorite(target, allowed=allowed, validate=validate)
            if result in ('sent', 'failed'):self.attempted = True
            record('native.input', outcome=result, pid=target.pid)
            return result
    root = tk.Tk()
    root.title('自动首选天赋 · 独立单次测试（不连接云翻译）')
    root.geometry('720x530')
    settings = WindowSettings(hotkeys={})
    apply_theme(root, settings)
    helper = TalentHelper(reader_factory=Reader, backend_factory=OnceBackend, diagnostics=Recorder())
    def toggle(enabled):
        if not helper.set_enabled(enabled):raise OSError('上次测试线程尚未退出')
        settings.talent_enabled = enabled
        record('user.toggle', enabled=enabled)
    panel = TalentPanel(root, settings=settings, metrics=display_metrics(root), on_toggle=toggle)
    panel.pack(fill='both', expand=True)
    closing = False
    def save_report():
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with lock:copy = list(events)
        args.report.write_text(json.dumps({'single_input_limit':True, 'events':copy},
                                         ensure_ascii=False, indent=2), encoding='utf-8')
    def pump():
        if closing:return
        for status in helper.drain_status():
            panel.update_status(status.state, status.detail)
        save_report()
        root.after(100, pump)
    def close():
        nonlocal closing
        closing = True
        helper.close()
        panel.close()
        save_report()
        root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    root.after(100, pump)
    root.mainloop()


if __name__ == '__main__':main()
