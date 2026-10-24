"""首选天赋的显式开关和状态；本组件不读取游戏、不产生输入。"""
import tkinter as tk
from tkinter import ttk

from .rounded_panel import RoundedPanel
from .theme import theme_colors


_TITLES = {
    'disabled': '自动选择已关闭', 'waiting': '等待可用的游戏状态',
    'ready': '已连接，等待天赋待选', 'deferred': '等待合适的操作时机',
    'sent': '已触发首选快捷键，正在确认', 'confirming': '正在确认天赋选择',
    'confirmed': '已确认该层天赋已选择', 'unconfirmed': '本层尚未选中，请手动处理',
    'error': '本次天赋操作未完成，请手动检查',
    'idle': '已连接，当前没有待选天赋', 'unknown': '等待可信的游戏状态',
    'blocked': '本层尚未选中，请手动处理',
}


class TalentPanel(ttk.Frame):
    def __init__(self, parent, *, settings, metrics, on_toggle, on_retry=None):
        super().__init__(parent, padding=12)
        self.settings, self.metrics, self._on_toggle = settings, metrics, on_toggle
        self._on_retry = on_retry
        self._running = None
        self._state, self._detail, self._tier_id = 'waiting', '', None
        self._last_render = None
        self._closed = False
        self._labels = []
        self.columnconfigure(0, weight=1)
        self.enabled = tk.BooleanVar(self, value=settings.talent_enabled)
        self.toggle = ttk.Checkbutton(self, text='自动选择游戏内的爱心天赋',
                                      variable=self.enabled, command=self._toggle)
        self.toggle.grid(row=0, column=0, sticky='w', pady=(0, 8))
        self._label(self, '先在游戏里标好爱心。出现天赋待选时，助手触发一次 Ctrl＋反引号，并回读确认。').grid(
            row=1, column=0, sticky='ew', pady=(0, 14))
        self.card = RoundedPanel(self, settings=settings, padding=14, fit_height=True)
        self.card.grid(row=2, column=0, sticky='ew')
        self.title = tk.StringVar(self)
        self.detail = tk.StringVar(self)
        self.last_result = tk.StringVar(self)
        self._label(self.card.body, variable=self.title, surface=True, heading=True).pack(fill='x')
        self._label(self.card.body, variable=self.detail, surface=True).pack(fill='x', pady=(6, 0))
        self._label(self.card.body, variable=self.last_result, surface=True).pack(fill='x', pady=(6, 0))
        self.retry_button = ttk.Button(self.card.body, text='重新连接', command=self._retry,
                                      style='Surface.TButton')
        self._label(self, '反引号是 Esc 下方、数字 1 左边的键。若改过游戏里的“快速选择首选天赋”快捷键，请先恢复为 Ctrl＋反引号。').grid(
            row=3, column=0, sticky='ew', pady=(14, 8))
        self._label(self, '本层未标爱心或快捷键未生效时，不会反复按键。请手动选择该层；下一层会继续检测。').grid(
            row=4, column=0, sticky='ew', pady=(0, 8))
        self._label(self, '乱斗与英文客户端尚待实机验收。无法确认本地玩家、对局或天赋状态时暂停；切出游戏、使用开箱／连按／快捷输入时延后。').grid(
            row=5, column=0, sticky='ew')
        self.bind('<Configure>', self._layout, add='+')
        self.apply_settings(settings)
        self.update_status('disabled' if not settings.talent_enabled else 'waiting')

    def _label(self, parent, text='', *, variable=None, surface=False, heading=False):
        label = tk.Label(parent, text=text, textvariable=variable, anchor='w', justify='left',
                         borderwidth=0, highlightthickness=0, wraplength=400)
        self._labels.append((label, surface, heading))
        return label

    def _toggle(self):
        if self._closed:return
        try:
            self._on_toggle(self.enabled.get())
        except (OSError, ValueError) as failure:
            self.enabled.set(self.settings.talent_enabled)
            self.detail.set('未保存：' + str(failure))

    def _retry(self):
        if self._closed or self._on_retry is None:return
        try:
            if self._on_retry() is False:
                self.detail.set('上一次运行仍在退出，请稍后重新连接。')
        except (OSError, ValueError) as failure:
            self.detail.set('重新连接失败：' + str(failure))

    def set_running(self, running):
        """实际运行状态由服务提供；不改变保存的开启偏好。"""
        if self._closed or self._running is bool(running):return
        self._running = bool(running)
        self._render_status()

    def update_status(self, state, detail='', tier_id=None):
        if self._closed:return
        self._state, self._detail, self._tier_id = state, detail, tier_id
        self._render_status()

    def _render_status(self):
        signature = (self.settings.talent_enabled, self._running,
                     self._state, self._detail, self._tier_id)
        if signature == self._last_render:return
        self._last_render = signature
        state, detail = self._state, self._detail
        title = _TITLES.get(state, '天赋辅助状态')
        if not self.settings.talent_enabled:
            title, detail = _TITLES['disabled'], detail or '开启后自动检测，无需每把重新准备。'
        elif self._running is False:
            title = '运行已停止 · 开启偏好已保留'
            detail = (detail + '\n' if detail else '') + '点击“重新连接”恢复检测。'
        elif not detail:
            detail = '正在等待可信的本地玩家与天赋状态。'
        if self.title.get() != title:self.title.set(title)
        if self.detail.get() != detail:self.detail.set(detail)
        if state in ('confirmed', 'blocked', 'unconfirmed', 'error'):
            result = '最近结果：' + (self._detail or _TITLES[state])
            if self.last_result.get() != result:self.last_result.set(result)
        if self.settings.talent_enabled and self._running is False and self._on_retry is not None:
            self.retry_button.pack(anchor='w', pady=(10, 0))
        else:self.retry_button.pack_forget()

    def _layout(self, event=None):
        width = max(100, self.winfo_width() - self.metrics.px(30))
        for label, surface, _ in self._labels:
            label.configure(wraplength=max(100, width - (self.metrics.px(28) if surface else 0)))

    def apply_settings(self, settings, metrics=None):
        if self._closed:return
        self.settings = settings
        if metrics is not None:self.metrics = metrics
        self.enabled.set(settings.talent_enabled)
        colors = theme_colors(settings)
        for label, surface, heading in self._labels:
            label.configure(background=colors['surface' if surface else 'background'],
                            foreground=colors['text' if heading else 'muted'],
                            font=('Microsoft YaHei UI', settings.font_size + (2 if heading else 0),
                                  'bold' if heading else 'normal'))
        self.card.apply_settings(settings, self.metrics)
        self._layout()
        self._render_status()

    def close(self):
        if self._closed:return
        self._closed = True
        self._on_toggle = self._on_retry = None
        if self.retry_button.winfo_exists():self.retry_button.configure(command='')

    def destroy(self):
        if getattr(self, '_destroyed', False):return
        self._destroyed = True
        self.close()
        descendants = []
        pending = list(self.children.values())
        while pending:
            child = pending.pop()
            descendants.append(child)
            pending.extend(child.children.values())
        super().destroy()
        # Tk销毁只清children；自建控件缓存与master回边仍会形成Python循环，
        # 让StringVar及Tcl解释器拖到后台GC析构。仅在控件已销毁后断开回边。
        self._labels.clear()
        for child in descendants:child.master = None
        self.master = None
