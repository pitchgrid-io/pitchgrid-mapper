## v0.4.0

### New Features

- **Wooting analog keyboard support**: Wooting 60HE v2 (and the rest of the Wooting Analog family) work as fully expressive, velocity-sensitive MPE microtonal instruments
  - Hall-effect per-key analog depth (0.0–1.0 at firmware-controlled rates up to 8 kHz) drives both note velocity and post-attack expression
  - **MPE profile** (default): each note gets its own MPE member channel with continuous channel pressure from key depth, plus the standard pre-note reset triplet (CC74=0, channel pressure=0, pitch bend center) on every note-on
  - **Piano-key physics profile** (PianoSim): per-key hammer simulation (Hirschkorn 1-DOF model) — velocity comes from simulated hammer head speed at let-off, supports multi-strike, per-note CC64 sustain
  - Per-pad RGB lighting driven by the same color schemes as the rest of the app (Scale / Rainbow / Harmony)
  - Spacebar acts as a global sustain pedal; pads stay visually lit while sustain is held
  - Sensitivity slider for tuning velocity response per playing style
- **Native Rust bridge (`pg_wooting_bridge`)**: Polling and profile state machines run in a Rust thread without the Python GIL; Python only drains a lock-free MIDI queue. Statically linked Wooting Analog SDK — no separate dylib to install
- **Self-contained installer**: All Wooting dependencies (analog plugin and RGB SDK; `hidapi` is statically linked on Windows and vendored on macOS) ship inside the application bundle — `.app` on macOS, signed `.exe` installer on Windows. No `/usr/local` writes, no Homebrew, no Wootility prerequisite, no manual plugin install — a fresh user plugs in the keyboard and plays
- **Windows support**: Native Windows build (x86_64) joins the macOS arm64 build. Signed via Azure Trusted Signing, packaged with Inno Setup. Requires a virtual MIDI driver (loopMIDI or similar) — see README

### Improvements

- **Rainbow color scheme reworked**: Cleaner accidental-count gradient, with unmapped pads dimmed for better contrast against playable area
- **USB-presence detection**: Controllers are now matched against actually-connected USB devices (by VID/PID) rather than just by MIDI port name, so connection state in the UI reflects physical reality
- **Sustain pedal visuals**: Held pads remain lit while the sustain pedal is down — the visual highlight tracks audible note-off rather than key-up
- **Compact Wooting YAML**: Per-keyboard configs derive HID and RGB maps automatically from `fixedLabels` + the standard HID usage table — no per-key tables to hand-maintain
- **Lumatone relabelled `[experimental]`** (was `[untested]`): The Lumatone integration has been hardware-validated since v0.3.0 (ACK-based SysEx, channel-based reverse mapping, color SysEx format, dynamic UI options), so the stale "untested" tag was misleading

### Packaging & Build

- Release pipelines are now fully self-contained on both macOS and Windows: no system-wide dependencies, no manual plugin install
- Wooting analog SDK pulled from a `pitchgrid-io` fork pinned to a specific commit for long-term build reproducibility
- macOS ships arm64 only (vendored Wooting dylibs are arm64-only; x86_64 macOS pending). Windows ships x86_64
- `build_app.sh` (macOS) and `build_app.ps1` (Windows) check for the Rust toolchain and force-rebuild the bridge wheel without cache to guarantee the binary reflects the current source

### Fixes

- Fixed LED filter regression on Wooting after the RGB-map refactor
- Controller switching no longer double-initializes the Wooting bridge (a single `/api/controllers/switch` followed by `/api/controllers/connect` is now the unified flow for every controller type, matching MIDI-controller behavior)

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
