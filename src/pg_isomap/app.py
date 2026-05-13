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

from .coloring import (
    AVAILABLE_SCHEMES,
    DEFAULT_COLORING_SCHEME,
    DEFAULT_HARMONY_SCHEME,
    DEFAULT_RAINBOW_SCHEME,
    SCHEME_HARMONY,
    SCHEME_RAINBOW,
    SCHEME_SCALE,
    SpectrumConsonance,
)
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
from .preferences import ControllerPreferences
from .tuning import TuningHandler
from .wooting import WootingBridge, WootingNotAvailable
from .wooting.usb_scan import pid_matches_base, scan as wooting_usb_scan

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

        # Dynamic option values for current controller (e.g. {"INVERT_SUSTAIN": True})
        self.preferences = ControllerPreferences()
        self._dynamic_option_values: dict = {}

        # WebAPI reference (set by WebAPI after initialization)
        self.web_api = None

        # Discovery thread
        self._discovery_thread: Optional[threading.Thread] = None
        self._discovery_running = False

        # Cached MIDI ports (updated from main thread to avoid CoreMIDI threading issues)
        self._cached_midi_ports: list[str] = []
        self._ports_lock = threading.Lock()

        # Cached per-pad device color data for restoring after note-off
        # Maps (logical_x, logical_y) -> {'red', 'green', 'blue', 'color'}
        self._pad_device_colors: dict[tuple[int, int], dict] = {}

        # Wooting analog bridge (active only when current_controller.is_wooting()).
        self._wooting_bridge: Optional[WootingBridge] = None

        # Cache of (vid, pid) pairs from the latest Wooting USB scan, refreshed
        # by the discovery thread. Read by get_status() so the API surfaces the
        # same "(available)" badge as MIDI controllers.
        self._wooting_usb_pids: set[int] = set()

        # Pads whose visual unhighlight has been deferred because the
        # sustain pedal is held. Released on the pedal's falling edge.
        self._sustained_pads: set[tuple[int, int]] = set()
        self._sustained_pads_lock = threading.Lock()

        # Coloring scheme state
        self._color_scheme: str = SCHEME_SCALE
        self._spectrum_consonance = SpectrumConsonance()
        self._last_partials: list[tuple[float, float]] = []

        # Throttle for spectrum-driven recoloring (seconds between resends)
        self._spectrum_refresh_interval: float = 0.2  # 5Hz
        self._spectrum_last_applied: float = 0.0
        self._spectrum_pending_timer: Optional[threading.Timer] = None
        self._spectrum_lock = threading.Lock()

        # Setup callbacks
        self.osc_handler.on_scale_update = self._handle_scale_update
        self.osc_handler.on_mapping_update = self._handle_mapping_update
        self.osc_handler.on_note_mapping = self._handle_note_mapping
        self.osc_handler.on_connection_changed = self._handle_osc_connection_changed
        self.osc_handler.on_plugin_note_on = self._handle_plugin_note_on
        self.osc_handler.on_plugin_note_off = self._handle_plugin_note_off
        self.osc_handler.on_spectrum = self._handle_spectrum_update
        self.midi_handler.get_scale_coord = self._get_scale_coordinate
        self.midi_handler.on_note_event = self._handle_note_event
        self.midi_handler.on_sustain_change = self._handle_sustain_change

    def start(self):
        """Start the application."""
        logger.info("Starting PitchGrid Mapper...")

        # Try to auto-load computer keyboard config FIRST
        self._try_load_computer_keyboard()

        # Initialize virtual MIDI port
        if not self.midi_handler.initialize_virtual_port():
            logger.warning("Virtual MIDI port not available")
            logger.warning("The app can run but cannot send remapped MIDI to your DAW without a virtual port")

        # Start MIDI processing
        self.midi_handler.start()

        # Start OSC server
        self.osc_handler.start()

        # Start controller discovery
        self._start_discovery()

        logger.info("PitchGrid Mapper started successfully")
        logger.info(f"Current controller: {self.current_controller.device_name if self.current_controller else 'None'}")
        return True

    def stop(self):
        """Stop the application."""
        logger.info("Stopping PitchGrid Mapper...")

        # Stop discovery
        self._stop_discovery()

        # Stop Wooting bridge if active
        self._stop_wooting_bridge()

        # Stop components
        self.midi_handler.shutdown()
        self.osc_handler.stop()

        logger.info("PitchGrid Mapper stopped")

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
                # Wooting controllers: scan USB for devices that match each
                # YAML's wootingProductId (with the +0/+1/+2 alt suffix
                # masking the SDK uses). This is independent of MIDI ports.
                wooting_pids = {
                    pid for _vid, pid in wooting_usb_scan()
                }
                self._wooting_usb_pids = wooting_pids
                for config_name in self.controller_manager.get_all_device_names():
                    config = self.controller_manager.get_config(config_name)
                    if not config:
                        continue
                    if config.is_wooting() and config.wooting_product_id is not None:
                        if any(
                            pid_matches_base(pid, int(config.wooting_product_id))
                            for pid in wooting_pids
                        ):
                            current_detected.add(config_name)
                            logger.debug(
                                f"Matched {config_name} to USB Wooting device "
                                f"(PID family 0x{config.wooting_product_id:04X})"
                            )
                        continue
                    if config.controller_midi_output:
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
            self._load_color_scheme()
            self._recalculate_layout()
            logger.info("Computer keyboard config loaded")

    def connect_to_controller(self, device_name: str) -> bool:
        """
        Activate a physical controller for live use.

        For MIDI controllers, opens the configured input/output ports.
        For Wooting analog controllers, builds and starts the native bridge.
        In both cases, sets `current_controller`, loads color scheme and
        dynamic options, and recalculates the layout.

        Returns:
            True if connection / bridge start succeeded.
        """
        config = self.controller_manager.get_config(device_name)
        if not config:
            logger.error(f"No configuration found for {device_name}")
            return False

        # Tear down any prior hardware connection (MIDI or Wooting).
        self._stop_wooting_bridge()
        self.midi_handler.disconnect_controller()

        if config.is_wooting():
            # Build the bridge first so a missing dylib / plugin surfaces
            # before we mutate app state.
            try:
                bridge = WootingBridge(self.midi_handler, config)
            except WootingNotAvailable as exc:
                logger.warning("Wooting bridge unavailable: %s", exc)
                return False
            except Exception as exc:
                logger.error("Failed to construct Wooting bridge: %s", exc, exc_info=True)
                return False

            self._wooting_bridge = bridge
            self.current_controller = config
            self._load_color_scheme()
            self._load_dynamic_option_values()
            self.current_layout_calculator = None
            self._recalculate_layout()

            try:
                bridge.start()
            except Exception as exc:
                logger.error("Failed to start Wooting bridge: %s", exc, exc_info=True)
                self._wooting_bridge = None
                return False

            logger.info("Wooting bridge active for %s", config.device_name)
            return True

        # MIDI-controller path.
        if not config.controller_midi_output:
            logger.warning(f"Controller {device_name} has no MIDI output port configured")
            return False

        if not self.midi_handler.connect_to_controller(
            output_port_name=config.controller_midi_output,
            input_port_name=config.controller_midi_input,
        ):
            return False

        self.current_controller = config
        self._load_color_scheme()
        self._load_dynamic_option_values()
        self.current_layout_calculator = None
        self._send_controller_setup()
        self._send_controller_setup_commands()
        self._recalculate_layout()

        logger.info(f"Connected to {device_name}")
        return True

    def disconnect_controller(self):
        """Disconnect from current controller."""
        # Cancel any ongoing color send before disconnecting
        self.midi_handler.cancel_color_send()
        self.midi_handler.disconnect_controller()
        self._stop_wooting_bridge()
        self.current_controller = None
        logger.info("Controller disconnected")

    def _stop_wooting_bridge(self):
        if self._wooting_bridge is not None:
            try:
                self._wooting_bridge.stop()
            except Exception as exc:
                logger.error("Error stopping Wooting bridge: %s", exc)
            self._wooting_bridge = None

    def set_wooting_profile(self, name: str) -> bool:
        """Switch the active Wooting profile (e.g. 'mpe' / 'piano_sim')."""
        if self._wooting_bridge is None:
            return False
        try:
            self._wooting_bridge.set_profile(name)
            return True
        except Exception as exc:
            logger.error("Failed to set Wooting profile %s: %s", name, exc)
            return False

    def get_wooting_profiles(self) -> list[dict]:
        """Available profile metadata; empty if the native module isn't loaded."""
        try:
            from .wooting import WootingBridge as _WB  # noqa: F401
            import pg_wooting_bridge  # type: ignore
            return list(pg_wooting_bridge.available_profiles())
        except Exception:
            return []

    def get_wooting_status(self) -> Optional[dict]:
        if self._wooting_bridge is None:
            return None
        try:
            return self._wooting_bridge.status()
        except Exception:
            return None

    def set_wooting_sensitivity(self, value: float) -> bool:
        """Set the Wooting analog sensitivity multiplier (1.0 = neutral)."""
        if self._wooting_bridge is None:
            return False
        try:
            self._wooting_bridge.set_sensitivity(value)
            return True
        except Exception as exc:
            logger.error("Failed to set Wooting sensitivity: %s", exc)
            return False

    def get_wooting_sensitivity(self) -> float:
        if self._wooting_bridge is None:
            return 1.0
        try:
            return self._wooting_bridge.sensitivity()
        except Exception:
            return 1.0

    def _load_color_scheme(self):
        """Load saved color scheme for current controller, or reset to default.

        Falls back to the scale scheme if the current controller doesn't
        support RGB (e.g. LinnStrument palette-only).
        """
        if not self.current_controller:
            self._color_scheme = SCHEME_SCALE
            return

        saved = self.preferences.get_color_scheme(self.current_controller.device_name)
        if saved in AVAILABLE_SCHEMES and (saved == SCHEME_SCALE or self._controller_supports_rgb()):
            self._color_scheme = saved
        else:
            self._color_scheme = SCHEME_SCALE

    def _load_dynamic_option_values(self):
        """Load dynamic option values for current controller from preferences, filling defaults."""
        if not self.current_controller:
            self._dynamic_option_values = {}
            return

        saved = self.preferences.get_option_values(self.current_controller.device_name)
        values = {}
        for opt in self.current_controller.dynamic_ui_options:
            if opt.name in saved:
                values[opt.name] = saved[opt.name]
            else:
                values[opt.name] = opt.default
        self._dynamic_option_values = values

    def set_dynamic_option(self, name: str, value) -> bool:
        """Set a dynamic option value and re-send setup commands to the controller."""
        if not self.current_controller:
            return False

        # Find the option definition for validation
        opt_def = None
        for opt in self.current_controller.dynamic_ui_options:
            if opt.name == name:
                opt_def = opt
                break
        if opt_def is None:
            logger.warning(f"Unknown dynamic option: {name}")
            return False

        # Validate and coerce
        if opt_def.type == 'bool':
            value = bool(value)
        elif opt_def.type == 'int':
            value = int(value)
            if opt_def.min_val is not None:
                value = max(opt_def.min_val, value)
            if opt_def.max_val is not None:
                value = min(opt_def.max_val, value)

        self._dynamic_option_values[name] = value
        self.preferences.set_option_value(self.current_controller.device_name, name, value)

        # Re-send setup commands to controller
        self._send_controller_setup_commands()

        # Broadcast updated status to UI
        if self.web_api:
            self.web_api.broadcast_status_update()

        return True

    def _send_controller_setup_commands(self):
        """Evaluate and send ControllerSetupCommands using current dynamic option values."""
        if not self.current_controller or not self.midi_handler.controller_out:
            return
        if not self.current_controller.controller_setup_commands:
            return

        from .midi_setup import MIDITemplateBuilder

        builder = MIDITemplateBuilder(self.current_controller)
        delay_ms = self.current_controller.message_delay_ms
        ack_config = self.current_controller.ack_messaging

        # Build substitution kwargs: convert bool->int (0/1) for MIDI bytes
        kwargs = {}
        for name, value in dict(self._dynamic_option_values).items():
            if isinstance(value, bool):
                kwargs[name] = 1 if value else 0
            else:
                kwargs[name] = int(value)

        all_bytes = []
        for template in self.current_controller.controller_setup_commands:
            try:
                midi_bytes = builder.build_midi_message(template, **kwargs)
                if midi_bytes:
                    all_bytes.extend(midi_bytes)
            except Exception as e:
                logger.error(f"Error building setup command '{template}': {e}")

        if all_bytes:
            self.midi_handler.send_raw_bytes(all_bytes, delay_ms=delay_ms, ack_config=ack_config)
            logger.info(f"Sent {len(self.current_controller.controller_setup_commands)} controller setup commands ({len(all_bytes)} bytes)")

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

        # Clear all playing note highlights before layout changes shift pads around
        if self.web_api:
            self.web_api.broadcast_clear_all_notes()

        # Create layout calculator only if we don't have one or if the type changed
        needs_new_calculator = (
            self.current_layout_calculator is None or
            (self.current_layout_config.layout_type == LayoutType.ISOMORPHIC and not isinstance(self.current_layout_calculator, IsomorphicLayout)) or
            (self.current_layout_config.layout_type == LayoutType.STRING_LIKE and not isinstance(self.current_layout_calculator, StringLikeLayout)) or
            (self.current_layout_config.layout_type == LayoutType.PIANO_LIKE and not isinstance(self.current_layout_calculator, PianoLikeLayout))
        )

        if needs_new_calculator:
            if self.current_layout_config.layout_type == LayoutType.ISOMORPHIC:
                # Pass default root coordinate and row_to_col_angle from controller config
                default_root = self.current_controller.default_iso_root_coordinate
                self.current_layout_calculator = IsomorphicLayout(
                    self.current_layout_config,
                    default_root=default_root,
                    row_to_col_angle=self.current_controller.row_to_col_angle
                )
            elif self.current_layout_config.layout_type == LayoutType.STRING_LIKE:
                # Pass default root coordinate and row_to_col_angle from controller config
                default_root = self.current_controller.default_iso_root_coordinate
                self.current_layout_calculator = StringLikeLayout(
                    self.current_layout_config,
                    default_root=default_root,
                    row_to_col_angle=self.current_controller.row_to_col_angle
                )
            elif self.current_layout_config.layout_type == LayoutType.PIANO_LIKE:
                # Pass default root coordinate and row_to_col_angle from controller config
                default_root = self.current_controller.default_iso_root_coordinate
                self.current_layout_calculator = PianoLikeLayout(
                    self.current_layout_config,
                    default_root=default_root,
                    row_to_col_angle=self.current_controller.row_to_col_angle
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
            coord_to_scale_index=self.tuning_handler.coord_to_scale_index,
            enharmonic_vector=self.tuning_handler.enharmonic_vector,
            mode_offset=self.tuning_handler.mode_offset
        )

        # Build reverse mapping (controller_note -> logical_coord)
        # Use controller's noteAssign function
        reverse_mapping = self.current_controller.build_controller_note_mapping()

        # Determine if channel should be used for reverse lookup
        # Controllers with channelAssign (e.g., Lumatone) need channel-based lookup
        use_channel_for_lookup = self.current_controller.channel_assign is not None

        # Build MOS → logical reverse mapping for plugin note highlighting
        self.current_layout_calculator.build_mos_to_logical_mapping(logical_coords)

        # Stop only notes whose mapping actually changed (prevents unnecessary note-offs)
        self.midi_handler.stop_notes_with_changed_mapping(mapping)

        # Update MIDI handler
        self.midi_handler.update_note_mapping(mapping, reverse_mapping, use_channel_for_lookup)

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
        """Handle tuning update from PitchGrid plugin (/pitchgrid/plugin/tuning).

        Live tuning params may diverge from mapping params when the plugin's
        mapping is locked. These are used by the harmony-based coloring (which
        depends on the actually-sounding intervals), not by layout.
        """
        args = scale_data.get('args', [])
        if len(args) < 8:
            logger.debug(f"Ignoring short tuning payload: {args}")
            return

        mode, root_freq, stretch, skew, mode_offset, steps, mos_a, mos_b = args[:8]
        root_changed = self.tuning_handler.update_live_tuning(
            mode=mode,
            root_freq=root_freq,
            stretch=stretch,
            skew=skew,
            mode_offset=mode_offset,
            steps=steps,
            mos_a=mos_a,
            mos_b=mos_b,
        )

        # Root frequency change invalidates the consonance curve — recompute
        # from cached spectrum if we have one.
        if root_changed and self._last_partials and self.tuning_handler.live_root_freq > 0:
            self._spectrum_consonance.update(
                self._last_partials, self.tuning_handler.live_root_freq
            )

        # Any live tuning change can shift pad cents → refresh harmony colors
        if self._color_scheme == SCHEME_HARMONY:
            self._schedule_spectrum_refresh()

    def _handle_mapping_update(self, mapping_data: dict):
        """Handle mapping update from PitchGrid plugin (/pitchgrid/plugin/mapping).

        This receives the params used for MIDI mapping (frozen when mapping is
        locked in the plugin). Drives layout recalculation.
        """
        logger.info("Received mapping update from PitchGrid")

        # Cancel any ongoing color send operation immediately
        # This prevents interleaved MIDI messages when rapid tuning changes arrive
        self.midi_handler.cancel_color_send()

        args = mapping_data.get('args', [])

        if len(args) >= 8:
            # Parse mapping data: (mode, root_freq, stretch, skew, mode_offset, steps, mos_a, mos_b)
            mode, root_freq, stretch, skew, mode_offset, steps, mos_a, mos_b = args[:8]

            # Update tuning handler with mapping params
            self.tuning_handler.update_tuning(
                mode=mode,
                root_freq=root_freq,
                stretch=stretch,
                skew=skew,
                mode_offset=mode_offset,
                steps=steps,
                mos_a=mos_a,
                mos_b=mos_b
            )

            # Recalculate layout with new mapping. (Consonance curve tracks
            # live-tuning root in _handle_scale_update, not the mapping root.)
            self._recalculate_layout()

        else:
            logger.warning(f"Unexpected mapping data format: {args}")

    def _handle_note_mapping(self, mapping_data: dict):
        """Handle note mapping update from PitchGrid plugin."""
        logger.info("Received note mapping from PitchGrid")

        # Parse and apply note mapping
        # TODO: Implement based on actual PitchGrid OSC format

    def _handle_spectrum_update(self, partials: list):
        """Handle incoming spectrum from PitchGrid plugin.

        Recomputes the consonance curve at the current root frequency and
        triggers a throttled color re-send if the harmony scheme is active.
        """
        if not partials:
            return

        with self._spectrum_lock:
            self._last_partials = list(partials)

        # Harmony coloring uses the *live* tuning (may differ from mapping
        # when the plugin's mapping is locked). Fall back to mapping root if
        # no live tuning has been received yet.
        root_freq = self.tuning_handler.live_root_freq
        if root_freq <= 0:
            root_freq = self.tuning_handler.root_freq
        if root_freq <= 0:
            return

        # Recompute consonance curve (runs off OSC server thread)
        if not self._spectrum_consonance.update(partials, root_freq):
            return

        # Only re-push colors/UI when the harmony scheme is actually active
        if self._color_scheme != SCHEME_HARMONY:
            return

        self._schedule_spectrum_refresh()

    def _schedule_spectrum_refresh(self):
        """Throttle spectrum-driven color refresh to at most once per interval."""
        with self._spectrum_lock:
            now = time.time()
            elapsed = now - self._spectrum_last_applied
            if elapsed >= self._spectrum_refresh_interval:
                self._spectrum_last_applied = now
                if self._spectrum_pending_timer:
                    self._spectrum_pending_timer.cancel()
                    self._spectrum_pending_timer = None
                threading.Thread(
                    target=self._apply_spectrum_refresh,
                    daemon=True,
                    name="Harmony-Refresh",
                ).start()
                return

            # Otherwise schedule a single trailing refresh
            if self._spectrum_pending_timer is not None:
                return
            delay = self._spectrum_refresh_interval - elapsed
            t = threading.Timer(delay, self._apply_spectrum_refresh_trailing)
            t.daemon = True
            t.name = "Harmony-RefreshTimer"
            self._spectrum_pending_timer = t
            t.start()

    def _apply_spectrum_refresh_trailing(self):
        with self._spectrum_lock:
            self._spectrum_pending_timer = None
            self._spectrum_last_applied = time.time()
        self._apply_spectrum_refresh()

    def _apply_spectrum_refresh(self):
        """Push updated colors to device and UI."""
        if self._color_scheme != SCHEME_HARMONY:
            return
        self._send_pad_colors_async()
        if self.web_api:
            self.web_api.broadcast_status_update()

    def set_color_scheme(self, scheme: str) -> bool:
        """Select a coloring scheme and re-render pad colors."""
        if scheme not in AVAILABLE_SCHEMES:
            logger.warning(f"Unknown color scheme: {scheme}")
            return False

        # Palette-only controllers can't display the RGB schemes meaningfully
        if scheme != SCHEME_SCALE and not self._controller_supports_rgb():
            logger.warning(f"Controller does not support RGB scheme '{scheme}'")
            return False

        if scheme == self._color_scheme:
            return True

        self._color_scheme = scheme

        if self.current_controller:
            self.preferences.set_color_scheme(self.current_controller.device_name, scheme)

        # If harmony was selected but spectrum is stale or missing, try to
        # recompute from the last seen partials.
        if scheme == SCHEME_HARMONY and self._last_partials:
            root = self.tuning_handler.live_root_freq or self.tuning_handler.root_freq
            if root > 0:
                self._spectrum_consonance.update(self._last_partials, root)

        self._send_pad_colors_async()
        if self.web_api:
            self.web_api.broadcast_status_update()
        return True

    def _controller_supports_rgb(self) -> bool:
        """True if the current controller can display continuous RGB colors.

        Palette-only controllers (e.g. LinnStrument) define a color enum and
        can't reproduce the Rainbow/Harmony palettes faithfully. Controllers
        with no color output (e.g. Computer Keyboard) are still allowed — the
        new schemes can still drive the UI canvas. Wooting analog keyboards
        get full RGB through the native bridge.
        """
        c = self.current_controller
        if c is None:
            return True
        if c.is_wooting():
            return True
        return c.color_enum_to_rgb is None

    def _mapping_to_tuning_coord(self, mapping_coord, tuning_mos):
        """Convert a mapping-MOS coordinate to the equivalent tuning-MOS coordinate.

        Goes via the shared (1,1) root lattice: mapping_coord → root → tuning_coord.
        If either MOS is missing or the tuning MOS is the same object as the
        mapping MOS, returns the mapping coord unchanged.
        """
        if mapping_coord is None or tuning_mos is None:
            return mapping_coord
        mapping_mos = self.tuning_handler.mos
        if mapping_mos is None or tuning_mos is mapping_mos:
            return mapping_coord
        try:
            root = mapping_mos.toRootCoord(sx.Vector2i(mapping_coord[0], mapping_coord[1]))
            tv = tuning_mos.fromRootCoord(root)
            return (tv.x, tv.y)
        except Exception as e:
            logger.debug(f"mapping→tuning coord conversion failed for {mapping_coord}: {e}")
            return mapping_coord

    def _get_pad_color(
        self,
        mos_coord,
        use_dark_offscale: bool = False,
    ) -> Optional[str]:
        """Dispatch pad color through the active coloring scheme."""
        scheme = self._color_scheme
        # Fall back to scale scheme if the current controller has no RGB support
        if scheme != SCHEME_SCALE and not self._controller_supports_rgb():
            scheme = SCHEME_SCALE

        is_mapped = mos_coord in self.tuning_handler.coord_to_scale_index

        if scheme == SCHEME_RAINBOW:
            return DEFAULT_RAINBOW_SCHEME.get_color(
                mos_coord=mos_coord,
                mos=self.tuning_handler.mos,
                steps=self.tuning_handler.steps,
                is_mapped=is_mapped,
            )

        if scheme == SCHEME_HARMONY:
            tuning_mos = self.tuning_handler.live_mos or self.tuning_handler.mos
            tuning_coord = self._mapping_to_tuning_coord(mos_coord, tuning_mos)
            return DEFAULT_HARMONY_SCHEME.get_color(
                tuning_coord=tuning_coord,
                tuning_mos=tuning_mos,
                spectrum_consonance=self._spectrum_consonance,
                is_mapped=is_mapped,
            )

        return DEFAULT_COLORING_SCHEME.get_color(
            mos_coord=mos_coord,
            mos=self.tuning_handler.mos,
            coord_to_scale_index=self.tuning_handler.coord_to_scale_index,
            supermos=None,
            use_dark_offscale=use_dark_offscale,
        )

    def _handle_osc_connection_changed(self, connected: bool):
        """Handle OSC connection state change."""
        logger.info(f"OSC connection state changed: {'connected' if connected else 'disconnected'}")

        # Broadcast updated status to WebSocket clients
        if self.web_api:
            self.web_api.broadcast_status_update()

    def _handle_note_event(self, logical_x: int, logical_y: int, note_on: bool):
        """Handle note event from MIDI handler, OSC plugin, or UI click.

        Sustain (CC64) is honoured at this level so any source — including
        the OSC plugin's note_off echo — defers the visual unhighlight while
        the pedal is held. Once sustain is released, every deferred pad
        gets a fresh note_off broadcast and pad-color clear (driven by
        `_handle_sustain_change`).
        """
        coord = (logical_x, logical_y)
        if note_on:
            with self._sustained_pads_lock:
                self._sustained_pads.discard(coord)
        else:
            held = self.midi_handler.is_sustain_held()
            if held:
                with self._sustained_pads_lock:
                    self._sustained_pads.add(coord)
                return  # don't broadcast / clear pad color while pedal is held

        if self.web_api:
            self.web_api.broadcast_note_event(logical_x, logical_y, note_on)
        # Send color to physical controller if it supports SetPadColor
        self._send_pad_playing_color(logical_x, logical_y, note_on)

    def _handle_sustain_change(self, held: bool):
        """Called by MIDIHandler when CC64 flips. On falling edge, drain
        every deferred pad and finally clear it visually."""
        if held:
            return
        with self._sustained_pads_lock:
            to_release = list(self._sustained_pads)
            self._sustained_pads.clear()
        for lx, ly in to_release:
            if self.web_api:
                self.web_api.broadcast_note_event(lx, ly, False)
            self._send_pad_playing_color(lx, ly, False)

    def _handle_plugin_note_on(self, root_x: int, root_y: int, velocity: int):
        """Handle note_on from PitchGrid plugin (root coords) - highlight corresponding pads."""
        if not self.current_layout_calculator or not self.tuning_handler.mos:
            return
        mos_coord = self.tuning_handler.mos.fromRootCoord(sx.Vector2i(root_x, root_y))
        logical_coords = self.current_layout_calculator.mos_to_logical.get((mos_coord.x, mos_coord.y), [])
        for lx, ly in logical_coords:
            self._handle_note_event(lx, ly, True)

    def _handle_plugin_note_off(self, root_x: int, root_y: int):
        """Handle note_off from PitchGrid plugin (root coords) - un-highlight corresponding pads."""
        if not self.current_layout_calculator or not self.tuning_handler.mos:
            return
        mos_coord = self.tuning_handler.mos.fromRootCoord(sx.Vector2i(root_x, root_y))
        logical_coords = self.current_layout_calculator.mos_to_logical.get((mos_coord.x, mos_coord.y), [])
        for lx, ly in logical_coords:
            self._handle_note_event(lx, ly, False)

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
            self.midi_handler.send_note_on(note, velocity, logical_coord=coord)
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
            if not config or config.device_name == "Computer Keyboard":
                continue
            if config.is_wooting() and config.wooting_product_id is not None:
                # Match against the cached USB scan (refreshed every discovery cycle).
                if any(
                    pid_matches_base(pid, int(config.wooting_product_id))
                    for pid in self._wooting_usb_pids
                ):
                    detected_controllers.append(config_name)
                continue
            if config.controller_midi_output:
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
                    # Get MOS coordinate (returns enharmonic equivalent if applicable)
                    mos_coord = self.current_layout_calculator.get_mos_coordinate(x, y)

                    # Dispatch through active coloring scheme (for UI display)
                    color = self._get_pad_color(
                        mos_coord=mos_coord,
                        use_dark_offscale=False,
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

                # Check if this pad was mapped via enharmonic equivalence
                is_enharmonic = False
                if (self.current_layout_calculator and
                        hasattr(self.current_layout_calculator, 'enharmonic_coords')):
                    is_enharmonic = coord in self.current_layout_calculator.enharmonic_coords

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
                    'is_enharmonic': is_enharmonic,
                })

        import sys

        # Add controller geometry info if available
        controller_geometry = None
        if self.current_controller:
            controller_geometry = {
                'horizon_to_row_angle': self.current_controller.horizon_to_row_angle,
                'row_to_col_angle': self.current_controller.row_to_col_angle,
            }

        # Dynamic UI options for current controller
        dynamic_ui_options = []
        if self.current_controller:
            for opt in self.current_controller.dynamic_ui_options:
                dynamic_ui_options.append({
                    'label': opt.label,
                    'name': opt.name,
                    'type': opt.type,
                    'default': opt.default,
                    'min': opt.min_val,
                    'max': opt.max_val,
                    'value': self._dynamic_option_values.get(opt.name, opt.default),
                })

        return {
            'connected_controller': self.current_controller.device_name if self.current_controller else None,
            'midi_connected': self.midi_handler.is_controller_connected(),
            'layout_type': self.current_layout_config.layout_type.value,
            'virtual_midi_device': self.midi_handler.virtual_port_name or 'None',
            'available_controllers': self.controller_manager.get_all_device_names(),
            'detected_controllers': detected_controllers,
            'controller_pads': controller_pads,
            'controller_geometry': controller_geometry,
            'osc_connected': self.osc_handler.is_connected(),
            'osc_port': self.osc_handler.port,
            'tuning': self.tuning_handler.get_tuning_info(),
            'midi_stats': {
                'messages_processed': self.midi_handler.messages_processed,
                'notes_remapped': self.midi_handler.notes_remapped,
            },
            'platform': sys.platform,  # 'win32', 'darwin', 'linux'
            'dynamic_ui_options': dynamic_ui_options,
            'color_scheme': self._color_scheme,
            'available_color_schemes': list(AVAILABLE_SCHEMES),
            'controller_supports_rgb': self._controller_supports_rgb(),
            'wooting': self._build_wooting_status_payload(),
        }

    def _build_wooting_status_payload(self) -> Optional[dict]:
        """Status block for Wooting controllers, or None when none is active."""
        if self._wooting_bridge is None:
            return None
        try:
            active = self._wooting_bridge.active_profile()
        except Exception:
            active = None
        return {
            'active': True,
            'profiles': self.get_wooting_profiles(),
            'active_profile': active,
            'sensitivity': self.get_wooting_sensitivity(),
            'per_note_sustain_enabled': self._wooting_bridge.per_note_sustain_enabled()
                if self._wooting_bridge is not None else False,
        }

    def _send_controller_setup(self):
        """Send note assignment setup to controller on connection."""
        if not self.current_controller or not self.midi_handler:
            return

        # Only send if we have at least one template
        if not (self.current_controller.set_pad_notes_bulk or self.current_controller.set_pad_note_and_channel):
            logger.debug("No SetPadNotesBulk or SetPadNoteAndChannel template, skipping controller setup")
            return

        # Check if MIDI output is available
        if not self.midi_handler.controller_out:
            logger.warning("_send_controller_setup: MIDI output not connected")
            return

        # Cancel any ongoing color send to prevent interleaved messages
        self.midi_handler.cancel_color_send()

        try:
            from .midi_setup import MIDITemplateBuilder

            # Build list of pads with their controller notes and channels
            pads = []
            for logical_x, logical_y, _, _ in self.current_controller.pads:
                controller_note = self.current_controller.logical_coord_to_controller_note(logical_x, logical_y)
                if controller_note is not None:
                    # Use channelAssign if defined, otherwise default to channel 0
                    controller_channel = self.current_controller.logical_coord_to_controller_channel(logical_x, logical_y)
                    pads.append({
                        'x': logical_x,
                        'y': logical_y,
                        'noteNumber': controller_note,
                        'midiChannel': controller_channel
                    })

            # Build and send MIDI message
            builder = MIDITemplateBuilder(self.current_controller)
            delay_ms = self.current_controller.message_delay_ms
            ack_config = self.current_controller.ack_messaging

            # Prefer bulk if available
            if self.current_controller.set_pad_notes_bulk:
                midi_bytes = builder.set_pad_notes_bulk(pads)
                if midi_bytes:
                    self.midi_handler.send_raw_bytes(midi_bytes, delay_ms=delay_ms, ack_config=ack_config)
                    logger.info(f"Sent SetPadNotesBulk: {len(pads)} pads, {len(midi_bytes)} bytes")

            # Fallback to individual messages - collect all and send together
            # so delay between messages is properly applied
            elif self.current_controller.set_pad_note_and_channel:
                all_midi_bytes = []
                for pad in pads:
                    midi_bytes = builder.set_pad_note_and_channel(
                        pad['x'], pad['y'],
                        pad['noteNumber'], pad['midiChannel']
                    )
                    if midi_bytes:
                        all_midi_bytes.extend(midi_bytes)
                if all_midi_bytes:
                    self.midi_handler.send_raw_bytes(all_midi_bytes, delay_ms=delay_ms, ack_config=ack_config)
                    logger.info(f"Sent SetPadNoteAndChannel for {len(pads)} pads ({len(all_midi_bytes)} bytes)")

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

        # Wooting bridge has its own RGB path (native, not MIDI templates).
        if self.current_controller.is_wooting() and self._wooting_bridge is not None:
            self._send_pad_colors_to_wooting()
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

            # For string-like layouts and EDO-compatible isomorphic layouts,
            # recalculate colors with dark off-scale for device
            use_dark_offscale = (
                self.current_layout_config.layout_type == LayoutType.STRING_LIKE or
                (self.current_layout_config.layout_type == LayoutType.ISOMORPHIC and
                 self.tuning_handler.is_edo_compatible)
            )

            # Build pad data with RGB colors for ALL pads
            pads_with_colors = []
            for pad in controller_pads:
                # For device colors, recalculate with use_dark_offscale if needed
                if use_dark_offscale and pad.get('mos_coord') and self.tuning_handler.mos:
                    device_color = self._get_pad_color(
                        mos_coord=pad['mos_coord'],
                        use_dark_offscale=True,
                    )
                    hsl_color = device_color if device_color else 'hsl(0, 0%, 0%)'
                elif pad.get('color'):
                    # Use UI color for device
                    hsl_color = pad['color']
                else:
                    # Unmapped pad - use black
                    hsl_color = 'hsl(0, 0%, 0%)'

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

            # Cache device colors for restoring after note-off highlights
            self._pad_device_colors = {
                (p['x'], p['y']): {'red': p['red'], 'green': p['green'], 'blue': p['blue'], 'color': p['color']}
                for p in pads_with_colors
            }

            # Build and send MIDI message
            builder = MIDITemplateBuilder(self.current_controller)

            # Get controller's configured message delay and ACK config
            delay_ms = self.current_controller.message_delay_ms
            ack_config = self.current_controller.ack_messaging

            # Prefer bulk if available
            if self.current_controller.set_pad_colors_bulk:
                midi_bytes = builder.set_pad_colors_bulk(pads_with_colors)
                if midi_bytes:
                    self.midi_handler.send_raw_bytes(midi_bytes, delay_ms=delay_ms, generation=generation, ack_config=ack_config)
                    logger.info(f"Sent SetPadColorsBulk: {len(pads_with_colors)} pads, {len(midi_bytes)} bytes")

            # Fallback to individual messages - collect all and send together
            # so delay between messages is properly applied
            elif self.current_controller.set_pad_color:
                all_midi_bytes = []
                for pad in pads_with_colors:
                    midi_bytes = builder.set_pad_color(
                        pad['x'], pad['y'],
                        pad['red'], pad['green'], pad['blue'],
                        pad['color']
                    )
                    if midi_bytes:
                        all_midi_bytes.extend(midi_bytes)
                if all_midi_bytes:
                    self.midi_handler.send_raw_bytes(all_midi_bytes, delay_ms=delay_ms, generation=generation, ack_config=ack_config)
                    logger.info(f"Sent SetPadColor for {len(pads_with_colors)} pads ({len(all_midi_bytes)} bytes)")

        except Exception as e:
            logger.error(f"Error sending pad colors: {e}", exc_info=True)

    def _send_pad_colors_to_wooting(self):
        """Push the current per-pad RGB layer to the active Wooting bridge.

        Only pads that have an RGB address mapping are forwarded — anything
        outside the keyboard's addressable matrix is silently skipped.
        """
        bridge = self._wooting_bridge
        if bridge is None or self.current_controller is None:
            return
        addr_map = self.current_controller.build_wooting_rgb_address_map()
        try:
            status = self.get_status()
            controller_pads = status.get('controller_pads', [])
            use_dark_offscale = (
                self.current_layout_config.layout_type == LayoutType.STRING_LIKE
                or (
                    self.current_layout_config.layout_type == LayoutType.ISOMORPHIC
                    and self.tuning_handler.is_edo_compatible
                )
            )
            colors: list[tuple[int, int, int, int, int]] = []
            cache: dict[tuple[int, int], dict] = {}
            for pad in controller_pads:
                coord = (pad['x'], pad['y'])
                if addr_map and coord not in addr_map:
                    continue
                if use_dark_offscale and pad.get('mos_coord') and self.tuning_handler.mos:
                    hsl = self._get_pad_color(
                        mos_coord=pad['mos_coord'], use_dark_offscale=True
                    ) or 'hsl(0, 0%, 0%)'
                else:
                    hsl = pad.get('color') or 'hsl(0, 0%, 0%)'
                r, g, b = self._hsl_to_rgb(hsl)
                colors.append((coord[0], coord[1], r, g, b))
                cache[coord] = {
                    'red': r, 'green': g, 'blue': b, 'color': self._rgb_to_controller_enum((r, g, b))
                }
            bridge.set_pad_colors(colors)
            self._pad_device_colors = cache
        except Exception as exc:
            logger.error("Error pushing colors to Wooting bridge: %s", exc, exc_info=True)

    # Playing highlight color (red, matching UI active note color)
    _PLAYING_RGB = (255, 0, 0)

    def _send_pad_playing_color(self, logical_x: int, logical_y: int, note_on: bool):
        """Send a playing highlight or restore original color for a single pad on the controller."""
        if not self.current_controller:
            return

        # Wooting bridge has its own overlay/clear path; this runs even when
        # there's no MIDI controller_out (Wooting has none).
        if self.current_controller.is_wooting() and self._wooting_bridge is not None:
            try:
                if note_on:
                    r, g, b = self._PLAYING_RGB
                    self._wooting_bridge.set_pad_overlay(logical_x, logical_y, r, g, b)
                else:
                    self._wooting_bridge.clear_pad_overlay(logical_x, logical_y)
            except Exception as exc:
                logger.error("Wooting overlay update failed: %s", exc)
            return

        if not self.current_controller.set_pad_color:
            return
        if not self.midi_handler or not self.midi_handler.controller_out:
            return

        coord = (logical_x, logical_y)
        if note_on:
            rgb = self._PLAYING_RGB
            c = {'red': rgb[0], 'green': rgb[1], 'blue': rgb[2],
                 'color': self._rgb_to_controller_enum(rgb)}
        else:
            c = self._pad_device_colors.get(coord)
            if not c:
                return

        try:
            from .midi_setup import MIDITemplateBuilder
            builder = MIDITemplateBuilder(self.current_controller)
            midi_bytes = builder.set_pad_color(
                logical_x, logical_y,
                c['red'], c['green'], c['blue'],
                c['color']
            )
            if midi_bytes:
                self.midi_handler.send_raw_bytes(midi_bytes, delay_ms=0)
        except Exception as e:
            logger.error(f"Error sending playing color for pad ({logical_x},{logical_y}): {e}")

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
        Convert RGB to controller-specific color enum.

        Uses the color enum mapping from the controller configuration YAML.
        If no mapping is available, returns 0.

        Args:
            rgb: RGB tuple (0-255, 0-255, 0-255)

        Returns:
            Color enum value, or 0 if not applicable
        """
        if not self.current_controller or not self.current_controller.color_enum_to_rgb:
            return 0

        # Find nearest color by Euclidean distance in RGB space
        min_dist = float('inf')
        nearest_enum = 0  # Default to 0 (no color)

        for enum_val, color_rgb in self.current_controller.color_enum_to_rgb.items():
            dist = sum((a - b) ** 2 for a, b in zip(rgb, color_rgb))
            if dist < min_dist:
                min_dist = dist
                nearest_enum = enum_val

        return nearest_enum
