"""集中管理外观、游戏内显示、快捷键及连接，不在编辑时改动共享配置。"""
from copy import deepcopy
from dataclasses import fields
import math
import tkinter as tk
from tkinter import ttk, messagebox

from ..appearance import normalize_theme
from ..hotkeys import parse_binding, parse_function_key
from .theme import PALETTE_NAMES, style_native_window, theme_colors


OVERLAY_STYLE_NAMES = {'subtitle': '透明字幕（推荐）', 'panel': '半透明面板'}


class SettingsDialog:
    def __init__(self, window):
        self.window = window
        self.values = {}
        self.bindings = {}
        self.theme_cards = {}
        self.scroll_canvases = {}
        self._entries = {}
        self._value_pages = {}
        self.dialog = dialog = tk.Toplevel(window.root)
        dialog.withdraw()
        dialog.title('助手设置')
        dialog.transient(window.root)
        dialog.resizable(True, True)
        dialog.protocol('WM_DELETE_WINDOW', self.cancel)
        dialog.bind('<Escape>', lambda event: self.cancel())
        dialog.bind('<MouseWheel>', self._scroll_wheel)
        dialog.bind('<FocusIn>', self._reveal_focus, add='+')
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)

        metrics = getattr(window, '_layout_metrics', None)
        self._px = metrics.px if metrics is not None else lambda value: round(value)
        p = self._px
        colors = theme_colors(window.settings)
        dialog.configure(background=colors['background'])
        heading = ttk.Frame(dialog, padding=(p(20), p(16), p(20), p(10)))
        heading.grid(row=0, column=0, sticky='ew')
        ttk.Label(heading, text='助手设置', style='Heading.TLabel').pack(anchor='w')

        self.notebook = ttk.Notebook(dialog)
        self.notebook.grid(row=1, column=0, sticky='nsew', padx=p(12))
        self.notebook.enable_traversal()
        appearance = self._page('外观')
        overlay = self._page('游戏内')
        keys = self._page('快捷键')
        connection = self._page('连接')
        self._appearance(appearance)
        self._overlay(overlay)
        self._hotkeys(keys)
        self._connection(connection)
        recorder=getattr(window.controller,'diagnostics',None)
        if recorder is not None:
            from .diagnostics_panel import DiagnosticsPanel
            diagnosis=self._page('诊断')
            self.diagnostics_panel=DiagnosticsPanel(diagnosis,recorder,root=window.root)
            self.diagnostics_panel.pack(fill='both',expand=True)

        footer = ttk.Frame(dialog, padding=(p(12), p(12)))
        footer.grid(row=2, column=0, sticky='ew')
        footer.columnconfigure(0, weight=1)
        ttk.Button(footer, text='取消', width=0, command=self.cancel).grid(row=0, column=1, padx=(0, p(8)))
        ttk.Button(footer, text='保存设置', width=0, style='Accent.TButton', command=self.save).grid(row=0, column=2)
        self._fit_window(metrics)
        style_native_window(dialog, colors)
        dialog.deiconify()

    def _fit_window(self, metrics):
        if metrics is None:
            bounds = (0, 0, self.dialog.winfo_screenwidth(), self.dialog.winfo_screenheight())
        else:
            bounds = metrics.work_area
        left, top, right, bottom = bounds
        available_width = max(1, right - left - self._px(16))
        available_height = max(1, bottom - top - self._px(32))
        width = min(self._px(740), available_width)
        height = min(self._px(720), available_height)
        self.dialog.minsize(min(width, self._px(400)), min(height, self._px(260)))
        self.dialog.maxsize(available_width, available_height)
        self.dialog.geometry(f'{width}x{height}+{left + (right-left-width)//2}+{top + (bottom-top-height)//2}')

    def _page(self, title):
        page = ttk.Frame(self.notebook)
        self.notebook.add(page, text=title)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        canvas = tk.Canvas(page, borderwidth=0, highlightthickness=0,
                           background=theme_colors(self.window.settings)['background'], takefocus=False)
        canvas.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(page, orient='vertical', command=canvas.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(page, orient='horizontal', command=canvas.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        body = ttk.Frame(canvas, padding=self._px(16))
        body.columnconfigure(0, weight=1)
        content = canvas.create_window((0, 0), window=body, anchor='nw')

        def resize(event=None):
            width = max(canvas.winfo_width(), body.winfo_reqwidth())
            canvas.itemconfigure(content, width=width)
            canvas.configure(scrollregion=canvas.bbox('all'))

        body.bind('<Configure>', resize)
        canvas.bind('<Configure>', resize)
        self.scroll_canvases[str(page)] = canvas
        body._settings_page = str(page)
        return body

    def _section(self, parent, title, description=None):
        frame = ttk.Frame(parent)
        frame.pack(fill='x', pady=(0, self._px(18)))
        ttk.Label(frame, text=title, style='Heading.TLabel').pack(anchor='w', pady=(0, self._px(8)))
        if description:
            self._note(frame, description)
        return frame

    def _note(self, parent, text):
        label = ttk.Label(parent, text=text, style='Muted.TLabel', justify='left', wraplength=self._px(510))
        label.pack(anchor='w', fill='x', pady=(0, self._px(10)))
        return label

    def _check(self, parent, name, label):
        value = tk.BooleanVar(master=self.dialog, value=getattr(self.window.settings, name))
        self.values[name] = value
        ttk.Checkbutton(parent, text=label, variable=value).pack(anchor='w', pady=self._px(4))

    def _number(self, parent, page, name, label, unit):
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=self._px(5))
        row.columnconfigure(0, weight=1)
        ttk.Label(row, text=label).grid(row=0, column=0, sticky='w', padx=(0, self._px(16)))
        value = tk.StringVar(master=self.dialog, value=str(getattr(self.window.settings, name)))
        self.values[name] = value
        entry = ttk.Entry(row, textvariable=value, width=8)
        entry.grid(row=0, column=1, sticky='e')
        ttk.Label(row, text=unit, style='Muted.TLabel', width=11).grid(row=0, column=2, sticky='w', padx=(self._px(8), 0))
        self._entries[name] = entry
        self._value_pages[name] = page._settings_page

    def _appearance(self, parent):
        section = self._section(parent, '界面配色', '选择色卡查看配色；保存后同步主界面、设置和浮窗。')
        value = tk.StringVar(master=self.dialog, value=normalize_theme(self.window.settings.color_theme))
        self.values['color_theme'] = value
        descriptions = {'moonlight': '白色内容区，暖灰侧栏与细边框', 'graphite': '中性石墨底色，低亮度阅读',
                        'mint': '浅灰青底色，安静柔和'}
        for name, label in PALETTE_NAMES.items():
            c = theme_colors(name)
            card = tk.Frame(section, background=c['surface'], highlightthickness=1,
                            highlightbackground=c['border'], padx=self._px(12), pady=self._px(10))
            card.pack(fill='x', pady=(0, self._px(8)))
            title = tk.Radiobutton(card, text=label, variable=value, value=name,
                                   command=self._refresh_theme_cards, background=c['surface'],
                                   foreground=c['text'], activebackground=c['surface'],
                                   activeforeground=c['text'], selectcolor=c['field'],
                                   highlightbackground=c['surface'], highlightcolor=c['accent'],
                                   highlightthickness=self._px(1), anchor='w')
            title.pack(anchor='w')
            tk.Label(card, text=descriptions.get(name, ''), background=c['surface'],
                     foreground=c['muted'], anchor='w').pack(anchor='w', pady=(self._px(2), self._px(8)))
            samples = tk.Frame(card, background=c['surface'])
            samples.pack(anchor='w')
            for role in ('background', 'field', 'accent'):
                tk.Frame(samples, background=c[role], width=self._px(52), height=self._px(10)).pack(
                    side='left', padx=(0, self._px(5)))
            self.theme_cards[name] = card
            pending = [card]
            while pending:
                target = pending.pop()
                pending.extend(target.winfo_children())
                if target is not title:
                    target.bind('<Button-1>', lambda event, choice=name: self.select_theme(choice))
        self._refresh_theme_cards()

        section = self._section(parent, '主窗口')
        self._number(section, parent, 'font_size', '内容字号', '10–32')
        self._number(section, parent, 'alpha', '窗口透明度', '0.35–1')
        self._check(section, 'topmost', '主窗口置顶')
        self._check(section, 'locked', '锁定主窗口位置')
        self.compact = tk.BooleanVar(master=self.dialog, value=self.window.settings.mode == 'compact')
        ttk.Checkbutton(section, text='精简模式', variable=self.compact).pack(anchor='w', pady=self._px(4))

    def select_theme(self, name):
        if name in PALETTE_NAMES:
            self.values['color_theme'].set(name)
            self._refresh_theme_cards()

    def _refresh_theme_cards(self):
        selected = self.values['color_theme'].get()
        for name, card in self.theme_cards.items():
            colors = theme_colors(name)
            card.configure(highlightbackground=colors['accent'] if name == selected else colors['border'])

    def _overlay(self, parent):
        section = self._section(parent, '游戏内显示', '字幕默认显示在屏幕底部；小输入条独立居中，打开时字幕暂时隐藏。')
        self._check(section, 'overlay_enabled', '显示译文悬浮窗')
        style = tk.StringVar(master=self.dialog, value=self.window.settings.overlay_style)
        self.values['overlay_style'] = style
        for name, label in OVERLAY_STYLE_NAMES.items():
            ttk.Radiobutton(section, text=label, variable=style, value=name).pack(
                anchor='w', pady=self._px(4))
        self._note(section, '透明字幕：背景透明、文字带暗边，仅显示最新 1 条、最多 2 行，默认停留 6 秒。')
        self._check(section, 'overlay_original', '显示原文（译文、原文各 1 行）')
        for name, label, unit in (
                ('overlay_font_size', '译文字号', '10–32'),
                ('overlay_alpha', '面板不透明度', '0.35–1'),
                ('overlay_width', '浮窗宽度', '280–900'),
                ('overlay_timeout', '消息停留', '5–120 秒')):
            self._number(section, parent, name, label, unit)
        self._note(section, '面板不透明度用于半透明面板和位置校准，不会淡化透明字幕的文字。完整内容可在主窗口查看。')
        section = self._section(parent, '位置校准')
        ttk.Button(section, text='保存并使用底部字幕布局', command=self.use_bottom_preset).pack(fill='x', pady=self._px(4))
        ttk.Button(section, text='保存并调整浮窗位置', command=self.edit_overlay).pack(fill='x', pady=self._px(4))
        self._note(section, '进入调整后拖动浮窗，再点击“完成调整”。')

    def _hotkeys(self, parent):
        section = self._section(parent, '全局快捷键', '留空可禁用；请使用不同组合，例如 Ctrl+T 或 Ctrl+Alt+T。')
        for action, label in (('focus', '呼出小输入条'), ('translate', '翻译主窗口中文'), ('copy', '复制主窗口译文')):
            row = ttk.Frame(section)
            row.pack(fill='x', pady=self._px(6))
            ttk.Label(row, text=label).pack(anchor='w', pady=(0, self._px(5)))
            value = tk.StringVar(master=self.dialog, value=self.window.settings.hotkeys.get(action, ''))
            self.bindings[action] = value
            entry = ttk.Entry(row, textvariable=value, width=24)
            entry.pack(fill='x')
            self._entries[action] = entry
            self._value_pages[action] = parent._settings_page
        section = self._section(parent, '动作连按', '在动作连按页选择 S 或 Y＋S；游戏前台单独按此键开始，再按取消。')
        value = tk.StringVar(master=self.dialog, value=self.window.settings.repeat_hotkey)
        self.values['repeat_hotkey'] = value
        ttk.Label(section, text='开始 / 取消快捷键').pack(anchor='w', pady=(0, self._px(5)))
        entry = ttk.Combobox(section, textvariable=value, state='readonly',
                             values=tuple(f'F{number}' for number in range(1, 13)), width=10)
        entry.pack(anchor='w')
        self._entries['repeat_hotkey'] = entry
        self._value_pages['repeat_hotkey'] = parent._settings_page
        self._note(section, '默认 F8；可选 F1–F12。保存改键会结束本次连按，回游戏新按所选键即可开始。')
        section = self._section(parent, '输入条操作')
        for title, description in (('Enter', '翻译并复制'), ('Esc', '取消本次输入'), ('Ctrl+Tab', '切换韩语 / 英语')):
            row = ttk.Frame(section)
            row.pack(fill='x', pady=self._px(5))
            ttk.Label(row, text=title, width=12).pack(side='left')
            ttk.Label(row, text=description, style='Muted.TLabel').pack(side='left')

    def _connection(self, parent):
        section = self._section(parent, '翻译服务', '配置个人密钥后即可翻译；未配置时继续接收原文。')
        if hasattr(self.window.controller, 'account_setup'):
            ttk.Button(section, text='个人阿里云密钥', command=self.window.configure_account).pack(fill='x', pady=self._px(4))
            self._note(section, '配置、替换或删除自己的阿里云密钥。')
        ttk.Button(section, text='查询剩余额度', command=self.window.open_cloud_quota).pack(fill='x', pady=self._px(4))
        self._note(section, '在默认浏览器打开阿里云控制台，登录后查看剩余额度与实际用量。')
        section = self._section(parent, '采集连接', '启动后自动接入，换局会自动恢复。以下操作立即生效。')
        for label, command in (
                ('手动重新连接', self.window.controller.source.reconnect),
                ('停止采集', self.window.stop), ('恢复采集', self.window.start),
                ('选择游戏进程', self.window.choose_process)):
            ttk.Button(section, text=label, command=command).pack(fill='x', pady=self._px(4))
        self._note(parent, '界面最近 500 条历史仅保留在本次运行中。基础日志不含正文；主动开启详细诊断后会记录限长正文与译文。')

    def _scroll_wheel(self, event):
        canvas = self.scroll_canvases.get(self.notebook.select())
        if canvas is None or not event.delta:
            return
        move = -1 if event.delta > 0 else 1
        if event.state & 0x0001:
            canvas.xview_scroll(move, 'units')
        else:
            canvas.yview_scroll(move, 'units')
        return 'break'

    def _reveal_focus(self, event):
        canvas = self.scroll_canvases.get(self.notebook.select())
        if canvas is None:
            return
        widget = event.widget
        ancestor = widget
        while ancestor is not None and ancestor is not canvas:
            ancestor = getattr(ancestor, 'master', None)
        if ancestor is None:
            return
        self.dialog.after_idle(lambda: self._scroll_to_widget(canvas, widget))

    @staticmethod
    def _scroll_to_widget(canvas, widget):
        if not canvas.winfo_exists() or not widget.winfo_exists():
            return
        bounds = canvas.bbox('all')
        if not bounds:
            return
        top = canvas.canvasy(widget.winfo_rooty() - canvas.winfo_rooty())
        bottom = top + widget.winfo_height()
        visible_top = canvas.canvasy(0)
        visible_bottom = visible_top + canvas.winfo_height()
        if top < visible_top:
            canvas.yview_moveto(max(0, top) / max(1, bounds[3]))
        elif bottom > visible_bottom:
            canvas.yview_moveto(max(0, bottom - canvas.winfo_height()) / max(1, bounds[3]))
        left = canvas.canvasx(widget.winfo_rootx() - canvas.winfo_rootx())
        right = left + widget.winfo_width()
        visible_left = canvas.canvasx(0)
        if left < visible_left:
            canvas.xview_moveto(max(0, left) / max(1, bounds[2]))
        elif right > visible_left + canvas.winfo_width():
            canvas.xview_moveto(max(0, right - canvas.winfo_width()) / max(1, bounds[2]))

    def cancel(self):
        self.dialog.destroy()

    def edit_overlay(self):
        self.values['overlay_enabled'].set(True)
        if not self.save():
            return
        self.window.overlay.set_editing(True)

    def use_chat_preset(self):
        self.values['overlay_enabled'].set(True)
        if not self.save(before_apply=self.window.overlay.use_chat_preset):
            return
        self.window.overlay.set_editing(True)
        self.window.quick_input.refresh_layout(self.window._layout_metrics, force=True)

    def use_bottom_preset(self):
        self.values['overlay_enabled'].set(True)
        self.values['overlay_style'].set('subtitle')
        if not self.save(before_apply=self.window.overlay.use_bottom_preset):
            return
        self.window.quick_input.refresh_layout(self.window._layout_metrics, force=True)

    def save(self, before_apply=None):
        invalid_key = None
        try:
            bindings = {}
            parsed = set()
            for action, variable in self.bindings.items():
                text = variable.get().strip()
                if not text:
                    continue
                invalid_key = action
                binding = parse_binding(text)
                if binding in parsed:
                    raise ValueError('不同操作不能使用同一个快捷键。')
                parsed.add(binding)
                bindings[action] = text
            values = {key: variable.get() for key, variable in self.values.items()}
            if 'repeat_hotkey' in values:
                invalid_key = 'repeat_hotkey'
                parse_function_key(values['repeat_hotkey'])
                values['repeat_hotkey'] = values['repeat_hotkey'].strip().upper()
            if 'overlay_style' in values:
                invalid_key = 'overlay_style'
                selected = next((key for key, label in OVERLAY_STYLE_NAMES.items()
                                 if values['overlay_style'] in (key, label)), None)
                if selected is None:
                    raise ValueError('请选择透明字幕或半透明面板。')
                values['overlay_style'] = selected
            if 'color_theme' in values:
                invalid_key = 'color_theme'
                selected = next((key for key, label in PALETTE_NAMES.items()
                                 if values['color_theme'] in (key, label)), None)
                if selected is None:
                    raise ValueError('请选择月白蓝、石墨紫或雾青。')
                values['color_theme'] = selected
            limits = {
                'overlay_font_size': (10, 32, int, '译文字号'),
                'overlay_alpha': (.35, 1, float, '面板不透明度'),
                'overlay_width': (280, 900, int, '浮窗宽度'),
                'overlay_timeout': (5, 120, int, '消息停留秒数'),
                'font_size': (10, 32, int, '内容字号'),
                'alpha': (.35, 1, float, '窗口透明度'),
            }
            for key, (low, high, convert, label) in limits.items():
                invalid_key = key
                try:
                    value = convert(values[key])
                except (ValueError, TypeError, OverflowError):
                    raise ValueError(f'{label}请输入 {low}–{high} 范围内的数值。') from None
                if not math.isfinite(value) or not low <= value <= high:
                    raise ValueError(f'{label}请输入 {low}–{high} 范围内的数值。')
                values[key] = value
            invalid_key = None
            # 布局应用可能归一化位置并修改字典，完整深拷贝才能可靠回滚。
            previous = {field.name: deepcopy(getattr(self.window.settings, field.name))
                        for field in fields(self.window.settings)}
            runtime = (self.window.capture_settings_state()
                       if hasattr(self.window, 'capture_settings_state') else None)
            values.update(mode='compact' if self.compact.get() else 'normal', hotkeys=bindings)

            def restore():
                for key, value in previous.items():
                    setattr(self.window.settings, key, deepcopy(value))

            try:
                for key, value in values.items():
                    setattr(self.window.settings, key, value)
                if before_apply is not None:
                    before_apply()
                self.window.apply_settings()
                self.window.save_settings()
            except (ValueError, OSError):
                restore()
                try:
                    self.window.apply_settings()
                finally:
                    try:
                        if runtime is not None:
                            self.window.restore_settings_state(runtime)
                    finally:
                        # 重新应用布局仍可能调整字段，事务失败必须保留原值。
                        restore()
                raise
            self.dialog.destroy()
            return True
        except (ValueError, OSError) as failure:
            if invalid_key is not None and invalid_key in getattr(self, '_entries', {}):
                self.notebook.select(self._value_pages[invalid_key])
                self._entries[invalid_key].focus_set()
            messagebox.showerror('设置未保存', str(failure), parent=self.dialog)
            return False
