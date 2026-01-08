"""
High-priority MIDI message handling with minimal latency.

This module runs in a dedicated thread with high priority to ensure
low-latency MIDI message passing and note remapping.
"""

import logging
import queue
import threading
import time
from typing import Callable, Dict, Optional, Tuple

import rtmidi
from rtmidi.midiconstants import NOTE_OFF, NOTE_ON

logger = logging.getLogger(__name__)


class MIDIHandler:
    """Handles MIDI input/output with real-time note remapping."""

    def __init__(self, virtual_device_name: str = "PG Isomap"):
        self.virtual_device_name = virtual_device_name

        # MIDI ports
        self.midi_in: Optional[rtmidi.MidiIn] = None
        self.midi_out: Optional[rtmidi.MidiOut] = None
        self.controller_port: Optional[rtmidi.MidiIn] = None

        # Note mapping table (logical_x, logical_y) -> midi_note
        self.note_mapping: Dict[Tuple[int, int], int] = {}
        # Reverse mapping: controller_note -> (logical_x, logical_y)
        self.reverse_mapping: Dict[int, Tuple[int, int]] = {}

        # Thread control
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._message_queue: queue.Queue = queue.Queue(maxsize=1000)

        # Statistics
        self.messages_processed = 0
        self.notes_remapped = 0

    def initialize_virtual_port(self) -> bool:
        """Create virtual MIDI output port."""
        try:
            self.midi_out = rtmidi.MidiOut()
            self.midi_out.open_virtual_port(self.virtual_device_name)
            logger.info(f"Virtual MIDI port '{self.virtual_device_name}' created")
            return True
        except Exception as e:
            logger.error(f"Failed to create virtual MIDI port: {e}")
            return False

    def connect_to_controller(self, port_name: str) -> bool:
        """Connect to a physical controller."""
        try:
            if self.controller_port:
                self.disconnect_controller()

            self.midi_in = rtmidi.MidiIn()

            # Find the port
            ports = self.midi_in.get_ports()
            port_index = None
            for i, p in enumerate(ports):
                if port_name in p:
                    port_index = i
                    break

            if port_index is None:
                logger.error(f"Controller port '{port_name}' not found")
                return False

            self.midi_in.open_port(port_index)
            self.midi_in.set_callback(self._midi_callback)
            self.controller_port = self.midi_in

            logger.info(f"Connected to controller: {port_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to controller: {e}")
            return False

    def disconnect_controller(self):
        """Disconnect from controller."""
        if self.controller_port:
            try:
                self.controller_port.close_port()
            except Exception as e:
                logger.error(f"Error closing controller port: {e}")
            finally:
                self.controller_port = None
                self.midi_in = None

    def _midi_callback(self, event, data=None):
        """MIDI input callback - runs in rtmidi's thread."""
        message, deltatime = event

        # Put message in queue for processing thread
        try:
            self._message_queue.put_nowait((message, time.time()))
        except queue.Full:
            logger.warning("MIDI message queue full, dropping message")

    def update_note_mapping(
        self,
        mapping: Dict[Tuple[int, int], int],
        reverse_mapping: Dict[int, Tuple[int, int]]
    ):
        """
        Update the note mapping table (thread-safe).

        Args:
            mapping: (logical_x, logical_y) -> pitchgrid_note
            reverse_mapping: controller_note -> (logical_x, logical_y)
        """
        self.note_mapping = mapping.copy()
        self.reverse_mapping = reverse_mapping.copy()
        logger.debug(f"Note mapping updated: {len(mapping)} mappings")

    def start(self):
        """Start the MIDI processing thread."""
        if self._running:
            logger.warning("MIDI handler already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._processing_loop, daemon=True)
        self._thread.name = "MIDI-Processing"
        self._thread.start()

        logger.info("MIDI handler started")

    def stop(self):
        """Stop the MIDI processing thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("MIDI handler stopped")

    def _processing_loop(self):
        """
        Main processing loop - runs in dedicated high-priority thread.

        This loop:
        1. Reads MIDI messages from queue
        2. Remaps note messages using pre-computed mapping table
        3. Passes through all other messages unchanged
        4. Sends to virtual MIDI output
        """
        # Try to increase thread priority (platform-specific)
        try:
            import os
            if hasattr(os, 'nice'):
                os.nice(-10)  # Unix-like systems
        except Exception:
            pass  # Not critical if priority adjustment fails

        while self._running:
            try:
                # Block with timeout to allow checking _running flag
                message, timestamp = self._message_queue.get(timeout=0.1)

                if not self.midi_out:
                    continue

                # Fast path: check if this is a note message
                if len(message) >= 3:
                    status = message[0] & 0xF0

                    if status == NOTE_ON or status == NOTE_OFF:
                        # Note message - remap if possible
                        channel = message[0] & 0x0F
                        controller_note = message[1]
                        velocity = message[2]

                        # Look up in reverse mapping
                        if controller_note in self.reverse_mapping:
                            logical_coord = self.reverse_mapping[controller_note]

                            # Look up mapped note
                            if logical_coord in self.note_mapping:
                                mapped_note = self.note_mapping[logical_coord]

                                # Send remapped note
                                remapped_message = [message[0], mapped_note, velocity]
                                self.midi_out.send_message(remapped_message)
                                self.notes_remapped += 1
                                self.messages_processed += 1
                                continue

                # Pass through unchanged (non-note or unmapped)
                self.midi_out.send_message(message)
                self.messages_processed += 1

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error processing MIDI message: {e}")

    def get_available_controllers(self) -> list[str]:
        """Get list of available MIDI input ports."""
        try:
            midi_in = rtmidi.MidiIn()
            ports = midi_in.get_ports()
            del midi_in
            return ports
        except Exception as e:
            logger.error(f"Error getting MIDI ports: {e}")
            return []

    def shutdown(self):
        """Clean shutdown."""
        self.stop()
        self.disconnect_controller()
        if self.midi_out:
            self.midi_out.close_port()
            self.midi_out = None
