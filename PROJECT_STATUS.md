# PG Isomap - Project Status

**Created:** 2026-01-08
**Status:** Initial scaffolding complete, ready for implementation

## What's Been Created

### ✅ Project Structure
```
pg_isomap/
├── src/pg_isomap/          # Python backend
│   ├── __init__.py
│   ├── __main__.py         # Entry point
│   ├── config.py           # Configuration management
│   ├── app.py              # Main coordinator
│   ├── web_api.py          # FastAPI routes + WebSocket
│   ├── midi_handler.py     # High-priority MIDI thread
│   ├── osc_handler.py      # OSC communication
│   ├── controller_config.py # YAML config loader
│   └── layouts/            # Layout calculators
│       ├── __init__.py
│       ├── base.py         # Abstract base classes
│       ├── isomorphic.py   # Isomorphic layout (placeholder)
│       ├── string_like.py  # String-like layout (placeholder)
│       └── piano_like.py   # Piano-like layout (placeholder)
├── frontend/               # Svelte UI
│   ├── src/
│   │   ├── App.svelte     # Main UI component
│   │   ├── main.ts
│   │   └── app.css
│   ├── package.json
│   ├── vite.config.ts
│   └── tsconfig.json
├── controller_config/      # Controller YAML files
│   ├── ComputerKeyboard.yaml
│   ├── Exquis.yaml
│   ├── LaunchpadMiniMK3.yaml
│   ├── LinnStrument128.yaml
│   └── Lumatone.yaml
├── tests/                  # Test directory (empty)
├── pyproject.toml          # Python deps with uv
├── .python-version         # Python 3.12
├── .gitignore
├── README.md               # Comprehensive documentation
├── CLAUDE.md               # Development guidelines
├── Makefile                # Common commands
├── run_dev.sh              # Dev startup script
└── notes.md                # Design notes (updated)
```

### ✅ Architecture Implemented

#### Threading Model
- **MIDI Processing Thread** (high priority) - Real-time note remapping with pre-computed lookup tables
- **OSC Server Thread** - Receives scale updates from PitchGrid plugin
- **Controller Discovery Thread** - Scans for controllers every 3 seconds
- **Web Server Thread** - FastAPI serving UI and WebSocket

#### Core Features Scaffolded
- Virtual MIDI device creation ("PG Isomap")
- Controller discovery and connection
- Layout configuration system (abstract, needs implementation)
- Web API with REST + WebSocket
- Basic UI with controller connection

### ✅ Configuration & Tooling
- uv package manager setup
- Python 3.12 requirement enforced
- FastAPI web server
- Svelte + Vite frontend
- Development scripts and Makefile
- Comprehensive .gitignore

## What's Still TODO

### 🔴 High Priority (Core Functionality)

1. **Scalatrix Integration**
   - Build or link scalatrix Python bindings
   - Import into layout calculators
   - Use for scale structure and interval calculations

2. **Complete Layout Implementations**
   - Port isomorphic layout from `../PitchGridRack`
   - Port string-like layout from `../pg_linn_companion`
   - Port piano-like layout from `../algos/mossy_keyboard_ui.py`
   - Implement all transformations (skew, rotate, flip, move)

3. **OSC Protocol Implementation**
   - Define message format with PitchGrid plugin
   - Parse scale updates correctly
   - Handle note mapping messages
   - Implement playing note feedback

4. **Controller Note Mapping**
   - Implement proper `logical_to_controller_note()` function
   - Use controller YAML configs for mapping
   - Handle different controller layouts (row-major vs other)

5. **Computer Keyboard MIDI Input**
   - Add keyboard event listener (consider `pynput`)
   - Map keyboard keys to logical coordinates
   - Generate MIDI messages when keys pressed
   - Only active when app in foreground

### 🟡 Medium Priority (Essential Features)

6. **Currently Playing Notes Visualization**
   - Track note-on/note-off events in MIDI handler
   - Push state updates via WebSocket
   - Highlight pressed pads in UI (always, regardless of layout)

7. **Visual Controller Display**
   - Render controller pads using physical coordinates
   - Show current layout mapping
   - Display colors based on scheme
   - Make interactive (click to test)

8. **Color Scheme Implementation**
   - Fixed colors (root, on-scale, off-scale, unmapped)
   - Circular HLC/HLS by scale degree
   - Harmony-based HLC/HLS (sensory consonance)
   - Send colors to controllers via MIDI SysEx

9. **Preset System**
   - Save/load layout + controller configurations
   - Store as JSON or YAML
   - Quick preset switching

### 🟢 Lower Priority (Nice to Have)

10. **Advanced UI Features**
    - Layout parameter controls (sliders, buttons)
    - Unmapped note highlighting
    - MIDI statistics display
    - Latency monitoring

11. **Testing**
    - Unit tests for layout calculators
    - Integration tests for MIDI flow
    - Controller config validation tests

12. **Packaging**
    - PyInstaller/py2app setup
    - Bundle frontend into executable
    - Platform-specific installers

## Known Limitations

1. **Layout algorithms are placeholders** - They use simple formulas instead of proper scalatrix-based calculations
2. **No scalatrix integration yet** - Dependency not linked
3. **Controller note mapping is hardcoded** - Assumes row-major layout
4. **No color support** - MIDI SysEx for colors not implemented
5. **No keyboard input** - Computer keyboard doesn't send MIDI yet
6. **No note visualization** - UI doesn't show currently playing notes

## Next Steps

### Immediate (to get a working MVP):

1. **Link scalatrix**
   ```bash
   # Build scalatrix with Python bindings
   cd ../scalatrix
   # [follow build instructions]

   # Add to pg_isomap dependencies
   # Update pyproject.toml
   ```

2. **Implement one complete layout** (suggest starting with string-like, as it's simplest)
   - Port from pg_linn_companion
   - Test with LinnStrument config
   - Verify MIDI output

3. **Define OSC protocol**
   - Look at PitchGrid plugin code or docs
   - Implement parser in `osc_handler.py`
   - Test with real plugin instance

4. **Test end-to-end**
   - Run app
   - Connect to controller (or use computer keyboard)
   - Send OSC from PitchGrid
   - Verify notes are remapped correctly

### Medium-term:

5. Implement remaining layouts (isomorphic, piano-like)
6. Add currently playing note visualization
7. Implement color schemes
8. Add computer keyboard input
9. Create comprehensive tests

## How to Start Development

```bash
# Install dependencies
make install

# Run in development mode
make dev

# Or manually:
./run_dev.sh

# Access UI at http://localhost:8080
```

## Architecture Decisions Made

✅ **Python 3.12** - Required for rtmidi compatibility
✅ **uv package manager** - Fast, modern Python package management
✅ **FastAPI + Svelte** - Clean separation, good performance
✅ **Dedicated MIDI thread** - High priority for low latency
✅ **Pre-computed lookup tables** - Atomic updates, lock-free reads
✅ **WebSocket for UI updates** - Real-time feedback
✅ **YAML controller configs** - Extensible, human-readable

## Performance Considerations

The critical path is:
```
Controller MIDI → Queue → MIDI Thread → Lookup → Virtual MIDI Out
```

Target latency: <5ms

Achieved through:
- High-priority thread (OS scheduling)
- Pre-computed lookup tables (no calculations in hot path)
- Lock-free queue (rtmidi releases GIL during I/O)
- Passthrough for non-note messages (minimal processing)

## Questions for Next Session

1. How should we integrate scalatrix? Build separately and link? Include as submodule?
2. What is the exact OSC format from PitchGrid plugin?
3. Which keyboard input library? pynput? keyboard? Platform-specific?
4. Should controller note mapping be in YAML configs or detected dynamically?
5. Color scheme priority - which to implement first?

---

**Ready to proceed with implementation!** 🚀
