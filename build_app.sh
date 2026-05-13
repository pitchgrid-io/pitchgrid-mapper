#!/bin/bash
# Build script for PitchGrid Mapper macOS app
# Usage: ./build_app.sh [--arch arm64|x86_64]

set -e  # Exit on any error

# Parse arguments
ARCH=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --arch)
            ARCH="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: ./build_app.sh [--arch arm64|x86_64]"
            exit 1
            ;;
    esac
done

# Default to native architecture if not specified
if [ -z "$ARCH" ]; then
    ARCH=$(uname -m)
fi

# Validate architecture
if [[ "$ARCH" != "arm64" && "$ARCH" != "x86_64" ]]; then
    echo "Error: Invalid architecture '$ARCH'. Must be 'arm64' or 'x86_64'"
    exit 1
fi

# Load environment variables (for secrets, Apple creds, etc.)
if [ -f .env ]; then
    set -a  # automatically export all variables
    source .env
    set +a
fi

# Version is always derived from pyproject.toml — overriding any value that
# may have leaked in from .env so the single source of truth is the project
# manifest, not a local dotfile.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_VERSION="$("${SCRIPT_DIR}/scripts/get_version.sh")"
export APP_VERSION

# Export ARCH for other scripts
export BUILD_ARCH="$ARCH"

APP_NAME="${APP_NAME:-PitchGrid Mapper}"
echo "Building ${APP_NAME} macOS application for ${ARCH}..."

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "Error: uv is not installed"
    echo "Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Check if Rust toolchain is installed (required to build the
# pg_wooting_bridge crate via maturin).
if ! command -v cargo &> /dev/null; then
    echo "Error: Rust toolchain (cargo) is not installed."
    echo "Install with: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    exit 1
fi

# Set up architecture-specific build environment
NATIVE_ARCH=$(uname -m)
if [[ "$ARCH" != "$NATIVE_ARCH" ]]; then
    echo "🔄 Cross-compiling for ${ARCH} (native: ${NATIVE_ARCH})"

    # For x86_64 on arm64, we need Rosetta and x86_64 Python
    if [[ "$ARCH" == "x86_64" && "$NATIVE_ARCH" == "arm64" ]]; then
        X86_PYTHON="/usr/local/bin/python3.12"
        X86_UV="/usr/local/bin/uv"

        if [ ! -f "$X86_PYTHON" ]; then
            echo "❌ x86_64 Python not found at $X86_PYTHON"
            echo "Install with: arch -x86_64 /usr/local/bin/brew install python@3.12"
            echo ""
            echo "First-time setup for x86_64 builds:"
            echo "  1. Install x86_64 Homebrew:"
            echo "     arch -x86_64 /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
            echo "  2. Install x86_64 Python:"
            echo "     arch -x86_64 /usr/local/bin/brew install python@3.12"
            echo "  3. (Optional) Install x86_64 uv:"
            echo "     arch -x86_64 /usr/local/bin/brew install uv"
            exit 1
        fi

        # Set up x86_64 venv if it doesn't exist or is stale
        VENV_X86=".venv-x86_64"
        if [ ! -d "$VENV_X86" ]; then
            echo "📦 Creating x86_64 virtual environment..."
            arch -x86_64 "$X86_PYTHON" -m venv "$VENV_X86"
        fi

        # Use x86_64 environment
        source "$VENV_X86/bin/activate"
        ARCH_PREFIX="arch -x86_64"
        export CMAKE_OSX_ARCHITECTURES="x86_64"
        export ARCHFLAGS="-arch x86_64"

        # Install/upgrade pip and dependencies in x86_64 venv
        echo "📦 Installing dependencies in x86_64 environment..."
        $ARCH_PREFIX pip install --upgrade pip
        $ARCH_PREFIX pip install pyinstaller python-dotenv
        $ARCH_PREFIX pip install -e ".[build]"

        # Reinstall scalatrix for x86_64
        echo "📦 Building scalatrix for x86_64..."
        $ARCH_PREFIX pip install --force-reinstall --no-cache-dir ../scalatrix
    fi
else
    ARCH_PREFIX=""
fi

# Generate fresh icons from icon_app.svg
echo "🎨 Generating app icons..."
./generate_icons.sh

# Build frontend
echo "🌐 Building frontend..."
cd frontend
npm install
npm run build
cd ..

# For native builds, sync with uv
if [ -z "$ARCH_PREFIX" ]; then
    echo "📦 Syncing Python dependencies..."
    uv sync --extra build

    # Force a fresh build of the Wooting bridge wheel. uv's cache has
    # been observed to serve a stale wheel when only the underlying
    # Rust source changes — `uv sync` then reports "Built" but actually
    # reuses the previously-cached .so. For release builds we always
    # want the bridge to reflect the current source.
    #
    # The `./` prefix is required so uv treats wooting_bridge as a local
    # path rather than a package name on PyPI.
    echo "🦀 Rebuilding pg-wooting-bridge from source (no cache)..."
    uv pip install --reinstall --no-cache ./wooting_bridge
fi

# Clean previous builds
rm -rf build/ dist/

# Create version file for runtime
echo "📝 Creating version file..."
echo "${APP_VERSION}" > _version.txt

# Build the application
# Note: target_arch is set in pg_isomap.spec via BUILD_ARCH environment variable
echo "🔨 Building application with PyInstaller for ${ARCH}..."
if [ -n "$ARCH_PREFIX" ]; then
    $ARCH_PREFIX python -m PyInstaller pg_isomap.spec
else
    uv run pyinstaller pg_isomap.spec
fi

# Clean up version file
rm -f _version.txt

# Deactivate x86_64 venv if active
if [ -n "$ARCH_PREFIX" ]; then
    deactivate 2>/dev/null || true
fi

# Sign the app
echo ""
echo "🔐 Signing the application..."
./sign_app.sh

echo ""
echo "✓ Build completed successfully!"
echo "✓ App location: dist/${APP_NAME}.app"
echo "✓ Architecture: ${ARCH}"
echo ""
echo "To test the app:"
echo "  open dist/${APP_NAME}.app"
echo ""
echo "To create a DMG for distribution:"
echo "  ./notarize_app.sh --arch ${ARCH}"
