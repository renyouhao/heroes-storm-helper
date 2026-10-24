from dataclasses import dataclass,asdict,field
from pathlib import Path
import json
import math
import os
import ctypes
from ctypes import wintypes
from .appearance import DEFAULT_THEME, normalize_theme


class MonitorInfo(ctypes.Structure):
    """复用类型，避免每次显示器轮询被 ctypes 指针缓存永久保留。"""
    _fields_=[('cb',wintypes.DWORD),('monitor',wintypes.RECT),('work',wintypes.RECT),('flags',wintypes.DWORD)]


def screen_bounds():
    user=ctypes.WinDLL('user32',use_last_error=True)
    bounds=[]
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HANDLE,wintypes.HDC,ctypes.POINTER(wintypes.RECT),wintypes.LPARAM)
    user.GetMonitorInfoW.argtypes=[wintypes.HANDLE,ctypes.POINTER(MonitorInfo)]
    @callback_type
    def collect(monitor,dc,rect,data):
        info=MonitorInfo();info.cb=ctypes.sizeof(info)
        if user.GetMonitorInfoW(monitor,ctypes.byref(info)):
            r=info.work;bounds.append((bool(info.flags & 1),(r.left,r.top,r.right,r.bottom)))
        return True
    user.EnumDisplayMonitors.argtypes=[wintypes.HDC,ctypes.POINTER(wintypes.RECT),callback_type,wintypes.LPARAM]
    user.EnumDisplayMonitors(None,None,collect,0)
    # EnumDisplayMonitors enumeration order is not the primary-monitor order.
    return [area for primary,area in sorted(bounds,key=lambda item:not item[0])] or [(0,0,user.GetSystemMetrics(0),user.GetSystemMetrics(1))]


def work_area_at(areas, x, y):
    """Select a containing/nearest work area in virtual-desktop coordinates."""
    if not areas:return None
    return next((tuple(area) for area in areas if area[0]<=x<area[2] and area[1]<=y<area[3]),
        tuple(min(areas,key=lambda area:max(area[0]-x,0,x-area[2])**2+
                                       max(area[1]-y,0,y-area[3])**2)))


@dataclass
class WindowSettings:
    mode: str='normal'
    font_size: int=12
    alpha: float=1.0
    x: int=120
    y: int=80
    locked: bool=False
    topmost: bool=False
    hotkeys: dict=field(default_factory=lambda:{'focus':'Ctrl+T'})
    preferences_version: int=2
    target_language: str='ko'
    auto_translate: bool=True
    overlay_enabled: bool=True
    overlay_x: int=40
    overlay_y: int=180
    overlay_width: int=420
    overlay_font_size: int=11
    overlay_alpha: float=.88
    overlay_timeout: int=6
    overlay_original: bool=False
    main_anchor: tuple | None=None
    overlay_anchor: tuple | None=None
    overlay_placement: str='bottom'
    overlay_bottom_anchor: float | None=None
    overlay_style: str='subtitle'
    overlay_monitor: tuple | None=None
    color_theme: str=DEFAULT_THEME
    repeat_hotkey: str='F8'
    repeat_action: str='S'
    repeat_interval_ms: int=20
    repeat_s_interval_ms: int=20
    talent_enabled: bool=False
    board_broadcast_enabled: bool=False
    board_output_language: str='zh'

    def __post_init__(self):
        self.color_theme=normalize_theme(self.color_theme)

    def clamp(self,screen_width,screen_height,width=720,height=500,left=0,top=0):
        self.font_size=max(10,min(32,int(self.font_size)))
        self.alpha=max(.35,min(1.,float(self.alpha))) if math.isfinite(float(self.alpha)) else 1.
        self.x=max(left,min(int(self.x),max(left,left+screen_width-width)))
        self.y=max(top,min(int(self.y),max(top,top+screen_height-height)))

    def recover(self,monitors,width=720,height=500):
        if not monitors:return
        self.x,self.y=int(self.x),int(self.y)
        containing=next((r for r in monitors if r[0]<=self.x and r[1]<=self.y and self.x+width<=r[2] and self.y+height<=r[3]),None)
        target=containing or min(monitors,key=lambda r:max(r[0]-self.x,0,self.x-r[2])**2+max(r[1]-self.y,0,self.y-r[3])**2)
        self.clamp(target[2]-target[0],target[3]-target[1],width,height,target[0],target[1])

    def recover_overlay(self,monitors,*,scale=1.0,height=None):
        self.overlay_width=max(280,min(900,int(self.overlay_width)))
        self.overlay_font_size=max(10,min(32,int(self.overlay_font_size)))
        self.overlay_timeout=max(5,min(120,int(self.overlay_timeout)))
        value=float(self.overlay_alpha)
        self.overlay_alpha=max(.35,min(1.,value)) if math.isfinite(value) else .88
        if not monitors:return
        # overlay_width保存96-DPI逻辑宽度，位置保持当前Tk坐标。
        from .layout import DisplayMetrics,overlay_layout
        preferred=tuple(self.overlay_monitor) if self.overlay_monitor is not None else None
        target=preferred if preferred in [tuple(area) for area in monitors] else work_area_at(monitors,self.overlay_x,self.overlay_y)
        self.overlay_monitor=tuple(target)
        layout=overlay_layout(DisplayMetrics(tuple(target),scale),width=self.overlay_width,
                              x=self.overlay_x,y=self.overlay_y,
                              height=round(300*scale) if height is None else height)
        self.overlay_x,self.overlay_y=layout.x,layout.y

    def migrate_display(self,metrics):
        """旧版本保存96-DPI虚拟坐标；原生DPI启动时只迁移一次。"""
        if self.preferences_version>=3:return False
        for name in ('x','y','overlay_x','overlay_y'):
            setattr(self,name,round(getattr(self,name)*metrics.scale))
        self.preferences_version=3
        return True

    def record_main_anchor(self,metrics,width,height):
        from .layout import relative_position
        self.main_anchor=relative_position(metrics,width=width,height=height,
                                          x=self.x,y=self.y,decorated=True)

    def record_overlay_anchor(self,metrics,width,height):
        from .layout import relative_position
        self.overlay_anchor=relative_position(metrics,width=width,height=height,
                                             x=self.overlay_x,y=self.overlay_y)
        _,top,_,bottom=metrics.work_area
        self.overlay_bottom_anchor=min(1.,max(0.,(self.overlay_y+height-top)/(bottom-top)))

    @classmethod
    def load(cls,path):
        try:
            if Path(path).stat().st_size>16384:raise ValueError
            data=json.loads(Path(path).read_text(encoding='utf-8'))
            if not isinstance(data,dict) or set(data)-set(cls.__dataclass_fields__):raise ValueError
            if 'preferences_version' not in data and data.get('hotkeys')=={}:
                data['hotkeys']={'focus':'Ctrl+T'}
            # 样式字段也是一次性迁移标记，保存新配置后不再改写用户尺寸。
            if 'overlay_style' not in data:
                for name,old,new in (('overlay_font_size',14,11),
                                     ('overlay_timeout',15,6),('overlay_width',440,420)):
                    if data.get(name)==old:data[name]=new
                if data.get('overlay_placement')=='chat':data['overlay_placement']='bottom'
            if data.get('overlay_style','subtitle') not in ('subtitle','panel'):
                data['overlay_style']='subtitle'
            # 旧版本保存过的位置属于用户偏好；非法新选项也不应清空其他设置。
            if data.get('overlay_placement') not in ('bottom','chat','custom'):
                data['overlay_placement']=('custom' if any(
                    name in data for name in ('overlay_x','overlay_y','overlay_anchor')) else 'bottom')
            s=cls(**data)
            monitor=s.overlay_monitor
            if monitor is not None:
                if (not isinstance(monitor,(tuple,list)) or len(monitor)!=4
                        or any(type(value) is not int for value in monitor)
                        or monitor[2]<=monitor[0] or monitor[3]<=monitor[1]):
                    s.overlay_monitor=None
                else:s.overlay_monitor=tuple(monitor)
            # 必须明确布尔同意；损坏的此项不丢弃其他外观偏好。
            if type(s.talent_enabled) is not bool:s.talent_enabled=False
            if type(s.board_broadcast_enabled) is not bool:s.board_broadcast_enabled=False
            if s.board_output_language not in ('zh','en'):s.board_output_language='zh'
            from .hotkeys import parse_function_key
            try:
                parse_function_key(s.repeat_hotkey)
                s.repeat_hotkey=s.repeat_hotkey.strip().upper()
            except ValueError:s.repeat_hotkey='F8'
            from .repeat import validate_repeat_config
            try:validate_repeat_config(s.repeat_action,s.repeat_interval_ms)
            except ValueError:s.repeat_action,s.repeat_interval_ms='S',20
            try:validate_repeat_config('S',s.repeat_s_interval_ms)
            except ValueError:s.repeat_s_interval_ms=20
            if s.mode not in ('normal','compact') or s.target_language not in ('ko','en') or not isinstance(s.hotkeys,dict):raise ValueError
            s.color_theme=normalize_theme(s.color_theme)
            if s.overlay_bottom_anchor is not None:
                if (type(s.overlay_bottom_anchor) not in (int,float)
                        or not math.isfinite(s.overlay_bottom_anchor)
                        or not 0<=s.overlay_bottom_anchor<=1):raise ValueError
                s.overlay_bottom_anchor=float(s.overlay_bottom_anchor)
            if any(type(getattr(s,key)) is not bool for key in ('locked','topmost','auto_translate','overlay_enabled','overlay_original')):raise ValueError
            if any(type(getattr(s,key)) is not int for key in ('font_size','x','y','preferences_version','overlay_x','overlay_y','overlay_width','overlay_font_size','overlay_timeout')):raise ValueError
            if s.preferences_version not in (1,2,3):raise ValueError
            for name in ('main_anchor','overlay_anchor'):
                anchor=getattr(s,name)
                if anchor is None:continue
                if (not isinstance(anchor,(tuple,list)) or len(anchor)!=2
                        or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in anchor)):
                    raise ValueError
                setattr(s,name,tuple(float(v) for v in anchor))
            if any(type(getattr(s,key)) not in (float,int) or not math.isfinite(getattr(s,key)) for key in ('alpha','overlay_alpha')):raise ValueError
            from .hotkeys import parse_binding
            try:
                if set(s.hotkeys)-{'focus','translate','copy'}:raise ValueError
                for binding in s.hotkeys.values():
                    if not isinstance(binding,str):raise ValueError
                    parse_binding(binding)
            except ValueError:s.hotkeys={}
            s.clamp(100000,100000,left=-50000,top=-50000)
            s.recover_overlay([])
            s.preferences_version=max(2,s.preferences_version)
            return s
        except (OSError,ValueError,TypeError):return cls()

    def save(self,path):
        self.color_theme=normalize_theme(self.color_theme)
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(asdict(self),ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temp,path)
