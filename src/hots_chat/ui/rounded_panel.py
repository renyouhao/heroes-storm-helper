"""轻量圆角容器；仅在尺寸或主题变化时绘制，不使用图片或动画。"""
import tkinter as tk
from tkinter import ttk

from .theme import theme_colors


class RoundedPanel(ttk.Frame):
    def __init__(self, parent, *, settings, padding=12, fit_height=False):
        super().__init__(parent)
        self._padding = padding
        self._fit_height = fit_height
        self._scale = 1
        self._pending = None
        self._colors = theme_colors(settings)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0,
                                width=1, height=1, takefocus=False)
        self.canvas.pack(fill='both', expand=True)
        self.body = ttk.Frame(self, style='Surface.TFrame')
        self.body.place(x=padding, y=padding, width=1, height=1)
        self.canvas.bind('<Configure>', self._schedule)
        self.body.bind('<Configure>', self._schedule)
        self.apply_settings(settings)

    def apply_settings(self, settings, metrics=None):
        self._colors = theme_colors(settings)
        if metrics is not None:
            self._scale = metrics.scale
        self.canvas.configure(background=self._colors['background'])
        self._schedule()

    def set_padding(self, padding):
        if self._padding != padding:
            self._padding = padding
            self._schedule()

    def _schedule(self, event=None):
        if self._pending is None:
            self._pending = self.after_idle(self._draw)

    def _draw(self):
        self._pending = None
        inset = max(1, round(self._padding * self._scale))
        width = max(1, self.canvas.winfo_width())
        if self._fit_height:
            height = self.body.winfo_reqheight() + inset * 2
            if int(self.canvas.cget('height')) != height:
                self.canvas.configure(height=height)
        else:
            height = max(1, self.canvas.winfo_height())
        # 内容由几何管理器定位，不依赖Canvas是否收到可见区域重绘事件。
        self.body.place_configure(x=inset, y=inset, width=max(1, width - inset * 2),
                                  height=max(1, height - inset * 2))
        radius = max(1, min(round(14 * self._scale), width / 2, height / 2))
        x, y, right, bottom = 1, 1, max(1, width - 1), max(1, height - 1)
        points = (x + radius, y, right - radius, y, right, y, right, y + radius,
                  right, bottom - radius, right, bottom, right - radius, bottom,
                  x + radius, bottom, x, bottom, x, bottom - radius, x, y + radius, x, y)
        self.canvas.delete('surface')
        self.canvas.create_polygon(points, smooth=True, splinesteps=24,
                                   fill=self._colors['surface'], outline=self._colors['border'],
                                   width=1, tags='surface')
        self.canvas.tag_lower('surface')

    def destroy(self):
        if self._pending is not None:
            self.after_cancel(self._pending)
            self._pending = None
        super().destroy()
