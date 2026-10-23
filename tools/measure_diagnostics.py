"""在临时目录用合成事件对比日志开销；不连接游戏或云。"""
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import statistics
import tempfile
import threading
import time
import tracemalloc

from hots_chat.diagnostics import DiagnosticRecorder


def working_set():
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize','WorkingSetSize',
            'QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage',
            'QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
    try:
        process = ctypes.WinDLL('kernel32').GetCurrentProcess
        process.restype = wintypes.HANDLE
        function = ctypes.WinDLL('psapi').GetProcessMemoryInfo
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        return counters.WorkingSetSize if function(process(), ctypes.byref(counters), counters.cb) else None
    except (AttributeError, OSError):
        return None


def measure_case(base, mode, enabled, round_number):
    directory = base / f'{mode}-{enabled}-{round_number}'
    recorder = DiagnosticRecorder(directory,enabled=enabled)
    release = threading.Event()
    latencies = []
    before_memory = working_set()
    started, cpu = time.perf_counter(), time.process_time()
    try:
        if mode == 'slow_writer' and enabled:
            entered = threading.Event()
            original = recorder._write_line
            def slow(line):
                entered.set()
                release.wait(5)
                original(line)
            recorder._write_line = slow
            recorder.record('app.start')
            if not entered.wait(1):
                raise RuntimeError('Synthetic writer did not start')
        count = {'idle':0, 'normal':100, 'burst':3000, 'repeated_error':1000, 'slow_writer':500}[mode]
        if not count:
            time.sleep(.05)
        for index in range(count):
            began = time.perf_counter_ns()
            if recorder is not None:
                if mode == 'repeated_error':
                    recorder.record('capture.state', source='lobby', outcome='error', error_type='OSError')
                else:
                    recorder.record('translation.result', job_id=str(index), elapsed_ms=2.5, outcome='cloud')
            latencies.append((time.perf_counter_ns() - began) / 1000)
            if mode == 'normal':
                time.sleep(.002)
        enqueue_wall_ms = (time.perf_counter() - started) * 1000
        release.set()
        if enabled:
            recorder.export_report(base / f'{mode}-{round_number}.zip')
        cpu_ms = (time.process_time() - cpu) * 1000
        after_memory = working_set()
        values = sorted(latencies)
        logs = list(directory.glob('helper.log*')) if enabled else []
        return {'enqueue_wall_ms':enqueue_wall_ms, 'cpu_ms':cpu_ms,
                'enqueue_median_us':statistics.median(values) if values else 0,
                'enqueue_p95_us':values[min(len(values)-1,int(len(values)*.95))] if values else 0,
                'working_set_delta_bytes':after_memory-before_memory if after_memory is not None and before_memory is not None else None,
                'log_bytes':sum(path.stat().st_size for path in logs),
                'log_lines':sum(len(path.read_bytes().splitlines()) for path in logs),
                'dropped':recorder.snapshot()['dropped'] if recorder else 0}
    finally:
        release.set()
        if recorder:
            recorder.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(argv)
    cases = {}
    with tempfile.TemporaryDirectory(prefix='hots-diagnostic-benchmark-') as temporary:
        base = Path(temporary)
        for mode in ('idle','normal','burst','repeated_error','slow_writer'):
            rows = {False:[],True:[]}
            for number in range(5):
                for enabled in ((False,True) if number%2==0 else (True,False)):
                    rows[enabled].append(measure_case(base,mode,enabled,number))
            cases[mode] = {}
            for enabled, results in rows.items():
                cases[mode]['enabled' if enabled else 'disabled'] = {
                    key: statistics.median([row[key] for row in results if row[key] is not None])
                    if any(row[key] is not None for row in results) else None for key in results[0]}
                cases[mode]['enabled_rounds' if enabled else 'disabled_rounds'] = results
        retained=base/'retained'
        retained.mkdir()
        line=(json.dumps({'event':'message.received','original':'x'*3000})+'\n').encode()
        for suffix in ('','.1','.2','.3','.4'):
            (retained/('helper.log'+suffix)).write_bytes(line*600)
        recorder=DiagnosticRecorder(retained)
        tracemalloc.start()
        began=time.perf_counter()
        try:
            target=recorder.export_report(base/'retained.zip')
            _,peak=tracemalloc.get_traced_memory()
            retained_export={'input_bytes':len(line)*600*5,'python_peak_bytes':peak,
                             'wall_ms':(time.perf_counter()-began)*1000,'zip_bytes':target.stat().st_size}
        finally:
            tracemalloc.stop()
            recorder.close()
    passed = all(case['enabled']['enqueue_median_us'] < 100 and case['enabled']['enqueue_p95_us'] < 500
                 for case in cases.values())
    passed=passed and retained_export['python_peak_bytes']<3*1024*1024
    report = {'passed':passed,'rounds':5,'cases':cases,'retained_export':retained_export,
              'limits':{'median_us':100,'p95_us':500},
              'scope':'Synthetic events in temporary directories; CPU includes writer/export; working set is per-process delta, not a game FPS measurement.'}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':passed,'cases':{name:{'enabled':values['enabled'],'disabled':values['disabled']}
                     for name,values in cases.items()},'retained_export':retained_export},ensure_ascii=True))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
