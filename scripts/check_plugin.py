#!/usr/bin/env python3
"""CI checks for Layer Order Plus (no QGIS runtime required)."""
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
    for key in ("name", "qgisMinimumVersion", "description", "version", "author", "email"):
        if key not in g or not str(g[key]).strip():
            err(f"metadata.txt missing required key: {key}")

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

    ok(f"qgisMinimumVersion={g.get('qgisMinimumVersion')} qgisMaximumVersion={g.get('qgisMaximumVersion', '')}")


def check_python_syntax() -> None:
    for path in sorted(ROOT.glob("*.py")):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            ok(f"syntax {path.name}")
        except SyntaxError as e:
            err(f"syntax error in {path.name}: {e}")


def check_qt6_patterns() -> None:
    for path in (ROOT / "dock.py", ROOT / "plugin.py"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(
            r"from\s+qgis\.PyQt\.QtWidgets\s+import\s+\([^)]*QUndo(Command|Stack)",
            text,
            re.S,
        ):
            err(f"{path.name}: QUndo* must not be imported from QtWidgets on QGIS 4")
        if "Qt.LeftDockWidgetArea" in text:
            err(f"{path.name}: use Qt.DockWidgetArea.LeftDockWidgetArea")
        if "Qt.UserRole" in text and "ItemDataRole" not in text:
            warn(f"{path.name}: Qt.UserRole without ItemDataRole")
    dock = ROOT / "dock.py"
    if dock.is_file():
        t = dock.read_text(encoding="utf-8")
        if "QUndoCommand" in t and "QtGui" in t:
            ok("dock.py: QUndo* via QtGui")
        if "ItemDataRole" in t:
            ok("dock.py: ItemDataRole used")
        if "_icon_group" in t and "_icon_for_layer" in t:
            ok("dock.py: icon helpers present")


def check_required_files() -> None:
    for name in ("__init__.py", "plugin.py", "dock.py", "metadata.txt", "VERSION", "LICENSE"):
        if (ROOT / name).is_file():
            ok(f"file {name}")
        else:
            err(f"missing required file: {name}")


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
