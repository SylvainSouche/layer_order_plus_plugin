#!/usr/bin/env bash
# Run the Layer Order Plus unit tests.
#
# Sets up LD_LIBRARY_PATH so PyQt6's QtWidgets can find libEGL.so.1
# (extracted into tests/vendor/ by tests/setup_libegl.sh), then runs pytest.
#
# Usage: ./scripts/run_tests.sh [pytest args...]
set -e

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PLUGIN_DIR"

# Locally-bundled libegl1 (extracted from the Debian package; we don't have
# root to apt-get install it system-wide in this environment).
VENDOR_LIB="$PLUGIN_DIR/tests/vendor/usr/lib/x86_64-linux-gnu"
if [ -d "$VENDOR_LIB" ]; then
    export LD_LIBRARY_PATH="$VENDOR_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

# Headless Qt
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"

exec python3 -m pytest tests/ "$@"
