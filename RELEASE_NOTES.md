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
