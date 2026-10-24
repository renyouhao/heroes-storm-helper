"""密钥只在掩码输入框与后台配置服务内流转，不放进偏好或事件。"""
from datetime import datetime, timedelta, timezone
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from ..config import Credentials, ConfigError, load_credentials
from .theme import style_native_window, theme_colors


def masked_key(value):
    return value[:4]+'…'+value[-4:] if len(value)>8 else '••••'


class CredentialDialog:
    def __init__(self, window):
        self.window=window
        self.service=window.controller.account_setup
        self.dialog=dialog=tk.Toplevel(window.root)
        dialog.title('配置自己的翻译服务')
        dialog.transient(window.root)
        dialog.protocol('WM_DELETE_WINDOW',self.cancel)
        dialog.columnconfigure(0,weight=1)
        body=ttk.Frame(dialog,padding=18);body.grid(sticky='nsew')
        body.columnconfigure(1,weight=1)
        ttk.Label(body,text='选择自己的翻译服务；两家密钥分别加密保存。可跳过并只接收原文。',wraplength=490).grid(row=0,column=0,columnspan=2,sticky='w',pady=(0,10))
        current=self.service.current
        active=getattr(self.service,'selected_provider','aliyun')
        credentials=getattr(current,'credentials',None)
        key_value=getattr(credentials,'api_key',getattr(credentials,'access_key_id',''))
        label=('已保存：'+masked_key(key_value)) if current else '尚未保存个人密钥'
        self.current=ttk.Label(body,text=label,wraplength=490)
        self.current.grid(row=1,column=0,columnspan=2,sticky='w')
        self.provider=tk.StringVar(value=active)
        provider_row=ttk.Frame(body)
        provider_row.grid(row=2,column=0,columnspan=2,sticky='ew',pady=(12,4))
        self.provider_buttons=[]
        for provider,label in (('aliyun','阿里云机器翻译'),('deepseek','DeepSeek V4.1 Flash')):
            button=ttk.Radiobutton(provider_row,text=label,variable=self.provider,value=provider,command=self.provider_changed)
            button.pack(side='left',padx=(0,12))
            self.provider_buttons.append(button)
        self.key=tk.StringVar();self.secret=tk.StringVar()
        self.key_label=ttk.Label(body,text='AccessKey ID')
        self.key_label.grid(row=3,column=0,sticky='w',pady=(10,3))
        self.key_entry=ttk.Entry(body,textvariable=self.key,width=42)
        self.key_entry.grid(row=3,column=1,sticky='ew',padx=(10,0),pady=(10,3))
        self.secret_label=ttk.Label(body,text='AccessKey Secret')
        self.secret_label.grid(row=4,column=0,sticky='w',pady=3)
        self.secret_entry=ttk.Entry(body,textvariable=self.secret,show='●',width=42)
        self.secret_entry.grid(row=4,column=1,sticky='ew',padx=(10,0),pady=3)
        self.context_enabled=tk.BooleanVar(value=True)
        self.context_button=ttk.Checkbutton(body,text='发送同会话最近3条原文，帮助理解上下文',variable=self.context_enabled)
        self.context_button.grid(row=5,column=0,columnspan=2,sticky='w',pady=6)
        self.service_note=ttk.Label(body,text='',wraplength=490)
        self.service_note.grid(row=6,column=0,columnspan=2,sticky='w',pady=(0,8))
        self.connect_button=ttk.Button(body,text='连接并开始使用',command=self.connect)
        self.connect_button.grid(row=7,column=0,columnspan=2,sticky='ew')
        self.status=tk.StringVar(value=window.controller.startup_notice or '点击后自动验证账号、测试翻译“你好”，加密保存并启用翻译。')
        ttk.Label(body,textvariable=self.status,wraplength=490).grid(row=8,column=0,columnspan=2,sticky='w',pady=10)
        controls=ttk.Frame(body);controls.grid(row=9,column=0,columnspan=2,sticky='ew')
        self.import_button=ttk.Button(controls,text='导入本机旧 .env',command=self.import_legacy)
        self.import_button.pack(side='left')
        self.delete_button=ttk.Button(controls,text='删除当前服务密钥',command=self.delete,state='normal' if current else 'disabled')
        self.delete_button.pack(side='left',padx=6)
        ttk.Button(controls,text='跳过 / 关闭',command=self.cancel).pack(side='right')
        self.use_saved_button=ttk.Button(body,text='使用已保存账户 / 应用上下文设置',command=self.use_saved)
        self.use_saved_button.grid(row=10,column=0,columnspan=2,sticky='ew',pady=(10,0))
        self.balance_status=tk.StringVar(value='')
        self.balance_label=ttk.Label(body,textvariable=self.balance_status,wraplength=490)
        self.balance_label.grid(row=11,column=0,columnspan=2,sticky='w',pady=6)
        self.balance_button=ttk.Button(body,text='查询 DeepSeek 账户余额',command=self.refresh_balance)
        self.balance_button.grid(row=12,column=0,columnspan=2,sticky='ew')
        self.provider_changed(clear=False)
        # This is our own dialog; scale geometry without changing global Tk scaling.
        dialog.update_idletasks()
        width=min(dialog.winfo_screenwidth()-40,dialog.winfo_reqwidth())
        height=min(dialog.winfo_screenheight()-60,dialog.winfo_reqheight())
        dialog.geometry(f'{max(320,width)}x{max(300,height)}')
        style_native_window(dialog, theme_colors(window.settings))

    def _busy(self, busy):
        state='disabled' if busy else 'normal'
        for widget in (self.key_entry,self.secret_entry,self.connect_button,self.context_button,*self.provider_buttons):widget.configure(state=state)
        self.import_button.configure(state='normal' if not busy and self.provider.get()=='aliyun' else 'disabled')
        self.use_saved_button.configure(state=state if self._profile().get('configured') else 'disabled')
        self.delete_button.configure(state='normal' if not busy and self.service.current else 'disabled')

    def _profile(self):
        method=getattr(self.service,'profiles_snapshot',None)
        if callable(method):return method().get('profiles',{}).get(self.provider.get(),{})
        return {'configured':bool(self.service.current) and self.provider.get()=='aliyun'}

    def provider_changed(self,clear=True):
        if clear:self.key.set('');self.secret.set('')
        deepseek=self.provider.get()=='deepseek'
        self.key_label.configure(text='DeepSeek API Key' if deepseek else 'AccessKey ID')
        self.key_entry.configure(show='●' if deepseek else '')
        for widget in (self.secret_label,self.secret_entry):
            widget.grid_remove() if deepseek else widget.grid()
        for widget in (self.context_button,self.balance_label,self.balance_button):
            widget.grid() if deepseek else widget.grid_remove()
        profile=self._profile()
        self.context_enabled.set(bool(profile.get('context_enabled',True)) if profile.get('configured') else True)
        self.service_note.configure(text=('非思考模式；仅翻译当前句。开启上下文时，接收消息会附带同会话前3条原文；手动回复只发送当前输入。'
                                          if deepseek else '使用阿里云机器翻译通用版；仅发送当前句。额度与实际用量在阿里云控制台查询。'))
        self._busy(bool(self.service.busy))
        self._show_balance()

    def use_saved(self):
        try:
            self.window.clear_draft();self.window.quick_input.cancel()
            options={'context_enabled':self.context_enabled.get()} if self.provider.get()=='deepseek' else {}
            self.service.select_provider(self.provider.get(),**options)
            self._busy(True)
            self.status.set('正在验证并应用已保存的翻译服务…')
        except ValueError as failure:self.status.set(str(failure))

    def refresh_balance(self):
        if getattr(self.service,'selected_provider','aliyun')!='deepseek':
            self.balance_status.set('请先连接或切换到已保存的 DeepSeek 账户。');return
        try:
            started=self.service.refresh_balance()
            if not started and not self.service.balance_snapshot().get('busy'):
                self.balance_status.set('余额查询未启动，请先连接当前 DeepSeek 账户并结束配置任务。');return
            self._show_balance()
        except ValueError as failure:self.balance_status.set(str(failure))

    def _show_balance(self):
        method=getattr(self.service,'balance_snapshot',None)
        if not callable(method):return
        result=method()
        self.balance_button.configure(state='disabled' if result.get('busy') or self.service.busy else 'normal')
        if result.get('busy'):
            self.balance_status.set('正在查询账户余额…');return
        if result.get('error'):
            self.balance_status.set(result['error']);return
        amounts=[f"{row['currency']} 总余额 {row['total_balance']}（赠送 {row['granted_balance']} / 充值 {row['topped_up_balance']}）"
                 for row in result.get('balance_infos',[])]
        text='；'.join(amounts) or '暂无余额结果，请点击查询。'
        if result.get('is_available') is False:text+=' · 账户当前不可用'
        if result.get('as_of'):
            try:
                when=datetime.fromisoformat(result['as_of']).astimezone(timezone(timedelta(hours=8)))
                text+='\n查询时间：'+when.strftime('%Y-%m-%d %H:%M:%S')+'（北京时间）'
            except (TypeError,ValueError):pass
        if self.balance_status.get()!=text:self.balance_status.set(text)

    def connect(self):
        try:
            self.window.clear_draft();self.window.quick_input.cancel()
            if self.provider.get()=='deepseek':
                self.service.connect_deepseek(self.key.get().strip(),context_enabled=self.context_enabled.get())
            else:
                self.service.connect(Credentials(self.key.get().strip(),self.secret.get().strip()))
            self.key.set('');self.secret.set('')
            self._busy(True)
            self.status.set('正在连接，自动验证、测试并加密保存；期间暂停其他云请求…')
        except ValueError as failure:self.status.set(str(failure))

    def import_legacy(self):
        path=filedialog.askopenfilename(parent=self.dialog,title='主动选择自己的旧 .env 配置',filetypes=[('环境配置','.env'),('所有文件','*')])
        if not path:return
        try:
            from pathlib import Path
            credentials=load_credentials(Path(path))
            self.key.set(credentials.access_key_id);self.secret.set(credentials.access_key_secret)
            self.status.set('已导入到本次输入框；点击“连接并开始使用”即可自动测试并加密保存。')
        except ConfigError:self.status.set('配置文件无效，请核对两个密钥字段，或直接输入。')

    def delete(self):
        if not messagebox.askyesno('删除本机密钥','删除当前正在使用的翻译服务密钥并关闭云翻译？另一家已保存的账户会保留。',parent=self.dialog):return
        try:
            self.window.clear_draft();self.window.quick_input.cancel()
            self.service.delete();self._busy(True)
            self.status.set('正在等待旧请求结束并删除本机凭据…')
        except ValueError as failure:self.status.set(str(failure))

    def handle(self, event):
        kind=event['kind']
        if kind in ('balance','balance_ready','balance_error'):
            self._show_balance();return
        if kind=='progress':
            self.status.set(event['message'])
            self._busy(True)
            return
        elif kind=='identified':
            self.current.configure(text='DeepSeek 账户已验证' if self.provider.get()=='deepseek' else '已验证主账号：'+event['account'])
            self.status.set('账号验证成功，正在继续测试翻译并保存…')
            self._busy(True)
            return
        elif kind=='ready':
            self.key.set('');self.secret.set('')
            self.current.configure(text='个人密钥已加密保存，后续启动自动读取。')
            self.status.set('配置成功，翻译已启用。测试译文：'+event['translation'])
        elif kind=='removed':
            self.key.set('');self.secret.set('');self.current.configure(text='个人密钥已删除；继续接收原文。')
            self.status.set('已关闭云翻译，聊天历史仍保留。')
        elif kind=='selected':
            self.current.configure(text='当前服务尚未配置个人密钥；继续接收原文。')
            self.status.set('已切换翻译服务，请填写当前服务的个人密钥。')
        elif kind=='error':self.status.set(event['message'])
        self._busy(False)

    def cancel(self):
        self.secret.set('');self.key.set('')
        self.service.cancel()
        self.window.credential_dialog=None
        self.dialog.destroy()
