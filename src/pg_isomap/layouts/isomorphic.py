"""Isomorphic layout calculator."""

import logging
from typing import Dict, List, Tuple

from .base import LayoutCalculator, LayoutConfig

logger = logging.getLogger(__name__)


class IsomorphicLayout(LayoutCalculator):
    """
    Fully isomorphic layout calculator.

    In an isomorphic layout:
    - Moving in one direction always changes pitch by the same interval
    - The pattern repeats uniformly across the surface
    - All scale patterns look identical regardless of position
    """

    def calculate_mapping(
        self,
        logical_coords: List[Tuple[int, int]],
        scale_degrees: List[int],
        scale_size: int
    ) -> Dict[Tuple[int, int], int]:
        """
        Calculate isomorphic mapping.

        The basic isomorphic mapping assigns notes based on:
        - X-axis: typically moves by one scale step
        - Y-axis: typically moves by a generator interval (e.g., perfect fifth)

        TODO: This is a placeholder. The actual implementation will need to:
        1. Apply transformations (skew, rotate, flip) from config
        2. Use scalatrix library to determine proper intervals
        3. Handle different controller geometries (quad vs hex)
        """
        mapping = {}

        if not scale_degrees:
            return mapping

        # Simple placeholder: linear mapping
        # In real implementation, this should use scalatrix scale structure
        root_note = scale_degrees[0] if scale_degrees else 60

        for logical_x, logical_y in logical_coords:
            # Apply root offset
            dx = logical_x - self.config.root_x
            dy = logical_y - self.config.root_y

            # Apply movement
            dx -= self.config.move_x
            dy -= self.config.move_y

            # Simple isomorphic formula (placeholder)
            # X-axis: move by 1 scale step
            # Y-axis: move by 7 semitones (perfect fifth)
            scale_index = dx % scale_size
            octave_offset = (dx // scale_size) * 12
            fifth_offset = dy * 7

            note = root_note + octave_offset + fifth_offset

            # Get the actual scale degree
            if 0 <= scale_index < len(scale_degrees):
                base_note = scale_degrees[scale_index]
                note = base_note + (dx // scale_size) * 12 + dy * 7

            # Clamp to MIDI range
            if 0 <= note <= 127:
                mapping[(logical_x, logical_y)] = note

        logger.info(f"Isomorphic layout calculated: {len(mapping)} mapped pads")
        return mapping

    def get_unmapped_coords(
        self,
        logical_coords: List[Tuple[int, int]]
    ) -> List[Tuple[int, int]]:
        """Get unmapped coordinates (those outside MIDI range)."""
        # For now, return empty list
        # Real implementation would check which coords produce notes outside 0-127
        return []
