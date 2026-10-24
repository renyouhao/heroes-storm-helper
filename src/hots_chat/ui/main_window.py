import tkinter as tk
from pathlib import Path
import sys
import uuid
import webbrowser
from tkinter import ttk,messagebox,simpledialog,font as tkfont
from .. import APP_NAME,DISPLAY_VERSION
from .viewmodel import AppViewModel
from ..models import AppEvent
from ..channels import CHANNEL_PRESETS
from ..window_settings import WindowSettings,screen_bounds
from ..hotkeys import HotkeyManager
from ..appearance import normalize_theme
from ..layout import display_metrics,main_layout
from .compact_window import apply_mode,position_geometry
from .theme import apply_theme, refresh_theme_scale, PALETTE_NAMES, theme_colors
from .chat_feed import ChatFeed
from .rounded_panel import RoundedPanel
from ..loot import LootHelper, LootStatus
from .loot_panel import LootPanel
from ..repeat import RepeatHelper, RepeatStatus, validate_repeat_config
from .repeat_panel import RepeatPanel
from ..talent_service import TalentHelper
from .talent_panel import TalentPanel
from ..update_service import UpdateService
from ..board_service import BoardHelper
from .board_panel import BoardPanel
from .workspace_icons import WorkspaceIcons
from .brand_icon import apply_assistant_icon


class MainWindow:
    def __init__(self,root,controller,settings,display=None,enable_repeat_hotkey=False,enable_talent_helper=False,
                 enable_update_check=False):
        self.root,self.controller,self.settings=root,controller,settings if isinstance(settings,WindowSettings) else WindowSettings()
        self.display=display
        self._layout_guard_timer=None
        self._applying_layout=True
        self._action_revision=0
        self._action_timer=None
        self._pending_action=None
        self._repeat_hotkey_enabled=bool(enable_repeat_hotkey)
        self._repeat_listener_started=False
        self._talent_runtime_enabled=bool(enable_talent_helper)
        self._update_check_enabled=bool(enable_update_check) and not getattr(controller,'is_demo',False)
        self._update_installing=False
        self._feedback_backend=None
        self._notice_target=None
        preferences=getattr(controller,'preferences_path',None)
        self.update_service=UpdateService(staging_root=Path(preferences).parent/'updates' if preferences else None)
        self.updates_can_install=bool(getattr(sys,'frozen',False))
        self.update_dialog=None
        self._layout_metrics=initial_metrics=self._read_display_metrics()
        if display is not None:self.settings.migrate_display(initial_metrics)
        self.vm=AppViewModel(); self.closed=False;self._rendered_rows={}
        self.credential_dialog=None
        root.title(APP_NAME+' · '+DISPLAY_VERSION); root.geometry('1080x720'); root.minsize(540,420)
        self.icon_loaded=apply_assistant_icon(root,default=True)
        root.columnconfigure(0,weight=1);root.rowconfigure(0,weight=1)
        style=apply_theme(root,self.settings)
        self._applied_theme=self.settings.color_theme
        # 持有命名字体，使DPI运行时能让已有控件的字体缓存失效。
        self._content_font=tkfont.Font(root=root,family='Microsoft YaHei UI',size=self.settings.font_size)
        style.configure('Treeview',rowheight=32,font=self._content_font)
        style.configure('TButton',padding=(10,6))
        self.shell=ttk.Frame(root);self.shell.grid(row=0,column=0,sticky='nsew')
        self.shell.columnconfigure(1,weight=1);self.shell.rowconfigure(0,weight=1)
        self.icons=WorkspaceIcons(root,self.settings,initial_metrics.scale)
        self.sidebar=ttk.Frame(self.shell,padding=(12,24,12,18),style='Sidebar.TFrame',width=212)
        self.sidebar.pack_propagate(False)
        self.sidebar.grid(row=0,column=0,sticky='ns')
        self.brand=ttk.Label(self.sidebar,text=' 风暴英雄助手',image=self.icons.get('brand'),
                             compound='left',style='Sidebar.Brand.TLabel')
        self.brand.pack(anchor='w',padx=8,pady=(0,30))
        self.navigation={}
        for key,label in (('chat','实时消息'),('phrases','常用短句'),('loot','开箱助手'),('repeat','动作连按'),('talent','天赋辅助'),('broadcast','双击播报')):
            button=ttk.Button(self.sidebar,text='  '+label,image=self.icons.get(key),compound='left',style='Sidebar.TButton',
                              command=lambda page=key:self.select_section(page),width=0)
            button.pack(fill='x',pady=(0,10));self.navigation[key]=button
        self.sidebar_update=ttk.Button(self.sidebar,text='  检查更新',image=self.icons.get('update'),compound='left',
                                      command=self.open_updates,style='Sidebar.TButton',width=0)
        self.sidebar_update.pack(side='bottom',fill='x',pady=(8,0))
        self.sidebar_settings=ttk.Button(self.sidebar,text='  偏好设置',image=self.icons.get('settings'),compound='left',
                                        command=self.window_options,style='Sidebar.TButton',width=0)
        self.sidebar_settings.pack(side='bottom',fill='x')
        self.sidebar_theme=ttk.Label(self.sidebar,text=PALETTE_NAMES[self.settings.color_theme],
                                     style='Sidebar.Muted.TLabel')
        self.sidebar_hotkey=ttk.Label(self.sidebar,text='游戏中按 Ctrl+T\n随时打开快捷输入',
                  style='Sidebar.Muted.TLabel',justify='left',wraplength=160)
        self.workspace=workspace=ttk.Frame(self.shell)
        workspace.grid(row=0,column=1,sticky='nsew');workspace.columnconfigure(0,weight=1)
        workspace.rowconfigure(3,weight=1)
        self.section_title=tk.StringVar(value='实时消息')
        toolbar=ttk.Frame(workspace,padding=(24,24,24,18)); toolbar.grid(row=0,column=0,sticky='ew')
        self.toolbar=toolbar
        toolbar.columnconfigure(1,weight=1)
        self.page_title=ttk.Label(toolbar,textvariable=self.section_title,style='PageTitle.TLabel')
        self.page_title.grid(row=0,column=0,sticky='w')
        self.header_actions=ttk.Frame(toolbar)
        self.header_actions.grid(row=0,column=1,sticky='e')
        self.settings_button=ttk.Button(self.header_actions,text='设置',command=self.window_options,style='Nav.TButton')
        self.settings_button.pack(side='right')
        self.update_button=ttk.Button(self.header_actions,text='检查更新',command=self.open_updates,style='Nav.TButton')
        self.update_button.pack(side='right',padx=(6,0))
        self.automatic=tk.BooleanVar(value=self.settings.auto_translate)
        self.header_controls=ttk.Frame(toolbar)
        self.header_controls.grid(row=0,column=1,sticky='e',padx=(16,0))
        service_row=ttk.Frame(self.header_controls);service_row.grid(row=0,column=0,sticky='ew')
        self.service_row=service_row
        service_row.columnconfigure(0,weight=1)
        self.service_label=ttk.Label(service_row,text='翻译服务：未配置 · 接收原文',style='Muted.TLabel',width=1)
        self.account_button=None
        if hasattr(controller,'account_setup'):
            self.account_button=ttk.Button(service_row,text='个人密钥' if controller.cloud_configured else '配置云翻译',command=self.configure_account,style='Nav.TButton')
        self.quota_button=ttk.Button(service_row,text='查询剩余额度',command=self.open_cloud_quota,style='Nav.TButton')
        self.cloud_badge=ttk.Button(service_row,text='翻译服务未配置',command=self.configure_account,style='Nav.TButton')
        self.cloud_badge.grid(row=0,column=1)
        self.capture_state=tk.StringVar(value='采集：自动连接中')
        self.capture_badge=ttk.Label(service_row,textvariable=self.capture_state,style='Muted.TLabel',width=16)
        self.capture_badge.grid(row=0,column=0,padx=(0,12))
        self.cloud=tk.BooleanVar(value=False)
        controller.pause_translation(True)
        controller.pause_automatic(not self.automatic.get())
        channel_row=ttk.Frame(self.header_controls);channel_row.grid(row=0,column=1,sticky='e',padx=(14,0))
        self.channel_row=channel_row
        self.channel=tk.StringVar(value='全部聊天')
        self.channel_combo=ttk.Combobox(channel_row,textvariable=self.channel,values=list(CHANNEL_PRESETS),state='readonly',width=10)
        self.channel_combo.grid(row=0,column=0,padx=(0,14))
        self.channel_combo.bind('<<ComboboxSelected>>',lambda e:controller.set_channels(CHANNEL_PRESETS[self.channel.get()]))
        ttk.Checkbutton(channel_row,text='自动翻译',variable=self.automatic,command=self.toggle_automatic,
                        style='Switch.TCheckbutton').grid(row=0,column=1)
        self.process_choice=None
        self.notebook=ttk.Notebook(workspace,style='Main.TNotebook'); self.notebook.grid(row=3,column=0,sticky='nsew',padx=(10,20),pady=(0,10))
        self.notebook.enable_traversal()
        self.notebook.bind('<<NotebookTabChanged>>',lambda event:self._sync_navigation())
        self.chat_page=ttk.Frame(self.notebook); self.notebook.add(self.chat_page,text='实时消息')
        self.message_panel=RoundedPanel(self.chat_page,settings=self.settings,padding=8,radius=16)
        self.message_panel.pack(fill='both',expand=True)
        self.message_toolbar=ttk.Frame(self.message_panel.body,style='Surface.TFrame')
        self.message_toolbar.pack(fill='x',padx=8,pady=(0,6))
        self.quick_button=ttk.Button(self.message_toolbar,text=' 快捷输入',image=self.icons.get('quick'),compound='left',
                                    command=self.open_quick_input,style='Surface.Soft.TButton')
        self.quick_button.pack(side='right')
        self.tree=ChatFeed(self.message_panel.body,font=self._content_font,settings=self.settings)
        self.tree.pack(fill='both',expand=True)
        self.tree.bind('<<TreeviewSelect>>',lambda e:self.show_selected())
        self.tree.bind('<<MessageDetails>>',lambda e:self.toggle_details())
        message_actions=ttk.Frame(self.message_toolbar,style='Surface.TFrame');message_actions.pack(side='left')
        self.retry_button=ttk.Button(message_actions,text='重试所选',command=self.retry,style='Surface.Nav.TButton',state='disabled')
        self.retry_button.pack(side='right')
        self.details_button=ttk.Button(message_actions,text='消息详情',command=self.toggle_details,style='Surface.Nav.TButton')
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
        self._repeat_action=self.settings.repeat_action
        self._repeat_interval_ms=self.settings.repeat_interval_ms
        self._repeat_s_interval_ms=self.settings.repeat_s_interval_ms
        self.repeat_panel=RepeatPanel(self.notebook,on_prepare=self.prepare_repeat,on_cancel=self.cancel_repeat,
                                      settings=self.settings,metrics=initial_metrics,on_apply=self.apply_repeat_config)
        self.notebook.add(self.repeat_panel,text='动作连按')
        self.talent_helper=TalentHelper(diagnostics=getattr(controller,'diagnostics',None))
        self.talent_panel=TalentPanel(self.notebook,settings=self.settings,metrics=initial_metrics,
                                      on_toggle=self.apply_talent_config,on_retry=self.retry_talent)
        self.notebook.add(self.talent_panel,text='天赋辅助')
        self.board_helper=BoardHelper(busy_provider=self._other_input_busy,
            input_locks=(self.talent_helper._dispatch,self.repeat_helper._dispatch),
            diagnostics=getattr(controller,'diagnostics',None),language=self.settings.board_output_language)
        self.board_panel=BoardPanel(self.notebook,settings=self.settings,metrics=initial_metrics,
                                    on_toggle=self.apply_board_config,on_retry=self.retry_board,
                                    on_language=self.apply_board_language)
        self.notebook.add(self.board_panel,text='双击播报')
        self.reply_panel=RoundedPanel(workspace,settings=self.settings,padding=12,fit_height=True,radius=16)
        self.reply_panel.grid(row=4,column=0,sticky='ew',padx=(10,20),pady=(0,5))
        send=self.reply_panel.body
        self.send_frame=send
        send.columnconfigure((0,1),weight=1,uniform='draft')
        self.composer_heading=ttk.Label(send,text='用中文回复',style='ComposerTitle.TLabel')
        self.composer_heading.grid(row=0,column=0,sticky='w',pady=(0,8))
        self.preview_heading=ttk.Frame(send,style='Surface.TFrame')
        self.preview_heading.grid(row=0,column=1,sticky='ew',padx=(16,0),pady=(0,8))
        self.preview_title=ttk.Label(self.preview_heading,
                                     text='译文预览（'+('英语' if self.settings.target_language=='en' else '韩语')+'）',
                                     style='Surface.PreviewTitle.TLabel')
        self.preview_title.pack(side='left')
        self.target=tk.StringVar(value=self.settings.target_language)
        self.language_choices=ttk.Frame(self.preview_heading,style='Surface.TFrame')
        self.language_choices.pack(side='right')
        for label,language in (('英语','en'),('韩语','ko')):
            ttk.Radiobutton(self.language_choices,text=label,variable=self.target,value=language,
                            command=self.target_changed,style='Surface.Segment.TRadiobutton',width=5).pack(side='left',padx=(2,0))
        self.input_frame=RoundedPanel(send,settings=self.settings,padding=4,fit_height=True,
                                      radius=10,surface='field',background='surface')
        self.input_frame.grid(row=1,column=0,rowspan=2,sticky='nsew',padx=(0,16))
        self.input=tk.Text(self.input_frame.body,height=4,width=1,wrap='word',font=self._content_font,
                           padx=8,pady=3,borderwidth=0,highlightthickness=0)
        self.input.pack(fill='both',expand=True)
        self.input.bind('<<Modified>>',self.invalidate_draft)
        row=ttk.Frame(send,style='Surface.TFrame'); row.grid(row=2,column=1,sticky='ew',padx=(16,0),pady=(8,0))
        self.action_row=row
        self.copy_button=ttk.Button(row,text=' 复制译文',image=self.icons.get('copy'),compound='left',
                                   command=self.copy,style='Surface.Soft.TButton')
        self.copy_button.pack(side='left')
        self.translate_button=ttk.Button(row,text=' 翻译中文',image=self.icons.get('translate','primary'),compound='left',
                                        command=self.translate,style='Surface.Accent.TButton')
        self.translate_button.pack(side='left',padx=(8,0),fill='x',expand=True)
        self.preview_frame=RoundedPanel(send,settings=self.settings,padding=4,fit_height=True,
                                        radius=10,surface='field',background='surface')
        self.preview_frame.grid(row=1,column=1,sticky='ew',padx=(16,0))
        self.preview=tk.Text(self.preview_frame.body,height=2,width=1,wrap='word',font=self._content_font,
                             state='disabled',padx=8,pady=3,borderwidth=0,highlightthickness=0)
        self.preview.pack(fill='both',expand=True)
        for text,frame in ((self.input,self.input_frame),(self.preview,self.preview_frame)):
            text.bind('<FocusIn>',lambda event,frame=frame:frame.set_focused(True),add='+')
            text.bind('<FocusOut>',lambda event,frame=frame:frame.set_focused(False),add='+')
        self.manual_source=tk.StringVar(value='')
        self.manual_source_label=ttk.Label(send,textvariable=self.manual_source,style='Surface.Muted.TLabel')
        self.manual_source_label.grid(row=3,column=1,sticky='w',padx=(16,0),pady=(8,0))
        self.reply_hint=ttk.Label(send,text='Ctrl+T 快捷输入',style='Surface.Muted.TLabel')
        self.reply_hint.grid(row=3,column=0,sticky='w',pady=(8,0))
        self._density_state=None
        self._sidebar_visible=None
        self._tabs_hidden=None
        self.status_var=tk.StringVar(value=self.vm.status)
        ttk.Label(workspace,textvariable=self.status_var,padding=(18,2),style='Muted.TLabel',
                  font=('Microsoft YaHei UI',9),width=1).grid(row=5,column=0,sticky='ew')
        self.refresh_service_status()
        self.hotkeys=HotkeyManager(diagnostics=getattr(controller,'diagnostics',None))
        self.screen_ticks=0;self.monitor_bounds=[]
        from .overlay_window import OverlayWindow
        from .quick_input import QuickInput
        saved_overlay_monitor=self.settings.overlay_monitor
        self.settings.recover_overlay(self._overlay_work_areas(),scale=initial_metrics.scale)
        from ..display_runtime import TkDisplayRuntime,window_dpi
        self.overlay=OverlayWindow(root,self.settings,metrics=initial_metrics,
            work_areas=self._overlay_work_areas,
            dpi_reader=window_dpi if isinstance(self.display,TkDisplayRuntime) else None,
            diagnostics=getattr(controller,'diagnostics',None),
            on_position_changed=self._save_overlay_position)
        if saved_overlay_monitor is not None and saved_overlay_monitor!=self.settings.overlay_monitor:
            self.overlay._trace_monitor('recovered',reason='monitor_unavailable',
                count=len(self._overlay_work_areas()))
        from .helper_notice import HelperNotice
        self.helper_notice=HelperNotice(root,self.settings,metrics=initial_metrics)
        self.quick_input=QuickInput(root,controller,self.settings,self.quick_target_changed,
                                   metrics=initial_metrics,position_provider=lambda:self.overlay.input_position(),
                                   on_visibility_changed=self.quick_visibility_changed)
        self.apply_settings()
        self.enable_configured_cloud()
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
              'loot':self.loot_panel,'repeat':self.repeat_panel,'talent':getattr(self,'talent_panel',self.chat_page),
              'broadcast':getattr(self,'board_panel',self.chat_page)}.get(section,self.chat_page)
        self.notebook.select(page)
        self._sync_navigation()

    def _sync_navigation(self):
        selected=self.notebook.select()
        current=('broadcast' if hasattr(self,'board_panel') and selected==str(self.board_panel)
                 else 'talent' if hasattr(self,'talent_panel') and selected==str(self.talent_panel)
                 else 'repeat' if hasattr(self,'repeat_panel') and selected==str(self.repeat_panel)
                 else 'loot' if hasattr(self,'loot_panel') and selected==str(self.loot_panel)
                 else 'phrases' if hasattr(self,'phrase_panel') and selected==str(self.phrase_panel) else 'chat')
        self._current_section=current
        self.section_title.set({'chat':'实时消息','phrases':'常用短句','loot':'开箱助手','repeat':'动作连按','talent':'天赋辅助','broadcast':'双击播报'}[current])
        if hasattr(self,'reply_panel'):
            if current in ('loot','repeat','talent','broadcast'):self.reply_panel.grid_remove()
            else:self.reply_panel.grid()
        for name in ('service_row','channel_row'):
            row=getattr(self,name,None)
            if row is not None:
                if current in ('loot','repeat','talent','broadcast') or (name=='service_row' and getattr(self,'_density_minimal',False)):
                    row.grid_remove()
                else:row.grid()
        for key,button in self.navigation.items():
            active=key==current
            button.configure(style='Sidebar.Selected.TButton' if active else 'Sidebar.TButton',
                             image=self.icons.get(key,'selected' if active else 'normal'))

    def prepare_loot(self):
        self._prepare_action('loot')

    def open_updates(self):
        if self.closed:return
        dialog=getattr(self,'update_dialog',None)
        if dialog is not None and not dialog._closed:
            dialog.dialog.deiconify()
            return dialog.dialog
        self.cancel_loot();self.cancel_repeat()
        from .update_dialog import UpdateDialog
        self.update_dialog=UpdateDialog(self)
        return self.update_dialog.dialog

    def _prepare_update(self,staged):
        from ..update_install import prepare_install,launch_install
        progress=self.update_service.report_install_progress
        plan=prepare_install(staged,pid=getattr(self,'_update_owner_pid',None),progress=progress)
        progress('正在校验并启动独立升级程序…')
        return launch_install(plan)

    def install_update(self):
        if self.closed or getattr(self,'_update_installing',False):return False
        if not getattr(sys,'frozen',False):
            self.update_dialog.detail.set('当前从源码运行，请手动使用新发行包；源码目录不会被替换。')
            return False
        self._update_installing=True
        self.cancel_loot();self.cancel_repeat()
        if self.quick_input._visible:self.quick_input.cancel()
        self._sync_talent_gate()
        if not self.update_service.install(self._prepare_update):
            self._update_installing=False
            self._sync_talent_gate()
            return False
        return True

    def apply_talent_config(self,enabled):
        if not enabled and hasattr(self,'helper_notice'):self.helper_notice.clear()
        if enabled and getattr(self,'_update_installing',False):raise ValueError('正在准备升级，请等待助手重启。')
        previous=self.settings.talent_enabled
        self.settings.talent_enabled=bool(enabled)
        if not enabled:self.talent_helper.set_enabled(False)
        try:self.save_settings()
        except OSError as failure:
            if enabled:self.settings.talent_enabled=previous
            else:self.talent_panel.update_status('disabled','本次已关闭，但偏好未保存；请检查目录写入权限。')
            raise OSError(('' if enabled else '本次已关闭，但未能保存关闭状态：')+str(failure)) from failure
        self._sync_talent_gate()
        if not self.talent_helper.set_enabled(bool(enabled) and self._talent_runtime_enabled):
            self.settings.talent_enabled=previous
            self.save_settings()
            raise OSError('上次天赋线程正在退出，请稍后再试。')
        self.talent_panel.apply_settings(self.settings,self._layout_metrics)
        if not self._talent_runtime_enabled and enabled:
            self.talent_panel.update_status('disabled','演示模式不读取游戏或发送按键。')

    def _sync_talent_gate(self,quick_active=None):
        busy=self._other_input_busy(quick_active=quick_active)
        board=getattr(self,'board_helper',None)
        if board is not None:board.set_suspended(busy)
        helper=getattr(self,'talent_helper',None)
        if helper is not None:
            helper.set_suspended(busy or bool(board is not None and board.busy))

    def _other_input_busy(self,quick_active=None):
        return bool(getattr(self,'closed',False) or getattr(self,'_pending_action',None)
                    or getattr(self,'_update_installing',False)
                    or getattr(self,'_loot_active',False)
                    or getattr(getattr(self,'loot_helper',None),'active',False)
                    or getattr(self,'_repeat_active',False)
                    or getattr(getattr(self,'repeat_helper',None),'active',False)
                    or (getattr(getattr(self,'quick_input',None),'_visible',False)
                        if quick_active is None else quick_active))

    def apply_board_config(self,enabled):
        if not enabled and hasattr(self,'helper_notice'):self.helper_notice.clear()
        if enabled and getattr(self,'_update_installing',False):raise ValueError('正在准备升级，请等待助手重启。')
        previous=self.settings.board_broadcast_enabled
        self.settings.board_broadcast_enabled=bool(enabled)
        if not enabled:self.board_helper.set_enabled(False)
        try:self.save_settings()
        except OSError:
            if enabled:self.settings.board_broadcast_enabled=previous
            raise
        self._sync_talent_gate()
        if not self.board_helper.set_enabled(bool(enabled) and self._talent_runtime_enabled):
            self.settings.board_broadcast_enabled=previous
            self.save_settings()
            raise OSError('上次播报线程正在退出，请稍后再试。')
        self.board_panel.apply_settings(self.settings,self._layout_metrics)
        if enabled and not self._talent_runtime_enabled:
            self.board_panel.update_status('disabled','演示模式不会监听双击、读取游戏或发送聊天。')

    def retry_talent(self):
        self._retry_helper('talent')

    def apply_board_language(self,language):
        request_ref=uuid.uuid4().hex
        recorder=getattr(getattr(self,'controller',None),'diagnostics',None)
        def trace(outcome,reason):
            if recorder is None:return
            try:
                recorder.record('ui.apply',source='broadcast',operation='language',stage='configuration',
                    outcome=outcome,reason=reason,request_ref=request_ref,
                    target_language=language if language in ('zh','en') else '')
            except Exception:pass
        trace('started','language_change')
        if language not in ('zh','en'):
            trace('rejected','unsupported_language')
            raise ValueError('播报语言只能选择中文或 English。')
        if getattr(self,'_update_installing',False):
            trace('rejected','update_in_progress')
            raise ValueError('正在准备升级，请等待助手重启。')
        previous=self.settings.board_output_language
        if previous==language:
            trace('unchanged','already_selected')
            return True
        self.settings.board_output_language=language
        try:self.save_settings()
        except OSError:
            self.settings.board_output_language=previous
            trace('error','save_failed')
            trace('recovered','previous_language_retained')
            raise OSError('播报语言未保存，已保留原语言；请稍后重试。') from None
        trace('saved','language_saved')
        try:
            if self.board_helper.set_language(language,request_ref=request_ref) is False:raise ValueError('language rejected')
        except Exception:
            self.settings.board_output_language=previous
            trace('error','apply_failed')
            restored=True
            try:
                if self.board_helper.set_language(previous,request_ref=request_ref) is False:restored=False
            except Exception:restored=False
            try:self.save_settings()
            except OSError:
                restored=False
                trace('error','rollback_save_failed')
            trace('recovered' if restored else 'error',
                  'previous_language_restored' if restored else 'rollback_incomplete')
            raise OSError('播报语言切换未完成，请核对当前语言后重试。') from None
        self.board_panel.apply_settings(self.settings,self._layout_metrics)
        trace('applied','language_ready')
        return True

    def retry_board(self):
        self._retry_helper('board')

    def _retry_helper(self,source):
        helper=getattr(self,source+'_helper')
        panel=getattr(self,source+'_panel')
        preference=getattr(self.settings,'talent_enabled' if source=='talent' else 'board_broadcast_enabled')
        if not preference:return
        if not self._talent_runtime_enabled:
            panel.update_status('disabled','演示模式不读取游戏或发送按键。')
            return
        if getattr(self,'_update_installing',False):
            panel.update_status('deferred','正在准备升级，请等待助手重启。')
            return
        self._sync_talent_gate()
        if not helper.set_enabled(True):
            panel.update_status('error','上次后台任务正在退出，请稍后重新连接。')
        if hasattr(panel,'set_running'):panel.set_running(bool(getattr(helper,'running',False)))

    def _feedback_allowed(self):
        """仅关键异常到来时核验游戏前台，不新增后台扫描或钩子。"""
        if not getattr(self,'_talent_runtime_enabled',False) or self._other_input_busy():return False
        try:
            if self._feedback_backend is None:
                from ..loot_native import Win32LootBackend
                self._feedback_backend=Win32LootBackend()
            target=self._feedback_backend.foreground_target()
            self._notice_target=target
            return target is not None
        except OSError:return False

    def update_helper_status(self,source,status):
        panel=getattr(self,source+'_panel')
        helper=getattr(self,source+'_helper')
        if source=='talent':panel.update_status(status.state,status.detail,getattr(status,'tier_id',None))
        else:panel.update_status(status.state,status.detail)
        if hasattr(panel,'set_running'):panel.set_running(bool(getattr(helper,'running',False)))
        critical=(status.state in ('blocked','unconfirmed','error') if source=='talent'
                  else status.state=='error')
        notice=getattr(self,'helper_notice',None)
        if critical and notice is not None and self._feedback_allowed():
            notice.show(source,status.state,status.detail,generation=self.controller.generation,
                        dedupe_key=getattr(status,'tier_id',None))

    def _guard_helper_notice(self):
        notice=getattr(self,'helper_notice',None)
        if notice is None or not notice.visible:return
        target=getattr(self,'_notice_target',None)
        backend=getattr(self,'_feedback_backend',None)
        try:
            foreground=bool(target and backend and backend.user.GetForegroundWindow()==target.hwnd)
        except OSError:foreground=False
        if not foreground or self._other_input_busy():notice.clear()

    def prepare_repeat(self):
        self._prepare_action('repeat')

    def _repeat_options(self):
        if getattr(self.settings,'repeat_action','S')=='S':
            interval=getattr(self.settings,'repeat_s_interval_ms',20)
            if interval==20:return {}
            return dict(action='S',interval_ms=interval)
        return dict(action='YS',interval_ms=getattr(self.settings,'repeat_interval_ms',20))

    def apply_repeat_config(self,action,interval_ms):
        validate_repeat_config(action,interval_ms)
        interval_field='repeat_s_interval_ms' if action=='S' else 'repeat_interval_ms'
        previous=(self.settings.repeat_action,getattr(self.settings,interval_field))
        self.cancel_repeat()
        self.settings.repeat_action=action
        setattr(self.settings,interval_field,interval_ms)
        try:self.save_settings()
        except OSError:
            self.settings.repeat_action=previous[0]
            setattr(self.settings,interval_field,previous[1])
            raise
        self._repeat_binding=self.settings.repeat_hotkey
        self._repeat_action=action
        self._repeat_interval_ms=self.settings.repeat_interval_ms
        self._repeat_s_interval_ms=self.settings.repeat_s_interval_ms
        if self._repeat_listener_started:self._start_repeat_listener()
        self.repeat_panel.apply_settings(self.settings,self._layout_metrics)

    def _cancel_action_request(self, kind=None):
        if kind is not None and getattr(self,'_pending_action',None)!=kind:return
        self._action_revision=getattr(self,'_action_revision',0)+1
        self._pending_action=None
        if getattr(self,'_action_timer',None) is not None:
            self.root.after_cancel(self._action_timer)
            self._action_timer=None

    def _prepare_action(self, kind):
        if self.closed or getattr(self,'_update_installing',False):return
        self._cancel_action_request()
        if getattr(self.loot_helper,'active',False) or self._loot_active:self.cancel_loot()
        if (getattr(getattr(self,'repeat_helper',None),'active',False)
                or getattr(self,'_repeat_active',False)):self.cancel_repeat()
        if self.quick_input._visible:self.quick_input.cancel()
        revision=self._action_revision
        self._pending_action=kind
        self._sync_talent_gate()
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
            started=(helper.prepare(self.settings.repeat_hotkey,**self._repeat_options())
                     if kind=='repeat' else helper.prepare())
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
        self._sync_talent_gate()
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
        recorder=getattr(getattr(self,'controller',None),'diagnostics',None)
        if recorder is not None:
            recorder.record('hotkey.state',stage='repeat',outcome=status.state,
                            source=getattr(self.settings,'repeat_action','S'),generation=status.generation)
        if status.state=='requested':
            if self.closed or not self.repeat_helper.accepts_status(status):return
            if self.quick_input._visible or getattr(self,'_update_installing',False):
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
        self._sync_talent_gate(quick_active=active)
        self.overlay.set_input_active(active or self._loot_active or getattr(self,'_repeat_active',False))

    def _apply_shell(self,width,height=None):
        height=self.root.winfo_height() if height is None else height
        visible=(width>=self._layout_metrics.px(900) and height>=self._layout_metrics.px(560))
        if visible!=self._sidebar_visible:
            self._sidebar_visible=visible
            if visible:
                self.sidebar.grid();self.settings_button.pack_forget();self.update_button.pack_forget()
            else:
                self.sidebar.grid_remove();self.settings_button.pack(side='right');self.update_button.pack(side='right')
        self._layout_header()
        if self._tabs_hidden!=visible:
            style=ttk.Style(self.root)
            style.layout('Main.TNotebook.Tab',[] if visible else style.layout('TNotebook.Tab'))
            style.configure('Main.TNotebook.Tab',padding=(self._layout_metrics.px(6),self._layout_metrics.px(3)))
            self._tabs_hidden=visible
            self._sync_navigation()

    def _layout_header(self):
        """小窗口把状态控件压为一行；只重排，不改真实连接状态。"""
        visible=bool(self._sidebar_visible)
        minimal=getattr(self,'_density_minimal',False)
        if minimal:
            self.page_title.grid_remove()
            self.header_actions.grid_configure(row=0,column=0,sticky='w')
            self.header_controls.grid_configure(row=0,column=1,columnspan=1,padx=0,pady=0)
        else:
            self.page_title.grid()
            self.header_actions.grid_configure(row=0,column=1,sticky='e')
            self.header_controls.grid_configure(row=0 if visible else 1,column=1 if visible else 0,
                                                columnspan=1 if visible else 2,
                                                padx=(self._layout_metrics.px(12),0) if visible else 0,
                                                pady=0 if visible else (self._layout_metrics.px(8),0))

    def clear_draft(self):
        self.vm.copy_text=''; self.vm.expected_job=None;self.vm.manual_provenance=''
        self.render_manual_preview()

    def render_manual_preview(self):
        self.set_text(self.preview,self.vm.copy_text)
        self.manual_source.set('来源：'+self.vm.manual_provenance if self.vm.copy_text and self.vm.manual_provenance else '')
        self.preview_title.configure(text='译文预览（'+('英语' if self.target.get()=='en' else '韩语')+'）')

    def invalidate_draft(self,event=None):
        if self.input.edit_modified(): self.clear_draft(); self.input.edit_modified(False)

    def _start_repeat_listener(self):
        if self.closed or not self._repeat_hotkey_enabled:return
        self._repeat_listener_started=True
        try:
            if not self.repeat_helper.start_listening(self.settings.repeat_hotkey,**self._repeat_options()):
                self.repeat_ui_failed(OSError('连按快捷键监听未能启动，请稍后重新保存设置。'))
        except (OSError,ValueError) as failure:self.repeat_ui_failed(failure)

    def start(self):
        if self.closed:return
        if getattr(self,'_repeat_hotkey_enabled',False):self._start_repeat_listener()
        if getattr(self,'_talent_runtime_enabled',False) and self.settings.talent_enabled:
            self._sync_talent_gate()
            self.talent_helper.set_enabled(True)
        if getattr(self,'_talent_runtime_enabled',False) and self.settings.board_broadcast_enabled:
            self._sync_talent_gate()
            self.board_helper.set_enabled(True)
        self.controller.start_receiving()
        if getattr(self,'_update_check_enabled',False):self.update_service.check_on_startup()
    def stop(self): self.controller.stop_receiving()

    def enable_configured_cloud(self):
        enabled=(getattr(self.controller,'cloud_configured',False)
                 and not getattr(self.controller,'configuration_held',False))
        self.cloud.set(enabled)
        self.controller.pause_translation(not enabled)
        self.refresh_service_status()

    def toggle_automatic(self):
        self.settings.auto_translate=self.automatic.get()
        self.controller.pause_automatic(not self.automatic.get())
        self.save_settings()
        self.refresh_service_status()

    def open_quick_input(self):
        if getattr(self,'_update_installing',False):return
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
        self._apply_shell(event.width,event.height)
        self._apply_density(event.height,event.width)
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

    def _overlay_work_areas(self):
        if self.display is not None:
            reader=getattr(self.display,'work_areas',None)
            if callable(reader):
                areas=reader()
                if areas:return areas
            return [self._layout_metrics.work_area]
        return screen_bounds()

    def _save_overlay_position(self):
        try:self.save_settings()
        except OSError:
            self.status_var.set('字幕位置暂未保存，请检查偏好文件后重试。')
            raise

    def _finish_layout(self):
        self._layout_guard_timer=None
        self._applying_layout=False

    def _apply_density(self,height,width=None):
        """真正缩到小窗口时减少占位，输入与译文并排，保留阅读和操作空间。"""
        width=self.root.winfo_width() if width is None else width
        tight=(self._layout_tight or height<self._layout_metrics.px(600)
               or width<self._layout_metrics.px(900))
        minimal=height<self._layout_metrics.px(480)
        state=(tight,minimal,self._layout_metrics.scale)
        if self._density_state==state:return
        self._density_state=state
        self._density_tight=tight
        self._density_minimal=minimal
        px=self._layout_metrics.px
        self.toolbar.configure(padding=(px(24),px(6 if minimal else 20),px(20),px(8 if minimal else 18)))
        self.notebook.grid_configure(pady=(0,px(4 if minimal else 10)))
        self.reply_panel.set_padding(6 if minimal else 12)
        self.message_panel.set_padding(4 if minimal else 8)
        self.input.configure(height=1 if tight else 3)
        self.preview.configure(height=1 if tight else 2)
        if tight:self.detail.pack_forget()
        elif self._details_open:self.detail.pack(fill='x',pady=(4,0))
        if minimal:
            self.service_row.grid_remove()
            self.composer_heading.grid_remove()
            self.preview_heading.grid_remove()
            self.manual_source_label.grid_remove()
            self.reply_hint.grid_remove()
        else:
            self.service_row.grid()
            self.composer_heading.grid()
            self.preview_heading.grid()
            self.manual_source_label.grid()
            self.reply_hint.grid()
        if getattr(self,'_current_section','chat') in ('loot','repeat','talent','broadcast'):
            self.service_row.grid_remove()
            self.channel_row.grid_remove()
        gap=px(4 if tight else 16)
        self.input_frame.grid_configure(row=1,column=0,columnspan=1,padx=(0,gap),rowspan=1 if tight else 2)
        self.preview_frame.grid_configure(row=1,column=1,columnspan=1,padx=(gap,0))
        self.preview_heading.grid_configure(padx=(gap,0))
        self.action_row.grid_configure(row=2,column=0 if tight else 1,columnspan=2 if tight else 1,
                                       padx=0 if tight else (gap,0))
        self.preview_title.pack_forget()
        if not tight:self.preview_title.pack(side='left',before=self.language_choices)
        if tight:
            # 小窗口依旧可切语言；把按钮和语言选择排成一行，给大字号编辑器留空间。
            self.language_choices.pack_forget()
            self.language_choices.grid_forget()
            self.preview_heading.grid(row=0,column=1,sticky='e',padx=(gap,0))
            self.language_choices.pack(side='right')
        self._layout_header()

    def apply_settings(self,layout_only=False,metrics=None):
        self._applying_layout=True
        if self.settings.color_theme!=self._applied_theme:
            apply_theme(self.root,self.settings)
            self._applied_theme=self.settings.color_theme
            self._tabs_hidden=None
        self._layout_metrics=metrics=metrics if metrics is not None else self._read_display_metrics()
        refresh_theme_scale(self.root,self.settings)
        self.sidebar.configure(width=metrics.px(212))
        self.icons.refresh(self.settings,metrics.scale)
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
        self._apply_density(layout.height,layout.width)
        self._apply_shell(layout.width,layout.height)
        if self._content_font.cget('size')!=self.settings.font_size:
            self._content_font.configure(size=self.settings.font_size)
        ttk.Style(self.root).configure('Treeview',font=self._content_font,rowheight=layout.row_height)
        ttk.Style(self.root).configure('TButton',padding=(metrics.px(10),metrics.px(6)))
        self.tree.apply_settings(self.settings)
        self._sync_navigation()
        self.message_panel.apply_settings(self.settings,metrics)
        self.reply_panel.apply_settings(self.settings,metrics)
        self.input_frame.apply_settings(self.settings,metrics)
        self.preview_frame.apply_settings(self.settings,metrics)
        self.input_frame.set_surface('surface' if normalize_theme(self.settings.color_theme)=='lavender' else 'field')
        colors=theme_colors(self.settings)
        self.input.configure(background=colors['surface'] if normalize_theme(self.settings.color_theme)=='lavender' else colors['field'])
        self.sidebar_theme.configure(text=PALETTE_NAMES[normalize_theme(self.settings.color_theme)])
        if hasattr(self,'phrase_panel'):self.phrase_panel.apply_metrics(metrics)
        try:self.loot_panel.apply_settings(self.settings,metrics)
        except (OSError,tk.TclError) as failure:self.loot_ui_failed(failure)
        if ((self._repeat_binding,getattr(self,'_repeat_action','S'),getattr(self,'_repeat_interval_ms',20),
             getattr(self,'_repeat_s_interval_ms',20))
                !=(self.settings.repeat_hotkey,self.settings.repeat_action,self.settings.repeat_interval_ms,
                   self.settings.repeat_s_interval_ms)):
            self.cancel_repeat()
            self._repeat_binding=self.settings.repeat_hotkey
            self._repeat_action,self._repeat_interval_ms=self.settings.repeat_action,self.settings.repeat_interval_ms
            self._repeat_s_interval_ms=self.settings.repeat_s_interval_ms
            if self._repeat_listener_started:self._start_repeat_listener()
        try:self.repeat_panel.apply_settings(self.settings,metrics)
        except (OSError,tk.TclError) as failure:self.repeat_ui_failed(failure)
        if hasattr(self,'talent_panel'):self.talent_panel.apply_settings(self.settings,metrics)
        if hasattr(self,'board_panel'):self.board_panel.apply_settings(self.settings,metrics)
        if hasattr(self,'helper_notice'):self.helper_notice.apply_settings(self.settings,metrics)
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
        dialog=getattr(self,'settings_dialog',None)
        if dialog is not None and dialog.dialog.winfo_exists():
            dialog.dialog.deiconify()
            dialog.dialog.lift()
            return dialog.dialog
        from .settings_dialog import SettingsDialog
        self.settings_dialog=SettingsDialog(self)
        return self.settings_dialog.dialog

    def configure_account(self):
        if self.closed or not hasattr(self.controller,'account_setup'):return
        if self.credential_dialog is not None:
            self.credential_dialog.dialog.lift();return self.credential_dialog.dialog
        from .credential_dialog import CredentialDialog
        self.credential_dialog=CredentialDialog(self)
        return self.credential_dialog.dialog

    def initial_account_setup(self):
        self.setup_timer=None
        if not self.closed and self.root.state()!='withdrawn':self.configure_account()

    def _account_events(self):
        service=getattr(self.controller,'account_setup',None)
        if service is None:return
        events=service.poll()
        for event in events:
            # AccountSetup emits fixed/redacted messages; keep only display text,
            # never credentials, candidate objects or the raw event in snapshots.
            kind=event['kind']
            if kind in ('progress','error') and isinstance(event.get('message'),str):
                self._desktop_account_message=event['message'][:1200]
            elif kind in ('ready','removed','cancelled','identified','selected'):
                self._desktop_account_message={
                    'ready':'配置成功，翻译已启用。',
                    'removed':'个人密钥已删除；继续接收原文。',
                    'cancelled':'本次配置已取消；已保存的个人密钥保持不变。',
                    'identified':'账号已验证，正在继续测试翻译并保存…',
                    'selected':'已切换翻译服务，请配置当前服务的个人密钥。',
                }[kind]
            if event['kind'] in ('ready','removed','selected'):
                self.clear_draft();self.quick_input.cancel()
                self.enable_configured_cloud()
                self.account_button.configure(text='个人密钥' if self.controller.cloud_configured else '配置云翻译')
            if self.credential_dialog is not None:self.credential_dialog.handle(event)
            if event['kind']=='error':self.status_var.set(event['message'])
        balance_method=getattr(service,'balance_snapshot',None)
        if callable(balance_method):
            balance=balance_method()
            state=(balance.get('busy'),balance.get('as_of'),balance.get('error'))
            if state!=getattr(self,'_last_balance_state',None):
                self._last_balance_state=state
                if balance.get('error'):
                    self.status_var.set(balance['error'])
                elif balance.get('as_of') and not balance.get('busy'):
                    amounts='；'.join(f"{row['currency']} 总余额 {row['total_balance']}" for row in balance.get('balance_infos',[]))
                    availability=' · 账户当前不可用' if balance.get('is_available') is False else ''
                    self.status_var.set('DeepSeek：'+(amounts or '未返回余额明细')+availability)
            dialog=getattr(self,'credential_dialog',None)
            if dialog is not None:dialog._show_balance()
        if events:self.refresh_service_status()

    def save_settings(self):
        path=getattr(self.controller,'preferences_path',None)
        if path:self.settings.save(path)

    def capture_settings_state(self):
        return self.overlay.capture_settings_state()

    def restore_settings_state(self, snapshot):
        self.overlay.restore_settings_state(snapshot)

    def toggle_cloud(self):
        enabled=(self.cloud.get() and getattr(self.controller,'cloud_configured',False)
                 and not getattr(self.controller,'configuration_held',False))
        self.cloud.set(enabled)
        self.controller.pause_translation(not enabled)
        self.refresh_service_status()

    def refresh_service_status(self):
        configured=getattr(self.controller,'cloud_configured',False)
        provider=getattr(getattr(self.controller,'account_setup',None),'selected_provider','aliyun')
        name='DeepSeek' if provider=='deepseek' else '阿里云机器翻译'
        if getattr(self.controller,'configuration_held',False):
            text=name+'：配置处理中 · 云请求暂停'
        elif not configured:text='翻译服务：未配置 · 接收原文'
        elif getattr(self.controller,'paused',False):text=name+'：已暂停 · 接收原文'
        else:text=name+'：已启用 · '+('自动翻译开启' if self.automatic.get() else '自动翻译暂停')
        if text!=getattr(self,'_service_text',None):
            self.service_label.configure(text=text)
            self._service_text=text
            if hasattr(self,'cloud_badge'):
                self.cloud_badge.configure(text=('● '+name+'已启用' if configured and not getattr(self.controller,'paused',False)
                                                 and not getattr(self.controller,'configuration_held',False)
                                                 else '翻译服务未配置' if not configured else name+'已暂停'))
            if hasattr(self,'quota_button'):
                self.quota_button.configure(text='查询账户余额' if provider=='deepseek' else '查询剩余额度')

    def open_cloud_quota(self):
        service=getattr(self.controller,'account_setup',None)
        if getattr(service,'selected_provider','aliyun')=='deepseek':
            started=service.refresh_balance()
            if started or service.balance_snapshot().get('busy'):
                self.status_var.set('正在后台查询 DeepSeek 账户余额…')
                return True
            self.status_var.set('余额查询未启动，请先连接当前 DeepSeek 账户并结束配置任务。')
            return False
        url='https://mt.console.aliyun.com/service'
        try:
            opened=webbrowser.open(url)
        except (OSError,webbrowser.Error):
            opened=False
        if opened:
            self.status_var.set('已请求打开阿里云控制台，请在浏览器登录后查看剩余额度与实际用量。')
        else:
            self.status_var.set('无法打开默认浏览器，请手动访问：'+url)
        return bool(opened)

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
        engine=getattr(self.controller,'engine',None)
        context_note=('本机最近消息（实际请求仅取同会话前3条，且可在账户中关闭）'
                      if getattr(engine,'provider_id',None)=='deepseek' and getattr(engine,'context_enabled',False)
                      else '本机最近三条（不上传）')
        self.set_text(self.detail,f'{e.timestamp} [{e.channel}] {e.sender}\n原文：{m.original}\n中文译文：{m.engine_translation}\n{context_note}：'+context)

    def pump(self):
        if self.closed:return
        if getattr(self,'timer',None):self.root.after_cancel(self.timer)
        try:self._pump_events()
        except (OSError,tk.TclError):
            self.status_var.set('窗口更新暂时失败，后台继续接收；请检查窗口设置。')
        finally:
            if not self.closed:self.timer=self.root.after(100,self.pump)

    def _pump_events(self):
        if getattr(self,'_update_installing',False):
            update=self.update_service.latest
            if update.state=='restarting':
                if not getattr(self,'_update_exit_managed_by_host',False):self.close()
                return
            if update.state in ('error','cancelled'):
                self._update_installing=False
                self._sync_talent_gate()
        for status in self.loot_helper.drain_status():
            if self.loot_helper.accepts_status(status):self.update_loot_status(status)
        helper=getattr(self,'repeat_helper',None)
        if helper is not None:
            for status in helper.drain_status():
                if helper.accepts_status(status):self.update_repeat_status(status)
        helper=getattr(self,'talent_helper',None)
        if helper is not None:
            for status in helper.drain_status():
                if helper.accepts_status(status):self.update_helper_status('talent',status)
        helper=getattr(self,'board_helper',None)
        if helper is not None:
            for status in helper.drain_status():self.update_helper_status('board',status)
        self._guard_helper_notice()
        self._sync_talent_gate()
        self._account_events()
        self.screen_ticks+=1
        if self.screen_ticks%20==0:
            metrics=self._read_display_metrics()
            if metrics!=self._layout_metrics:
                self.apply_settings(layout_only=True,metrics=metrics)
            refresh=getattr(self.overlay,'refresh_layout',None)
            if callable(refresh):refresh()
        events=self.controller.drain_events()
        changed=False
        for event in events:
            applied=self.vm.apply(event)
            recorder=getattr(self.controller,'diagnostics',None)
            if recorder is not None and event.kind in ('chat','result','manual_result','error','generation'):
                payload=event.payload if isinstance(event.payload,dict) else {}
                recorder.record('ui.apply',stage=event.kind,generation=event.generation,
                                message_id=payload.get('message_id',getattr(event.payload,'message_id',None)),
                                job_id=payload.get('job_id'),
                                outcome=applied)
            if hasattr(self,'quick_input'):self.quick_input.handle_event(event)
            if event.kind=='capture_state' and event.generation==self.vm.generation:
                self.capture_state.set('采集：'+str(event.payload))
            if event.kind in ('chat','result','error','generation','suppressed','translation_state') and applied=='applied': changed=True
            if event.kind=='generation' and applied=='applied' and hasattr(self,'helper_notice'):
                self.helper_notice.clear()
            if event.kind in ('manual_result','generation'):self.render_manual_preview()
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
        updates=getattr(self,'update_service',None)
        if updates is not None:updates.close(wait=False)
        dialog=getattr(self,'update_dialog',None)
        if dialog is not None:
            dialog.close()
            self.update_dialog=None
        helper=getattr(self,'talent_helper',None)
        if helper is not None:helper.set_enabled(False,wait=False)
        helper=getattr(self,'board_helper',None)
        if helper is not None:helper.set_enabled(False,wait=False)
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
                  lambda:getattr(self,'talent_helper',None) and self.talent_helper.close(),
                  lambda:getattr(self,'talent_panel',None) and self.talent_panel.close(),
                  lambda:getattr(self,'board_helper',None) and self.board_helper.close(),
                  lambda:getattr(self,'board_panel',None) and self.board_panel.close(),
                  lambda:getattr(self,'helper_notice',None) and self.helper_notice.close(),
                  self.hotkeys.stop,self.quick_input.close,self.overlay.close,self.save_settings,
                  self.controller.close,self.root.destroy)
        # 一个原生句柄/偏好写入失败，也必须继续释放其他线程与窗口。
        for cleanup in cleanups:
            try:cleanup()
            except (OSError,ValueError,tk.TclError):pass


def create_app(root,controller,settings,display=None,enable_repeat_hotkey=False,enable_talent_helper=False,
               enable_update_check=False):
    return MainWindow(root,controller,settings,display=display,enable_repeat_hotkey=enable_repeat_hotkey,
                      enable_talent_helper=enable_talent_helper,enable_update_check=enable_update_check)
