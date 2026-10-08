"""Tests for icons.py (bundled SVGs; QGIS layer icons are stubbed)."""
import os

import pytest

from layer_order_plus_qgis4.icons import (
    bundled_icon,
    icon_add_group,
    icon_for_layer,
    icon_group,
    icon_remove_group,
    icon_rename_group,
)

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_bundled_icon_missing_file_returns_empty_qicon():
    assert bundled_icon("does_not_exist.svg").isNull()


@pytest.mark.parametrize("factory", [icon_group, icon_add_group, icon_remove_group,
                                     icon_rename_group])
def test_toolbar_and_group_icons_are_bundled(factory):
    assert not factory().isNull()


def test_icon_for_missing_layer_is_generic():
    assert not icon_for_layer(None).isNull()


def test_bundled_icon_files_exist():
    icons_dir = os.path.join(PLUGIN_ROOT, "icons")
    for fname in ("folder.svg", "add_group.svg", "remove_group.svg",
                  "rename_group.svg", "layer.svg"):
        assert os.path.isfile(os.path.join(icons_dir, fname)), f"missing icon: {fname}"
