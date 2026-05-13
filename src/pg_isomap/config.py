"""Configuration management for PitchGrid Mapper."""

import os
import sys
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


def _get_base_path() -> Path:
    """Get the base path for resources, handling PyInstaller bundle."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # Running in PyInstaller bundle
        return Path(sys._MEIPASS)
    else:
        # Running in development
        return Path(__file__).parent.parent.parent


def _get_app_version() -> str:
    """Get app version.

    Priority:
      1. APP_VERSION env var (explicit override, rarely used)
      2. Frozen bundle: _version.txt baked in at build time
      3. Dev run: pyproject.toml — the single source of truth for this repo
      4. Fallback: "0.1.0"
    """
    version = os.getenv('APP_VERSION')
    if version:
        return version

    # Frozen bundle: use the version file written at build time
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        version_file = Path(sys._MEIPASS) / '_version.txt'
        if version_file.exists():
            try:
                return version_file.read_text().strip()
            except Exception:
                pass

    # Dev run: read directly from pyproject.toml so nothing needs to stay in sync
    try:
        try:
            import tomllib  # Python 3.11+
        except ImportError:  # pragma: no cover
            import tomli as tomllib  # type: ignore
        pyproject = _get_base_path() / "pyproject.toml"
        if pyproject.exists():
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            return data.get("project", {}).get("version", "0.1.0")
    except Exception:
        pass

    return "0.1.0"  # Fallback default


class Settings(BaseSettings):
    """Application settings."""

    # Application - these use unprefixed env vars to match build scripts
    app_name: str = Field(default="PitchGrid Mapper", validation_alias="APP_NAME")
    app_full_name: str = "PitchGrid Isomorphic Controller Mapper"
    app_version: str = Field(default_factory=_get_app_version, validation_alias="APP_VERSION")
    debug: bool = False

    # MIDI
    virtual_midi_device_name: str = "PitchGrid Mapper"
    midi_buffer_size: int = 1024

    # OSC (bidirectional communication with PitchGrid plugin)
    osc_host: str = "127.0.0.1"
    osc_server_port: int = 34561  # Port we listen on (receive from plugin)
    osc_client_port: int = 34562  # Port we send to (plugin listens here)

    # Controller Discovery
    discovery_interval_seconds: float = 3.0

    # Web Server
    web_host: str = "127.0.0.1"
    web_port: int = 0  # 0 = ephemeral port (assigned by OS)

    # Paths - computed at runtime based on bundle vs development
    controller_config_dir: Path = _get_base_path() / "controller_config"
    frontend_dist_dir: Optional[Path] = _get_base_path() / "frontend" / "dist"

    # Wooting analog bridge
    # The Analog SDK is statically linked into the pg_wooting_bridge wheel —
    # we only need to tell it where to find the *plugin* directory (the
    # HID-side adapter, e.g. AnalogSense's abiv1.dylib). The RGB SDK is
    # still loaded at runtime from its dylib.
    wooting_plugin_dir: Path = Path("/usr/local/share/WootingAnalogPlugins")
    wooting_rgb_dylib_path: Optional[Path] = Path("/usr/local/lib/libwooting-rgb-sdk.dylib")
    wooting_min_poll_interval_us: int = 125  # 8 kHz upper bound; firmware decides actual rate
    wooting_velocity_arm_threshold: float = 0.15
    wooting_velocity_trigger_threshold: float = 0.50
    wooting_velocity_release_threshold: float = 0.30
    wooting_velocity_off_threshold: float = 0.10
    wooting_velocity_min_dt_ms: float = 2.0
    wooting_velocity_max_dt_ms: float = 80.0
    wooting_default_profile: str = "mpe"
    wooting_aftertouch_enabled: bool = True
    wooting_aftertouch_smooth_alpha: float = 0.30
    wooting_aftertouch_min_interval_ms: float = 5.0
    wooting_rgb_refresh_hz: float = 30.0
    wooting_drain_interval_ms: float = 2.0  # Python-side drain cadence (~500 Hz)

    model_config = SettingsConfigDict(
        env_prefix="PGISOMAP_",
        env_file=".env",
        extra="ignore"  # Ignore extra env vars (like Azure signing config)
    )


settings = Settings()
