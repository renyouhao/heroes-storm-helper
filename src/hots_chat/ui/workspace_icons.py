"""工作区的轻量线性图标；固定图片池随主题和 DPI 原地刷新。"""
import math
import struct
import tkinter as tk
import zlib

from .theme import theme_colors


_PATHS = {
    'chat': [[(3,4),(20,4),(20,17),(10,17),(5,21),(5,17),(3,17),(3,4)],[(7,9),(16,9)],[(7,13),(13,13)]],
    'phrases': [[(4,4),(20,4),(20,17),(10,17),(4,22),(4,4)]],
    'loot': [[(3,7),(12,2),(21,7),(21,18),(12,23),(3,18),(3,7),(12,12),(21,7)],[(12,12),(12,23)],[(12,2),(12,12)]],
    'repeat': [[(3,5),(21,5),(21,19),(3,19),(3,5)],[(7,9),(8,9)],[(11,9),(12,9)],[(16,9),(17,9)],[(7,14),(17,14)]],
    'talent': [[(12,3),(4,18),(20,18),(12,3)],[(8,12),(16,12)]],
    'broadcast': [[(3,9),(8,9),(15,4),(15,21),(8,16),(3,16),(3,9)],[(19,8),(21,11),(21,14),(19,17)]],
    'settings': [[(9,3),(15,3),(16,6),(20,7),(22,12),(20,17),(16,18),(15,21),(9,21),(8,18),(4,17),(2,12),(4,7),(8,6),(9,3)]],
    'update': [[(20,9),(19,5),(15,2),(10,2),(5,5),(3,10)],[(17,9),(21,9),(21,4)],[(4,15),(5,19),(9,22),(14,22),(19,19),(21,14)],[(7,15),(3,15),(3,20)]],
    'quick': [[(14,2),(5,13),(11,13),(9,23),(20,10),(13,10),(14,2)]],
    'copy': [[(8,7),(20,7),(20,22),(8,22),(8,7)],[(15,3),(4,3),(4,17)]],
    'translate': [[(3,6),(15,6)],[(9,2),(9,6)],[(5,7),(7,12),(13,17)],[(13,7),(10,12),(3,17)],[(13,22),(18,10),(23,22)],[(15,18),(21,18)]],
    'chevron': [[(9,5),(16,12),(9,19)]],
}
_CIRCLES = {'talent': [(12,3,3),(4,18,3),(20,18,3)], 'settings': [(12,12,4)]}


def _distance(x,y,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1]
    t=max(0,min(1,((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy or 1)))
    return math.hypot(x-a[0]-t*dx,y-a[1]-t*dy)


def _png(kind,size,color,accent):
    """在小型逻辑画布上抗锯齿绘制；不依赖系统图标字体。"""
    rgb=tuple(int(color[i:i+2],16) for i in (1,3,5))
    brand=tuple(int(accent[i:i+2],16) for i in (1,3,5))
    paths=_PATHS.get(kind,())
    circles=_CIRCLES.get(kind,())
    segments=[(a,b) for path in paths for a,b in zip(path,path[1:])]
    data=bytearray()
    for y in range(size):
        data.append(0)
        for x in range(size):
            px,py=(x+.5)*24/size,(y+.5)*24/size
            if kind=='brand':
                inside=abs(px-12)<10 and abs(py-12)<10.5-abs(px-12)*.48
                angle=math.atan2(py-12,px-12)
                radius=math.hypot(px-12,py-12)
                swirl=abs(radius-(4.3+1.2*math.sin(angle*3)))<1.2
                data.extend((*((255,255,255) if swirl else brand),255 if inside else 0))
                continue
            distance=min([_distance(px,py,a,b) for a,b in segments]+
                         [abs(math.hypot(px-cx,py-cy)-r) for cx,cy,r in circles]+[99])
            alpha=round(255*max(0,min(1,(1.05-distance)*size/24+.5)))
            data.extend((*rgb,alpha))
    def chunk(kind,content):
        return struct.pack('!I',len(content))+kind+content+struct.pack('!I',zlib.crc32(kind+content))
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',size,size,8,6,0,0,0))+
            chunk(b'IDAT',zlib.compress(data))+chunk(b'IEND',b''))


class WorkspaceIcons:
    def __init__(self,root,settings,scale=1):
        self.root=root
        self.images={}
        self.signature=None
        self.colors=theme_colors(settings)
        self.scale=scale
        root.bind('<Destroy>',self._destroy,add='+')

    def get(self,kind,tone='normal'):
        key=(kind,tone)
        if key not in self.images:
            self.images[key]=tk.PhotoImage(master=self.root,data=self._pixels(kind,tone),format='png')
        return self.images[key]

    def _pixels(self,kind,tone):
        c=self.colors
        color=c['accent'] if tone=='selected' or kind=='quick' else c['sidebar_text']
        if tone=='primary':color=c['on_accent'] if c is theme_colors('lavender') else c['surface']
        size=max(12,round((30 if kind=='brand' else 22)*self.scale))
        return _png(kind,size,color,c['accent'])

    def refresh(self,settings,scale):
        colors=theme_colors(settings)
        signature=(id(colors),scale)
        if signature==self.signature:return
        self.signature=signature
        self.colors,self.scale=colors,scale
        for (kind,tone),image in self.images.items():
            image.configure(data=self._pixels(kind,tone),format='png')

    def _destroy(self,event):
        if event.widget is not self.root:return
        for image in self.images.values():
            try:image.tk.call('image','delete',image.name)
            except tk.TclError:pass
            image.name=None
            image.tk=None
        self.images.clear()
        self.root=None
