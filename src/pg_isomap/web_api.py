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

        # Set reference back to app so it can broadcast updates
        self.app.web_api = self

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

        # Event loop reference (set when FastAPI starts)
        self.event_loop = None

        # Register routes
        self._register_routes()

        # Register startup event for periodic MIDI port refresh
        self._register_startup_tasks()

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

        @self.fastapi.post("/api/controllers/switch")
        async def switch_controller(request: ConnectControllerRequest):
            """
            Switch to a controller configuration without requiring MIDI connection.

            This loads the controller's pad layout and geometry, but doesn't
            establish a MIDI connection. Useful for Computer Keyboard and for
            visualizing controllers that aren't physically connected.
            """
            config = self.app.controller_manager.get_config(request.device_name)
            if not config:
                return {'success': False, 'error': f'Controller config not found: {request.device_name}'}

            # Disconnect from any existing MIDI controller
            self.app.midi_handler.disconnect_controller()

            # Load the configuration
            self.app.current_controller = config

            # Reset layout calculator to default when changing controllers
            self.app.current_layout_calculator = None

            # Recalculate layout with fresh calculator
            self.app._recalculate_layout()

            logger.info(f"Switched to controller configuration: {request.device_name}")
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

        @self.fastapi.post("/api/trigger_note")
        async def trigger_note(request: dict):
            """Trigger a MIDI note (for clicking pads or keyboard input)."""
            logical_x = request.get('x')
            logical_y = request.get('y')
            velocity = request.get('velocity', 100)
            note_on = request.get('note_on', True)  # True for note-on, False for note-off

            if logical_x is None or logical_y is None:
                return {'success': False, 'error': 'Missing x or y coordinate'}

            # Use app's trigger_note method for consistent logging
            success = self.app.trigger_note(
                logical_x,
                logical_y,
                velocity,
                note_on,
                source="ui"
            )

            if success:
                coord = (logical_x, logical_y)
                note = self.app.midi_handler.note_mapping.get(coord)
                return {'success': True, 'note': note}
            else:
                return {'success': False, 'error': 'Pad not mapped to a note'}

        @self.fastapi.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket endpoint for real-time updates."""
            import asyncio

            # Store event loop reference on first WebSocket connection
            if self.event_loop is None:
                self.event_loop = asyncio.get_event_loop()

            await websocket.accept()
            self.active_connections.append(websocket)
            logger.info(f"WebSocket client connected (total: {len(self.active_connections)})")

            try:
                # Send initial state
                await websocket.send_json({
                    'type': 'init',
                    'status': self.app.get_status()
                })

                # Keep connection alive and receive messages
                while True:
                    data = await websocket.receive_text()
                    logger.debug(f"Received WebSocket message: {data}")

                    # Parse and handle client messages
                    try:
                        import json
                        message = json.loads(data)
                        message_type = message.get('type')

                        if message_type == 'apply_transformation':
                            # Handle transformation request
                            transformation = message.get('transformation')
                            if transformation:
                                success = self.app.apply_transformation(transformation)
                                if success:
                                    # Broadcast updated status to all clients
                                    await self._broadcast({
                                        'type': 'status_update',
                                        'status': self.app.get_status()
                                    })
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON in WebSocket message: {data}")
                    except Exception as e:
                        logger.error(f"Error handling WebSocket message: {e}")

            except WebSocketDisconnect:
                if websocket in self.active_connections:
                    self.active_connections.remove(websocket)
                logger.info(f"WebSocket client disconnected (remaining: {len(self.active_connections)})")

        # Serve frontend static files if available
        if settings.frontend_dist_dir and settings.frontend_dist_dir.exists():
            @self.fastapi.get("/")
            async def serve_frontend():
                """Serve frontend index.html."""
                return FileResponse(settings.frontend_dist_dir / "index.html")

            # Mount static assets after defining routes
            self.fastapi.mount(
                "/assets",
                StaticFiles(directory=settings.frontend_dist_dir / "assets"),
                name="assets"
            )

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

    def broadcast_status_update(self):
        """
        Broadcast current status to all WebSocket clients.

        This is a synchronous wrapper that schedules the broadcast
        to be executed in the event loop from any thread.
        """
        import asyncio

        if not self.active_connections or self.event_loop is None:
            return

        message = {
            'type': 'status_update',
            'status': self.app.get_status()
        }

        # Schedule the broadcast in the event loop
        async def send_to_all():
            disconnected = []
            for connection in self.active_connections[:]:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    logger.error(f"Error broadcasting to WebSocket: {e}")
                    disconnected.append(connection)

            # Remove disconnected clients
            for connection in disconnected:
                if connection in self.active_connections:
                    self.active_connections.remove(connection)

        # Use run_coroutine_threadsafe to schedule from another thread
        try:
            asyncio.run_coroutine_threadsafe(send_to_all(), self.event_loop)
        except Exception as e:
            logger.error(f"Error scheduling WebSocket broadcast: {e}")

    def broadcast_note_event(self, logical_x: int, logical_y: int, note_on: bool):
        """
        Broadcast a note event to all WebSocket clients for UI highlighting.

        Args:
            logical_x: Logical X coordinate of the pad
            logical_y: Logical Y coordinate of the pad
            note_on: True for note-on, False for note-off
        """
        import asyncio

        if not self.active_connections or self.event_loop is None:
            return

        message = {
            'type': 'note_event',
            'x': logical_x,
            'y': logical_y,
            'note_on': note_on
        }

        # Schedule the broadcast in the event loop
        async def send_to_all():
            disconnected = []
            for connection in self.active_connections[:]:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    logger.error(f"Error broadcasting note event: {e}")
                    disconnected.append(connection)

            # Remove disconnected clients
            for connection in disconnected:
                if connection in self.active_connections:
                    self.active_connections.remove(connection)

        # Use run_coroutine_threadsafe to schedule from another thread
        try:
            asyncio.run_coroutine_threadsafe(send_to_all(), self.event_loop)
        except Exception as e:
            logger.error(f"Error scheduling note event broadcast: {e}")

    def _register_startup_tasks(self):
        """Register startup tasks including periodic MIDI port refresh."""
        import asyncio
        from .config import settings

        @self.fastapi.on_event("startup")
        async def start_midi_port_refresh():
            """Start periodic MIDI port refresh on the main event loop."""
            self.event_loop = asyncio.get_event_loop()

            # Do initial refresh
            self.app.refresh_midi_ports()
            logger.info("Initial MIDI port refresh completed")

            # Start periodic refresh task
            async def periodic_refresh():
                while True:
                    await asyncio.sleep(settings.discovery_interval_seconds)
                    self.app.refresh_midi_ports()

            asyncio.create_task(periodic_refresh())
            logger.info(f"Started periodic MIDI port refresh (every {settings.discovery_interval_seconds}s)")
