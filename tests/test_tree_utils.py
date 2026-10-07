"""Tests for tree_utils pure helpers."""
import os
import sys

import pytest

# Make the plugin package importable as a sibling
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from layer_order_plus_qgis4.tree_utils import (
    TYPE_GROUP,
    TYPE_LAYER,
    ROLE_TYPE,
    ROLE_ID,
    new_group_id,
    collect_group_names,
    unique_group_name,
    expand_item_and_ancestors,
    find_group_item,
    find_layer_item,
    iter_all_layer_ids,
    index_in_parent,
    prune_empty_groups,
)


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

def test_new_group_id_format():
    gid = new_group_id()
    assert gid.startswith("grp_")
    assert len(gid) == len("grp_") + 10


def test_new_group_id_uniqueness():
    ids = {new_group_id() for _ in range(1000)}
    assert len(ids) == 1000  # collisions are astronomically unlikely


def test_collect_group_names(tree):
    _build_sample_tree(tree)
    names = collect_group_names(tree)
    assert names == {"Group A", "Group B", "Nested"}


def test_collect_group_names_empty_tree(tree):
    assert collect_group_names(tree) == set()


def test_unique_group_name_no_collision(tree):
    _build_sample_tree(tree)
    assert unique_group_name(tree, "Brand new") == "Brand new"


def test_unique_group_name_first_collision(tree):
    _build_sample_tree(tree)
    assert unique_group_name(tree, "Group A") == "Group A 2"


def test_unique_group_name_with_existing_suffixed(tree):
    """If 'New group' and 'New group 2' both exist, the next should be 'New group 3'."""
    tree.addTopLevelItem(_make_group("New group", "g1"))
    tree.addTopLevelItem(_make_group("New group 2", "g2"))
    assert unique_group_name(tree, "New group") == "New group 3"


def test_unique_group_name_custom_base(tree):
    _build_sample_tree(tree)
    tree.addTopLevelItem(_make_group("Sites", "g_sites"))
    assert unique_group_name(tree, "Sites") == "Sites 2"
    assert unique_group_name(tree, "Other") == "Other"


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


def test_iter_all_layer_ids(tree):
    _build_sample_tree(tree)
    ids = list(iter_all_layer_ids(tree))
    # Depth-first order: l1, l2, l3, l4
    assert ids == ["l1", "l2", "l3", "l4"]


def test_iter_all_layer_ids_empty_tree(tree):
    assert list(iter_all_layer_ids(tree)) == []


def test_index_in_parent_top_level(tree):
    _build_sample_tree(tree)
    top0 = tree.topLevelItem(0)
    assert index_in_parent(tree, top0) == 0
    top1 = tree.topLevelItem(1)
    assert index_in_parent(tree, top1) == 1


def test_index_in_parent_nested(tree):
    _build_sample_tree(tree)
    grp_b = tree.topLevelItem(2)  # "Group B"
    nested = grp_b.child(1)       # "Nested"
    assert index_in_parent(tree, nested) == 1
    layer3 = grp_b.child(0)
    assert index_in_parent(tree, layer3) == 0


def test_prune_empty_groups_no_op_when_no_empty(tree):
    _build_sample_tree(tree)
    removed = prune_empty_groups(tree)
    assert removed == 0
    # Tree should still have 3 top-level items
    assert tree.topLevelItemCount() == 3


def test_prune_empty_groups_removes_nested_empty(tree):
    """Empty nested group should be removed but its parent kept."""
    a = _make_group("Group A", "grp_a")
    a.addChild(_make_layer("Layer 1", "l1"))
    empty_nested = _make_group("EmptyNested", "grp_empty")
    a.addChild(empty_nested)
    tree.addTopLevelItem(a)

    removed = prune_empty_groups(tree)
    assert removed == 1
    # Group A should still be there with its layer
    assert tree.topLevelItemCount() == 1
    grp_a = tree.topLevelItem(0)
    assert grp_a.text(0) == "Group A"
    assert grp_a.childCount() == 1  # only Layer 1 left
    assert grp_a.child(0).text(0) == "Layer 1"


def test_prune_empty_groups_removes_top_level_empty(tree):
    tree.addTopLevelItem(_make_group("Lonely", "grp_lonely"))
    removed = prune_empty_groups(tree)
    assert removed == 1
    assert tree.topLevelItemCount() == 0


def test_prune_empty_groups_cascades(tree):
    """If pruning a nested group leaves its parent empty, parent should go too."""
    outer = _make_group("Outer", "grp_outer")
    inner = _make_group("Inner", "grp_inner")  # empty
    outer.addChild(inner)
    tree.addTopLevelItem(outer)

    removed = prune_empty_groups(tree)
    assert removed == 2  # inner first, then outer
    assert tree.topLevelItemCount() == 0


def test_prune_empty_groups_keeps_groups_with_layers(tree):
    a = _make_group("Group A", "grp_a")
    a.addChild(_make_layer("Layer 1", "l1"))
    tree.addTopLevelItem(a)
    tree.addTopLevelItem(_make_group("Empty", "grp_empty"))
    b = _make_group("Group B", "grp_b")
    b.addChild(_make_layer("Layer 2", "l2"))
    tree.addTopLevelItem(b)

    removed = prune_empty_groups(tree)
    assert removed == 1
    assert tree.topLevelItemCount() == 2
    assert tree.topLevelItem(0).text(0) == "Group A"
    assert tree.topLevelItem(1).text(0) == "Group B"


def test_expand_item_and_ancestors(tree):
    _build_sample_tree(tree)
    grp_b = tree.topLevelItem(2)
    nested = grp_b.child(1)
    layer4 = nested.child(0)

    # Initially collapsed
    grp_b.setExpanded(False)
    nested.setExpanded(False)

    expand_item_and_ancestors(layer4)

    # Layer items can't be expanded, but ancestors should be
    assert grp_b.isExpanded() is True
    assert nested.isExpanded() is True


def test_expand_item_and_ancestors_none_input(tree):
    # Should not raise on None
    expand_item_and_ancestors(None)
