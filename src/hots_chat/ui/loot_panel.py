"""开箱助手的说明页与被动状态浮条；不拥有热键、输入或业务线程。"""
import tkinter as tk
from tkinter import font as tkfont, ttk

from ..layout import bottom_overlay_layout
from .native_windows import Win32Window
from .rounded_panel import RoundedPanel
from .theme import overlay_colors, theme_colors


_STATUS = {
    'idle': ('准备好再开始', '先进入游戏的补给箱页，再点击下方按钮。', 'muted'),
    'preparing': ('正在准备', '正在检查游戏窗口，请稍候。', 'accent'),
    'ready': ('待开始', '回到游戏，按 Space 开始，Esc 结束本次模式。尚未开始时可切回游戏、选择补给箱页。', 'accent'),
    'running': ('正在开箱', 'Space 暂停。其他按键、鼠标点击、滚轮或切出游戏，会结束本次模式。', 'success'),
    'paused': ('已暂停', 'Space 继续。其他按键、鼠标点击、滚轮或切出游戏，会结束本次模式。', 'warning'),
    'error': ('暂时无法准备', '请确认游戏已打开，再重新准备。', 'danger'),
}
_ACTIVE = frozenset(('ready', 'running', 'paused'))
_OVERLAY_HINT = {
    'ready': 'Space 开始 · Esc 结束',
    'running': 'Space 暂停 · 其他操作结束',
    'paused': 'Space 继续 · 其他操作结束',
}


class _LootOverlay:
    """只在状态改变时重绘；原生接口只设置助手自身窗口的被动样式。"""
    TRANSPARENT_COLOR = '#010203'

    def __init__(self, parent, native):
        self.native = native
        self.window = tk.Toplevel(parent)
        try:
            self.window._hots_theme_scope = 'overlay'
            self.window.withdraw()
            self.window.title('开箱助手状态')
            self.window.overrideredirect(True)
            self.window.configure(background=self.TRANSPARENT_COLOR, takefocus=False)
            self.window.attributes('-transparentcolor', self.TRANSPARENT_COLOR)
            self.window.attributes('-alpha', .94)
            self.canvas = tk.Canvas(self.window, background=self.TRANSPARENT_COLOR,
                                    highlightthickness=0, borderwidth=0, takefocus=False)
            self.canvas.pack(fill='both', expand=True)
            self.title_font = tkfont.Font(root=self.window, family='Microsoft YaHei UI',
                                          size=11, weight='bold')
            self.hint_font = tkfont.Font(root=self.window, family='Microsoft YaHei UI', size=10)
            self._styled = False
            self._visible = False
        except Exception:
            # 构造失败时对象不会交给调用方，必须在此回收已创建的窗口。
            self.window.destroy()
            raise

    def render(self, state, settings, metrics):
        colors = overlay_colors(settings)
        self.window.configure(background=self.TRANSPARENT_COLOR)
        padding = metrics.px(12)
        width = min(metrics.px(420), metrics.work_area[2] - metrics.work_area[0])
        text_width = max(1, width - padding * 2)
        title = '开箱助手 · ' + _STATUS[state][0]
        self.canvas.delete('all')
        # Canvas 根据真实 Tk 字体度量决定高度；字号不再乘一次 DPI。
        self.canvas.create_text(padding, padding, text=title, font=self.title_font,
                                fill=colors['accent'], anchor='nw', width=text_width, tags='title')
        title_bottom = self.canvas.bbox('title')[3]
        self.canvas.create_text(padding, title_bottom + metrics.px(3),
                                text=_OVERLAY_HINT[state], font=self.hint_font,
                                fill=colors['text'], anchor='nw', width=text_width, tags='hint')
        height = self.canvas.bbox('hint')[3] + padding
        layout = bottom_overlay_layout(metrics, width=420, height=height)
        radius = min(metrics.px(10), width / 2, height / 2)
        x, y, right, bottom = 1, 1, width - 1, height - 1
        points = (x + radius, y, right - radius, y, right, y, right, y + radius,
                  right, bottom - radius, right, bottom, right - radius, bottom,
                  x + radius, bottom, x, bottom, x, bottom - radius, x, y + radius, x, y)
        self.canvas.create_polygon(points, smooth=True, splinesteps=24,
                                   fill=colors['surface'], outline=colors['border'], tags='surface')
        self.canvas.tag_lower('surface')
        self.canvas.configure(width=layout.width, height=layout.height)
        self.window.geometry(layout.geometry)
        self.window.update_idletasks()
        if not self._styled:
            self.native.set_overlay_mode(self.window, editing=False)
            self._styled = True
        if not self._visible:
            self.native.show_no_activate(self.window)
            self._visible = True

    def hide(self):
        self.window.withdraw()
        self._visible = False

    def close(self):
        if self.window.winfo_exists():
            self.window.destroy()


class LootPanel(ttk.Frame):
    """状态由主窗口传入，主按钮只调用准备/取消回调。

    ``native`` 可注入无副作用测试替身；默认直到首次就绪才创建原生接口。
    ``close`` 只清理本组件资源，父窗口仍由主窗口管理。
    """
    def __init__(self, parent, *, on_prepare, on_cancel, settings, metrics, native=None):
        super().__init__(parent)
        self.settings, self.metrics = settings, metrics
        self._on_prepare, self._on_cancel = on_prepare, on_cancel
        self._native = native
        self._state, self._detail = 'idle', ''
        self._closed = False
        self._layout_pending = None
        self._overlay = None
        self.overlay_window = None
        self._overlay_visible = False
        self._labels = []
        self._style_prefix = 'Loot' + str(id(self)) + '.'
        self._body_font = tkfont.Font(root=self, family='Microsoft YaHei UI', size=settings.font_size)
        self._title_font = tkfont.Font(root=self, family='Microsoft YaHei UI',
                                       size=settings.font_size + 5, weight='bold')
        self._status_font = tkfont.Font(root=self, family='Microsoft YaHei UI',
                                        size=settings.font_size + 2, weight='bold')
        self._action_font = tkfont.Font(root=self, family='Microsoft YaHei UI', size=settings.font_size)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.scroll_canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0,
                                       takefocus=True, width=1, height=1)
        self.scroll_canvas.grid(row=0, column=0, sticky='nsew')
        self.scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.scroll_canvas.yview)
        self.scrollbar.grid(row=0, column=1, sticky='ns')
        self.scroll_canvas.configure(yscrollcommand=self.scrollbar.set)
        self.content = ttk.Frame(self.scroll_canvas)
        self._content_item = self.scroll_canvas.create_window(0, 0, anchor='nw', window=self.content)
        self._label(self.content, '开箱，交给空格键', font=self._title_font).pack(anchor='w', fill='x', pady=(0, 8))
        self._label(self.content, '先到游戏补给箱页，再准备本次开箱。', tone='muted').pack(anchor='w', fill='x')
        self.status_panel = RoundedPanel(self.content, settings=settings, padding=14, fit_height=True)
        self.status_panel.pack(fill='x', pady=(18, 20))
        self.status_text = tk.StringVar(self)
        self.status_detail = tk.StringVar(self)
        self.status_label = self._label(self.status_panel.body, textvariable=self.status_text,
                                        font=self._status_font, surface=True)
        self.status_label.pack(fill='x', pady=(0, 5))
        self._label(self.status_panel.body, textvariable=self.status_detail, surface=True,
                    tone='muted').pack(fill='x')
        for text in ('1  进入游戏的补给箱页', '2  点击「准备开箱」', '3  回到游戏，按 Space 开始或暂停'):
            self._label(self.content, text).pack(fill='x', pady=(0, 10))
        self._label(self.content, '结束很简单', font=self._status_font).pack(fill='x', pady=(14, 8))
        for text in (
            '开始后，其他按键、鼠标点击、滚轮或切出游戏，都会结束本次模式。',
            '尚未开始时，可以切回游戏、选择补给箱页。',
            '每 900 ms 一次空格；请在补给箱页使用，不执行购买或重掷点击。',
            '不会识别补给箱页面或剩余箱数。开完后请主动结束本次模式。',
        ):
            self._label(self.content, text, tone='muted').pack(fill='x', pady=(0, 10))
        self.actions = ttk.Frame(self)
        self.actions.grid(row=1, column=0, columnspan=2, sticky='ew')
        self.actions.columnconfigure(0, weight=1)
        self.prepare_button = ttk.Button(self.actions, text='准备开箱', command=self._prepare,
                                         style=self._style_prefix + 'Accent.TButton', width=0)
        self.cancel_button = ttk.Button(self.actions, text='结束本次模式', command=self._cancel,
                                        style=self._style_prefix + 'Cancel.TButton', width=0)
        self.prepare_button.grid(row=0, column=0, sticky='ew')
        self.content.bind('<Configure>', self._schedule_layout)
        self.scroll_canvas.bind('<Configure>', self._schedule_layout)
        self.actions.bind('<Configure>', self._schedule_layout)
        self.bind('<Destroy>', self._on_destroy, add='+')
        # 仅绑定本页面子树，不注册全局热键或修改其他页面的鼠标行为。
        for widget in self._descendants(self):
            widget.bind('<MouseWheel>', self._scroll_wheel, add='+')
        self.scroll_canvas.bind('<Up>', lambda event: self._scroll_units(-1))
        self.scroll_canvas.bind('<Down>', lambda event: self._scroll_units(1))
        self.scroll_canvas.bind('<Prior>', lambda event: self._scroll_pages(-1))
        self.scroll_canvas.bind('<Next>', lambda event: self._scroll_pages(1))
        self.apply_settings(settings, metrics)
        self.update_status('idle')

    @property
    def state(self):
        """当前开箱业务状态；主窗口用于幂等同步。"""
        return self._state

    def _label(self, parent, text='', *, textvariable=None, font=None, tone='text', surface=False):
        label = tk.Label(parent, text=text, textvariable=textvariable, font=font or self._body_font,
                          justify='left', anchor='w', borderwidth=0, highlightthickness=0,
                          padx=0, pady=0, takefocus=False)
        self._labels.append((label, tone, surface))
        return label

    @staticmethod
    def _descendants(parent):
        yield parent
        for child in parent.winfo_children():
            yield from LootPanel._descendants(child)

    def _prepare(self):
        if not self._closed and self._state in ('idle', 'error'):
            self._on_prepare()

    def _cancel(self):
        if not self._closed and self._state in _ACTIVE | {'preparing'}:
            self._on_cancel()

    def update_status(self, state, detail=''):
        if self._closed:
            return
        if state not in _STATUS:
            raise ValueError('未知的开箱助手状态：' + str(state))
        self._state, self._detail = state, detail
        title, instruction, tone = _STATUS[state]
        self.status_text.set(title)
        self.status_detail.set(instruction + ('\n' + str(detail) if detail else ''))
        self.status_label.configure(foreground=theme_colors(self.settings)[tone])
        active = state in _ACTIVE or state == 'preparing'
        self.prepare_button.state(['disabled'] if active else ['!disabled'])
        if active:
            self.prepare_button.grid_remove()
            self.cancel_button.grid(row=0, column=0, sticky='ew')
        else:
            self.cancel_button.grid_remove()
            self.prepare_button.grid(row=0, column=0, sticky='ew')
        self._refresh_overlay()
        self._schedule_layout()

    def _refresh_overlay(self):
        if self._state not in _ACTIVE:
            if self._overlay is not None:
                self._overlay.hide()
            self._overlay_visible = False
            return
        try:
            if self._overlay is None:
                if self._native is None:
                    self._native = Win32Window()
                self._overlay = _LootOverlay(self.winfo_toplevel(), self._native)
                self.overlay_window = self._overlay.window
            self._overlay.render(self._state, self.settings, self.metrics)
            self._overlay_visible = True
        except Exception:
            if self._overlay is not None:
                self._overlay.close()
            self._overlay = self.overlay_window = None
            self._overlay_visible = False
            # 主窗口负责停止服务；不得把不可见的活动模式伪装成成功。
            raise

    def apply_settings(self, settings, metrics=None):
        if self._closed:
            return
        self.settings = settings
        if metrics is not None:
            self.metrics = metrics
        colors = theme_colors(settings)
        size = settings.font_size
        self._body_font.configure(size=size)
        self._title_font.configure(size=size + 5)
        self._status_font.configure(size=size + 2)
        self.scroll_canvas.configure(background=colors['background'], yscrollincrement=self.metrics.px(24))
        for label, tone, surface in self._labels:
            label.configure(background=colors['surface' if surface else 'background'], foreground=colors[tone])
        self.status_label.configure(foreground=colors[_STATUS[self._state][2]])
        self.status_panel.apply_settings(settings, self.metrics)
        pad = self.metrics.px(12)
        self.actions.configure(padding=(pad, self.metrics.px(10), pad, self.metrics.px(8)))
        style = ttk.Style(self)
        style.configure(self._style_prefix + 'Accent.TButton', font=self._action_font,
                        padding=(pad, self.metrics.px(8)), background=colors['accent'],
                        foreground=colors['on_accent'], bordercolor=colors['accent'],
                        lightcolor=colors['accent'], darkcolor=colors['accent'], focuscolor=colors['on_accent'])
        style.map(self._style_prefix + 'Accent.TButton',
                  foreground=[('disabled', colors['muted']), ('!disabled', colors['on_accent'])],
                  background=[('disabled', colors['surface']), ('active', colors['accent']),
                              ('pressed', colors['accent'])])
        style.configure(self._style_prefix + 'Cancel.TButton', font=self._action_font,
                        padding=(pad, self.metrics.px(8)), background=colors['surface'],
                        foreground=colors['text'], bordercolor=colors['border'], focuscolor=colors['accent'])
        style.map(self._style_prefix + 'Cancel.TButton',
                  background=[('active', colors['selection']), ('pressed', colors['selection'])])
        self._refresh_overlay()
        self._schedule_layout()

    def _schedule_layout(self, event=None):
        if not self._closed and self._layout_pending is None:
            self._layout_pending = self.after_idle(self._layout_content)

    def _layout_content(self):
        self._layout_pending = None
        if self._closed:
            return
        pad = self.metrics.px(12)
        width = max(1, self.scroll_canvas.winfo_width() - pad * 2)
        self.scroll_canvas.coords(self._content_item, pad, pad)
        self.scroll_canvas.itemconfigure(self._content_item, width=width)
        for label, _, surface in self._labels:
            label.configure(wraplength=max(1, width - (self.metrics.px(28) if surface else 0)))
        # 只在窄窗口里收紧动作字号，正文仍保留用户设置的完整点值。
        available = max(1, self.actions.winfo_width() - pad * 4 - self.metrics.px(8))
        text = '结束本次模式' if self._state in _ACTIVE or self._state == 'preparing' else '准备开箱'
        size = self.settings.font_size
        self._action_font.configure(size=size)
        while size > 10 and self._action_font.measure(text) > available:
            size -= 1
            self._action_font.configure(size=size)
        bounds = self.scroll_canvas.bbox(self._content_item)
        if bounds:
            self.scroll_canvas.configure(scrollregion=(0, 0, bounds[2] + pad, bounds[3] + pad))

    def _scroll_wheel(self, event):
        if event.delta:
            return self._scroll_units(-1 if event.delta > 0 else 1)

    def _scroll_units(self, amount):
        self.scroll_canvas.yview_scroll(amount, 'units')
        return 'break'

    def _scroll_pages(self, amount):
        self.scroll_canvas.yview_scroll(amount, 'pages')
        return 'break'

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._layout_pending is not None:
            self.after_cancel(self._layout_pending)
            self._layout_pending = None
        if self._overlay is not None:
            self._overlay.close()
        self._overlay_visible = False
        self._on_prepare = self._on_cancel = None
        # <Destroy> 到达父 Frame 前，Tk 已经先销毁它的子控件。
        for button in (self.prepare_button, self.cancel_button):
            if button.winfo_exists():
                button.configure(command='')

    def _on_destroy(self, event):
        if event.widget is self:
            self.close()
