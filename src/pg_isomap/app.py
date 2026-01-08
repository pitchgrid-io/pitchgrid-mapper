"""
Main application coordinator.

Manages the lifecycle of all components and coordinates between them.
"""

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

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

logger = logging.getLogger(__name__)


class PGIsomapApp:
    """Main application coordinator."""

    def __init__(self):
        # Components
        self.controller_manager = ControllerManager(settings.controller_config_dir)
        self.midi_handler = MIDIHandler(settings.virtual_midi_device_name)
        self.osc_handler = OSCHandler(settings.osc_listen_host, settings.osc_listen_port)

        # State
        self.current_controller: Optional[ControllerConfig] = None
        self.current_layout_config: LayoutConfig = LayoutConfig(layout_type=LayoutType.ISOMORPHIC)
        self.current_layout_calculator: Optional[LayoutCalculator] = None

        # Discovery thread
        self._discovery_thread: Optional[threading.Thread] = None
        self._discovery_running = False

        # Current scale data from PitchGrid
        self.current_scale_degrees: list[int] = []
        self.current_scale_size: int = 12

        # Setup callbacks
        self.osc_handler.on_scale_update = self._handle_scale_update
        self.osc_handler.on_note_mapping = self._handle_note_mapping

    def start(self):
        """Start the application."""
        logger.info("Starting PG Isomap...")

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

        # Try to auto-load computer keyboard config
        self._try_load_computer_keyboard()

        logger.info("PG Isomap started successfully")
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

    def _stop_discovery(self):
        """Stop controller discovery thread."""
        self._discovery_running = False
        if self._discovery_thread:
            self._discovery_thread.join(timeout=5.0)
            self._discovery_thread = None

    def _discovery_loop(self):
        """Periodically scan for controllers."""
        while self._discovery_running:
            try:
                available_ports = self.midi_handler.get_available_controllers()
                # This can be used to update UI with available controllers
                # For now, just log
                logger.debug(f"Available MIDI ports: {available_ports}")

            except Exception as e:
                logger.error(f"Error in discovery loop: {e}")

            time.sleep(settings.discovery_interval_seconds)

    def _try_load_computer_keyboard(self):
        """Try to load computer keyboard config on startup."""
        kb_config = self.controller_manager.get_config("ComputerKeyboard")
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

        # Try to connect
        if not self.midi_handler.connect_to_controller(config.midi_device_name):
            return False

        # Update current controller
        self.current_controller = config

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

    def _recalculate_layout(self):
        """Recalculate layout mapping and update MIDI handler."""
        if not self.current_controller:
            logger.warning("No controller loaded, cannot calculate layout")
            return

        # Create layout calculator
        if self.current_layout_config.layout_type == LayoutType.ISOMORPHIC:
            self.current_layout_calculator = IsomorphicLayout(self.current_layout_config)
        elif self.current_layout_config.layout_type == LayoutType.STRING_LIKE:
            self.current_layout_calculator = StringLikeLayout(self.current_layout_config)
        elif self.current_layout_config.layout_type == LayoutType.PIANO_LIKE:
            self.current_layout_calculator = PianoLikeLayout(self.current_layout_config)
        else:
            logger.error(f"Unsupported layout type: {self.current_layout_config.layout_type}")
            return

        # Get logical coordinates from controller
        logical_coords = self.current_controller.get_logical_coordinates()

        # Calculate mapping
        mapping = self.current_layout_calculator.calculate_mapping(
            logical_coords,
            self.current_scale_degrees,
            self.current_scale_size
        )

        # Build reverse mapping (controller_note -> logical_coord)
        # This requires knowing how the controller maps its physical pads to MIDI notes
        reverse_mapping: Dict[int, Tuple[int, int]] = {}

        for logical_x, logical_y in logical_coords:
            # Use controller's note mapping function if available
            controller_note = self._logical_to_controller_note(logical_x, logical_y)
            if controller_note is not None:
                reverse_mapping[controller_note] = (logical_x, logical_y)

        # Update MIDI handler
        self.midi_handler.update_note_mapping(mapping, reverse_mapping)

        logger.info(
            f"Layout recalculated: {len(mapping)} mapped pads, "
            f"{len(reverse_mapping)} reverse mappings"
        )

    def _logical_to_controller_note(self, logical_x: int, logical_y: int) -> Optional[int]:
        """
        Convert logical coordinate to controller's native MIDI note number.

        This is controller-specific. For now, use a simple formula.
        Real implementation should use controller config.
        """
        if not self.current_controller:
            return None

        # For LinnStrument: note = x + y * 16
        # For other controllers, this varies
        # TODO: Make this configurable in controller YAML

        # Simple default: assume row-major layout
        note = logical_x + logical_y * 16

        if 0 <= note <= 127:
            return note
        return None

    def _handle_scale_update(self, scale_data: dict):
        """Handle scale update from PitchGrid plugin."""
        logger.info("Received scale update from PitchGrid")

        # Parse scale data
        # TODO: Implement based on actual PitchGrid OSC format
        # For now, use a default scale
        self.current_scale_degrees = list(range(60, 72))  # C major placeholder
        self.current_scale_size = 12

        # Recalculate layout
        self._recalculate_layout()

    def _handle_note_mapping(self, mapping_data: dict):
        """Handle note mapping update from PitchGrid plugin."""
        logger.info("Received note mapping from PitchGrid")

        # Parse and apply note mapping
        # TODO: Implement based on actual PitchGrid OSC format

    def get_status(self) -> dict:
        """Get current application status."""
        return {
            'connected_controller': self.current_controller.device_name if self.current_controller else None,
            'layout_type': self.current_layout_config.layout_type.value,
            'virtual_midi_device': settings.virtual_midi_device_name,
            'available_controllers': self.controller_manager.get_all_device_names(),
            'midi_stats': {
                'messages_processed': self.midi_handler.messages_processed,
                'notes_remapped': self.midi_handler.notes_remapped,
            }
        }
