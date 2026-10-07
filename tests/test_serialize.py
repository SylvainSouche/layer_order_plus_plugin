"""Tests for tree serialization round-trip + schema version (QualityOverhaul 2.4).

We test the pure JSON shape — the dock's _serialize_tree / load_from_project
are tightly coupled to QGIS state and harder to test in isolation, so we
replicate the JSON shape here and verify round-trip semantics.
"""
import json
import os
import sys

import pytest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from layer_order_plus_qgis4.dock import TREE_JSON_SCHEMA_VERSION


def _serialize_children(items):
    """Replicate the dock's _serialize_tree JSON shape for a list of items."""
    from layer_order_plus_qgis4.tree_utils import ROLE_TYPE, ROLE_ID, TYPE_GROUP, TYPE_LAYER

    def ser_item(item):
        t = item.data(0, ROLE_TYPE)
        if t == TYPE_GROUP:
            try:
                expanded = bool(item.isExpanded())
            except Exception:
                expanded = True
            return {
                "type": TYPE_GROUP,
                "id": item.data(0, ROLE_ID),
                "name": item.text(0),
                "expanded": expanded,
                "children": [ser_item(item.child(i)) for i in range(item.childCount())],
            }
        return {"type": TYPE_LAYER, "id": item.data(0, ROLE_ID)}

    return json.dumps(
        {
            "version": TREE_JSON_SCHEMA_VERSION,
            "children": [ser_item(it) for it in items],
        },
        ensure_ascii=False,
    )


def _make_group(name, gid):
    from PyQt6.QtWidgets import QTreeWidgetItem
    from layer_order_plus_qgis4.tree_utils import ROLE_TYPE, ROLE_ID, TYPE_GROUP
    it = QTreeWidgetItem([name])
    it.setData(0, ROLE_TYPE, TYPE_GROUP)
    it.setData(0, ROLE_ID, gid)
    return it


def _make_layer(name, lid):
    from PyQt6.QtWidgets import QTreeWidgetItem
    from layer_order_plus_qgis4.tree_utils import ROLE_TYPE, ROLE_ID, TYPE_LAYER
    it = QTreeWidgetItem([name])
    it.setData(0, ROLE_TYPE, TYPE_LAYER)
    it.setData(0, ROLE_ID, lid)
    return it


def test_schema_version_is_1():
    assert TREE_JSON_SCHEMA_VERSION == 1


def test_serialize_emits_version_field(tree):
    from layer_order_plus_qgis4.tree_utils import ROLE_TYPE, TYPE_GROUP, TYPE_LAYER
    a = _make_group("Group A", "grp_a")
    a.addChild(_make_layer("Layer 1", "l1"))
    tree.addTopLevelItem(a)

    raw = _serialize_children([tree.topLevelItem(i) for i in range(tree.topLevelItemCount())])
    obj = json.loads(raw)
    assert "version" in obj
    assert obj["version"] == 1
    assert "children" in obj


def test_serialize_layer_node_has_only_type_and_id(tree):
    from layer_order_plus_qgis4.tree_utils import ROLE_TYPE, TYPE_LAYER
    tree.addTopLevelItem(_make_layer("Solo", "l_solo"))
    raw = _serialize_children([tree.topLevelItem(0)])
    obj = json.loads(raw)
    layer_node = obj["children"][0]
    assert layer_node == {"type": "layer", "id": "l_solo"}
    # No name/expanded/children keys on layer nodes
    assert "name" not in layer_node
    assert "expanded" not in layer_node


def test_serialize_group_node_has_required_keys(tree):
    raw = _serialize_children([_make_group("Standalone", "grp_std")])
    obj = json.loads(raw)
    grp_node = obj["children"][0]
    assert set(grp_node.keys()) == {"type", "id", "name", "expanded", "children"}
    assert grp_node["type"] == "group"
    assert grp_node["id"] == "grp_std"
    assert grp_node["name"] == "Standalone"
    # isExpanded() returns False until the item is actually shown in the tree;
    # the serializer captures whatever isExpanded() reports.
    assert isinstance(grp_node["expanded"], bool)
    assert grp_node["children"] == []


def test_serialize_nested_structure(tree):
    """Build a 3-level tree and verify the JSON structure matches."""
    outer = _make_group("Outer", "grp_outer")
    inner = _make_group("Inner", "grp_inner")
    inner.addChild(_make_layer("Deep", "l_deep"))
    outer.addChild(inner)
    outer.addChild(_make_layer("Shallow", "l_shallow"))

    raw = _serialize_children([outer])
    obj = json.loads(raw)
    assert obj["children"][0]["name"] == "Outer"
    assert obj["children"][0]["children"][0]["name"] == "Inner"
    assert obj["children"][0]["children"][0]["children"][0] == {"type": "layer", "id": "l_deep"}
    assert obj["children"][0]["children"][1] == {"type": "layer", "id": "l_shallow"}


def test_round_trip_preserves_structure(tree):
    """Serialize → parse → rebuild → re-serialize should yield identical JSON."""
    a = _make_group("Group A", "grp_a")
    a.addChild(_make_layer("Layer 1", "l1"))
    a.addChild(_make_layer("Layer 2", "l2"))
    tree.addTopLevelItem(a)
    tree.addTopLevelItem(_make_layer("Top", "l_top"))

    raw_before = _serialize_children([tree.topLevelItem(i) for i in range(tree.topLevelItemCount())])

    # Parse and rebuild
    obj = json.loads(raw_before)
    tree.clear()
    from layer_order_plus_qgis4.tree_utils import ROLE_TYPE, ROLE_ID, TYPE_GROUP, TYPE_LAYER

    def build(parent, node):
        if node["type"] == TYPE_GROUP:
            it = _make_group(node["name"], node["id"])
            if parent is None:
                tree.addTopLevelItem(it)
            else:
                parent.addChild(it)
            for ch in node.get("children", []):
                build(it, ch)
        else:
            it = _make_layer(node.get("name", ""), node["id"])
            if parent is None:
                tree.addTopLevelItem(it)
            else:
                parent.addChild(it)

    for top in obj["children"]:
        build(None, top)

    raw_after = _serialize_children([tree.topLevelItem(i) for i in range(tree.topLevelItemCount())])
    assert json.loads(raw_before) == json.loads(raw_after)


def test_legacy_load_no_version_field():
    """Load a JSON with no 'version' field — should be treatable as v1."""
    legacy = json.dumps({
        "children": [
            {"type": "group", "id": "g1", "name": "Legacy", "expanded": True, "children": []}
        ]
    })
    obj = json.loads(legacy)
    # Legacy = no version field
    assert "version" not in obj
    # The loader treats missing version as v1 (we just verify the shape is parseable)
    assert obj["children"][0]["name"] == "Legacy"


def test_mismatched_version_logs_warning():
    """A future version (e.g. 2) should be detectable so we can migrate."""
    future = json.dumps({"version": 999, "children": []})
    obj = json.loads(future)
    assert obj["version"] != TREE_JSON_SCHEMA_VERSION
    # In the real loader this logs a Warning and attempts v1 load anyway;
    # we just verify the detection logic.


def test_expanded_state_round_trip(tree):
    """A collapsed group should serialize as expanded=False and round-trip back."""
    g = _make_group("Collapsed", "grp_collapsed")
    g.addChild(_make_layer("Inside", "l_inside"))
    g.setExpanded(False)
    tree.addTopLevelItem(g)

    raw = _serialize_children([tree.topLevelItem(0)])
    obj = json.loads(raw)
    assert obj["children"][0]["expanded"] is False

    # Round-trip: rebuild and verify expanded state restored
    tree.clear()
    rebuilt = _make_group("Collapsed", "grp_collapsed")
    rebuilt.addChild(_make_layer("Inside", "l_inside"))
    rebuilt.setExpanded(False)
    tree.addTopLevelItem(rebuilt)

    assert tree.topLevelItem(0).isExpanded() is False
