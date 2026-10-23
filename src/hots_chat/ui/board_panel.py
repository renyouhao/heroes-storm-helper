"""双击播报的显式开关；本组件不安装hook、不读取游戏。"""
import tkinter as tk
from tkinter import ttk

from .rounded_panel import RoundedPanel
from .theme import theme_colors


_TITLES = {
    'disabled': '双击播报已关闭', 'waiting': '正在连接双击播报',
    'idle': '已连接，等待 Tab 内双击', 'armed': '等待同一项目的第二次点击',
    'pending': '已识别双击，等待安全发送', 'deferred': '等待输入空闲后发送',
    'ready': '正在核对团队频道', 'sent': '已发送到己方团队',
    'cancelled': '本次播报已取消', 'limited': '短时间内不重复播报',
    'error': '本次播报未完整确认，请核对聊天记录',
}


class BoardPanel(ttk.Frame):
    def __init__(self, parent, *, settings, metrics, on_toggle, on_retry=None):
        super().__init__(parent,padding=14)
        self.settings,self.metrics,self.on_toggle=settings,metrics,on_toggle
        self._on_retry=on_retry
        self._running=None
        self._state,self._detail='waiting',''
        self._last_render=None
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
        self.title=tk.StringVar(self)
        self.detail=tk.StringVar(self)
        self.last_result=tk.StringVar(self)
        self._label(self,variable=self.title,heading=True).pack(fill='x',pady=(14,0))
        self._label(self,variable=self.detail).pack(fill='x',pady=(14,10))
        self._label(self,variable=self.last_result).pack(fill='x',pady=(0,10))
        self.retry_button=ttk.Button(self.card.body,text='重新连接',command=self._retry,
                                     style='Surface.TButton')
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
        if self._closed:return
        self._state,self._detail=state,detail
        self._render_status()

    def set_running(self,running):
        """同步服务实际运行状态，不改保存的开关。"""
        if self._closed or self._running is bool(running):return
        self._running=bool(running)
        self._render_status()

    def _retry(self):
        if self._closed or self._on_retry is None:return
        try:
            if self._on_retry() is False:
                self.detail.set('上一次运行仍在退出，请稍后重新连接。')
        except (OSError,ValueError) as failure:
            self.detail.set('重新连接失败：'+str(failure))

    def _render_status(self):
        signature=(self.settings.board_broadcast_enabled,self._running,self._state,self._detail)
        if signature==self._last_render:return
        self._last_render=signature
        title=_TITLES.get(self._state,'双击播报状态')
        detail=self._detail or title+'。'
        if not self.settings.board_broadcast_enabled:
            title,detail=_TITLES['disabled'],self._detail or '开启后在 Tab 面板双击具体的一格。'
        elif self._running is False:
            title='运行已停止 · 开启偏好已保留'
            detail=(self._detail+'\n' if self._detail else '')+'点击“重新连接”恢复监听。'
        if self.title.get()!=title:self.title.set(title)
        if self.detail.get()!=detail:self.detail.set(detail)
        if self._state in ('sent','error'):
            result='最近结果：'+(self._detail or _TITLES[self._state])
            if self.last_result.get()!=result:self.last_result.set(result)
        if self.settings.board_broadcast_enabled and self._running is False and self._on_retry is not None:
            self.retry_button.pack(anchor='w',pady=(10,0))
        else:self.retry_button.pack_forget()

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
        self._render_status()

    def close(self):
        if self._closed:return
        self._closed=True
        self.on_toggle=self._on_retry=None
        if self.retry_button.winfo_exists():self.retry_button.configure(command='')

    def destroy(self):
        if getattr(self,'_destroyed',False):return
        self._destroyed=True
        self.close()
        descendants=[]
        pending=list(self.children.values())
        while pending:
            child=pending.pop()
            descendants.append(child)
            pending.extend(child.children.values())
        super().destroy()
        # 销毁后解除缓存子控件的master回边，保证Tk变量在UI线程及时释放。
        self._labels.clear()
        for child in descendants:child.master=None
        self.master=None
