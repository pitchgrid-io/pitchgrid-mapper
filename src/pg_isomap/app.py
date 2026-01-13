"""
Main application coordinator.

Manages the lifecycle of all components and coordinates between them.
"""

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Optional

from .coloring import DEFAULT_COLORING_SCHEME
from .config import settings
from .controller_config import ControllerConfig, ControllerManager
from .layouts import (
    IsomorphicLayout,
    LayoutCalculator,
    LayoutConfig,
    LayoutType,
    PianoLikeLayout,
    StringLikeLayout,
)
from .midi_handler import MIDIHandler
from .osc_handler import OSCHandler
from .tuning import TuningHandler

import scalatrix as sx

logger = logging.getLogger(__name__)


class PGIsomapApp:
    """Main application coordinator."""

    def __init__(self):
        # Components
        self.controller_manager = ControllerManager(settings.controller_config_dir)
        self.midi_handler = MIDIHandler(settings.virtual_midi_device_name)
        self.osc_handler = OSCHandler(
            host=settings.osc_host,
            server_port=settings.osc_server_port,
            client_port=settings.osc_client_port
        )
        self.tuning_handler = TuningHandler()

        # State
        self.current_controller: Optional[ControllerConfig] = None
        self.current_layout_config: LayoutConfig = LayoutConfig(layout_type=LayoutType.ISOMORPHIC)
        self.current_layout_calculator: Optional[LayoutCalculator] = None

        # WebAPI reference (set by WebAPI after initialization)
        self.web_api = None

        # Discovery thread
        self._discovery_thread: Optional[threading.Thread] = None
        self._discovery_running = False

        # Cached MIDI ports (updated from main thread to avoid CoreMIDI threading issues)
        self._cached_midi_ports: list[str] = []
        self._ports_lock = threading.Lock()

        # Setup callbacks
        self.osc_handler.on_scale_update = self._handle_scale_update
        self.osc_handler.on_note_mapping = self._handle_note_mapping
        self.osc_handler.on_connection_changed = self._handle_osc_connection_changed
        self.midi_handler.get_scale_coord = self._get_scale_coordinate
        self.midi_handler.on_note_event = self._handle_note_event

    def start(self):
        """Start the application."""
        logger.info("Starting PG Isomap...")

        # Try to auto-load computer keyboard config FIRST
        self._try_load_computer_keyboard()

        # Initialize virtual MIDI port
        if not self.midi_handler.initialize_virtual_port():
            logger.error("Failed to create virtual MIDI port")
            return False

        # Start MIDI processing
        self.midi_handler.start()

        # Start OSC server
        self.osc_handler.start()

        # Start controller discovery
        self._start_discovery()

        logger.info("PG Isomap started successfully")
        logger.info(f"Current controller: {self.current_controller.device_name if self.current_controller else 'None'}")
        return True

    def stop(self):
        """Stop the application."""
        logger.info("Stopping PG Isomap...")

        # Stop discovery
        self._stop_discovery()

        # Stop components
        self.midi_handler.shutdown()
        self.osc_handler.stop()

        logger.info("PG Isomap stopped")

    def _start_discovery(self):
        """Start controller discovery thread."""
        if self._discovery_running:
            return

        self._discovery_running = True
        self._discovery_thread = threading.Thread(
            target=self._discovery_loop,
            daemon=True
        )
        self._discovery_thread.name = "Controller-Discovery"
        self._discovery_thread.start()
        logger.info("Controller discovery thread started")

    def _stop_discovery(self):
        """Stop controller discovery thread."""
        self._discovery_running = False
        if self._discovery_thread:
            self._discovery_thread.join(timeout=5.0)
            self._discovery_thread = None

    def refresh_midi_ports(self):
        """
        Refresh the cached MIDI port list.

        IMPORTANT: This must be called from the main thread on macOS due to
        CoreMIDI run loop requirements. Call this periodically from the
        FastAPI event loop.
        """
        ports = self.midi_handler.get_available_controllers()
        with self._ports_lock:
            self._cached_midi_ports = ports
        logger.debug(f"Refreshed MIDI ports: {len(ports)} available")

    def get_cached_midi_ports(self) -> list[str]:
        """Get the cached list of MIDI ports (thread-safe)."""
        with self._ports_lock:
            return self._cached_midi_ports.copy()

    def _discovery_loop(self):
        """Periodically scan for controllers and broadcast status updates.

        Note: This runs in a separate thread but uses cached port list that
        is refreshed from the main thread (due to CoreMIDI requirements).
        """
        logger.debug("Discovery loop starting first iteration")
        last_detected = set()
        last_midi_connected = False

        while self._discovery_running:
            try:
                # Use cached ports (refreshed from main thread)
                available_ports = self.get_cached_midi_ports()
                logger.debug(f"Discovery scan: {len(available_ports)} cached ports")

                # Check what controllers are currently detected
                current_detected = set()
                for config_name in self.controller_manager.get_all_device_names():
                    config = self.controller_manager.get_config(config_name)
                    if config and config.controller_midi_output:
                        for port in available_ports:
                            if config.controller_midi_output.lower() in port.lower():
                                current_detected.add(config_name)
                                logger.debug(f"Matched {config_name} to port {port}")
                                break

                # Check if our connected port is still available
                # If not, auto-disconnect to keep state consistent
                if self.midi_handler.connected_port_name:
                    port_still_available = any(
                        self.midi_handler.connected_port_name.lower() in port.lower()
                        for port in available_ports
                    )
                    if not port_still_available:
                        logger.info(f"Connected port '{self.midi_handler.connected_port_name}' no longer available, disconnecting")
                        self.midi_handler.disconnect_controller()

                # Check current MIDI connection state
                current_midi_connected = self.midi_handler.is_controller_connected()

                # Log and broadcast status update if something changed
                if current_detected != last_detected or current_midi_connected != last_midi_connected:
                    logger.debug(f"State change: detected={current_detected} (was {last_detected}), midi_connected={current_midi_connected} (was {last_midi_connected})")
                    # Log device connection changes
                    newly_connected = current_detected - last_detected
                    newly_disconnected = last_detected - current_detected
                    for device in newly_connected:
                        logger.info(f"Controller detected: {device}")
                    for device in newly_disconnected:
                        logger.info(f"Controller disconnected: {device}")
                    if current_midi_connected != last_midi_connected:
                        logger.info(f"MIDI connection state: {'connected' if current_midi_connected else 'disconnected'}")

                    # Auto-connect if the currently selected controller just became available
                    # and we're not already connected via MIDI
                    if (self.current_controller and
                        self.current_controller.device_name in newly_connected and
                        not current_midi_connected and
                        self.current_controller.controller_midi_output):
                        logger.info(f"Auto-connecting to selected controller: {self.current_controller.device_name}")
                        self.connect_to_controller(self.current_controller.device_name)
                        # Update midi connected state after auto-connect
                        current_midi_connected = self.midi_handler.is_controller_connected()

                    last_detected = current_detected
                    last_midi_connected = current_midi_connected
                    if self.web_api:
                        self.web_api.broadcast_status_update()

            except Exception as e:
                logger.error(f"Error in discovery loop: {e}")

            time.sleep(settings.discovery_interval_seconds)

    def _try_load_computer_keyboard(self):
        """Try to load computer keyboard config on startup."""
        kb_config = self.controller_manager.get_config("Computer Keyboard")
        if kb_config:
            self.current_controller = kb_config
            self._recalculate_layout()
            logger.info("Computer keyboard config loaded")

    def connect_to_controller(self, device_name: str) -> bool:
        """
        Connect to a physical controller.

        Args:
            device_name: Name of the controller from configuration

        Returns:
            True if connection successful
        """
        # Get config
        config = self.controller_manager.get_config(device_name)
        if not config:
            logger.error(f"No configuration found for {device_name}")
            return False

        # Check if this controller has MIDI ports
        if not config.controller_midi_output:
            logger.warning(f"Controller {device_name} has no MIDI output port configured")
            return False

        # Try to connect using separate input/output ports
        if not self.midi_handler.connect_to_controller(
            output_port_name=config.controller_midi_output,
            input_port_name=config.controller_midi_input
        ):
            return False

        # Update current controller
        self.current_controller = config

        # Reset layout calculator to default when changing controllers
        self.current_layout_calculator = None

        # Send controller setup messages
        self._send_controller_setup()

        # Recalculate layout
        self._recalculate_layout()

        logger.info(f"Connected to {device_name}")
        return True

    def disconnect_controller(self):
        """Disconnect from current controller."""
        self.midi_handler.disconnect_controller()
        self.current_controller = None
        logger.info("Controller disconnected")

    def update_layout_config(self, config: LayoutConfig):
        """Update layout configuration and recalculate."""
        self.current_layout_config = config
        self._recalculate_layout()

    def apply_transformation(self, transformation_type: str) -> bool:
        """
        Apply a transformation to the current layout.

        Args:
            transformation_type: Transformation to apply (e.g., 'shift_left', 'rotate_right')

        Returns:
            True if transformation was applied successfully
        """
        if not self.current_layout_calculator:
            logger.warning("No layout calculator available")
            return False

        # Check if the layout calculator supports transformations
        if not hasattr(self.current_layout_calculator, 'apply_transformation'):
            logger.warning(f"Layout type {self.current_layout_config.layout_type} does not support transformations")
            return False

        try:
            # Apply the transformation
            self.current_layout_calculator.apply_transformation(transformation_type)

            # Recalculate the layout with the new transform
            self._recalculate_layout()

            logger.info(f"Applied transformation: {transformation_type}")
            return True

        except Exception as e:
            logger.error(f"Error applying transformation {transformation_type}: {e}")
            return False

    def _recalculate_layout(self):
        """Recalculate layout mapping and update MIDI handler."""
        if not self.current_controller:
            logger.warning("No controller loaded, cannot calculate layout")
            return

        # Create layout calculator only if we don't have one or if the type changed
        needs_new_calculator = (
            self.current_layout_calculator is None or
            (self.current_layout_config.layout_type == LayoutType.ISOMORPHIC and not isinstance(self.current_layout_calculator, IsomorphicLayout)) or
            (self.current_layout_config.layout_type == LayoutType.STRING_LIKE and not isinstance(self.current_layout_calculator, StringLikeLayout)) or
            (self.current_layout_config.layout_type == LayoutType.PIANO_LIKE and not isinstance(self.current_layout_calculator, PianoLikeLayout))
        )

        if needs_new_calculator:
            if self.current_layout_config.layout_type == LayoutType.ISOMORPHIC:
                # Pass default root coordinate from controller config
                default_root = self.current_controller.default_iso_root_coordinate
                self.current_layout_calculator = IsomorphicLayout(
                    self.current_layout_config,
                    default_root=default_root
                )
            elif self.current_layout_config.layout_type == LayoutType.STRING_LIKE:
                # Pass default root coordinate from controller config
                default_root = self.current_controller.default_iso_root_coordinate
                self.current_layout_calculator = StringLikeLayout(
                    self.current_layout_config,
                    default_root=default_root
                )
            elif self.current_layout_config.layout_type == LayoutType.PIANO_LIKE:
                # Pass default root coordinate from controller config
                default_root = self.current_controller.default_iso_root_coordinate
                self.current_layout_calculator = PianoLikeLayout(
                    self.current_layout_config,
                    default_root=default_root
                )
            else:
                logger.error(f"Unsupported layout type: {self.current_layout_config.layout_type}")
                return

        # Get logical coordinates from controller
        logical_coords = self.current_controller.get_logical_coordinates()

        # Calculate mapping using scale degrees from tuning handler
        mapping = self.current_layout_calculator.calculate_mapping(
            logical_coords,
            self.tuning_handler.scale_degrees,
            self.tuning_handler.steps,
            mos=self.tuning_handler.mos,
            coord_to_scale_index=self.tuning_handler.coord_to_scale_index
        )

        # Build reverse mapping (controller_note -> logical_coord)
        # Use controller's noteAssign function
        reverse_mapping = self.current_controller.build_controller_note_mapping()

        # Update MIDI handler
        self.midi_handler.update_note_mapping(mapping, reverse_mapping)

        logger.info(
            f"Layout recalculated: {len(mapping)} mapped pads, "
            f"{len(reverse_mapping)} reverse mappings"
        )

        # Broadcast updated status to WebSocket clients
        if self.web_api:
            self.web_api.broadcast_status_update()

        # Send color updates to physical controller (async to keep UI responsive)
        self._send_pad_colors_async()

    def _handle_scale_update(self, scale_data: dict):
        """Handle scale/tuning update from PitchGrid plugin."""
        logger.info("Received scale/tuning update from PitchGrid")

        # Cancel any ongoing color send operation immediately
        # This prevents interleaved MIDI messages when rapid tuning changes arrive
        self.midi_handler.cancel_color_send()

        # Check if this is tuning data from /pitchgrid/plugin/tuning
        args = scale_data.get('args', [])

        if len(args) >= 7:
            # Parse tuning data: (depth, mode, root_freq, stretch, skew, mode_offset, steps)
            try:
                depth, mode, root_freq, stretch, skew, mode_offset, steps = args[:7]

                # Update tuning handler
                self.tuning_handler.update_tuning(
                    depth=depth,
                    mode=mode,
                    root_freq=root_freq,
                    stretch=stretch,
                    skew=skew,
                    mode_offset=mode_offset,
                    steps=steps
                )

                # Recalculate layout with new scale degrees
                self._recalculate_layout()

            except Exception as e:
                logger.error(f"Error processing tuning data: {e}")
        else:
            logger.warning(f"Unexpected scale data format: {args}")

    def _handle_note_mapping(self, mapping_data: dict):
        """Handle note mapping update from PitchGrid plugin."""
        logger.info("Received note mapping from PitchGrid")

        # Parse and apply note mapping
        # TODO: Implement based on actual PitchGrid OSC format

    def _handle_osc_connection_changed(self, connected: bool):
        """Handle OSC connection state change."""
        logger.info(f"OSC connection state changed: {'connected' if connected else 'disconnected'}")

        # Broadcast updated status to WebSocket clients
        if self.web_api:
            self.web_api.broadcast_status_update()

    def _handle_note_event(self, logical_x: int, logical_y: int, note_on: bool):
        """Handle note event from MIDI handler (for UI highlighting)."""
        if self.web_api:
            self.web_api.broadcast_note_event(logical_x, logical_y, note_on)

    def _get_scale_coordinate(self, logical_x: int, logical_y: int) -> Optional[tuple[int, int]]:
        """
        Get scale coordinate for a logical coordinate.

        Args:
            logical_x: Logical X coordinate
            logical_y: Logical Y coordinate

        Returns:
            Scale (MOS) coordinate tuple or None
        """
        if self.current_layout_calculator and hasattr(self.current_layout_calculator, 'get_mos_coordinate'):
            try:
                return self.current_layout_calculator.get_mos_coordinate(logical_x, logical_y)
            except Exception:
                return None
        return None

    def trigger_note(self, logical_x: int, logical_y: int, velocity: int = 100, note_on: bool = True, source: str = "ui") -> bool:
        """
        Trigger a MIDI note from UI or other source.

        Args:
            logical_x: Logical X coordinate
            logical_y: Logical Y coordinate
            velocity: Note velocity (0-127)
            note_on: True for note-on, False for note-off
            source: Source of trigger ("ui" or "device")

        Returns:
            True if note was triggered successfully
        """
        coord = (logical_x, logical_y)

        # Look up mapped note
        if coord not in self.midi_handler.note_mapping:
            logger.info(f"{source} note_{'on' if note_on else 'off'} -> ({logical_x}, {logical_y}) -> unmapped")
            return False

        note = self.midi_handler.note_mapping[coord]

        # Get scale coordinate if available
        scale_coord_str = "?"
        if self.current_layout_calculator and hasattr(self.current_layout_calculator, 'get_mos_coordinate'):
            try:
                mos_coord = self.current_layout_calculator.get_mos_coordinate(logical_x, logical_y)
                scale_coord_str = f"({mos_coord[0]}, {mos_coord[1]})"
            except Exception:
                pass

        # Log the full pipeline
        note_type = "note_on" if note_on else "note_off"
        logger.info(
            f"{source} {note_type} -> ({logical_x}, {logical_y}) -> {scale_coord_str} -> note {note}"
        )

        # Notify UI about note event (for highlighting)
        self._handle_note_event(logical_x, logical_y, note_on)

        # Send MIDI message
        if note_on:
            self.midi_handler.send_note_on(note, velocity)
        else:
            self.midi_handler.send_note_off(note)

        return True

    def get_status(self) -> dict:
        """Get current application status."""
        # Get detected controllers (those actually available via MIDI)
        # Use cached ports to avoid CoreMIDI threading issues
        available_ports = self.get_cached_midi_ports()
        detected_controllers = []
        for config_name in self.controller_manager.get_all_device_names():
            config = self.controller_manager.get_config(config_name)
            if config and config.device_name != "Computer Keyboard" and config.controller_midi_output:
                # Check if this controller's MIDI output port is available
                for port in available_ports:
                    if config.controller_midi_output.lower() in port.lower():
                        detected_controllers.append(config_name)
                        break

        # Get controller pads for visualization with note mapping and colors
        controller_pads = []
        if self.current_controller:
            for x, y, px, py in self.current_controller.pads:
                coord = (x, y)
                # Get mapped note if available
                mapped_note = self.midi_handler.note_mapping.get(coord)

                # Calculate MOS coordinate and color based on scale system
                mos_coord = None
                color = None

                if self.current_layout_calculator and hasattr(self.current_layout_calculator, 'get_mos_coordinate'):
                    # Get MOS coordinate for this pad
                    mos_coord = self.current_layout_calculator.get_mos_coordinate(x, y)

                    # Use coloring scheme to determine color
                    color = DEFAULT_COLORING_SCHEME.get_color(
                        mos_coord=mos_coord,
                        mos=self.tuning_handler.mos,
                        coord_to_scale_index=self.tuning_handler.coord_to_scale_index,
                        supermos=None
                    )
                elif mapped_note is not None:
                    # Fallback: simple hue based on note number
                    hue = (mapped_note * 30) % 360
                    color = f"hsl({hue}, 70%, 60%)"

                # Get MOS labels if mos_coord is available
                mos_label_digit = None
                mos_label_letter = None
                if mos_coord and self.tuning_handler.mos:
                    try:
                        v = sx.Vector2i(mos_coord[0], mos_coord[1])
                        mos_label_digit = self.tuning_handler.mos.nodeLabelDigit(v)
                        mos_label_letter = self.tuning_handler.mos.nodeLabelLetter(v)
                    except Exception as e:
                        logger.debug(f"Error getting MOS labels for {mos_coord}: {e}")

                controller_pads.append({
                    'x': x,
                    'y': y,
                    'phys_x': px,
                    'phys_y': py,
                    'shape': self.current_controller.pad_shapes.get((x, y), []),
                    'note': mapped_note,
                    'color': color,
                    'mos_coord': mos_coord,
                    'mos_label_digit': mos_label_digit,
                    'mos_label_letter': mos_label_letter,
                })

        return {
            'connected_controller': self.current_controller.device_name if self.current_controller else None,
            'midi_connected': self.midi_handler.is_controller_connected(),
            'layout_type': self.current_layout_config.layout_type.value,
            'virtual_midi_device': settings.virtual_midi_device_name,
            'available_controllers': self.controller_manager.get_all_device_names(),
            'detected_controllers': detected_controllers,
            'controller_pads': controller_pads,
            'osc_connected': self.osc_handler.is_connected(),
            'osc_port': self.osc_handler.port,
            'tuning': self.tuning_handler.get_tuning_info(),
            'midi_stats': {
                'messages_processed': self.midi_handler.messages_processed,
                'notes_remapped': self.midi_handler.notes_remapped,
            }
        }

    def _send_controller_setup(self):
        """Send note assignment setup to controller on connection."""
        if not self.current_controller or not self.midi_handler:
            return

        # Only send if template exists
        if not self.current_controller.set_pad_notes_bulk:
            logger.debug("No SetPadNotesBulk template, skipping controller setup")
            return

        try:
            from .midi_setup import MIDITemplateBuilder

            # Build list of pads with their controller notes
            pads = []
            for logical_x, logical_y, _, _ in self.current_controller.pads:
                controller_note = self.current_controller.logical_coord_to_controller_note(logical_x, logical_y)
                if controller_note is not None:
                    pads.append({
                        'x': logical_x,
                        'y': logical_y,
                        'noteNumber': controller_note,
                        'midiChannel': 0
                    })

            # Build and send MIDI message
            builder = MIDITemplateBuilder(self.current_controller)
            midi_bytes = builder.set_pad_notes_bulk(pads)

            if midi_bytes:
                self.midi_handler.send_raw_bytes(midi_bytes)
                logger.info(f"Sent SetPadNotesBulk: {len(pads)} pads, {len(midi_bytes)} bytes")

        except Exception as e:
            logger.error(f"Error sending controller setup: {e}", exc_info=True)

    def _send_pad_colors_async(self):
        """Send color updates to physical controller asynchronously.

        This runs the color send in a background thread to keep the UI responsive
        during rapid layout changes (e.g., transformation controls).
        The generation-based cancellation ensures that if a new update arrives,
        the old send operation is cancelled.
        """
        # Cancel any ongoing color send and get a new generation number
        generation = self.midi_handler.cancel_color_send()

        # Start the color send in a background thread
        thread = threading.Thread(
            target=self._send_pad_colors_worker,
            args=(generation,),
            daemon=True
        )
        thread.name = f"ColorSend-{generation}"
        thread.start()

    def _send_pad_colors_worker(self, generation: int):
        """Worker method that sends pad colors (runs in background thread)."""
        if not self.current_controller or not self.midi_handler:
            logger.debug("_send_pad_colors: No controller or midi_handler")
            return

        # Only send if we have color templates
        if not (self.current_controller.set_pad_colors_bulk or self.current_controller.set_pad_color):
            logger.debug(f"_send_pad_colors: No color templates for {self.current_controller.device_name}")
            return

        # Check if MIDI output is available
        if not self.midi_handler.controller_out:
            logger.warning(f"_send_pad_colors: MIDI output not connected for {self.current_controller.device_name}")
            return

        logger.info(f"_send_pad_colors: Sending colors for {self.current_controller.device_name}"
                    f" (bulk={bool(self.current_controller.set_pad_colors_bulk)},"
                    f" individual={bool(self.current_controller.set_pad_color)},"
                    f" generation={generation})")

        try:
            from .midi_setup import MIDITemplateBuilder

            # Get current status with colors
            status = self.get_status()
            controller_pads = status.get('controller_pads', [])

            # Build pad data with RGB colors for ALL pads
            pads_with_colors = []
            for pad in controller_pads:
                # Convert HSL color to RGB
                # Use gray for unmapped pads, otherwise use pad's color
                if pad.get('color'):
                    hsl_color = pad['color']
                else:
                    # Unmapped pad - use dark gray
                    hsl_color = 'hsl(0, 0%, 20%)'

                rgb = self._hsl_to_rgb(hsl_color)

                pads_with_colors.append({
                    'x': pad['x'],
                    'y': pad['y'],
                    'red': rgb[0],
                    'green': rgb[1],
                    'blue': rgb[2],
                    'color': self._rgb_to_controller_enum(rgb)
                })

            if not pads_with_colors:
                return

            # Build and send MIDI message
            builder = MIDITemplateBuilder(self.current_controller)

            # Prefer bulk if available
            if self.current_controller.set_pad_colors_bulk:
                midi_bytes = builder.set_pad_colors_bulk(pads_with_colors)
                if midi_bytes:
                    self.midi_handler.send_raw_bytes(midi_bytes, generation=generation)
                    logger.info(f"Sent SetPadColorsBulk: {len(pads_with_colors)} pads, {len(midi_bytes)} bytes")

            # Fallback to individual messages
            elif self.current_controller.set_pad_color:
                for pad in pads_with_colors:
                    midi_bytes = builder.set_pad_color(
                        pad['x'], pad['y'],
                        pad['red'], pad['green'], pad['blue'],
                        pad['color']
                    )
                    if midi_bytes:
                        self.midi_handler.send_raw_bytes(midi_bytes, generation=generation)
                logger.info(f"Sent SetPadColor for {len(pads_with_colors)} pads individually")

        except Exception as e:
            logger.error(f"Error sending pad colors: {e}", exc_info=True)

    def _hsl_to_rgb(self, hsl_string: str) -> tuple[int, int, int]:
        """
        Convert HSL color string to RGB tuple.

        Args:
            hsl_string: HSL string like "hsl(120, 100%, 50%)"

        Returns:
            RGB tuple (0-255, 0-255, 0-255)
        """
        import colorsys
        import re

        # Parse HSL string
        match = re.match(r'hsl\((\d+),\s*(\d+)%,\s*(\d+)%\)', hsl_string)
        if not match:
            logger.warning(f"Invalid HSL format: {hsl_string}, using gray")
            return (128, 128, 128)

        h = int(match.group(1)) / 360.0
        s = int(match.group(2)) / 100.0
        l = int(match.group(3)) / 100.0

        # Convert to RGB
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        return (int(r * 255), int(g * 255), int(b * 255))

    def _rgb_to_controller_enum(self, rgb: tuple[int, int, int]) -> int:
        """
        Convert RGB to controller-specific color enum (for LinnStrument).

        Args:
            rgb: RGB tuple (0-255, 0-255, 0-255)

        Returns:
            Color enum value (1-11 for LinnStrument, or 0 if not applicable)
        """
        # Only LinnStrument uses color enums
        if not self.current_controller or 'LinnStrument' not in self.current_controller.device_name:
            return 0

        # LinnStrument color mapping
        LINNSTRUMENT_COLORS = {
            (255, 0, 0): 1,      # Red
            (255, 255, 0): 2,    # Yellow
            (0, 255, 0): 3,      # Green
            (0, 255, 255): 4,    # Cyan
            (0, 0, 255): 5,      # Blue
            (255, 0, 255): 6,    # Magenta
            (0, 0, 0): 7,        # Off
            (255, 255, 255): 8,  # White
            (255, 127, 0): 9,    # Orange
            (127, 255, 0): 10,   # Lime
            (255, 0, 127): 11,   # Pink
        }

        # Find nearest color by Euclidean distance
        min_dist = float('inf')
        nearest_enum = 7  # Default to Off

        for color_rgb, enum_val in LINNSTRUMENT_COLORS.items():
            dist = sum((a - b) ** 2 for a, b in zip(rgb, color_rgb))
            if dist < min_dist:
                min_dist = dist
                nearest_enum = enum_val

        return nearest_enum
