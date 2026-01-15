"""Piano-like layout calculator (unfolded/mosaic)."""

import logging
from typing import Dict, List, Optional, Tuple

import scalatrix as sx

from .base import LayoutCalculator, LayoutConfig

logger = logging.getLogger(__name__)


class PianoLikeLayout(LayoutCalculator):
    """
    Piano-like layout calculator.

    Creates a mosaic/unfolded piano layout where:
    - Controller rows are grouped into "piano strips" of configurable width (num_rows)
    - Within each strip, one row is the "scale row" showing scale degrees
    - Rows above the scale row show notes with positive accidentals (+chroma_vec)
    - Rows below the scale row show notes with negative accidentals (-chroma_vec)
    - Multiple piano strips are stacked vertically, each offset by row_offset scale degrees

    Parameters:
        strip_width: Number of rows per piano strip (default 2, commonly 2-4)
        scale_row_index: Which row within the strip is the scale row (0 to strip_width-1)
        row_offset: How many scale degrees separate adjacent piano strips (default 5)
        root_x, root_y: Root position where scale index 60 is mapped

    Scale index formula within a strip:
        scale_index = strip_base + (x - root_x) + 60
        where strip_base = strip_number * row_offset * scale_size

    Accidental calculation:
        accidental = (y_within_strip - scale_row_index)
        mos_coord = base_mos_coord + chroma_vec * accidental
    """

    def __init__(self, config: LayoutConfig, default_root: Optional[Tuple[int, int]] = None,
                 row_to_col_angle: float = 90.0):
        """
        Initialize piano-like layout.

        Args:
            config: Layout configuration
            default_root: Default root coordinate from controller config
            row_to_col_angle: Angle between row and column axes (from controller config)
        """
        super().__init__(config)

        # Determine if controller is quad-like or hex-like
        self.quad_or_hex = 'rect' if 75 < row_to_col_angle < 105 else 'hex'
        logger.info(f"Piano-like layout mode: {self.quad_or_hex} (RowToColAngle={row_to_col_angle}°)")

        # Root position (where scale index 60 is mapped on the scale row)
        self.root_x, self.root_y = default_root if default_root else (0, 0)

        # Piano strip parameters
        self.strip_width = 2  # Number of rows per piano strip
        self.scale_row_index = 0  # Which row within strip is the scale row (0 = bottom)

        # Row offset: how many scale indices separate adjacent piano strips
        self.row_offset = 7

        # Cache for MOS data
        self._mos: Optional[sx.MOS] = None
        self._coord_to_scale_index: Dict[Tuple[int, int], int] = {}
        self._scale_index_to_mos_coord: Dict[int, Tuple[int, int]] = {}

        # Cache for computed pad -> mos_coord mapping
        self._pad_to_mos_coord: Dict[Tuple[int, int], Tuple[int, int]] = {}

        # Controller row count (set during calculate_mapping)
        self._controller_rows = 0

    def set_root(self, root_x: int, root_y: int):
        """Set the root position."""
        self.root_x = root_x
        self.root_y = root_y

    def apply_transformation(self, transform_type: str):
        """
        Apply a transformation to the piano-like layout.

        Args:
            transform_type: One of:
                - 'shift_left', 'shift_right': Move along scale axis
                - 'shift_up', 'shift_down': Move between piano strips
                - 'skew_left', 'skew_right': Adjust row_offset
                - 'increase_strip_width', 'decrease_strip_width': Change strip width
                - 'scale_row_up', 'scale_row_down': Move scale row within strip
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
        elif transform_type == 'increase_strip_width':
            self.strip_width = min(self.strip_width + 1, self._controller_rows if self._controller_rows > 0 else 6)
            # Ensure scale_row_index is still valid
            if self.scale_row_index >= self.strip_width:
                self.scale_row_index = self.strip_width - 1
        elif transform_type == 'decrease_strip_width':
            self.strip_width = max(self.strip_width - 1, 1)
            # Ensure scale_row_index is still valid
            if self.scale_row_index >= self.strip_width:
                self.scale_row_index = self.strip_width - 1
        elif transform_type == 'scale_row_up':
            self.scale_row_index = min(self.scale_row_index + 1, self.strip_width - 1)
        elif transform_type == 'scale_row_down':
            self.scale_row_index = max(self.scale_row_index - 1, 0)
        else:
            logger.warning(f"Unknown transformation type for piano-like layout: {transform_type}")

        logger.info(f"Piano-like transform {transform_type}: root=({self.root_x}, {self.root_y}), "
                   f"strip_width={self.strip_width}, scale_row={self.scale_row_index}, "
                   f"row_offset={self.row_offset}")


    def calculate_mapping(
        self,
        logical_coords: List[Tuple[int, int]],
        scale_degrees: List[int],
        scale_size: int,
        mos: Optional[sx.MOS] = None,
        coord_to_scale_index: Optional[Dict[Tuple[int, int], int]] = None,
        **kwargs
    ) -> Dict[Tuple[int, int], int]:
        """
        Calculate piano-like mapping.

        Args:
            logical_coords: List of available (logical_x, logical_y) coordinates
            scale_degrees: List of scale degrees (not used directly)
            scale_size: Total scale size (mos.n)
            mos: The MOS object for chroma_vec and coordinate transforms
            coord_to_scale_index: Mapping from MOS natural coordinate to scale index
            **kwargs: Additional arguments

        Returns:
            Dict mapping (logical_x, logical_y) -> MIDI note number (scale index)
        """
        mapping = {}
        self._pad_to_mos_coord = {}

        if coord_to_scale_index is None or mos is None:
            logger.warning("Piano-like layout requires coord_to_scale_index and mos")
            return mapping

        # Store MOS data
        self._mos = mos
        self._coord_to_scale_index = coord_to_scale_index

        print("lenght of coord_to_scale_index:", len(coord_to_scale_index))

        # Determine controller dimensions
        if logical_coords:
            max_y = max(y for _, y in logical_coords)
            min_y = min(y for _, y in logical_coords)
            self._controller_rows = max_y - min_y + 1
        else:
            self._controller_rows = 0
            return mapping

        # Scale size (n) from MOS
        n = mos.n

        # Calculate number of complete piano strips
        num_complete_strips = self._controller_rows // self.strip_width
        remainder_rows = self._controller_rows % self.strip_width

        logger.debug(f"Piano layout: {self._controller_rows} rows, strip_width={self.strip_width}, "
                    f"{num_complete_strips} complete strips, {remainder_rows} remainder rows")

        accidental_sign = 1 if mos.L_vec.x == 1 else -1
        neutral_mode = 1 if mos.L_vec.x == 1 else mos.n0 - 2
        for logical_x, logical_y in logical_coords:
            # Determine which piano strip this row belongs to
            # Rows are numbered from bottom (y=0) to top
            # Remainder rows at the top are left unmapped

            # Adjust y relative to bottom of controller
            y_from_bottom = logical_y - min_y

            # Skip remainder rows at the top
            if y_from_bottom >= num_complete_strips * self.strip_width:
                continue

            # Which strip (0 = bottom strip)
            strip_number = y_from_bottom // self.strip_width

            # Position within strip (0 = bottom row of strip)
            y_within_strip = y_from_bottom % self.strip_width - self.scale_row_index
            x_within_strip = logical_x - self.root_x + strip_number * self.row_offset

            # go from strip coord to mos coordinate by using (scale_degree, accidental) as intermediate step
            scale_degree = x_within_strip
            accidental = accidental_sign * y_within_strip
            
            q = (neutral_mode - mos.a0 * scale_degree) // n

            scale_coord_x = accidental - q
            scale_coord_y = scale_degree - scale_coord_x

            #sd_from_mos = scale_coord_x + scale_coord_y
            #acc_from_mos = accidental_sign * (scale_coord_x * mos.b0 - scale_coord_y * mos.a0 + neutral_mode)//mos.n0
            #print(f"Pad ({logical_x}, {logical_y}): strip(x,y)=({x_within_strip},{y_within_strip}) -> (s,acc) = ({scale_degree},{accidental}) -> "
            #      f"q={q}, mos_coord=({scale_coord_x},{scale_coord_y}), acc_sign={accidental_sign}, neutral_mode={neutral_mode}",
            #      f" back calc: (s,acc)=({sd_from_mos},{acc_from_mos})")

            mos_coord = (scale_coord_x, scale_coord_y)
            self._pad_to_mos_coord[(logical_x, logical_y)] = mos_coord
            if mos_coord in self._coord_to_scale_index:
                scale_index = self._coord_to_scale_index[mos_coord]
                mapping[(logical_x, logical_y)] = scale_index
            else:
                mapping[(logical_x, logical_y)] = None  # unmapped

        logger.info(f"Piano-like layout calculated: {len(mapping)} mapped pads, "
                   f"strip_width={self.strip_width}, scale_row={self.scale_row_index}, "
                   f"row_offset={self.row_offset}")
        
        return mapping

    def get_unmapped_coords(
        self,
        logical_coords: List[Tuple[int, int]]
    ) -> List[Tuple[int, int]]:
        """Get unmapped coordinates (remainder rows and accidentals outside scale)."""
        # TODO: Calculate which coords are in remainder rows or have invalid accidentals
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
        return self._pad_to_mos_coord.get((logical_x, logical_y))
