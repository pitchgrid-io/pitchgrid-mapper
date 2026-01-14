"""
High-priority MIDI message handling with minimal latency.

This module runs in a dedicated thread with high priority to ensure
low-latency MIDI message passing and note remapping.
"""

import logging
import queue
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

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
        self.controller_out: Optional[rtmidi.MidiOut] = None  # Output TO controller for setup messages
        self.connected_port_name: Optional[str] = None  # Name of currently connected controller port
        self.virtual_port_name: Optional[str] = None  # Name of connected virtual MIDI output port

        # Note mapping table (logical_x, logical_y) -> midi_note
        self.note_mapping: Dict[Tuple[int, int], int] = {}
        # Reverse mapping: controller_note -> (logical_x, logical_y)
        self.reverse_mapping: Dict[int, Tuple[int, int]] = {}

        # Callback for getting scale coordinates
        self.get_scale_coord: Optional[Callable[[int, int], Optional[Tuple[int, int]]]] = None

        # Callback for note events (for UI highlighting)
        # Signature: on_note_event(logical_x, logical_y, note_on: bool)
        self.on_note_event: Optional[Callable[[int, int, bool], None]] = None

        # Thread control
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._message_queue: queue.Queue = queue.Queue(maxsize=1000)

        # Statistics
        self.messages_processed = 0
        self.notes_remapped = 0

        # Color send cancellation
        self._color_send_generation = 0
        self._color_send_lock = threading.Lock()

    def initialize_virtual_port(self) -> bool:
        """
        Create or connect to virtual MIDI output port named 'PitchGrid ICM'.

        On macOS/Linux:
        - First checks if port already exists, connects to it
        - If not, creates a new virtual port

        On Windows:
        - Looks for existing port with exact name
        - If not found, returns False and shows user message
        """
        try:
            self.midi_out = rtmidi.MidiOut()
            available_ports = self.midi_out.get_ports()

            # First, try to find existing port with our exact name
            for port_idx, port_name in enumerate(available_ports):
                if self.virtual_device_name in port_name:
                    try:
                        self.midi_out.open_port(port_idx)
                        self.virtual_port_name = port_name
                        logger.info(f"Connected to existing virtual MIDI port: {port_name}")
                        return True
                    except Exception as open_err:
                        logger.warning(f"Failed to open port '{port_name}': {open_err}")

            # Port doesn't exist, try to create it (works on macOS/Linux only)
            try:
                self.midi_out.open_virtual_port(self.virtual_device_name)
                self.virtual_port_name = self.virtual_device_name
                logger.info(f"Created virtual MIDI port '{self.virtual_device_name}'")
                return True
            except Exception as e:
                # Virtual port creation failed - this is expected on Windows
                logger.debug(f"Could not create virtual port: {e}")

                # On Windows, we need the user to create the port manually
                self.virtual_port_name = None
                logger.error(f"Virtual MIDI port '{self.virtual_device_name}' not found")
                logger.error("Please create a virtual MIDI port using loopMIDI:")
                logger.error(f"  1. Download loopMIDI: https://www.tobias-erichsen.de/software/loopmidi.html")
                logger.error(f"  2. Install and run loopMIDI")
                logger.error(f"  3. Create a new port with the exact name: {self.virtual_device_name}")
                logger.error(f"  4. Restart this application")
                return False

        except Exception as e:
            logger.error(f"Failed to initialize MIDI output: {e}")
            self.virtual_port_name = None
            return False

    def connect_to_controller(
        self,
        output_port_name: Optional[str],
        input_port_name: Optional[str] = None
    ) -> bool:
        """
        Connect to a physical controller.

        Args:
            output_port_name: MIDI port from which controller sends notes (we listen here).
                              If None, no input connection is made.
            input_port_name: MIDI port to which we send setup/color messages.
                             If None, defaults to output_port_name for devices with single port.

        Returns:
            True if connection successful
        """
        try:
            if self.controller_port:
                self.disconnect_controller()

            # If no input port specified, assume same as output (single-port device)
            if input_port_name is None:
                input_port_name = output_port_name

            # Open MIDI input from controller (controller's output port)
            if output_port_name:
                self.midi_in = rtmidi.MidiIn()
                in_ports = self.midi_in.get_ports()
                in_port_index = None
                for i, p in enumerate(in_ports):
                    if output_port_name in p:
                        in_port_index = i
                        break

                if in_port_index is None:
                    logger.error(f"Controller output port '{output_port_name}' not found in available ports")
                    logger.debug(f"Available input ports: {in_ports}")
                    return False

                self.midi_in.open_port(in_port_index)
                self.midi_in.set_callback(self._midi_callback)
                self.controller_port = self.midi_in
                self.connected_port_name = output_port_name
                logger.info(f"Listening to controller on: {output_port_name}")

            # Open MIDI output to controller (controller's input port) for setup messages
            if input_port_name:
                self.controller_out = rtmidi.MidiOut()
                out_ports = self.controller_out.get_ports()
                out_port_index = None
                for i, p in enumerate(out_ports):
                    if input_port_name in p:
                        out_port_index = i
                        break

                if out_port_index is not None:
                    self.controller_out.open_port(out_port_index)
                    logger.info(f"Sending setup messages to controller on: {input_port_name}")
                else:
                    logger.warning(f"Controller input port '{input_port_name}' not found, setup messages will not work")
                    logger.debug(f"Available output ports: {out_ports}")
                    self.controller_out = None

            return True

        except Exception as e:
            logger.error(f"Failed to connect to controller: {e}")
            return False

    def is_controller_connected(self) -> bool:
        """Check if a controller is connected via MIDI."""
        return self.controller_port is not None

    def disconnect_controller(self):
        """Disconnect from controller."""
        if self.controller_port:
            try:
                self.controller_port.close_port()
            except Exception as e:
                logger.error(f"Error closing controller input port: {e}")
            finally:
                self.controller_port = None
                self.midi_in = None

        if self.controller_out:
            try:
                self.controller_out.close_port()
            except Exception as e:
                logger.error(f"Error closing controller output port: {e}")
            finally:
                self.controller_out = None

        self.connected_port_name = None

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
                status = message[0] & 0xF0 if message else 0

                if len(message) >= 3 and (status == NOTE_ON or status == NOTE_OFF):
                    # Note message - remap if possible
                    channel = message[0] & 0x0F
                    controller_note = message[1]
                    velocity = message[2]
                    note_type = "note_on" if status == NOTE_ON else "note_off"

                    # Look up in reverse mapping
                    if controller_note in self.reverse_mapping:
                        logical_coord = self.reverse_mapping[controller_note]

                        # Get scale coordinate if callback is available (before checking if mapped)
                        scale_coord_str = "?"
                        if self.get_scale_coord:
                            try:
                                scale_coord = self.get_scale_coord(logical_coord[0], logical_coord[1])
                                if scale_coord:
                                    scale_coord_str = f"({scale_coord[0]}, {scale_coord[1]})"
                            except Exception:
                                pass

                        # Look up mapped note
                        if logical_coord in self.note_mapping:
                            mapped_note = self.note_mapping[logical_coord]

                            # Log the full pipeline
                            # Format: device note_{on/off} {incoming_note} -> (lx, ly) -> (sx, sy) -> note {outgoing_note}
                            logger.info(
                                f"device {note_type} {controller_note} -> ({logical_coord[0]}, {logical_coord[1]}) -> {scale_coord_str} -> note {mapped_note}"
                            )

                            # Notify UI about note event
                            is_note_on = (status == NOTE_ON and velocity > 0)
                            if self.on_note_event:
                                try:
                                    self.on_note_event(logical_coord[0], logical_coord[1], is_note_on)
                                except Exception as e:
                                    logger.error(f"Error in note event callback: {e}")

                            # Send remapped note
                            remapped_message = [message[0], mapped_note, velocity]
                            self.midi_out.send_message(remapped_message)
                            self.notes_remapped += 1
                            self.messages_processed += 1
                            continue
                        else:
                            logger.info(
                                f"device {note_type} {controller_note} -> ({logical_coord[0]}, {logical_coord[1]}) -> {scale_coord_str} -> unmapped"
                            )
                    else:
                        logger.info(f"device {note_type} {controller_note} -> unmapped (no logical coord)")
                    # Don't pass through unmapped notes
                else:
                    # Pass through all non-note messages unchanged (CC, pitch bend, etc.)
                    self.midi_out.send_message(message)
                    self.messages_processed += 1

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error processing MIDI message: {e}")

    def get_available_controllers(self) -> list[str]:
        """Get list of available MIDI input ports.

        Note: rtmidi caches port lists internally. On macOS (CoreMIDI),
        sending a message through our virtual port seems to trigger a cache
        refresh. We send an ignored SysEx (identity request) before scanning.
        """
        try:
            # Now query ports - should be refreshed
            midi_in = rtmidi.MidiIn()
            in_ports = midi_in.get_ports()
            del midi_in

            return in_ports
        except Exception as e:
            logger.error(f"Error getting MIDI ports: {e}")
            return []

    def send_note_on(self, note: int, velocity: int = 100, channel: int = 0):
        """
        Send a MIDI note-on message to the virtual output.

        Args:
            note: MIDI note number (0-127)
            velocity: Note velocity (0-127)
            channel: MIDI channel (0-15)
        """
        if not self.midi_out:
            logger.warning("Cannot send note-on: virtual MIDI port not initialized")
            return

        if not (0 <= note <= 127 and 0 <= velocity <= 127 and 0 <= channel <= 15):
            logger.warning(f"Invalid MIDI parameters: note={note}, velocity={velocity}, channel={channel}")
            return

        try:
            message = [NOTE_ON | channel, note, velocity]
            self.midi_out.send_message(message)
            logger.debug(f"Sent note-on: note={note}, velocity={velocity}, channel={channel}")
        except Exception as e:
            logger.error(f"Error sending note-on: {e}")

    def send_note_off(self, note: int, channel: int = 0):
        """
        Send a MIDI note-off message to the virtual output.

        Args:
            note: MIDI note number (0-127)
            channel: MIDI channel (0-15)
        """
        if not self.midi_out:
            logger.warning("Cannot send note-off: virtual MIDI port not initialized")
            return

        if not (0 <= note <= 127 and 0 <= channel <= 15):
            logger.warning(f"Invalid MIDI parameters: note={note}, channel={channel}")
            return

        try:
            message = [NOTE_OFF | channel, note, 0]
            self.midi_out.send_message(message)
            logger.debug(f"Sent note-off: note={note}, channel={channel}")
        except Exception as e:
            logger.error(f"Error sending note-off: {e}")

    def cancel_color_send(self) -> int:
        """
        Cancel any ongoing color send operation.

        Returns:
            The new generation number to use for the next send operation.
        """
        with self._color_send_lock:
            self._color_send_generation += 1
            return self._color_send_generation

    def send_raw_bytes(self, data: List[int], delay_ms: float = 1.5, generation: Optional[int] = None):
        """
        Send arbitrary MIDI message bytes TO THE CONTROLLER.

        Used for controller setup messages (colors, note assignments, etc).

        Handles:
        - SysEx messages (start with 0xF0, end with 0xF7): sent as single message
        - Multiple channel messages: parsed and sent separately respecting MIDI boundaries
        - Single messages: sent as-is

        Args:
            data: List of MIDI bytes to send
            delay_ms: Delay in milliseconds between consecutive messages (default 1.5ms)
            generation: If provided, the send will be cancelled if the generation number
                        has changed (indicating a newer send operation has started)
        """
        if not self.controller_out:
            logger.warning("Cannot send MIDI to controller: no output port connected")
            return

        if not data:
            return

        try:
            # Parse MIDI stream into individual messages
            messages = self._parse_midi_messages(data)

            # Send each message with delay between them
            for i, msg in enumerate(messages):
                # Check for cancellation if generation is provided
                if generation is not None:
                    with self._color_send_lock:
                        if self._color_send_generation != generation:
                            logger.debug(f"Color send cancelled (generation {generation} != {self._color_send_generation})")
                            return

                self.controller_out.send_message(msg)
                # Add delay between messages (but not after the last one)
                if i < len(messages) - 1:
                    time.sleep(delay_ms / 1000.0)

            logger.debug(f"Sent {len(messages)} MIDI message(s) to controller ({len(data)} bytes total)")
        except Exception as e:
            logger.error(f"Error sending MIDI to controller: {e}")

    def _parse_midi_messages(self, data: List[int]) -> List[List[int]]:
        """
        Parse a byte stream into individual MIDI messages.

        Respects MIDI message boundaries:
        - SysEx: 0xF0 ... 0xF7 (variable length)
        - Channel messages: status + 1-2 data bytes
        - System messages: status + 0-2 data bytes

        Args:
            data: Raw MIDI bytes

        Returns:
            List of individual MIDI messages
        """
        messages = []
        i = 0

        while i < len(data):
            status = data[i]

            # SysEx message (0xF0 to 0xF7)
            if status == 0xF0:
                # Find the end of SysEx (0xF7)
                end = i + 1
                while end < len(data) and data[end] != 0xF7:
                    end += 1
                if end < len(data):
                    end += 1  # Include the 0xF7
                messages.append(data[i:end])
                i = end

            # Channel messages (0x80-0xEF)
            elif 0x80 <= status <= 0xEF:
                msg_type = status & 0xF0
                # Program Change (0xC0) and Channel Pressure (0xD0) have 1 data byte
                if msg_type in [0xC0, 0xD0]:
                    msg_len = 2  # status + 1 data byte
                else:
                    # Note On/Off, CC, Pitch Bend, etc. have 2 data bytes
                    msg_len = 3  # status + 2 data bytes

                messages.append(data[i:i+msg_len])
                i += msg_len

            # System Common messages (0xF1-0xF6)
            elif 0xF1 <= status <= 0xF6:
                if status in [0xF1, 0xF3]:  # Time Code, Song Select
                    msg_len = 2  # status + 1 data byte
                elif status == 0xF2:  # Song Position
                    msg_len = 3  # status + 2 data bytes
                else:  # 0xF4, 0xF5, 0xF6 (undefined, reserved, Tune Request)
                    msg_len = 1  # status only
                messages.append(data[i:i+msg_len])
                i += msg_len

            # System Real-Time messages (0xF8-0xFF) - single byte
            elif status >= 0xF8:
                messages.append([status])
                i += 1

            else:
                # Unknown or invalid status byte, skip it
                logger.warning(f"Unknown MIDI status byte: 0x{status:02X}, skipping")
                i += 1

        return messages

    def shutdown(self):
        """Clean shutdown."""
        self.stop()
        self.disconnect_controller()
        if self.midi_out:
            self.midi_out.close_port()
            self.midi_out = None
