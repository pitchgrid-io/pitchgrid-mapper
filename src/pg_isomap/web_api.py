"""
Web API for controlling the application and serving the UI.

Provides REST endpoints and WebSocket for real-time updates.
"""

import logging
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .app import PGIsomapApp
from .config import settings
from .layouts import LayoutConfig, LayoutType

logger = logging.getLogger(__name__)


class ConnectControllerRequest(BaseModel):
    """Request to connect to a controller."""
    device_name: str


class LayoutConfigUpdate(BaseModel):
    """Layout configuration update."""
    layout_type: LayoutType
    root_x: int = 0
    root_y: int = 0
    skew_x: int = 0
    skew_y: int = 0
    rotation: int = 0
    move_x: int = 0
    move_y: int = 0


class WebAPI:
    """Web API server."""

    def __init__(self, app: PGIsomapApp):
        self.app = app
        self.fastapi = FastAPI(title="PG Isomap API", version=settings.version)

        # CORS middleware
        self.fastapi.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],  # In production, restrict this
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # WebSocket connections
        self.active_connections: list[WebSocket] = []

        # Register routes
        self._register_routes()

    def _register_routes(self):
        """Register API routes."""

        @self.fastapi.get("/api/status")
        async def get_status():
            """Get current application status."""
            return self.app.get_status()

        @self.fastapi.get("/api/controllers")
        async def get_controllers():
            """Get list of available controllers."""
            return {
                'controllers': self.app.controller_manager.get_all_device_names(),
                'connected': self.app.current_controller.device_name if self.app.current_controller else None
            }

        @self.fastapi.post("/api/controllers/connect")
        async def connect_controller(request: ConnectControllerRequest):
            """Connect to a controller."""
            success = self.app.connect_to_controller(request.device_name)
            return {'success': success}

        @self.fastapi.post("/api/controllers/disconnect")
        async def disconnect_controller():
            """Disconnect from current controller."""
            self.app.disconnect_controller()
            return {'success': True}

        @self.fastapi.get("/api/layout")
        async def get_layout():
            """Get current layout configuration."""
            return self.app.current_layout_config.dict()

        @self.fastapi.post("/api/layout")
        async def update_layout(config: LayoutConfigUpdate):
            """Update layout configuration."""
            layout_config = LayoutConfig(**config.dict())
            self.app.update_layout_config(layout_config)

            # Notify WebSocket clients
            await self._broadcast({
                'type': 'layout_update',
                'config': config.dict()
            })

            return {'success': True}

        @self.fastapi.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket endpoint for real-time updates."""
            await websocket.accept()
            self.active_connections.append(websocket)

            try:
                # Send initial state
                await websocket.send_json({
                    'type': 'init',
                    'status': self.app.get_status()
                })

                # Keep connection alive and receive messages
                while True:
                    data = await websocket.receive_text()
                    # Handle client messages if needed
                    logger.debug(f"Received WebSocket message: {data}")

            except WebSocketDisconnect:
                self.active_connections.remove(websocket)
                logger.info("WebSocket client disconnected")

        # Serve frontend static files if available
        if settings.frontend_dist_dir and settings.frontend_dist_dir.exists():
            self.fastapi.mount(
                "/assets",
                StaticFiles(directory=settings.frontend_dist_dir / "assets"),
                name="assets"
            )

            @self.fastapi.get("/")
            async def serve_frontend():
                """Serve frontend index.html."""
                return FileResponse(settings.frontend_dist_dir / "index.html")

    async def _broadcast(self, message: dict):
        """Broadcast message to all connected WebSocket clients."""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting to WebSocket: {e}")
                disconnected.append(connection)

        # Remove disconnected clients
        for connection in disconnected:
            self.active_connections.remove(connection)
