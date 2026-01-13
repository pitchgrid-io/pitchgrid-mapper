"""
Desktop application entry point using pywebview.

This creates a native window with embedded webview for the UI.
"""

import logging
import sys
import threading

import uvicorn
import webview

from .app import PGIsomapApp
from .config import settings
from .web_api import WebAPI

logger = logging.getLogger(__name__)


class DesktopApp:
    """Desktop application with native window."""

    def __init__(self):
        self.pg_app: PGIsomapApp | None = None
        self.web_api: WebAPI | None = None
        self.server_thread: threading.Thread | None = None
        self.window: webview.Window | None = None
        self.actual_port: int | None = None

    def start_backend(self):
        """Start the backend server in a separate thread."""
        logger.info("Starting backend server...")

        # Create application
        self.pg_app = PGIsomapApp()

        # Start application
        if not self.pg_app.start():
            logger.error("Failed to start application")
            sys.exit(1)

        # Create web API
        self.web_api = WebAPI(self.pg_app)

        # If port is 0, bind to get an ephemeral port first
        import socket

        if settings.web_port == 0:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((settings.web_host, 0))
            self.actual_port = sock.getsockname()[1]
            sock.close()
        else:
            self.actual_port = settings.web_port

        # Run server in thread
        def run_server():
            uvicorn.run(
                self.web_api.fastapi,
                host=settings.web_host,
                port=self.actual_port,
                log_level="debug" if settings.debug else "warning",
            )

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()

        # Give server time to start
        import time
        time.sleep(0.5)

        logger.info(f"Backend server running on http://{settings.web_host}:{self.actual_port}")

    def create_window(self):
        """Create the native window with webview."""
        logger.info("Creating application window...")

        # URL to load (local server with ephemeral port)
        url = f"http://{settings.web_host}:{self.actual_port}"

        # Create window
        self.window = webview.create_window(
            title=f"{settings.app_name} v{settings.version}",
            url=url,
            width=1280,
            height=800,
            resizable=True,
            min_size=(800, 600),
        )

        logger.info("Application window created")

    def run(self):
        """Run the desktop application."""
        try:
            # Start backend server
            self.start_backend()

            # Create and show window
            self.create_window()

            # Start webview (blocking call)
            logger.info("Starting webview...")
            webview.start(debug=settings.debug)

            logger.info("Application window closed")

        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        finally:
            # Cleanup
            if self.pg_app:
                self.pg_app.stop()
            logger.info("Application stopped")


def main():
    """Main entry point for desktop app."""
    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.WARNING,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    logger.info(f"Starting {settings.app_name} v{settings.version}")

    # Create and run desktop app
    app = DesktopApp()
    app.run()


if __name__ == "__main__":
    main()
