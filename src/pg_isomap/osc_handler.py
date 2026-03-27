"""
OSC communication with PitchGrid plugin.

Receives scale information and note mappings from the plugin.

Protocol:
  - Receiver binds to a local port and sends heartbeats to the plugin
    on port 34562: /pitchgrid/heartbeat [1, <my_port>]
  - Plugin registers the receiver and sends data back to <my_port>
  - Plugin sends its own heartbeat: /pitchgrid/heartbeat [1]
  - Both sides consider the connection dead after 2s without a heartbeat
  - Up to 9 receivers can connect simultaneously
"""

import logging
import threading
import time
from typing import Callable, Optional

from pythonosc import dispatcher, osc_server, udp_client

logger = logging.getLogger(__name__)


class OSCHandler:
    """Handles bidirectional OSC communication with PitchGrid plugin."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        server_port: int = 34561,  # Receive from plugin (0 = ephemeral)
        client_port: int = 34562   # Send to plugin
    ):
        self.host = host
        self.server_port = server_port  # Where we listen (receive from plugin)
        self.client_port = client_port  # Where we send (to plugin)
        self.actual_port: int = 0       # Resolved after bind (may differ if ephemeral)

        # OSC Server (receives from plugin)
        self._server: Optional[osc_server.ThreadingOSCUDPServer] = None
        self._server_thread: Optional[threading.Thread] = None

        # OSC Client (sends to plugin)
        self._client: Optional[udp_client.SimpleUDPClient] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False

        # Callbacks
        self.on_scale_update: Optional[Callable] = None
        self.on_mapping_update: Optional[Callable] = None
        self.on_note_mapping: Optional[Callable] = None
        self.on_connection_changed: Optional[Callable] = None
        self.on_plugin_note_on: Optional[Callable] = None   # (mos_x, mos_y, velocity)
        self.on_plugin_note_off: Optional[Callable] = None   # (mos_x, mos_y)

        # Current state
        self.current_scale_data = None

        # Connection tracking — based on receiving plugin heartbeat
        self._last_plugin_heartbeat: float = 0
        self._connection_timeout: float = 2.0
        self.connected: bool = False

    @property
    def port(self) -> int:
        """Return actual listening port."""
        return self.actual_port or self.server_port

    def is_connected(self) -> bool:
        """Check if we're connected to the plugin."""
        return self.connected

    def start(self):
        """Start bidirectional OSC communication."""
        if self._running:
            logger.warning("OSC handler already running")
            return

        self._running = True

        # Initialize OSC client (for sending heartbeats to plugin)
        self._client = udp_client.SimpleUDPClient(self.host, self.client_port)

        # Create dispatcher for incoming messages
        disp = dispatcher.Dispatcher()
        disp.map("/pitchgrid/heartbeat", self._handle_plugin_heartbeat)
        disp.map("/pitchgrid/plugin/tuning", self._handle_tuning)
        disp.map("/pitchgrid/plugin/mapping", self._handle_mapping)
        disp.map("/pitchgrid/scale", self._handle_scale_update)
        disp.map("/pitchgrid/notes", self._handle_note_mapping)
        disp.map("/pitchgrid/playing", self._handle_playing_notes)
        disp.map("/pitchgrid/plugin/note_on", self._handle_plugin_note_on)
        disp.map("/pitchgrid/plugin/note_off", self._handle_plugin_note_off)
        disp.set_default_handler(self._default_handler)

        # Create and start OSC server (for receiving from plugin)
        self._server = osc_server.ThreadingOSCUDPServer(
            (self.host, self.server_port), disp
        )
        self.actual_port = self._server.server_address[1]

        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True
        )
        self._server_thread.name = "OSC-Server"
        self._server_thread.start()

        # Start heartbeat thread (sends heartbeat with our port to plugin every 1 second)
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True
        )
        self._heartbeat_thread.name = "OSC-Heartbeat"
        self._heartbeat_thread.start()

        # Start connection monitor thread
        self._monitor_thread = threading.Thread(
            target=self._monitor_connection,
            daemon=True
        )
        self._monitor_thread.name = "OSC-Monitor"
        self._monitor_thread.start()

        logger.info(
            f"OSC communication started - "
            f"Listening: {self.host}:{self.actual_port}, "
            f"Plugin: {self.host}:{self.client_port}"
        )

    def stop(self):
        """Stop OSC communication."""
        self._running = False

        if self._server:
            self._server.shutdown()
            self._server = None

        if self._server_thread:
            self._server_thread.join(timeout=2.0)
            self._server_thread = None

        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=2.0)
            self._heartbeat_thread = None

        if self._monitor_thread:
            self._monitor_thread.join(timeout=2.0)
            self._monitor_thread = None

        self._client = None

        logger.info("OSC communication stopped")

    def _heartbeat_loop(self):
        """Send periodic heartbeat to plugin, including our listen port."""
        while self._running:
            try:
                if self._client:
                    self._client.send_message(
                        "/pitchgrid/heartbeat",
                        [1, self.actual_port]
                    )
            except Exception as e:
                logger.error(f"Error sending heartbeat: {e}")

            time.sleep(1.0)

    def _monitor_connection(self):
        """Monitor connection status based on receiving plugin heartbeats."""
        while self._running:
            time_since_heartbeat = time.time() - self._last_plugin_heartbeat

            was_connected = self.connected

            if time_since_heartbeat > self._connection_timeout:
                self.connected = False
            elif time_since_heartbeat <= self._connection_timeout and not self.connected:
                self.connected = True

            # Notify on connection state change
            if was_connected != self.connected:
                logger.info(f"OSC connection: {'connected' if self.connected else 'disconnected'}")
                if self.on_connection_changed:
                    self.on_connection_changed(self.connected)

            time.sleep(0.5)

    def _default_handler(self, address: str, *args):
        """Handle unmapped OSC messages."""
        logger.debug(f"Received unmapped OSC: {address} {args}")

    def _handle_plugin_heartbeat(self, address: str, *args):
        """Handle heartbeat from plugin — connection is alive."""
        self._last_plugin_heartbeat = time.time()

    def _handle_tuning(self, address: str, *args):
        """Handle tuning data from PitchGrid plugin."""
        logger.info(f"Received tuning data: {args}")
        self._last_plugin_heartbeat = time.time()

        # Parse tuning data (mode, root_freq, stretch, skew, mode_offset, steps, mos_a, mos_b)
        if self.on_scale_update:
            tuning_data = {
                'address': address,
                'args': args
            }
            self.on_scale_update(tuning_data)

    def _handle_mapping(self, address: str, *args):
        """Handle mapping data from PitchGrid plugin (frozen when mapping is locked)."""
        logger.info(f"Received mapping data: {args}")
        self._last_plugin_heartbeat = time.time()

        # Parse mapping data (mode, root_freq, stretch, skew, mode_offset, steps, mos_a, mos_b)
        if self.on_mapping_update:
            mapping_data = {
                'address': address,
                'args': args
            }
            self.on_mapping_update(mapping_data)

    def _handle_scale_update(self, address: str, *args):
        """Handle scale update from PitchGrid plugin."""
        logger.debug(f"Received scale update: {args}")

        self._last_plugin_heartbeat = time.time()

        scale_data = {
            'address': address,
            'args': args
        }

        self.current_scale_data = scale_data

        if self.on_scale_update:
            self.on_scale_update(scale_data)

    def _handle_note_mapping(self, address: str, *args):
        """Handle note mapping from PitchGrid plugin."""
        logger.debug(f"Received note mapping: {args}")

        self._last_plugin_heartbeat = time.time()

        mapping_data = {
            'address': address,
            'args': args
        }

        if self.on_note_mapping:
            self.on_note_mapping(mapping_data)

    def _handle_plugin_note_on(self, address: str, *args):
        """Handle note_on forwarded from PitchGrid plugin (root coordinates)."""
        if len(args) >= 3:
            root_x, root_y, velocity = int(args[0]), int(args[1]), int(args[2])
            logger.debug(f"Plugin note_on: root=({root_x},{root_y}) vel={velocity}")
            if self.on_plugin_note_on:
                self.on_plugin_note_on(root_x, root_y, velocity)

    def _handle_plugin_note_off(self, address: str, *args):
        """Handle note_off forwarded from PitchGrid plugin (root coordinates)."""
        if len(args) >= 2:
            root_x, root_y = int(args[0]), int(args[1])
            logger.debug(f"Plugin note_off: root=({root_x},{root_y})")
            if self.on_plugin_note_off:
                self.on_plugin_note_off(root_x, root_y)

    def _handle_playing_notes(self, address: str, *args):
        """Handle currently playing notes (for visualization)."""
        logger.debug(f"Received playing notes: {args}")

        self._last_plugin_heartbeat = time.time()
