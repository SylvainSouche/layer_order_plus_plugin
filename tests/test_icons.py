"""Smoke tests for icons.py.

These don't verify the actual icon images (would need QGIS theme + bundled
SVGs to be meaningful) — they just verify the helpers don't crash and return
QIcon objects.
"""
import os
import sys

import pytest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from PyQt6.QtGui import QIcon
from layer_order_plus_qgis4 import icons as icons_mod
from layer_order_plus_qgis4.icons import (
    icon_group,
    icon_add_group,
    icon_remove_group,
    icon_rename_group,
    icon_for_layer,
    bundled_icon,
    theme_icon,
)


def test_bundled_icon_returns_qicon():
    result = bundled_icon("folder.svg")
    assert isinstance(result, QIcon)


def test_bundled_icon_missing_file_returns_empty_qicon():
    result = bundled_icon("does_not_exist.svg")
    assert isinstance(result, QIcon)
    assert result.isNull()  # empty icon for missing file


def test_theme_icon_returns_qicon():
    result = theme_icon("/mIconFolder.svg")
    assert isinstance(result, QIcon)


def test_icon_group_returns_qicon():
    result = icon_group()
    assert isinstance(result, QIcon)


def test_icon_add_group_returns_qicon():
    result = icon_add_group()
    assert isinstance(result, QIcon)


def test_icon_remove_group_returns_qicon():
    result = icon_remove_group()
    assert isinstance(result, QIcon)


def test_icon_rename_group_returns_qicon():
    result = icon_rename_group()
    assert isinstance(result, QIcon)


def test_icon_for_layer_none_returns_qicon():
    """icon_for_layer(None) should fall through to the generic layer icon."""
    result = icon_for_layer(None)
    assert isinstance(result, QIcon)


def test_icon_for_layer_with_mock_layer():
    """A non-None layer that throws on every method call should still return a QIcon."""
    class BrokenLayer:
        def __getattr__(self, name):
            raise Exception(f"mocked: {name}")
    # Should not raise even when the layer is broken
    result = icon_for_layer(BrokenLayer())
    assert isinstance(result, QIcon)


def test_bundled_icons_directory_exists():
    """The icons/ folder should exist and contain the bundled SVGs."""
    plugin_root = os.path.dirname(PLUGIN_ROOT)
    # Actually the plugin dir IS PLUGIN_ROOT
    icons_dir = os.path.join(PLUGIN_ROOT, "icons")
    assert os.path.isdir(icons_dir), f"icons/ directory missing at {icons_dir}"
    expected_files = ["folder.svg", "add_group.svg", "remove_group.svg",
                      "rename_group.svg", "point.svg", "line.svg",
                      "polygon.svg", "raster.svg", "layer.svg"]
    for fname in expected_files:
        assert os.path.isfile(os.path.join(icons_dir, fname)), f"missing icon: {fname}"
