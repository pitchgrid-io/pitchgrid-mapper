# MAD68HE testing scripts

Standalone helpers for the Madlions MAD68HE bridge. Run them from the repo
root (they expect `src/`, `controller_config/`, and `wooting_plugins/` on the
relative path), with the MAD68HE plugged in:

```bash
.venv/bin/python testing_scripts/<script>.py
```

`hidapi` is required for the raw-HID scripts: `uv pip install hidapi`.

| Script | What it does |
|---|---|
| `test_mad68_bridge.py` | End-to-end: builds the native bridge, prints NoteOn/Off + velocity while you play. Quick regression check for analog → MIDI. |
| `test_mad68_rgb.py` | Pushes per-key colours + a playing overlay through the bridge. Regression check for RGB + the (x,y)→slot map. |
| `mad68_map_rgb_slots.py` | Re-discovers the 80-slot RGB wire order by lighting each slot and reading which key the analog poll sees. Use if a board variant's slot order differs from `slot = idx + idx//15`. Writes `mad68_rgb_slots.json`. |
| `mad68_descriptors.py` | Dumps + parses the board's HID report descriptors (per interface). Useful to confirm capabilities / a new variant's layout. |
| `mad68_poll_bench.py` | Benchmarks the `0xFF60` analog-poll round-trip rate (polls/sec, implied full-scan Hz). |
