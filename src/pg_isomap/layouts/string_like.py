"""String-like layout calculator (like guitar/bass)."""

import logging
from typing import Dict, List, Tuple

from .base import LayoutCalculator, LayoutConfig

logger = logging.getLogger(__name__)


class StringLikeLayout(LayoutCalculator):
    """
    String-like layout calculator.

    Mimics stringed instruments where:
    - Rows are like strings, each tuned to a specific note
    - Moving along a row increases pitch chromatically or diatonically
    - Different rows start at different pitches
    """

    def calculate_mapping(
        self,
        logical_coords: List[Tuple[int, int]],
        scale_degrees: List[int],
        scale_size: int
    ) -> Dict[Tuple[int, int], int]:
        """
        Calculate string-like mapping.

        TODO: Implement based on pg_linn_companion logic
        - Each row represents a "string"
        - Notes increase along the row
        - Row offset determines tuning between strings
        """
        mapping = {}

        if not scale_degrees:
            return mapping

        # Placeholder implementation
        root_note = scale_degrees[0] if scale_degrees else 60

        for logical_x, logical_y in logical_coords:
            # Simple string-like: each row is tuned in fourths (5 semitones)
            string_offset = (logical_y - self.config.root_y) * 5
            fret_offset = (logical_x - self.config.root_x)

            note = root_note + string_offset + fret_offset

            if 0 <= note <= 127:
                mapping[(logical_x, logical_y)] = note

        logger.info(f"String-like layout calculated: {len(mapping)} mapped pads")
        return mapping

    def get_unmapped_coords(
        self,
        logical_coords: List[Tuple[int, int]]
    ) -> List[Tuple[int, int]]:
        """Get unmapped coordinates."""
        return []
