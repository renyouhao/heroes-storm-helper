"""更新弹窗仅呈现后台状态，打开时不自动联网或安装。"""
import tkinter as tk
from tkinter import ttk

from .. import __version__
from .theme import style_native_window, theme_colors


class UpdateDialog:
    def __init__(self, window, *, show=True):
        self.owner = window
        self.service = window.update_service
        self._closed = False
        self._timer = None
        self._notes_key = None
        self.dialog = tk.Toplevel(window.root)
        self.dialog.withdraw()
        self.dialog.title('检查更新 · 风暴英雄助手')
        self.dialog.transient(window.root)
        self.dialog.geometry('620x470')
        self.dialog.minsize(420, 340)
        self.dialog.columnconfigure(0, weight=1)
        self.dialog.rowconfigure(3, weight=1)
        self.dialog.protocol('WM_DELETE_WINDOW', self.close)
        self.dialog.bind('<Escape>', lambda event:self.close())
        body = ttk.Frame(self.dialog, padding=18)
        body.grid(row=0, column=0, sticky='ew')
        ttk.Label(body, text='客户端更新', style='Heading.TLabel').pack(anchor='w')
        self.version = tk.StringVar(self.dialog, value='当前版本 ' + __version__)
        ttk.Label(body, textvariable=self.version, style='Muted.TLabel').pack(anchor='w', pady=(7,0))
        self.detail = tk.StringVar(self.dialog)
        ttk.Label(self.dialog, textvariable=self.detail, wraplength=540, justify='left',
                  style='Muted.TLabel', padding=(18,0,18,10)).grid(row=1,column=0,sticky='ew')
        self.progress = ttk.Progressbar(self.dialog, mode='determinate', maximum=100)
        self.progress.grid(row=2,column=0,sticky='ew',padx=18,pady=(0,12))
        notes_box = ttk.Frame(self.dialog)
        notes_box.grid(row=3,column=0,sticky='nsew',padx=18)
        notes_box.columnconfigure(0,weight=1); notes_box.rowconfigure(0,weight=1)
        colors = theme_colors(window.settings)
        self.notes = tk.Text(notes_box,wrap='word',height=6,state='disabled',
                             font=('Microsoft YaHei UI',window.settings.font_size),
                             background=colors['field'],foreground=colors['text'],
                             padx=10,pady=8,relief='flat')
        self.notes.grid(row=0,column=0,sticky='nsew')
        scroll = ttk.Scrollbar(notes_box, orient='vertical',command=self.notes.yview)
        scroll.grid(row=0,column=1,sticky='ns'); self.notes.configure(yscrollcommand=scroll.set)
        actions = ttk.Frame(self.dialog,padding=18)
        actions.grid(row=4,column=0,sticky='ew'); actions.columnconfigure(0,weight=1)
        self.check_button = ttk.Button(actions,text='检查更新',command=self._check)
        self.check_button.grid(row=0,column=0,sticky='w')
        self.download_button = ttk.Button(actions,text='下载更新',command=self._download)
        self.download_button.grid(row=0,column=1,padx=6)
        self.install_button = ttk.Button(actions,text='重启并更新',style='Accent.TButton',command=window.install_update)
        self.install_button.grid(row=0,column=2)
        self.cancel_button = ttk.Button(actions,text='取消任务',command=self._cancel)
        self.cancel_button.grid(row=1,column=0,sticky='w',pady=(10,0))
        ttk.Label(actions,text='下载后由你决定安装时机；个人密钥和设置保留。',
                  style='Muted.TLabel',wraplength=470).grid(row=1,column=1,columnspan=2,sticky='e',pady=(10,0))
        self.archive_frame = ttk.Frame(actions)
        self.archive_frame.grid(row=2,column=0,columnspan=3,sticky='ew',pady=(10,0))
        self.archive_frame.columnconfigure(0,weight=1)
        self.archive_hint = ttk.Label(self.archive_frame,
            text='关闭助手或重新检查会清理未安装暂存包；手动使用请先复制。',style='Muted.TLabel',wraplength=500)
        self.archive_hint.grid(row=0,column=0,sticky='w')
        self.archive_path = tk.StringVar(self.dialog)
        self.archive_entry = ttk.Entry(self.archive_frame,textvariable=self.archive_path,state='readonly')
        self.archive_entry.grid(row=1,column=0,sticky='ew',pady=(5,0))
        self.refresh()
        style_native_window(self.dialog, colors)
        self._timer = self.dialog.after(100,self._pump)
        if show:self.dialog.deiconify()

    def _check(self):
        if self._closed:return
        if not self.service.check():self.detail.set('上次任务尚未退出，请稍后再试。')
        else:self.refresh()

    def _download(self):
        if self._closed:return
        if not self.service.download():self.detail.set('请先检查可用更新，并等待上次任务结束。')
        else:self.refresh()

    def _cancel(self):
        if self._closed:return
        if self.service.latest.state in ('checking','downloading'):
            self.service.cancel(); self.refresh()

    def _pump(self):
        self._timer = None
        if self._closed:return
        self.refresh()
        self._timer = self.dialog.after(100,self._pump)

    def refresh(self):
        if self._closed:return
        status = self.service.latest
        release = status.release
        suffix = ' · 最新正式版 ' + release.version if release is not None else ''
        self.version.set('当前版本 ' + __version__ + suffix)
        self.detail.set(status.detail)
        can_install = getattr(self.owner,'updates_can_install',False)
        if status.state=='downloaded' and not can_install:
            self.detail.set('下载和校验已完成。当前从源码运行，请手动使用新发行包。')
        archive = getattr(status.staged,'archive_path',None) if status.state=='downloaded' else None
        self.archive_path.set(str(archive) if archive is not None else '')
        if archive is None:self.archive_frame.grid_remove()
        else:self.archive_frame.grid()
        self.progress['value'] = min(100,100*status.downloaded/status.total) if status.total else 0
        self.check_button.state(['disabled' if self.service.busy or status.state == 'restarting' else '!disabled'])
        self.download_button.state(['!disabled' if status.state=='available' and not self.service.busy else 'disabled'])
        self.install_button.state(['!disabled' if status.state=='downloaded' and status.staged is not None and can_install else 'disabled'])
        self.cancel_button.state(['!disabled' if status.state in ('checking','downloading') else 'disabled'])
        notes = getattr(release,'notes','') if release is not None else ''
        if notes != self._notes_key:
            self._notes_key = notes
            self.notes.configure(state='normal'); self.notes.delete('1.0','end')
            self.notes.insert('1.0',notes or '检查到新版本后，这里会显示更新内容。')
            self.notes.configure(state='disabled')

    def close(self):
        if self._closed:return
        self._closed = True
        try:
            if self._timer is not None:
                try:self.dialog.after_cancel(self._timer)
                except tk.TclError:pass
                finally:self._timer = None
            if self.service is not None and self.service.busy and self.service.latest.state in ('checking','downloading'):
                self.service.cancel()
        finally:
            try:self.dialog.destroy()
            except tk.TclError:pass
            finally:self.owner = self.service = None
