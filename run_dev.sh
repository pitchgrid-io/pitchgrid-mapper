#!/bin/bash
# Development startup script - Desktop app with hot reload
# Works on macOS, Linux, and Windows (Git Bash)

set -e

echo "=== PitchGrid Mapper Development Mode ==="
echo ""

# Detect platform
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
        IS_WINDOWS=true
        # On Windows, ensure common paths are in PATH for Git Bash
        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH:/c/Users/$USER/.local/bin:/c/Users/$USER/AppData/Local/Microsoft/WinGet/Packages"
        ;;
    *)
        IS_WINDOWS=false
        ;;
esac

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "Error: uv is not installed"
    if [ "$IS_WINDOWS" = true ]; then
        echo "Install with: powershell -c 'irm https://astral.sh/uv/install.ps1 | iex'"
    else
        echo "Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    fi
    exit 1
fi

# Check if Python 3.12 is available
if ! uv run python --version | grep -q "3.12"; then
    echo "Warning: Python 3.12 is required (found: $(uv run python --version))"
    echo "Make sure .python-version is set to 3.12"
fi

# Install/sync dependencies
echo "Syncing Python dependencies (upgrading scalatrix to latest)..."
uv sync --upgrade-package scalatrix

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
echo "Virtual MIDI Device: PitchGrid Mapper"
echo ""
echo "Press Ctrl+C to stop all services"
echo ""

# Port file for backend to communicate its ephemeral port
BACKEND_PORT_FILE="$PWD/.dev_backend_port"
rm -f "$BACKEND_PORT_FILE"

# Function to cleanup background processes
cleanup() {
    echo ""
    echo "Stopping services..."
    if [ "$IS_WINDOWS" = true ]; then
        taskkill //F //PID $BACKEND_PID 2>/dev/null || kill $BACKEND_PID 2>/dev/null || true
        taskkill //F //PID $FRONTEND_PID 2>/dev/null || kill $FRONTEND_PID 2>/dev/null || true
    else
        kill $BACKEND_PID 2>/dev/null || true
        kill $FRONTEND_PID 2>/dev/null || true
    fi
    wait $BACKEND_PID 2>/dev/null || true
    wait $FRONTEND_PID 2>/dev/null || true
    rm -f "$BACKEND_PORT_FILE"
    echo "All services stopped"
    exit 0
}

trap cleanup SIGINT SIGTERM EXIT

# Pick an available port for the frontend
FRONTEND_PORT=$(uv run python -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()")

# Start backend with ephemeral port
echo "Starting backend..."
export PGISOMAP_DEBUG=true
export PGISOMAP_PORT_FILE="$BACKEND_PORT_FILE"
# Don't set PGISOMAP_WEB_PORT — let the OS assign an ephemeral port
uv run python -m pg_isomap &
BACKEND_PID=$!

# Wait for backend to write its port file
echo "Waiting for backend to start..."
WAIT_COUNT=0
while [ ! -f "$BACKEND_PORT_FILE" ] && kill -0 $BACKEND_PID 2>/dev/null; do
    sleep 0.2
    WAIT_COUNT=$((WAIT_COUNT + 1))
    if [ $WAIT_COUNT -ge 50 ]; then
        echo "Error: Backend failed to start within timeout"
        exit 1
    fi
done

if [ ! -f "$BACKEND_PORT_FILE" ]; then
    echo "Error: Backend process exited before becoming ready"
    exit 1
fi

BACKEND_PORT=$(cat "$BACKEND_PORT_FILE")

echo ""
echo "====================================="
echo "  Backend API:  http://localhost:$BACKEND_PORT"
echo "  Frontend Dev: http://localhost:$FRONTEND_PORT"
echo "====================================="
echo ""

# Start frontend dev server with backend port for proxy
echo "Starting frontend dev server..."
cd frontend
BACKEND_PORT=$BACKEND_PORT FRONTEND_PORT=$FRONTEND_PORT npm run dev &
FRONTEND_PID=$!
cd ..

# Give frontend dev server a moment to start
sleep 2

# Start desktop app (points to dev server for hot reload)
echo "Opening desktop app window..."
export PGISOMAP_DEV_MODE=true
uv run python -c "
import webview

url = 'http://localhost:$FRONTEND_PORT'

window = webview.create_window(
    title='PitchGrid Mapper (Dev)',
    url=url,
    width=1280,
    height=800,
    resizable=True,
    min_size=(800, 600),
)

webview.start(debug=True)
print('Desktop app closed')
"

# Cleanup when window closes (trap EXIT handles this)
