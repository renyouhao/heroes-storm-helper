"""测量用户已启动的助手 PID；不启动、识别或操作界面。"""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import time


def summarize(samples,cores):
    wall=samples[-1][0]-samples[0][0]
    cpu=max(0,samples[-1][1]-samples[0][1])
    return dict(wall_seconds=wall,cpu_percent_all_cores=cpu/wall/max(1,cores)*100 if wall else None,
                peak_working_set_mib=max(v[2] for v in samples),logical_cores=cores,
                ten_minute_window=wall>=599,scope='指定 PID；由用户另行确认空闲／普通消息场景')


class ProcessSampler:
    def __init__(self,pid):
        self.k=ctypes.WinDLL('kernel32',use_last_error=True);self.p=ctypes.WinDLL('psapi',use_last_error=True)
        self.k.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];self.k.OpenProcess.restype=wintypes.HANDLE
        self.k.CloseHandle.argtypes=[wintypes.HANDLE]
        self.k.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
        self.p.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
        self.handle=self.k.OpenProcess(0x410,False,pid)
        if not self.handle:raise OSError('无法访问指定 PID。')
    def sample(self):
        class Counters(ctypes.Structure):
            _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD),('peak',ctypes.c_size_t),('working',ctypes.c_size_t),
                      ('paged_peak',ctypes.c_size_t),('paged',ctypes.c_size_t),('nonpaged_peak',ctypes.c_size_t),('nonpaged',ctypes.c_size_t),('page',ctypes.c_size_t),('page_peak',ctypes.c_size_t)]
        times=[wintypes.FILETIME() for _ in range(4)];c=Counters();c.cb=ctypes.sizeof(c)
        if not self.k.GetProcessTimes(self.handle,*[ctypes.byref(t) for t in times]) or not self.p.GetProcessMemoryInfo(self.handle,ctypes.byref(c),c.cb):raise OSError('进程已退出或采样失败。')
        cpu=sum((t.dwHighDateTime<<32)+t.dwLowDateTime for t in times[2:])/10000000
        return time.monotonic(),cpu,c.working/1048576
    def close(self):
        if self.handle:self.k.CloseHandle(self.handle);self.handle=None


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--pid',type=int,required=True);p.add_argument('--seconds',type=int,default=600)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if args.seconds<1:p.error('seconds 至少 1')
    sampler=ProcessSampler(args.pid);samples=[]
    try:
        started=time.monotonic()
        while True:
            samples.append(sampler.sample())
            if time.monotonic()-started>=args.seconds:break
            time.sleep(min(1,args.seconds-(time.monotonic()-started)))
    finally:sampler.close()
    result=summarize(samples,os.cpu_count() or 1)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True))


if __name__=='__main__':main()
