#!/bin/bash
# Development startup script

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

# Check if frontend is built
if [ ! -d "frontend/dist" ]; then
    echo ""
    echo "Frontend not built. Building..."
    cd frontend
    if ! command -v npm &> /dev/null; then
        echo "Error: npm is not installed"
        exit 1
    fi
    npm install
    npm run build
    cd ..
fi

echo ""
echo "=== Starting PG Isomap ==="
echo "Web UI: http://localhost:8080"
echo "Virtual MIDI Device: PG Isomap"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Run with debug mode
export PGISOMAP_DEBUG=true
uv run python -m pg_isomap
