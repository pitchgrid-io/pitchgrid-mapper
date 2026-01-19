<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import ControllerCanvas from './ControllerCanvas.svelte';
  import TransformIcon from './TransformIcon.svelte';
  import ButtonSymbol from './ButtonSymbol.svelte';

  interface Pad {
    x: number;
    y: number;
    phys_x: number;
    phys_y: number;
    shape: Array<[number, number]>;
    note?: number | null;
    color?: string | null;
    mos_coord?: [number, number] | null;
    mos_label_digit?: string | null;
    mos_label_letter?: string | null;
  }

  interface AppStatus {
    connected_controller: string | null;
    midi_connected: boolean;
    layout_type: string;
    virtual_midi_device: string;
    available_controllers: string[];
    detected_controllers: string[];
    controller_pads: Pad[];
    controller_geometry: {
      horizon_to_row_angle: number;
      row_to_col_angle: number;
    } | null;
    osc_connected: boolean;
    osc_port: number;
    tuning: {
      depth: number;
      mode: number;
      root_freq: number;
      stretch: number;
      skew: number;
      mode_offset: number;
      steps: number;
      scale_system: string;
      scale_degree_count: number;
    };
    midi_stats: {
      messages_processed: number;
      notes_remapped: number;
    };
    platform: string;  // 'win32', 'darwin', 'linux'
  }

  let ws: WebSocket | null = null;
  let status: AppStatus | null = null;
  let selectedController: string = '';

  // Track active (playing) notes by coordinate string "x,y"
  let activeNotes: Set<string> = new Set();

  // Keyboard mapping: keyboard key code -> (x, y) pad coordinate
  // Based on US QWERTY layout matching ComputerKeyboard.yaml fixedLabels
  const keyboardMapping: Record<string, [number, number]> = {
    // Row 0 (y=0): Z, X, C, V, B, N, M, comma, period, slash
    'KeyZ': [0, 0], 'KeyX': [1, 0], 'KeyC': [2, 0], 'KeyV': [3, 0], 'KeyB': [4, 0],
    'KeyN': [5, 0], 'KeyM': [6, 0], 'Comma': [7, 0], 'Period': [8, 0], 'Slash': [9, 0],
    // Row 1 (y=1): A, S, D, F, G, H, J, K, L, semicolon, quote
    'KeyA': [-1, 1], 'KeyS': [0, 1], 'KeyD': [1, 1], 'KeyF': [2, 1], 'KeyG': [3, 1],
    'KeyH': [4, 1], 'KeyJ': [5, 1], 'KeyK': [6, 1], 'KeyL': [7, 1], 'Semicolon': [8, 1], 'Quote': [9, 1],
    // Row 2 (y=2): Q, W, E, R, T, Y, U, I, O, P, bracket left, bracket right, backslash
    'KeyQ': [-2, 2], 'KeyW': [-1, 2], 'KeyE': [0, 2], 'KeyR': [1, 2], 'KeyT': [2, 2],
    'KeyY': [3, 2], 'KeyU': [4, 2], 'KeyI': [5, 2], 'KeyO': [6, 2], 'KeyP': [7, 2],
    'BracketLeft': [8, 2], 'BracketRight': [9, 2], 'Backslash': [10, 2],
    // Row 3 (y=3): 1, 2, 3, 4, 5, 6, 7, 8, 9, 0, minus, equal
    'Digit1': [-3, 3], 'Digit2': [-2, 3], 'Digit3': [-1, 3], 'Digit4': [0, 3], 'Digit5': [1, 3],
    'Digit6': [2, 3], 'Digit7': [3, 3], 'Digit8': [4, 3], 'Digit9': [5, 3], 'Digit0': [6, 3],
    'Minus': [7, 3], 'Equal': [8, 3],
  };

  // Track which keys are currently pressed to prevent repeat triggers
  const pressedKeys: Set<string> = new Set();

  // Pad label type: 'digits' (default), 'letters', 'mos_coords', 'device_coords', or 'midi_note'
  type LabelType = 'digits' | 'letters' | 'mos_coords' | 'device_coords' | 'midi_note';
  let padLabelType: LabelType = 'digits';

  // Helper to determine if controller is quad-like or hex-like
  function isQuadLayout(status: AppStatus | null): boolean {
    if (!status || !status.controller_geometry) return true; // Default to quad
    const angle = status.controller_geometry.row_to_col_angle;
    return angle > 75 && angle < 105;
  }

  // Get controller geometry angles with defaults
  function getGeometry(status: AppStatus | null): { horizonAngle: number; rowToColAngle: number } {
    if (!status || !status.controller_geometry) {
      return { horizonAngle: 0, rowToColAngle: 90 };
    }
    return {
      horizonAngle: status.controller_geometry.horizon_to_row_angle,
      rowToColAngle: status.controller_geometry.row_to_col_angle,
    };
  }

  // Helper to check if controller is detected/available
  function isControllerAvailable(controllerName: string): boolean {
    if (!status) return false;
    if (controllerName === 'Computer Keyboard') return true;
    return status.detected_controllers.includes(controllerName);
  }

  // Handle controller selection from dropdown
  async function handleControllerSelection(event: Event) {
    const target = event.target as HTMLSelectElement;
    const controllerName = target.value;

    if (!controllerName) return;

    selectedController = controllerName;

    // Always switch to the controller configuration to show its layout
    await switchToController(controllerName);

    // If it's a physical controller and it's available, also connect via MIDI
    if (controllerName !== 'Computer Keyboard' && isControllerAvailable(controllerName)) {
      await connectController(controllerName);
    }
  }

  // Switch to a controller configuration (doesn't require MIDI connection)
  async function switchToController(deviceName: string) {
    try {
      const response = await fetch('/api/controllers/switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_name: deviceName }),
      });

      const result = await response.json();
      if (result.success) {
        await fetchStatus();
      }
    } catch (error) {
      console.error('Error switching controller:', error);
    }
  }

  // Handle layout type selection
  async function handleLayoutSelection(event: Event) {
    const target = event.target as HTMLSelectElement;
    const layoutType = target.value;

    try {
      const response = await fetch('/api/layout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ layout_type: layoutType }),
      });

      const result = await response.json();
      if (result.success) {
        console.log('Layout changed to:', layoutType);
      }
    } catch (error) {
      console.error('Error changing layout:', error);
    }
  }

  // Handle transformation toolbar actions
  async function handleTransformation(transformType: string) {
    console.log(`Applying transformation: ${transformType}`);

    // Send transformation via WebSocket if connected
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: 'apply_transformation',
        transformation: transformType,
      }));
    }
  }

  // WebSocket connection
  function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}/ws`);

    ws.onopen = () => {
      console.log('WebSocket connected');
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      console.log('Received:', data);

      if (data.type === 'init') {
        status = data.status;
      } else if (data.type === 'status_update') {
        // Real-time status update from backend
        status = data.status;
      } else if (data.type === 'layout_update') {
        // Handle layout updates
        fetchStatus();
      } else if (data.type === 'note_event') {
        // Handle note on/off for pad highlighting
        const key = `${data.x},${data.y}`;
        if (data.note_on) {
          activeNotes.add(key);
        } else {
          activeNotes.delete(key);
        }
        // Trigger reactivity
        activeNotes = activeNotes;
      }
    };

    ws.onclose = () => {
      console.log('WebSocket disconnected, reconnecting...');
      setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = (error) => {
      console.error('WebSocket error:', error);
    };
  }

  async function fetchStatus() {
    try {
      const response = await fetch('/api/status');
      const data = await response.json();
      console.log('Fetched status:', data);
      status = data;
    } catch (error) {
      console.error('Error fetching status:', error);
    }
  }

  async function connectController(deviceName?: string) {
    const name = deviceName || selectedController;
    if (!name) return;

    try {
      const response = await fetch('/api/controllers/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_name: name }),
      });

      const result = await response.json();
      if (result.success) {
        await fetchStatus();
      }
    } catch (error) {
      console.error('Error connecting controller:', error);
    }
  }

  async function disconnectController() {
    try {
      await fetch('/api/controllers/disconnect', { method: 'POST' });
      await fetchStatus();
    } catch (error) {
      console.error('Error disconnecting controller:', error);
    }
  }

  async function handlePadNoteOn(x: number, y: number) {
    console.log(`Pad note on: (${x}, ${y})`);
    try {
      const response = await fetch('/api/trigger_note', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ x, y, velocity: 100, note_on: true }),
      });
      const result = await response.json();
      if (result.success) {
        console.log(`Note on: ${result.note}`);
      } else {
        console.warn('Pad not mapped:', result.error);
      }
    } catch (error) {
      console.error('Error triggering note on:', error);
    }
  }

  async function handlePadNoteOff(x: number, y: number) {
    console.log(`Pad note off: (${x}, ${y})`);
    try {
      const response = await fetch('/api/trigger_note', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ x, y, velocity: 0, note_on: false }),
      });
      const result = await response.json();
      if (result.success) {
        console.log(`Note off: ${result.note}`);
      } else {
        console.warn('Pad not mapped:', result.error);
      }
    } catch (error) {
      console.error('Error triggering note off:', error);
    }
  }

  // Keyboard event handlers for Computer Keyboard controller
  function handleKeyDown(event: KeyboardEvent) {
    // Only handle if Computer Keyboard is selected
    if (!status || status.connected_controller !== 'Computer Keyboard') return;

    // Check if this is a mapped key
    const coord = keyboardMapping[event.code];
    if (!coord) return;

    // Always prevent default for mapped keys to stop system sounds and key repeat behavior
    event.preventDefault();
    event.stopPropagation();

    // Ignore if key is already pressed (prevent key repeat from triggering multiple note-ons)
    if (pressedKeys.has(event.code)) return;

    // Trigger note on
    pressedKeys.add(event.code);
    handlePadNoteOn(coord[0], coord[1]);
  }

  function handleKeyUp(event: KeyboardEvent) {
    // Only handle if Computer Keyboard is selected
    if (!status || status.connected_controller !== 'Computer Keyboard') return;

    // Check if this is a mapped key
    const coord = keyboardMapping[event.code];
    if (!coord) return;

    // Always prevent default for mapped keys
    event.preventDefault();
    event.stopPropagation();

    // Check if this key was pressed (might not be if controller was switched while held)
    if (!pressedKeys.has(event.code)) return;

    // Trigger note off
    pressedKeys.delete(event.code);
    handlePadNoteOff(coord[0], coord[1]);
  }

  // Handle window blur - release all pressed keys
  function handleWindowBlur() {
    // Release all currently pressed keys
    for (const keyCode of pressedKeys) {
      const coord = keyboardMapping[keyCode];
      if (coord) {
        handlePadNoteOff(coord[0], coord[1]);
      }
    }
    pressedKeys.clear();
  }

  onMount(() => {
    connectWebSocket();
    fetchStatus();

    // Add keyboard event listeners with capture phase to intercept before focused elements
    document.addEventListener('keydown', handleKeyDown, { capture: true });
    document.addEventListener('keyup', handleKeyUp, { capture: true });
    window.addEventListener('blur', handleWindowBlur);
  });

  // Update selected controller when status changes
  $: if (status && status.connected_controller) {
    selectedController = status.connected_controller;
  }

  onDestroy(() => {
    if (ws) {
      ws.close();
    }

    // Remove keyboard event listeners (must match capture option used in addEventListener)
    document.removeEventListener('keydown', handleKeyDown, { capture: true });
    document.removeEventListener('keyup', handleKeyUp, { capture: true });
    window.removeEventListener('blur', handleWindowBlur);
  });
</script>

<main>

  {#if status}
    <div class="card">
      <div class="controller-selector">
        <label for="controller-select">Controller:</label>
        <select
          id="controller-select"
          value={selectedController}
          on:change={handleControllerSelection}
        >
          {#each status.available_controllers as controller}
            {@const available = isControllerAvailable(controller)}
            <option value={controller}>
              {controller}{available ? ' (available)' : ''}
            </option>
          {/each}
        </select>

        <label for="layout-select">Layout:</label>
        <select
          id="layout-select"
          value={status.layout_type}
          on:change={handleLayoutSelection}
        >
          <option value="isomorphic">Isomorphic</option>
          <option value="string_like">String-like</option>
          <option value="piano_like">Piano-like</option>
        </select>

        <label for="label-select">Labels:</label>
        <select
          id="label-select"
          bind:value={padLabelType}
        >
          <option value="digits">Digits (1, 2, 3...)</option>
          <option value="letters">Letters (C, D, E...)</option>
          <option value="midi_note">MIDI Note</option>
          <option value="mos_coords">MOS Coordinates</option>
          <option value="device_coords">Device Coordinates</option>
        </select>
      </div>

      <!-- Transformation Toolbar (for isomorphic layout) -->
      {#if status.layout_type === 'isomorphic'}
        {@const geometry = getGeometry(status)}
        {@const isQuad = isQuadLayout(status)}
        {#key `${geometry.horizonAngle}-${geometry.rowToColAngle}`}
        <div class="transformation-toolbar">
          <!-- Shift operations -->
          <div class="toolbar-group">
            <span class="toolbar-label">Shift:</span>
            {#if isQuad}
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_left')} title="Shift Left">
                <TransformIcon type="shift" direction="left" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_right')} title="Shift Right">
                <TransformIcon type="shift" direction="right" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_up')} title="Shift Up">
                <TransformIcon type="shift" direction="up" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_down')} title="Shift Down">
                <TransformIcon type="shift" direction="down" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
            {:else}
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_left')} title="Shift Left">
                <TransformIcon type="shift" direction="left" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_right')} title="Shift Right">
                <TransformIcon type="shift" direction="right" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_upright')} title="Shift Up-Right">
                <TransformIcon type="shift" direction="upright" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_downleft')} title="Shift Down-Left">
                <TransformIcon type="shift" direction="downleft" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_upleft')} title="Shift Up-Left">
                <TransformIcon type="shift" direction="upleft" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_downright')} title="Shift Down-Right">
                <TransformIcon type="shift" direction="downright" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
            {/if}
          </div>

          <!-- Skew operations -->
          <div class="toolbar-group">
            <span class="toolbar-label">Skew:</span>
            {#if isQuad}
              <button class="toolbar-btn" on:click={() => handleTransformation('skew_left')} title="Skew Left">
                <TransformIcon type="skew" direction="left" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('skew_right')} title="Skew Right">
                <TransformIcon type="skew" direction="right" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('skew_down')} title="Skew Down">
                <TransformIcon type="skew" direction="down" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('skew_up')} title="Skew Up">
                <TransformIcon type="skew" direction="up" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
            {:else}
              <button class="toolbar-btn" on:click={() => handleTransformation('skew_left')} title="Skew Left">
                <TransformIcon type="skew" direction="left" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('skew_right')} title="Skew Right">
                <TransformIcon type="skew" direction="right" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('skew_upright')} title="Skew Up-Right">
                <TransformIcon type="skew" direction="upright" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('skew_downleft')} title="Skew Down-Left">
                <TransformIcon type="skew" direction="downleft" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('skew_upleft')} title="Skew Up-Left">
                <TransformIcon type="skew" direction="upleft" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('skew_downright')} title="Skew Down-Right">
                <TransformIcon type="skew" direction="downright" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
            {/if}
          </div>

          <!-- Rotate operations -->
          <div class="toolbar-group">
            <span class="toolbar-label">Rotate:</span>
            {#if isQuad}
              <button class="toolbar-btn" on:click={() => handleTransformation('rotate_left')} title="Rotate Left (90°)">
                <TransformIcon type="rotate" direction="left" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('rotate_right')} title="Rotate Right (90°)">
                <TransformIcon type="rotate" direction="right" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
            {:else}
              <button class="toolbar-btn" on:click={() => handleTransformation('rotate_left_hex')} title="Rotate Left (60°)">
                <TransformIcon type="rotate" direction="left_hex" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('rotate_right_hex')} title="Rotate Right (60°)">
                <TransformIcon type="rotate" direction="right_hex" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
            {/if}
          </div>

          <!-- Reflect operations -->
          <div class="toolbar-group">
            <span class="toolbar-label">Reflect:</span>
            {#if isQuad}
              <button class="toolbar-btn" on:click={() => handleTransformation('reflect_horizontal')} title="Reflect Horizontal">
                <TransformIcon type="reflect" direction="left" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('reflect_vertical')} title="Reflect Vertical">
                <TransformIcon type="reflect" direction="up" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
            {:else}
              <button class="toolbar-btn" on:click={() => handleTransformation('reflect_x_hex')} title="Reflect X">
                <TransformIcon type="reflect" direction="x_hex" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('reflect_y_hex')} title="Reflect Y">
                <TransformIcon type="reflect" direction="y_hex" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('reflect_xy_hex')} title="Reflect XY">
                <TransformIcon type="reflect" direction="xy_hex" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
            {/if}
          </div>
        </div>
        {/key}
      {/if}

      <!-- Transformation Toolbar (for string-like layout) -->
      {#if status.layout_type === 'string_like'}
        {@const geometry = getGeometry(status)}
        {@const isQuad = isQuadLayout(status)}
        {#key `${geometry.horizonAngle}-${geometry.rowToColAngle}`}
        <div class="transformation-toolbar">
          <div class="toolbar-group">
            <span class="toolbar-label">Shift:</span>
            <button class="toolbar-btn" on:click={() => handleTransformation('shift_left')} title="Shift Left (along string)">
              <TransformIcon type="shift" direction="left" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
            </button>
            <button class="toolbar-btn" on:click={() => handleTransformation('shift_right')} title="Shift Right (along string)">
              <TransformIcon type="shift" direction="right" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
            </button>
            {#if isQuad}
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_up')} title="Shift Up (between strings)">
                <TransformIcon type="shift" direction="up" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_down')} title="Shift Down (between strings)">
                <TransformIcon type="shift" direction="down" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
            {:else}
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_up')} title="Shift Up-Right (between strings)">
                <TransformIcon type="shift" direction="up" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_down')} title="Shift Down-Left (between strings)">
                <TransformIcon type="shift" direction="down" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_upleft')} title="Shift Up-Left (between strings)">
                <TransformIcon type="shift" direction="upleft" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_downright')} title="Shift Down-Right (between strings)">
                <TransformIcon type="shift" direction="downright" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
            {/if}
          </div>
          <div class="toolbar-group">
            <span class="toolbar-label">Row Offset:</span>
            <button class="toolbar-btn" on:click={() => handleTransformation('skew_left')} title="Decrease Row Offset">
              <ButtonSymbol type="minus" />
            </button>
            <button class="toolbar-btn" on:click={() => handleTransformation('skew_right')} title="Increase Row Offset">
              <ButtonSymbol type="plus" />
            </button>
          </div>

          <div class="toolbar-group">
            <span class="toolbar-label">Reverse:</span>
            {#if isQuad}
              <button class="toolbar-btn" on:click={() => handleTransformation('reflect_vertical')} title="Reverse Ordering of Strings">
                <TransformIcon type="reverse" direction="left" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('reflect_horizontal')} title="Reverse Pitch Direction on Strings">
                <TransformIcon type="reverse" direction="up" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
            {:else}
              <button class="toolbar-btn" on:click={() => handleTransformation('reflect_vertical_hex')} title="Reverse Ordering of Strings">
                <TransformIcon type="reverse" direction="left" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('reflect_horizontal_hex')} title="Reverse Pitch Direction on Strings">
                <TransformIcon type="reverse" direction="up" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
            {/if}
          </div>
        </div>
        {/key}
      {/if}

      <!-- Transformation Toolbar (for piano-like layout) -->
      {#if status.layout_type === 'piano_like'}
        {@const geometry = getGeometry(status)}
        {@const isQuad = isQuadLayout(status)}
        {#key `${geometry.horizonAngle}-${geometry.rowToColAngle}`}
        <div class="transformation-toolbar">
          <div class="toolbar-group">
            <span class="toolbar-label">Shift:</span>
            <button class="toolbar-btn" on:click={() => handleTransformation('shift_left')} title="Shift Left (along scale)">
              <TransformIcon type="shift" direction="left" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
            </button>
            <button class="toolbar-btn" on:click={() => handleTransformation('shift_right')} title="Shift Right (along scale)">
              <TransformIcon type="shift" direction="right" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
            </button>
            {#if isQuad}
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_up')} title="Shift Up (between strips)">
                <TransformIcon type="shift" direction="up" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_down')} title="Shift Down (between strips)">
                <TransformIcon type="shift" direction="down" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
            {:else}
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_up')} title="Shift Up-Right (between strips)">
                <TransformIcon type="shift" direction="up" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_down')} title="Shift Down-Left (between strips)">
                <TransformIcon type="shift" direction="down" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_upleft')} title="Shift Up-Left (between strips)">
                <TransformIcon type="shift" direction="upleft" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
              <button class="toolbar-btn" on:click={() => handleTransformation('shift_downright')} title="Shift Down-Right (between strips)">
                <TransformIcon type="shift" direction="downright" horizonToRowAngle={geometry.horizonAngle} rowToColAngle={geometry.rowToColAngle} />
              </button>
            {/if}
          </div>
          <div class="toolbar-group">
            <span class="toolbar-label">Strip Offset:</span>
            <button class="toolbar-btn" on:click={() => handleTransformation('skew_left')} title="Decrease Strip Offset">
              <ButtonSymbol type="minus" />
            </button>
            <button class="toolbar-btn" on:click={() => handleTransformation('skew_right')} title="Increase Strip Offset">
              <ButtonSymbol type="plus" />
            </button>
          </div>

          <div class="toolbar-group">
            <span class="toolbar-label">Strip Width:</span>
            <button class="toolbar-btn" on:click={() => handleTransformation('decrease_strip_width')} title="Decrease Strip Width">
              <ButtonSymbol type="minus" />
            </button>
            <button class="toolbar-btn" on:click={() => handleTransformation('increase_strip_width')} title="Increase Strip Width">
              <ButtonSymbol type="plus" />
            </button>
          </div>

          <div class="toolbar-group">
            <span class="toolbar-label">Scale Row:</span>
            <button class="toolbar-btn" on:click={() => handleTransformation('scale_row_down')} title="Move Scale Row Down (within strip)">
              <ButtonSymbol type="down" />
            </button>
            <button class="toolbar-btn" on:click={() => handleTransformation('scale_row_up')} title="Move Scale Row Up (within strip)">
              <ButtonSymbol type="up" />
            </button>
          </div>
        </div>
        {/key}
      {/if}

      {#if status.controller_pads.length > 0}
        <div class="canvas-wrapper">
          <ControllerCanvas
            pads={status.controller_pads}
            deviceName={status.connected_controller || 'Computer Keyboard'}
            onPadNoteOn={handlePadNoteOn}
            onPadNoteOff={handlePadNoteOff}
            {activeNotes}
            {padLabelType}
          />
        </div>
      {:else}
        <p>No controller loaded</p>
      {/if}

      <!-- Status badges row below canvas -->
      <div class="status-badges">
        {#if status.midi_connected}
          <span class="badge midi-connected">● Controller: {status.connected_controller}</span>
        {/if}

        <span class="badge virtual-midi-indicator" class:virtual-midi-connected={status.virtual_midi_device !== 'None'}>
          ● Virtual MIDI: {status.virtual_midi_device}
        </span>

        <span class="badge osc-indicator" class:osc-connected={status.osc_connected}>
          ● Tuning sync via OSC: {status.osc_connected ? 'Connected' : 'Disconnected'}
        </span>
      </div>
    </div>
  {:else}
    <p>Loading...</p>
  {/if}
</main>

<style>
  main {
    width: 100%;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-sizing: border-box;
  }

  main > .card {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }

  .canvas-wrapper {
    flex: 1;
    min-height: 0;
    display: flex;
  }

  h2 {
    font-size: 1.5em;
    margin-top: 0;
    margin-bottom: 0.5em;
  }

  .controller-selector {
    display: flex;
    align-items: center;
    gap: 1em;
    margin-bottom: 1em;
    flex-wrap: wrap;
  }

  .controller-selector label {
    font-weight: 500;
  }

  select {
    padding: 0.5em;
    border-radius: 4px;
    border: 1px solid #444;
    background-color: #1a1a1a;
    color: #d4d4d4;
    font-size: 1em;
    min-width: 200px;
  }

  .status-badges {
    display: flex;
    gap: 1em;
    align-items: center;
    padding: 1em;
    background-color: rgba(0, 0, 0, 0.3);
    border-top: 1px solid #333;
    flex-wrap: wrap;
  }

  .badge {
    font-size: 0.85em;
    padding: 0.4em 0.75em;
    border-radius: 4px;
    white-space: nowrap;
  }

  .midi-connected {
    color: #54cec2;
    background-color: rgba(84, 206, 194, 0.15);
    border: 1px solid rgba(84, 206, 194, 0.3);
  }

  .virtual-midi-indicator {
    color: #888;
    background-color: rgba(136, 136, 136, 0.1);
    border: 1px solid rgba(136, 136, 136, 0.3);
  }

  .virtual-midi-indicator.virtual-midi-connected {
    color: #9b7eff;
    background-color: rgba(155, 126, 255, 0.15);
    border: 1px solid rgba(155, 126, 255, 0.3);
  }

  .osc-indicator {
    color: #888;
    background-color: rgba(136, 136, 136, 0.1);
    border: 1px solid rgba(136, 136, 136, 0.3);
  }

  .osc-indicator.osc-connected {
    color: #54cec2;
    background-color: rgba(84, 206, 194, 0.15);
    border: 1px solid rgba(84, 206, 194, 0.3);
  }

  button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .detected-controller {
    display: flex;
    align-items: center;
    gap: 1em;
    padding: 0.5em 0;
  }

  .detected-controller span {
    flex: 1;
  }

  .info-text {
    margin-top: 1em;
    font-size: 0.9em;
    color: #888;
  }

  .transformation-toolbar {
    display: flex;
    gap: 1em;
    align-items: center;
    padding: 0.5em 0.75em;
    background-color: rgba(84, 206, 194, 0.05);
    border-radius: 4px;
    margin-bottom: 0.75em;
    flex-wrap: wrap;
  }

  .toolbar-group {
    display: flex;
    gap: 0;
    align-items: center;
  }

  .toolbar-label {
    font-size: 0.85em;
    font-weight: 500;
    color: #54cec2;
    margin-right: 0.4em;
  }

  .toolbar-btn {
    width: 2.5em;
    height: 2.5em;
    padding: 0;
    border: 1px solid #444;
    border-radius: 4px;
    background-color: #1a1a1a;
    color: #d4d4d4;
    font-size: 1em;
    cursor: pointer;
    transition: all 0.2s;
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .toolbar-btn:hover {
    background-color: #2a2a2a;
    border-color: #54cec2;
    color: #54cec2;
    z-index: 2;
  }

  .toolbar-btn:active {
    transform: scale(0.95);
    background-color: rgba(84, 206, 194, 0.2);
  }
</style>
