"""双击播报的显式开关；本组件不安装hook、不读取游戏。"""
import tkinter as tk
from tkinter import ttk

from .rounded_panel import RoundedPanel
from .theme import theme_colors


class BoardPanel(ttk.Frame):
    def __init__(self, parent, *, settings, metrics, on_toggle):
        super().__init__(parent,padding=14)
        self.settings,self.metrics,self.on_toggle=settings,metrics,on_toggle
        self._closed=False
        self._labels=[]
        self.enabled=tk.BooleanVar(self,value=settings.board_broadcast_enabled)
        ttk.Checkbutton(self,text='开启 Tab 双击播报，自动发送到己方团队',
                        variable=self.enabled,command=self._toggle).pack(anchor='w',pady=(0,10))
        self._label(self,'点哪项，就说哪项。自己、队友和对手的已选天赋、统计数据都可以播报。').pack(fill='x',pady=(0,14))
        self.card=RoundedPanel(self,settings=settings,padding=14,fit_height=True)
        self.card.pack(fill='x')
        self._label(self.card.body,'Tab → 双击具体的一格',surface=True,heading=True).pack(fill='x')
        for line in ('天赋：玩家、英雄、天赋等级、名称和一句关键效果。',
                     '统计：只发送点中的击杀、助攻、死亡、伤害或治疗等单项数据。',
                     '例如：【我方】玩家（安度因）｜英雄伤害：12,345'):
            self._label(self.card.body,line,surface=True).pack(fill='x',pady=(7,0))
        self.detail=tk.StringVar(self,value='双击播报已关闭。')
        self._label(self,variable=self.detail).pack(fill='x',pady=(14,10))
        self._label(self,'保留游戏默认的 Ctrl＋Enter 团队聊天快捷键。双击后松开鼠标和按键，助手会核对频道再发送。').pack(fill='x')
        self._label(self,'聊天框或菜单打开、其他输入功能运行时暂停。同一项短时间内不会重复播报；“—”不播报。发送失败不自动重试，未提交的文字可按 Esc 取消。').pack(fill='x',pady=(8,0))
        self.bind('<Configure>',lambda event:self._layout(),add='+')
        self.apply_settings(settings,metrics)

    def _label(self,parent,text='',*,variable=None,surface=False,heading=False):
        label=tk.Label(parent,text=text,textvariable=variable,anchor='w',justify='left',
                       borderwidth=0,highlightthickness=0,wraplength=400)
        self._labels.append((label,surface,heading))
        return label

    def _layout(self):
        for label,surface,heading in self._labels:
            label.configure(wraplength=max(100,self.winfo_width()-self.metrics.px(56 if surface else 28)))

    def _toggle(self):
        if self._closed:return
        try:self.on_toggle(self.enabled.get())
        except (OSError,ValueError) as failure:
            self.enabled.set(self.settings.board_broadcast_enabled)
            self.detail.set(str(failure))

    def update_status(self,state,detail=''):
        if not self._closed:self.detail.set(detail or state)

    def apply_settings(self,settings,metrics=None):
        if self._closed:return
        self.settings=settings
        if metrics is not None:self.metrics=metrics
        self.enabled.set(settings.board_broadcast_enabled)
        colors=theme_colors(settings)
        for label,surface,heading in self._labels:
            label.configure(background=colors['surface' if surface else 'background'],
                            foreground=colors['text' if heading else 'muted'],
                            font=('Microsoft YaHei UI',settings.font_size+(2 if heading else 0),
                                  'bold' if heading else 'normal'))
        self.card.apply_settings(settings,self.metrics)
        self._layout()

    def close(self):
        self._closed=True
        self.on_toggle=None
