"""Piano-like layout calculator (unfolded/mosaic)."""

import logging
from typing import Dict, List, Tuple

from .base import LayoutCalculator, LayoutConfig

logger = logging.getLogger(__name__)


class PianoLikeLayout(LayoutCalculator):
    """
    Piano-like layout calculator.

    Creates a mosaic/unfolded piano layout where:
    - Scale degrees are arranged in a strip
    - Accidentals are placed in a different direction
    - Can be unfolded (preserving accidental direction) or folded (strip_width=2)

    TODO: Implement based on ../algos/mossy_keyboard_ui.py
    """

    def calculate_mapping(
        self,
        logical_coords: List[Tuple[int, int]],
        scale_degrees: List[int],
        scale_size: int
    ) -> Dict[Tuple[int, int], int]:
        """
        Calculate piano-like mapping.

        TODO: Implement mosaic keyboard algorithm
        - Arrange scale degrees in strips
        - Handle accidentals based on direction
        - Support both unfolded and folded variants
        """
        mapping = {}

        if not scale_degrees:
            return mapping

        # Placeholder implementation
        root_note = scale_degrees[0] if scale_degrees else 60

        for logical_x, logical_y in logical_coords:
            # Very simple placeholder: just linear
            offset = (logical_x - self.config.root_x) + (logical_y - self.config.root_y) * 12
            note = root_note + offset

            if 0 <= note <= 127:
                mapping[(logical_x, logical_y)] = note

        logger.info(f"Piano-like layout calculated: {len(mapping)} mapped pads")
        return mapping

    def get_unmapped_coords(
        self,
        logical_coords: List[Tuple[int, int]]
    ) -> List[Tuple[int, int]]:
        """Get unmapped coordinates."""
        return []
