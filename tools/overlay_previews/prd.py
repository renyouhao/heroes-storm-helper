"""A 方案：只用于离线 A/B 体验的柔雾紫 Tk 悬浮窗。

本模块不读配置、游戏或云服务，不注册全局热键。唯一可写的外部资源是
用户明确点击“复制译文”时的系统剪贴板。所有演示结果来自宿主 translate。
"""
from __future__ import annotations

import math
import sys
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable

from hots_chat.layout import DisplayMetrics


_KEY = '#010203'
_INK = '#17132f'
_MUTED = '#756181'
_PURPLE = '#7843f4'
_BORDER = '#e5ddf1'
_SURFACE = '#fcfaff'
_FONT = 'Microsoft YaHei UI'


def _rounded(canvas, box, radius, *, fill, outline='', width=1, tags=()):
    x, y, right, bottom = box
    radius = max(0, min(radius, (right - x) / 2, (bottom - y) / 2))
    points = (x + radius, y, right - radius, y, right, y, right, y + radius,
              right, bottom - radius, right, bottom, right - radius, bottom,
              x + radius, bottom, x, bottom, x, bottom - radius, x, y + radius, x, y)
    return canvas.create_polygon(points, smooth=True, splinesteps=24,
                                 fill=fill, outline=outline, width=width, tags=tags)


def _geometry(mode, area, scale):
    """area 已为 Tk 屏幕坐标；96-DPI 设计尺寸只缩放一次。"""
    if mode not in ('subtitle', 'panel', 'input'):
        raise ValueError('未知演示模式：' + str(mode))
    left, top, available_width, available_height = (int(value) for value in area)
    if available_width < 1 or available_height < 1:
        raise ValueError('演示舞台必须有正的宽度和高度。')
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('缩放必须是有限正数。')
    base_width, base_height = {'subtitle': (880, 143), 'panel': (390, 356), 'input': (1000, 252)}[mode]
    margin = min(round(22 * scale), max(0, (min(available_width, available_height) - 30) // 8))
    actual_scale = min(scale, (available_width - margin * 2) / base_width,
                       (available_height - margin * 2) / base_height)
    actual_scale = max(.01, actual_scale)
    width, height = max(1, round(base_width * actual_scale)), max(1, round(base_height * actual_scale))
    if mode == 'panel':
        x, y = left + available_width - margin - width, top + margin
    elif mode == 'subtitle':
        x, y = left + (available_width - width) // 2, top + available_height - height - margin - round(18 * actual_scale)
    else:
        x, y = left + (available_width - width) // 2, top + (available_height - height) // 2
    x = min(max(x, left), left + available_width - width)
    y = min(max(y, top), top + available_height - height)
    return width, height, x, y, actual_scale


class PrdPreview:
    """三个实际 Toplevel；show 同一模式只重排，不重置编辑或翻译状态。"""

    def __init__(self, root, *, metrics: DisplayMetrics, messages: list[dict],
                 notify: Callable, translate: Callable):
        self.root, self.metrics = root, metrics
        self.notify, self.translate = notify, translate
        self.messages = [dict(message) for message in messages]
        self.windows = {}
        self.canvases = {}
        self._layouts = {}
        self._fonts = {}
        self._mode = None
        self._closed = False
        self._light = False
        self._area = None
        self._timers = set()
        self._translation_timer = None
        self._revision = 0
        self._busy = False
        self._target = 'en'
        self._result = ''
        self._result_identity = None
        self._status = '离线预置译文演示；输入后点击翻译。'
        self._error = False
        self._syncing = False
        self._drag = None
        self._return_armed = False
        self._return_composing = False
        self._native = None
        self._ime = None
        self._native_error = ''
        self._ime_error = ''
        self._draft = '我们等大招好了再开团'
        self._last_layout = None
        self._button_regions = {}
        self._truncated = {}
        self._input_box = None
        self._input_hint = None
        self._native_styled = set()
        try:
            if sys.platform == 'win32':
                from hots_chat.ui.native_windows import Win32Window
                self._native = Win32Window()
            for mode in ('subtitle', 'panel', 'input'):
                self._create_window(mode)
            self._create_input()
        except Exception:
            self.close()
            raise

    def _create_window(self, mode):
        window = tk.Toplevel(self.root)
        window.withdraw()
        window.title({'subtitle': 'A · 柔雾紫字幕 · 离线演示',
                      'panel': 'A · 柔雾紫消息面板 · 离线演示',
                      'input': 'A · 柔雾紫快捷输入 · 离线演示'}[mode])
        window.overrideredirect(True)
        window.attributes('-topmost', True)
        window.configure(background=_KEY if mode == 'subtitle' else _SURFACE)
        if mode == 'subtitle':
            try:
                window.attributes('-transparentcolor', _KEY)
            except tk.TclError:
                self._native_error = '当前平台不支持透明色，字幕背景使用深色。'
        canvas = tk.Canvas(window, highlightthickness=0, borderwidth=0,
                           background=_KEY if mode == 'subtitle' else _SURFACE,
                           takefocus=False)
        canvas.pack(fill='both', expand=True)
        window.protocol('WM_DELETE_WINDOW', self.hide)
        window.bind('<Escape>', lambda event: self._escape())
        canvas.bind('<ButtonPress-1>', lambda event, current=mode: self._drag_start(current, event))
        canvas.bind('<B1-Motion>', self._drag_move)
        canvas.bind('<ButtonRelease-1>', self._drag_end)
        self.windows[mode], self.canvases[mode] = window, canvas

    def _create_input(self):
        self.entry = tk.Text(self.windows['input'], wrap='word', undo=True, maxundo=40,
                             background='#ffffff', foreground=_INK, insertbackground=_PURPLE,
                             selectbackground='#e6d8ff', selectforeground=_INK,
                             relief='flat', borderwidth=0, highlightthickness=0,
                             padx=0, pady=0, spacing1=0, spacing2=1, spacing3=0,
                             exportselection=False)
        self.entry.insert('1.0', self._draft)
        self.entry.edit_modified(False)
        self.entry.bind('<<Modified>>', self._on_edit)
        self.entry.bind('<Control-KeyPress-Return>', self._control_return)
        self.entry.bind('<Control-KeyPress-KP_Enter>', self._control_return)
        self.entry.bind('<KeyRelease-Return>', self._release_return)
        self.entry.bind('<KeyRelease-KP_Enter>', self._release_return)
        self.entry.bind('<FocusOut>', self._focus_out)
        self.entry.bind('<Escape>', lambda event: self._escape())
        if sys.platform == 'win32':
            try:
                from hots_chat.ui.input_native import WindowsInputNative
                self._ime = WindowsInputNative()
                self._ime.attach_ime(self.entry.winfo_id())
            except (OSError, tk.TclError) as failure:
                self._ime_error = str(failure)
                if self._ime is not None:
                    try:
                        self._ime.detach_ime()
                    except OSError:
                        pass
                    self._ime = None

    def _font(self, size, scale, weight='normal'):
        pixels = max(1, round(size * scale))
        key = (pixels, weight)
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(root=self.root, family=_FONT, size=-pixels, weight=weight)
        return self._fonts[key]

    def _text(self, mode, x, y, text, *, size=14, color=_INK, anchor='nw',
              width=None, weight='normal', max_height=None, tag=None):
        canvas = self.canvases[mode]
        scale = self._layouts[mode][4]
        font = self._font(size, scale, weight)
        options = dict(text=str(text), font=font, fill=color, anchor=anchor,
                       tags=('content',) + ((tag,) if tag else ()))
        if width is not None:
            options['width'] = max(1, round(width * scale))
        item = canvas.create_text(x * scale, y * scale, **options)
        if max_height is not None:
            full = str(text)
            bounds = canvas.bbox(item)
            # Canvas 的中文字体 bbox 可比“设计字号”高出数像素；高度下限
            # 必须容纳字体的真实单行，否则二分截断连一个字也容不下，只剩“…” 。
            maximum = max(max_height * scale, font.metrics('linespace') + 4)
            if bounds and bounds[3] - bounds[1] > maximum:
                low, high = 0, len(full)
                while low < high:
                    middle = (low + high + 1) // 2
                    canvas.itemconfigure(item, text=full[:middle] + '…')
                    bounds = canvas.bbox(item)
                    if bounds and bounds[3] - bounds[1] <= maximum:
                        low = middle
                    else:
                        high = middle - 1
                canvas.itemconfigure(item, text=full[:low] + '…')
                self._truncated.setdefault(mode, []).append(tag or 'text')
        return item

    def _box(self, mode, box, radius, *, fill, outline='', width=1, tag=None):
        scale = self._layouts[mode][4]
        return _rounded(self.canvases[mode], tuple(value * scale for value in box),
                        radius * scale, fill=fill, outline=outline,
                        width=max(1, round(width * scale)), tags=('content',) + ((tag,) if tag else ()))

    def _line(self, mode, coordinates, color, width=1, tag=None):
        scale = self._layouts[mode][4]
        return self.canvases[mode].create_line(*(value * scale for value in coordinates),
                                              fill=color, width=max(1, round(width * scale)),
                                              tags=('content',) + ((tag,) if tag else ()))

    def _icon(self, mode, kind, x, y, *, color, tag=None):
        if kind == 'close':
            self._line(mode, (x - 4, y - 4, x + 4, y + 4), color, 1.3, tag)
            self._line(mode, (x + 4, y - 4, x - 4, y + 4), color, 1.3, tag)
        elif kind == 'minus':
            self._line(mode, (x - 5, y, x + 5, y), color, 1.2, tag)
        elif kind == 'zap':
            scale = self._layouts[mode][4]
            self.canvases[mode].create_polygon(
                *[value * scale for value in (x + 2, y - 9, x - 7, y + 2,
                                              x - 1, y + 2, x - 2, y + 9,
                                              x + 7, y - 3, x + 1, y - 3)],
                fill=color, outline='', tags=('content',) + ((tag,) if tag else ()))
        elif kind == 'copy':
            self._box(mode, (x - 4, y - 4, x + 5, y + 7), 1.5,
                      fill='', outline=color, tag=tag)
            self._line(mode, (x - 6, y + 3, x - 8, y + 3, x - 8, y - 7,
                             x + 1, y - 7, x + 1, y - 5), color, 1.2, tag)
        elif kind == 'translate':
            self._line(mode, (x - 8, y - 5, x + 1, y - 5), color, 1.2, tag)
            self._line(mode, (x - 4, y - 8, x - 4, y - 5), color, 1.2, tag)
            self._line(mode, (x - 1, y - 5, x - 3, y + 1, x - 8, y + 5), color, 1.2, tag)
            self._line(mode, (x - 7, y - 4, x - 3, y + 2, x + 1, y + 5), color, 1.2, tag)
            self._line(mode, (x + 1, y + 7, x + 5, y - 3, x + 9, y + 7), color, 1.2, tag)
            self._line(mode, (x + 3, y + 3, x + 7, y + 3), color, 1.2, tag)
        elif kind == 'message':
            self._box(mode, (x - 6, y - 6, x + 6, y + 4), 2,
                      fill='', outline=color, tag=tag)
            self._line(mode, (x - 3, y + 4, x - 6, y + 7, x - 6, y + 2), color, 1.2, tag)

    def _button(self, mode, box, label, command, *, kind='secondary', enabled=True,
                icon=None, tag='button'):
        x, y, right, bottom = box
        scale = self._layouts[mode][4]
        if kind == 'primary':
            fill, text_color = (_PURPLE, '#ffffff') if enabled else ('#c7b1ef', '#f9f6ff')
        elif kind == 'pill':
            fill, text_color = '#ddd0ff', '#6731dc'
        else:
            fill, text_color = ('#eee6fb', '#715a8a') if enabled else ('#f0ecf5', '#b5a9c2')
        self._box(mode, box, 9 if kind != 'pill' else 15, fill=fill, tag=tag)
        center_x, center_y = (x + right) / 2, (y + bottom) / 2
        if icon:
            self._icon(mode, icon, center_x - 29, center_y, color=text_color, tag=tag)
        self._text(mode, center_x + (9 if icon else 0), center_y, label,
                   size=12, color=text_color, anchor='center', tag=tag)
        self._button_regions.setdefault(mode, {})[tag] = dict(box=tuple(round(v * scale) for v in box), enabled=enabled)
        if enabled:
            canvas = self.canvases[mode]
            canvas.tag_bind(tag, '<Button-1>', lambda event: self._click(command))
            canvas.tag_bind(tag, '<Enter>', lambda event: canvas.configure(cursor='hand2'))
            canvas.tag_bind(tag, '<Leave>', lambda event: canvas.configure(cursor=''))

    def _click(self, command):
        if not self._closed:
            command()
        return 'break'

    def _reset_canvas(self, mode):
        self.canvases[mode].delete('all')
        self.canvases[mode].configure(cursor='')
        self._button_regions[mode] = {}
        self._truncated[mode] = []

    def _render(self, mode):
        self._reset_canvas(mode)
        if mode == 'subtitle':
            self._render_subtitle()
        elif mode == 'panel':
            self._render_panel()
        else:
            self._render_input()

    def _render_subtitle(self):
        mode, scale = 'subtitle', self._layouts['subtitle'][4]
        canvas = self.canvases[mode]
        self._text(mode, 440, 8, 'A · 柔雾紫字幕 · 离线演示', size=10,
                   color='#c7b6e5' if not self._light else '#714a9a', anchor='n')
        messages = self.messages[-2:]
        if not messages:
            messages = [{'sender': '助手', 'channel': '提示', 'translated': '等待离线示例消息'}]
        font = self._font(24, scale, 'bold')
        name_font = self._font(16, scale, 'bold')
        tag_font = self._font(12, scale, 'bold')
        for index, message in enumerate(messages):
            words = ' '.join(str(message.get('translated') or message.get('original') or '').split())
            name = str(message.get('sender', '队友'))
            channel = str(message.get('channel', '团队'))
            max_words_width = 650 * scale
            while len(words) > 1 and font.measure(words) > max_words_width:
                words = words[:-2].rstrip('…') + '…'
                self._truncated[mode] = ['subtitle']
            while len(name) > 1 and name_font.measure(name) > 120 * scale:
                name = name[:-2].rstrip('…') + '…'
            channel = channel[:4]
            tag_width = tag_font.measure(channel) + 16 * scale
            name_width, words_width = name_font.measure(name), font.measure(words)
            full_width = tag_width + name_width + words_width + 24 * scale
            start = (880 * scale - full_width) / 2
            y = (52 + index * 48) * scale
            _rounded(canvas, (start, y - 12 * scale, start + tag_width, y + 12 * scale),
                     6 * scale, fill='#eee6ff', outline='#fcf8ff', tags=('content',))
            canvas.create_text(start + tag_width / 2, y, text=channel,
                               font=tag_font, fill='#7142ca', tags=('content',))
            name_x = start + tag_width + 10 * scale
            words_x = name_x + name_width + 14 * scale
            for offset_x, offset_y in ((-1,-1),(0,-1),(1,-1),(-1,0),(1,0),(-1,1),(0,1),(1,1),(0,2)):
                canvas.create_text(name_x + offset_x * scale, y + offset_y * scale,
                                   text=name, anchor='w', font=name_font, fill='#151125', tags=('content',))
                canvas.create_text(words_x + offset_x * scale, y + offset_y * scale,
                                   text=words, anchor='w', font=font, fill='#12131d', tags=('content',))
            canvas.create_text(name_x, y, text=name, anchor='w', font=name_font,
                               fill='#d8c8ff', tags=('content',))
            canvas.create_text(words_x, y, text=words, anchor='w', font=font,
                               fill='#ffffff', tags=('content',))

    def _render_panel(self):
        mode = 'panel'
        self._box(mode, (1, 1, 389, 355), 15, fill='#fcfaff', outline='#f1eaf9')
        self._box(mode, (1, 1, 389, 51), 15, fill='#f0eafa')
        self._box(mode, (1, 24, 389, 51), 0, fill='#f0eafa')
        self._line(mode, (1, 51, 389, 51), _BORDER)
        self._box(mode, (17, 16, 38, 37), 6, fill=_PURPLE)
        self._icon(mode, 'message', 27.5, 26, color='#ffffff')
        self._text(mode, 48, 26, 'A · 实时消息', size=15, weight='bold', anchor='w')
        self._text(mode, 276, 26, '离线演示', size=10, color=_MUTED, anchor='center')
        self._box(mode, (339, 10, 377, 42), 8, fill='#f0eafa', tag='panel-hide')
        self._icon(mode, 'minus', 358, 26, color='#837394', tag='panel-hide')
        self.canvases[mode].tag_bind('panel-hide', '<Button-1>', lambda event: self._click(self.hide))
        self._button_regions[mode]['panel-hide'] = {'box': (339, 10, 377, 42), 'enabled': True}
        messages = self.messages[-3:]
        if not messages:
            self._text(mode, 195, 170, '等待离线示例消息', size=15,
                       color=_MUTED, anchor='center')
        for index, message in enumerate(messages):
            top = 52 + index * 100
            if index == 1:
                self._box(mode, (7, top + 2, 383, top + 98), 9, fill='#f1ebff')
            elif index:
                self._line(mode, (17, top, 373, top), '#eeE8f5')
            sender = str(message.get('sender', '队友'))
            sender_font = self._font(13, self._layouts[mode][4], 'bold')
            scale = self._layouts[mode][4]
            while len(sender) > 1 and sender_font.measure(sender) > 185 * scale:
                sender = sender[:-2].rstrip('…') + '…'
            sender_width = sender_font.measure(sender) / scale
            self._text(mode, 18, top + 12, sender, size=13, weight='bold')
            tag_x = min(213, 18 + sender_width + 11)
            channel = str(message.get('channel', '团队'))[:4]
            self._box(mode, (tag_x, top + 11, tag_x + 39, top + 29), 5, fill='#e8ddff')
            self._text(mode, tag_x + 19.5, top + 20, channel,
                       size=10, color='#7140cc', anchor='center')
            self._text(mode, 369, top + 20, str(message.get('time', '')),
                       size=10, color=_MUTED, anchor='e')
            translated_item = self._text(mode, 18, top + 36,
                       str(message.get('translated') or message.get('original') or ''),
                       size=15, width=354, max_height=40, tag='panel-translated')
            bounds = self.canvases[mode].bbox(translated_item)
            original_y = max(top + 60, bounds[3] / scale + 3 if bounds else top + 60)
            self._text(mode, 18, original_y, str(message.get('original') or ''),
                       size=11, color=_MUTED, width=353, max_height=18, tag='panel-original')

    def _render_input(self):
        mode, scale = 'input', self._layouts['input'][4]
        self._box(mode, (1, 1, 999, 251), 17, fill=_SURFACE, outline='#f0e8fa')
        self._box(mode, (1, 1, 999, 52), 17, fill='#f1ebfc')
        self._box(mode, (1, 25, 999, 52), 0, fill='#f1ebfc')
        self._line(mode, (1, 52, 999, 52), '#e6dff1')
        self._icon(mode, 'zap', 27, 27, color=_PURPLE)
        self._text(mode, 44, 27, 'A · 快捷输入', size=16, weight='bold', anchor='w')
        self._box(mode, (161, 17, 226, 37), 5, fill='#e9e0f7')
        self._text(mode, 193.5, 27, '离线演示', size=10, color='#715a8a', anchor='center')
        self._text(mode, 918, 27, 'Esc 关闭', size=10, color=_MUTED, anchor='e')
        for x in (846, 851, 856):
            for y in (24, 29):
                self._box(mode, (x, y, x + 2, y + 2), 1, fill='#b4a5c4')
        self._box(mode, (945, 10, 982, 43), 8, fill='#f1ebfc', tag='input-hide')
        self._icon(mode, 'close', 963, 27, color='#8c7c9c', tag='input-hide')
        self.canvases[mode].tag_bind('input-hide', '<Button-1>', lambda event: self._click(self.hide))
        self._button_regions[mode]['input-hide'] = {'box': tuple(round(v * scale) for v in (945, 10, 982, 43)), 'enabled': True}
        self._text(mode, 22, 77, '用中文回复', size=13, weight='bold', anchor='w')
        self._text(mode, 523, 77, '译文预览', size=13, weight='bold', anchor='w')
        self._line(mode, (499, 72, 499, 218), '#e9e2f2')
        self._box(mode, (837, 62, 979, 91), 15, fill='#efe9f6')
        for target, label, box in (('en', '英语', (841, 65, 907, 88)), ('ko', '韩语', (909, 65, 975, 88))):
            tag = 'language-' + target
            selected = self._target == target
            self._box(mode, box, 12, fill='#ddd0ff' if selected else '#efe9f6', tag=tag)
            self._text(mode, (box[0] + box[2]) / 2, 76.5, label,
                       size=11, color='#6731dc' if selected else _MUTED, anchor='center', tag=tag)
            self.canvases[mode].tag_bind(tag, '<Button-1>', lambda event, value=target: self._click(lambda: self._set_target(value)))
            self._button_regions[mode][tag] = {'box': tuple(round(v * scale) for v in box), 'enabled': True}
        self._box(mode, (21, 99, 477, 180), 10, fill='#f1eaff', outline='#e9defb')
        self._box(mode, (23, 101, 475, 178), 9, fill='#ffffff', outline='#cfc0ef')
        self._input_box = tuple(round(value * scale) for value in (38, 115, 422, 51))
        x, y, width, height = self._input_box
        self.entry.configure(font=self._font(17, scale), insertwidth=max(1, round(2 * scale)))
        self.entry.place(x=x, y=y, width=width, height=height)
        self._box(mode, (523, 101, 979, 178), 10, fill='#f0edf6')
        if self._busy:
            preview, color = '正在准备演示译文…', _MUTED
        elif self._copy_ready():
            preview, color = self._result, '#39314f'
        else:
            preview, color = ('点击“翻译中文”，在这里预览译文。' if self._draft.strip()
                              else '输入中文后，译文会显示在这里。'), _MUTED
        self._text(mode, 538, 113, preview, size=14, color=color,
                   width=425, max_height=56, tag='translation-preview')
        self._text(mode, 23, 195, '在这里输入想说的话', size=11, color='#776a86', anchor='w')
        self._text(mode, 475, 195, 'Ctrl+Enter 翻译', size=11, color='#776a86', anchor='e')
        self._button(mode, (523, 190, 657, 226), '复制译文', self._copy,
                     enabled=self._copy_ready(), icon='copy', tag='copy')
        self._button(mode, (666, 190, 821, 226), '翻译中…' if self._busy else '翻译中文',
                     self._submit, kind='primary', enabled=not self._busy,
                     icon='translate', tag='translate')
        self._text(mode, 977, 208, '复制后回游戏粘贴', size=10, color='#776a86', anchor='e')
        self._text(mode, 23, 235, self._status, size=10,
                   color='#a53e61' if self._error else _MUTED,
                   width=940, max_height=15, tag='input-status')

    def show(self, mode: str, area: tuple):
        if self._closed:
            return
        layout = _geometry(mode, area, self.metrics.scale)
        changed_mode = mode != self._mode
        if changed_mode:
            self.hide()
        self._area = tuple(int(value) for value in area)
        self._layouts[mode] = layout
        self._mode = mode
        width, height, x, y, scale = layout
        window = self.windows[mode]
        window.geometry(f'{width}x{height}{x:+d}{y:+d}')
        self._render(mode)
        window.update_idletasks()
        if self._native is not None:
            try:
                if mode == 'subtitle':
                    if mode not in self._native_styled:
                        self._native.set_overlay_mode(window, editing=False)
                        self._native_styled.add(mode)
                else:
                    self._native.set_rounded_corners(window, round(16 * scale))
            except (OSError, tk.TclError) as failure:
                self._native_error = str(failure)
        if changed_mode:
            if mode == 'subtitle' and self._native is not None:
                try:
                    self._native.show_no_activate(window)
                except (OSError, tk.TclError):
                    window.deiconify()
            else:
                window.deiconify()
                window.lift()
            if mode == 'input':
                if self._ime:
                    self._ime.clear_return_guard()
                self.entry.focus_set()
        self._last_layout = layout

    def hide(self):
        self._cancel_timers()
        self._revision += 1
        self._busy = False
        self._return_armed = False
        self._return_composing = False
        if self._mode == 'input' and self._status == '正在准备演示译文，可继续改稿或 Esc 关闭。':
            self._status = '已取消本次演示翻译；输入内容已保留。'
        for window in self.windows.values():
            if window.winfo_exists():
                window.withdraw()
        was_visible = self._mode is not None
        self._mode = None
        self._drag = None
        if was_visible and not self._closed:
            self.notify('已收起 A 方案悬浮窗。')

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.hide()
        if self._ime is not None:
            try:
                self._ime.detach_ime()
            except OSError:
                pass
        for window in self.windows.values():
            try:
                window.destroy()
            except tk.TclError:
                pass
        self._ime = None
        self._fonts.clear()

    def set_messages(self, messages):
        self.messages = [dict(message) for message in messages]
        if not self._closed and self._mode in ('subtitle', 'panel'):
            self._render(self._mode)

    def set_background(self, light: bool):
        self._light = bool(light)
        if not self._closed and self._mode == 'subtitle':
            self._render('subtitle')

    def _invalidate(self, *, status=None):
        self._revision += 1
        self._busy = False
        self._result = ''
        self._result_identity = None
        self._cancel_timers()
        self._error = False
        if status:
            self._status = status

    def _on_edit(self, event=None):
        if self._closed or self._syncing or not self.entry.edit_modified():
            return
        self.entry.edit_modified(False)
        draft = self.entry.get('1.0', 'end-1c')
        if draft == self._draft:
            return
        self._draft = draft
        self._invalidate(status='内容已修改，请重新翻译。' if draft.strip() else '请输入中文后再翻译。')
        if self._mode == 'input':
            self._render_input_only()

    def _render_input_only(self):
        if not self._closed and self._mode == 'input':
            self._render('input')

    def _set_target(self, target):
        if target not in ('en', 'ko') or target == self._target:
            return
        self._target = target
        self._invalidate(status='已切换为' + ('英语' if target == 'en' else '韩语') + '，请重新翻译。')
        self._render_input_only()

    def _submit(self):
        if self._closed or self._mode != 'input' or self._busy:
            return
        # Text 的 Modified 事件可能尚在事件队列，提交前再同步一次真实内容。
        self._draft = self.entry.get('1.0', 'end-1c')
        if not self._draft.strip():
            self._invalidate(status='请先输入中文，再点击“翻译中文”。')
            self._error = True
            self._render_input_only()
            self.entry.focus_set()
            return
        self._invalidate()
        identity = (self._revision, self._draft, self._target)
        self._busy = True
        self._status = '正在准备演示译文，可继续改稿或 Esc 关闭。'
        self._render_input_only()
        self._translation_timer = self._after(550, lambda: self._complete_translation(identity))

    def _after(self, delay, callback):
        holder = []
        def run():
            timer = holder[0]
            self._timers.discard(timer)
            if timer == self._translation_timer:
                self._translation_timer = None
            if not self._closed:
                callback()
        timer = self.root.after(delay, run)
        holder.append(timer)
        self._timers.add(timer)
        return timer

    def _complete_translation(self, identity):
        if (self._closed or self._mode != 'input'
                or identity != (self._revision, self._draft, self._target)):
            return
        self._busy = False
        try:
            result = self.translate(identity[1], identity[2])
            if not isinstance(result, str) or not result.strip():
                raise ValueError('离线示例没有返回有效译文。')
            self._result = result
            self._result_identity = identity
            self._status = '演示译文已就绪。点击“复制译文”后，才会写入剪贴板。'
            self._error = False
        except Exception as failure:
            self._result = ''
            self._result_identity = None
            self._status = str(failure) or '离线演示仅支持预置短句，请换一句后重试。'
            self._error = True
        self._render_input_only()

    def _copy_ready(self):
        return bool(not self._busy and self._result
                    and self._result_identity == (self._revision, self._draft, self._target))

    def _copy(self):
        if self._closed or self._mode != 'input':
            return
        # 复制时再次核对 Text，避免刚编辑但 Modified 尚未派发时复制旧译文。
        current = self.entry.get('1.0', 'end-1c')
        if current != self._draft:
            self._draft = current
            self._invalidate(status='内容已修改，请重新翻译。')
        if not self._copy_ready():
            self._status = '内容或目标语言已变化，请重新翻译。'
            self._render_input_only()
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._result)
            self._status = '译文已复制。演示程序不会向游戏发送任何内容。'
            self._error = False
            self.notify('A 方案：已复制演示译文。')
        except tk.TclError as failure:
            self._status = '剪贴板暂不可用，请重试：' + str(failure)
            self._error = True
        self._render_input_only()

    def _control_return(self, event=None):
        if self._return_armed:
            return 'break'
        if sys.platform == 'win32' and self._ime is None:
            self._status = '中文输入保护未就绪，请点击“翻译中文”。'
            self._render_input_only()
            return 'break'
        self._return_armed = True
        try:
            self._return_composing = bool(self._ime and self._ime.return_is_composing(self.entry.winfo_id()))
        except OSError:
            self._return_composing = True
        return 'break'

    def _release_return(self, event=None):
        if not self._return_armed:
            if self._ime:
                self._ime.clear_return_guard()
            return
        composing = self._return_composing
        self._return_armed = self._return_composing = False
        try:
            composing = composing or bool(self._ime and self._ime.return_is_composing(self.entry.winfo_id()))
        except OSError:
            composing = True
        finally:
            if self._ime:
                self._ime.clear_return_guard()
        if not composing:
            self._submit()
        return 'break'

    def _focus_out(self, event=None):
        self._return_armed = self._return_composing = False

    def _escape(self):
        self.hide()
        return 'break'

    def _cancel_timers(self):
        for timer in tuple(self._timers):
            try:
                self.root.after_cancel(timer)
            except tk.TclError:
                pass
        self._timers.clear()
        self._translation_timer = None

    def _drag_start(self, mode, event):
        if self._closed or mode == 'subtitle' or mode != self._mode:
            return
        scale = self._layouts[mode][4]
        if event.y > 52 * scale:
            return
        for button in self._button_regions.get(mode, {}).values():
            x, y, right, bottom = button['box']
            if x <= event.x <= right and y <= event.y <= bottom:
                return
        window = self.windows[mode]
        self._drag = (mode, event.x_root - window.winfo_x(), event.y_root - window.winfo_y())

    def _drag_move(self, event):
        if not self._drag or not self._area:
            return
        mode, offset_x, offset_y = self._drag
        window = self.windows[mode]
        left, top, width, height = self._area
        x = min(max(event.x_root - offset_x, left), left + width - window.winfo_width())
        y = min(max(event.y_root - offset_y, top), top + height - window.winfo_height())
        window.geometry(f'{window.winfo_width()}x{window.winfo_height()}{x:+d}{y:+d}')

    def _drag_end(self, event=None):
        self._drag = None

    def diagnostics(self) -> dict:
        windows = {}
        overflow = []
        area = self._area
        if not self._closed:
            for mode, window in self.windows.items():
                window.update_idletasks()
                geometry = (window.winfo_x(), window.winfo_y(), window.winfo_width(), window.winfo_height())
                canvas = self.canvases[mode]
                bounds = canvas.bbox('content')
                visible = self._mode == mode and bool(window.winfo_viewable())
                content_overflow = bool(bounds and (bounds[0] < -2 or bounds[1] < -2
                                        or bounds[2] > geometry[2] + 2 or bounds[3] > geometry[3] + 2))
                area_overflow = bool(visible and area and (geometry[0] < area[0] or geometry[1] < area[1]
                                     or geometry[0] + geometry[2] > area[0] + area[2]
                                     or geometry[1] + geometry[3] > area[1] + area[3]))
                if visible and (content_overflow or area_overflow):
                    overflow.append(mode)
                windows[mode] = {'visible': visible, 'geometry': geometry,
                                 'content_bounds': bounds, 'content_overflow': content_overflow,
                                 'area_overflow': area_overflow, 'truncated': self._truncated.get(mode, []),
                                 'scale': self._layouts.get(mode, (0, 0, 0, 0, self.metrics.scale))[4]}
        return {'variant': 'A', 'mode': self._mode, 'closed': self._closed,
                'area': area, 'windows': windows, 'overflow': overflow,
                'pending_after': len(self._timers), 'busy': self._busy,
                'draft': self._draft, 'target': self._target,
                'preview': self._result, 'copy_enabled': self._copy_ready(),
                'status': self._status, 'error': self._error,
                'native_error': self._native_error, 'ime_error': self._ime_error}


def run_state_checks():
    """纯 API 回归：使用内存调度器和内存剪贴板，不创建窗口、不输入按键。

    可从宿主导入执行。这里替换的仅是 Tk 表面，实际 show/翻译/改稿/复制/
    取消/close 方法保持不变；真实字体布局另由宿主 diagnostics 验证。
    """
    import re

    class MemoryRoot:
        def __init__(self):
            self.pending = {}
            self.counter = 0
            self.clipboard = []
        def after(self, delay, callback):
            self.counter += 1
            key = 'memory-after-' + str(self.counter)
            self.pending[key] = (delay, callback)
            return key
        def after_cancel(self, key):
            self.pending.pop(key, None)
        def complete(self):
            for key, (_, callback) in tuple(self.pending.items()):
                if key in self.pending:
                    self.pending.pop(key)
                    callback()
        def clipboard_clear(self):
            self.clipboard.clear()
        def clipboard_append(self, text):
            self.clipboard.append(text)

    class MemoryWindow:
        def __init__(self):
            self.rect = (0, 0, 1, 1)
            self.visible = False
            self.exists = True
        def geometry(self, value):
            width, height, x, y = map(int, re.fullmatch(r'(\d+)x(\d+)([+-]\d+)([+-]\d+)', value).groups())
            self.rect = (x, y, width, height)
        def winfo_x(self): return self.rect[0]
        def winfo_y(self): return self.rect[1]
        def winfo_width(self): return self.rect[2]
        def winfo_height(self): return self.rect[3]
        def winfo_exists(self): return self.exists
        def winfo_viewable(self): return self.visible
        def update_idletasks(self): pass
        def deiconify(self): self.visible = True
        def lift(self): pass
        def withdraw(self): self.visible = False
        def destroy(self): self.exists = False

    class MemoryText:
        def __init__(self, text):
            self.data, self.modified = text, False
        def get(self, *args): return self.data
        def edit_modified(self, value=None):
            if value is not None: self.modified = value
            return self.modified
        def focus_set(self): pass
        def edit(self, text): self.data, self.modified = text, True

    class MemoryCanvas:
        def bbox(self, tag): return None

    root = MemoryRoot()
    preview = PrdPreview.__new__(PrdPreview)
    notices = []
    def translate(text, target):
        if text != '我们等大招好了再开团':
            raise ValueError('离线演示暂未收录这句话，请使用预置短句。')
        return {'en': "Let's wait until our ult is ready, then start the fight.",
                'ko': '궁극기가 준비되면 한타를 시작하자.'}[target]
    preview.__dict__.update(
        root=root, metrics=DisplayMetrics((0, 0, 1920, 1080), 1.25),
        notify=notices.append, translate=translate, messages=[],
        windows={mode: MemoryWindow() for mode in ('subtitle', 'panel', 'input')},
        canvases={mode: MemoryCanvas() for mode in ('subtitle', 'panel', 'input')},
        _layouts={}, _fonts={}, _mode=None, _closed=False, _light=False,
        _area=None, _timers=set(), _translation_timer=None, _revision=0,
        _busy=False, _target='en', _result='', _result_identity=None,
        _status='', _error=False, _syncing=False, _drag=None,
        _return_armed=False, _return_composing=False, _native=None, _ime=None,
        _native_error='', _ime_error='', _draft='我们等大招好了再开团',
        _last_layout=None, _button_regions={}, _truncated={},
        _input_box=None, _input_hint=None, _native_styled=set(),
    )
    preview.entry = MemoryText(preview._draft)
    preview._render = lambda mode: None
    area = (20, 100, 1192, 500)
    checks = []
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)
    def edit(text):
        preview.entry.edit(text)
        preview._on_edit()

    preview.show('input', area)
    preview._submit()
    check('翻译期间不可复制且显示加载状态', preview._busy and not preview._copy_ready() and len(root.pending) == 1)
    pending = set(root.pending)
    preview.show('input', (24, 103, 1160, 490))
    check('幂等 show 保留草稿和在途翻译', preview._draft == '我们等大招好了再开团' and set(root.pending) == pending and preview._busy)
    root.complete()
    check('英语预置译文完成', preview._copy_ready() and preview._result.startswith("Let's wait"))
    check('翻译完成不会自动复制', root.clipboard == [])
    preview._copy()
    check('仅显式复制调用写入内存剪贴板', root.clipboard == [preview._result])
    root.clipboard.clear()
    edit('改稿')
    check('改稿使旧译文不可复制', not preview._copy_ready() and preview._result == '')
    edit('我们等大招好了再开团')
    preview._submit()
    root.complete()
    preview._set_target('ko')
    check('切换语言使旧译文失效', not preview._copy_ready() and preview._result == '')
    preview._submit()
    root.complete()
    check('韩语预置译文完成', preview._copy_ready() and preview._result.startswith('궁극기'))
    edit('未知离线输入')
    preview._submit()
    root.complete()
    check('未知输入显示错误且不能复制', preview._error and '暂未收录' in preview._status and not preview._copy_ready())
    edit('   ')
    preview._submit()
    check('空输入不创建翻译任务', preview._error and '请先输入' in preview._status and not root.pending)
    edit('我们等大招好了再开团')
    preview._submit()
    preview.hide()
    root.complete()
    check('hide 中途取消任务且不复制', not preview._busy and not root.pending and not preview._timers and root.clipboard == [])
    preview.show('input', area)
    check('重新显示保留草稿', preview._draft == '我们等大招好了再开团')
    preview._submit()
    edit('在途改稿')
    root.complete()
    check('在途改稿废弃未完成结果', not preview._busy and not preview._copy_ready() and not root.pending)
    edit('我们等大招好了再开团')
    preview._submit()
    preview._set_target('en')
    root.complete()
    check('在途切语言废弃未完成结果', not preview._busy and not preview._copy_ready() and not root.pending)
    for mode in ('subtitle', 'panel', 'input'):
        preview.show(mode, area)
        check('状态隔离：' + mode, sum(window.visible for window in preview.windows.values()) == 1 and preview.windows[mode].visible)
    preview.close()
    check('close 后没有残留 timer 或窗口', preview._closed and not root.pending and not preview._timers
          and not any(window.exists for window in preview.windows.values()))
    return {'passed': len(checks), 'checks': checks, 'real_clipboard_touched': False,
            'real_windows_created': False}


if __name__ == '__main__':
    import argparse
    import json
    parser = argparse.ArgumentParser(description='A 方案离线状态回归，不创建窗口或操作真实剪贴板。')
    parser.add_argument('--self-check', action='store_true')
    options = parser.parse_args()
    if options.self_check:
        print(json.dumps(run_state_checks(), ensure_ascii=False, indent=2))
    else:
        parser.print_help()
