"""Record velocity fire-diagnostics while you play (fixed 10 s run).

For every NoteOn the bridge exports what the velocity was actually computed
from: number of rise samples, first/last sample depth, sample span, and
whether the note fired at the trigger crossing or the detection-window
deadline. Depths are shown as raw travel (0..350, 0.01 mm units).
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path("src").resolve()))

from pg_isomap.controller_config import ControllerConfig
from pg_isomap.wooting import WootingBridge

RUN_SECONDS = 10.0


class FakeMidi:
    def inject_message(self, data):
        pass


cfg = ControllerConfig(Path("controller_config/MAD68HE.yaml"))
bridge = WootingBridge(FakeMidi(), cfg)
bridge.start()
time.sleep(0.8)
st = bridge.status()
print(f"connected={st['connected']} nkro={st.get('madlions_nkro')}", flush=True)
bridge._native_bridge.drain_velocity_stats()  # discard anything stale

print(f"\n>>> GO — play for {RUN_SECONDS:.0f}s <<<\n", flush=True)
print(f"{'note':>4} {'vel':>4} {'n':>3} {'first':>6} {'last':>6} {'span':>7} {'rate':>7}  fired-by", flush=True)

rows = []
t_end = time.time() + RUN_SECONDS
while time.time() < t_end:
    for s in bridge._native_bridge.drain_velocity_stats():
        rows.append(s)
        rate = (s["n_samples"] - 1) / (s["span_ms"] / 1000.0) if s["span_ms"] > 0 else 0
        print(f"{s['note']:>4} {s['velocity']:>4} {s['n_samples']:>3} "
              f"{s['first_depth']*350:>6.0f} {s['last_depth']*350:>6.0f} "
              f"{s['span_ms']:>6.1f}ms {rate:>5.0f}Hz  "
              f"{'window' if s['window_fired'] else 'trigger'}", flush=True)
    time.sleep(0.02)

bridge.stop()

if rows:
    ns = [r["n_samples"] for r in rows]
    spans = [r["span_ms"] for r in rows]
    n_window = sum(1 for r in rows if r["window_fired"])
    print(f"\n=== summary over {len(rows)} presses ===", flush=True)
    print(f"  samples/press: min={min(ns)} max={max(ns)} mean={sum(ns)/len(ns):.1f}", flush=True)
    print(f"  span:          min={min(spans):.1f}ms max={max(spans):.1f}ms "
          f"mean={sum(spans)/len(spans):.1f}ms", flush=True)
    print(f"  fired by:      trigger={len(rows)-n_window}  window={n_window}", flush=True)
else:
    print("\n(no presses recorded)", flush=True)
