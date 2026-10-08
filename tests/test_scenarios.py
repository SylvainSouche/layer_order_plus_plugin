"""Tests for grouping + moving scenarios — drives bug discovery + fixes.

These tests exercise the Model + ViewController logic for complex grouping
and layer-moving scenarios. They use the LayerOrderModel directly (Qt-free)
plus a headless ViewController where needed.
"""
import os
import sys

import pytest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

# Stub the package so importing model.py doesn't trigger __init__.py → plugin.py → dock.py
import types
if "layer_order_plus_qgis4" not in sys.modules:
    pkg = types.ModuleType("layer_order_plus_qgis4")
    pkg.__path__ = [PLUGIN_ROOT]
    sys.modules["layer_order_plus_qgis4"] = pkg

from layer_order_plus_qgis4.model import (
    LayerOrderModel,
    GroupNode,
    LayerNode,
    new_group_id,
)


# ---------- helpers ----------

def _make_model_with_flat(layers):
    """Build a Model with flat top-level layers. layers = list of (id, name)."""
    m = LayerOrderModel()
    for lid, name in layers:
        m.add_layer(lid, name)
    return m


def _make_model_with_tree(spec):
    """Build a Model from a nested spec.
    spec = list of items, each item is either:
      - ("layer", "l1", "Layer 1") → LayerNode
      - ("group", "g1", "Group 1", [children...]) → GroupNode with children
    """
    m = LayerOrderModel()
    root = []
    for item in spec:
        node = _build_node(item)
        if node is not None:
            root.append(node)
    m._root = root
    return m


def _build_node(item):
    if item[0] == "layer":
        return LayerNode(id=item[1], name=item[2])
    elif item[0] == "group":
        children = [_build_node(ch) for ch in item[3]]
        return GroupNode(id=item[1], name=item[2], children=children)
    return None


def _flat_ids(model):
    """Return the flattened layer id list (depth-first)."""
    return model.get_flattened_layer_ids()


def _tree_structure(model):
    """Return a nested tuple representation of the tree for assertions.
    Format: ("layer", id) or ("group", id, name, [child_structures...])
    """
    def ser(node):
        if isinstance(node, LayerNode):
            return ("layer", node.id)
        return ("group", node.id, node.name, [ser(ch) for ch in node.children])
    return [ser(n) for n in model.get_root()]


# ====================================================================
# BUG-1: Multi-select "Move to bottom" reverses item order
# ====================================================================

def test_move_items_to_bottom_multiple_preserves_order():
    """Select [A, B] in [A, B, C, D], move to bottom → should be [C, D, A, B].

    BUG-1: currently produces [C, D, B, A] (reversed).
    """
    model = _make_model_with_flat([("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")])
    model.move_items_to_boundary(["a", "b"], to_top=False)
    assert _flat_ids(model) == ["c", "d", "a", "b"]


def test_move_items_to_top_multiple_preserves_order():
    """Select [B, C] in [A, B, C, D], move to top → should be [B, C, A, D]."""
    model = _make_model_with_flat([("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")])
    model.move_items_to_boundary(["b", "c"], to_top=True)
    assert _flat_ids(model) == ["b", "c", "a", "d"]


def test_move_items_to_bottom_single():
    """Single item to bottom — should work correctly."""
    model = _make_model_with_flat([("a", "A"), ("b", "B"), ("c", "C")])
    model.move_items_to_boundary(["a"], to_top=False)
    assert _flat_ids(model) == ["b", "c", "a"]


# ====================================================================
# BUG-4: Moving all items out of a group leaves an empty group
# ====================================================================

def test_move_all_items_out_of_group_prunes_empty():
    """With remove_empty_groups=True, moving all items out should prune the group.

    BUG-4: move_item never prunes empty groups → leaves [A, B, Group1()].
    """
    model = _make_model_with_tree([
        ("group", "g1", "Group1", [
            ("layer", "a", "A"),
            ("layer", "b", "B"),
        ])
    ])
    model.set_remove_empty_groups(True)
    # Move A to top-level
    model.move_item("a", None, 0)
    # Move B to top-level
    model.move_item("b", None, 1)
    # Group1 should be pruned (it's now empty)
    assert model.find_item("g1") is None, "Empty group should be pruned after move_item"
    assert _flat_ids(model) == ["a", "b"]


def test_move_all_items_out_of_group_keeps_when_setting_off():
    """With remove_empty_groups=False, empty group should persist."""
    model = _make_model_with_tree([
        ("group", "g1", "Group1", [
            ("layer", "a", "A"),
            ("layer", "b", "B"),
        ])
    ])
    model.set_remove_empty_groups(False)
    model.move_item("a", None, 0)
    model.move_item("b", None, 1)
    # Group1 should still exist (empty)
    grp = model.find_item("g1")
    assert grp is not None, "Empty group should persist when setting is off"
    assert len(grp.children) == 0


# ====================================================================
# Scenario: within-group swap via native panel (reconcile)
# ====================================================================

def test_reconcile_within_group_swap():
    """(C, Group(A, B)) → native reorders to [C, B, A] → Plus should become (C, Group(B, A)).

    Tests _reorder_all_levels_to_match_qgis logic at the Model level.
    """
    from layer_order_plus_qgis4.controller import LayerOrderController
    # We can't easily test Controller without QGIS, but we can test the
    # reorder logic by simulating what _reorder_all_levels_to_match_qgis does.
    # For now, test that the Model's move_item handles within-group reorder.
    model = _make_model_with_tree([
        ("layer", "c", "C"),
        ("group", "g1", "Group1", [
            ("layer", "a", "A"),
            ("layer", "b", "B"),
        ])
    ])
    # Simulate: swap A and B within Group1
    model.move_item("b", "g1", 0)
    grp = model.find_item("g1")
    assert [c.id for c in grp.children] == ["b", "a"]


# ====================================================================
# Scenario: create group from multiple top-level items
# ====================================================================

def test_create_group_from_two_layers():
    """[A, B, C] → select A+B, create group → (Group(A, B), C)."""
    model = _make_model_with_flat([("a", "A"), ("b", "B"), ("c", "C")])
    gid = model.create_group("NewGroup", parent_id=None, index=2)
    model.move_item("a", gid, 0)
    model.move_item("b", gid, 1)
    assert _flat_ids(model) == ["a", "b", "c"]
    grp = model.find_item(gid)
    assert grp is not None
    assert [c.id for c in grp.children] == ["a", "b"]


def test_create_group_from_two_groups():
    """(Group(A,B), Group(C,D)) → select both, create group → (Outer(Group(A,B), Group(C,D)))."""
    model = _make_model_with_tree([
        ("group", "g1", "Group1", [("layer", "a", "A"), ("layer", "b", "B")]),
        ("group", "g2", "Group2", [("layer", "c", "C"), ("layer", "d", "D")]),
    ])
    outer = model.create_group("Outer", parent_id=None, index=2)
    model.move_item("g1", outer, 0)
    model.move_item("g2", outer, 1)
    assert _flat_ids(model) == ["a", "b", "c", "d"]
    outer_node = model.find_item(outer)
    assert outer_node is not None
    assert [c.id for c in outer_node.children] == ["g1", "g2"]


# ====================================================================
# Scenario: delete group with unwrap
# ====================================================================

def test_delete_group_unwrap_children():
    """(Group(A, B), C) → delete Group → (A, B, C)."""
    model = _make_model_with_tree([
        ("group", "g1", "Group1", [("layer", "a", "A"), ("layer", "b", "B")]),
        ("layer", "c", "C"),
    ])
    model.delete_group("g1", unwrap_children=True)
    assert _flat_ids(model) == ["a", "b", "c"]
    assert model.find_item("g1") is None


def test_delete_nested_group_unwrap():
    """(Outer(Inner(A, B), C)) → delete Inner → (Outer(A, B, C))."""
    model = _make_model_with_tree([
        ("group", "outer", "Outer", [
            ("group", "inner", "Inner", [("layer", "a", "A"), ("layer", "b", "B")]),
            ("layer", "c", "C"),
        ])
    ])
    model.delete_group("inner", unwrap_children=True)
    outer = model.find_item("outer")
    assert outer is not None
    assert [c.id for c in outer.children] == ["a", "b", "c"]
    assert model.find_item("inner") is None


# ====================================================================
# Scenario: move item into group + out of group
# ====================================================================

def test_move_layer_into_group():
    """(A, B, C) → move B into new group → (A, Group(B), C) → move A in → (Group(A, B), C)."""
    model = _make_model_with_flat([("a", "A"), ("b", "B"), ("c", "C")])
    gid = model.create_group("Group", parent_id=None, index=1)
    model.move_item("b", gid, 0)
    model.move_item("a", gid, 0)
    assert _flat_ids(model) == ["a", "b", "c"]
    grp = model.find_item(gid)
    assert [c.id for c in grp.children] == ["a", "b"]


def test_move_layer_out_of_group():
    """(Group(A, B), C) → move A to top-level index 0 → (A, Group(B), C)."""
    model = _make_model_with_tree([
        ("group", "g1", "Group1", [("layer", "a", "A"), ("layer", "b", "B")]),
        ("layer", "c", "C"),
    ])
    model.move_item("a", None, 0)
    assert _flat_ids(model) == ["a", "b", "c"]
    grp = model.find_item("g1")
    assert [c.id for c in grp.children] == ["b"]


# ====================================================================
# Scenario: nested group manipulation
# ====================================================================

def test_move_layer_into_nested_group():
    """(Outer(Inner(A, B), C)) → move C into Inner → (Outer(Inner(C, A, B)))."""
    model = _make_model_with_tree([
        ("group", "outer", "Outer", [
            ("group", "inner", "Inner", [("layer", "a", "A"), ("layer", "b", "B")]),
            ("layer", "c", "C"),
        ])
    ])
    model.move_item("c", "inner", 0)
    inner = model.find_item("inner")
    assert [c.id for c in inner.children] == ["c", "a", "b"]


def test_move_group_into_another_group():
    """(Group1(A), Group2(B)) → move Group1 into Group2 → (Group2(Group1(A), B))."""
    model = _make_model_with_tree([
        ("group", "g1", "Group1", [("layer", "a", "A")]),
        ("group", "g2", "Group2", [("layer", "b", "B")]),
    ])
    model.move_item("g1", "g2", 0)
    g2 = model.find_item("g2")
    assert [c.id for c in g2.children] == ["g1", "b"]


# ====================================================================
# Scenario: cycle prevention
# ====================================================================

def test_move_group_into_itself_blocked():
    """Can't move a group into itself."""
    model = _make_model_with_tree([
        ("group", "g1", "Group1", [("layer", "a", "A")]),
    ])
    before = model.serialize()
    model.move_item("g1", "g1", 0)  # should be no-op
    after = model.serialize()
    assert before == after


def test_move_group_into_descendant_blocked():
    """Can't move a group into its descendant."""
    model = _make_model_with_tree([
        ("group", "outer", "Outer", [
            ("group", "inner", "Inner", [("layer", "a", "A")]),
        ])
    ])
    before = model.serialize()
    model.move_item("outer", "inner", 0)  # should be no-op
    after = model.serialize()
    assert before == after


# ====================================================================
# Scenario: multi-select drag preserves order
# ====================================================================

def test_multi_move_preserves_order_to_top():
    """Select [A, B] in [A, B, C, D], move to top → [A, B, C, D] (no change, already top)."""
    model = _make_model_with_flat([("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")])
    model.move_items_to_boundary(["a", "b"], to_top=True)
    # A and B are already at top — no move, no events
    assert _flat_ids(model) == ["a", "b", "c", "d"]


def test_multi_move_preserves_order_to_bottom():
    """Select [A, B] in [A, B, C, D], move to bottom → [C, D, A, B] (preserve A before B)."""
    model = _make_model_with_flat([("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")])
    model.move_items_to_boundary(["a", "b"], to_top=False)
    assert _flat_ids(model) == ["c", "d", "a", "b"]


# ====================================================================
# Scenario: empty group handling
# ====================================================================

def test_prune_empty_groups_cascades():
    """(Outer(Inner())) → prune → () — both removed."""
    model = _make_model_with_tree([
        ("group", "outer", "Outer", [
            ("group", "inner", "Inner", []),
        ])
    ])
    removed = model.prune_empty_groups()
    assert removed == 2
    assert model.find_item("outer") is None
    assert model.find_item("inner") is None


def test_prune_empty_groups_keeps_non_empty():
    """(Group1(A), Empty()) → prune → (Group1(A))."""
    model = _make_model_with_tree([
        ("group", "g1", "Group1", [("layer", "a", "A")]),
        ("group", "g2", "Empty", []),
    ])
    removed = model.prune_empty_groups()
    assert removed == 1
    assert model.find_item("g1") is not None
    assert model.find_item("g2") is None


# ====================================================================
# Scenario: serialize/deserialize round-trip with groups
# ====================================================================

def test_serialize_round_trip_with_nested_groups():
    """(Outer(Inner(A, B), C)) → serialize → deserialize → same structure."""
    import json
    model = _make_model_with_tree([
        ("group", "outer", "Outer", [
            ("group", "inner", "Inner", [("layer", "a", "A"), ("layer", "b", "B")]),
            ("layer", "c", "C"),
        ])
    ])
    raw = model.serialize()
    model2 = LayerOrderModel()
    model2.load_from_json(raw)
    assert model2.serialize() == raw
    assert _flat_ids(model2) == ["a", "b", "c"]


def test_serialize_preserves_expanded_state():
    """Group with expanded=False → serialize → deserialize → expanded=False."""
    import json
    model = _make_model_with_tree([
        ("group", "g1", "Group1", [("layer", "a", "A")]),
    ])
    model.set_expanded("g1", False)
    raw = model.serialize()
    obj = json.loads(raw)
    assert obj["children"][0]["expanded"] is False

    model2 = LayerOrderModel()
    model2.load_from_json(raw)
    grp = model2.find_item("g1")
    assert grp.expanded is False
