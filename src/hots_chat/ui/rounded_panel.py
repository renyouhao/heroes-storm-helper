"""轻量圆角容器；仅在尺寸或主题变化时绘制，不使用图片或动画。"""
import tkinter as tk
from tkinter import ttk

from .theme import theme_colors


class RoundedPanel(ttk.Frame):
    def __init__(self, parent, *, settings, padding=12, fit_height=False,
                 radius=12, surface='surface', background='background'):
        super().__init__(parent)
        self._padding = padding
        self._fit_height = fit_height
        self._radius = radius
        self._surface_role, self._background_role = surface, background
        self._focused = False
        self._scale = 1
        self._pending = None
        self._draw_signature = None
        self._body_geometry = None
        self._surface = None
        self._colors = theme_colors(settings)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0,
                                width=1, height=1, takefocus=False)
        self.canvas.pack(fill='both', expand=True)
        self.body = ttk.Frame(self, style='Field.TFrame' if surface == 'field' else 'Surface.TFrame')
        self.body.place(x=padding, y=padding, width=1, height=1)
        self.canvas.bind('<Configure>', self._schedule)
        self.body.bind('<Configure>', self._schedule)
        self.apply_settings(settings)

    def apply_settings(self, settings, metrics=None):
        if getattr(self, '_destroyed', False):return
        self._colors = theme_colors(settings)
        if metrics is not None:
            self._scale = metrics.scale
        self.canvas.configure(background=self._colors[self._background_role])
        self._schedule()

    def set_padding(self, padding):
        if self._padding != padding:
            self._padding = padding
            self._schedule()

    def set_focused(self, focused):
        if getattr(self, '_destroyed', False):return
        focused = bool(focused)
        if self._focused != focused:
            self._focused = focused
            self._schedule()

    def _schedule(self, event=None):
        if getattr(self, '_destroyed', False):return
        if self._pending is None:
            self._pending = self.after_idle(self._draw)

    def _draw(self):
        self._pending = None
        if getattr(self, '_destroyed', False):return
        inset = max(1, round(self._padding * self._scale))
        width = max(1, self.canvas.winfo_width())
        if self._fit_height:
            height = self.body.winfo_reqheight() + inset * 2
            if int(self.canvas.cget('height')) != height:
                self.canvas.configure(height=height)
        else:
            height = max(1, self.canvas.winfo_height())
        # 内容由几何管理器定位，不依赖Canvas是否收到可见区域重绘事件。
        geometry = (inset, inset, max(1, width - inset * 2), max(1, height - inset * 2))
        if geometry != self._body_geometry:
            self.body.place_configure(x=geometry[0], y=geometry[1], width=geometry[2], height=geometry[3])
            self._body_geometry = geometry
        radius = max(1, min(round(self._radius * self._scale), width / 2, height / 2))
        fill = self._colors[self._surface_role]
        outline = self._colors['accent' if self._focused else 'border']
        signature = (width, height, radius, fill, outline)
        if signature == self._draw_signature:
            return
        x, y, right, bottom = 1, 1, max(1, width - 1), max(1, height - 1)
        points = (x + radius, y, right - radius, y, right, y, right, y + radius,
                  right, bottom - radius, right, bottom, right - radius, bottom,
                  x + radius, bottom, x, bottom, x, bottom - radius, x, y + radius, x, y)
        if self._surface is None:
            self._surface = self.canvas.create_polygon(points, smooth=True, splinesteps=12,
                                                       width=1, tags='surface')
            self.canvas.tag_lower(self._surface)
        elif self._draw_signature[:3] != signature[:3]:
            self.canvas.coords(self._surface, *points)
        self.canvas.itemconfigure(self._surface, fill=fill, outline=outline)
        self._draw_signature = signature

    def destroy(self):
        if getattr(self, '_destroyed', False):
            return
        self._destroyed = True
        if self._pending is not None:
            self.after_cancel(self._pending)
            self._pending = None
        super().destroy()
        # Tk 删除 children 里的控件，但自建缓存仍会经 child.master 回指本容器。
        # 在界面线程断开此环，避免解释器延迟到后台线程 GC 时析构。
        self.canvas = self.body = None
