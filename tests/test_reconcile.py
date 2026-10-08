"""Reconcile the Plus tree with a flat order coming from the stock Layer Order panel."""
import os
import sys
import types

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)
if "layer_order_plus_qgis4" not in sys.modules:
    pkg = types.ModuleType("layer_order_plus_qgis4")
    pkg.__path__ = [PLUGIN_ROOT]
    sys.modules["layer_order_plus_qgis4"] = pkg

from layer_order_plus_qgis4.model import GroupNode, LayerNode
from layer_order_plus_qgis4.reconcile import reconcile_tree


def _build(spec):
    out = []
    for item in spec:
        if isinstance(item, str):
            out.append(LayerNode(id=item, name=item))
        else:
            gid, children = item
            out.append(GroupNode(id=gid, name=gid, children=_build(children)))
    return out


def _shape(nodes):
    return [n.id if isinstance(n, LayerNode) else (n.id, _shape(n.children)) for n in nodes]


def _stock_move(tree_spec, order, hint=()):
    out = reconcile_tree(_build(tree_spec), list(order), hint)
    return _shape(out) if out is not None else None


ABCD = [("ABCD", [("AB", ["A", "B"]), ("CD", ["C", "D"])]), "E"]


def test_between_two_members_joins_group():
    assert _stock_move(ABCD, "ABCED") == [("ABCD", [("AB", ["A", "B"]), ("CD", ["C", "E", "D"])])]


def test_next_to_group_from_outside_stays_sibling():
    # E dropped right after D: adjacent to CD and ABCD, but E was top-level
    assert _stock_move([("CD", ["C", "D"]), "A", "E"], "CDEA") == [("CD", ["C", "D"]), "E", "A"]


def test_reorder_inside_group_keeps_membership_at_edges():
    # (C, (A, B)) → C, B, A  stays (C, (B, A)) whichever layer is seen as moved
    tree = ["C", ("g", ["A", "B"])]
    assert _stock_move(tree, "CBA") == ["C", ("g", ["B", "A"])]
    assert _stock_move(tree, "CBA", hint=["A"]) == ["C", ("g", ["B", "A"])]
    assert _stock_move(tree, "CBA", hint=["B"]) == ["C", ("g", ["B", "A"])]


def test_leaving_a_group_past_a_sibling():
    tree = [("CD", ["C", "E", "D"]), "X"]
    assert _stock_move(tree, "CDXE", hint=["E"]) == [("CD", ["C", "D"]), "X", "E"]


def test_between_members_of_other_group():
    tree = [("AB", ["A", "B"]), ("CD", ["C", "E", "D"])]
    assert _stock_move(tree, "AEBCD", hint=["E"]) == [("AB", ["A", "E", "B"]), ("CD", ["C", "D"])]


def test_nested_groups_survive():
    tree = [("ABCD", [("AB", ["A", "B"]), ("CD", ["C", "D"])]), "E"]
    assert _stock_move(tree, "EABCD", hint=["E"]) == ["E", ("ABCD", [("AB", ["A", "B"]), ("CD", ["C", "D"])])]


def test_empty_group_kept():
    tree = [("empty", []), "A", "B"]
    assert _stock_move(tree, "BA") == [("empty", []), "B", "A"]


def test_duplicate_or_mismatched_order_rejected():
    assert _stock_move(ABCD, "ABECED") is None   # stock panel's intermediate state
    assert _stock_move(ABCD, "ABCD") is None


def test_user_scenario_never_loses_groups():
    """Sequence from the 1.2.24 bug report, driven through stock-panel moves."""
    tree = _build(ABCD)
    steps = [
        ("ABCED", "E"),   # E between C and D
        ("ABCDE", "E"),   # after D
        ("ABECD", "E"),   # before C
        ("AEBCD", "E"),   # between A and B
        ("ABCDE", "E"),   # back to the end
    ]
    for order, moved in steps:
        tree = reconcile_tree(tree, list(order), [moved])
        assert tree is not None
        flat = []
        def walk(ns, groups):
            for n in ns:
                if isinstance(n, GroupNode):
                    groups.add(n.id)
                    walk(n.children, groups)
                else:
                    flat.append(n.id)
        groups = set()
        walk(tree, groups)
        assert "".join(flat) == order
        assert groups == {"ABCD", "AB", "CD"}
    # E ended up inside AB in step 4; moving it to the very end takes it
    # past CD, so it leaves every group
    assert _shape(tree) == [("ABCD", [("AB", ["A", "B"]), ("CD", ["C", "D"])]), "E"]
