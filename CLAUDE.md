# CLAUDE.md - Development Guidelines for PG Isomap

## Project Overview

This is PG Isomap (PitchGrid Isomorphic Controller Mapper), a Python application that maps microtonal scales from the PitchGrid VST plugin to various isomorphic MIDI controllers.

## Important Constraints

### Tech Stack
- **Backend**: Python 3.12 (REQUIRED - do not use 3.13 due to rtmidi issues)
- **Package Manager**: uv (not pip, not poetry)
- **Frontend**: Svelte 4 + Vite + TypeScript
- **Web Framework**: FastAPI with WebSocket support
- **MIDI**: python-rtmidi
- **OSC**: python-osc

### Related Projects
- **scalatrix** at `../scalatrix` - C++ library with Python bindings for scale theory. We depend on this.
- **pitchgrid-plugin** at `../pitchgrid-plugin` - The PitchGrid VST plugin source
- **pg_linn_companion** at `../pg_linn_companion` - Predecessor app for LinnStrument only. Reference for string-like layout.
- **PitchGridRack** at `../PitchGridRack` - Reference for Exquis fully-isomorphic layout
- **algos/mossy_keyboard_ui.py** at `../algos/mossy_keyboard_ui.py` - Reference for piano-like layout

## Architecture Principles

### Real-Time MIDI Processing
The core requirement is low-latency MIDI message passing:

1. **Dedicated MIDI Thread**: High-priority thread handles MIDI I/O
2. **Pre-computed Lookup Tables**: Layout changes update lookup table atomically, MIDI thread just does fast lookups
3. **Passthrough Design**: Non-note messages pass through unchanged
4. **Lock-Free Communication**: Use thread-safe queues, avoid locks in hot path

### Thread Architecture
```
Main Thread
├── MIDI Processing Thread (HIGH PRIORITY) - Real-time note remapping
├── OSC Server Thread - Receives scale updates from PitchGrid
├── Controller Discovery Thread - Scans every 3 seconds
└── Web Server Thread - FastAPI serving UI and WebSocket updates
```

### Critical Performance Path
```
Controller MIDI → Queue → MIDI Thread → Lookup (logical_coord → note) → Virtual MIDI Out
                                     ↑
                                     Pre-computed by layout calculator
                                     when scale updates arrive
```

## Module Organization

```
src/pg_isomap/
├── __init__.py
├── __main__.py           # Entry point
├── config.py             # Settings with pydantic-settings
├── app.py                # Main coordinator (PGIsomapApp)
├── web_api.py            # FastAPI routes and WebSocket
├── midi_handler.py       # MIDI I/O and threading
├── osc_handler.py        # OSC communication with PitchGrid
├── controller_config.py  # YAML config loader for controllers
└── layouts/
    ├── __init__.py
    ├── base.py           # Abstract base classes
    ├── isomorphic.py     # Fully isomorphic layout
    ├── string_like.py    # Guitar-like layout
    └── piano_like.py     # Mosaic/unfolded piano layout
```

## Key Implementation Notes

### Layout Calculators
All layout calculators inherit from `LayoutCalculator` and implement:
- `calculate_mapping(logical_coords, scale_degrees, scale_size) -> Dict[(x,y), note]`
- `get_unmapped_coords(logical_coords) -> List[(x,y)]`

**IMPORTANT**: These are currently placeholders! They need to be implemented with:
1. Proper scalatrix integration for scale theory
2. Reference implementations from pg_linn_companion, PitchGridRack, and mossy_keyboard_ui.py
3. Full transformation support (skew, rotate, flip, move)

### Controller Configurations
Each controller has a YAML file in `controller_config/` defining:
- Physical geometry (rows, columns, angles, spacing)
- MIDI device name for auto-discovery
- Optional: MIDI message templates for setting colors/notes
- Optional: Note-to-coordinate conversion functions

### Computer Keyboard Support
**TODO**: Implement virtual computer keyboard MIDI input:
- When computer keyboard layout is selected and app is in foreground
- Keyboard keys send MIDI messages through the virtual device
- This allows testing without a physical controller
- Use a keyboard event listener (may need platform-specific code or library)

### Currently Playing Notes Visualization
**TODO**: The UI should always highlight currently playing notes:
- Listen to MIDI messages going through the system
- Track note-on/note-off events
- Update UI in real-time via WebSocket
- Show which pads are currently pressed (regardless of layout type)

## Development Workflow

### Setup
```bash
# Install dependencies
uv sync

# Build frontend
cd frontend && npm install && npm run build && cd ..

# Run application
uv run python -m pg_isomap
```

### Development Mode
```bash
# Backend (one terminal)
PGISOMAP_DEBUG=true uv run python -m pg_isomap

# Frontend (another terminal)
cd frontend && npm run dev
```

### Testing
```bash
# Run tests
uv run pytest

# Type checking
cd frontend && npm run check
```

### Code Style
- **Python**: black (line length 100) + ruff
- **TypeScript/Svelte**: Prettier (via Vite)

## TODO: High Priority Items

### 1. Scalatrix Integration
The layout calculators currently use placeholder logic. They need to:
- Import and use scalatrix Python bindings
- Use proper scale structure (degrees, intervals, generators)
- Handle different scale types (MOS, EDO, arbitrary)

### 2. Complete Layout Implementations
- **Isomorphic**: Port logic from PitchGridRack Exquis implementation
- **String-Like**: Port logic from pg_linn_companion
- **Piano-Like**: Port logic from algos/mossy_keyboard_ui.py

### 3. Color Scheme Support
Implement coloring schemes (see notes.md):
- Fixed colors (root, on-scale, off-scale, unmapped)
- Circular HLC/HLS by scale degree
- Harmony-based HLC/HLS (sensory consonance)

Send colors to controllers via MIDI SysEx (templates in YAML configs).

### 4. Computer Keyboard MIDI Input
Create a keyboard event handler:
- Map physical keyboard keys to logical coordinates
- Generate MIDI note-on/note-off events
- Only active when app is in foreground
- Consider using `pynput` or similar library

### 5. Note Visualization
Enhance UI to show:
- Visual representation of controller pads
- Highlight currently playing notes in real-time
- Show which notes are mapped vs unmapped
- Use WebSocket to push updates from backend

### 6. OSC Protocol Implementation
Define and implement OSC message format with PitchGrid plugin:
- Scale updates (degrees, intervals, root)
- Note mappings (if plugin sends explicit mappings)
- Playing notes (for visualization)
- Unmapped note feedback (optional feature)

## Platform-Specific Notes

### macOS
- Virtual MIDI works out of the box via CoreMIDI
- May need to request microphone permission for keyboard events

### Linux
- Requires ALSA (`libasound2-dev`)
- User must be in `audio` group
- Virtual MIDI via rtmidi should work

### Windows
- Requires virtual MIDI driver (loopMIDI, etc.)
- User must install driver first
- Keyboard events may require elevated permissions

## Deployment

### Packaging (Future)
Target deployment methods:
- **macOS**: py2app or PyInstaller → .app bundle
- **Windows**: PyInstaller → .exe installer
- **Linux**: AppImage or .deb package

Bundle the built frontend (`frontend/dist/`) into the package.

## Testing Strategy

### Unit Tests
- Layout calculations with known scales
- MIDI message parsing and generation
- Controller config loading

### Integration Tests
- MIDI thread with mock devices
- OSC message handling
- Layout changes triggering mapping updates

### Manual Testing
- Test with real controllers if available
- Verify latency is acceptable (<5ms)
- Check all MIDI message types pass through correctly

## Common Pitfalls

1. **Don't use python 3.13** - rtmidi has issues
2. **Don't use placeholders in production** - Layout algorithms must be ported from reference implementations
3. **Don't hold locks in MIDI thread** - Use atomic updates and lock-free queues
4. **Don't forget thread-safety** - Multiple threads access shared state
5. **Don't hardcode controller note mappings** - Use config YAML or detection logic

## Questions to Resolve

1. **OSC Protocol**: What is the exact format PitchGrid plugin sends? (Need to check plugin code or docs)
2. **Note Mappings**: Does PitchGrid send explicit note mappings, or just scale structure?
3. **Controller Note Functions**: How should `noteToCoordX/Y` be generalized beyond simple formulas?
4. **Keyboard Library**: Which library for computer keyboard events? pynput? keyboard? Platform-specific?

## When Making Changes

- Always consider thread-safety implications
- Measure MIDI latency after significant changes
- Test with multiple controllers if possible
- Update this document when architecture changes
- Keep README.md in sync with features
