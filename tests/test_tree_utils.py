"""Tests for the View's tree_utils helpers."""

from layer_order_plus_qgis4.tree_utils import (
    TYPE_GROUP,
    TYPE_LAYER,
    ROLE_TYPE,
    ROLE_ID,
    find_group_item,
    find_layer_item,
)
from layer_order_plus_qgis4.model import new_group_id


# ---------- helpers ----------

def _make_group(name, gid=None):
    """Build a group QTreeWidgetItem with the right roles set."""
    from PyQt6.QtWidgets import QTreeWidgetItem
    from PyQt6.QtCore import Qt
    it = QTreeWidgetItem([name])
    it.setData(0, ROLE_TYPE, TYPE_GROUP)
    it.setData(0, ROLE_ID, gid or new_group_id())
    return it


def _make_layer(name, lid):
    """Build a layer QTreeWidgetItem with the right roles set."""
    from PyQt6.QtWidgets import QTreeWidgetItem
    it = QTreeWidgetItem([name])
    it.setData(0, ROLE_TYPE, TYPE_LAYER)
    it.setData(0, ROLE_ID, lid)
    return it


def _build_sample_tree(tree):
    """
    Build:
      top0 (group "Group A")
        └── layer "Layer 1" (id=l1)
      top1 (layer "Layer 2" (id=l2))
      top2 (group "Group B")
        ├── layer "Layer 3" (id=l3)
        └── group "Nested"
              └── layer "Layer 4" (id=l4)
    """
    a = _make_group("Group A", "grp_a")
    a.addChild(_make_layer("Layer 1", "l1"))
    tree.addTopLevelItem(a)

    tree.addTopLevelItem(_make_layer("Layer 2", "l2"))

    b = _make_group("Group B", "grp_b")
    b.addChild(_make_layer("Layer 3", "l3"))
    nested = _make_group("Nested", "grp_nested")
    nested.addChild(_make_layer("Layer 4", "l4"))
    b.addChild(nested)
    tree.addTopLevelItem(b)


# ---------- tests ----------

def test_find_group_item_hit(tree):
    _build_sample_tree(tree)
    it = find_group_item(tree, "grp_nested")
    assert it is not None
    assert it.text(0) == "Nested"


def test_find_group_item_miss(tree):
    _build_sample_tree(tree)
    assert find_group_item(tree, "grp_nonexistent") is None


def test_find_group_item_empty_id(tree):
    _build_sample_tree(tree)
    assert find_group_item(tree, "") is None
    assert find_group_item(tree, None) is None


def test_find_layer_item_hit(tree):
    _build_sample_tree(tree)
    it = find_layer_item(tree, "l4")
    assert it is not None
    assert it.text(0) == "Layer 4"


def test_find_layer_item_miss(tree):
    _build_sample_tree(tree)
    assert find_layer_item(tree, "l_nonexistent") is None
