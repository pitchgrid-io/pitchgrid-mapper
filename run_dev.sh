#!/bin/bash
# Development startup script - Desktop app with hot reload

set -e

echo "=== PG Isomap Development Mode ==="
echo ""

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "Error: uv is not installed"
    echo "Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Check if Python 3.12 is available
if ! uv run python --version | grep -q "3.12"; then
    echo "Warning: Python 3.12 is required (found: $(uv run python --version))"
    echo "Make sure .python-version is set to 3.12"
fi

# Install/sync dependencies
echo "Syncing Python dependencies..."
uv sync

# Check if npm is installed
if ! command -v npm &> /dev/null; then
    echo "Error: npm is not installed"
    exit 1
fi

# Install frontend dependencies if needed
if [ ! -d "frontend/node_modules" ]; then
    echo ""
    echo "Installing frontend dependencies..."
    cd frontend
    npm install
    cd ..
fi

echo ""
echo "Starting Desktop App with Hot Reload..."
echo "Backend API: http://localhost:8080"
echo "Frontend Dev Server: http://localhost:5173 (with hot reload)"
echo "Virtual MIDI Device: PG Isomap"
echo ""
echo "The desktop app window will load the dev server for hot reload."
echo "Press Ctrl+C to stop all services"
echo ""

# Function to cleanup background processes
cleanup() {
    echo ""
    echo "Stopping services..."
    kill $BACKEND_PID 2>/dev/null || true
    kill $FRONTEND_PID 2>/dev/null || true
    wait $BACKEND_PID 2>/dev/null || true
    wait $FRONTEND_PID 2>/dev/null || true
    echo "All services stopped"
    exit 0
}

trap cleanup SIGINT SIGTERM

# Start backend in background
echo "Starting backend..."
export PGISOMAP_DEBUG=true
export PGISOMAP_WEB_PORT=8080  # Fixed port for dev mode (vite proxy expects this)
uv run python -m pg_isomap &
BACKEND_PID=$!

# Give backend a moment to start
sleep 2

# Start frontend dev server in background
echo "Starting frontend dev server..."
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

# Give frontend dev server a moment to start
sleep 2

# Start desktop app (points to dev server for hot reload)
echo "Opening desktop app window..."
export PGISOMAP_DEV_MODE=true
uv run python -c "
import webview
import sys
import os

# Point to dev server for hot reload
url = 'http://localhost:5173'

window = webview.create_window(
    title='PG Isomap (Dev)',
    url=url,
    width=1280,
    height=800,
    resizable=True,
    min_size=(800, 600),
)

webview.start(debug=True)
print('Desktop app closed')
"

# Cleanup when window closes
cleanup
