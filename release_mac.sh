#!/bin/bash
# Full macOS release script - builds and notarizes both arm64 and x86_64
# Usage: ./release_mac.sh

set -e

echo "=========================================="
echo "  PitchGrid Mapper - macOS Full Release"
echo "=========================================="
echo ""

# Load environment variables (for secrets, Apple creds, etc.)
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Version is always derived from pyproject.toml — overriding any value that
# may have leaked in from .env so the single source of truth is the project
# manifest, not a local dotfile.
APP_VERSION="$(./scripts/get_version.sh)"
export APP_VERSION
VERSION="$APP_VERSION"
echo "Version: ${VERSION}"
echo ""

# Build and notarize arm64
echo "=========================================="
echo "  Building for Apple Silicon (arm64)"
echo "=========================================="
./build_app.sh --arch arm64

echo ""
echo "=========================================="
echo "  Notarizing arm64 build"
echo "=========================================="
./notarize_app.sh --arch arm64

# x86_64 builds are currently skipped: the vendored Wooting binaries
# (analog plugin, RGB SDK, hidapi) are arm64-only. Re-enable once those
# are also built for Intel or universal2'd via `lipo -create`.
echo ""
echo "(Skipping x86_64 — vendored Wooting dylibs are arm64-only.)"

# Upload to GitHub
echo ""
echo "=========================================="
echo "  Creating GitHub Release"
echo "=========================================="
./create_release.sh

echo ""
echo "=========================================="
echo "  Release Complete!"
echo "=========================================="
echo ""
echo "DMG files created:"
ls -la PitchGrid-Mapper-${VERSION}-*.dmg 2>/dev/null || echo "  (none found)"
