"""底部短字幕与兼容面板；完整聊天仍由主窗口保留。"""
from collections import OrderedDict
from dataclasses import dataclass
import math
import logging
from time import monotonic
import tkinter as tk
from tkinter import font as tkfont

from .native_windows import Win32Window
from .theme import overlay_colors
from .subtitle_text import draw_outlined_text
from ..layout import display_metrics,overlay_layout,bottom_overlay_layout


logger=logging.getLogger(__name__)


@dataclass
class OverlayEntry:
    message: object
    expires_at: float
    count: int=1
    identity: tuple=()


@dataclass(frozen=True)
class OverlayHistorySnapshot:
    generation: int | None
    candidates: tuple
    seen: frozenset


class OverlayHistory:
    """从连接代和首次接收时间确定展示资格，不因翻译更新重播。"""
    def __init__(self):
        self.generation=None
        self.entries=OrderedDict()
        self._candidates=OrderedDict()
        self._seen=set()

    def sync(self,messages,generation,*,now,timeout,enabled=True):
        if generation!=self.generation:
            self.generation=generation
            self.entries.clear()
            self._candidates.clear()
            self._seen.clear()
        self.expire(now)
        current={key for key,message in messages.items() if message.event.generation==generation}
        groups=OrderedDict()
        for key,message in messages.items():
            if key not in current:continue
            event=message.event
            identity=(event.generation,event.channel,event.sender,event.text)
            groups.setdefault(identity,[]).append((key,message))
        active={entry.identity:(key,entry) for key,entry in self._candidates.items()}
        for identity,rows in groups.items():
            eligible=[message for _,message in rows if not getattr(message,'suppressed_reason','')]
            unseen=[key for key,_ in rows if key not in self._seen]
            existing=active.get(identity)
            if existing and eligible and enabled:
                entry=existing[1]
                entry.count+=len(unseen)
                entry.message=max((message for _,message in rows),
                                  key=lambda message:bool(message.engine_translation))
            elif not existing and enabled and any(key not in self._seen and not getattr(message,'suppressed_reason','')
                                                  for key,message in rows):
                # 独特的限流消息不会挤占位置；同文限流仍计入已有折叠。
                key=next(key for key,message in rows if key not in self._seen and not getattr(message,'suppressed_reason',''))
                representative=max((message for _,message in rows),
                                   key=lambda message:bool(message.engine_translation))
                self._candidates[key]=OverlayEntry(representative,now+timeout,len(unseen),identity)
        # VM 最多保留 500 条；去重集合随它回收，不随整场聊天无限增长。
        self._seen=current
        for key,entry in list(self._candidates.items()):
            rows=groups.get(entry.identity,[])
            if not enabled or not any(not getattr(message,'suppressed_reason','') for _,message in rows):
                del self._candidates[key]
        self._select_entries()

    def _select_entries(self):
        # 保留有界候选直到原期限：延后到达的限流结果不能永久挤掉正常消息。
        self.entries=OrderedDict(list(self._candidates.items())[-3:])

    def expire(self,now):
        for key,entry in list(self._candidates.items()):
            if entry.expires_at<=now:del self._candidates[key]
        self._select_entries()

    @property
    def next_expiry(self):
        return min((entry.expires_at for entry in self.entries.values()),default=None)

    def clear(self):
        self.entries.clear()
        self._candidates.clear()

    def snapshot(self):
        """复制有界展示容器，保留消息引用与原截止时间；不复制业务数据。"""
        return OverlayHistorySnapshot(self.generation, tuple(self._candidates.items()), frozenset(self._seen))

    def restore(self, snapshot):
        self.generation=snapshot.generation
        self._candidates=OrderedDict(snapshot.candidates)
        self._seen=set(snapshot.seen)
        self._select_entries()


class OverlayWindow:
    TRANSPARENT_COLOR='#010203'
    BACKGROUND=overlay_colors()['surface']
    FOREGROUND=overlay_colors()['text']
    MUTED=overlay_colors()['muted']

    def __init__(self,root,settings,*,clock=monotonic,native=None,metrics=None):
        self.root=root
        self.settings=settings
        self._clock=clock
        self._native=native if native is not None else Win32Window()
        self.history=OverlayHistory()
        self._timer=None
        self._scheduled_expiry=None
        self._closed=False
        self._editing=False
        self._visible=False
        self._input_active=False
        self._transparent=False
        self.transparency_error=None
        self._drag_origin=None
        self._rendered=None
        self._rounded_size=None
        self._metrics=metrics if metrics is not None else display_metrics(root)
        self._reanchor=settings.overlay_anchor is not None
        self.window=tk.Toplevel(root)
        self.window._hots_theme_scope='overlay'
        self.window.withdraw()
        colors=overlay_colors(self.settings)
        self.BACKGROUND,self.FOREGROUND,self.MUTED=colors['surface'],colors['text'],colors['muted']
        self._header_font=tkfont.Font(root=self.window,family='Microsoft YaHei UI',size=10)
        self._translation_font=tkfont.Font(root=self.window,family='Microsoft YaHei UI',
                                           size=self.settings.overlay_font_size)
        self._original_font=tkfont.Font(root=self.window,family='Microsoft YaHei UI',
                                        size=max(10,self.settings.overlay_font_size-2))
        self.window.title('游戏内译文')
        self.window.overrideredirect(True)
        self.window.configure(background=self.BACKGROUND,takefocus=False,
                              highlightthickness=self._metrics.px(1),
                              highlightbackground=colors['accent'],highlightcolor=colors['accent'])
        self.window.protocol('WM_DELETE_WINDOW',lambda:self.set_editing(False))
        self.header=tk.Frame(self.window,background=colors['field'])
        self.instructions=tk.Label(self.header,text='拖动此处调整位置',background=colors['field'],
                                   foreground=self.FOREGROUND,padx=10,pady=7,takefocus=False)
        self.instructions.pack(side='left')
        self.done=tk.Button(self.header,text='完成调整',command=lambda:self.set_editing(False),
                            takefocus=False,background=colors['selection'],foreground=self.FOREGROUND,
                            relief='flat',padx=8,pady=4)
        self.done.pack(side='right',padx=5,before=self.instructions)
        self.body=tk.Frame(self.window,background=self.BACKGROUND)
        self.body.pack(fill='both',expand=True,padx=10,pady=7)
        self.subtitle_canvas=tk.Canvas(self.body,background=self.BACKGROUND,
                                       borderwidth=0,highlightthickness=0,takefocus=False)
        self.placeholder=tk.Label(self.body,text='译文会显示在这里；完成后鼠标可穿透。',
                                  background=self.BACKGROUND,foreground=self.MUTED,
                                  justify='left',anchor='w',takefocus=False)
        self.rows=[]
        for _ in range(2):
            frame=tk.Frame(self.body,background=self.BACKGROUND)
            translation=tk.Label(frame,background=self.BACKGROUND,foreground=self.FOREGROUND,
                                 justify='left',anchor='w',takefocus=False,
                                 borderwidth=0,highlightthickness=0,padx=0,pady=0)
            translation.pack(fill='x')
            original=tk.Label(frame,background=self.BACKGROUND,foreground=self.MUTED,
                              justify='left',anchor='w',takefocus=False,
                              borderwidth=0,highlightthickness=0,padx=0,pady=0)
            self.rows.append({'frame':frame,'translation':translation,'original':original})
        for widget in (self.header,self.instructions):
            widget.bind('<ButtonPress-1>',self._start_drag)
            widget.bind('<B1-Motion>',self._drag)
            widget.bind('<ButtonRelease-1>',self._end_drag)
        self.apply_settings()

    def sync(self,messages,generation):
        if self._closed:return
        self.history.sync(messages,generation,now=self._clock(),
                          timeout=self.settings.overlay_timeout,enabled=self.settings.overlay_enabled)
        self._refresh()

    def capture_settings_state(self):
        return self.history.snapshot(), self._editing

    def restore_settings_state(self, snapshot):
        """失败设置事务恢复展示资格；自然到期的消息不会重新计时。"""
        if self._closed:return
        history, editing=snapshot
        self.history.restore(history)
        self.set_editing(editing)

    def apply_settings(self,metrics=None):
        if self._closed:return
        if metrics is not None:
            if metrics!=self._metrics:self._reanchor=True
            self._metrics=metrics
        # 显式应用也可能来自偏好回滚，不能只靠显示指标变化恢复锚点。
        if self.settings.overlay_anchor is not None:self._reanchor=True
        if not self.settings.overlay_enabled:
            self._editing=False
            self.history.clear()
            self.header.pack_forget()
        self._apply_palette()
        self._apply_layout_style()
        self._native.set_overlay_mode(self.window,self._editing)
        self._rendered=None
        self.history.expire(self._clock())
        self._refresh()

    def _apply_palette(self):
        colors=overlay_colors(self.settings)
        self.BACKGROUND=colors['surface']
        self.FOREGROUND=colors['text']
        self.MUTED=colors['muted']
        self.window.configure(background=self.BACKGROUND,
                              highlightbackground=colors['accent'],highlightcolor=colors['accent'])
        self.body.configure(background=self.BACKGROUND)
        self.header.configure(background=colors['field'])
        self.instructions.configure(background=colors['field'],foreground=self.FOREGROUND)
        self.done.configure(background=colors['selection'],foreground=self.FOREGROUND,
                            activebackground=colors['accent'],activeforeground=colors['on_accent'])
        self.placeholder.configure(background=self.BACKGROUND,foreground=self.MUTED)
        for row in self.rows:
            row['frame'].configure(background=self.BACKGROUND)
            row['translation'].configure(background=self.BACKGROUND,foreground=self.FOREGROUND)
            row['original'].configure(background=self.BACKGROUND,foreground=self.MUTED)
        self._apply_surface()

    @property
    def _subtitle(self):
        return getattr(self.settings,'overlay_style','subtitle')=='subtitle'

    def _apply_surface(self):
        """颜色键只隐藏背景；字幕始终以alpha=1绘制，失败时明确降级。"""
        transparent=self._subtitle and not self._editing
        self._transparent=False
        self.window.attributes('-alpha',1. if self._subtitle else self.settings.overlay_alpha)
        try:
            self.window.attributes('-transparentcolor',self.TRANSPARENT_COLOR if transparent else '')
            if transparent:
                actual=str(self.window.attributes('-transparentcolor'))
                if actual.lower()!=self.TRANSPARENT_COLOR:
                    raise tk.TclError('窗口未接受透明颜色键')
                self._transparent=True
                self.transparency_error=None
        except tk.TclError as error:
            if transparent:
                reason=str(error)
                if reason!=self.transparency_error:
                    logger.warning('字幕透明背景不可用，已使用不透明可读背景：%s',reason)
                self.transparency_error=reason
            # 某些Tk平台完全不支持颜色键；不要让恢复背景的第二个异常盖过原原因。
            try:self.window.attributes('-transparentcolor','')
            except tk.TclError:pass
        background=self.TRANSPARENT_COLOR if self._transparent else self.BACKGROUND
        for widget in (self.window,self.body,self.subtitle_canvas):
            widget.configure(background=background)
        self.window.configure(highlightthickness=0 if self._subtitle else self._metrics.px(1))
        self.instructions.configure(text=('透明不可用；拖动调整' if self.transparency_error
                                          else '拖动此处调整位置'))

    def refresh_layout(self,metrics=None):
        """供主窗口低频显示变化检测调用；相同指标不重排。"""
        if self._closed:return False
        metrics=metrics if metrics is not None else display_metrics(self.root)
        if metrics==self._metrics:return False
        self._metrics=metrics
        self._reanchor=True
        self._apply_layout_style()
        self._rendered=None
        self._refresh()
        return True

    def _apply_layout_style(self):
        metrics=self._metrics
        if getattr(self.settings,'overlay_placement','custom')=='bottom':
            self._layout=bottom_overlay_layout(metrics,width=self.settings.overlay_width,height=1)
        else:
            self._layout=overlay_layout(metrics,width=self.settings.overlay_width,
                                        x=self.settings.overlay_x,y=self.settings.overlay_y,height=1)
        self.window.geometry(self._layout.geometry)
        self.window.configure(highlightthickness=0 if self._subtitle else metrics.px(1))
        self.body.pack_configure(padx=metrics.px(6 if self._subtitle else 10),
                                 pady=metrics.px(2 if self._subtitle else 7))
        self.instructions.configure(font=self._header_font,padx=metrics.px(8),pady=metrics.px(7))
        self.done.configure(font=self._header_font,padx=metrics.px(8),pady=metrics.px(4))
        self.done.pack_configure(padx=metrics.px(5))
        wrap=max(1,self._layout.width-metrics.px(24))
        if self._translation_font.cget('size')!=self.settings.overlay_font_size:
            self._translation_font.configure(size=self.settings.overlay_font_size)
        original_size=max(10,self.settings.overlay_font_size-2)
        if self._original_font.cget('size')!=original_size:
            self._original_font.configure(size=original_size)
        for row in self.rows:
            row['translation'].configure(font=self._translation_font,wraplength=wrap)
            row['original'].configure(font=self._original_font,wraplength=wrap)
        self.placeholder.configure(font=self._translation_font,wraplength=wrap)

    @staticmethod
    def _fit_label(label,text,height):
        """按自己创建的Label实际请求高度省略文本，不推测CJK换行。"""
        label.configure(text=text)
        label.update_idletasks()
        if label.winfo_reqheight()<=height:return
        low,high=0,len(text)
        while low<high:
            middle=(low+high+1)//2
            label.configure(text=text[:middle]+'…')
            label.update_idletasks()
            if label.winfo_reqheight()<=height:low=middle
            else:high=middle-1
        label.configure(text=text[:low]+'…')

    def set_editing(self,editing):
        if self._closed:return
        self._editing=bool(editing and self.settings.overlay_enabled)
        self._drag_origin=None
        if self._editing:self.header.pack(fill='x',before=self.body)
        else:self.header.pack_forget()
        self._apply_surface()
        # 调整时只接收鼠标；NOACTIVATE 和不接受 Tab 焦点始终保留。
        self._native.set_overlay_mode(self.window,self._editing)
        self._rendered=None
        self.history.expire(self._clock())
        self._refresh()

    def set_input_active(self,active):
        """输入条打开时避让；截止时间与历史不变，关闭后不复活过期消息。"""
        if self._closed:return
        self._input_active=bool(active)
        self.history.expire(self._clock())
        self._refresh()

    def use_chat_preset(self):
        """恢复估算的聊天附近位置；不检测或操作游戏聊天框。"""
        if self._closed:return
        self.settings.overlay_placement='chat'
        self.settings.overlay_anchor=None
        self.settings.overlay_bottom_anchor=None
        self._reanchor=True
        self._rendered=None
        self._refresh(force_place=True)

    def use_bottom_preset(self):
        """恢复底部中央估算位置，不读取游戏画面或覆盖自定义坐标协议。"""
        if self._closed:return
        self.settings.overlay_placement='bottom'
        self.settings.overlay_anchor=None
        self.settings.overlay_bottom_anchor=None
        self._reanchor=True
        self._rendered=None
        self._refresh(force_place=True)

    def _baseline_bottom(self):
        left,top,right,bottom=self._metrics.work_area
        if getattr(self.settings,'overlay_placement','custom')=='chat':
            return top+round((bottom-top)*.62)
        anchor=getattr(self.settings,'overlay_bottom_anchor',None)
        return None if anchor is None else top+round((bottom-top)*anchor)

    def input_position(self):
        """输入条使用同一底边基准，在原生聊天估算区域下方放置。"""
        if getattr(self.settings,'overlay_placement','custom')=='bottom':return None
        baseline=self._baseline_bottom()
        if baseline is None:baseline=self.settings.overlay_y+self._layout.height
        return self._placement_x(),baseline+self._metrics.px(84)

    def _placement_x(self):
        metrics=self._metrics
        if getattr(self.settings,'overlay_placement','custom')=='chat':
            return metrics.work_area[0]+metrics.px(40)
        if self._reanchor and self.settings.overlay_anchor is not None:
            # 输入条也可能在浮窗隐藏期间先使用更新后的显示指标。
            return overlay_layout(metrics,width=self.settings.overlay_width,height=1,
                                  anchor=self.settings.overlay_anchor).x
        return self.settings.overlay_x

    def _record_position(self,width,height):
        self.settings.record_overlay_anchor(self._metrics,width,height)
        # 兼容仅实现旧相对锚点协议的设置对象；底边按整个工作区归一化。
        left,top,right,bottom=self._metrics.work_area
        self.settings.overlay_bottom_anchor=min(1.,max(0.,
            (self.settings.overlay_y+height-top)/(bottom-top)))

    def _apply_rounding(self):
        # 颜色键字幕没有实体外框，避免区域裁剪描边；面板保留兼容圆角。
        if self._subtitle:
            if self._rounded_size is not None and hasattr(self._native,'clear_rounded_corners'):
                self._native.clear_rounded_corners(self.window)
            self._rounded_size=None
            return
        if not hasattr(self._native,'set_rounded_corners'):return
        self.window.update_idletasks()
        width,height=self.window.winfo_width(),self.window.winfo_height()
        size=(width,height,self._metrics.px(10))
        if width>1 and height>1 and size!=self._rounded_size:
            self._native.set_rounded_corners(self.window,size[2])
            self._rounded_size=size

    def _place(self,height,*,placing):
        metrics=self._metrics
        if getattr(self.settings,'overlay_placement','custom')=='bottom':
            self._layout=bottom_overlay_layout(metrics,width=self.settings.overlay_width,height=height)
            self.settings.overlay_x,self.settings.overlay_y=self._layout.x,self._layout.y
            self.window.geometry(self._layout.geometry)
            self._apply_rounding()
            if placing:self._reanchor=False
            return
        x,y=self.settings.overlay_x,self.settings.overlay_y
        baseline=self._baseline_bottom()
        if placing:
            if baseline is None and self._reanchor and self.settings.overlay_anchor is not None:
                # 首次迁移先按真实高度恢复旧锚点，避免固化启动时300高的临时夹紧位置。
                legacy=overlay_layout(metrics,width=self.settings.overlay_width,height=height,
                                      anchor=self.settings.overlay_anchor)
                x,y=legacy.x,legacy.y
            else:
                # 迁移后旧锚点负责水平位置，竖向由固定底边负责。
                x=self._placement_x()
                if baseline is not None:y=baseline-height
        self._layout=overlay_layout(metrics,width=self.settings.overlay_width,height=height,x=x,y=y)
        self.settings.overlay_x,self.settings.overlay_y=self._layout.x,self._layout.y
        self.window.geometry(self._layout.geometry)
        self._apply_rounding()
        if placing:
            if self.settings.overlay_anchor is None or baseline is None:
                self._record_position(self._layout.width,self._layout.height)
            self._reanchor=False

    @staticmethod
    def _preview(text,limit=180):
        text=' '.join(str(text).split())
        return text if len(text)<=limit else text[:limit-1]+'…'

    def _refresh(self,*,force_place=False):
        # 预设、输入与样式回调也可能先于已到期的Tk定时器执行。
        self.history.expire(self._clock())
        limit=1 if self._subtitle else 2
        entries=list(self.history.entries.values())[-limit:] if self.settings.overlay_enabled else []
        content=[]
        for entry in entries:
            message=entry.message
            translation=message.engine_translation
            original=(' '.join(str(message.original).split()) if self._subtitle
                      else self._preview(message.original))
            sender=self._preview(message.event.sender,40)
            if translation:
                main=' '.join(str(translation).split()) if self._subtitle else self._preview(translation)
            else:main='待翻译：'+original
            repeated=f' ×{entry.count}' if entry.count>1 else ''
            content.append((f'{sender}{repeated}：{main}',original if self.settings.overlay_original and translation else ''))
        if self._editing and not content:
            previews=(('队友：请先集合，等大家到齐后再进攻。','Group up before the next fight.'),
                      ('队友：我会保护后排，注意小地图。','후방을 지킬게요. 미니맵을 봐 주세요.'))
            content=[(translated,original if self.settings.overlay_original else '')
                     for translated,original in previews[:limit]]
        signature=(tuple(content),self._editing,self._subtitle)
        if signature!=self._rendered:
            self._rendered=signature
            self.placeholder.pack_forget()
            if self._subtitle:self._render_subtitle(content)
            else:self._render_panel(content)
            self.window.update_idletasks()
            height=max(1,self.window.winfo_reqheight())
            self._place(height,placing=bool(entries or self._editing or force_place))
        visible=bool(self.settings.overlay_enabled and not self._input_active and (entries or self._editing))
        if visible and not self._visible:
            self._native.show_no_activate(self.window)
            self._apply_rounding()
        elif not visible and self._visible:
            self.window.withdraw()
        self._visible=visible
        self._schedule_expiry()

    def _render_subtitle(self,content):
        for row in self.rows:row['frame'].pack_forget()
        canvas=self.subtitle_canvas
        canvas.delete('all')
        metrics=self._metrics
        edge=metrics.px(1)
        # Tk文字bbox左右各多出1px，描边再各占edge，留足空间避免裁字。
        canvas_width=max(1,self._layout.width-metrics.px(6)*2)
        wrap=max(1,canvas_width-2*edge-4)
        y=edge
        if content:
            translated,original=content[-1]
            bounds=draw_outlined_text(canvas,translated,x=canvas_width/2,y=y,width=wrap,
                                      font=self._translation_font,max_lines=1 if original else 2,edge=edge)
            y=bounds[3] if bounds else y
            if original:
                y+=metrics.px(2)
                bounds=draw_outlined_text(canvas,original,x=canvas_width/2,y=y,width=wrap,
                                          font=self._original_font,max_lines=1,edge=edge)
                y=bounds[3] if bounds else y
        canvas.configure(width=canvas_width,height=max(1,y+edge))
        canvas.pack(fill='x')

    def _render_panel(self,content):
        self.subtitle_canvas.pack_forget()
        metrics=self._metrics
        self.window.update_idletasks()
        header_height=self.header.winfo_reqheight() if self._editing else 0
        max_height=metrics.work_area[3]-metrics.work_area[1]
        content_height=max(1,max_height-header_height-metrics.px(14))
        row_height=max(1,content_height//max(1,len(content))-metrics.px(8))
        translation_line=self._translation_font.metrics('linespace')
        original_line=self._original_font.metrics('linespace')
        for index,row in enumerate(self.rows):
            if index>=len(content):
                row['frame'].pack_forget()
                continue
            translated,original=content[index]
            if original:
                original_height=min(original_line,max(1,row_height*2//5-metrics.px(2)))
                self._fit_label(row['original'],original,original_height)
                translated_height=min(translation_line,max(1,row_height-original_height-metrics.px(2)))
                self._fit_label(row['translation'],translated,translated_height)
                row['original'].pack(fill='x',pady=(metrics.px(2),0))
            else:row['original'].pack_forget()
            if not original:self._fit_label(row['translation'],translated,min(row_height,translation_line*2))
            row['frame'].pack(fill='x',pady=(metrics.px(3),metrics.px(5)))

    def _schedule_expiry(self):
        deadline=self.history.next_expiry if self.settings.overlay_enabled else None
        if deadline==self._scheduled_expiry:return
        self._cancel_timer()
        if deadline is not None:
            self._scheduled_expiry=deadline
            delay=max(1,math.ceil((deadline-self._clock())*1000))
            self._timer=self.root.after(delay,self._expire)

    def _cancel_timer(self):
        if self._timer is not None:
            try:self.root.after_cancel(self._timer)
            except tk.TclError:pass
        self._timer=None
        self._scheduled_expiry=None

    def _expire(self):
        if self._closed:return
        self._cancel_timer()
        self.history.expire(self._clock())
        self._refresh()

    def _start_drag(self,event):
        if self._editing:
            self._drag_origin=(event.x_root,event.y_root,self.settings.overlay_x,self.settings.overlay_y)

    def _drag(self,event):
        if not self._editing or self._drag_origin is None:return
        start_x,start_y,x,y=self._drag_origin
        self.settings.overlay_x=int(x+event.x_root-start_x)
        self.settings.overlay_y=int(y+event.y_root-start_y)
        layout=overlay_layout(self._metrics,width=self.settings.overlay_width,
                              height=self.window.winfo_height(),x=self.settings.overlay_x,y=self.settings.overlay_y)
        self.settings.overlay_x,self.settings.overlay_y=layout.x,layout.y
        self.window.geometry(layout.geometry)
        self.settings.overlay_placement='custom'
        self._record_position(layout.width,layout.height)
        self._reanchor=False
        self._apply_rounding()

    def _end_drag(self,event):
        if self._drag_origin is not None:
            self.settings.overlay_placement='custom'
            self._record_position(self.window.winfo_width(),self.window.winfo_height())
        self._drag_origin=None

    def close(self):
        if self._closed:return
        self._closed=True
        self._cancel_timer()
        self.history.clear()
        self.window.destroy()
