"""Configuration management for pg-isomap."""

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings."""

    # Application
    app_name: str = "PitchGrid Isomap"
    version: str = "0.1.0"
    debug: bool = False

    # MIDI
    virtual_midi_device_name: str = "PG Isomap"
    midi_buffer_size: int = 1024

    # OSC
    osc_listen_host: str = "127.0.0.1"
    osc_listen_port: int = 9000
    pitchgrid_osc_host: str = "127.0.0.1"
    pitchgrid_osc_port: int = 8000

    # Controller Discovery
    discovery_interval_seconds: float = 3.0

    # Web Server
    web_host: str = "127.0.0.1"
    web_port: int = 8080

    # Paths
    controller_config_dir: Path = Path(__file__).parent.parent.parent / "controller_config"
    frontend_dist_dir: Optional[Path] = None

    class Config:
        env_prefix = "PGISOMAP_"
        env_file = ".env"


settings = Settings()
