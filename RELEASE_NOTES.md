## v0.3.1

### New Features

- **Pad coloring schemes**: Added a "Colors" dropdown in the toolbar with three options:
  - **Scale** (default): The existing root / on-scale / off-scale coloring
  - **Rainbow**: Color by accidental count — accidental-free scale degrees stay white, positive accidentals walk toward green/blue/violet, negative accidentals toward yellow/orange/red
  - **Harmony**: Live harmony-based coloring driven by the synth spectrum forwarded from the PitchGrid plugin. Hue is derived from cents-mod-equave; lightness tracks Plomp-Levelt consonance of each pad's interval against the root. True root/equave multiples render white with brightness from consonance, so the tonic stays visible even as harmony shifts
- **Spectrum-driven live coloring**: Harmony colors update in real time as the plugin forwards spectrum changes (`/pitchgrid/plugin/spectrum`), with throttled pushes to physical controllers to keep MIDI traffic bounded
- **Per-controller scheme persistence**: The selected coloring scheme is remembered per controller; palette-only devices (LinnStrument) are restricted to Scale since Rainbow/Harmony need continuous RGB

### Improvements

- Harmony coloring uses the **live tuning MOS** (from `/pitchgrid/plugin/tuning`) rather than the mapping MOS, so colors reflect audible pitch even when the plugin's mapping is locked
- Accidental unisons (pads at scale degree 0 with non-zero accidental) correctly show their harmony hue instead of being flattened to white

## v0.3.0

### New Features

- **Plugin note highlighting**: Notes played through the PitchGrid plugin (via cantus/external sources) now light up corresponding pads in both the web UI and on physical controllers that support `SetPadColor`
- **OSC connection hint popup**: Shows setup instructions when the plugin connection is not established
- **Dynamic per-controller UI options**: Controllers can define custom toggles and settings that appear in the app UI (e.g., Lumatone sustain pedal invert)
- **ACK-based MIDI messaging**: Reliable SysEx communication for controllers like Lumatone that require acknowledgment before sending the next message
- **EDO-compatible enharmonic mapping**: Isomorphic layouts now correctly handle enharmonic equivalents for EDO-compatible scales, with darkened off-scale colors
- **Lumatone channel-based reverse mapping**: Proper per-board channel routing and SysEx color format for Lumatone

### Improvements

- Updated OSC protocol: heartbeat now includes port number, enabling reliable bidirectional plugin communication
- Layout uses mapping-based scale generation (`generateMappedScale`) for accurate MIDI assignment
- Ephemeral ports in dev mode to avoid port conflicts when running multiple instances
- Unmapped pads in piano-like layout now show as black instead of gray
- Optimized note-off behavior on mapping change: only affected notes are stopped
- Comprehensive MIDI diagnostics and auto-detection of ACK response position
- Playing note highlights are cleared before layout recalculation to prevent stale pad states

### Packaging & Build

- Combined release scripts for macOS (Apple Silicon + Intel) and Windows
- Intel Mac (x86_64) cross-compilation support
- Scalatrix dependency now tracks `@main` branch with auto-upgrade on dev start

### Fixes

- Fixed LinnStrument channel lookup for correct note routing
- Fixed Lumatone SysEx addressing and message timing
- Fixed ACK response position detection for reliable Lumatone communication
