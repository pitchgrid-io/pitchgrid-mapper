"""Configuration management for PitchGrid Mapper."""

import sys
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


def _get_base_path() -> Path:
    """Get the base path for resources, handling PyInstaller bundle."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # Running in PyInstaller bundle
        return Path(sys._MEIPASS)
    else:
        # Running in development
        return Path(__file__).parent.parent.parent


class Settings(BaseSettings):
    """Application settings."""

    # Application
    app_name: str = "PitchGrid Mapper"
    app_full_name: str = "PitchGrid Isomorphic Controller Mapper"
    version: str = "0.1.0"
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

    class Config:
        env_prefix = "PGISOMAP_"
        env_file = ".env"
        extra = "ignore"  # Ignore extra env vars (like Azure signing config)


settings = Settings()
