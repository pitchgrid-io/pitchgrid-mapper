"""Does the MAD68HE 0xFF60 travel poll support pipelining?

Serialized polling is send->read->send->read (one round-trip each). Pipelining
sends N requests back-to-back, then reads N responses. If the firmware queues
requests and answers all N in order, we can sample many keys per USB window
instead of paying a full 0.6 ms round-trip per chunk — decisive for dense
velocity sampling of large chords.

Prints: serialized vs pipelined cycle time, how many responses come back, and
whether each response's echoed offset matches the request order.
"""
import sys, time
import hid

VID, PID = 0x373B, 0x1058
CHUNKS_16 = [0, 16, 32, 48]          # 4 chunks = 16 keys
CHUNKS_8 = [0, 8, 16, 24, 32, 40, 48, 56]
ITERS = 300


def find_path():
    for d in hid.enumerate(VID, PID):
        if d.get("usage_page") == 0xFF60:
            return d["path"]


def build(offset):
    r = bytearray(33)
    r[1] = 0x02; r[2] = 0x96; r[3] = 0x1C; r[7] = offset; r[8] = 4
    return bytes(r)


def drain(h):
    while h.read(64, 0):
        pass


def serialized(h, offsets):
    drain(h)
    t0 = time.perf_counter()
    for _ in range(ITERS):
        for off in offsets:
            h.write(build(off))
            h.read(64, 20)
    return (time.perf_counter() - t0) / ITERS


def pipelined(h, offsets):
    drain(h)
    ordered_ok = 0
    total = 0
    resp_counts = []
    sample_seq = None
    t0 = time.perf_counter()
    for it in range(ITERS):
        for off in offsets:
            h.write(build(off))            # send all, no reads yet
        resps = []
        for _ in range(len(offsets)):
            r = h.read(64, 20)
            if r:
                resps.append(bytes(r))
        resp_counts.append(len(resps))
        seq = [r[6] if len(r) > 6 else None for r in resps]
        if it == 0:
            sample_seq = seq
        for i, off in enumerate(offsets):
            total += 1
            if i < len(resps) and len(resps[i]) > 6 and resps[i][6] == off:
                ordered_ok += 1
        drain(h)
    dt = (time.perf_counter() - t0) / ITERS
    return dt, ordered_ok / total, sum(resp_counts) / len(resp_counts), sample_seq


def main():
    h = hid.device(); h.open_path(find_path()); h.set_nonblocking(0)
    # warmup
    for off in CHUNKS_16:
        h.write(build(off)); h.read(64, 20)

    for name, offs in (("16 keys / 4 chunks", CHUNKS_16), ("32 keys / 8 chunks", CHUNKS_8)):
        ser = serialized(h, offs)
        pipe, order_match, avg_resp, seq = pipelined(h, offs)
        print(f"\n== {name} (requested offsets {offs}) ==")
        print(f"  serialized cycle : {ser*1000:6.2f} ms  ({len(offs)} round-trips)")
        print(f"  pipelined  cycle : {pipe*1000:6.2f} ms")
        print(f"  speedup          : {ser/pipe:5.2f}x")
        print(f"  avg responses    : {avg_resp:.2f} / {len(offs)} requested")
        print(f"  in-order offset match: {order_match*100:.1f}%")
        print(f"  first-iter offsets received: {seq}")
    h.close()


if __name__ == "__main__":
    sys.exit(main())
