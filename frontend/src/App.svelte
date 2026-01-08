<script lang="ts">
  import { onMount, onDestroy } from 'svelte';

  interface AppStatus {
    connected_controller: string | null;
    layout_type: string;
    virtual_midi_device: string;
    available_controllers: string[];
    midi_stats: {
      messages_processed: number;
      notes_remapped: number;
    };
  }

  let ws: WebSocket | null = null;
  let status: AppStatus | null = null;
  let selectedController: string = '';

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
      } else if (data.type === 'layout_update') {
        // Handle layout updates
        fetchStatus();
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
      status = await response.json();
    } catch (error) {
      console.error('Error fetching status:', error);
    }
  }

  async function connectController() {
    if (!selectedController) return;

    try {
      const response = await fetch('/api/controllers/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_name: selectedController }),
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

  onMount(() => {
    connectWebSocket();
    fetchStatus();
  });

  onDestroy(() => {
    if (ws) {
      ws.close();
    }
  });
</script>

<main>
  <h1>PitchGrid Isomap</h1>

  {#if status}
    <div class="card">
      <h2>Status</h2>
      <p><strong>Virtual MIDI Device:</strong> {status.virtual_midi_device}</p>
      <p><strong>Connected Controller:</strong> {status.connected_controller || 'None'}</p>
      <p><strong>Layout Type:</strong> {status.layout_type}</p>
      <p><strong>Messages Processed:</strong> {status.midi_stats.messages_processed}</p>
      <p><strong>Notes Remapped:</strong> {status.midi_stats.notes_remapped}</p>
    </div>

    <div class="card">
      <h2>Controller Connection</h2>

      {#if status.connected_controller}
        <p>Connected to: <strong>{status.connected_controller}</strong></p>
        <button on:click={disconnectController}>Disconnect</button>
      {:else}
        <p>Select a controller to connect:</p>
        <select bind:value={selectedController}>
          <option value="">-- Select Controller --</option>
          {#each status.available_controllers as controller}
            <option value={controller}>{controller}</option>
          {/each}
        </select>
        <button on:click={connectController} disabled={!selectedController}>
          Connect
        </button>
      {/if}
    </div>

    <div class="card">
      <h2>Layout Configuration</h2>
      <p>Layout configuration controls will be added here.</p>
      <p>Current layout: <strong>{status.layout_type}</strong></p>
    </div>
  {:else}
    <p>Loading...</p>
  {/if}
</main>

<style>
  main {
    width: 100%;
  }

  h1 {
    color: #646cff;
    margin-bottom: 1em;
  }

  h2 {
    font-size: 1.5em;
    margin-top: 0;
    margin-bottom: 0.5em;
  }

  select {
    padding: 0.5em;
    margin-right: 0.5em;
    border-radius: 4px;
    border: 1px solid #444;
    background-color: #1a1a1a;
    color: inherit;
    font-size: 1em;
  }

  button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
</style>
