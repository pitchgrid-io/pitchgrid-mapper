# key_recorder

Standalone Rust binary that records per-key analog travel from a Wooting
keyboard for offline analysis. Decoupled from the PyO3 bridge so it can
run without the Python app.

## Build & run

```bash
cd tools/key_recorder
cargo build --release

# Default: reads SDK from /usr/local/lib/libwooting_analog_sdk.dylib,
# writes CSVs to current dir.
./target/release/key_recorder --output-dir /tmp/wooting-traces
```

**The recorder needs exclusive access to the keyboard.** Stop these
*before* running it:

- pitchgrid-mapper's dev server (`./run_dev.sh`)
- Wootility (the desktop app)
- Any other process that previously initialized the Wooting Analog SDK

If the recorder prints `0 device(s) connected` at startup, those CSVs
will never appear — kill the conflicting process and re-run. The
heartbeat line repeats this warning every 2 s while no device is visible.

```bash
# Override SDK location:
WOOTING_ANALOG_SDK_PATH=/path/to/libwooting_analog_sdk.dylib \
    ./target/release/key_recorder -o /tmp/wooting-traces
```

The recorder polls at ~8 kHz and watches every key. Each time depth on a
key crosses **0.5 upward** (from below 0.5), it captures a 1-second window:

- **1000 samples (~125 ms) before** the trigger
- **7000 samples (~875 ms) at and after** the trigger

…and writes one CSV per press to the output directory.

## Filename format

```
key_0xNN_YYYYMMDDTHHMMSSmmm.csv
```

- `0xNN` — HID Keyboard Usage code (e.g. `0x1D` for Z).
- `YYYYMMDDTHHMMSSmmm` — UTC timestamp at capture, sortable.

## CSV format

```csv
sample_idx,t_us_relative_to_trigger,depth
0,-124875,0.000000
1,-124750,0.000000
...
1000,0,0.501234        <- trigger sample
...
7999,874875,0.041000
```

- `sample_idx` 0..7999 (the trigger sample is always at index 1000).
- `t_us_relative_to_trigger` — microseconds relative to the trigger sample.
- `depth` — 0.0 to 1.0.

## Notes

- The recorder synthesises depth=0 samples for keys that disappear from
  the SDK's `read_full_buffer` output (the SDK omits fully-released keys),
  so the post-trigger window completes even after you let go.
- Multiple simultaneous presses are recorded independently — each key has
  its own ring buffer and trigger state.
- A second press of the same key only fires after the first capture window
  completes; presses that happen during an active capture are ignored.
- The recorder does not write the SDK shutdown gracefully on Ctrl-C; the
  process just exits. Fine for an analysis tool.

## Analysis

The accompanying [analysis.ipynb](analysis.ipynb) loads all CSVs from a
directory into pandas DataFrames and provides:

- Single-trace plots (depth vs. time around the trigger).
- Overlays of multiple presses for one key.
- Pre-trigger noise stats (mean / std / p95 / p99).
- T_arm → T_trigger dt distribution (the metric our MPE velocity FSM uses).

Run the recorder, capture some presses, then open the notebook.
