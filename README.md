# PitchGrid Isomorphic Controller Mapper (pg-isomap)

A universal mapper between PitchGrid microtonal scales and isomorphic MIDI controllers.

## Overview

PG Isomap provides musicians with easy access to microtonal scales on various isomorphic controllers. The application:

- Creates a virtual MIDI device that connects to the PitchGrid plugin
- Automatically discovers and connects to supported controllers
- Maps controller pads to PitchGrid's microtonal note mappings
- Supports multiple layout types: isomorphic, string-like, and piano-like
- Provides real-time visualization and configuration through a web UI

## Architecture

```
┌─────────────────┐
│   Controller    │ (Physical MIDI Device)
└────────┬────────┘
         │ MIDI In
         ▼
┌─────────────────────────────────────────┐
│         PG Isomap Application           │
│  ┌───────────────────────────────────┐  │
│  │   High-Priority MIDI Thread       │  │
│  │   - Note remapping                │  │
│  │   - Passthrough other messages    │  │
│  └───────────────────────────────────┘  │
│  ┌───────────────────────────────────┐  │
│  │   OSC Handler                     │  │
│  │   - Receives scale from PitchGrid │  │
│  └───────────────────────────────────┘  │
│  ┌───────────────────────────────────┐  │
│  │   Layout Calculator               │  │
│  │   - Isomorphic / String / Piano   │  │
│  └───────────────────────────────────┘  │
│  ┌───────────────────────────────────┐  │
│  │   Web Server (FastAPI + Svelte)  │  │
│  └───────────────────────────────────┘  │
└────────┬────────────────────────────────┘
         │ MIDI Out (Virtual Port)
         ▼
┌─────────────────┐
│  PitchGrid VST  │
└─────────────────┘
         │
         ▼
┌─────────────────┐
│      DAW        │
└─────────────────┘
```

## Supported Controllers

- Computer Keyboard (default, always available)
- LinnStrument 128
- Exquis
- Lumatone
- Launchpad Mini MK3

Additional controllers can be added via YAML configuration files.

## Installation

### Prerequisites

- Python 3.12 (required for rtmidi compatibility)
- [uv](https://github.com/astral-sh/uv) package manager
- Node.js 18+ (for frontend development)

### Setup

1. **Clone the repository:**
   ```bash
   cd /Users/peter/dev/PitchGrid/pg_isomap
   ```

2. **Install Python dependencies with uv:**
   ```bash
   uv sync
   ```

3. **Build the frontend:**
   ```bash
   cd frontend
   npm install
   npm run build
   cd ..
   ```

4. **Run the application:**
   ```bash
   uv run python -m pg_isomap
   ```

   Or for development with auto-reload:
   ```bash
   uv run python -m pg_isomap
   ```

5. **Access the web UI:**
   Open your browser to `http://localhost:8080`

## Development

### Backend Development

```bash
# Activate virtual environment
source .venv/bin/activate

# Run with debug logging
PGISOMAP_DEBUG=true uv run python -m pg_isomap

# Run tests
uv run pytest

# Format code
uv run black src/
uv run ruff check src/
```

### Frontend Development

```bash
cd frontend

# Run dev server with hot reload
npm run dev

# Build for production
npm run build

# Type check
npm run check
```

The frontend dev server runs on `http://localhost:5173` and proxies API requests to the backend.

## Configuration

### Environment Variables

Create a `.env` file in the project root:

```env
PGISOMAP_DEBUG=false
PGISOMAP_VIRTUAL_MIDI_DEVICE_NAME="PG Isomap"
PGISOMAP_OSC_LISTEN_PORT=9000
PGISOMAP_WEB_PORT=8080
```

### Adding New Controllers

Create a YAML file in `controller_config/`:

```yaml
DeviceName: "MyController"
MIDIDeviceName: "MyController MIDI"
virtualMIDIDeviceName: "PG MyController"
isMPE: true
hasGlobalPitchBend: false
NumRows: 8
FirstRowIdx: 0
RowLengths: [16, 16, 16, 16, 16, 16, 16, 16]
RowOffsets: [0, 0, 0, 0, 0, 0, 0]
HorizonToRowAngle: 0.0
RowToColAngle: 90.0
xSpacing: 19.0
ySpacing: 19.0
defaultIsoRootCoordinate: [5, 3]
```

## Layout Types

### Isomorphic
- All scale patterns look identical regardless of position
- Moving in one direction = same interval change
- Operations: root position, skew, rotate, flip, move

### String-Like
- Rows act as "strings" tuned to different notes
- Like guitar/bass layout
- Operations: string orientation, row offset, root position, move

### Piano-Like (Mosaic)
- Scale degrees arranged in strips
- Accidentals placed in configurable direction
- Can be unfolded or folded
- Operations: strip orientation, strip width, accidental direction, root position

## Usage

1. **Start the application** - Virtual MIDI device "PG Isomap" is created automatically

2. **Configure your DAW:**
   - Set MIDI input to "PG Isomap"
   - Route to PitchGrid plugin

3. **Configure PitchGrid plugin:**
   - Set OSC output to `localhost:9000`
   - Enable scale broadcasting

4. **Connect a controller:**
   - Physical controllers are auto-discovered every 3 seconds
   - Click "Connect" in the web UI when your controller appears
   - Or use computer keyboard by default

5. **Configure layout:**
   - Choose layout type (isomorphic/string-like/piano-like)
   - Adjust parameters (root position, orientation, etc.)
   - Changes apply in real-time

## Architecture Details

### Thread Safety

The application uses multiple threads:

- **MIDI Processing Thread** (high priority): Handles real-time note remapping
- **OSC Server Thread**: Receives scale updates from PitchGrid
- **Discovery Thread**: Periodically scans for controllers
- **Web Server Thread**: Serves UI and API

All threads communicate via thread-safe queues and atomic operations. The MIDI thread uses pre-computed lookup tables to minimize latency.

### Note Mapping Flow

1. OSC receives scale update from PitchGrid
2. Layout calculator generates (logical_x, logical_y) → MIDI note mapping
3. Mapping is stored as lookup table (atomic update)
4. MIDI thread uses lookup table for real-time note remapping
5. Non-note MIDI messages pass through unchanged

## Roadmap

### Phase 1 (Current - MVP)
- [x] Project structure and configuration
- [x] MIDI handling with threading
- [x] OSC communication
- [x] Basic layout calculators (placeholders)
- [x] Web UI scaffold
- [ ] Scalatrix integration
- [ ] Complete layout algorithms
- [ ] Color scheme support

### Phase 2
- [ ] Advanced layout transformations
- [ ] Multiple coloring schemes (fixed, circular HLC, harmony-based)
- [ ] Visual controller pad display in UI
- [ ] Save/load presets
- [ ] Note highlighting (played notes from controller)

### Phase 3
- [ ] Receive playing notes from PitchGrid (OSC)
- [ ] Send unmapped notes to PitchGrid
- [ ] Virtual play mode (software keyboard)
- [ ] Packaging for macOS/Windows/Linux

## Scalatrix Integration

The application depends on the [scalatrix](../scalatrix) library for scale theory. To build with scalatrix:

```bash
# Build scalatrix with Python bindings
cd ../scalatrix
# Follow scalatrix build instructions

# Link to pg-isomap
# TODO: Add specific integration steps
```

## Troubleshooting

### Virtual MIDI Device Not Appearing

- **macOS**: Virtual MIDI ports should work out of the box
- **Linux**: Install `libasound2-dev` and ensure user is in `audio` group
- **Windows**: Install loopMIDI or similar virtual MIDI driver

### Controller Not Detected

- Check that controller is connected and powered on
- Verify controller appears in system MIDI devices
- Check controller name matches `MIDIDeviceName` in config YAML

### High Latency

- Ensure MIDI thread has high priority (automatic on most systems)
- Check CPU usage - other processes may interfere
- Reduce buffer sizes in config if needed

## Contributing

Contributions welcome! Please:

1. Follow existing code style (black + ruff for Python)
2. Add tests for new features
3. Update documentation
4. Test with multiple controllers if possible

## License

[License TBD]

## Related Projects

- [pitchgrid-plugin](../pitchgrid-plugin) - PitchGrid VST plugin
- [pg_linn_companion](../pg_linn_companion) - LinnStrument-specific companion (predecessor)
- [scalatrix](../scalatrix) - Scale theory library (C++ with Python bindings)
- [PitchGridRack](../PitchGridRack) - Exquis isomorphic layout reference implementation
