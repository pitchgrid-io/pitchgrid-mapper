"""End-to-end test of the MAD68HE native bridge path (no web app).

Loads MAD68HE.yaml, builds the WootingBridge (Madlions mode), starts it, and
prints NoteOn/NoteOff drained from the bridge while you play.
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path("src").resolve()))

from pg_isomap.controller_config import ControllerConfig
from pg_isomap.wooting import WootingBridge


class FakeMidi:
    """Minimal stand-in for MIDIHandler.inject_message."""
    def inject_message(self, data):
        if len(data) >= 3:
            status, d1, d2 = data[0] & 0xF0, data[1], data[2]
            ch = (data[0] & 0x0F) + 1
            if status == 0x90 and d2 > 0:
                print(f"  NoteOn  ch{ch:<2} note={d1:<3} vel={d2}", flush=True)
            elif status == 0x80 or (status == 0x90 and d2 == 0):
                print(f"  NoteOff ch{ch:<2} note={d1}", flush=True)


cfg = ControllerConfig(Path("controller_config/MAD68HE.yaml"))
print("is_madlions:", cfg.is_madlions(), "pid:", hex(cfg.madlions_product_id), flush=True)
idx_kc = cfg.build_madlions_index_keycode()
print(f"index->keycode entries: {len(idx_kc)}", flush=True)

bridge = WootingBridge(FakeMidi(), cfg)
bridge.start()
print("bridge started; status:", bridge.status().get("connected"), flush=True)
print(">>> Play the MAD68 for 20s <<<", flush=True)
try:
    time.sleep(20)
finally:
    bridge.stop()
    print("stopped", flush=True)
