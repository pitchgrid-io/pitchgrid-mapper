"""Python-side wrapper around the native `pg_wooting_bridge` PyO3 module."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from ..config import settings
from ..controller_config import ControllerConfig

if TYPE_CHECKING:
    from ..midi_handler import MIDIHandler

logger = logging.getLogger(__name__)


class WootingNotAvailable(RuntimeError):
    """Raised when the native bridge module or required dylibs are missing."""


def _import_native() -> Any:
    try:
        import pg_wooting_bridge  # type: ignore
    except ImportError as e:
        raise WootingNotAvailable(
            "pg_wooting_bridge native module not built; run "
            "`maturin develop --release -m wooting_bridge/Cargo.toml`"
        ) from e
    return pg_wooting_bridge


class WootingBridge:
    """Lifecycle owner for one active Wooting controller.

    `start()` spins up the Rust polling thread and a small Python drain thread
    that pulls outgoing MIDI messages from the bridge's lock-free queue and
    feeds them to `MIDIHandler.inject_message`. `stop()` tears both down.
    """

    def __init__(self, midi_handler: "MIDIHandler", controller_config: ControllerConfig):
        if not controller_config.is_wooting():
            raise WootingNotAvailable(
                f"Controller {controller_config.device_name} has no wootingKeycodeMap"
            )
        self._native = _import_native()
        self._midi = midi_handler
        self._cc = controller_config
        self._native_bridge: Any = None
        self._drain_thread: Optional[threading.Thread] = None
        self._drain_running = False

        analog_path = self._resolve_dylib(settings.wooting_analog_dylib_path)
        rgb_path = self._resolve_dylib(settings.wooting_rgb_dylib_path)
        if analog_path is None:
            raise WootingNotAvailable(
                f"Wooting analog dylib not found at {settings.wooting_analog_dylib_path}"
            )

        keycode_map: Dict[int, Tuple[int, int, int]] = controller_config.build_wooting_keymap_for_bridge()
        if not keycode_map:
            raise WootingNotAvailable(
                f"Empty Wooting keymap for {controller_config.device_name} — check noteAssign and wootingKeycodeMap"
            )

        rgb_address_map: Dict[Tuple[int, int], Tuple[int, int]] = (
            controller_config.wooting_rgb_address_map
        )

        cfg: Dict[str, Any] = {
            "min_poll_interval_us": settings.wooting_min_poll_interval_us,
            "velocity_arm_threshold": settings.wooting_velocity_arm_threshold,
            "velocity_trigger_threshold": settings.wooting_velocity_trigger_threshold,
            "velocity_release_threshold": settings.wooting_velocity_release_threshold,
            "velocity_off_threshold": settings.wooting_velocity_off_threshold,
            "velocity_min_dt_ms": settings.wooting_velocity_min_dt_ms,
            "velocity_max_dt_ms": settings.wooting_velocity_max_dt_ms,
            "default_profile": settings.wooting_default_profile,
            "aftertouch_enabled": settings.wooting_aftertouch_enabled,
            "aftertouch_smooth_alpha": settings.wooting_aftertouch_smooth_alpha,
            "aftertouch_min_interval_ms": settings.wooting_aftertouch_min_interval_ms,
            "rgb_refresh_hz": settings.wooting_rgb_refresh_hz,
        }

        product_ids: List[int] = []
        if controller_config.wooting_product_id is not None:
            product_ids.append(int(controller_config.wooting_product_id))

        self._native_bridge = self._native.Bridge(
            str(analog_path),
            str(rgb_path) if rgb_path else None,
            keycode_map,
            rgb_address_map,
            product_ids,
            cfg,
        )

    @staticmethod
    def _resolve_dylib(path: Optional[Path]) -> Optional[Path]:
        if path is None:
            return None
        if path.exists():
            return path
        return None

    def available_profiles(self) -> List[Dict[str, str]]:
        return list(self._native.available_profiles())

    def start(self) -> None:
        if self._drain_running:
            return
        self._native_bridge.start()
        self._drain_running = True
        self._drain_thread = threading.Thread(
            target=self._drain_loop, name="Wooting-MIDI-Drain", daemon=True
        )
        self._drain_thread.start()
        logger.info(
            "Wooting bridge started for %s (profile=%s)",
            self._cc.device_name,
            settings.wooting_default_profile,
        )

    def stop(self) -> None:
        self._drain_running = False
        if self._drain_thread is not None:
            self._drain_thread.join(timeout=2.0)
            self._drain_thread = None
        if self._native_bridge is not None:
            self._native_bridge.stop()
        logger.info("Wooting bridge stopped")

    def set_profile(self, name: str) -> None:
        self._native_bridge.set_profile(name)

    def set_pad_colors(self, colors: List[Tuple[int, int, int, int, int]]) -> None:
        """`colors` is a list of (x, y, r, g, b) tuples — base layer only."""
        self._native_bridge.set_pad_colors(colors)

    def set_pad_overlay(self, x: int, y: int, r: int, g: int, b: int) -> None:
        self._native_bridge.set_pad_overlay(x, y, r, g, b)

    def clear_pad_overlay(self, x: int, y: int) -> None:
        self._native_bridge.clear_pad_overlay(x, y)

    def status(self) -> Dict[str, Any]:
        return dict(self._native_bridge.status())

    def active_profile(self) -> str:
        return str(self._native_bridge.active_profile())

    def set_per_note_sustain(self, enabled: bool) -> None:
        """Toggle the experimental per-note CC64 sustain.

        When enabled, profiles that opt in (currently PianoSim) emit
        CC64=127 on the note's member channel at strike. The matching
        CC64=0 on key release is suppressed while the master sustain
        pedal (spacebar) is held, so per-note state never fights the
        master pedal.
        """
        self._native_bridge.set_per_note_sustain(bool(enabled))

    def per_note_sustain_enabled(self) -> bool:
        return bool(self._native_bridge.per_note_sustain_enabled())

    def set_sensitivity(self, value: float) -> None:
        """Set the analog-input sensitivity multiplier.

        1.0 = neutral, >1.0 louder, <1.0 quieter. Applied to every profile
        as a final scaling on the computed NoteOn velocity (clamped 1..127).
        Held notes are unaffected — the slider's effect is on future
        NoteOns only.
        """
        self._native_bridge.set_sensitivity(float(value))

    def sensitivity(self) -> float:
        return float(self._native_bridge.sensitivity())

    def _drain_loop(self) -> None:
        interval = max(0.001, settings.wooting_drain_interval_ms / 1000.0)
        while self._drain_running:
            try:
                batch = self._native_bridge.drain_midi()
                for msg_bytes in batch:
                    self._midi.inject_message(list(msg_bytes))
            except Exception as exc:
                logger.error("Wooting drain error: %s", exc)
            time.sleep(interval)
