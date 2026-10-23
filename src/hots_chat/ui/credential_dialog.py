"""密钥只在掩码输入框与后台配置服务内流转，不放进偏好或事件。"""
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
        dialog.title('配置自己的阿里云翻译')
        dialog.transient(window.root)
        dialog.protocol('WM_DELETE_WINDOW',self.cancel)
        dialog.columnconfigure(0,weight=1)
        body=ttk.Frame(dialog,padding=18);body.grid(sticky='nsew')
        body.columnconfigure(1,weight=1)
        ttk.Label(body,text='每位用户使用自己的 AccessKey；可跳过并只接收原文。',wraplength=490).grid(row=0,column=0,columnspan=2,sticky='w',pady=(0,10))
        current=self.service.current
        label=('已保存：'+masked_key(current.credentials.access_key_id)+' ｜ 主账号 '+current.account) if current else '尚未保存个人密钥'
        self.current=ttk.Label(body,text=label,wraplength=490)
        self.current.grid(row=1,column=0,columnspan=2,sticky='w')
        self.key=tk.StringVar();self.secret=tk.StringVar()
        ttk.Label(body,text='AccessKey ID').grid(row=2,column=0,sticky='w',pady=(10,3))
        self.key_entry=ttk.Entry(body,textvariable=self.key,width=42)
        self.key_entry.grid(row=2,column=1,sticky='ew',padx=(10,0),pady=(10,3))
        ttk.Label(body,text='AccessKey Secret').grid(row=3,column=0,sticky='w',pady=3)
        self.secret_entry=ttk.Entry(body,textvariable=self.secret,show='●',width=42)
        self.secret_entry.grid(row=3,column=1,sticky='ew',padx=(10,0),pady=3)
        self.connect_button=ttk.Button(body,text='连接并开始使用',command=self.connect)
        self.connect_button.grid(row=7,column=0,columnspan=2,sticky='ew')
        self.status=tk.StringVar(value=window.controller.startup_notice or '点击后自动验证账号、测试翻译“你好”，加密保存并启用翻译。')
        ttk.Label(body,textvariable=self.status,wraplength=490).grid(row=8,column=0,columnspan=2,sticky='w',pady=10)
        controls=ttk.Frame(body);controls.grid(row=9,column=0,columnspan=2,sticky='ew')
        self.import_button=ttk.Button(controls,text='导入本机旧 .env',command=self.import_legacy)
        self.import_button.pack(side='left')
        self.delete_button=ttk.Button(controls,text='删除已保存密钥',command=self.delete,state='normal' if current else 'disabled')
        self.delete_button.pack(side='left',padx=6)
        ttk.Button(controls,text='跳过 / 关闭',command=self.cancel).pack(side='right')
        # This is our own dialog; scale geometry without changing global Tk scaling.
        dialog.update_idletasks()
        width=min(dialog.winfo_screenwidth()-40,dialog.winfo_reqwidth())
        height=min(dialog.winfo_screenheight()-60,dialog.winfo_reqheight())
        dialog.geometry(f'{max(320,width)}x{max(300,height)}')
        style_native_window(dialog, theme_colors(window.settings))

    def _busy(self, busy):
        state='disabled' if busy else 'normal'
        for widget in (self.key_entry,self.secret_entry,self.connect_button,self.import_button):widget.configure(state=state)
        self.delete_button.configure(state='normal' if not busy and self.service.current else 'disabled')

    def connect(self):
        try:
            self.window.clear_draft();self.window.quick_input.cancel()
            self.service.connect(Credentials(self.key.get().strip(),self.secret.get().strip()))
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
        if not messagebox.askyesno('删除本机密钥','删除这台电脑当前用户保存的密钥并关闭云翻译？',parent=self.dialog):return
        try:
            self.window.clear_draft();self.window.quick_input.cancel()
            self.service.delete();self._busy(True)
            self.status.set('正在等待旧请求结束并删除本机凭据…')
        except ValueError as failure:self.status.set(str(failure))

    def handle(self, event):
        kind=event['kind']
        if kind=='progress':
            self.status.set(event['message'])
            self._busy(True)
            return
        elif kind=='identified':
            self.current.configure(text='已验证主账号：'+event['account'])
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
        elif kind=='error':self.status.set(event['message'])
        self._busy(False)

    def cancel(self):
        self.secret.set('');self.key.set('')
        self.service.cancel()
        self.window.credential_dialog=None
        self.dialog.destroy()
