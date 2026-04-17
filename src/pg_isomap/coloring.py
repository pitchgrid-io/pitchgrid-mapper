"""
Coloring schemes for pads based on scale role.

Provides different coloring strategies based on the pad's role in the MOS scale.
"""

import logging
import threading
from typing import Dict, List, Optional, Tuple

import scalatrix as sx

logger = logging.getLogger(__name__)


class ColoringScheme:
    """Base class for coloring schemes."""

    def get_color(
        self,
        mos_coord: Tuple[int, int],
        mos: Optional[sx.MOS],
        steps: int
    ) -> Optional[str]:
        """
        Get color for a pad based on its MOS coordinate and role.

        Args:
            mos_coord: The (x, y) natural coordinate in MOS space
            mos: The MOS object containing scale structure
            steps: Total EDO steps

        Returns:
            CSS color string (e.g., "hsl(240, 70%, 60%)") or None
        """
        raise NotImplementedError


class ScaleColoringScheme(ColoringScheme):
    """
    Scale-based coloring: root / onscale / offscale.

    Colors pads based on their role in the scale:
    - Root: Magenta/pink (special highlighting)
    - On-scale: Cyan/blue (notes in the current mode)
    - Off-scale: Gray (notes outside the mode)
    """

    def __init__(
        self,
        root_color: str = "hsl(300, 70%, 60%)",      # Magenta
        onscale_color: str = "hsl(180, 70%, 50%)",   # Cyan
        onsuperscale_color: str = "hsl(120, 70%, 50%)", # Green
        offscale_color: str = "hsl(0, 0%, 50%)",      # Gray
        onscale_color_unmapped: str = "hsl(270, 70%, 30%)",   # Cyan muted (darker)
        onsuperscale_color_unmapped: str = "hsl(120, 70%, 30%)", # Green muted (darker)
        offscale_color_unmapped: str = "hsl(0, 0%, 0%)"      # Gray muted (darker)
    ):
        """
        Initialize scale coloring scheme.

        Args:
            root_color: Color for root note
            onscale_color: Color for notes in the scale
            onsuperscale_color: Color for notes in the superscale
            offscale_color: Color for notes outside the scale
            onscale_color_unmapped: Color for unmapped notes in the scale
            onsuperscale_color_unmapped: Color for unmapped notes in the superscale
            offscale_color_unmapped: Color for unmapped notes outside the scale
        """
        self.root_color = root_color
        self.onscale_color = onscale_color
        self.onsuperscale_color = onsuperscale_color
        self.offscale_color = offscale_color
        self.onscale_color_unmapped = onscale_color_unmapped
        self.onsuperscale_color_unmapped = onsuperscale_color_unmapped
        self.offscale_color_unmapped = offscale_color_unmapped

    def get_color(
        self,
        mos_coord: Optional[Tuple[int, int]],
        mos: sx.MOS,
        coord_to_scale_index: Dict[Tuple[int, int], int],
        supermos: Optional[sx.MOS] = None,
        use_dark_offscale: bool = False
    ) -> Optional[str]:
        """
        Get color based on scale role and mapping.

        Args:
            mos_coord: The (x, y) natural coordinate in MOS space, or None if pad is outside layout
            mos: The MOS object containing scale structure
            coord_to_scale_index: Mapping from coordinates to scale indices
            supermos: Optional superscale MOS object
            use_dark_offscale: If True, use unmapped color for off-scale notes
                              (useful for string-like layouts where all pads are mapped)
        """
        # Pads with no MOS coordinate (e.g., outside piano strips) get no color
        if mos_coord is None:
            return None

        try:
            d = mos_coord[0] * mos.b - mos_coord[1] * mos.a + mos.mode
            is_root = d == mos.mode
            if is_root:
                return self.root_color

            is_in_scale = d >= 0 and d < mos.n0
            if is_in_scale:
                if mos_coord in coord_to_scale_index:
                    return self.onscale_color
                else:
                    return self.onscale_color_unmapped

            if supermos:
                d_super = mos_coord[0] * supermos.b - mos_coord[1] * supermos.a + supermos.mode
                is_in_supermos = d_super >= 0 and d_super < supermos.n0
                if is_in_supermos:
                    if mos_coord in coord_to_scale_index:
                        return self.onsuperscale_color
                    else:
                        return self.onsuperscale_color_unmapped

            # For off-scale notes, use dark color if requested (string-like layout)
            if use_dark_offscale:
                return self.offscale_color_unmapped

            if mos_coord in coord_to_scale_index:
                return self.offscale_color

            return self.offscale_color_unmapped

        except Exception as e:
            logger.error(f"Error determining scale role for {mos_coord}: {e}")
            return self.offscale_color


class RainbowColoringScheme(ColoringScheme):
    """
    Color pads by accidental count.

    Accidental-free pads are white; positive accidentals walk outward from a
    green/yellow midpoint toward blue/violet; negative accidentals walk toward
    orange/red. Mode is ignored — the coloring is purely a function of the MOS
    lattice coordinate.
    """

    def __init__(
        self,
        anchor_hue: float = 90.0,      # Midpoint between green (120) and yellow (60)
        step_deg: float = 40.0,        # Hue step per accidental
        max_abs_acc: int = 3,          # Clamp so positive/negative extremes stay apart
        saturation: float = 85.0,
        lightness: float = 55.0,
        white_color: str = "hsl(0, 0%, 95%)",
    ):
        self.anchor_hue = anchor_hue
        self.step_deg = step_deg
        self.max_abs_acc = max_abs_acc
        self.saturation = saturation
        self.lightness = lightness
        self.white_color = white_color

    def get_color(
        self,
        mos_coord: Optional[Tuple[int, int]],
        mos: Optional[sx.MOS],
    ) -> Optional[str]:
        if mos_coord is None or mos is None:
            return None

        try:
            acc = mos.nodeAccidental(sx.Vector2i(mos_coord[0], mos_coord[1]))
        except Exception as e:
            logger.error(f"Error getting accidental for {mos_coord}: {e}")
            return None

        if acc == 0:
            return self.white_color

        clamped = max(-self.max_abs_acc, min(self.max_abs_acc, acc))
        hue = (self.anchor_hue + clamped * self.step_deg) % 360
        return f"hsl({int(round(hue))}, {int(round(self.saturation))}%, {int(round(self.lightness))}%)"


class SpectrumConsonance:
    """
    Caches a consonance curve computed from a spectrum around the current root.

    The curve is recomputed whenever a new spectrum or root frequency arrives.
    Thread-safe lookup by cents-from-root.
    """

    def __init__(
        self,
        cents_min: float = -3600.0,
        cents_max: float = 3600.0,
        resolution: float = 1.0,
        log_baseline: float = 0.5,
    ):
        self.cents_min = cents_min
        self.cents_max = cents_max
        self.resolution = resolution
        self.log_baseline = log_baseline

        self._lock = threading.Lock()
        self._consonance: List[float] = []
        self._peak: float = 0.0
        self._ready: bool = False

    @property
    def is_ready(self) -> bool:
        return self._ready

    def update(self, partials: List[Tuple[float, float]], root_freq: float) -> bool:
        """
        Compute a fresh consonance curve from a spectrum and root frequency.

        Args:
            partials: list of (ratio, amplitude) pairs
            root_freq: reference frequency in Hz

        Returns True if the curve was successfully computed.
        """
        if not partials or root_freq <= 0:
            return False

        try:
            spectrum = sx.Spectrum()
            spectrum.partials = [sx.Partial(r, a) for r, a in partials]
            curve = sx.computeConsonanceCurve(
                spectrum,
                root_freq,
                self.cents_min,
                self.cents_max,
                self.resolution,
                self.log_baseline,
            )
            with self._lock:
                self._consonance = list(curve.consonance)
                self._peak = curve.peak
                self._ready = True
            return True
        except Exception as e:
            logger.error(f"Error computing consonance curve: {e}")
            return False

    def clear(self):
        with self._lock:
            self._consonance = []
            self._peak = 0.0
            self._ready = False

    def get_consonance(self, cents: float) -> float:
        """Look up consonance at a given cents offset from root. Returns 0 if out of range."""
        with self._lock:
            if not self._ready or not self._consonance:
                return 0.0
            idx = int(round((cents - self.cents_min) / self.resolution))
            if idx < 0 or idx >= len(self._consonance):
                return 0.0
            return self._consonance[idx]


class HarmonyColoringScheme(ColoringScheme):
    """
    Harmony-based coloring using the live synth spectrum.

    For each pad, compute its cents offset from the root and look up the
    consonance of that interval. Hue is derived from cents-mod-equave; lightness
    from consonance. Pads at scale degree 0 (root and equave multiples) are
    rendered white (zero saturation) with lightness still driven by consonance.
    """

    def __init__(
        self,
        saturation: float = 80.0,
        lightness_min: float = 10.0,
        lightness_max: float = 60.0,
        # Scale-degree-0 pads render at zero saturation; lightness can safely
        # go all the way to pure white at consonance=1 without washing a hue out.
        white_lightness_min: float = 20.0,
        white_lightness_max: float = 100.0,
        hue_offset_deg: float = 0.0,
    ):
        self.saturation = saturation
        self.lightness_min = lightness_min
        self.lightness_max = lightness_max
        self.white_lightness_min = white_lightness_min
        self.white_lightness_max = white_lightness_max
        self.hue_offset_deg = hue_offset_deg

    def get_color(
        self,
        tuning_coord: Optional[Tuple[int, int]],
        tuning_mos: Optional[sx.MOS],
        spectrum_consonance: SpectrumConsonance,
    ) -> Optional[str]:
        """
        Args:
            tuning_coord: pad coordinate expressed in the *tuning* MOS's lattice
                (not the mapping MOS's — those may differ when the plugin's
                mapping is locked).
            tuning_mos: the live-tuning MOS matching that coordinate system.
            spectrum_consonance: consonance curve built at the tuning root.
        """
        if tuning_coord is None or tuning_mos is None:
            return None
        if not spectrum_consonance.is_ready:
            return None

        try:
            # pitchHeight returns log2(fr from root); * 1200 = cents
            log2fr = tuning_mos.pitchHeight(float(tuning_coord[0]), float(tuning_coord[1]))
            cents = log2fr * 1200.0

            equave_cents = tuning_mos.equave * 1200.0
            if equave_cents <= 0:
                return None

            consonance = spectrum_consonance.get_consonance(cents)
            consonance = max(0.0, min(1.0, consonance))

            v = sx.Vector2i(tuning_coord[0], tuning_coord[1])

            # White override applies only to true root/equave multiples — i.e.
            # scale degree 0 AND accidental 0. Pads like the augmented unison
            # also have sd=0 but carry an accidental and should show their
            # actual harmony hue instead of being painted white.
            if tuning_mos.nodeScaleDegree(v) == 0 and tuning_mos.nodeAccidental(v) == 0:
                white_lightness = (
                    self.white_lightness_min
                    + consonance * (self.white_lightness_max - self.white_lightness_min)
                )
                return (
                    f"hsl(0, 0%, "
                    f"{int(round(max(0.0, min(100.0, white_lightness))))}%)"
                )

            lightness = self.lightness_min + consonance * (self.lightness_max - self.lightness_min)

            cents_mod = cents % equave_cents
            hue = ((cents_mod / equave_cents) * 360.0 + self.hue_offset_deg) % 360.0

            return (
                f"hsl({int(round(hue))}, "
                f"{int(round(max(0.0, min(100.0, self.saturation))))}%, "
                f"{int(round(max(0.0, min(100.0, lightness))))}%)"
            )
        except Exception as e:
            logger.error(f"Error computing harmony color for {tuning_coord}: {e}")
            return None


# Canonical scheme names used across the wire
SCHEME_SCALE = "scale"
SCHEME_RAINBOW = "rainbow"
SCHEME_HARMONY = "harmony"

AVAILABLE_SCHEMES = (SCHEME_SCALE, SCHEME_RAINBOW, SCHEME_HARMONY)

# Default scheme
DEFAULT_COLORING_SCHEME = ScaleColoringScheme()
DEFAULT_RAINBOW_SCHEME = RainbowColoringScheme()
DEFAULT_HARMONY_SCHEME = HarmonyColoringScheme()
