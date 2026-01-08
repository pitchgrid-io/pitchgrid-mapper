"""Main entry point for pg-isomap application."""

import logging
import sys

import uvicorn

from .app import PGIsomapApp
from .config import settings
from .web_api import WebAPI

# Setup logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)

logger = logging.getLogger(__name__)


def main():
    """Main entry point."""
    logger.info(f"Starting {settings.app_name} v{settings.version}")

    # Create application
    app = PGIsomapApp()

    # Start application
    if not app.start():
        logger.error("Failed to start application")
        sys.exit(1)

    # Create web API
    web_api = WebAPI(app)

    try:
        # Run web server
        logger.info(f"Starting web server on {settings.web_host}:{settings.web_port}")
        uvicorn.run(
            web_api.fastapi,
            host=settings.web_host,
            port=settings.web_port,
            log_level="info" if not settings.debug else "debug"
        )
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        # Cleanup
        app.stop()
        logger.info("Application stopped")


if __name__ == "__main__":
    main()
