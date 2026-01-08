"""
OSC communication with PitchGrid plugin.

Receives scale information and note mappings from the plugin.
"""

import logging
import threading
from typing import Callable, Optional

from pythonosc import dispatcher, osc_server

logger = logging.getLogger(__name__)


class OSCHandler:
    """Handles OSC communication with PitchGrid plugin."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9000):
        self.host = host
        self.port = port

        self._server: Optional[osc_server.ThreadingOSCUDPServer] = None
        self._server_thread: Optional[threading.Thread] = None

        # Callbacks
        self.on_scale_update: Optional[Callable] = None
        self.on_note_mapping: Optional[Callable] = None

        # Current state
        self.current_scale_data = None

    def start(self):
        """Start OSC server in background thread."""
        if self._server:
            logger.warning("OSC server already running")
            return

        # Create dispatcher
        disp = dispatcher.Dispatcher()
        disp.map("/pitchgrid/scale", self._handle_scale_update)
        disp.map("/pitchgrid/notes", self._handle_note_mapping)
        disp.map("/pitchgrid/playing", self._handle_playing_notes)

        # Create server
        self._server = osc_server.ThreadingOSCUDPServer(
            (self.host, self.port), disp
        )

        # Start in thread
        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True
        )
        self._server_thread.name = "OSC-Server"
        self._server_thread.start()

        logger.info(f"OSC server started on {self.host}:{self.port}")

    def stop(self):
        """Stop OSC server."""
        if self._server:
            self._server.shutdown()
            self._server = None

        if self._server_thread:
            self._server_thread.join(timeout=2.0)
            self._server_thread = None

        logger.info("OSC server stopped")

    def _handle_scale_update(self, address: str, *args):
        """Handle scale update from PitchGrid plugin."""
        logger.debug(f"Received scale update: {args}")

        # Parse scale data based on PitchGrid's OSC format
        # This will depend on the actual format from the plugin
        # Placeholder for now
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

        # Parse note mapping data
        # Format TBD based on plugin implementation
        mapping_data = {
            'address': address,
            'args': args
        }

        if self.on_note_mapping:
            self.on_note_mapping(mapping_data)

    def _handle_playing_notes(self, address: str, *args):
        """Handle currently playing notes (for visualization)."""
        logger.debug(f"Received playing notes: {args}")
        # This can be used to highlight notes in the UI

    def send_unmapped_notes(self, host: str, port: int, unmapped_coords: list):
        """
        Send unmapped note coordinates back to PitchGrid (optional feature).

        Args:
            host: PitchGrid plugin host
            port: PitchGrid plugin port
            unmapped_coords: List of (x, y) coordinates that couldn't be mapped
        """
        # TODO: Implement if needed
        pass
