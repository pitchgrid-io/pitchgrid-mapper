#!/usr/bin/env bash
# Prints the project version from pyproject.toml.
# Single source of truth for build/release scripts — used instead of any
# APP_VERSION that might leak in from .env.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYPROJECT="${SCRIPT_DIR}/../pyproject.toml"

if [ ! -f "$PYPROJECT" ]; then
    echo "pyproject.toml not found at $PYPROJECT" >&2
    exit 1
fi

VERSION=$(awk '
    /^\[project\]/ { in_project = 1; next }
    /^\[/ && !/^\[project\]/ { in_project = 0 }
    in_project && /^version[[:space:]]*=/ {
        if (match($0, /"[^"]+"/)) {
            print substr($0, RSTART + 1, RLENGTH - 2)
            exit
        }
    }
' "$PYPROJECT")

if [ -z "$VERSION" ]; then
    echo "version not found in [project] section of $PYPROJECT" >&2
    exit 1
fi

echo "$VERSION"
