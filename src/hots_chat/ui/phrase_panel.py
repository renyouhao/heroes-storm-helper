from dataclasses import replace
import tkinter as tk
from tkinter import ttk,messagebox,filedialog,font as tkfont
from pathlib import Path
import uuid
from ..phrases import Phrase


class PhrasePanel(ttk.Frame):
    def __init__(self,parent,book,on_use,on_change):
        super().__init__(parent,padding=8);self.book,self.on_use,self.on_change=book,on_use,on_change
        self.query=tk.StringVar();ttk.Entry(self,textvariable=self.query).pack(fill='x')
        self.query.trace_add('write',lambda *a:self.refresh())
        self._list_font=tkfont.Font(root=self,family='Microsoft YaHei UI',size=11)
        self.listbox=tk.Listbox(self,font=self._list_font,height=8,borderwidth=0,
                               exportselection=False,activestyle='dotbox')
        self.listbox.bind('<Double-1>',lambda e:self.use())
        row=ttk.Frame(self);row.pack(side='bottom',fill='x');self.actions=row
        for label,command in [('使用',self.use),('新增',lambda:self.edit(None)),('编辑',lambda:self.edit(self.selected())),('收藏',self.favorite),('删除',self.delete),('导入',self.import_file),('导出所选',self.export_file)]:
            ttk.Button(row,text=label,command=command,
                       style='Accent.TButton' if label=='使用' else 'TButton',padding=(6,6),width=0).pack(side='left',padx=2)
        self.notice=ttk.Label(self,text='已校对的短句，可在精确匹配时直接使用本机译文。',
                              style='Muted.TLabel')
        self.notice.pack(side='bottom',anchor='w',pady=5)
        self._notice_shown=True
        # 管理入口先分配空间，短句列表使用余下高度并保持键盘滚动。
        self.listbox.pack(fill='both',expand=True,pady=5)
        self.bind('<Configure>',self._fit_notice)
        self.refresh()

    def _fit_notice(self,event):
        shown=event.height>=self._list_font.metrics('linespace')*10
        if shown==self._notice_shown:return
        self._notice_shown=shown
        if shown:self.notice.pack(side='bottom',anchor='w',pady=5,before=self.listbox)
        else:self.notice.pack_forget()

    def apply_metrics(self,metrics):
        for button in self.actions.winfo_children():
            button.configure(padding=(metrics.px(6),metrics.px(6)))

    def refresh(self):
        self.items=self.book.list(query=self.query.get());self.listbox.delete(0,'end')
        for p in self.items:self.listbox.insert('end',f'{"★" if p.favorite else "☆"} [{p.category}] {p.source_text} · {"已校对" if p.reviewed else "待校对"}')
    def selected(self):
        selection=self.listbox.curselection();return self.items[selection[0]] if selection else None
    def use(self):
        p=self.selected()
        if p:self.on_use(p.source_text)
    def favorite(self):
        p=self.selected()
        if p:self.book.save(replace(p,favorite=not p.favorite));self.changed()
    def changed(self):self.refresh();self.on_change()
    def delete(self):
        p=self.selected()
        if p and messagebox.askyesno('删除短句','删除选中的短句？',parent=self):self.book.delete(p.id);self.changed()
    def edit(self,p):
        p=p or Phrase(uuid.uuid4().hex,'机制','',{'en':'','ko':''})
        dialog=tk.Toplevel(self);dialog.title('编辑常用短句');variables={}
        category=tk.StringVar(value=p.category);ttk.Combobox(dialog,textvariable=category,values=['对线','团战','机制','礼貌'],state='readonly').pack(fill='x')
        for key,label,value in [('source_text','中文',p.source_text),('en','英语',p.translations.get('en','')),('ko','韩语',p.translations.get('ko',''))]:
            ttk.Label(dialog,text=label).pack();v=tk.StringVar(value=value);ttk.Entry(dialog,textvariable=v,width=65).pack(fill='x');variables[key]=v
        reviewed=tk.BooleanVar(value=p.reviewed);ttk.Checkbutton(dialog,text='我已核对以上译文，允许本机直接输出',variable=reviewed).pack()
        def save():
            try:
                self.book.save(replace(p,category=category.get(),source_text=variables['source_text'].get(),translations={k:variables[k].get() for k in ('en','ko') if variables[k].get()},reviewed=reviewed.get()))
                self.changed();dialog.destroy()
            except ValueError as failure:messagebox.showerror('保存失败',str(failure),parent=dialog)
        ttk.Button(dialog,text='保存',command=save).pack(pady=8)
    def import_file(self):
        name=filedialog.askopenfilename(filetypes=[('JSON','*.json')],parent=self)
        if not name:return
        try:
            p=Path(name)
            if p.stat().st_size>1048576:raise ValueError('文件过大。')
            self.book.import_json(p.read_text(encoding='utf-8'));self.changed()
        except (OSError,ValueError) as failure:messagebox.showerror('导入失败',str(failure),parent=self)
    def export_file(self):
        p=self.selected()
        if not p:return
        name=filedialog.asksaveasfilename(defaultextension='.json',parent=self)
        if name:
            try:Path(name).write_text(self.book.export_json([p.id]),encoding='utf-8')
            except OSError:messagebox.showerror('导出失败','无法写入所选文件。',parent=self)
