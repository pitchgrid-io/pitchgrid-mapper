"""Discover the MAD68HE 80-slot RGB wire order by analog correlation.

For each RGB slot, light ONLY that slot white, then read analog travel: the key
the user presses identifies which physical key that slot is. Both RGB and the
travel poll ride the same 0xFF60 handle, so there's no second device to map
against. Output: testing_scripts/mad68_rgb_slots.json  { "(x,y)": slot, ... } plus a
slot->label table for review.
"""
import json, sys, time
from pathlib import Path
import hid

sys.path.insert(0, str(Path("src").resolve()))
from pg_isomap.controller_config import (
    ControllerConfig, HID_USAGE_BY_LABEL, SOUP_MAD68HE_LAYOUT,
)

VID, PID = 0x373B, 0x1058
PRESS_RAW = 220          # travel that counts as a firm, deliberate press
SLOT_TIMEOUT = 2.5
OUT = Path("testing_scripts/mad68_rgb_slots.json")

cfg = ControllerConfig(Path("controller_config/MAD68HE.yaml"))
hid_to_xy = cfg.derive_wooting_hid_to_xy()           # HID keycode -> (x,y)
# analog index -> (label, (x,y)) for playable keys
index_xy, index_label = {}, {}
for idx, label in enumerate(SOUP_MAD68HE_LAYOUT):
    if label is None:
        continue
    hidc = HID_USAGE_BY_LABEL.get(label)
    if hidc is not None and hidc in hid_to_xy:
        index_xy[idx] = hid_to_xy[hidc]
        index_label[idx] = label

WATCH_CHUNKS = sorted({(i // 4) * 4 for i in index_xy})


def find_path():
    for d in hid.enumerate(VID, PID):
        if d.get("usage_page") == 0xFF60:
            return d["path"]


def light_slot(h, slot):
    frame = [(0, 0, 0)] * 80
    if slot is not None:
        frame[slot] = (255, 255, 255)
    idx = 0
    for chunk in range(5):
        for sub in (0x00, 0x08):
            pkt = bytearray(33)
            pkt[1] = 0x07; pkt[2] = 0x42; pkt[3] = chunk; pkt[4] = sub; pkt[5] = 8
            for k in range(8):
                r, g, b = frame[idx]; pkt[6+k*3] = r; pkt[7+k*3] = g; pkt[8+k*3] = b; idx += 1
            h.write(bytes(pkt))
    commit = bytearray(33)
    commit[1] = 0x07; commit[2] = 0x41; commit[3] = 0x01
    commit[5] = 0x90; commit[6] = 0xFF; commit[8] = 0xEE; commit[9] = 0xD2
    h.write(bytes(commit))


def poll_max(h):
    """Return (index, travel) of the most-pressed watched key right now."""
    best = (None, 0)
    for off in WATCH_CHUNKS:
        while h.read(64, 1):
            pass
        r = bytearray(33); r[1] = 0x02; r[2] = 0x96; r[3] = 0x1C; r[7] = off; r[8] = 4
        h.write(bytes(r))
        resp = h.read(64, 20)
        if not resp or len(resp) < 27:
            continue
        for j in range(4):
            idx = off + j
            if idx not in index_xy:
                continue
            tv = (resp[10+j*5] << 8) | resp[11+j*5]
            if tv > best[1]:
                best = (idx, tv)
    return best


def main():
    h = hid.device(); h.open_path(find_path()); h.set_nonblocking(0)
    print(f"{len(index_xy)} playable keys; mapping 80 slots. Press each lit key; "
          f"wait ~{SLOT_TIMEOUT:.0f}s to skip blank slots.", flush=True)
    slot_to_index = {}
    used = set()
    try:
        for slot in range(80):
            light_slot(h, slot)
            time.sleep(0.05)
            deadline = time.time() + SLOT_TIMEOUT
            hit = None
            while time.time() < deadline:
                idx, tv = poll_max(h)
                if idx is not None and tv >= PRESS_RAW and idx not in used:
                    hit = idx
                    break
            if hit is not None:
                slot_to_index[slot] = hit
                used.add(hit)
                print(f"slot {slot:2d} -> {index_label[hit]!r} {index_xy[hit]}  "
                      f"({len(used)}/{len(index_xy)})", flush=True)
                # wait for release
                t = time.time()
                while time.time() - t < 1.0:
                    if poll_max(h)[1] < 40:
                        break
            else:
                print(f"slot {slot:2d} -> (skip)", flush=True)
        light_slot(h, None)
    finally:
        h.close()

    xy_to_slot = {f"{index_xy[i][0]},{index_xy[i][1]}": s for s, i in slot_to_index.items()}
    missing = sorted(set(index_label.values()) - {index_label[i] for i in slot_to_index.values()})
    OUT.write_text(json.dumps({
        "slot_to_label": {str(s): index_label[i] for s, i in slot_to_index.items()},
        "xy_to_slot": xy_to_slot,
        "missing": missing,
    }, indent=2))
    print(f"\nSaved {len(slot_to_index)} slots to {OUT}", flush=True)
    if missing:
        print(f"MISSING: {missing}", flush=True)


if __name__ == "__main__":
    main()
