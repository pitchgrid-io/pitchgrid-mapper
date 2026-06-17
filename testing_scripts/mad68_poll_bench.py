"""Benchmark the MAD68HE analog poll rate over 0xFF60.

Measures the request->response round-trip so we know:
  - single poll rate (one poll covers up to 4 keys)
  - implied full-layout scan rate (all keys)
This decides whether we can continuously poll every key (catch presses from
travel~0) or must use the digital-NKRO hint to poll only active keys.
"""
import sys, time
import hid

VID, PID = 0x373B, 0x1058


def find_path():
    for d in hid.enumerate(VID, PID):
        if d.get("usage_page") == 0xFF60:
            return d["path"]
    return None


def build_poll(offset, nkeys=4):
    r = bytearray(33)
    r[1] = 0x02; r[2] = 0x96; r[3] = 0x1C
    r[7] = offset & 0xFF; r[8] = nkeys & 0xFF
    return bytes(r)


def bench(h, n, nkeys=4):
    ok = 0
    t0 = time.perf_counter()
    for k in range(n):
        # rotate offset so we exercise different chunks
        h.write(build_poll((k % 17) * 4, nkeys))
        resp = h.read(64, 50)
        if resp and len(resp) >= 12:
            ok += 1
    dt = time.perf_counter() - t0
    return ok, dt


def main():
    path = find_path()
    if not path:
        print("0xFF60 not found"); return 1
    h = hid.device(); h.open_path(path); h.set_nonblocking(0)
    try:
        # warmup
        bench(h, 50)
        for nkeys in (4, 5):
            ok, dt = bench(h, 1000, nkeys)
            rate = ok / dt if dt else 0
            keys_per_poll = nkeys
            full_scan_polls = (68 + keys_per_poll - 1) // keys_per_poll
            print(f"nkeys/poll={nkeys}: {ok}/1000 ok, {dt*1000/max(ok,1):.2f} ms/poll, "
                  f"{rate:.0f} polls/s  ->  full 68-key scan ~{rate/full_scan_polls:.0f} Hz "
                  f"({full_scan_polls} polls/scan)", flush=True)
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
