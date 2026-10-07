#!/usr/bin/env bash
# Fetch libegl1 .deb and extract it into tests/vendor/.
#
# In CI environments without root, we can't `apt-get install libegl1`.
# This script downloads the .deb and extracts it locally so PyQt6.QtWidgets
# can find libEGL.so.1 at import time.
#
# Run this once before `./scripts/run_tests.sh`.

set -e

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR_DIR="$PLUGIN_DIR/tests/vendor"
LIB_DIR="$VENDOR_DIR/usr/lib/x86_64-linux-gnu"

mkdir -p "$VENDOR_DIR"
cd /tmp

# Download libegl1 (small, ~35 KB)
apt-get download libegl1

# Extract into vendor dir
for deb in libegl1_*.deb; do
    [ -e "$deb" ] || { echo "ERROR: libegl1 .deb not found after apt-get download"; exit 1; }
    dpkg-deb -x "$deb" "$VENDOR_DIR/"
    break
done

if [ -f "$LIB_DIR/libEGL.so.1" ]; then
    echo "OK: libEGL.so.1 extracted to $LIB_DIR/"
else
    echo "ERROR: libEGL.so.1 not found after extraction"
    exit 1
fi
