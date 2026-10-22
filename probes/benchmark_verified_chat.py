"""Measure bounded reads of two already verified game chat records; no scanning."""
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import statistics
import time

from chat_memory_probe import ReadOnlyGame, decode_chat_record


class MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
            "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = json.loads(args.records.read_text(encoding="utf-8"))
    start = int(records[0]["record_address"], 16)
    end = int(records[-1]["record_address"], 16) + records[-1]["decoded"]["consumed_bytes"]
    if not 0 < end-start <= 4096:
        raise RuntimeError("Verified record span exceeds a bounded read")
    with ReadOnlyGame(args.pid) as game:
        def poll():
            data = game.read(start, end-start)
            for record in records:
                offset = int(record["record_address"], 16)-start
                if decode_chat_record(data[offset:]) != record["decoded"]:
                    raise RuntimeError("Verified records changed; benchmark stopped")
        for _ in range(100):
            poll()
        batch_wall=time.perf_counter(); batch_cpu=time.process_time()
        for _ in range(5000):
            poll()
        batch_wall=time.perf_counter()-batch_wall; batch_cpu=time.process_time()-batch_cpu
        samples=[]; cpu=time.process_time(); wall=time.perf_counter()
        for index in range(24):
            due=wall+index*0.5
            if due>time.perf_counter(): time.sleep(due-time.perf_counter())
            before=time.perf_counter(); poll(); samples.append((time.perf_counter()-before)*1000)
        if wall+12>time.perf_counter(): time.sleep(wall+12-time.perf_counter())
        elapsed=time.perf_counter()-wall; used_cpu=time.process_time()-cpu
        kernel=ctypes.WinDLL("kernel32"); kernel.GetCurrentProcess.restype=wintypes.HANDLE
        psapi=ctypes.WinDLL("psapi"); psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(MemoryCounters),wintypes.DWORD]
        counters=MemoryCounters(); counters.cb=ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(counters),counters.cb):
            raise ctypes.WinError()
        report={"kind":"verified_game_chat_bounded_read", "process_id":args.pid,
                "read_bytes_per_poll":end-start,"records_per_poll":len(records),"polls":24,"period_ms":500,
                "median_poll_ms":statistics.median(samples),"p95_poll_ms":sorted(samples)[22],
                "wall_seconds":elapsed,"process_cpu_seconds":used_cpu,
                "batch_iterations":5000,"batch_mean_poll_ms":batch_wall/5000*1000,"batch_mean_cpu_ms":batch_cpu/5000*1000,
                "host_working_set_mib":counters.WorkingSetSize/1024/1024,"host_private_mib":counters.PrivateUsage/1024/1024,
                "limitations":["Reads and decodes existing verified records, not an end-to-end new-message receiver.",
                               "Addresses are valid only for this game process/session.","Translation, user interface and initialization scans are excluded."]}
    args.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report),flush=True)


if __name__ == "__main__":
    main()
