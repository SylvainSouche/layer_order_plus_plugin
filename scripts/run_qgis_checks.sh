#!/usr/bin/env bash
# Run the in-QGIS checks (tests/qgis/check_*.py) headless, under every QGIS
# found on this Mac, or one script with --script FILE (first QGIS found).
#
# QGIS installs are found in $QGIS_APPS (space-separated .app paths) or in
# /Applications/QGIS*.app and /Applications/MacPorts/QGIS*.app. Two kinds:
#   * official builds, with their own Python (Contents/MacOS/python3.*);
#   * MacPorts builds, whose bindings run with MacPorts' Python.
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APPS="${QGIS_APPS:-$(ls -d /Applications/QGIS*.app /Applications/MacPorts/QGIS*.app 2>/dev/null || true)}"
[ -n "$APPS" ] || { echo "No QGIS found; set QGIS_APPS=\"/path/to/QGIS.app ...\""; exit 2; }

# The plugin is imported under its package (zip folder) name
PKG_ROOT="$(mktemp -d)"
trap 'rm -rf "$PKG_ROOT"' EXIT
ln -s "$PLUGIN_DIR" "$PKG_ROOT/advanced_layer_order"
export QT_QPA_PLATFORM=offscreen

# Sets PY and the environment for one QGIS app; returns 1 if unusable
use_qgis() {
    local C="$1/Contents"
    unset PYTHONHOME QGIS_PREFIX_PATH
    if ls "$C"/MacOS/python3.* >/dev/null 2>&1; then          # official build
        PY="$(ls "$C"/MacOS/python3.* | head -1)"
        local V; V="$(basename "$PY")"
        export PYTHONHOME="$C/Resources" QGIS_PREFIX_PATH="$C/MacOS"
        export PYTHONPATH="$C/Resources/$V:$C/Resources/$V/site-packages:$C/Resources/$V/lib-dynload"
    elif [ -d "$C/Resources/python/qgis" ]; then               # MacPorts build
        # The bindings don't name their Python: take the one that loads them
        export PYTHONPATH="$C/Resources/python"
        PY=""
        for candidate in $(ls -r /opt/local/bin/python3.* 2>/dev/null | grep -E 'python3\.[0-9]+$'); do
            if "$candidate" -c "import qgis.core" >/dev/null 2>&1; then PY="$candidate"; break; fi
        done
        [ -n "$PY" ] || return 1
    else
        return 1
    fi
    export PYTHONPATH="$PYTHONPATH:$PKG_ROOT:$PLUGIN_DIR/tests/qgis"
}

qgis_version() {
    "$PY" -c "from qgis.core import Qgis; print(Qgis.version().split('-')[0])" 2>/dev/null || echo "?"
}

if [ "${1:-}" = "--script" ]; then
    for app in $APPS; do
        use_qgis "$app" && exec "$PY" "$2"
    done
    echo "No usable QGIS"; exit 2
fi

status=0
for app in $APPS; do
    use_qgis "$app" || { echo "skip  $app (no usable Python)"; continue; }
    label="QGIS $(qgis_version)"
    for check in "$PLUGIN_DIR"/tests/qgis/check_*.py; do
        name="$(basename "$check" .py)"
        out="$(cd "$PLUGIN_DIR/tests/qgis" && "$PY" "$check" 2>&1 \
               | grep -v -E 'proj\.db|Populating font|propagateSizeHints|^Warning' || true)"
        if [ "$(echo "$out" | tail -1)" = "OK" ]; then
            printf "PASS  %-12s %s\n" "$label" "$name"
        else
            printf "FAIL  %-12s %s\n" "$label" "$name"; echo "$out" | tail -15 | sed 's/^/      /'; status=1
        fi
    done
done
exit $status
