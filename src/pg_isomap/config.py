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

    # OSC (bidirectional communication with PitchGrid plugin)
    osc_host: str = "127.0.0.1"
    osc_server_port: int = 34561  # Port we listen on (receive from plugin)
    osc_client_port: int = 34562  # Port we send to (plugin listens here)

    # Controller Discovery
    discovery_interval_seconds: float = 3.0

    # Web Server
    web_host: str = "127.0.0.1"
    web_port: int = 0  # 0 = ephemeral port (assigned by OS)

    # Paths
    controller_config_dir: Path = Path(__file__).parent.parent.parent / "controller_config"
    frontend_dist_dir: Optional[Path] = Path(__file__).parent.parent.parent / "frontend" / "dist"

    class Config:
        env_prefix = "PGISOMAP_"
        env_file = ".env"


settings = Settings()
