"""
Tuning processing and MOS (Moment of Symmetry) handling.

Processes tuning data from PitchGrid plugin and calculates scale degrees.
"""

import logging
from typing import Optional

import scalatrix as sx

logger = logging.getLogger(__name__)


class TuningHandler:
    """Handles tuning data from PitchGrid plugin and maintains MOS state."""

    def __init__(self):
        # Tuning parameters from plugin
        self.depth: int = 1
        self.mode: int = 0
        self.root_freq: float = 440.0
        self.stretch: float = 1.0
        self.skew: float = 0.0
        self.mode_offset: int = 0
        self.steps: int = 12

        # MOS object and scale info
        self.mos: Optional[sx.MOS] = None
        self.scale_degrees: list[int] = []
        self.L: int = 12  # Large steps
        self.s: int = 0   # Small steps

        # Scale object and coordinate mapping
        self.scale: Optional[sx.Scale] = None
        self.coord_to_scale_index: dict[tuple[int, int], int] = {}

        # Initialize with default MOS (12-EDO chromatic)
        self._calculate_mos()

    def update_tuning(
        self,
        depth: int,
        mode: int,
        root_freq: float,
        stretch: float,
        skew: float,
        mode_offset: int,
        steps: int
    ):
        """
        Update tuning parameters from OSC message.

        Args:
            depth: MOS depth (generation)
            mode: Mode index
            root_freq: Root frequency in Hz
            stretch: Stretch factor
            skew: Skew factor
            mode_offset: Mode offset
            steps: Number of steps per period
        """
        self.depth = max(1, int(depth))
        self.mode = int(mode)
        self.root_freq = float(root_freq)
        self.stretch = float(stretch)
        self.skew = float(skew)
        self.mode_offset = int(mode_offset)
        self.steps = max(1, int(steps))

        logger.info(
            f"Tuning updated: depth={self.depth}, mode={self.mode}, "
            f"root_freq={self.root_freq}, stretch={self.stretch}, "
            f"skew={self.skew}, mode_offset={self.mode_offset}, steps={self.steps}"
        )

        # Recalculate MOS and scale degrees
        self._calculate_mos()

    def _calculate_mos(self):
        """Calculate MOS from current tuning parameters."""
        try:
            # Create MOS using fromG (depth, mode, skew, stretch, repetitions)
            self.mos = sx.MOS.fromG(
                self.depth,
                self.mode,
                self.skew,
                self.stretch,
                1  # repetitions
            )

            # For now, use chromatic mapping (all notes in the EDO)
            # This maps all pads to the full EDO rather than filtering to a subset
            self.scale_degrees = list(range(self.steps))

            # Get L and s count directly from MOS
            # nL = number of large steps, nS = number of small steps
            self.L = self.mos.nL
            self.s = self.mos.nS

            onscreen_affine = sx.affineFromThreeDots(
                sx.Vector2d(0,0), 
                sx.Vector2d(self.mos.v_gen.x, self.mos.v_gen.y),
                sx.Vector2d(self.mos.a, self.mos.b), 
                sx.Vector2d(0, (self.mode_offset+.5)/self.steps),
                sx.Vector2d(self.skew * self.stretch, (self.mode_offset+1.5)/self.steps),
                sx.Vector2d(self.stretch, (self.mode_offset+.5)/self.steps)
            )

            # Calculate scale from MOS using implied affine transform
            self.scale = sx.Scale.fromAffine(
                onscreen_affine,
                self.root_freq,
                128,  # Max MIDI note
                60  # MIDI root note -- might become dynamic later
            )

            # Build dictionary from natural coordinate to scale index
            self.coord_to_scale_index = {}
            scale_nodes = self.scale.getNodes()
            for index, node in enumerate(scale_nodes):
                coord = (node.natural_coord.x, node.natural_coord.y)
                self.coord_to_scale_index[coord] = index

            logger.info(
                f"MOS calculated: depth={self.depth}, mode={self.mode}, "
                f"scale_system={self.L}L {self.s}s, {len(self.scale_degrees)} scale degrees, "
                f"{len(self.coord_to_scale_index)} mapped coordinates"
            )

            # Output full scale coordinate -> index mapping
            #logger.info("Scale coordinate to index mapping:")
            #for coord, index in sorted(self.coord_to_scale_index.items(), key=lambda x: x[1]):
            #    logger.info(f"  ({coord[0]:3d}, {coord[1]:3d}) -> index {index:3d}")

        except Exception as e:
            logger.error(f"Error calculating MOS: {e}")
            # Fallback to chromatic scale
            self.mos = None
            self.scale = None
            self.coord_to_scale_index = {}
            self.scale_degrees = list(range(self.steps))
            self.L, self.s = self.steps, 0

    def get_scale_system_string(self) -> str:
        """Get formatted scale system string (e.g., '5L 2s')."""
        if self.mos and hasattr(self, 'L') and hasattr(self, 's'):
            return f"{self.L}L {self.s}s"
        return f"{self.steps} EDO"

    def get_tuning_info(self) -> dict:
        """Get current tuning information as dict."""
        return {
            'depth': self.depth,
            'mode': self.mode,
            'root_freq': self.root_freq,
            'stretch': self.stretch,
            'skew': self.skew,
            'mode_offset': self.mode_offset,
            'steps': self.steps,
            'scale_system': self.get_scale_system_string(),
            'scale_degree_count': len(self.scale_degrees),
        }
