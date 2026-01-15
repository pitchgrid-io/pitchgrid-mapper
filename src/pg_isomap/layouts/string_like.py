"""String-like layout calculator (like guitar/bass)."""

import logging
from typing import Dict, List, Optional, Tuple

from .base import LayoutCalculator, LayoutConfig

logger = logging.getLogger(__name__)


class StringLikeLayout(LayoutCalculator):
    """
    String-like layout calculator.

    Mimics stringed instruments where:
    - Rows are like strings
    - Moving along a row increases/decreases the scale index
    - Different rows start at different scale indices based on row_offset

    Scale index formula:
        scale_index = ((y - y_root) * row_offset) + (x - x_root) + 60

    The scale index is then looked up in coord_to_scale_index (reversed) to get
    the MOS coordinate, which determines the MIDI note.
    """

    def __init__(self, config: LayoutConfig, default_root: Optional[Tuple[int, int]] = None,
                 row_to_col_angle: float = 90.0):
        """
        Initialize string-like layout.

        Args:
            config: Layout configuration
            default_root: Default root coordinate from controller config
            row_to_col_angle: Angle between row and column axes (from controller config)
        """
        super().__init__(config)

        # Root position (where scale index 60 is mapped)
        self.root_x, self.root_y = default_root if default_root else (0, 0)

        # Row offset: how many scale indices each row is offset by (default 5, like guitar fourths)
        self.row_offset = 5

        # Horizontal flip: if True, notes decrease along x instead of increase
        self.flip_horizontal = False

        # Vertical flip: if True, row_offset is negated
        self.flip_vertical = False

        # Determine if controller is quad-like or hex-like
        self.quad_or_hex = 'rect' if 75 < row_to_col_angle < 105 else 'hex'
        logger.info(f"String-like layout mode: {self.quad_or_hex} (RowToColAngle={row_to_col_angle}°)")

        # Cache for reverse lookup: scale_index -> mos_coord
        self._index_to_mos_coord: Dict[int, Tuple[int, int]] = {}

    def set_root(self, root_x: int, root_y: int):
        """Set the root position."""
        self.root_x = root_x
        self.root_y = root_y

    def apply_transformation(self, transform_type: str):
        """
        Apply a transformation to the string-like layout.

        Args:
            transform_type: One of 'shift_left', 'shift_right', 'shift_up', 'shift_down',
                          'skew_left', 'skew_right', 'reflect_horizontal', 'reflect_vertical'
        """
        if transform_type == 'shift_left':
            self.root_x -= 1
        elif transform_type == 'shift_right':
            self.root_x += 1
        elif transform_type == 'shift_up':
            self.root_y += 1
        elif transform_type == 'shift_down':
            self.root_y -= 1
        elif transform_type == 'skew_left':
            self.row_offset -= 1
        elif transform_type == 'skew_right':
            self.row_offset += 1
        elif transform_type == 'reflect_vertical':
            # Reflect vertically: flip vertical direction
            self.flip_vertical = not self.flip_vertical
        elif transform_type == 'reflect_horizontal':
            # Reflect horizontally: flip horizontal direction
            self.flip_horizontal = not self.flip_horizontal
        else:
            logger.warning(f"Unknown transformation type for string-like layout: {transform_type}")

        logger.info(f"String-like transform {transform_type}: root=({self.root_x}, {self.root_y}), "
                   f"row_offset={self.row_offset}, flip_h={self.flip_horizontal}, flip_v={self.flip_vertical}")

    def _build_reverse_lookup(self, coord_to_scale_index: Dict[Tuple[int, int], int]):
        """Build reverse lookup from scale index to MOS coordinate."""
        self._index_to_mos_coord = {}
        for mos_coord, scale_index in coord_to_scale_index.items():
            self._index_to_mos_coord[scale_index] = mos_coord

    def _get_scale_index(self, logical_x: int, logical_y: int) -> int:
        """
        Calculate scale index from logical coordinates.

        Formula: scale_index = ((y - y_root) * row_offset) + (x - x_root) + 60
        If flip_horizontal, the x contribution is negated.
        If flip_vertical, the y contribution is negated.
        """
        x_delta = logical_x - self.root_x
        if self.flip_horizontal:
            x_delta = -x_delta

        y_delta = logical_y - self.root_y
        if self.flip_vertical:
            y_delta = -y_delta

        return (y_delta * self.row_offset) + x_delta + 60

    def calculate_mapping(
        self,
        logical_coords: List[Tuple[int, int]],
        scale_degrees: List[int],
        scale_size: int,
        coord_to_scale_index: Optional[Dict[Tuple[int, int], int]] = None,
        **kwargs
    ) -> Dict[Tuple[int, int], int]:
        """
        Calculate string-like mapping.

        Args:
            logical_coords: List of available (logical_x, logical_y) coordinates
            scale_degrees: List of scale degrees (not used directly)
            scale_size: Total EDO steps
            coord_to_scale_index: Mapping from MOS natural coordinate to scale index
            **kwargs: Additional arguments (mos, etc.)

        Returns:
            Dict mapping (logical_x, logical_y) -> MIDI note number (scale index)
        """
        mapping = {}

        if coord_to_scale_index is None:
            logger.warning("String-like layout requires coord_to_scale_index")
            return mapping

        # Build reverse lookup
        self._build_reverse_lookup(coord_to_scale_index)

        for logical_x, logical_y in logical_coords:
            # Calculate scale index for this position
            scale_index = self._get_scale_index(logical_x, logical_y)

            # Check if this scale index exists in the scale
            if scale_index in self._index_to_mos_coord:
                # Clamp to MIDI range
                if 0 <= scale_index <= 127:
                    mapping[(logical_x, logical_y)] = scale_index

        logger.info(f"String-like layout calculated: {len(mapping)} mapped pads, "
                   f"row_offset={self.row_offset}, root=({self.root_x}, {self.root_y})")
        return mapping

    def get_unmapped_coords(
        self,
        logical_coords: List[Tuple[int, int]]
    ) -> List[Tuple[int, int]]:
        """Get unmapped coordinates (those outside scale range)."""
        # TODO: Calculate which coords produce indices outside the scale
        return []

    def get_mos_coordinate(self, logical_x: int, logical_y: int) -> Optional[Tuple[int, int]]:
        """
        Get the MOS natural coordinate for a logical coordinate.

        Args:
            logical_x: Logical X coordinate
            logical_y: Logical Y coordinate

        Returns:
            Tuple of (mos_x, mos_y) natural coordinates, or None if unmapped
        """
        scale_index = self._get_scale_index(logical_x, logical_y)
        return self._index_to_mos_coord.get(scale_index)
