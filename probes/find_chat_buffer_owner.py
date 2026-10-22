"""Temporary pointer-reference probe for the already verified chat event buffer."""
import argparse
import json
from pathlib import Path
import struct
import time

from chat_memory_probe import ReadOnlyGame, StreamingMatcher, decode_chat_record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--markers", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = json.loads(args.markers.read_text(encoding="utf-8"))
    first_address = int(records[0]["record_address"], 16)
    with ReadOnlyGame(args.pid) as game:
        for record in records:
            expected = record["decoded"]
            current = decode_chat_record(game.read(int(record["record_address"], 16), expected["consumed_bytes"]))
            if current != expected:
                raise RuntimeError("Known chat records no longer match; stop instead of scanning stale targets")
        regions = list(game.regions())
        buffer_region = next((r for r in regions if r[0] <= first_address < r[0] + r[1]), None)
        if not buffer_region:
            raise RuntimeError("Verified message buffer is no longer mapped")
        buffer_base = buffer_region[0]
        candidates = {"buffer_base": buffer_base, "buffer_plus_16": buffer_base + 16,
                      "buffer_plus_32": buffer_base + 32}
        matchers = {name: StreamingMatcher(struct.pack("<Q", address)) for name, address in candidates.items()}
        references = []
        started = time.perf_counter()
        cpu = time.process_time()
        scanned = 0
        for base, size, protection in regions:
            if time.perf_counter() - started > 90:
                break
            for offset in range(0, size, 1024 * 1024):
                data = game.read(base + offset, min(1024 * 1024, size - offset))
                scanned += len(data)
                for name, matcher in matchers.items():
                    for address in matcher.feed(base + offset, data):
                        if address % 8:
                            continue
                        # Save a tiny window of pointer/counter values, never a memory dump.
                        start = max(base, address - 24)
                        values = game.read(start, min(88, base + size - start))
                        qwords = [hex(value[0]) for value in struct.iter_unpack("<Q", values[:len(values)//8*8])]
                        entry = {"target": name, "reference_address": hex(address), "window_address": hex(start), "pointer_counter_values": qwords}
                        references.append(entry)
                        print(json.dumps(entry), flush=True)
                time.sleep(0.004 * len(data) / (1024 * 1024))
        report = {"process_id": args.pid, "buffer_base": hex(buffer_base), "buffer_region_size": buffer_region[1],
                  "known_end_address": hex(int(records[-1]["record_address"], 16) + records[-1]["decoded"]["consumed_bytes"]),
                  "scanned_mib": round(scanned / 1024 / 1024, 2), "wall_seconds": round(time.perf_counter()-started,3),
                  "process_cpu_seconds": round(time.process_time()-cpu,3), "references": references,
                  "scope": "Numeric references to the validated chat buffer only; no object layout asserted."}
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(args.output), "references": len(references)}), flush=True)


if __name__ == "__main__":
    main()
