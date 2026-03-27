"""Base classes for layout calculations."""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel


class LayoutType(str, Enum):
    """Types of keyboard layouts."""
    ISOMORPHIC = "isomorphic"
    STRING_LIKE = "string_like"
    PIANO_LIKE = "piano_like"
    PIANO_FOLDED = "piano_folded"
    EDO_ISO = "edo_iso"


class LayoutConfig(BaseModel):
    """Configuration for a keyboard layout."""

    layout_type: LayoutType

    # Root position in logical coordinates
    root_x: int = 0
    root_y: int = 0

    # Isomorphic layout parameters
    skew_x: int = 0  # Skew amount along x-axis
    skew_y: int = 0  # Skew amount along y-axis
    rotation: int = 0  # Rotation steps (controller-dependent)
    flip_axis: Optional[int] = None  # Flip axis (0, 1, 2 for hex; 0, 1 for quad)

    # String-like layout parameters
    string_orientation: int = 0  # 0, 1, or 2 depending on controller geometry
    row_offset: int = 0  # Offset between strings

    # Piano-like layout parameters
    strip_orientation: int = 0
    strip_width: int = 2
    accidental_direction: int = 0  # Direction for accidentals
    strips_offset: int = 0
    scale_line_position: int = 0  # Position of scale line within strips

    # Movement
    move_x: int = 0  # Move layout in x direction
    move_y: int = 0  # Move layout in y direction


class LayoutCalculator(ABC):
    """Abstract base class for layout calculators."""

    def __init__(self, config: LayoutConfig):
        self.config = config
        self.mos_to_logical: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}

    @abstractmethod
    def calculate_mapping(
        self,
        logical_coords: List[Tuple[int, int]],
        scale_degrees: List[int],
        scale_size: int,
        **kwargs
    ) -> Dict[Tuple[int, int], int]:
        """
        Calculate the mapping from logical coordinates to scale degrees.

        Args:
            logical_coords: List of available (logical_x, logical_y) coordinates
            scale_degrees: List of MIDI note numbers from PitchGrid
            scale_size: Number of notes in the scale
            **kwargs: Additional arguments (mos, coord_to_scale_index, etc.)

        Returns:
            Dictionary mapping (logical_x, logical_y) -> MIDI note number
        """
        pass

    @abstractmethod
    def get_unmapped_coords(
        self,
        logical_coords: List[Tuple[int, int]]
    ) -> List[Tuple[int, int]]:
        """
        Get list of coordinates that couldn't be mapped to notes.

        Args:
            logical_coords: List of available coordinates

        Returns:
            List of unmapped coordinates
        """
        pass

    def get_mos_coordinate(self, logical_x: int, logical_y: int) -> Optional[Tuple[int, int]]:
        """Get the MOS lattice coordinate for a logical coordinate. Override in subclasses."""
        return None

    def build_mos_to_logical_mapping(self, logical_coords: List[Tuple[int, int]]) -> None:
        """Build reverse mapping from MOS coordinates to logical coordinates."""
        self.mos_to_logical = {}
        for lx, ly in logical_coords:
            mos = self.get_mos_coordinate(lx, ly)
            if mos is not None:
                self.mos_to_logical.setdefault(mos, []).append((lx, ly))
