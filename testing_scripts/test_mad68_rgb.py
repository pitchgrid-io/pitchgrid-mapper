"""Test per-key RGB through the MAD68HE native bridge (set_pad_colors / overlay)."""
import colorsys, sys, time
from pathlib import Path
sys.path.insert(0, str(Path("src").resolve()))
from pg_isomap.controller_config import ControllerConfig
from pg_isomap.wooting import WootingBridge


class FakeMidi:
    def inject_message(self, data):
        pass


cfg = ControllerConfig(Path("controller_config/MAD68HE.yaml"))
slot_map = cfg.build_madlions_slot_map()      # (x,y) -> slot
print(f"{len(slot_map)} playable keys mapped", flush=True)

bridge = WootingBridge(FakeMidi(), cfg)
bridge.start()
time.sleep(0.3)

# 1) Column gradient: hue follows x, brightness follows row. A correct slot map
#    shows clean horizontal rainbows, one per physical row.
colors = []
xs = [x for (x, y) in slot_map]
xmin, xmax = min(xs), max(xs)
for (x, y) in slot_map:
    hue = (x - xmin) / max(1, (xmax - xmin))
    r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
    colors.append((x, y, int(r * 255), int(g * 255), int(b * 255)))
bridge.set_pad_colors(colors)
print("pushed column gradient — keyboard should show horizontal rainbows (5s)", flush=True)
time.sleep(5)

# 2) Scale-style: root (0,0) white, even x green, odd x dim blue.
colors2 = []
for (x, y) in slot_map:
    if (x, y) == (0, 0):
        colors2.append((x, y, 255, 255, 255))
    elif x % 2 == 0:
        colors2.append((x, y, 0, 180, 60))
    else:
        colors2.append((x, y, 10, 10, 60))
bridge.set_pad_colors(colors2)
print("pushed scale-style colors — root (0,0) white (5s)", flush=True)
time.sleep(5)

# 3) Overlay test: flash a 'playing' highlight on (0,0).
print("overlay: (0,0) flashes red 3x", flush=True)
for _ in range(3):
    bridge.set_pad_overlay(0, 0, 255, 0, 0)
    time.sleep(0.4)
    bridge.clear_pad_overlay(0, 0)
    time.sleep(0.4)

bridge.stop()
print("done", flush=True)
