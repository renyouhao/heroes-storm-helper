"""用屏幕外、离线的合成聊天比较绘制耗时与桌面资源；不读取游戏。"""
import argparse
from collections import OrderedDict
import ctypes
from ctypes import wintypes
import gc
import json
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

from hots_chat.models import ChatEvent
from hots_chat.ui.chat_feed import ChatFeed
from hots_chat.ui.theme import apply_theme
from hots_chat.ui.viewmodel import MessageView
from hots_chat.window_settings import WindowSettings


def resources(root):
    class Memory(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[
            (name,ctypes.c_size_t) for name in ('peak_ws','ws','peak_paged','paged',
                'peak_nonpaged','nonpaged','pagefile','peak_pagefile','private')]
    kernel=ctypes.WinDLL('kernel32')
    kernel.GetCurrentProcess.restype=wintypes.HANDLE
    handle=kernel.GetCurrentProcess()
    memory=Memory()
    memory.cb=ctypes.sizeof(memory)
    psapi=ctypes.WinDLL('psapi')
    psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Memory),wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(handle,ctypes.byref(memory),memory.cb):
        raise ctypes.WinError()
    handles=wintypes.DWORD()
    kernel.GetProcessHandleCount.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessHandleCount(handle,ctypes.byref(handles))
    user=ctypes.WinDLL('user32')
    user.GetGuiResources.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    return dict(private_mib=round(memory.private/1048576,3),working_set_mib=round(memory.ws/1048576,3),
                handles=handles.value,gdi=user.GetGuiResources(handle,0),user=user.GetGuiResources(handle,1),
                threads=len(threading.enumerate()),tk_images=len(root.tk.call('image','names')),
                tk_timers=len(root.tk.call('after','info')))


def baseline_class(ref):
    source=subprocess.check_output(['git','show',ref+':src/hots_chat/ui/chat_feed.py'],
                                   cwd=Path(__file__).resolve().parents[1]).decode('utf-8')
    namespace={'__name__':'hots_chat.ui._baseline_chat_feed','__package__':'hots_chat.ui'}
    exec(compile(source,'<baseline ChatFeed>','exec'),namespace)
    return namespace['ChatFeed']


def measure(feed_class,iterations):
    root=tk.Tk()
    root.withdraw()
    root.geometry('800x500+10000+10000')
    root.tk.call('tk','scaling',96/72)
    try:
        settings=WindowSettings()
        apply_theme(root,settings)
        font=tkfont.Font(root=root,family='Microsoft YaHei UI',size=12)
        feed=feed_class(root,font=font,settings=settings)
        feed.pack(fill='both',expand=True)
        def row(number):
            return MessageView(ChatEvent(str(number),1,'团队','合成队友','00:01',
                                        f'synthetic message {number}: group up before the next fight.'))
        messages=OrderedDict((str(i),row(i)) for i in range(500))
        feed.sync(messages,1)
        root.deiconify()
        root.update()
        feed.yview_moveto(1)
        root.update_idletasks()
        before=resources(root)
        samples={'append_at_500':[],'translate_at_500':[],'unchanged_sync':[]}
        cpu_start=time.process_time()
        for i in range(iterations):
            messages.popitem(last=False)
            messages[str(500+i)]=row(500+i)
            start=time.perf_counter()
            feed.sync(messages,1)
            root.update_idletasks()
            samples['append_at_500'].append((time.perf_counter()-start)*1000)
            messages[str(500+i)].engine_translation='请先集合，等大家到齐后再进攻。'
            start=time.perf_counter()
            feed.sync(messages,1)
            root.update_idletasks()
            samples['translate_at_500'].append((time.perf_counter()-start)*1000)
            start=time.perf_counter()
            feed.sync(messages,1)
            root.update_idletasks()
            samples['unchanged_sync'].append((time.perf_counter()-start)*1000)
        cpu_ms=round((time.process_time()-cpu_start)*1000,3)
        for _ in range(8):
            for theme in ('moonlight','mint','graphite'):
                apply_theme(root,theme)
                feed.apply_settings(WindowSettings(color_theme=theme))
                root.update_idletasks()
        gc.collect()
        after=resources(root)
        return dict(iterations=iterations,median_ms={key:round(statistics.median(value),3)
                    for key,value in samples.items()},cpu_ms=cpu_ms,
                    resources_before=before,resources_after=after,
                    retained_messages=len(feed.get_children()))
    finally:root.destroy()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compare-ref')
    parser.add_argument('--iterations',type=int,default=30)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not 1<=args.iterations<=1000:parser.error('iterations must be between 1 and 1000')
    result={'scope':'offline synthetic 500-row chat; 24 theme changes; no game or cloud calls'}
    if args.compare_ref:result['baseline']=measure(baseline_class(args.compare_ref),args.iterations)
    result['current']=measure(ChatFeed,args.iterations)
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:args.output.write_text(text,encoding='utf-8')
    print(text)
    return 0


if __name__=='__main__':sys.exit(main())
