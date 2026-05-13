"""
High-priority MIDI message handling with minimal latency.

This module runs in a dedicated thread with high priority to ensure
low-latency MIDI message passing and note remapping.
"""

import logging
import queue
import re
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

import rtmidi
from rtmidi.midiconstants import NOTE_OFF, NOTE_ON

if TYPE_CHECKING:
    from .controller_config import ACKMessagingConfig

logger = logging.getLogger(__name__)


def find_best_matching_port(search_string: str, available_ports: List[str]) -> Optional[str]:
    """
    Find the best matching port name using substring matching.

    Returns the shortest port name that contains the search_string as a substring.
    This handles platform differences where port names vary (e.g., macOS adds prefixes,
    Windows adds MIDIIN/MIDIOUT prefixes).

    Args:
        search_string: The substring to search for in port names
        available_ports: List of available port names

    Returns:
        The shortest matching port name, or None if no match found
    """
    matching_ports = [port for port in available_ports if search_string in port]

    if not matching_ports:
        return None

    # Return the shortest matching port name
    return min(matching_ports, key=len)


class MIDIHandler:
    """Handles MIDI input/output with real-time note remapping."""

    def __init__(self, virtual_device_name: str = "PitchGrid Mapper"):
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
        # Reverse mapping: (channel, controller_note) -> (logical_x, logical_y)
        self.reverse_mapping: Dict[Tuple[int, int], Tuple[int, int]] = {}
        # Whether to use incoming channel for reverse lookup (True for Lumatone, False for MPE/LinnStrument)
        self.use_channel_for_lookup: bool = False

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

        # ACK-based messaging support
        self._ack_response_queue: queue.Queue = queue.Queue(maxsize=100)
        self._waiting_for_ack = False
        self._ack_lock = threading.Lock()

        # Track currently playing notes (note_number -> (logical_coord, channel))
        # Used to send note-off messages when layout changes
        # For MPE, we must send note-off on the same channel as note-on
        self._playing_notes: Dict[int, Tuple[Tuple[int, int], int]] = {}
        self._playing_notes_lock = threading.Lock()

        # Sustain pedal (CC64) state. The MIDIHandler observes incoming
        # CC64 messages and tracks the held/released state. Deferring the
        # visual NoteOff (so pads stay lit while the pedal is held) is the
        # *app's* responsibility — see `on_sustain_change` and the App's
        # `_handle_sustain_change`. Doing it at the app level means the same
        # sustain semantics apply to NoteOffs that arrive via this MIDI
        # handler AND to those that come back through the OSC plugin loop.
        self._sustain_held: bool = False
        self._sustain_lock = threading.Lock()
        # Signature: on_sustain_change(held: bool)
        self.on_sustain_change: Optional[Callable[[bool], None]] = None

    def initialize_virtual_port(self) -> bool:
        """
        Create or connect to virtual MIDI output port named 'PitchGrid Mapper'.

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

                # Find best matching port using substring matching
                matched_port_name = find_best_matching_port(output_port_name, in_ports)

                if matched_port_name is None:
                    logger.error(f"Controller output port matching '{output_port_name}' not found")
                    logger.debug(f"Available input ports: {in_ports}")
                    return False

                # Find the index of the matched port
                in_port_index = in_ports.index(matched_port_name)

                self.midi_in.open_port(in_port_index)
                # Enable SysEx reception (rtmidi filters SysEx by default!)
                # Required for ACK-based messaging with controllers like Lumatone
                self.midi_in.ignore_types(sysex=False, timing=True, active_sense=True)
                self.midi_in.set_callback(self._midi_callback)
                self.controller_port = self.midi_in
                self.connected_port_name = matched_port_name
                logger.info(f"Listening to controller on: {matched_port_name} (matched '{output_port_name}')")

            # Open MIDI output to controller (controller's input port) for setup messages
            if input_port_name:
                self.controller_out = rtmidi.MidiOut()
                out_ports = self.controller_out.get_ports()

                # Find best matching port using substring matching
                matched_out_port_name = find_best_matching_port(input_port_name, out_ports)

                if matched_out_port_name is not None:
                    out_port_index = out_ports.index(matched_out_port_name)
                    self.controller_out.open_port(out_port_index)
                    logger.info(f"Sending setup messages to controller on: {matched_out_port_name} (matched '{input_port_name}')")
                else:
                    logger.warning(f"Controller input port matching '{input_port_name}' not found, setup messages will not work")
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

        # Check if this is a SysEx response and we're waiting for ACK
        if message and message[0] == 0xF0:
            if self._waiting_for_ack:
                # This is an ACK response - route to ACK queue
                try:
                    self._ack_response_queue.put_nowait(message)
                    # Don't log here - logged in _send_single_with_ack when processed
                    return  # Don't process SysEx responses through normal path
                except queue.Full:
                    logger.error("⚠️  ACK response queue full, dropping response!")
            else:
                # SysEx received but not waiting for ACK - might be unsolicited
                msg_hex = ' '.join(f'{b:02X}' for b in message[:20])
                if len(message) > 20:
                    msg_hex += f"... ({len(message)} bytes)"
                logger.debug(f"Received SysEx (not ACK): {msg_hex}")

        # Put message in queue for processing thread
        try:
            self._message_queue.put_nowait((message, time.time()))
        except queue.Full:
            logger.warning("MIDI message queue full, dropping message")

    def is_sustain_held(self) -> bool:
        """Whether the sustain pedal (CC64) is currently asserted."""
        with self._sustain_lock:
            return self._sustain_held

    def _handle_sustain_cc(self, cc_value: int) -> None:
        """Update sustain state on incoming CC64. Notify the app via the
        on_sustain_change callback so it can release any visuals it has
        been holding through the pedal."""
        new_held = cc_value >= 64
        with self._sustain_lock:
            was_held = self._sustain_held
            self._sustain_held = new_held
        if was_held != new_held:
            logger.info(f"sustain CC64 -> {'on' if new_held else 'off'}")
            if self.on_sustain_change:
                try:
                    self.on_sustain_change(new_held)
                except Exception as e:
                    logger.error(f"Error in sustain-change callback: {e}")

    def inject_message(self, message: List[int], timestamp: Optional[float] = None) -> None:
        """Push a synthesized MIDI message into the same input queue rtmidi feeds.

        Used by non-MIDI input sources (e.g. the Wooting analog bridge) so the
        rest of the pipeline (reverse mapping, scale-aware retuning, UI events)
        works identically to a real MIDI controller.
        """
        ts = timestamp if timestamp is not None else time.time()
        try:
            self._message_queue.put_nowait((list(message), ts))
        except queue.Full:
            logger.warning("MIDI message queue full, dropping injected message")

    def update_note_mapping(
        self,
        mapping: Dict[Tuple[int, int], int],
        reverse_mapping: Dict[Tuple[int, int], Tuple[int, int]],
        use_channel_for_lookup: bool = False
    ):
        """
        Update the note mapping table (thread-safe).

        Args:
            mapping: (logical_x, logical_y) -> pitchgrid_note
            reverse_mapping: (channel, controller_note) -> (logical_x, logical_y)
            use_channel_for_lookup: If True, use (channel, note) for reverse lookup.
                If False, use (0, note) for lookup (for MPE controllers where
                the same note on any channel maps to the same pad).
        """
        self.note_mapping = mapping.copy()
        self.reverse_mapping = reverse_mapping.copy()
        self.use_channel_for_lookup = use_channel_for_lookup
        logger.debug(f"Note mapping updated: {len(mapping)} mappings, channel_lookup={use_channel_for_lookup}")

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

                # Sustain pedal handling (CC64). Works for any controller —
                # the same code path catches Wooting's spacebar-emitted CC64
                # and a hardware sustain pedal plugged into a MIDI keyboard.
                if (
                    status == 0xB0
                    and len(message) >= 3
                    and message[1] == 0x40
                ):
                    self._handle_sustain_cc(message[2])
                    # Still pass the CC64 message through to the virtual
                    # output so DAWs / synths respect it.
                    self.midi_out.send_message(message)
                    self.messages_processed += 1
                    continue

                if len(message) >= 3 and (status == NOTE_ON or status == NOTE_OFF):
                    # Note message - remap if possible
                    channel = message[0] & 0x0F
                    controller_note = message[1]
                    velocity = message[2]
                    note_type = "note_on" if status == NOTE_ON else "note_off"

                    # Look up in reverse mapping
                    # For controllers with channelAssign (e.g., Lumatone), use incoming channel
                    # For others (e.g., MPE controllers), use channel 0
                    lookup_channel = channel if self.use_channel_for_lookup else 0
                    reverse_key = (lookup_channel, controller_note)
                    if reverse_key in self.reverse_mapping:
                        logical_coord = self.reverse_mapping[reverse_key]

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

                            # Notify UI about note event. Sustain-aware
                            # deferral lives in the app's _handle_note_event
                            # so the same logic also applies to NoteOffs
                            # echoed back via OSC from the PitchGrid plugin.
                            is_note_on = (status == NOTE_ON and velocity > 0)
                            if self.on_note_event:
                                try:
                                    self.on_note_event(logical_coord[0], logical_coord[1], is_note_on)
                                except Exception as e:
                                    logger.error(f"Error in note event callback: {e}")

                            # Track playing notes with channel (for MPE support)
                            with self._playing_notes_lock:
                                if is_note_on:
                                    self._playing_notes[mapped_note] = (logical_coord, channel)
                                else:
                                    self._playing_notes.pop(mapped_note, None)

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

    def send_note_on(self, note: int, velocity: int = 100, channel: int = 0, logical_coord: Optional[Tuple[int, int]] = None):
        """
        Send a MIDI note-on message to the virtual output.

        Args:
            note: MIDI note number (0-127)
            velocity: Note velocity (0-127)
            channel: MIDI channel (0-15)
            logical_coord: Optional logical coordinate for tracking playing notes
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

            # Track playing notes with channel (for MPE support)
            if logical_coord is not None:
                with self._playing_notes_lock:
                    self._playing_notes[note] = (logical_coord, channel)

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

            # Remove from playing notes
            with self._playing_notes_lock:
                self._playing_notes.pop(note, None)

            logger.debug(f"Sent note-off: note={note}, channel={channel}")
        except Exception as e:
            logger.error(f"Error sending note-off: {e}")

    def stop_all_playing_notes(self):
        """
        Send note-off messages for all currently playing notes.

        This should be called when the layout changes to prevent stuck notes.
        For MPE support, note-off is sent on the same channel as note-on.
        """
        if not self.midi_out:
            return

        with self._playing_notes_lock:
            # Copy the dict items (note -> (logical_coord, channel))
            notes_to_stop = list(self._playing_notes.items())
            self._playing_notes.clear()

        if notes_to_stop:
            logger.info(f"Stopping {len(notes_to_stop)} playing notes due to layout change")
            for note, (logical_coord, channel) in notes_to_stop:
                try:
                    message = [NOTE_OFF | channel, note, 0]
                    self.midi_out.send_message(message)
                    logger.debug(f"Sent note-off: note={note}, channel={channel}")
                except Exception as e:
                    logger.error(f"Error sending note-off for note {note} on channel {channel}: {e}")

    def stop_notes_with_changed_mapping(self, new_mapping: Dict[Tuple[int, int], int]):
        """
        Send note-off only for notes whose mapping changed under the new layout.

        For each playing note, checks if its logical coordinate still maps to the
        same MIDI note. Only sends note_off if the mapping changed or the coordinate
        is no longer mapped.

        Args:
            new_mapping: The new (logical_x, logical_y) -> midi_note mapping
        """
        if not self.midi_out:
            return

        with self._playing_notes_lock:
            notes_to_stop = []
            notes_to_keep = {}

            for old_note, (logical_coord, channel) in self._playing_notes.items():
                new_note = new_mapping.get(logical_coord)
                if new_note is None or new_note != old_note:
                    # Mapping changed or coord no longer mapped - need to stop this note
                    notes_to_stop.append((old_note, logical_coord, channel))
                else:
                    # Mapping unchanged - keep tracking this note
                    notes_to_keep[old_note] = (logical_coord, channel)

            self._playing_notes = notes_to_keep

        if notes_to_stop:
            logger.info(f"Stopping {len(notes_to_stop)} notes due to mapping change (keeping {len(notes_to_keep)})")
            for note, logical_coord, channel in notes_to_stop:
                try:
                    message = [NOTE_OFF | channel, note, 0]
                    self.midi_out.send_message(message)
                    logger.debug(f"Sent note-off: note={note}, channel={channel}, coord={logical_coord}")
                except Exception as e:
                    logger.error(f"Error sending note-off for note {note} on channel {channel}: {e}")

    def cancel_color_send(self) -> int:
        """
        Cancel any ongoing color send operation.

        Returns:
            The new generation number to use for the next send operation.
        """
        with self._color_send_lock:
            self._color_send_generation += 1
            return self._color_send_generation

    def send_with_ack(
        self,
        data: List[int],
        ack_config: "ACKMessagingConfig",
        generation: Optional[int] = None
    ) -> bool:
        """
        Send MIDI messages with ACK-based flow control.

        Each SysEx message is sent and we wait for an ACK response before
        sending the next message. Non-SysEx messages are sent without waiting.

        Args:
            data: List of MIDI bytes to send (may contain multiple messages)
            ack_config: ACK messaging configuration with response types, timeout, and response position
            generation: If provided, the send will be cancelled if the generation number
                        has changed (indicating a newer send operation has started)

        Returns:
            True if all messages sent successfully, False if aborted or timed out
        """
        if not self.controller_out:
            logger.warning("Cannot send MIDI to controller: no output port connected")
            return False

        if not data:
            return True

        try:
            # Parse MIDI stream into individual messages
            messages = self._parse_midi_messages(data)

            # Statistics tracking
            stats = {"sent": 0, "ack": 0, "nack": 0, "busy": 0, "timeout": 0, "error": 0}

            logger.info(f"▶ Starting ACK-based send: {len(messages)} messages, timeout={ack_config.timeout_ms}ms")

            for i, msg in enumerate(messages):
                # Check for cancellation if generation is provided
                if generation is not None:
                    with self._color_send_lock:
                        if self._color_send_generation != generation:
                            logger.debug(f"ACK send cancelled (generation {generation} != {self._color_send_generation})")
                            return False

                # For SysEx messages, use ACK-based sending
                if msg and msg[0] == 0xF0:
                    result, outcome = self._send_single_with_ack(msg, ack_config, i + 1, len(messages))
                    stats["sent"] += 1
                    if outcome in stats:
                        stats[outcome] += 1

                    if not result:
                        logger.error(f"✗ ACK send failed at message {i+1}/{len(messages)}")
                        logger.error(f"  Stats: {stats['sent']} sent, {stats['ack']} ACK, {stats['nack']} NACK, {stats['busy']} BUSY, {stats['timeout']} timeout, {stats['error']} error")
                        return False
                else:
                    # Non-SysEx messages sent without ACK
                    self.controller_out.send_message(msg)

            logger.info(f"✓ ACK send completed: {stats['sent']} messages, {stats['ack']} ACK, {stats['busy']} BUSY retries, {stats['timeout']} timeouts")
            return True

        except Exception as e:
            logger.error(f"Error in ACK-based send: {e}", exc_info=True)
            return False

    def _send_single_with_ack(
        self,
        msg: List[int],
        ack_config: "ACKMessagingConfig",
        msg_num: int = 0,
        total_msgs: int = 0
    ) -> Tuple[bool, str]:
        """
        Send a single SysEx message and wait for ACK response.

        Args:
            msg: The SysEx message to send
            ack_config: ACK messaging configuration (includes response_position)
            msg_num: Message number (for logging)
            total_msgs: Total number of messages (for logging)

        Returns:
            (success, outcome) where success is True if ACK received, outcome is "ack"/"nack"/"busy"/"timeout"/"error"
        """
        max_retries = 10  # Prevent infinite retry loops
        retries = 0

        # Format message for logging (first 20 bytes)
        msg_hex = ' '.join(f'{b:02X}' for b in msg[:20])
        if len(msg) > 20:
            msg_hex += f"... ({len(msg)} bytes)"

        msg_label = f"[{msg_num}/{total_msgs}]" if msg_num > 0 else ""

        while retries < max_retries:
            # Clear the response queue before sending
            while not self._ack_response_queue.empty():
                try:
                    self._ack_response_queue.get_nowait()
                except queue.Empty:
                    break

            # Start listening for ACK
            with self._ack_lock:
                self._waiting_for_ack = True

            try:
                # Send the message
                retry_label = f" retry {retries}" if retries > 0 else ""
                logger.info(f"  → {msg_label}{retry_label} SEND: {msg_hex}")
                self.controller_out.send_message(msg)

                # Wait for response
                timeout_seconds = ack_config.timeout_ms / 1000.0
                try:
                    response = self._ack_response_queue.get(timeout=timeout_seconds)

                    # Format response for logging
                    resp_hex = ' '.join(f'{b:02X}' for b in response[:20])
                    if len(response) > 20:
                        resp_hex += f"... ({len(response)} bytes)"

                    # Extract response type from the configured position
                    # Response format: F0 <mfr 3 bytes> <board> <cmd> <status> ... F7
                    # The position is in the SysEx data (after F0)
                    response_pos = ack_config.response_position
                    if len(response) > response_pos:
                        response_value = response[response_pos]
                        action = ack_config.get_action_for_value(response_value)

                        # Find response type name for logging
                        resp_name = next((rt.name for rt in ack_config.response_types if rt.value == response_value), f"0x{response_value:02X}")
                        logger.info(f"  ← {msg_label} RECV: {resp_hex}")
                        logger.info(f"     Response[{response_pos}]=0x{response_value:02X} ({resp_name}) → action={action}")

                        if action is None:
                            logger.error(f"  ✗ Unknown ACK response value: 0x{response_value:02X}")
                            return (False, "error")

                        if action == 'next':
                            return (True, "ack")  # Success, proceed to next message

                        elif action == 'abort':
                            logger.error(f"  ✗ Response indicates abort ({resp_name})")
                            return (False, "nack")

                        elif action.startswith('delay('):
                            # Parse delay time from "delay(500)"
                            match = re.match(r'delay\((\d+)\)', action)
                            if match:
                                delay_ms = int(match.group(1))
                                logger.warning(f"  ⏸ BUSY response, retrying after {delay_ms}ms (retry {retries+1}/{max_retries})")
                                time.sleep(delay_ms / 1000.0)
                                retries += 1
                                continue  # Retry the same message
                            else:
                                logger.error(f"  ✗ Invalid delay action format: {action}")
                                return (False, "error")
                        else:
                            logger.error(f"  ✗ Unknown action: {action}")
                            return (False, "error")
                    else:
                        logger.error(f"  ✗ Response too short ({len(response)} bytes) to extract status at position {response_pos}")
                        logger.error(f"     Response was: {resp_hex}")
                        return (False, "error")

                except queue.Empty:
                    logger.error(f"  ✗ {msg_label} TIMEOUT after {ack_config.timeout_ms}ms - NO RESPONSE")
                    return (False, "timeout")

            finally:
                with self._ack_lock:
                    self._waiting_for_ack = False

        logger.error(f"✗ Max retries ({max_retries}) exceeded for ACK-based send")
        return (False, "busy")

    def send_raw_bytes(
        self,
        data: List[int],
        delay_ms: float = 1.5,
        generation: Optional[int] = None,
        ack_config: Optional["ACKMessagingConfig"] = None
    ):
        """
        Send arbitrary MIDI message bytes TO THE CONTROLLER.

        Used for controller setup messages (colors, note assignments, etc).

        Handles:
        - SysEx messages (start with 0xF0, end with 0xF7): sent as single message
        - Multiple channel messages: parsed and sent separately respecting MIDI boundaries
        - Single messages: sent as-is

        If ack_config is provided, uses ACK-based sending for reliable communication.
        Otherwise, uses delay-based sending.

        Args:
            data: List of MIDI bytes to send
            delay_ms: Delay in milliseconds between consecutive messages (default 1.5ms)
            generation: If provided, the send will be cancelled if the generation number
                        has changed (indicating a newer send operation has started)
            ack_config: If provided, uses ACK-based sending instead of delay-based
        """
        if not self.controller_out:
            logger.warning("Cannot send MIDI to controller: no output port connected")
            return

        if not data:
            return

        # Use ACK-based sending if config is provided
        if ack_config is not None:
            success = self.send_with_ack(data, ack_config, generation=generation)
            if success:
                return  # ACK-based send succeeded
            else:
                # ACK-based send failed - fall back to delay-based sending
                logger.warning("⚠️  ACK-based send FAILED - falling back to delay-based sending")
                logger.warning(f"   This may cause unreliable communication. Check MIDI connections and controller firmware.")
                # Continue to delay-based sending below

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
