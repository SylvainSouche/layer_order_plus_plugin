"""Tests for the name substring filter (QualityOverhaul 3.3)."""
import os
import sys

import pytest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from layer_order_plus_qgis4.tree_utils import (
    ROLE_TYPE,
    ROLE_ID,
    TYPE_GROUP,
    TYPE_LAYER,
    new_group_id,
    apply_name_filter,
)


def _make_group(name, gid=None):
    from PyQt6.QtWidgets import QTreeWidgetItem
    it = QTreeWidgetItem([name])
    it.setData(0, ROLE_TYPE, TYPE_GROUP)
    it.setData(0, ROLE_ID, gid or new_group_id())
    return it


def _make_layer(name, lid):
    from PyQt6.QtWidgets import QTreeWidgetItem
    it = QTreeWidgetItem([name])
    it.setData(0, ROLE_TYPE, TYPE_LAYER)
    it.setData(0, ROLE_ID, lid)
    return it


def _build_tree(tree):
    """
    Build:
      [0] group "Roads"
            ├── layer "Highway 1"  (id=l_h1)
            └── layer "Street A"   (id=l_sa)
      [1] layer "Railway"          (id=l_rail)
      [2] group "Water"
            └── layer "River"      (id=l_river)
    """
    roads = _make_group("Roads", "grp_roads")
    roads.addChild(_make_layer("Highway 1", "l_h1"))
    roads.addChild(_make_layer("Street A", "l_sa"))
    tree.addTopLevelItem(roads)
    tree.addTopLevelItem(_make_layer("Railway", "l_rail"))
    water = _make_group("Water", "grp_water")
    water.addChild(_make_layer("River", "l_river"))
    tree.addTopLevelItem(water)


def _visible_top_level_names(tree):
    return [tree.topLevelItem(i).text(0)
            for i in range(tree.topLevelItemCount())
            if not tree.topLevelItem(i).isHidden()]


def _visible_child_names(tree, top_idx):
    top = tree.topLevelItem(top_idx)
    return [top.child(i).text(0)
            for i in range(top.childCount())
            if not top.child(i).isHidden()]


# ---------- tests ----------

def test_empty_needle_shows_everything(tree):
    _build_tree(tree)
    apply_name_filter(tree, "highway")  # hide some
    apply_name_filter(tree, "")         # clear
    assert not tree.topLevelItem(0).isHidden()
    assert not tree.topLevelItem(1).isHidden()
    assert not tree.topLevelItem(2).isHidden()


def test_none_needle_shows_everything(tree):
    _build_tree(tree)
    apply_name_filter(tree, "zzz")
    apply_name_filter(tree, None)
    assert not tree.topLevelItem(0).isHidden()


def test_case_insensitive_match(tree):
    _build_tree(tree)
    apply_name_filter(tree, "HIGHWAY")
    # Roads group visible (has matching descendant), Railway hidden, Water hidden
    assert not tree.topLevelItem(0).isHidden()  # Roads
    assert tree.topLevelItem(1).isHidden()       # Railway
    assert tree.topLevelItem(2).isHidden()       # Water
    # Highway 1 visible, Street A hidden
    assert not tree.topLevelItem(0).child(0).isHidden()  # Highway 1
    assert tree.topLevelItem(0).child(1).isHidden()      # Street A


def test_substring_match(tree):
    _build_tree(tree)
    apply_name_filter(tree, "rail")
    assert tree.topLevelItem(0).isHidden()  # Roads
    assert not tree.topLevelItem(1).isHidden()  # Railway
    assert tree.topLevelItem(2).isHidden()  # Water


def test_group_name_match_keeps_all_descendants_visible(tree):
    """If the group itself matches, all its descendants should be visible."""
    _build_tree(tree)
    apply_name_filter(tree, "water")
    assert tree.topLevelItem(0).isHidden()  # Roads
    assert tree.topLevelItem(1).isHidden()  # Railway
    assert not tree.topLevelItem(2).isHidden()  # Water
    # River should be visible even though it doesn't match "water"
    assert not tree.topLevelItem(2).child(0).isHidden()


def test_group_with_matching_descendant_is_expanded(tree):
    """A group with a matching descendant but no self-match should be expanded."""
    _build_tree(tree)
    roads = tree.topLevelItem(0)
    roads.setExpanded(False)
    apply_name_filter(tree, "highway")
    assert roads.isExpanded() is True


def test_no_match_hides_everything(tree):
    _build_tree(tree)
    apply_name_filter(tree, "zzz_nonexistent")
    assert tree.topLevelItem(0).isHidden()
    assert tree.topLevelItem(1).isHidden()
    assert tree.topLevelItem(2).isHidden()


def test_whitespace_only_needle_clears_filter(tree):
    _build_tree(tree)
    apply_name_filter(tree, "highway")
    apply_name_filter(tree, "   ")
    assert not tree.topLevelItem(0).isHidden()


def test_filter_does_not_remove_items(tree):
    """Filter hides items, doesn't remove them — count should be unchanged."""
    _build_tree(tree)
    before_count = tree.topLevelItemCount() + sum(tree.topLevelItem(i).childCount()
                                                   for i in range(tree.topLevelItemCount()))
    apply_name_filter(tree, "highway")
    after_count = tree.topLevelItemCount() + sum(tree.topLevelItem(i).childCount()
                                                  for i in range(tree.topLevelItemCount()))
    assert before_count == after_count


def test_clear_filter_after_match_restores_all(tree):
    _build_tree(tree)
    apply_name_filter(tree, "highway")
    apply_name_filter(tree, "")
    # All items visible again
    for i in range(tree.topLevelItemCount()):
        assert not tree.topLevelItem(i).isHidden()
        for j in range(tree.topLevelItem(i).childCount()):
            assert not tree.topLevelItem(i).child(j).isHidden()


def test_match_in_nested_group_keeps_ancestors_visible(tree):
    """A layer deep in a nested group should keep all its ancestor groups visible."""
    outer = _make_group("Outer", "grp_outer")
    inner = _make_group("Inner", "grp_inner")
    inner.addChild(_make_layer("Treasure", "l_treasure"))
    outer.addChild(inner)
    tree.addTopLevelItem(outer)

    apply_name_filter(tree, "treasure")
    assert not outer.isHidden()
    assert not inner.isHidden()
    assert not inner.child(0).isHidden()
    # Group should be expanded so the match is visible
    assert outer.isExpanded()
    assert inner.isExpanded()
