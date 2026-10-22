"""单屏布局：工作区/几何使用Tk坐标，配置尺寸使用96-DPI逻辑像素。

正数字号仍为点值，由Tk自己的 ``tk scaling`` 缩放一次。本模块不修改
进程DPI感知或Tk全局缩放；调用者只需在工作区/缩放改变时重新应用布局。
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class DisplayMetrics:
    work_area: tuple
    scale: float = 1.0

    def __post_init__(self):
        area=tuple(int(value) for value in self.work_area)
        if len(area)!=4 or area[2]<=area[0] or area[3]<=area[1]:
            raise ValueError('工作区必须有正的宽度和高度')
        if not math.isfinite(self.scale) or self.scale<=0:
            raise ValueError('缩放必须为有限正数')
        object.__setattr__(self,'work_area',area)

    def px(self,value):
        return max(1,round(value*self.scale))


@dataclass(frozen=True)
class WindowLayout:
    width: int
    height: int
    x: int
    y: int
    min_width: int = 1
    min_height: int = 1
    padding: int = 1
    row_height: int = 1
    collapse_details: bool = False

    @property
    def geometry(self):
        return f'{self.width}x{self.height}+{self.x}+{self.y}'


def display_metrics(root,work_areas=None):
    """读取当前Tk点到像素比例，禁止再乘一次Windows显示缩放。"""
    if work_areas is None:
        from .window_settings import screen_bounds
        work_areas=screen_bounds()
    if not work_areas:
        work_areas=[(0,0,root.winfo_screenwidth(),root.winfo_screenheight())]
    # Tk scaling是每点像素数；96-DPI时为96/72，不是1。
    scale=round(float(root.tk.call('tk','scaling'))*72/96,4)
    return DisplayMetrics(tuple(work_areas[0]),scale)


def bound_rect(metrics,*,width,height,x=0,y=0):
    left,top,right,bottom=metrics.work_area
    width=max(1,min(round(width),right-left))
    height=max(1,min(round(height),bottom-top))
    x=max(left,min(round(x),right-width))
    y=max(top,min(round(y),bottom-height))
    return WindowLayout(width,height,x,y)


def _client_metrics(metrics):
    left,top,right,bottom=metrics.work_area
    # geometry指定客户区尺寸；给Windows标题栏和边框预留空间。
    return DisplayMetrics((left,top,max(left+1,right-metrics.px(16)),
                           max(top+1,bottom-metrics.px(40))),metrics.scale)


def relative_position(metrics,*,width,height,x,y,decorated=False):
    area=_client_metrics(metrics).work_area if decorated else metrics.work_area
    left,top,right,bottom=area
    free_x=max(0,right-left-round(width))
    free_y=max(0,bottom-top-round(height))
    return (min(1.,max(0.,(x-left)/free_x)) if free_x else 0.,
            min(1.,max(0.,(y-top)/free_y)) if free_y else 0.)


def anchored_position(metrics,*,width,height,anchor,decorated=False):
    if (not isinstance(anchor,(tuple,list)) or len(anchor)!=2
            or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in anchor)):
        raise ValueError('相对位置必须是两个0到1之间的有限数值。')
    left,top,right,bottom=(_client_metrics(metrics).work_area if decorated else metrics.work_area)
    return (left+round(max(0,right-left-round(width))*anchor[0]),
            top+round(max(0,bottom-top-round(height))*anchor[1]))


def main_layout(metrics,*,mode='normal',x=120,y=80,font_size=12,anchor=None):
    available=_client_metrics(metrics)
    compact=mode=='compact'
    width,height=(740,520) if compact else (1080,720)
    if anchor is not None:
        x,y=anchored_position(available,width=metrics.px(width),height=metrics.px(height),anchor=anchor)
    rect=bound_rect(available,width=metrics.px(width),height=metrics.px(height),x=x,y=y)
    return WindowLayout(rect.width,rect.height,rect.x,rect.y,
                        min(rect.width,metrics.px(540)),min(rect.height,metrics.px(380 if compact else 420)),
                        metrics.px(10),metrics.px(font_size*2+16),
                        compact or rect.height<metrics.px(650))


def overlay_layout(metrics,*,width=440,x=40,y=180,height=300,anchor=None):
    width=metrics.px(max(280,min(900,width)))
    if anchor is not None:x,y=anchored_position(metrics,width=width,height=height,anchor=anchor)
    return bound_rect(metrics,width=width,height=height,x=x,y=y)


def bottom_overlay_layout(metrics,*,width=420,height=1):
    """字幕水平居中，底边固定在工作区高度的86%，宽度保存逻辑像素。"""
    left,top,right,bottom=metrics.work_area
    width=min(metrics.px(max(280,min(900,width))),right-left)
    return bound_rect(metrics,width=width,height=height,
                      x=left+round((right-left-width)/2),
                      y=top+round((bottom-top)*.86)-height)


def quick_input_layout(metrics,*,font_size=12,height=None,position=None):
    available=_client_metrics(metrics)
    left,top,right,bottom=available.work_area
    width=min(metrics.px(640),right-left)
    height=height if height is not None else metrics.px(80+max(12,font_size)*3)
    if position is None:
        position=(left+round((right-left-width)/2),bottom-height-metrics.px(16))
    return bound_rect(available,width=width,height=height,x=position[0],y=position[1])
