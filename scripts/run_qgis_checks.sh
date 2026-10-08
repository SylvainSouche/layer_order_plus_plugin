#!/usr/bin/env bash
# Run the in-QGIS checks (tests/qgis/check_*.py) with QGIS's own Python.
#
# QGIS is found via $QGIS_APP (macOS .app bundle) or the newest
# /Applications/QGIS*.app. Each check runs headless in its own process.
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
QGIS_APP="${QGIS_APP:-$(ls -d /Applications/QGIS*.app 2>/dev/null | sort | tail -1)}"
[ -d "$QGIS_APP" ] || { echo "QGIS not found; set QGIS_APP=/path/to/QGIS.app"; exit 2; }
C="$QGIS_APP/Contents"
PY="$(ls "$C"/MacOS/python3.* | head -1)"
PYVER="$(basename "$PY")"

# The plugin is imported as `layer_order_plus_qgis4` (its zip/folder name)
PKG_ROOT="$(mktemp -d)"
trap 'rm -rf "$PKG_ROOT"' EXIT
ln -s "$PLUGIN_DIR" "$PKG_ROOT/layer_order_plus_qgis4"

export PYTHONHOME="$C/Resources"
export PYTHONPATH="$C/Resources/$PYVER:$C/Resources/$PYVER/site-packages:$C/Resources/$PYVER/lib-dynload:$PKG_ROOT:$PLUGIN_DIR/tests/qgis"
export QGIS_PREFIX_PATH="$C/MacOS"
export QT_QPA_PLATFORM=offscreen

status=0
for check in "$PLUGIN_DIR"/tests/qgis/check_*.py; do
    name="$(basename "$check" .py)"
    out="$("$PY" "$check" 2>&1 | grep -v -E 'proj\.db|Populating font|propagateSizeHints' || true)"
    if [ "$(echo "$out" | tail -1)" = "OK" ]; then
        echo "PASS  $name"
    else
        echo "FAIL  $name"; echo "$out" | tail -15 | sed 's/^/      /'; status=1
    fi
done
exit $status
