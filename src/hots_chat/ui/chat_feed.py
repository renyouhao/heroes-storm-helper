"""可选择、可复制的聊天阅读区，并保留消息列表的小型兼容接口。"""
from collections import OrderedDict
import tkinter as tk
from tkinter import font as tkfont, ttk

from .theme import theme_colors
from .widget_lifecycle import widget_descendants,detach_destroyed_tree


class ChatFeed(ttk.Frame):
    def __init__(self, parent, *, font, settings):
        super().__init__(parent, style='Surface.TFrame')
        self._font = font
        self._secondary_font = tkfont.Font(root=self, family='Microsoft YaHei UI', size=10)
        self._sender_font = tkfont.Font(root=self, family='Microsoft YaHei UI', size=10, weight='bold')
        self._avatar_font = tkfont.Font(root=self, family='Microsoft YaHei UI', size=17, weight='bold')
        self._records = OrderedDict()
        self._selected = ()
        self._tags = {}
        self._painted_identities = {}
        self._painted_values = {}
        self._painted_generation = None
        self._columns = {}
        self._next_tag = 0
        self._paint_timer = None
        self._avatar_timer = None
        self._visible_avatars = []
        self._generation = 0
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.body = tk.Text(self, wrap='word', font=font, state='disabled',
                            cursor='arrow', padx=16, pady=10, borderwidth=0,
                            highlightthickness=0, takefocus=True, exportselection=False)
        self.body.grid(row=0, column=0, sticky='nsew')
        self.scrollbar = ttk.Scrollbar(self, command=self.body.yview)
        self.scrollbar.grid(row=0, column=1, sticky='ns')
        # 一个头像槽覆盖阅读区左侧留白；文本自身仍是可复制的单个 Text。
        # 只绘制可见行，不为历史消息创建控件、图片或额外的 Tcl 资源。
        self.avatars = tk.Canvas(self.body, borderwidth=0, highlightthickness=0,
                                 takefocus=False, cursor='arrow')
        self.avatars.place(x=0, y=0, relheight=1, width=80)
        self.avatars.bind('<ButtonRelease-1>', self._select_avatar)
        self.avatars.bind('<MouseWheel>', self._scroll_avatar)
        self.avatars.bind('<Button-4>', lambda event: self.body.yview_scroll(-1, 'units'))
        self.avatars.bind('<Button-5>', lambda event: self.body.yview_scroll(1, 'units'))
        self.body.configure(yscrollcommand=self._on_scroll)
        self.body.bind('<Configure>', self._resize_feed)
        self.body.bind('<ButtonRelease-1>', self._select_at)
        self.body.bind('<Up>', lambda event: self._move_selection(-1))
        self.body.bind('<Down>', lambda event: self._move_selection(1))
        self.body.bind('<Home>', lambda event: self._select_edge(False))
        self.body.bind('<End>', lambda event: self._select_edge(True))
        self.body.bind('<Return>', lambda event: self.event_generate('<<MessageDetails>>'))
        self.apply_settings(settings)
        self._schedule_paint()

    def apply_settings(self, settings):
        size = max(10, int(settings.font_size) - 2)
        self._secondary_font.configure(size=size)
        self._sender_font.configure(size=int(settings.font_size))
        self._avatar_font.configure(size=max(15, min(22, int(settings.font_size) + 5)))
        c = theme_colors(settings)
        self._colors = c
        scale = max(.75, min(4, float(self.tk.call('tk', 'scaling')) * 72 / 96))
        self._avatar_size = round(52 * scale)
        self._gutter = round(80 * scale)
        self.avatars.configure(background=c['surface'])
        self.avatars.place_configure(width=self._gutter)
        self.body.configure(background=c['surface'], foreground=c['text'],
                            selectbackground=c['selection'], selectforeground=c['text'],
                            highlightbackground=c['border'], highlightcolor=c['accent'],
                            insertbackground=c['accent'])
        self.body.tag_configure('sender', foreground=c['text'], font=self._sender_font,
                                 spacing1=0, spacing3=round(6 * scale))
        self.body.tag_configure('channel', foreground=c['accent'], background=c['selection'],
                                 font=self._secondary_font)
        self.body.tag_configure('metadata', foreground=c['muted'], font=self._secondary_font,
                                 spacing1=0, spacing3=round(6 * scale))
        self.body.tag_configure('message_gap',font=('Microsoft YaHei UI',max(4,round(settings.font_size/2))),
                                 spacing1=0,spacing3=0)
        self.body.tag_configure('translation', foreground=c['text'], font=self._font,
                                 spacing3=round(4 * scale))
        self.body.tag_configure('original', foreground=c['muted'], font=self._secondary_font,
                                 spacing3=round(8 * scale))
        # 保留原文标签的数据兼容性，但阅读时只显示原文本身。
        self.body.tag_configure('source_label', elide=True)
        self.body.tag_configure('status', foreground=c['muted'], font=self._secondary_font,
                                 spacing3=10)
        self.body.tag_configure('empty', foreground=c['muted'], font=self._font,
                                 justify='center', spacing1=30, spacing3=12)
        self.body.tag_configure('message_selected', background=c['selection'])
        self.body.tag_lower('message_selected')
        for role in ('sender', 'channel', 'metadata', 'translation', 'original', 'status'):
            self.body.tag_configure(role, lmargin1=self._gutter, lmargin2=self._gutter,
                                     rmargin=8)
        self._resize_feed()

    def _on_scroll(self, first, last):
        self.scrollbar.set(first, last)
        self._schedule_avatars()

    def _resize_feed(self, event=None):
        width = self.body.winfo_width() - 2 * int(self.body.cget('padx')) - 12
        # Tk 使用逻辑行首字符的段落设置，不能只给时间片段设置制表位。
        for role in ('sender', 'channel', 'metadata'):
            self.body.tag_configure(role, tabs=(max(self._gutter + 100, width), 'right'))
        self._schedule_avatars()

    def _schedule_avatars(self):
        if not getattr(self, '_destroyed', False) and self._avatar_timer is None:
            self._avatar_timer = self.after_idle(self._paint_avatars)

    @staticmethod
    def _mix(first, second, amount):
        rgb = (round(int(first[i:i + 2], 16) * (1 - amount)
                     + int(second[i:i + 2], 16) * amount) for i in (1, 3, 5))
        return '#{:02x}{:02x}{:02x}'.format(*rgb)

    def _paint_avatars(self):
        self._avatar_timer = None
        self.avatars.delete('all')
        self._visible_avatars = []
        if not self._records:
            # 空状态可使用完整宽度，头像槽不能遮挡居中的提示文字。
            self.avatars.place_forget()
            return
        self.avatars.place(x=0, y=0, relheight=1, width=self._gutter)
        height = self.body.winfo_height()
        first_visible = self.body.index('@0,0')
        last_visible = self.body.index(f'@0,{height}')
        c = self._colors
        for key, row in self._records.items():
            event = row['event']
            tag = self._tags.get(key)
            ranges = self.body.tag_ranges(tag) if tag else ()
            if (event is None or not ranges
                    or self.body.compare(ranges[-1], '<=', first_visible)
                    or self.body.compare(ranges[0], '>', last_visible)):
                continue
            first = self.body.dlineinfo(ranges[0])
            end = self.body.dlineinfo(f'{ranges[-1]}-1c')
            top = first[1] if first else 0
            bottom = end[1] + end[3] if end else height
            self._visible_avatars.append((key, top, bottom))
            if key in self._selected:
                self.avatars.create_rectangle(0, top, self._gutter, bottom,
                                               fill=c['selection'], outline='')
            if first is None:
                continue
            diameter = self._avatar_size
            left = (self._gutter - diameter) / 2 + 5
            bounds = self.body.bbox(ranges[0])
            top = bounds[1] - 2 if bounds else first[1]
            # 昵称仅用于稳定选取明暗层次；首字头像不声称识别游戏英雄。
            tone = sum(ord(char) for char in event.sender) % 4
            fill = self._mix(c['accent'], c['surface'], .82 + tone * .025)
            self.avatars.create_oval(left, top, left + diameter, top + diameter,
                                     fill=fill, outline=self._mix(c['accent'], c['surface'], .72))
            self.avatars.create_text(left + diameter / 2, top + diameter / 2,
                                      text=(event.sender.strip()[:1] or '?').upper(),
                                      font=self._avatar_font, fill=c['accent'])

    def _select_avatar(self, event):
        self.body.focus_set()
        for key, top, bottom in self._visible_avatars:
            if top <= event.y < bottom:
                self.selection_set(key)
                break

    def _scroll_avatar(self, event):
        units = -int(event.delta / 120)
        if units:
            self.body.yview_scroll(units, 'units')
        return 'break'

    def sync(self, messages, generation):
        """一次同步一批事件；历史 ID 重命名时按原事件保留用户选择。"""
        selected_event = None
        if self._selected:
            selected_event = self._records.get(self._selected[0], {}).get('event')
        records = OrderedDict((key, {
            'values': (message.original, message.engine_translation, message.status),
            'event': message.event,
        }) for key, message in messages.items())
        changed = records != self._records or generation != self._generation
        self._records, self._generation = records, generation
        selected = tuple(key for key in self._selected if key in records)
        if selected_event is not None:
            selected = tuple(key for key, row in records.items() if row['event'] is selected_event)[:1]
        if selected != self._selected:
            self._selected = selected
            self.event_generate('<<TreeviewSelect>>')
        if changed:
            self._schedule_paint()

    def get_children(self, item=''):
        return tuple(self._records) if not item else ()

    def exists(self, item):
        return item in self._records

    def insert(self, parent, index, iid=None, **options):
        if iid is None:
            while True:
                self._next_tag += 1
                iid = 'message-' + str(self._next_tag)
                if iid not in self._records:
                    break
        else:
            iid = str(iid)
        if iid in self._records:
            raise tk.TclError('消息 ID 已存在：' + iid)
        self._records[iid] = {'values': tuple(options.get('values', ())), 'event': None,
                              'identity': object()}
        if index != 'end':
            order = list(self._records)
            order.insert(int(index), order.pop())
            self._records = OrderedDict((key, self._records[key]) for key in order)
        self._schedule_paint()
        return iid

    def item(self, item, option=None, **options):
        row = self._records[item]
        if 'values' in options:
            row['values'] = tuple(options['values'])
            self._schedule_paint()
        result = {'values': row['values']}
        return result.get(option, '') if option else result

    def delete(self, *items):
        for item in items:
            self._records.pop(item, None)
        selected = tuple(key for key in self._selected if key in self._records)
        if selected != self._selected:
            self._selected = selected
            self.event_generate('<<TreeviewSelect>>')
        self._schedule_paint()

    def selection(self):
        return self._selected

    def selection_set(self, *items):
        if len(items) == 1 and isinstance(items[0], (tuple, list)):
            items = tuple(items[0])
        selected = tuple(key for key in items if key in self._records)[:1]
        if selected != self._selected:
            self._selected = selected
            self._highlight_selection()
            self.event_generate('<<TreeviewSelect>>')

    def column(self, column, option=None, **options):
        current = self._columns.setdefault(column, {})
        current.update(options)
        return current.get(option, '') if option else dict(current)

    def heading(self, column, option=None, **options):
        return options.get('text', '')

    def yview(self, *args):
        return self.body.yview(*args)

    def yview_moveto(self, fraction):
        return self.body.yview_moveto(fraction)

    def see(self, item):
        tag = self._tags.get(item)
        ranges = self.body.tag_ranges(tag) if tag else ()
        if ranges:
            self.body.see(ranges[0])

    def _move_selection(self, direction):
        keys = list(self._records)
        if keys:
            index = keys.index(self._selected[0]) if self._selected else (-1 if direction > 0 else len(keys))
            key = keys[max(0, min(len(keys) - 1, index + direction))]
            self.selection_set(key)
            self.see(key)
        return 'break'

    def _select_edge(self, last):
        keys = list(self._records)
        if keys:
            key = keys[-1 if last else 0]
            self.selection_set(key)
            self.see(key)
        return 'break'

    def _select_at(self, event):
        tags = self.body.tag_names('@%d,%d' % (event.x, event.y))
        for key, tag in self._tags.items():
            if tag in tags:
                self.selection_set(key)
                return

    def _highlight_selection(self):
        self.body.tag_remove('message_selected', '1.0', 'end')
        for key in self._selected:
            tag = self._tags.get(key)
            ranges = self.body.tag_ranges(tag) if tag else ()
            if ranges:
                self.body.tag_add('message_selected', ranges[0], ranges[-1])
        self._schedule_avatars()

    def _schedule_paint(self):
        if self._paint_timer is None:
            self._paint_timer = self.after_idle(self._paint)

    def _painted_rows(self):
        """读取实际已绘制内容；待绘制模型可能已经换代、改名或淘汰。"""
        rows = {}
        for key, identity in self._painted_identities.items():
            ranges = self.body.tag_ranges(self._tags[key])
            if ranges:
                start, end = str(ranges[0]), str(ranges[-1])
                rows[id(identity)] = {'start': start, 'end': end,
                                      'text': self.body.get(start, end)}
        return rows

    def _text_anchor(self, index, rows, *, end=False):
        index = self.body.index(index)
        for identity, row in rows.items():
            if (self.body.compare(row['start'], '<=', index)
                    and self.body.compare(index, '<=' if end else '<', row['end'])):
                count = self.body.count(row['start'], index, 'chars')
                return identity, count[0] if count else 0
        return None

    def _anchored_index(self, anchor, rows):
        if anchor is None or anchor[0] not in rows:
            return None
        row = rows[anchor[0]]
        index = self.body.index(f"{row['start']}+{anchor[1]}c")
        return row['end'] if self.body.compare(index, '>', row['end']) else index

    def _selected_rows(self, start, end, rows):
        return [(identity, row['text']) for identity, row in rows.items()
                if self.body.compare(row['end'], '>', start)
                and self.body.compare(row['start'], '<', end)]

    def _save_text_selection(self, rows):
        ranges = self.body.tag_ranges('sel')
        return [(self._text_anchor(start, rows), self._text_anchor(end, rows, end=True),
                 self.body.get(start, end), self._selected_rows(start, end, rows))
                for start, end in zip(ranges[::2], ranges[1::2])]

    def _restore_text_selection(self, selections, rows):
        restored = []
        for start_anchor, end_anchor, text, selected_rows in selections:
            start = self._anchored_index(start_anchor, rows)
            end = self._anchored_index(end_anchor, rows)
            # 任一选中消息自身改变/被淘汰时清除选区，不能把旧索引转给别的消息。
            if (start is None or end is None
                    or self._selected_rows(start, end, rows) != selected_rows
                    or self.body.get(start, end) != text):
                return
            restored.append((start, end))
        for start, end in restored:
            self.body.tag_add('sel', start, end)

    def _paint(self):
        self._paint_timer = None
        if self._paint_tail_changes():
            return
        old_rows = self._painted_rows()
        top_index = self.body.index('@0,0')
        anchor = self._text_anchor(top_index, old_rows)
        line = self.body.dlineinfo(top_index)
        top_y = line[1] if line else None
        selections = self._save_text_selection(old_rows)
        follow_tail = self.body.yview()[1] >= .98 and not selections
        self.body.configure(state='normal')
        self.body.delete('1.0', 'end')
        for key in set(self._tags) - set(self._records):
            self.body.tag_delete(self._tags.pop(key))
        if not self._records:
            self.body.insert('end', '等待队友的第一条消息\n', 'empty')
            self.body.insert('end', '连接游戏后，聊天与中文译文会显示在这里。', 'empty')
        for key, row in self._records.items():
            self._insert_row(key,row,'end-1c')
        self.body.configure(state='disabled')
        self._remember_paint()
        new_rows = self._painted_rows()
        self._restore_text_selection(selections, new_rows)
        self._highlight_selection()
        if follow_tail:
            self.body.see('end')
        else:
            index = self._anchored_index(anchor, new_rows)
            self.body.yview(index or '1.0')
            line = self.body.dlineinfo(index) if index is not None else None
            if line is not None and top_y is not None:
                self.body.yview_scroll(line[1] - top_y, 'pixels')

    def _insert_row(self,key,row,index):
        if key not in self._tags:
            self._next_tag += 1
            self._tags[key]='message_'+str(self._next_tag)
        tag=self._tags[key]
        # 右重力使同一行的各段文本按顺序插入，更新中间行时不会移到文档尾部。
        self.body.mark_set('_render_cursor',index)
        self.body.mark_gravity('_render_cursor','right')
        def insert(text,role):
            self.body.insert('_render_cursor',text,(tag,role))
        event=row['event']
        original,translation,status=row['values']
        if event is not None:
            insert(event.sender+'   ','sender')
            insert(' '+event.channel+' ','channel')
            history='  ·  历史对局' if event.generation!=self._generation else ''
            insert(f'\t{event.timestamp}{history}\n','metadata')
        insert((translation or original)+'\n','translation')
        if translation:
            self.body.insert('_render_cursor', '原文  ', (tag, 'original', 'source_label'))
            insert(original+'\n','original')
        if status and status not in ('阿里云','缓存'):insert(status+'\n','status')
        # 独立空白行负责间距，避免频道底色被 Text 的段前留白拉成色块。
        insert('\n','message_gap')
        self.body.mark_unset('_render_cursor')

    def _remember_paint(self):
        self._painted_identities={key:row['event'] if row['event'] is not None else row['identity']
                                  for key,row in self._records.items()}
        self._painted_values={key:row['values'] for key,row in self._records.items()}
        self._painted_generation=self._generation

    def _paint_tail_changes(self):
        """正常跟随最新消息时只向Tk提交变动行；阅读选区走原有锚点恢复。"""
        if (self._painted_generation!=self._generation or not self._painted_identities
                or not self._records or self.body.tag_ranges('sel') or self.body.yview()[1]<.98):
            return False
        old=self._painted_identities
        retained=[key for key in old if key in self._records]
        current=list(self._records)
        # 历史改名、插入中间或重排时完整恢复锚点，不能把旧行归给新身份。
        if current[:len(retained)]!=retained:return False
        for key in retained:
            row=self._records[key]
            identity=row['event'] if row['event'] is not None else row['identity']
            if identity is not old[key]:return False
        self.body.configure(state='normal')
        for key in tuple(old):
            if key not in self._records:
                tag=self._tags.pop(key)
                ranges=self.body.tag_ranges(tag)
                if ranges:self.body.delete(ranges[0],ranges[-1])
                self.body.tag_delete(tag)
        for key in retained:
            row=self._records[key]
            if row['values']==self._painted_values[key]:continue
            ranges=self.body.tag_ranges(self._tags[key])
            start=str(ranges[0])
            self.body.delete(start,ranges[-1])
            self._insert_row(key,row,start)
        for key in current[len(retained):]:
            self._insert_row(key,self._records[key],'end-1c')
        self.body.configure(state='disabled')
        self._remember_paint()
        self._highlight_selection()
        self.body.see('end')
        return True

    def destroy(self):
        if getattr(self,'_destroyed',False):return
        self._destroyed=True
        if self._paint_timer is not None:
            self.after_cancel(self._paint_timer)
            self._paint_timer = None
        if self._avatar_timer is not None:
            self.after_cancel(self._avatar_timer)
            self._avatar_timer = None
        self._visible_avatars.clear()
        descendants=widget_descendants(self)
        super().destroy()
        detach_destroyed_tree(self,descendants)
