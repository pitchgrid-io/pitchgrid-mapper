"""Main entry point for PitchGrid Mapper application."""

import logging
import sys

import uvicorn

from .app import PGIsomapApp
from .config import settings
from .web_api import WebAPI

# Setup logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)

logger = logging.getLogger(__name__)


def main():
    """Main entry point."""
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")

    # Create application
    app = PGIsomapApp()

    # Start application
    if not app.start():
        logger.error("Failed to start application")
        sys.exit(1)

    # Create web API
    web_api = WebAPI(app)

    try:
        # Run web server with ephemeral port support
        import socket

        # If port is 0, bind to get an ephemeral port first
        if settings.web_port == 0:
            # Create a socket to get an ephemeral port
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((settings.web_host, 0))
            actual_port = sock.getsockname()[1]
            sock.close()
        else:
            actual_port = settings.web_port

        logger.info(f"Web server starting on http://{settings.web_host}:{actual_port}")

        uvicorn.run(
            web_api.fastapi,
            host=settings.web_host,
            port=actual_port,
            log_level="debug" if settings.debug else "warning"
        )
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        # Cleanup
        app.stop()
        logger.info("Application stopped")


if __name__ == "__main__":
    main()
