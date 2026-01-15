"""Isomorphic layout calculator with IntegerAffineTransform support."""

import logging
from typing import Dict, List, Optional, Tuple

import scalatrix as sx

from .base import LayoutCalculator, LayoutConfig

logger = logging.getLogger(__name__)


class IsomorphicLayout(LayoutCalculator):
    """
    Fully isomorphic layout calculator.

    In an isomorphic layout:
    - MOS natural coordinates are mapped to logical coordinates via an integer affine transform
    - The mapping transform can be manipulated via shift/skew/rotate/reflect operations
    - Moving in one direction always changes pitch by the same interval
    - The pattern repeats uniformly across the surface
    """

    def __init__(self, config: LayoutConfig, default_root: Optional[Tuple[int, int]] = None,
                 row_to_col_angle: float = 90.0):
        """
        Initialize isomorphic layout with an integer affine transform.

        Args:
            config: Layout configuration
            default_root: Default root coordinate from controller config (tx, ty for offset)
            row_to_col_angle: Angle between row and column axes (from controller config)
        """
        super().__init__(config)

        # Determine if controller is quad-like or hex-like based on RowToColAngle
        # Quad-like: 75° < angle < 105° (approximately rectangular grid)
        # Hex-like: otherwise (hexagonal or rhombic grid)
        self.quad_or_hex = 'rect' if 75 < row_to_col_angle < 105 else 'hex'
        logger.info(f"Isomorphic layout mode: {self.quad_or_hex} (RowToColAngle={row_to_col_angle}°)")

        # Initialize mapping transform
        # Matrix: ((1, 0), (0, 1)) = identity
        # Offset: default_root or (0, 0)
        root_x, root_y = default_root if default_root else (0, 0)

        self.mapping_transform = sx.IntegerAffineTransform(
            1, 0,   # First row: a, b
            0, 1,   # Second row: c, d
            root_x, root_y  # Offset: tx, ty
        )

    def set_transform(self, transform: sx.IntegerAffineTransform):
        """Set the mapping transform directly."""
        self.mapping_transform = transform

    def apply_transformation(self, transform_type: str) -> sx.IntegerAffineTransform:
        """
        Apply a transformation to the mapping transform.

        Args:
            transform_type: One of 'shift_left', 'shift_right', 'shift_up', 'shift_down',
                          'skew_left', 'skew_right', 'rotate_left', 'rotate_right',
                          'reflect_horizontal', 'reflect_vertical'

        Returns:
            The new mapping transform after applying the transformation
        """
        # Create transformation matrices/offsets
        if self.quad_or_hex == 'rect':
            if transform_type == 'shift_left':
                delta = sx.IntegerAffineTransform(1, 0, 0, 1, -1, 0)
            elif transform_type == 'shift_right':
                delta = sx.IntegerAffineTransform(1, 0, 0, 1, 1, 0)
            elif transform_type == 'shift_up':
                delta = sx.IntegerAffineTransform(1, 0, 0, 1, 0, 1)
            elif transform_type == 'shift_down':
                delta = sx.IntegerAffineTransform(1, 0, 0, 1, 0, -1)
            elif transform_type == 'skew_left':
                # Shear matrix: [[1, -1], [0, 1]]
                delta = sx.IntegerAffineTransform(1, -1, 0, 1, 0, 0)
            elif transform_type == 'skew_right':
                # Shear matrix: [[1, 1], [0, 1]]
                delta = sx.IntegerAffineTransform(1, 1, 0, 1, 0, 0)
            elif transform_type == 'rotate_left':
                # 90° counter-clockwise: [[0, -1], [1, 0]]
                delta = sx.IntegerAffineTransform(0, -1, 1, 0, 0, 0)
            elif transform_type == 'rotate_right':
                # 90° clockwise: [[0, 1], [-1, 0]]
                delta = sx.IntegerAffineTransform(0, 1, -1, 0, 0, 0)
            elif transform_type == 'reflect_horizontal':
                # Mirror on Y-axis: [[-1, 0], [0, 1]]
                delta = sx.IntegerAffineTransform(-1, 0, 0, 1, 0, 0)
            elif transform_type == 'reflect_vertical':
                # Mirror on X-axis: [[1, 0], [0, -1]]
                delta = sx.IntegerAffineTransform(1, 0, 0, -1, 0, 0)
            else:
                logger.warning(f"Unknown transformation type: {transform_type}")
                return self.mapping_transform
        else:  # hex
            if transform_type == 'shift_left':
                delta = sx.IntegerAffineTransform(1, 0, 0, 1, -1, 0)
            elif transform_type == 'shift_right':
                delta = sx.IntegerAffineTransform(1, 0, 0, 1, 1, 0)
            elif transform_type == 'shift_upright':
                delta = sx.IntegerAffineTransform(1, 0, 0, 1, 0, 1)
            elif transform_type == 'shift_downleft':
                delta = sx.IntegerAffineTransform(1, 0, 0, 1, 0, -1)
            elif transform_type == 'shift_upleft':
                delta = sx.IntegerAffineTransform(1, 0, 0, 1, -1, 1)
            elif transform_type == 'shift_downright':
                delta = sx.IntegerAffineTransform(1, 0, 0, 1, 1, -1)
            elif transform_type == 'skew_left':
                delta = sx.IntegerAffineTransform(1, -1, 0, 1, 0, 0)
            elif transform_type == 'skew_right':
                delta = sx.IntegerAffineTransform(1, 1, 0, 1, 0, 0)
            elif transform_type == 'skew_upright':
                delta = sx.IntegerAffineTransform(1, 0, -1, 1, 0, 0)
            elif transform_type == 'skew_downleft':
                delta = sx.IntegerAffineTransform(1, 0, 1, 1, 0, 0)
            elif transform_type == 'skew_upleft':
                delta = sx.IntegerAffineTransform(2, 1, -1, 0, 0, 0)
            elif transform_type == 'skew_downright':
                delta = sx.IntegerAffineTransform(0, -1, 1, 2, 0, 0)
            elif transform_type == 'rotate_left_hex':
                delta = sx.IntegerAffineTransform(0, -1, 1, 1, 0, 0)
            elif transform_type == 'rotate_right_hex':
                delta = sx.IntegerAffineTransform(1, 1, -1, 0, 0, 0)
            elif transform_type == 'reflect_x_hex':
                delta = sx.IntegerAffineTransform(1, 1, 0, -1, 0, 0)
            elif transform_type == 'reflect_y_hex':
                delta = sx.IntegerAffineTransform(-1, 0, 1, 1, 0, 0)
            elif transform_type == 'reflect_xy_hex':
                delta = sx.IntegerAffineTransform(0, -1, -1, 0, 0, 0)
            else:
                logger.warning(f"Unknown transformation type: {transform_type}")
                return self.mapping_transform            

        # Apply transformation by composing with delta
        M = self.mapping_transform
        M_trans = sx.IntegerAffineTransform(1, 0, 0, 1, M.tx, M.ty)
        M_mat = sx.IntegerAffineTransform(M.a, M.b, M.c, M.d, 0, 0)
        self.mapping_transform = M_trans.applyAffine(delta.applyAffine(M_mat))
        return self.mapping_transform

    def calculate_mapping(
        self,
        logical_coords: List[Tuple[int, int]],
        scale_degrees: List[int],
        scale_size: int,
        mos: Optional[sx.MOS] = None,
        coord_to_scale_index: Optional[Dict[Tuple[int, int], int]] = None
    ) -> Dict[Tuple[int, int], int]:
        """
        Calculate isomorphic mapping using IntegerAffineTransform.

        Args:
            logical_coords: List of available (logical_x, logical_y) coordinates
            scale_degrees: List of scale degrees (chromatic for now)
            scale_size: Total EDO steps
            mos: Optional MOS object for advanced mapping
            coord_to_scale_index: Optional mapping from MOS natural coordinate to scale index

        Returns:
            Dict mapping (logical_x, logical_y) -> MIDI note number
        """
        mapping = {}

        if not scale_degrees:
            return mapping

        # Get the inverse transform to map logical -> MOS coordinates
        try:
            inverse_transform = self.mapping_transform.inverse()
        except Exception as e:
            logger.error(f"Failed to invert mapping transform: {e}")
            return mapping

        # For each logical coordinate, calculate its MOS coordinate
        for logical_x, logical_y in logical_coords:
            try:
                # Apply inverse transform to get MOS natural coordinate
                logical_vec = sx.Vector2i(logical_x, logical_y)
                mos_coord = inverse_transform.apply(logical_vec)
                mos_coord_tuple = (mos_coord.x, mos_coord.y)

                # If we have a coord_to_scale_index mapping, use it
                if coord_to_scale_index is None:
                    continue 

                if mos_coord_tuple in coord_to_scale_index:
                    # This coordinate is in the scale - use its index as the MIDI note
                    note = coord_to_scale_index[mos_coord_tuple]

                    # Clamp to MIDI range
                    if 0 <= note <= 127:
                        mapping[(logical_x, logical_y)] = note

            except Exception as e:
                logger.debug(f"Failed to map coordinate ({logical_x}, {logical_y}): {e}")
                continue

        logger.info(f"Isomorphic layout calculated: {len(mapping)} mapped pads")
        return mapping

    def get_unmapped_coords(
        self,
        logical_coords: List[Tuple[int, int]]
    ) -> List[Tuple[int, int]]:
        """Get unmapped coordinates (those outside MIDI range)."""
        # TODO: Calculate which coords produce notes outside 0-127
        return []

    def get_mos_coordinate(self, logical_x: int, logical_y: int) -> Tuple[int, int]:
        """
        Get the MOS natural coordinate for a logical coordinate.

        Args:
            logical_x: Logical X coordinate
            logical_y: Logical Y coordinate

        Returns:
            Tuple of (mos_x, mos_y) natural coordinates
        """
        try:
            inverse_transform = self.mapping_transform.inverse()
            logical_vec = sx.Vector2i(logical_x, logical_y)
            mos_coord = inverse_transform.apply(logical_vec)
            return (mos_coord.x, mos_coord.y)
        except Exception as e:
            logger.error(f"Failed to get MOS coordinate: {e}")
            return (0, 0)
