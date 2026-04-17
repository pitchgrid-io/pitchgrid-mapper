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
        self.mode: int = 0
        self.root_freq: float = 440.0
        self.stretch: float = 1.0
        self.skew: float = 0.583333
        self.mode_offset: int = 0
        self.steps: int = 12
        self.mos_a: int = 5
        self.mos_b: int = 2

        # MOS object and scale info
        self.mos: Optional[sx.MOS] = None
        self.scale_degrees: list[int] = []
        self.L: int = 12  # Large steps
        self.s: int = 0   # Small steps

        # Scale object and coordinate mapping
        self.scale: Optional[sx.Scale] = None
        self.coord_to_scale_index: dict[tuple[int, int], int] = {}

        # EDO compatibility and enharmonic vector
        self.is_edo_compatible: bool = False
        self.edo_mos: Optional[sx.MOS] = None
        self.enharmonic_vector: Optional[sx.Vector2i] = None

        # Live tuning state (from /pitchgrid/plugin/tuning). Used for
        # harmony-based coloring — diverges from mapping state when the
        # plugin's mapping is locked. Falls back to mapping state when no
        # live tuning has been received yet.
        self.live_mos: Optional[sx.MOS] = None
        self.live_root_freq: float = 0.0
        self.live_mode: int = 0
        self.live_stretch: float = 1.0
        self.live_skew: float = 0.583333
        self.live_mos_a: int = 5
        self.live_mos_b: int = 2
        self.live_steps: int = 12
        self.live_mode_offset: int = 0

        # Initialize with default MOS (12-EDO chromatic)
        self._calculate_mos()

    def update_tuning(
        self,
        mode: int,
        root_freq: float,
        stretch: float,
        skew: float,
        mode_offset: int,
        steps: int,
        mos_a: int,
        mos_b: int
    ):
        """
        Update tuning parameters from OSC message.

        Args:
            mode: Mode index
            root_freq: Root frequency in Hz
            stretch: Stretch factor
            skew: Skew factor
            mode_offset: Mode offset
            steps: Number of steps per period
            mos_a: MOS a parameter (number of intervals of type A)
            mos_b: MOS b parameter (number of intervals of type B)
        """
        self.mode = int(mode)
        self.root_freq = float(root_freq)
        self.stretch = float(stretch)
        self.skew = float(skew)
        self.mode_offset = int(mode_offset)
        self.steps = max(1, int(steps))
        self.mos_a = max(1, int(mos_a))
        self.mos_b = max(1, int(mos_b))

        logger.info(
            f"Tuning updated: mos=({self.mos_a},{self.mos_b}), mode={self.mode}, "
            f"root_freq={self.root_freq}, stretch={self.stretch}, "
            f"skew={self.skew}, mode_offset={self.mode_offset}, steps={self.steps}"
        )

        # Recalculate MOS and scale degrees
        self._calculate_mos()

    def update_live_tuning(
        self,
        mode: int,
        root_freq: float,
        stretch: float,
        skew: float,
        mode_offset: int,
        steps: int,
        mos_a: int,
        mos_b: int,
    ) -> bool:
        """Update the live-tuning MOS (used for harmony coloring).

        Returns True if the root frequency changed (caller may want to refresh
        consonance curve).
        """
        root_freq = float(root_freq)
        old_root = self.live_root_freq

        self.live_mode = int(mode)
        self.live_root_freq = root_freq
        self.live_stretch = float(stretch)
        self.live_skew = float(skew)
        self.live_mode_offset = int(mode_offset)
        self.live_steps = max(1, int(steps))
        self.live_mos_a = max(1, int(mos_a))
        self.live_mos_b = max(1, int(mos_b))

        try:
            self.live_mos = sx.MOS.fromParams(
                self.live_mos_a,
                self.live_mos_b,
                self.live_mode,
                self.live_stretch,
                self.live_skew,
                1,
            )
        except Exception as e:
            logger.error(f"Error building live-tuning MOS: {e}")
            self.live_mos = None

        return old_root != root_freq

    def _calculate_mos(self):
        """Calculate MOS from current tuning parameters."""
        try:
            # Create MOS using fromParams (a, b, mode, equave, generator, repetitions)
            self.mos = sx.MOS.fromParams(
                self.mos_a,
                self.mos_b,
                self.mode,
                self.stretch,
                self.skew,
                1  # repetitions
            )

            # For now, use chromatic mapping (all notes in the EDO)
            # This maps all pads to the full EDO rather than filtering to a subset
            self.scale_degrees = list(range(self.steps))

            # Get L and s count directly from MOS
            # nL = number of large steps, nS = number of small steps
            self.L = self.mos.nL
            self.s = self.mos.nS

            # Generate mapped scale using the canonical method in scalatrix
            self.scale = self.mos.generateMappedScale(
                self.steps, self.mode_offset, self.root_freq, 128, 60
            )

            # Build dictionary from natural coordinate to scale index
            self.coord_to_scale_index = {}
            scale_nodes = self.scale.getNodes()
            for index, node in enumerate(scale_nodes):
                coord = (node.natural_coord.x, node.natural_coord.y)
                self.coord_to_scale_index[coord] = index

            logger.info(
                f"MOS calculated: ({self.mos_a},{self.mos_b}), mode={self.mode}, "
                f"scale_system={self.L}L {self.s}s, {len(self.scale_degrees)} scale degrees, "
                f"{len(self.coord_to_scale_index)} mapped coordinates"
            )

            # Calculate EDO compatibility and enharmonic vector
            self._calculate_edo_compatibility()

        except Exception as e:
            logger.error(f"Error calculating MOS: {e}")
            # Fallback to chromatic scale
            self.mos = None
            self.scale = None
            self.coord_to_scale_index = {}
            self.scale_degrees = list(range(self.steps))
            self.L, self.s = self.steps, 0
            self.is_edo_compatible = False
            self.edo_mos = None
            self.enharmonic_vector = None

    def _calculate_edo_compatibility(self):
        """
        Check if current tuning is EDO-compatible and calculate enharmonic vector.

        EDO-compatible means there exists a deeper MOS whose note count equals
        the EDO step count (self.steps).
        """
        if self.mos is None:
            self.is_edo_compatible = False
            self.edo_mos = None
            self.enharmonic_vector = None
            return

        # Start from current depth (derived from MOS) and search for matching EDO
        current_depth = self.mos.depth
        max_search_depth = current_depth + 20  # Reasonable limit

        for search_depth in range(current_depth, max_search_depth + 1):
            try:
                edo_mos = sx.MOS.fromG(
                    search_depth,
                    self.mode,
                    self.skew,
                    self.stretch,
                    1  # repetitions
                )

                if edo_mos.n == self.steps:
                    # Found EDO-compatible MOS
                    self.is_edo_compatible = True
                    self.edo_mos = edo_mos

                    # Calculate enharmonic vector:
                    # edo_gen_steps = edo_mos.v_gen.x + edo_mos.v_gen.y
                    # enharmonic_vector = mos.v_gen * edo_mos.n - Vector2i(mos.a, mos.b) * edo_gen_steps
                    edo_gen_steps = edo_mos.v_gen.x + edo_mos.v_gen.y

                    # Vector arithmetic: mos.v_gen * edo_mos.n
                    gen_scaled_x = self.mos.v_gen.x * edo_mos.n
                    gen_scaled_y = self.mos.v_gen.y * edo_mos.n

                    # Vector arithmetic: Vector2i(mos.a, mos.b) * edo_gen_steps
                    period_scaled_x = self.mos.a * edo_gen_steps
                    period_scaled_y = self.mos.b * edo_gen_steps

                    # enharmonic_vector = gen_scaled - period_scaled
                    self.enharmonic_vector = sx.Vector2i(
                        gen_scaled_x - period_scaled_x,
                        gen_scaled_y - period_scaled_y
                    )

                    logger.info(
                        f"EDO-compatible: depth {search_depth} gives {edo_mos.n} steps, "
                        f"enharmonic_vector=({self.enharmonic_vector.x}, {self.enharmonic_vector.y})"
                    )
                    return

                if edo_mos.n > self.steps:
                    # Overshot - not EDO-compatible
                    break

            except Exception as e:
                logger.debug(f"Error checking depth {search_depth}: {e}")
                continue

        # Not EDO-compatible
        self.is_edo_compatible = False
        self.edo_mos = None
        self.enharmonic_vector = None
        logger.info(f"Not EDO-compatible: no depth gives exactly {self.steps} steps")

    def get_scale_system_string(self) -> str:
        """Get formatted scale system string (e.g., '5L 2s')."""
        if self.mos and hasattr(self, 'L') and hasattr(self, 's'):
            return f"{self.L}L {self.s}s"
        return f"{self.steps} EDO"

    def get_tuning_info(self) -> dict:
        """Get current tuning information as dict."""
        return {
            'mos_a': self.mos_a,
            'mos_b': self.mos_b,
            'mode': self.mode,
            'root_freq': self.root_freq,
            'stretch': self.stretch,
            'skew': self.skew,
            'mode_offset': self.mode_offset,
            'steps': self.steps,
            'scale_system': self.get_scale_system_string(),
            'scale_degree_count': len(self.scale_degrees),
            'is_edo_compatible': self.is_edo_compatible,
            'enharmonic_vector': (
                (self.enharmonic_vector.x, self.enharmonic_vector.y)
                if self.enharmonic_vector else None
            ),
        }
