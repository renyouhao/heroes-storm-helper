import tkinter as tk
from tkinter import ttk,messagebox,simpledialog,font as tkfont
from .. import APP_NAME
from .viewmodel import AppViewModel
from ..models import AppEvent
from ..channels import CHANNEL_PRESETS
from ..clock import month_key
from ..usage import BudgetError
from ..window_settings import WindowSettings,screen_bounds
from ..hotkeys import HotkeyManager
from ..appearance import normalize_theme
from ..layout import display_metrics,main_layout
from .compact_window import apply_mode,position_geometry
from .theme import apply_theme, PALETTE_NAMES, theme_colors
from .chat_feed import ChatFeed
from .rounded_panel import RoundedPanel
from ..loot import LootHelper, LootStatus
from .loot_panel import LootPanel
from ..repeat import RepeatHelper, RepeatStatus
from .repeat_panel import RepeatPanel


class MainWindow:
    def __init__(self,root,controller,settings,display=None,enable_repeat_hotkey=False):
        self.root,self.controller,self.settings=root,controller,settings if isinstance(settings,WindowSettings) else WindowSettings()
        self.display=display
        self._layout_guard_timer=None
        self._applying_layout=True
        self._action_revision=0
        self._action_timer=None
        self._pending_action=None
        self._repeat_hotkey_enabled=bool(enable_repeat_hotkey)
        self._repeat_listener_started=False
        self._layout_metrics=initial_metrics=self._read_display_metrics()
        if display is not None:self.settings.migrate_display(initial_metrics)
        self.vm=AppViewModel(); self.closed=False;self._rendered_rows={}
        self.credential_dialog=None
        root.title(APP_NAME); root.geometry('1080x720'); root.minsize(540,420)
        root.columnconfigure(0,weight=1);root.rowconfigure(0,weight=1)
        style=apply_theme(root,self.settings)
        self._applied_theme=self.settings.color_theme
        # 持有命名字体，使DPI运行时能让已有控件的字体缓存失效。
        self._content_font=tkfont.Font(root=root,family='Microsoft YaHei UI',size=self.settings.font_size)
        style.configure('Treeview',rowheight=32,font=self._content_font)
        style.configure('TButton',padding=(10,6))
        self.shell=ttk.Frame(root);self.shell.grid(row=0,column=0,sticky='nsew')
        self.shell.columnconfigure(1,weight=1);self.shell.rowconfigure(0,weight=1)
        self.sidebar=ttk.Frame(self.shell,padding=(16,22,12,16),style='Surface.TFrame')
        self.sidebar.grid(row=0,column=0,sticky='ns',padx=(0,4))
        ttk.Label(self.sidebar,text='风暴英雄助手',style='Brand.TLabel').pack(anchor='w',pady=(0,6))
        ttk.Label(self.sidebar,text='跨语言，聊得来',style='Surface.Muted.TLabel').pack(anchor='w',pady=(0,26))
        self.navigation={}
        for key,label in (('chat','实时消息'),('phrases','常用短句'),('loot','开箱助手'),('repeat','动作连按')):
            button=ttk.Button(self.sidebar,text=label,style='Sidebar.TButton',
                              command=lambda page=key:self.select_section(page),width=0)
            button.pack(fill='x',pady=(0,8));self.navigation[key]=button
        ttk.Separator(self.sidebar).pack(fill='x',pady=(8,12))
        ttk.Button(self.sidebar,text='偏好设置',command=self.window_options,style='Sidebar.TButton',width=0).pack(fill='x')
        self.sidebar_theme=ttk.Label(self.sidebar,text=PALETTE_NAMES[self.settings.color_theme],
                                     style='Surface.Muted.TLabel')
        self.sidebar_theme.pack(side='bottom',anchor='w',pady=(10,0))
        self.sidebar_hotkey=ttk.Label(self.sidebar,text='游戏中按 Ctrl+T\n随时打开快捷输入',
                  style='Surface.Muted.TLabel',justify='left',wraplength=160)
        self.sidebar_hotkey.pack(side='bottom',anchor='w')
        self.workspace=workspace=ttk.Frame(self.shell)
        workspace.grid(row=0,column=1,sticky='nsew');workspace.columnconfigure(0,weight=1)
        workspace.rowconfigure(3,weight=1)
        self.section_title=tk.StringVar(value='实时消息')
        toolbar=ttk.Frame(workspace,padding=(18,16,18,8)); toolbar.grid(row=0,column=0,sticky='ew')
        self.toolbar=toolbar
        ttk.Label(toolbar,textvariable=self.section_title,style='PageTitle.TLabel').pack(side='left')
        self.settings_button=ttk.Button(toolbar,text='设置',command=self.window_options,style='Nav.TButton')
        self.settings_button.pack(side='right')
        self.quick_button=ttk.Button(toolbar,text='快捷输入',command=self.open_quick_input,style='Accent.TButton')
        self.quick_button.pack(side='right',padx=(8,8))
        self.automatic=tk.BooleanVar(value=self.settings.auto_translate)
        service_row=ttk.Frame(workspace,padding=(18,0,18,5));service_row.grid(row=1,column=0,sticky='ew')
        self.service_row=service_row
        service_row.columnconfigure(0,weight=1)
        self.usage_label=ttk.Label(service_row,text='额度未确认 · 继续接收原文',style='Muted.TLabel',width=1)
        self.usage_label.grid(row=0,column=0,sticky='ew')
        self.account_button=None
        if hasattr(controller,'account_setup'):
            self.account_button=ttk.Button(service_row,text='个人密钥' if controller.cloud_configured else '配置云翻译',command=self.configure_account,style='Nav.TButton')
            self.account_button.grid(row=0,column=1,padx=(8,0))
        self.cloud=tk.BooleanVar(value=False)
        controller.pause_translation(True)
        controller.pause_automatic(not self.automatic.get())
        channel_row=ttk.Frame(workspace,padding=(18,3,18,10));channel_row.grid(row=2,column=0,sticky='ew')
        self.channel_row=channel_row
        channel_row.columnconfigure(2,weight=1)
        ttk.Label(channel_row,text='频道',style='Muted.TLabel').grid(row=0,column=0)
        self.channel=tk.StringVar(value='全部聊天')
        self.channel_combo=ttk.Combobox(channel_row,textvariable=self.channel,values=list(CHANNEL_PRESETS),state='readonly',width=15)
        self.channel_combo.grid(row=0,column=1,padx=(8,12))
        self.channel_combo.bind('<<ComboboxSelected>>',lambda e:controller.set_channels(CHANNEL_PRESETS[self.channel.get()]))
        self.capture_state=tk.StringVar(value='采集：自动连接中')
        ttk.Label(channel_row,textvariable=self.capture_state,style='Muted.TLabel',width=1).grid(row=0,column=2,sticky='ew')
        ttk.Checkbutton(channel_row,text='自动翻译',variable=self.automatic,command=self.toggle_automatic).grid(row=0,column=3,padx=(8,0))
        self.process_choice=None
        self.notebook=ttk.Notebook(workspace,style='Main.TNotebook'); self.notebook.grid(row=3,column=0,sticky='nsew',padx=16,pady=(0,10))
        self.notebook.enable_traversal()
        self.notebook.bind('<<NotebookTabChanged>>',lambda event:self._sync_navigation())
        self.chat_page=ttk.Frame(self.notebook); self.notebook.add(self.chat_page,text='实时消息')
        self.message_panel=RoundedPanel(self.chat_page,settings=self.settings,padding=6)
        self.message_panel.pack(fill='both',expand=True)
        self.tree=ChatFeed(self.message_panel.body,font=self._content_font,settings=self.settings)
        self.tree.pack(fill='both',expand=True)
        self.tree.bind('<<TreeviewSelect>>',lambda e:self.show_selected())
        self.tree.bind('<<MessageDetails>>',lambda e:self.toggle_details())
        message_actions=ttk.Frame(self.chat_page,padding=(0,4,0,0));message_actions.pack(fill='x')
        ttk.Label(message_actions,text='译文优先 · 可选择消息重试',style='Muted.TLabel',width=1).pack(side='left',fill='x',expand=True)
        self.retry_button=ttk.Button(message_actions,text='重试所选',command=self.retry,style='Nav.TButton',state='disabled')
        self.retry_button.pack(side='right')
        self.details_button=ttk.Button(message_actions,text='消息详情',command=self.toggle_details,style='Nav.TButton')
        self.details_button.pack(side='right',padx=(0,6))
        self.detail=tk.Text(self.chat_page,height=3,wrap='word',font=self._content_font,state='disabled')
        self._details_open=False
        if hasattr(controller,'phrases'):
            from .phrase_panel import PhrasePanel
            self.phrase_panel=PhrasePanel(self.notebook,controller.phrases,self.use_phrase,self.clear_draft)
            self.notebook.add(self.phrase_panel,text='常用短句')
        self.loot_helper=LootHelper()
        self._loot_active=False
        self.loot_panel=LootPanel(self.notebook,on_prepare=self.prepare_loot,on_cancel=self.cancel_loot,
                                  settings=self.settings,metrics=initial_metrics)
        self.notebook.add(self.loot_panel,text='开箱助手')
        self.repeat_helper=RepeatHelper()
        self._repeat_active=False
        self._repeat_binding=self.settings.repeat_hotkey
        self.repeat_panel=RepeatPanel(self.notebook,on_prepare=self.prepare_repeat,on_cancel=self.cancel_repeat,
                                      settings=self.settings,metrics=initial_metrics)
        self.notebook.add(self.repeat_panel,text='动作连按')
        self.reply_panel=RoundedPanel(workspace,settings=self.settings,padding=12,fit_height=True)
        self.reply_panel.grid(row=4,column=0,sticky='ew',padx=16,pady=(0,3))
        send=self.reply_panel.body
        self.send_frame=send
        send.columnconfigure((0,1),weight=1,uniform='draft')
        self.composer_heading=ttk.Label(send,text='用中文回复',style='ComposerTitle.TLabel')
        self.composer_heading.grid(row=0,column=0,columnspan=2,sticky='w',pady=(0,6))
        self.input=tk.Text(send,height=2,wrap='word',font=self._content_font,padx=8,pady=5)
        self.input.grid(row=1,column=0,columnspan=2,sticky='ew')
        self.input.bind('<<Modified>>',self.invalidate_draft)
        row=ttk.Frame(send,style='Surface.TFrame'); row.grid(row=2,column=0,columnspan=2,sticky='ew',pady=5)
        self.action_row=row
        self.target=tk.StringVar(value=self.settings.target_language)
        ttk.Radiobutton(row,text='韩语',variable=self.target,value='ko',command=self.target_changed,style='Surface.TRadiobutton').pack(side='left')
        ttk.Radiobutton(row,text='英语',variable=self.target,value='en',command=self.target_changed,style='Surface.TRadiobutton').pack(side='left',padx=(6,0))
        ttk.Button(row,text='复制译文',command=self.copy).pack(side='right')
        ttk.Button(row,text='翻译中文',command=self.translate,style='Accent.TButton').pack(side='right',padx=(8,8))
        self.preview=tk.Text(send,height=2,wrap='word',font=self._content_font,state='disabled',padx=8,pady=5)
        self.preview.grid(row=3,column=0,columnspan=2,sticky='ew')
        self.manual_source=tk.StringVar(value='')
        self.manual_source_label=ttk.Label(send,textvariable=self.manual_source,style='Surface.Muted.TLabel')
        self.manual_source_label.grid(row=4,column=0,columnspan=2,sticky='w',pady=(3,0))
        self._density_state=None
        self._sidebar_visible=None
        self._tabs_hidden=None
        self.status_var=tk.StringVar(value=self.vm.status); ttk.Label(workspace,textvariable=self.status_var,padding=(18,6),style='Muted.TLabel',width=1).grid(row=5,column=0,sticky='ew')
        self.refresh_usage()
        self.hotkeys=HotkeyManager()
        self.screen_ticks=0;self.monitor_bounds=[]
        from .overlay_window import OverlayWindow
        from .quick_input import QuickInput
        self.settings.recover_overlay([initial_metrics.work_area],scale=initial_metrics.scale)
        self.overlay=OverlayWindow(root,self.settings,metrics=initial_metrics)
        self.quick_input=QuickInput(root,controller,self.settings,self.quick_target_changed,
                                   metrics=initial_metrics,position_provider=lambda:self.overlay.input_position(),
                                   on_visibility_changed=self.quick_visibility_changed)
        self.apply_settings()
        self.enable_confirmed_cloud()
        root.bind('<Configure>',self.moved)
        root.protocol('WM_DELETE_WINDOW',self.close)
        self.timer=root.after(100,self.pump)
        self.start_timer=root.after_idle(self.start)
        self.setup_timer=None
        if hasattr(controller,'account_setup') and not controller.cloud_configured:
            self.setup_timer=root.after_idle(self.initial_account_setup)

    @staticmethod
    def set_text(widget,text):
        widget.configure(state='normal'); widget.delete('1.0','end'); widget.insert('1.0',text); widget.configure(state='disabled')

    def select_section(self, section):
        page={'chat':self.chat_page,'phrases':getattr(self,'phrase_panel',self.chat_page),
              'loot':self.loot_panel,'repeat':self.repeat_panel}.get(section,self.chat_page)
        self.notebook.select(page)
        self._sync_navigation()

    def _sync_navigation(self):
        selected=self.notebook.select()
        current=('repeat' if hasattr(self,'repeat_panel') and selected==str(self.repeat_panel)
                 else 'loot' if hasattr(self,'loot_panel') and selected==str(self.loot_panel)
                 else 'phrases' if hasattr(self,'phrase_panel') and selected==str(self.phrase_panel) else 'chat')
        self.section_title.set({'chat':'实时消息','phrases':'常用短句','loot':'开箱助手','repeat':'动作连按'}[current])
        if hasattr(self,'reply_panel'):
            if current in ('loot','repeat'):self.reply_panel.grid_remove()
            else:self.reply_panel.grid()
        for key,button in self.navigation.items():
            active=key==current
            button.configure(style='Sidebar.Selected.TButton' if active else 'Sidebar.TButton')

    def prepare_loot(self):
        self._prepare_action('loot')

    def prepare_repeat(self):
        self._prepare_action('repeat')

    def _cancel_action_request(self, kind=None):
        if kind is not None and getattr(self,'_pending_action',None)!=kind:return
        self._action_revision=getattr(self,'_action_revision',0)+1
        self._pending_action=None
        if getattr(self,'_action_timer',None) is not None:
            self.root.after_cancel(self._action_timer)
            self._action_timer=None

    def _prepare_action(self, kind):
        if self.closed:return
        self._cancel_action_request()
        if getattr(self.loot_helper,'active',False) or self._loot_active:self.cancel_loot()
        if (getattr(getattr(self,'repeat_helper',None),'active',False)
                or getattr(self,'_repeat_active',False)):self.cancel_repeat()
        if self.quick_input._visible:self.quick_input.cancel()
        revision=self._action_revision
        self._pending_action=kind
        emit=self.update_repeat_status if kind=='repeat' else self.update_loot_status
        status=RepeatStatus if kind=='repeat' else LootStatus
        emit(status('preparing','正在准备连按快捷操作…' if kind=='repeat' else '正在准备开箱快捷操作…'))

        def attempt(remaining=150):
            if self.closed or revision!=self._action_revision:return
            self._action_timer=None
            # Cancellation closes the sending gate immediately. Wait for both
            # native loops to release their hooks before starting another one.
            services=(self.loot_helper,getattr(self,'repeat_helper',None))
            if any(getattr(service,'active',False) for service in services):
                if remaining:
                    self._action_timer=self.root.after(20,lambda:attempt(remaining-1))
                else:
                    self._pending_action=None
                    emit(status('error','上次操作尚未退出，请稍后重新准备。'))
                return
            self._pending_action=None
            helper=self.repeat_helper if kind=='repeat' else self.loot_helper
            started=(helper.prepare(self.settings.repeat_hotkey) if kind=='repeat' else helper.prepare())
            if not started:emit(status('error','请等待本次模式结束后再重新准备。'))

        attempt()

    def cancel_loot(self):
        self._cancel_action_request('loot')
        self.loot_helper.cancel()
        self.update_loot_status(LootStatus('idle','本次开箱模式已结束。'))

    def cancel_repeat(self):
        self._cancel_action_request('repeat')
        helper=getattr(self,'repeat_helper',None)
        if helper is not None:
            helper.cancel()
            self.update_repeat_status(RepeatStatus('idle','本次连按模式已结束。'))

    def _sync_action_visibility(self):
        self.overlay.set_input_active(getattr(self,'_loot_active',False)
                                      or getattr(self,'_repeat_active',False) or self.quick_input._visible)

    def update_loot_status(self,status):
        try:self.loot_panel.update_status(status.state,status.detail)
        except (OSError,tk.TclError) as failure:
            self.loot_ui_failed(failure)
            return
        if status.state=='ready':self.loot_helper.confirm_ready(status.generation)
        self._loot_active=status.state in ('ready','running','paused')
        self._sync_action_visibility()

    def update_repeat_status(self,status):
        if status.state=='requested':
            if self.closed or not self.repeat_helper.accepts_status(status):return
            if self.quick_input._visible:
                self.cancel_repeat()
                return
        try:self.repeat_panel.update_status(status.state,status.detail)
        except (OSError,tk.TclError) as failure:
            self.repeat_ui_failed(failure)
            return
        if status.state=='ready':self.repeat_helper.confirm_ready(status.generation)
        self._repeat_active=status.state in ('requested','ready','running','paused')
        self._sync_action_visibility()
        if status.state=='requested':self._confirm_direct_repeat(status)
        elif status.state in ('idle','error'):self._cancel_action_request('repeat')

    def _confirm_direct_repeat(self,status):
        self._cancel_action_request()
        if self.loot_helper.active or self._loot_active:self.cancel_loot()
        revision=self._action_revision
        self._pending_action='repeat'

        def attempt(remaining=150):
            if self.closed or revision!=self._action_revision:return
            self._action_timer=None
            if not self.repeat_helper.accepts_status(status):
                self._cancel_action_request('repeat')
                return
            if self.quick_input._visible:
                self.cancel_repeat()
                return
            if self.loot_helper.active:
                if remaining:
                    self._action_timer=self.root.after(20,lambda:attempt(remaining-1))
                else:
                    self.cancel_repeat()
                    self.update_repeat_status(RepeatStatus('error','开箱线程尚未退出，本次连按已取消，请稍后再按快捷键。'))
                return
            self._pending_action=None
            # requested 浮条已成功显示；开箱发送门关闭且线程退出后才放行。
            self.repeat_helper.confirm_ready(status.generation)

        attempt()

    def repeat_ui_failed(self,failure):
        self._cancel_action_request('repeat')
        self.repeat_helper.cancel()
        self._repeat_active=False
        detail='连按状态条不可用，本次模式已停止：'+str(failure)
        self.status_var.set(detail)
        try:self.repeat_panel.update_status('error',detail)
        except (OSError,tk.TclError):pass
        self._sync_action_visibility()

    def loot_ui_failed(self,failure):
        self.loot_helper.cancel()
        self._loot_active=False
        detail='开箱状态条不可用，本次模式已停止：'+str(failure)
        self.status_var.set(detail)
        try:self.loot_panel.update_status('error',detail)
        except (OSError,tk.TclError):pass
        self._sync_action_visibility()

    def quick_visibility_changed(self,active):
        self.overlay.set_input_active(active or self._loot_active or getattr(self,'_repeat_active',False))

    def _apply_shell(self,width):
        visible=width>=self._layout_metrics.px(900)
        if visible!=self._sidebar_visible:
            self._sidebar_visible=visible
            if visible:
                self.sidebar.grid();self.settings_button.pack_forget()
            else:
                self.sidebar.grid_remove();self.settings_button.pack(side='right',before=self.quick_button)
        if self._tabs_hidden!=visible:
            style=ttk.Style(self.root)
            style.layout('Main.TNotebook.Tab',[] if visible else style.layout('TNotebook.Tab'))
            self._tabs_hidden=visible
            self._sync_navigation()

    def clear_draft(self):
        self.vm.copy_text=''; self.vm.expected_job=None;self.vm.manual_provenance=''
        self.render_manual_preview()

    def render_manual_preview(self):
        self.set_text(self.preview,self.vm.copy_text)
        self.manual_source.set('来源：'+self.vm.manual_provenance if self.vm.copy_text and self.vm.manual_provenance else '')

    def invalidate_draft(self,event=None):
        if self.input.edit_modified(): self.clear_draft(); self.input.edit_modified(False)

    def _start_repeat_listener(self):
        if self.closed or not self._repeat_hotkey_enabled:return
        self._repeat_listener_started=True
        try:
            if not self.repeat_helper.start_listening(self.settings.repeat_hotkey):
                self.repeat_ui_failed(OSError('连按快捷键监听未能启动，请稍后重新保存设置。'))
        except (OSError,ValueError) as failure:self.repeat_ui_failed(failure)

    def start(self):
        if getattr(self,'_repeat_hotkey_enabled',False):self._start_repeat_listener()
        self.controller.start_receiving()
    def stop(self): self.controller.stop_receiving()

    def enable_confirmed_cloud(self):
        self.refresh_usage()
        enabled=(self.vm.can_translate and getattr(self.controller,'cloud_configured',True)
                 and not getattr(self.controller,'configuration_held',False))
        self.cloud.set(enabled)
        self.controller.pause_translation(not enabled)

    def toggle_automatic(self):
        self.settings.auto_translate=self.automatic.get()
        self.controller.pause_automatic(not self.automatic.get())
        if self.automatic.get() and not self.vm.can_translate:
            self.status_var.set('额度未确认：在设置中确认本月用量后即可翻译，原文继续接收。')
        self.save_settings()

    def open_quick_input(self):
        self.cancel_loot()
        self.cancel_repeat()
        try:self.quick_input.open()
        except (OSError,tk.TclError):
            self.status_var.set('快捷输入暂时无法打开，请再试一次或使用主窗口中文输出。')

    def target_changed(self):
        self.settings.target_language=self.target.get()
        self.clear_draft();self.save_settings()

    def quick_target_changed(self):
        self.target.set(self.settings.target_language)
        self.clear_draft();self.save_settings()

    def choose_process(self,processes=None):
        processes=processes if processes is not None else self.controller.source.process_finder()
        ids=tuple(p.pid for p in processes)
        if not ids:
            messagebox.showinfo('选择游戏进程','尚未检测到可访问的游戏进程。',parent=self.root);return
        chosen=simpledialog.askinteger('选择游戏进程','填写其中一个 PID：'+', '.join(str(i) for i in ids),parent=self.root)
        if chosen in ids:
            self.process_choice=ids;self.controller.source.selected_pid=chosen;self.controller.source.reconnect()

    def moved(self,event):
        if event.widget is not self.root:return
        self._apply_shell(event.width)
        self._apply_density(event.height)
        if self._applying_layout:return
        if self.display is not None:
            metrics=self._read_display_metrics()
            if metrics!=self._layout_metrics:
                # WM可先于低频轮询调整窗口；该移动不能当作用户拖动保存。
                self.apply_settings(layout_only=True,metrics=metrics)
                return
        if self.settings.locked:
            if event.x!=self.settings.x or event.y!=self.settings.y:
                self.root.geometry(position_geometry(self.settings.x,self.settings.y))
        elif (event.x,event.y)!=(self.settings.x,self.settings.y):
            self.settings.x,self.settings.y=event.x,event.y
            if self.display is not None:
                self.settings.record_main_anchor(self._layout_metrics,self.root.winfo_width(),self.root.winfo_height())

    def layout_columns(self,event=None):
        width=self.tree.winfo_width()-8
        if width<=20:return
        state=max(60,round(width*.20))
        text=max(40,(width-state)//2)
        for key,value in (('original',text),('translation',text),('state',state)):
            self.tree.column(key,width=value,minwidth=min(value,60),stretch=True)

    def _read_display_metrics(self):
        if self.display is not None:return self.display.refresh()
        return display_metrics(self.root,screen_bounds())

    def _finish_layout(self):
        self._layout_guard_timer=None
        self._applying_layout=False

    def _apply_density(self,height):
        """真正缩到小窗口时减少占位，输入与译文并排，保留阅读和操作空间。"""
        tight=self._layout_tight or height<self._layout_metrics.px(600)
        minimal=height<self._layout_metrics.px(480)
        state=(tight,minimal,self._layout_metrics.scale)
        if self._density_state==state:return
        self._density_state=state
        self._density_tight=tight
        px=self._layout_metrics.px
        self.toolbar.configure(padding=(px(18),px(6 if minimal else 16),px(18),px(4 if minimal else 8)))
        self.channel_row.configure(padding=(px(18),px(3),px(18),px(4 if minimal else 10)))
        self.notebook.grid_configure(pady=(0,px(4 if minimal else 10)))
        self.reply_panel.set_padding(6 if minimal else 12)
        for widget in (self.input,self.preview):widget.configure(height=1 if tight else 2)
        if tight:self.detail.pack_forget()
        elif self._details_open:self.detail.pack(fill='x',pady=(4,0))
        if minimal:
            self.service_row.grid_remove()
            self.composer_heading.grid_remove()
            self.manual_source_label.grid_remove()
        else:
            self.service_row.grid()
            self.composer_heading.grid()
            self.manual_source_label.grid()
        if tight:
            self.input.grid_configure(row=1,column=0,columnspan=1,padx=(0,px(4)))
            self.preview.grid_configure(row=1,column=1,columnspan=1,padx=(px(4),0))
        else:
            self.input.grid_configure(row=1,column=0,columnspan=2,padx=0)
            self.preview.grid_configure(row=3,column=0,columnspan=2,padx=0)

    def apply_settings(self,layout_only=False,metrics=None):
        self._applying_layout=True
        if self.settings.color_theme!=self._applied_theme:
            apply_theme(self.root,self.settings)
            self._applied_theme=self.settings.color_theme
            self._tabs_hidden=None
        self._layout_metrics=metrics=metrics if metrics is not None else self._read_display_metrics()
        self.monitor_bounds=[metrics.work_area]
        layout=main_layout(metrics,mode=self.settings.mode,x=self.settings.x,y=self.settings.y,
                           font_size=self.settings.font_size,anchor=self.settings.main_anchor)
        self.settings.x,self.settings.y=layout.x,layout.y
        if self.display is not None and self.settings.main_anchor is None:
            self.settings.record_main_anchor(metrics,layout.width,layout.height)
        self.root.minsize(layout.min_width,layout.min_height)
        self.root.geometry(layout.geometry)
        self.root.attributes('-alpha',self.settings.alpha)
        self.root.attributes('-topmost',self.settings.topmost)
        # 字号较大或精简模式优先给聊天留空间；详情只在主动展开时出现。
        tight=layout.collapse_details or self.settings.font_size>18
        self._layout_tight=tight
        if tight or not self._details_open:self.detail.pack_forget()
        else:self.detail.pack(fill='x',pady=(4,0))
        self._apply_density(layout.height)
        self._apply_shell(layout.width)
        if self._content_font.cget('size')!=self.settings.font_size:
            self._content_font.configure(size=self.settings.font_size)
        ttk.Style(self.root).configure('Treeview',font=self._content_font,rowheight=layout.row_height)
        ttk.Style(self.root).configure('TButton',padding=(metrics.px(10),metrics.px(6)))
        self.tree.apply_settings(self.settings)
        self.message_panel.apply_settings(self.settings,metrics)
        self.reply_panel.apply_settings(self.settings,metrics)
        self.sidebar_theme.configure(text=PALETTE_NAMES[normalize_theme(self.settings.color_theme)])
        if hasattr(self,'phrase_panel'):self.phrase_panel.apply_metrics(metrics)
        try:self.loot_panel.apply_settings(self.settings,metrics)
        except (OSError,tk.TclError) as failure:self.loot_ui_failed(failure)
        if self._repeat_binding!=self.settings.repeat_hotkey:
            self.cancel_repeat()
            self._repeat_binding=self.settings.repeat_hotkey
            if self._repeat_listener_started:self._start_repeat_listener()
        try:self.repeat_panel.apply_settings(self.settings,metrics)
        except (OSError,tk.TclError) as failure:self.repeat_ui_failed(failure)
        self.overlay.apply_settings(metrics)
        self.quick_input.refresh_layout(metrics,force=True)
        self.quick_button.configure(text='快捷输入')
        binding=self.settings.hotkeys.get('focus')
        self.sidebar_hotkey.configure(text=('游戏中按 '+binding+'\n打开快捷输入') if binding
                                      else '从右上角打开\n快捷输入')
        if not layout_only:
            self.hotkeys.start(self.settings.hotkeys,lambda data:self.controller._source_emit(AppEvent(data['kind'],self.controller.generation,data)))
        if self._layout_guard_timer is not None:self.root.after_cancel(self._layout_guard_timer)
        self._layout_guard_timer=self.root.after_idle(self._finish_layout)

    def window_options(self):
        self.cancel_loot()
        self.cancel_repeat()
        from .settings_dialog import SettingsDialog
        SettingsDialog(self)

    def configure_account(self):
        if self.closed or not hasattr(self.controller,'account_setup'):return
        if self.credential_dialog is not None:
            self.credential_dialog.dialog.lift();return
        from .credential_dialog import CredentialDialog
        self.credential_dialog=CredentialDialog(self)

    def initial_account_setup(self):
        self.setup_timer=None
        if not self.closed and self.root.state()!='withdrawn':self.configure_account()

    def _account_events(self):
        service=getattr(self.controller,'account_setup',None)
        if service is None:return
        for event in service.poll():
            if event['kind'] in ('ready','removed'):
                self.clear_draft();self.quick_input.cancel()
                self.enable_confirmed_cloud()
                self.account_button.configure(text='个人密钥' if self.controller.cloud_configured else '配置云翻译')
            if self.credential_dialog is not None:self.credential_dialog.handle(event)
            if event['kind']=='error':self.status_var.set(event['message'])

    def save_settings(self):
        path=getattr(self.controller,'preferences_path',None)
        if path:self.settings.save(path)

    def capture_settings_state(self):
        return self.overlay.capture_settings_state()

    def restore_settings_state(self, snapshot):
        self.overlay.restore_settings_state(snapshot)

    def toggle_cloud(self):
        self.refresh_usage()
        if self.cloud.get() and not self.vm.can_translate:
            self.cloud.set(False); messagebox.showinfo('用量未确认','请先在阿里云控制台核对本月其他用量，再确认基线。',parent=self.root)
        self.controller.pause_translation(not self.cloud.get())

    def confirm_usage(self):
        if not getattr(self.controller,'cloud_configured',True):
            self.configure_account();return
        value=simpledialog.askinteger('确认本月其他调用量','填写本月此主账号在本助手之外已使用的字符数。\n请先核对阿里云用量；未知时取消。\n同月基线只可增加，助手本身预留量无需重复填写。',minvalue=0,parent=self.root)
        if value is None: return
        try:
            self.controller.ledger.confirm_baseline(self.controller.account,month_key(self.controller.clock()),value,self.controller.clock())
            self.enable_confirmed_cloud()
        except BudgetError as failure: messagebox.showerror('用量保护',str(failure),parent=self.root)

    def refresh_usage(self):
        try:
            snap=self.controller.ledger.snapshot(self.controller.account,self.controller.clock())
            self.vm.apply(AppEvent('usage',self.vm.generation,snap))
            self.usage_label.configure(text=(f'本月剩余 {snap.remaining:,} 字符 · 用量已确认' if snap.confirmed
                                             else '用量未确认 · 原文继续接收，可在设置中确认本月用量'))
            if not getattr(self.controller,'cloud_configured',True):
                self.usage_label.configure(text='尚未配置个人密钥 ｜ 继续接收原文；点击“配置云翻译”即可启用。')
        except BudgetError as failure: self.status_var.set(str(failure))

    def translate(self):
        try:
            self.clear_draft()
            text=self.input.get('1.0','end-1c')
            self.vm.expected_job=self.controller.submit_manual(text,self.target.get())
        except ValueError as failure: self.status_var.set(str(failure))

    def use_phrase(self,text):
        self.input.delete('1.0','end');self.input.insert('1.0',text);self.input.edit_modified(False)
        self.clear_draft();self.input.focus_set()

    def copy(self):
        if not self.vm.copy_text:
            self.status_var.set('请先翻译并选择当前译文。'); return
        self.root.clipboard_clear(); self.root.clipboard_append(self.vm.copy_text)
        self.status_var.set('译文已复制；请在游戏中自行粘贴并发送。')

    def retry(self):
        selected=self.tree.selection()
        if selected:
            try: self.controller.retry_message(selected[0])
            except ValueError as failure: self.status_var.set(str(failure))

    def toggle_details(self):
        if not self.tree.selection():
            self.status_var.set('先选择一条消息，即可查看完整详情。');return
        if self._layout_tight or self._density_tight:
            self.status_var.set('当前窗口空间较小，可在聊天区选择并复制原文或译文。');return
        self._details_open=not self._details_open
        if self._details_open:self.detail.pack(fill='x',pady=(4,0))
        else:self.detail.pack_forget()
        self.details_button.configure(text='收起详情' if self._details_open else '消息详情')

    def show_selected(self):
        selected=self.tree.selection()
        if hasattr(self,'retry_button'):
            self.retry_button.configure(state='normal' if selected else 'disabled')
        if not selected or selected[0] not in self.vm.messages: return
        m=self.vm.messages[selected[0]]; e=m.event
        context=(' / '.join(item.text for item in self.controller.context.recent())
                 if e.generation==self.controller.generation else '历史连接消息，不显示当前对局上下文。')
        self.set_text(self.detail,f'{e.timestamp} [{e.channel}] {e.sender}\n原文：{m.original}\n中文译文：{m.engine_translation}\n本机最近三条（不上传）：'+context)

    def pump(self):
        if self.closed:return
        if getattr(self,'timer',None):self.root.after_cancel(self.timer)
        try:self._pump_events()
        except (OSError,tk.TclError):
            self.status_var.set('窗口更新暂时失败，后台继续接收；请检查窗口设置。')
        finally:
            if not self.closed:self.timer=self.root.after(100,self.pump)

    def _pump_events(self):
        for status in self.loot_helper.drain_status():
            if self.loot_helper.accepts_status(status):self.update_loot_status(status)
        helper=getattr(self,'repeat_helper',None)
        if helper is not None:
            for status in helper.drain_status():
                if helper.accepts_status(status):self.update_repeat_status(status)
        self._account_events()
        self.screen_ticks+=1
        if self.screen_ticks%20==0:
            metrics=self._read_display_metrics()
            if metrics!=self._layout_metrics:
                self.apply_settings(layout_only=True,metrics=metrics)
        events=self.controller.drain_events()
        changed=False
        for event in events:
            self.vm.apply(event)
            if hasattr(self,'quick_input'):self.quick_input.handle_event(event)
            if event.kind=='capture_state' and event.generation==self.vm.generation:
                self.capture_state.set('采集：'+str(event.payload))
            if event.kind in ('chat','result','error','generation','suppressed'): changed=True
            if event.kind in ('manual_result','generation'):self.render_manual_preview()
            if event.kind=='usage': self.refresh_usage()
            if event.kind in ('status','gap','error','generation'): self.status_var.set(self.vm.status)
            if event.kind=='conflict':self.status_var.set('热键注册失败或已被占用：'+str(event.payload['action']))
            if event.kind=='hotkey':
                action=event.payload['action']
                if action=='focus':self.open_quick_input()
                elif action=='translate':self.translate()
                elif action=='copy':self.copy()
            if event.kind=='processes' and len(event.payload)>1:
                ids=tuple(p.pid for p in event.payload)
                if self.process_choice!=ids:
                    self.process_choice=ids
                    self.status_var.set('检测到多个游戏进程，请在设置 → 连接中选择进程。')
        if changed:
            if hasattr(self,'overlay'):self.overlay.sync(self.vm.messages,self.vm.generation)
            if isinstance(self.tree,ChatFeed):
                self.tree.sync(self.vm.messages,self.vm.generation)
                self.show_selected()
                return
            wanted=set(self.vm.messages)
            for key in self.tree.get_children():
                if key not in wanted:
                    self.tree.delete(key);self._rendered_rows.pop(key,None)
            for key,m in self.vm.messages.items():
                values=(m.original,m.engine_translation,m.status)
                if self._rendered_rows.get(key)!=values:
                    if self.tree.exists(key): self.tree.item(key,values=values)
                    else:self.tree.insert('','end',iid=key,values=values)
                    self._rendered_rows[key]=values
            self.show_selected()

    def close(self):
        if self.closed:return
        self.closed=True
        service=getattr(self.controller,'account_setup',None)
        if service is not None:service.close()
        if getattr(self,'setup_timer',None):self.root.after_cancel(self.setup_timer)
        cleanups=(self._cancel_action_request,
                  lambda:self.root.after_cancel(self.timer),lambda:self.root.after_cancel(self.start_timer),
                  lambda:self.root.after_cancel(self._layout_guard_timer)
                  if getattr(self,'_layout_guard_timer',None) is not None else None,
                  self.loot_helper.close,self.loot_panel.close,
                  lambda:getattr(self,'repeat_helper',None) and self.repeat_helper.close(),
                  lambda:getattr(self,'repeat_panel',None) and self.repeat_panel.close(),
                  self.hotkeys.stop,self.quick_input.close,self.overlay.close,self.save_settings,
                  self.controller.close,self.root.destroy)
        # 一个原生句柄/偏好写入失败，也必须继续释放其他线程与窗口。
        for cleanup in cleanups:
            try:cleanup()
            except (OSError,ValueError,tk.TclError):pass


def create_app(root,controller,settings,display=None,enable_repeat_hotkey=False):
    return MainWindow(root,controller,settings,display=display,enable_repeat_hotkey=enable_repeat_hotkey)
