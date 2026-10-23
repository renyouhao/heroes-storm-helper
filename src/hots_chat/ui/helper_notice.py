"""天赋与播报的被动关键结果提示；没有游戏输入、hook、线程或轮询。"""
from collections import OrderedDict
import tkinter as tk
from tkinter import font as tkfont

from ..layout import bound_rect, display_metrics
from .native_windows import Win32Window
from .theme import overlay_colors


_TALENT_STATES = frozenset(('blocked', 'unconfirmed', 'error'))
_BOARD_STATES = frozenset(('error', 'failed', 'unsubmitted', 'send_unconfirmed'))


class _NoticeOverlay:
    TRANSPARENT_COLOR = '#010203'

    def __init__(self, root, native):
        self.native = native
        self.window = tk.Toplevel(root)
        try:
            self.window._hots_theme_scope = 'overlay'
            self.window.withdraw()
            self.window.title('辅助功能提示')
            self.window.overrideredirect(True)
            self.window.configure(background=self.TRANSPARENT_COLOR, takefocus=False)
            self.window.attributes('-transparentcolor', self.TRANSPARENT_COLOR)
            self.window.attributes('-alpha', .96)
            self.canvas = tk.Canvas(self.window, background=self.TRANSPARENT_COLOR,
                                    highlightthickness=0, borderwidth=0, takefocus=False)
            self.canvas.pack(fill='both', expand=True)
            self.title_font = tkfont.Font(root=self.window, family='Microsoft YaHei UI',
                                          size=12, weight='bold')
            self.detail_font = tkfont.Font(root=self.window, family='Microsoft YaHei UI', size=11)
            self._styled = False
        except Exception:
            self.window.destroy()
            raise

    def render(self, title, detail, settings, metrics):
        colors = overlay_colors(settings)
        size = max(10, settings.overlay_font_size)
        self.title_font.configure(size=size + 1)
        self.detail_font.configure(size=size)
        padding = metrics.px(12)
        width = min(metrics.px(460), metrics.work_area[2] - metrics.work_area[0])
        text_width = max(1, width - padding * 2)
        self.canvas.delete('all')
        self.canvas.create_text(padding, padding, text=title, font=self.title_font,
                                fill=colors['warning'], anchor='nw', width=text_width, tags='title')
        title_bottom = self.canvas.bbox('title')[3]
        self.canvas.create_text(padding, title_bottom + metrics.px(5), text=detail,
                                font=self.detail_font, fill=colors['text'], anchor='nw',
                                width=text_width, tags='detail')
        height = self.canvas.bbox('detail')[3] + padding
        left, top, right_edge, bottom_edge = metrics.work_area
        layout = bound_rect(metrics, width=width, height=height,
                            x=left + round((right_edge - left - width) / 2),
                            y=top + round((bottom_edge - top) * .70) - height)
        radius = min(metrics.px(10), width / 2, height / 2)
        x, y, right, bottom = 1, 1, width - 1, height - 1
        points = (x + radius, y, right - radius, y, right, y, right, y + radius,
                  right, bottom - radius, right, bottom, right - radius, bottom,
                  x + radius, bottom, x, bottom, x, bottom - radius, x, y + radius, x, y)
        self.canvas.create_polygon(points, smooth=True, splinesteps=24, fill=colors['surface'],
                                   outline=colors['border'], tags='surface')
        self.canvas.tag_lower('surface')
        self.canvas.configure(width=layout.width, height=layout.height)
        self.window.geometry(layout.geometry)
        self.window.update_idletasks()
        if not self._styled:
            # 仅操作助手自己窗口：鼠标穿透、工具窗、禁止激活。
            self.native.set_overlay_mode(self.window, editing=False)
            self._styled = True

    def show(self):
        self.native.show_no_activate(self.window)

    def hide(self):
        try:self.window.withdraw()
        except tk.TclError:pass  # 父窗口可能已经由主窗关闭流程销毁。

    def close(self):
        try:
            if self.window.winfo_exists():self.window.destroy()
        except tk.TclError:pass


class HelperNotice:
    """由主窗口按前台与代次过滤后推送；同一关键结果只展示一次。

    ``clear`` 用于换局、切出游戏或关闭功能，同时回收唯一到期timer。
    在首次关键异常前不创建窗口或原生接口；成功与等待状态始终静默。
    """
    DURATION_MS = 5000
    MAX_SEEN = 64

    def __init__(self, root, settings, metrics=None, native=None):
        self.root, self.settings, self.metrics = root, settings, metrics
        self._native = native
        self._overlay = None
        self.window = None
        self._timer = None
        self._seen = OrderedDict()
        self._current = None
        self._visible = self._closed = False

    @property
    def visible(self):
        return self._visible

    def _enabled(self, source):
        return bool(getattr(self.settings, 'talent_enabled' if source == 'talent'
                            else 'board_broadcast_enabled', False))

    def show(self, source, state, detail, generation=0, *, dedupe_key=None):
        source = 'board' if source == 'broadcast' else source
        if self._closed or source not in ('talent', 'board') or not self._enabled(source):
            return False
        if state not in (_TALENT_STATES if source == 'talent' else _BOARD_STATES):
            return False
        detail = str(detail or '')[:500]
        key = source, state, detail, generation, dedupe_key
        if key in self._seen:return False
        self._seen[key] = None
        while len(self._seen) > self.MAX_SEEN:self._seen.popitem(last=False)
        if source == 'talent':
            title = '天赋辅助 · 请手动检查本层' if state != 'error' else '天赋辅助 · 本次操作未完成'
            detail = detail or '本层未确认选中，请手动选择；助手不会重复按键。'
        else:
            title = '双击播报 · 请核对聊天框'
            detail = detail or '发送未完整确认，请检查聊天记录与草稿；不会自动补发。'
        try:
            if self.metrics is None:self.metrics = display_metrics(self.root)
            if self._overlay is None:
                if self._native is None:self._native = Win32Window()
                self._overlay = _NoticeOverlay(self.root, self._native)
                self.window = self._overlay.window
            self._overlay.render(title, detail, self.settings, self.metrics)
            self._cancel_timer()
            self._overlay.show()
            self._current = source, title, detail
            self._visible = True
            self._timer = self.root.after(self.DURATION_MS, self._hide)
            return True
        except Exception:
            # 提示失败不能影响辅助服务；失败结果仍去重，避免每轮重建资源。
            self._cancel_timer()
            if self._overlay is not None:self._overlay.close()
            self._overlay = self.window = self._current = None
            self._visible = False
            return False

    def _cancel_timer(self):
        if self._timer is not None:
            try:self.root.after_cancel(self._timer)
            except tk.TclError:pass
            self._timer = None

    def _hide(self):
        self._cancel_timer()
        if self._overlay is not None:self._overlay.hide()
        self._visible = False
        self._current = None

    def clear(self):
        if self._closed:return
        self._hide()
        self._seen.clear()

    def apply_settings(self, settings, metrics=None):
        if self._closed:return
        self.settings = settings
        if metrics is not None:self.metrics = metrics
        if self._current is None:return
        source, title, detail = self._current
        if not self._enabled(source):
            self._hide()
        elif self._visible:
            self._overlay.render(title, detail, settings, self.metrics)

    def close(self):
        if self._closed:return
        self.clear()
        self._closed = True
        if self._overlay is not None:self._overlay.close()
        self._overlay = self.window = None
