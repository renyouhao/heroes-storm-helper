"""按热键出现的中文输入条，译文仅复制，由用户在游戏中粘贴。"""
from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk,font as tkfont

from .input_native import WindowsInputNative
from .theme import overlay_colors,style_native_window
from ..appearance import DEFAULT_THEME
from ..layout import display_metrics,quick_input_layout


@dataclass(frozen=True)
class _Submission:
    job_id: str
    generation: int
    revision: int
    draft: str
    target: str


class QuickInput:
    def __init__(self, root, controller, settings, on_target_changed=None, *, native=None, clipboard=None,metrics=None,position_provider=None,on_visibility_changed=None):
        self.root, self.controller, self.settings = root, controller, settings
        self.on_target_changed = on_target_changed
        self.on_visibility_changed = on_visibility_changed
        self._native = native
        self._clipboard = clipboard or self._copy_to_clipboard
        self.window = self.entry = self.text = self.target = self.status = None
        self._generation = controller.generation
        self._revision = 0
        self._pending = None
        self._visible = False
        self._closed = False
        self._prior_window = 0
        self._own_window = 0
        self._hide_timer = None
        self._return_down = False
        self._return_composing = False
        self._metrics=metrics
        self._layout_signature=None
        self._position_provider=position_provider
        self._select_on_open=False
        self.submit_button=None
        self.target_display=None
        self._status_font=None
        self._status_width=0
        self._status_suffix='… 按 Enter 重试'

    def _create_window(self):
        self.window = tk.Toplevel(self.root)
        self.window._hots_theme_scope='overlay'
        self.window.withdraw()
        self.window.title('快捷中文输入')
        self.window.attributes('-topmost', True)
        self.window.attributes('-alpha', .94)
        self.window.configure(background=overlay_colors(self.settings)['background'])
        self.window.resizable(False, False)
        self.window.protocol('WM_DELETE_WINDOW', self.cancel)
        self.window.bind('<Unmap>', self._on_window_unmap)
        self.window.bind('<Destroy>', self._on_window_destroy)
        self.text = tk.StringVar(master=self.window)
        self.target = tk.StringVar(master=self.window, value=self.settings.target_language)
        self.target_display=tk.StringVar(master=self.window)
        self.target.trace_add('write',self._sync_target_display)
        self._sync_target_display()
        self.status = tk.StringVar(master=self.window)
        self.body = ttk.Frame(self.window,style='Overlay.TFrame')
        self.body.pack(fill='both', expand=True)
        body=self.body
        self._entry_font=tkfont.Font(root=self.window,family='Microsoft YaHei UI',
                                     size=max(12,self.settings.font_size))
        input_row=ttk.Frame(body,style='Overlay.TFrame')
        input_row.pack(fill='x')
        self.target_control=ttk.Combobox(input_row,width=5,state='readonly',
                                        textvariable=self.target_display,values=('韩语','英语'),
                                        style='Overlay.TCombobox')
        self.target_control.pack(side='left')
        self.target_control.bind('<<ComboboxSelected>>',self._select_target)
        self.entry = ttk.Entry(input_row, textvariable=self.text, width=1,font=self._entry_font,
                               style='Overlay.TEntry')
        self.entry.pack(side='left',fill='x',expand=True,padx=(8,0))
        self.submit_button=ttk.Button(input_row,text='翻译并复制',style='Overlay.Accent.TButton',command=self._submit)
        self.submit_button.pack(side='right',padx=(8,0),fill='y')
        self._status_font=tkfont.nametofont('TkDefaultFont',root=self.window)
        self.status_label=ttk.Label(body,width=1,font=self._status_font,style='Overlay.Muted.TLabel',anchor='w')
        self.status_label.pack(fill='x',pady=(6,0))
        ttk.Label(body,text='Enter 翻译并复制  ·  Esc 收起  ·  Ctrl+Tab 切换语言',
                  style='Overlay.Muted.TLabel').pack(anchor='w',pady=(3,0))
        self.status.trace_add('write',self._status_changed)
        self.text.trace_add('write', self._on_edit)
        self.entry.bind('<KeyPress-Return>', self._on_return_press)
        self.entry.bind('<KeyRelease-Return>', self._on_return_release)
        self.entry.bind('<KeyPress-KP_Enter>', self._on_return_press)
        self.entry.bind('<KeyRelease-KP_Enter>', self._on_return_release)
        self.entry.bind('<FocusOut>', self._on_focus_out)
        self.window.bind('<Escape>', lambda event: self.cancel())
        self.window.bind('<Control-Tab>', self._toggle_target)
        self.refresh_layout(self._metrics,force=True)

    def refresh_layout(self,metrics=None,*,force=False):
        """由主窗口在工作区/缩放变化时调用，也可在再次呼出时恢复位置。"""
        if self._closed:return False
        metrics=metrics or self._metrics or display_metrics(self.root)
        position=self._position_provider() if self._position_provider else None
        signature=(metrics,self.settings.font_size,position,getattr(self.settings,'color_theme',DEFAULT_THEME))
        self._metrics=metrics
        if self.window is None:return False
        if not force and signature==self._layout_signature:return False
        self._layout_signature=signature
        palette=overlay_colors(self.settings)
        self.window.configure(background=palette['background'])
        # 下拉列表由 Tk 延迟创建，局部规则防止它继承亮色主窗口的全局选项。
        if hasattr(self,'target_control'):
            popup_options={}
            for option,role in (('background','field'),('foreground','text'),
                                ('selectBackground','selection'),('selectForeground','text')):
                # 选项路径从应用名开始匹配；Tk 控件路径开头的点不能直接用于模式。
                self.window.option_add(f'*{str(self.target_control)[1:]}*Listbox.{option}',palette[role])
                popup_options['-'+option.lower()]=palette[role]
            popup=f'{self.target_control}.popdown.f.l'
            if self.window.tk.call('winfo','exists',popup):
                self.window.tk.call(popup,'configure',
                                    *[value for pair in popup_options.items() for value in pair])
        if self._entry_font.cget('size')!=max(12,self.settings.font_size):
            self._entry_font.configure(size=max(12,self.settings.font_size))
        self.body.configure(padding=(metrics.px(12),metrics.px(10)))
        layout=quick_input_layout(metrics,font_size=self.settings.font_size,position=position)
        self._status_width=max(1,layout.width-metrics.px(24))
        self._status_changed()
        self.window.update_idletasks()
        layout=quick_input_layout(metrics,font_size=self.settings.font_size,height=self.window.winfo_reqheight(),position=position)
        self.window.geometry(layout.geometry)
        style_native_window(self.window,palette)
        return True

    def _status_changed(self,*args):
        # 单行状态不改变输入位置；原始错误仍完整保存在 StringVar。
        text=' '.join(self.status.get().split())
        if self._status_font is None or not self._status_width:
            return
        displayed=text
        if len(text)>300 or self._status_font.measure(text)>self._status_width:
            suffix=self._status_suffix
            if self._status_font.measure(suffix)>self._status_width:
                displayed='…'
            else:
                low,high=0,min(len(text),300)
                while low<high:
                    middle=(low+high+1)//2
                    if self._status_font.measure(text[:middle]+suffix)<=self._status_width:
                        low=middle
                    else:
                        high=middle-1
                displayed=text[:low]+suffix
        if self.status_label.cget('text')==displayed:return
        self.status_label.configure(text=displayed)

    def open(self):
        if self._closed:
            return
        try:
            if self._native is None:
                self._native = WindowsInputNative()
            foreground = self._native.foreground_window()
            if self.window is None:
                try:
                    self._create_window()
                    self._own_window = self._native.window_handle(self.window.winfo_id())
                    self._native.attach_ime(self.entry.winfo_id())
                except (OSError, tk.TclError):
                    # 初始化是一笔事务，失败后下次呼出必须重新装好 IME 保护。
                    try:
                        self._native.detach_ime()
                    finally:
                        if self.window is not None:self.window.destroy()
                        self.window = self.entry = self.text = self.target = self.status = None
                        self.submit_button = None
                        self.target_display = None
                        self._own_window = 0
                    raise
            if foreground != self._own_window:
                self._prior_window = foreground
            self._invalidate()
            self.refresh_layout(force=True)
            self._native.clear_return_guard()
            self._generation = max(self._generation, self.controller.generation)
            self.target.set(self.settings.target_language)
            self.status.set('输入中文，译文会复制；请在游戏中粘贴发送。')
            self.window.deiconify()
            self.window.lift()
            self.entry.focus_force()
            if self._select_on_open:
                self.entry.selection_range(0,tk.END)
                self.entry.icursor(tk.END)
                self._select_on_open=False
            self._native.activate(self._own_window)
            self._set_visible(True)
        except (OSError, tk.TclError):
            # 布局、焦点或原生激活失败也必须撤回，避免字幕永久停留在输入模式。
            self._withdraw()
            raise

    def _set_visible(self, visible):
        if self._visible == visible:
            return
        self._visible = visible
        if self.on_visibility_changed:
            try:
                self.on_visibility_changed(visible)
            except (OSError, tk.TclError):
                # 字幕可能已销毁；它不能中断输入、焦点恢复或 IME 清理。
                pass

    def _withdraw(self):
        try:
            if self.window is not None:
                self.window.withdraw()
        except tk.TclError:
            pass
        finally:
            self._set_visible(False)

    def _on_window_unmap(self, event):
        if event.widget is not self.window:
            return
        # 撤回后立即重新呼出时，事件队列里仍可能有旧的 Unmap。
        try:
            hidden = self.window.state() not in ('normal', 'zoomed')
        except tk.TclError:
            hidden = True
        if hidden:
            self._set_visible(False)

    def _on_window_destroy(self, event):
        if event.widget is self.window:
            self.submit_button = None
            self._set_visible(False)

    def _set_submit_enabled(self,enabled):
        if self.submit_button is not None:
            self.submit_button.state(['!disabled' if enabled else 'disabled'])

    def _invalidate(self):
        self._revision += 1
        self._pending = None
        self._status_suffix='… 按 Enter 重试'
        self._set_submit_enabled(True)
        self._return_down = False
        self._return_composing = False
        self._cancel_hide_timer()

    def _on_edit(self, *args):
        self._select_on_open=False
        self._invalidate()
        if self.status is not None:
            self.status.set('准备就绪；译文仅复制，由你在游戏中粘贴。')

    def _target_changed(self):
        self._invalidate()
        self.settings.target_language = self.target.get()
        self.status.set('已选择' + ('韩语' if self.target.get() == 'ko' else '英语') + '；Enter 翻译并复制。')
        if self.on_target_changed:
            try:
                self.on_target_changed()
            except (OSError, ValueError) as failure:
                self.status.set('语言已切换，保存设置失败：' + str(failure))
        if self._visible:
            self.entry.focus_set()

    def _sync_target_display(self,*args):
        if self.target_display is not None:
            self.target_display.set('韩语' if self.target.get()=='ko' else '英语')

    def _select_target(self,event=None):
        self.target.set('ko' if self.target_display.get()=='韩语' else 'en')
        self._target_changed()

    def _toggle_target(self, event=None):
        if self._closed or not self._visible:
            return 'break'
        self.target.set('en' if self.target.get() == 'ko' else 'ko')
        self._target_changed()
        return 'break'

    def _on_return_press(self, event=None):
        if self._closed or not self._visible:
            return 'break'
        if not self._return_down:
            self._return_down = True
            self._return_composing = self._native.return_is_composing(self.entry.winfo_id())
        return 'break'

    def _on_return_release(self, event=None):
        armed, composing = self._return_down, self._return_composing
        self._return_down = False
        self._return_composing = False
        if self._closed or not self._visible:
            return 'break'
        still_composing = self._native.return_is_composing(self.entry.winfo_id())
        self._native.clear_return_guard()
        if armed and not composing and not still_composing:
            self._submit()
        return 'break'

    def _on_focus_out(self, event=None):
        self._return_down = False
        self._return_composing = False
        if self._pending and self._native.foreground_window() != self._own_window:
            self._invalidate()
            self.status.set('已离开输入条，取消自动复制；输入保留，可重新翻译。')

    def _submit(self):
        if self._closed or not self._visible:
            return
        draft, target = self.text.get(), self.target.get()
        pending=self._pending
        if (pending and pending.generation==self.controller.generation
                and pending.draft==draft and pending.target==target):
            return
        if self._native.return_is_composing(self.entry.winfo_id()):
            self.status.set('请先完成输入法选词，再翻译。')
            self.entry.focus_set()
            return
        self.entry.focus_set()
        self._invalidate()
        if not draft.strip():
            self.status.set('请先输入中文。')
            return
        generation = self.controller.generation
        self._generation = max(self._generation, generation)
        try:
            job = self.controller.submit_manual(draft.strip(), target)
        except (ValueError, OSError) as failure:
            self.status.set(str(failure))
            return
        self._pending = _Submission(job, generation, self._revision, draft, target)
        self._set_submit_enabled(False)
        self.status.set('正在翻译；可修改输入或 Esc 取消。')

    def handle_event(self, event):
        if self._closed:
            return
        generation = max(self._generation, self.controller.generation)
        if event.kind == 'generation':
            generation = max(generation, event.generation)
        if generation != self._generation:
            self._generation = generation
            self._invalidate()
            if self._visible:
                self.status.set('连接已更新，输入已保留；Enter 重新翻译。')
        pending = self._pending
        if (not pending or not self._visible or event.generation != pending.generation
                or event.generation != self._generation or pending.revision != self._revision
                or pending.draft != self.text.get() or pending.target != self.target.get()
                or pending.target != self.settings.target_language):
            return
        if event.kind not in ('manual_result', 'error') or not isinstance(event.payload, dict):
            return
        payload = event.payload
        if payload.get('job_id') != pending.job_id:
            return
        if self._native.foreground_window() != self._own_window:
            self._invalidate()
            self.status.set('已离开输入条，取消自动复制；输入保留，可重新翻译。')
            return
        self._pending = None
        self._set_submit_enabled(True)
        if event.kind == 'error':
            self.status.set(str(payload.get('error', '翻译失败，请按 Enter 重试。')))
            return
        translated = payload.get('translated')
        if not isinstance(translated, str) or not translated.strip():
            self.status.set('未收到有效译文，请按 Enter 重试。')
            return
        try:
            self._clipboard(translated)
        except Exception as failure:
            self.status.set('复制失败，请按 Enter 重试：' + str(failure))
            return
        preview=' '.join(translated.split())
        if len(preview)>24:preview=preview[:24]+'…'
        self._status_suffix='… 回游戏后 Ctrl+V'
        self.status.set('已复制：'+preview+'；回游戏后 Ctrl+V')
        self._select_on_open=True
        if self._restore_focus():
            self._hide_timer = self.root.after(850, self._hide_after_copy)
        else:
            self._status_suffix='… 手动切回游戏 Ctrl+V'
            self.status.set('译文已复制；请手动切回游戏粘贴，Esc 关闭输入条。')

    def _copy_to_clipboard(self, translated):
        self.root.clipboard_clear()
        self.root.clipboard_append(translated)

    def _restore_focus(self):
        if self._native and self._own_window:
            try:return bool(self._native.restore_foreground(self._prior_window, self._own_window))
            except OSError:return False
        return False

    def _cancel_hide_timer(self):
        if self._hide_timer is not None:
            try:
                self.root.after_cancel(self._hide_timer)
            except tk.TclError:
                pass
            self._hide_timer = None

    def _hide_after_copy(self):
        self._hide_timer = None
        if not self._closed and self._visible:
            self._withdraw()

    def cancel(self):
        self._invalidate()
        if self._visible:
            self._restore_focus()
            self._withdraw()
        self._prior_window = 0
        return 'break'

    def close(self):
        if self._closed:
            return
        try:
            self.cancel()
        finally:
            self._closed = True
            try:
                try:
                    if self._native:self._native.detach_ime()
                except OSError:
                    # 销毁控件时 WM_NCDESTROY 仍会拆除回调，保持回调对象活到销毁结束。
                    pass
                finally:
                    if self.window is not None:self.window.destroy()
            finally:
                # The final visibility notification must run before releasing
                # callbacks bound to the main Tk window.
                self.on_target_changed = self.on_visibility_changed = self._position_provider = None
