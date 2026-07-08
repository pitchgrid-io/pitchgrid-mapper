"""Interactive verify of the NKRO-driven Madlions loop.

1. Reads the board's actuation array (baseline) with a raw hid handle.
2. Starts the bridge (which saves config, sets 0.1 mm actuation + RT off).
3. Prints MIDI events with a countdown while you play.
4. Stops the bridge (which restores config), then reads actuation again and
   reports whether it matches the baseline.
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path("src").resolve()))
import hid


def read_actuation_raw():
    """Read the 77-slot actuation array via a fresh hid handle."""
    path = None
    for d in hid.enumerate(0x373B, 0x1058):
        if d.get("usage_page") == 0xFF60:
            path = d["path"]
    if not path:
        return None
    h = hid.device(); h.open_path(path); h.set_nonblocking(0)
    arr = [0] * 77
    try:
        for start in (0x00, 0x0C, 0x18, 0x24, 0x30, 0x3C):
            for _ in range(100):
                if not h.read(64, 1):
                    break
            req = bytearray(33)
            req[1] = 0x02; req[2] = 0x96; req[3] = 0x0D; req[7] = start; req[8] = 0x0C
            h.write(bytes(req))
            for _ in range(6):
                r = h.read(64, 50)
                if r and r[0] == 0x02 and r[1] == 0x96 and r[2] == 0x0D:
                    rs, count = r[6], r[7]
                    for k in range(count):
                        b = 8 + k * 2
                        if rs + k < 77:
                            arr[rs + k] = (r[b] << 8) | r[b + 1]
                    break
            else:
                return None
    finally:
        h.close()
    return arr


def summarize(arr):
    if arr is None:
        return "READ FAILED"
    real = arr[:70]
    return f"min={min(real)} max={max(real)} (raw 0.01mm; 10=0.1mm, 354=default)"


print("=== baseline actuation ===", flush=True)
base = read_actuation_raw()
print(" ", summarize(base), flush=True)

from pg_isomap.controller_config import ControllerConfig
from pg_isomap.wooting import WootingBridge


class FakeMidi:
    def inject_message(self, data):
        if len(data) >= 3:
            s, d1, d2 = data[0] & 0xF0, data[1], data[2]
            ch = (data[0] & 0x0F) + 1
            if s == 0x90 and d2 > 0:
                print(f"  NoteOn  ch{ch:<2} note={d1:<3} vel={d2}", flush=True)
            elif s == 0x80 or (s == 0x90 and d2 == 0):
                print(f"  NoteOff ch{ch:<2} note={d1}", flush=True)
            elif s == 0xB0 and d1 == 0x40:
                print(f"  Sustain {'ON' if d2 else 'off'} (spacebar)", flush=True)


cfg = ControllerConfig(Path("controller_config/MAD68HE.yaml"))
bridge = WootingBridge(FakeMidi(), cfg)
bridge.start()
time.sleep(1.0)
st = bridge.status()
print(f"bridge: connected={st['connected']} nkro={st.get('madlions_nkro')}", flush=True)

print("\n>>> GO — play now! (soft + hard strikes, chords, spacebar) <<<", flush=True)
t_end = time.time() + 25
last = 0
while time.time() < t_end:
    remaining = int(t_end - time.time())
    if remaining != last:
        last = remaining
        if remaining % 5 == 0:
            print(f"  ...{remaining}s left", flush=True)
    time.sleep(0.05)

bridge.stop()
print("bridge stopped (config restore ran)", flush=True)
time.sleep(0.5)

print("\n=== actuation after stop ===", flush=True)
after = read_actuation_raw()
print(" ", summarize(after), flush=True)
if base is not None and after is not None:
    print("  restore " + ("OK — matches baseline" if base == after
                          else f"MISMATCH: {sum(1 for a,b in zip(base,after) if a!=b)} slots differ"),
          flush=True)
