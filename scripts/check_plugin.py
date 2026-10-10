#!/usr/bin/env python3
"""CI checks for Advanced Layer Order (no QGIS runtime required)."""
from __future__ import annotations

import ast
import re
import sys
from configparser import ConfigParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []
WARNINGS: list[str] = []


def err(msg: str) -> None:
    ERRORS.append(msg)
    print(f"ERROR: {msg}")


def warn(msg: str) -> None:
    WARNINGS.append(msg)
    print(f"WARN: {msg}")


def ok(msg: str) -> None:
    print(f"OK: {msg}")


def check_version_sync() -> str:
    version_file = ROOT / "VERSION"
    if not version_file.is_file():
        err("VERSION file missing")
        return ""
    ver = version_file.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", ver):
        err(f"VERSION must be semver X.Y.Z, got {ver!r}")
    else:
        ok(f"VERSION={ver}")
    return ver


def check_metadata(expected_version: str) -> None:
    path = ROOT / "metadata.txt"
    if not path.is_file():
        err("metadata.txt missing")
        return
    raw = path.read_text(encoding="utf-8")
    try:
        cp = ConfigParser()
        cp.read_string(raw)
    except Exception as e:
        err(f"metadata.txt is not valid INI: {e}")
        return

    if "general" not in cp:
        err("metadata.txt missing [general] section")
        return

    g = cp["general"]
    # Required by plugins.qgis.org (PyQGIS cookbook, plugin metadata table)
    for key in ("name", "qgisMinimumVersion", "description", "about", "version",
                "author", "email", "repository"):
        if key not in g or not str(g[key]).strip():
            err(f"metadata.txt missing required key: {key}")

    if not raw.isascii():
        err("metadata.txt must be ASCII")
    if "supportsQt6" in g:
        err("supportsQt6 is obsolete (QGIS 4 compatibility comes from qgisMaximumVersion)")
    category = g.get("category", "").strip()
    if category and category not in ("Raster", "Vector", "Database", "Mesh", "Web"):
        err(f"invalid category {category!r} (Raster, Vector, Database, Mesh or Web; omit for Plugins)")
    for key in ("qgisMinimumVersion", "qgisMaximumVersion"):
        value = g.get(key, "").strip()
        if value and not re.fullmatch(r"\d+\.\d+(\.\d+)?", value):
            err(f"{key} must be dotted numbers, got {value!r}")
    def as_tuple(v):
        return tuple(int(x) for x in v.split(".")[:2]) if re.fullmatch(r"\d+\.\d+(\.\d+)?", v) else (0, 0)
    if as_tuple(g.get("qgisMinimumVersion", "0").strip()) < (3, 40):
        err("qgisMinimumVersion must be >= 3.40 (oldest QGIS whose APIs the plugin uses)")
    if as_tuple(g.get("qgisMaximumVersion", "0").strip()) < (4, 99):
        err("qgisMaximumVersion must be >= 4.99 to be listed for QGIS 4")
    if not (ROOT / "LICENSE").is_file():
        err("LICENSE (no extension) is mandatory for plugins.qgis.org")
    if "plugin" in g.get("name", "").lower():
        warn("plugin name should not contain the word 'plugin'")

    meta_ver = g.get("version", "").strip()
    if expected_version and meta_ver != expected_version:
        err(f"version mismatch: VERSION={expected_version!r} metadata={meta_ver!r}")
    else:
        ok(f"metadata version matches VERSION ({meta_ver})")

    for i, line in enumerate(raw.splitlines(), 1):
        if re.match(r"^\d+\.\d+\.\d+", line):
            err(
                f"metadata.txt line {i}: unindented version line breaks INI parse: {line!r}"
            )

    icon = g.get("icon", "")
    if icon and not (ROOT / icon).is_file():
        err(f"icon file missing: {icon}")
    elif icon:
        ok(f"icon present: {icon}")

    ok(f"qgisMinimumVersion={g.get('qgisMinimumVersion')} "
       f"qgisMaximumVersion={g.get('qgisMaximumVersion', '')}")


def check_python_syntax() -> None:
    for path in sorted(ROOT.glob("*.py")):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            ok(f"syntax {path.name}")
        except SyntaxError as e:
            err(f"syntax error in {path.name}: {e}")


def check_qt6_patterns() -> None:
    py_files = sorted(ROOT.glob("*.py"))
    for path in py_files:
        text = path.read_text(encoding="utf-8")
        if re.search(
            r"from\s+qgis\.PyQt\.QtWidgets\s+import\s+\([^)]*QUndo(Command|Stack|Group)",
            text,
            re.S,
        ):
            err(f"{path.name}: QUndo* must not be imported from QtWidgets on QGIS 4")
        if "Qt.LeftDockWidgetArea" in text:
            err(f"{path.name}: use Qt.DockWidgetArea.LeftDockWidgetArea")
        if "Qt.UserRole" in text and "ItemDataRole" not in text:
            warn(f"{path.name}: Qt.UserRole without ItemDataRole")


def check_required_files() -> None:
    # Core files always required
    for name in ("__init__.py", "plugin.py", "metadata.txt", "VERSION", "LICENSE"):
        if (ROOT / name).is_file():
            ok(f"file {name}")
        else:
            err(f"missing required file: {name}")
    # Refactored modules (1.0.26+)
    for name in ("model.py", "view.py", "view_controller.py", "controller.py", "reconcile.py",
                 "tree_model.py", "tree_view.py", "icons.py", "undo.py", "logger.py"):
        if (ROOT / name).is_file():
            ok(f"file {name}")
        else:
            err(f"missing refactored module: {name}")


def main() -> int:
    print(f"Checking plugin root: {ROOT}")
    check_required_files()
    ver = check_version_sync()
    check_metadata(ver)
    check_python_syntax()
    check_qt6_patterns()
    print()
    if ERRORS:
        print(f"FAILED: {len(ERRORS)} error(s), {len(WARNINGS)} warning(s)")
        return 1
    print(f"PASSED: 0 errors, {len(WARNINGS)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
