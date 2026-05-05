"""
Controller configuration loader and manager.

Loads YAML configuration files for different isomorphic controllers.
"""

import logging
import math
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import yaml
from scipy.spatial import Voronoi

logger = logging.getLogger(__name__)


def find_midi_response_type_position(template: str) -> Optional[int]:
    """
    Parse a MIDI response template and find the byte position of MIDI_RESPONSE_TYPE.

    Args:
        template: Template string like "240 MANUFACTURER_CODE boardIndex(x, y) SET_KEY_COLOUR MIDI_RESPONSE_TYPE 247"

    Returns:
        The byte position where MIDI_RESPONSE_TYPE appears, or None if not found
    """
    if not template or 'MIDI_RESPONSE_TYPE' not in template:
        return None

    # First, handle function calls with spaces by collapsing them
    # e.g., "boardIndex(x, y)" might be split as "boardIndex(x," and "y)"
    # We need to combine these back together
    import re
    # Replace "func(x, y)" patterns with "func(...)" to avoid space splitting issues
    normalized = re.sub(r'\w+\([^)]+\)', 'FUNC_CALL', template)

    tokens = normalized.split()
    position = 0

    for token in tokens:
        if token == 'MIDI_RESPONSE_TYPE':
            return position
        # Count how many bytes this token represents
        # Tokens can be: numbers, hex (0x...), multi-byte constants, or function calls
        if token.startswith('{') and token.endswith('}'):
            # Expression like {red >> 4} - counts as 1 byte
            position += 1
        elif token == 'FUNC_CALL':
            # Function call placeholder - counts as 1 byte
            position += 1
        elif token.isdigit() or token.startswith('0x'):
            # Single byte number
            position += 1
        else:
            # Named constant - need to count how many bytes it represents
            # Multi-byte constants like MANUFACTURER_CODE need special handling
            if token == 'MANUFACTURER_CODE':
                position += 3  # Lumatone manufacturer code is 3 bytes: 00 21 50
            else:
                position += 1

    return None


@dataclass
class DynamicUIOption:
    """A dynamic UI option defined in controller config YAML."""
    label: str
    name: str          # Variable name used in ControllerSetupCommands, e.g. "INVERT_SUSTAIN"
    type: str          # "bool" or "int"
    default: Any       # Default value
    min_val: Optional[int] = None  # For int type
    max_val: Optional[int] = None  # For int type


@dataclass
class ACKResponseType:
    """Represents a possible response type in ACK-based messaging."""
    name: str
    value: int
    action: str  # 'abort', 'next', or 'delay(ms)'


@dataclass
class ACKMessagingConfig:
    """Configuration for ACK-based MIDI messaging."""
    timeout_ms: int = 2000
    response_position: int = 5  # Default position in SysEx response where status byte is found
    response_types: List[ACKResponseType] = field(default_factory=list)

    def get_action_for_value(self, value: int) -> Optional[str]:
        """Get the action for a given response value."""
        for rt in self.response_types:
            if rt.value == value:
                return rt.action
        return None


class ControllerConfig:
    """Configuration for an isomorphic controller."""

    def __init__(self, config_path: Path):
        self.config_path = config_path

        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)

        # Basic properties
        self.device_name: str = self.config['DeviceName']
        # Controller MIDI ports:
        # - controller_midi_output: port from which controller sends note messages (we listen here)
        # - controller_midi_input: port to which we send setup/color messages
        self.controller_midi_output: Optional[str] = self.config.get('ControllerMIDIOutput')
        self.controller_midi_input: Optional[str] = self.config.get('ControllerMIDIInput')
        # Handle "none" string as None
        if self.controller_midi_output == "none":
            self.controller_midi_output = None
        if self.controller_midi_input == "none":
            self.controller_midi_input = None
        # Legacy support for old config format
        if not self.controller_midi_output and 'MIDIDeviceName' in self.config:
            self.controller_midi_output = self.config['MIDIDeviceName']
            self.controller_midi_input = self.config['MIDIDeviceName']
        self.virtual_midi_device_name: str = self.config.get(
            'virtualMIDIDeviceName', f"PG {self.device_name}"
        )
        self.is_mpe: bool = self.config['isMPE']
        self.has_global_pitch_bend: bool = self.config['hasGlobalPitchBend']

        # Grid geometry
        self.num_rows: int = self.config['NumRows']
        self.first_row_idx: int = self.config['FirstRowIdx']
        self.row_lengths: List[int] = self.config['RowLengths']
        self.row_offsets: List[int] = self.config['RowOffsets']

        # Physical layout
        self.horizon_to_row_angle: float = self.config['HorizonToRowAngle']
        self.row_to_col_angle: float = self.config['RowToColAngle']
        self.x_spacing: float = self.config['xSpacing']
        self.y_spacing: float = self.config['ySpacing']

        # Optional properties
        self.fixed_labels: Optional[List] = self.config.get('fixedLabels')
        self.default_iso_root_coordinate: Optional[Tuple[int, int]] = None
        if 'defaultIsoRootCoordinate' in self.config:
            coord = self.config['defaultIsoRootCoordinate']
            self.default_iso_root_coordinate = (coord[0], coord[1])

        # Wooting analog bridge metadata. The presence of `wootingKeycodeMap`
        # marks this controller as one driven by the native Wooting bridge
        # rather than by a MIDI port — see `is_wooting()` below.
        self.wooting_keycode_map: Dict[int, Tuple[int, int]] = {}
        if 'wootingKeycodeMap' in self.config:
            raw = self.config['wootingKeycodeMap']
            if isinstance(raw, dict):
                for k, v in raw.items():
                    self.wooting_keycode_map[int(k)] = (int(v[0]), int(v[1]))
        self.wooting_rgb_address_map: Dict[Tuple[int, int], Tuple[int, int]] = {}
        if 'wootingRgbAddressMap' in self.config:
            raw = self.config['wootingRgbAddressMap']
            # Accept a list of {x, y, row, col} entries (preferred — YAML
            # cannot hash list keys) or a dict of "x,y" string keys.
            if isinstance(raw, list):
                for entry in raw:
                    self.wooting_rgb_address_map[
                        (int(entry['x']), int(entry['y']))
                    ] = (int(entry['row']), int(entry['col']))
            elif isinstance(raw, dict):
                for k, v in raw.items():
                    if isinstance(k, str) and ',' in k:
                        sx_, sy_ = k.split(',', 1)
                        self.wooting_rgb_address_map[
                            (int(sx_.strip()), int(sy_.strip()))
                        ] = (int(v[0]), int(v[1]))
        self.wooting_vendor_id: Optional[int] = self.config.get('wootingVendorId')
        self.wooting_product_id: Optional[int] = self.config.get('wootingProductId')
        self.wooting_device_label: Optional[str] = self.config.get('wootingDeviceLabel')

        # MIDI commands (templates)
        self.set_pad_note_and_channel: Optional[str] = self.config.get('SetPadNoteAndChannel')
        self.set_pad_color: Optional[str] = self.config.get('SetPadColor')
        self.set_pad_notes_bulk: Optional[str] = self.config.get('SetPadNotesBulk')
        self.set_pad_colors_bulk: Optional[str] = self.config.get('SetPadColorsBulk')

        # Message timing - delay between consecutive MIDI messages (in milliseconds)
        # Default is 1.5ms, but some controllers (like Lumatone) need longer delays
        self.message_delay_ms: float = self.config.get('MessageDelayMs', 1.5)

        # ACK-based messaging configuration (for controllers like Lumatone)
        self.ack_messaging: Optional[ACKMessagingConfig] = None
        self.set_pad_color_response: Optional[str] = self.config.get('SetPadColorResponse')
        self.set_pad_note_and_channel_response: Optional[str] = self.config.get('SetPadNoteAndChannelResponse')

        if 'ACKBasedMIDIMessaging' in self.config:
            ack_config = self.config['ACKBasedMIDIMessaging']
            timeout = ack_config.get('Timeout', 2000)

            # Auto-detect response position from SetPadColorResponse or SetPadNoteAndChannelResponse template
            response_position = None
            if self.set_pad_color_response:
                response_position = find_midi_response_type_position(self.set_pad_color_response)
            if response_position is None and self.set_pad_note_and_channel_response:
                response_position = find_midi_response_type_position(self.set_pad_note_and_channel_response)

            # Fallback to manual config or default
            if response_position is None:
                response_position = ack_config.get('ResponsePosition', 5)
                logger.warning(f"Could not auto-detect MIDI_RESPONSE_TYPE position, using {response_position}")

            response_types = []
            for rt in ack_config.get('ResponseTypes', []):
                # Parse value - handle hex strings like "0x01"
                value = rt.get('Value', 0)
                if isinstance(value, str):
                    value = int(value, 0)  # Auto-detect base (handles 0x prefix)
                response_types.append(ACKResponseType(
                    name=rt.get('Name', ''),
                    value=value,
                    action=rt.get('Action', 'abort')
                ))
            self.ack_messaging = ACKMessagingConfig(
                timeout_ms=timeout,
                response_position=response_position,
                response_types=response_types
            )
            logger.info(f"Loaded ACK messaging config for {self.device_name}: timeout={timeout}ms, response_pos={response_position}, {len(response_types)} response types")

        # Color mapping (for controllers with discrete color enums like LinnStrument)
        self.color_enum_to_rgb: Optional[Dict[int, Tuple[int, int, int]]] = None
        if 'params' in self.config and 'color' in self.config['params']:
            color_param = self.config['params']['color']
            if color_param.get('type') == 'enum' and 'values' in color_param:
                self.color_enum_to_rgb = {}
                for enum_val, color_data in color_param['values'].items():
                    rgb = color_data.get('rgb')
                    if rgb and all(x is not None for x in rgb):
                        self.color_enum_to_rgb[int(enum_val)] = tuple(rgb)
                logger.info(f"Loaded {len(self.color_enum_to_rgb)} color enum mappings for {self.device_name}")

        # Note mapping functions
        self.note_to_coord_x: Optional[str] = self.config.get('noteToCoordX')
        self.note_to_coord_y: Optional[str] = self.config.get('noteToCoordY')
        self.note_assign: Optional[str] = self.config.get('noteAssign')
        self.channel_assign: Optional[str] = self.config.get('channelAssign')

        # Helper functions defined in config (e.g., boardIndex, keyIndex for Lumatone)
        self._helper_expressions: Dict[str, str] = {}
        helper_keys = ['boardIndex', 'xB0', 'yB0', 'keyIndex']
        for key in helper_keys:
            if key in self.config:
                self._helper_expressions[key] = self.config[key]

        # Dynamic UI options (defined in YAML, rendered in frontend)
        self.dynamic_ui_options: List[DynamicUIOption] = []
        for opt_data in self.config.get('DynamicUIOptions', []):
            opt_type = opt_data.get('type', 'bool')
            self.dynamic_ui_options.append(DynamicUIOption(
                label=opt_data['label'],
                name=opt_data['name'],
                type=opt_type,
                default=opt_data.get('default', False if opt_type == 'bool' else 0),
                min_val=opt_data.get('min') if opt_type == 'int' else None,
                max_val=opt_data.get('max') if opt_type == 'int' else None,
            ))

        # Controller setup commands (templates with {OPTION_NAME} placeholders)
        self.controller_setup_commands: List[str] = self.config.get('ControllerSetupCommands', [])

        # Generate pad coordinates
        self.pads: List[Tuple[int, int, float, float]] = self._generate_pad_coordinates()
        # Calculate cumulative indices
        self.cumulative_index_by_pad_coord: Dict[Tuple[int, int], int] = {(x,y):idx for idx, (x, y, _, _) in enumerate(self.pads)}

        # Calculate Voronoi shapes for each pad
        self.pad_shapes: Dict[Tuple[int, int], List[Tuple[float, float]]] = (
            self._calculate_voronoi_shapes()
        )

        logger.info(f"Loaded controller config: {self.device_name} ({len(self.pads)} pads)")

    def _generate_pad_coordinates(self) -> List[Tuple[int, int, float, float]]:
        """
        Generate logical and physical coordinates for all pads.

        Returns:
            List of (logical_x, logical_y, phys_x, phys_y) tuples
        """
        pads = []

        # Calculate cumulative row offset
        cumulative_row_offset = -sum([
            self.row_offsets[e]
            for e, _ in enumerate(range(self.first_row_idx, 0))
        ])

        # Precompute angles
        x_angle_rad = math.radians(self.horizon_to_row_angle)
        y_angle_rad = math.radians(self.row_to_col_angle + self.horizon_to_row_angle)

        for row_idx in range(self.num_rows):
            row = self.first_row_idx + row_idx
            row_length = self.row_lengths[row_idx]

            if row_idx > 0:
                row_offset = self.row_offsets[row_idx - 1]
                cumulative_row_offset += row_offset

            for col_idx in range(row_length):
                logical_x = cumulative_row_offset + col_idx
                logical_y = row

                # Convert to physical coordinates
                phys_x = (
                    logical_x * self.x_spacing * math.cos(x_angle_rad) +
                    logical_y * self.y_spacing * math.cos(y_angle_rad)
                )
                phys_y = (
                    logical_x * self.x_spacing * math.sin(x_angle_rad) +
                    logical_y * self.y_spacing * math.sin(y_angle_rad)
                )

                pads.append((logical_x, logical_y, phys_x, -phys_y))

        return pads

    def cumulativeIndex(self, x: int, y: int) -> int:
        """Get cumulative index for a logical coordinate."""
        return self.cumulative_index_by_pad_coord.get((x, y), 0)

    def get_logical_coordinates(self) -> List[Tuple[int, int]]:
        """Get list of all logical coordinates."""
        return [(x, y) for x, y, _, _ in self.pads]

    def controller_note_to_logical_coord(self, note: int) -> Optional[Tuple[int, int]]:
        """
        Convert controller MIDI note to logical coordinate.

        This uses the noteToCoordX and noteToCoordY expressions from config.
        """
        if not self.note_to_coord_x or not self.note_to_coord_y:
            return None

        try:
            # Safe eval with limited scope
            scope = {'noteNumber': note}
            x = eval(self.note_to_coord_x, {"__builtins__": {}}, scope)
            y = eval(self.note_to_coord_y, {"__builtins__": {}}, scope)
            return (int(x), int(y))
        except Exception as e:
            logger.error(f"Error converting note {note} to coordinates: {e}")
            return None

    def _build_helper_scope(self, x: int, y: int) -> Dict:
        """
        Build a scope dictionary with helper functions for eval.

        This creates callable functions from helper expressions defined in config
        (e.g., boardIndex, xB0, yB0, keyIndex for Lumatone).
        """
        scope = {
            'x': x,
            'y': y,
            'cumulativeIndex': self.cumulativeIndex,
        }

        # Create callable functions from helper expressions
        # These need to be created in dependency order and reference each other
        for name, expr in self._helper_expressions.items():
            # Create a function that evaluates the expression with given x, y
            # We need to capture the expression and scope in a closure
            def make_helper(expr_str, current_scope):
                def helper(hx, hy):
                    # Build a new scope for this call with the helper's x, y
                    helper_scope = dict(current_scope)
                    helper_scope['x'] = hx
                    helper_scope['y'] = hy
                    return eval(expr_str, {"__builtins__": {}}, helper_scope)
                return helper

            scope[name] = make_helper(expr, scope)

        return scope

    def logical_coord_to_controller_note(self, x: int, y: int) -> Optional[int]:
        """
        Calculate controller MIDI note from logical coordinate using noteAssign.

        Args:
            x: Logical X coordinate
            y: Logical Y coordinate

        Returns:
            MIDI note number assigned to this coordinate, or None if noteAssign not defined
        """
        if not self.note_assign:
            return None

        try:
            scope = self._build_helper_scope(x, y)
            note = eval(self.note_assign, {"__builtins__": {}}, scope)
            return int(note)
        except Exception as e:
            logger.error(f"Error calculating controller note for ({x}, {y}): {e}")
            return None

    def is_wooting(self) -> bool:
        """True if this YAML config drives the native Wooting analog bridge."""
        return bool(self.wooting_keycode_map)

    def build_wooting_keymap_for_bridge(self) -> Dict[int, Tuple[int, int, int]]:
        """Build the (HID keycode -> (x, y, controller_note)) dict the bridge wants.

        The third tuple element is the same controller MIDI note number any
        non-Wooting MIDI controller would emit at this position, so the host's
        reverse-mapping table picks up Wooting events identically.
        """
        out: Dict[int, Tuple[int, int, int]] = {}
        for keycode, (lx, ly) in self.wooting_keycode_map.items():
            note = self.logical_coord_to_controller_note(lx, ly)
            if note is None:
                continue
            out[keycode] = (lx, ly, int(note) & 0x7F)
        return out

    def logical_coord_to_controller_channel(self, x: int, y: int) -> int:
        """
        Calculate controller MIDI channel from logical coordinate using channelAssign.

        Args:
            x: Logical X coordinate
            y: Logical Y coordinate

        Returns:
            MIDI channel (0-15), defaults to 0 if channelAssign not defined
        """
        if not self.channel_assign:
            return 0

        try:
            scope = self._build_helper_scope(x, y)
            channel = eval(self.channel_assign, {"__builtins__": {}}, scope)
            return int(channel)
        except Exception as e:
            logger.error(f"Error calculating controller channel for ({x}, {y}): {e}")
            return 0

    def build_controller_note_mapping(self) -> Dict[Tuple[int, int], Tuple[int, int]]:
        """
        Build reverse mapping from (channel, note) to logical coordinate.

        Returns:
            Dictionary mapping (channel, controller_note) -> (logical_x, logical_y)
        """
        reverse_mapping = {}

        for logical_x, logical_y, _, _ in self.pads:
            controller_note = self.logical_coord_to_controller_note(logical_x, logical_y)
            controller_channel = self.logical_coord_to_controller_channel(logical_x, logical_y)
            if controller_note is not None and 0 <= controller_note <= 127:
                reverse_mapping[(controller_channel, controller_note)] = (logical_x, logical_y)

        logger.info(f"Built controller note mapping: {len(reverse_mapping)} pads mapped")
        return reverse_mapping

    def _logical_to_physical(self, logical_x: int, logical_y: int) -> Tuple[float, float]:
        """Convert logical coordinates to physical coordinates."""
        x_angle_rad = math.radians(self.horizon_to_row_angle)
        y_angle_rad = math.radians(self.row_to_col_angle + self.horizon_to_row_angle)

        phys_x = (
            logical_x * self.x_spacing * math.cos(x_angle_rad) +
            logical_y * self.y_spacing * math.cos(y_angle_rad)
        )
        phys_y = (
            logical_x * self.x_spacing * math.sin(x_angle_rad) +
            logical_y * self.y_spacing * math.sin(y_angle_rad)
        )

        return phys_x, -phys_y

    def _calculate_voronoi_shapes(
        self, shrink_factor: float = 0.9
    ) -> Dict[Tuple[int, int], List[Tuple[float, float]]]:
        """
        Calculate Voronoi polygon vertices for each pad.

        Args:
            shrink_factor: Factor to shrink polygons for visual spacing (0.0-1.0)

        Returns:
            Dictionary mapping (logical_x, logical_y) -> list of (x, y) vertices
        """
        pad_shapes = {}

        for logical_x, logical_y, _, _ in self.pads:
            # Create a 3x3 grid of logical coordinates around this pad
            logical_coords = []
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    phys_x, phys_y = self._logical_to_physical(
                        logical_x + dx, logical_y + dy
                    )
                    logical_coords.append([phys_x, phys_y])

            points = np.array(logical_coords)

            try:
                vor = Voronoi(points)

                # Get region for central point (index 4 in 3x3 grid)
                center_index = 4
                region_index = vor.point_region[center_index]
                region = vor.regions[region_index]

                if -1 in region or len(region) == 0:
                    # Infinite region or invalid, use default hexagon
                    vertices = self._default_hexagon(shrink_factor)
                else:
                    # Get vertices from Voronoi
                    vertices = vor.vertices[region]
                    # Close the polygon
                    vertices = np.append(vertices, [vertices[0]], axis=0)

                    # Apply shrink factor relative to centroid
                    centroid = np.mean(vertices[:-1], axis=0)
                    vertices = centroid + (vertices - centroid) * shrink_factor

                # Convert to list of tuples
                pad_shapes[(logical_x, logical_y)] = [
                    (float(x), float(y)) for x, y in vertices
                ]

            except Exception as e:
                logger.warning(
                    f"Failed to calculate Voronoi for pad ({logical_x}, {logical_y}): {e}"
                )
                # Use default hexagon
                pad_shapes[(logical_x, logical_y)] = self._default_hexagon(shrink_factor)

        return pad_shapes

    def _default_hexagon(self, shrink_factor: float = 0.9) -> List[Tuple[float, float]]:
        """Generate a default hexagon shape for pads when Voronoi fails."""
        v_scale = self.y_spacing / max(self.x_spacing, 1.0)
        size = 0.5 * self.x_spacing * shrink_factor

        vertices = [
            (0, v_scale * size),
            (size, 0.5 * v_scale * size),
            (size, -0.5 * v_scale * size),
            (0, -v_scale * size),
            (-size, -0.5 * v_scale * size),
            (-size, 0.5 * v_scale * size),
            (0, v_scale * size),  # Close the polygon
        ]

        return vertices


class ControllerManager:
    """Manages available controller configurations."""

    def __init__(self, config_dir: Path):
        self.config_dir = config_dir
        self.configs: Dict[str, ControllerConfig] = {}
        self.load_configs()

    def load_configs(self):
        """Load all controller configurations from directory."""
        if not self.config_dir.exists():
            logger.warning(f"Controller config directory not found: {self.config_dir}")
            return

        for yaml_file in self.config_dir.glob("*.yaml"):
            try:
                config = ControllerConfig(yaml_file)
                self.configs[config.device_name] = config
                logger.info(f"Loaded controller: {config.device_name}")
            except Exception as e:
                logger.error(f"Failed to load {yaml_file}: {e}")

    def get_config(self, device_name: str) -> Optional[ControllerConfig]:
        """Get configuration for a specific device."""
        return self.configs.get(device_name)

    def get_all_device_names(self) -> List[str]:
        """Get list of all configured device names."""
        return list(self.configs.keys())

    def match_midi_port_to_config(self, port_name: str) -> Optional[ControllerConfig]:
        """
        Try to match a MIDI port name to a controller configuration.

        Args:
            port_name: MIDI port name from rtmidi

        Returns:
            Matching ControllerConfig or None
        """
        for config in self.configs.values():
            # Check against controller's output port (the port we listen on)
            if config.controller_midi_output and config.controller_midi_output.lower() in port_name.lower():
                return config
        return None
