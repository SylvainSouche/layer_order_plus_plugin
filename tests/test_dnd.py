"""Drag-and-drop through the full View ↔ ViewController ↔ Model chain.

Drops are injected at the drop_intent level (what BetterLayerTree emits),
then we check that the Model has the expected structure AND that the View
is an exact projection of the Model afterwards.
"""
import os
import sys
import types

import pytest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)
if "layer_order_plus_qgis4" not in sys.modules:
    pkg = types.ModuleType("layer_order_plus_qgis4")
    pkg.__path__ = [PLUGIN_ROOT]
    sys.modules["layer_order_plus_qgis4"] = pkg

from PyQt6.QtCore import Qt

from layer_order_plus_qgis4.model import LayerOrderModel, GroupNode, LayerNode
from layer_order_plus_qgis4.tree_utils import ROLE_ID, ROLE_TYPE, TYPE_GROUP, find_group_item, find_layer_item
from layer_order_plus_qgis4.tree_widget import DROP_ON, DROP_ABOVE, DROP_BELOW, DROP_END


# ---------- helpers ----------

def _build(spec):
    out = []
    for item in spec:
        if isinstance(item, str):
            out.append(LayerNode(id=item, name=item))
        else:
            gid, children = item
            out.append(GroupNode(id=gid, name=gid, children=_build(children)))
    return out


def _model_shape(nodes):
    """['A', ('g1', ['B', 'C'])] — same notation as _build's spec."""
    return [n.id if isinstance(n, LayerNode) else (n.id, _model_shape(n.children))
            for n in nodes]


def _view_shape(tree):
    def walk(it):
        if it.data(0, ROLE_TYPE) == TYPE_GROUP:
            return (it.data(0, ROLE_ID), [walk(it.child(i)) for i in range(it.childCount())])
        return it.data(0, ROLE_ID)
    return [walk(tree.topLevelItem(i)) for i in range(tree.topLevelItemCount())]


@pytest.fixture
def mvc(qapp):
    from layer_order_plus_qgis4.view import LayerOrderView
    from layer_order_plus_qgis4.view_controller import ViewController

    view = LayerOrderView()
    view.set_control_enabled(True)
    model = LayerOrderModel()
    vc = ViewController(model, view)

    def load(spec):
        with model.block_notifications():
            model._root = _build(spec)
        vc._rebuild_view_from_model()

    def drop(moving, target, pos):
        vc._handle_drop_intent(list(moving), target, pos)
        shape = _model_shape(model.get_root())
        assert _view_shape(view.tree) == shape, "View diverged from Model"
        return shape

    yield types.SimpleNamespace(view=view, model=model, vc=vc, load=load, drop=drop)
    view.deleteLater()


# ---------- Model.move_items ----------

def test_move_items_anchor_survives_removal():
    m = LayerOrderModel()
    m._root = _build(["A", "B", "C", "D", "E"])
    assert m.move_items(["A", "B"], None, "E")
    assert _model_shape(m.get_root()) == ["C", "D", "A", "B", "E"]


def test_move_items_rejects_cycle():
    m = LayerOrderModel()
    m._root = _build([("g1", [("g2", ["A"])]), "B"])
    assert not m.move_items(["g1"], "g2", None)
    assert _model_shape(m.get_root()) == [("g1", [("g2", ["A"])]), "B"]


def test_move_items_skips_descendants_of_movers():
    m = LayerOrderModel()
    m._root = _build([("g1", ["A", "B"]), "C"])
    assert m.move_items(["g1", "A"], None, None)
    assert _model_shape(m.get_root()) == ["C", ("g1", ["A", "B"])]


def test_move_items_noop_emits_nothing():
    m = LayerOrderModel()
    m._root = _build(["A", "B", "C"])
    events = []
    m.add_listener(lambda t, p: events.append(t))
    assert not m.move_items(["A", "B"], None, "C")
    assert events == []


# ---------- drops through the ViewController ----------

def test_drop_two_items_above(mvc):
    mvc.load(["A", "B", "C", "D", "E"])
    assert mvc.drop(["A", "B"], "E", DROP_ABOVE) == ["C", "D", "A", "B", "E"]


def test_drop_two_items_below(mvc):
    mvc.load(["A", "B", "C", "D", "E"])
    # Used to give C, A, D, B, E
    assert mvc.drop(["A", "B"], "C", DROP_BELOW) == ["C", "A", "B", "D", "E"]


def test_drop_uses_display_order_not_click_order(mvc):
    mvc.load(["A", "B", "C", "D"])
    assert mvc.drop(["B", "A"], "D", DROP_BELOW) == ["C", "D", "A", "B"]


def test_drop_upwards_below(mvc):
    mvc.load(["A", "B", "C", "D", "E"])
    assert mvc.drop(["D", "E"], "A", DROP_BELOW) == ["A", "D", "E", "B", "C"]


def test_drop_below_skips_sibling_that_is_moving(mvc):
    mvc.load(["A", "B", "C", "D"])
    assert mvc.drop(["B", "D"], "A", DROP_BELOW) == ["A", "B", "D", "C"]


def test_drop_on_group_appends(mvc):
    mvc.load([("g1", ["A", "B"]), "C", "D"])
    assert mvc.drop(["C", "D"], "g1", DROP_ON) == [("g1", ["A", "B", "C", "D"])]


def test_drop_on_group_from_inside_same_group(mvc):
    mvc.load([("g1", ["A", "B", "C"])])
    assert mvc.drop(["A"], "g1", DROP_ON) == [("g1", ["B", "C", "A"])]


def test_drop_out_of_group_above_group(mvc):
    mvc.load(["X", ("g1", ["A", "B", "C"])])
    assert mvc.drop(["B"], "g1", DROP_ABOVE) == ["X", "B", ("g1", ["A", "C"])]


def test_drop_into_nested_group_position(mvc):
    mvc.load([("g1", ["A", ("g2", ["B", "C"])]), "D"])
    assert mvc.drop(["D"], "B", DROP_BELOW) == [("g1", ["A", ("g2", ["B", "D", "C"])])]


def test_drop_on_layer_creates_group(mvc):
    mvc.load(["A", "B", "C", "D"])
    shape = mvc.drop(["A", "D"], "C", DROP_ON)
    assert shape[0] == "B"
    gid, children = shape[1]
    assert children == ["C", "A", "D"]
    assert mvc.model.find_item(gid).expanded


def test_drop_in_empty_area_goes_to_bottom(mvc):
    mvc.load([("g1", ["A", "B"]), "C"])
    assert mvc.drop(["A"], "", DROP_END) == [("g1", ["B"]), "C", "A"]


def test_drop_into_own_descendant_is_rejected(mvc):
    mvc.load([("g1", [("g2", ["A"])]), "B"])
    before = _model_shape(mvc.model.get_root())
    assert mvc.drop(["g1"], "A", DROP_ON) == before
    assert not mvc.vc.undo_stack.canUndo()


def test_drop_is_one_undo_step(mvc):
    mvc.load(["A", "B", "C", "D"])
    mvc.drop(["A", "D"], "C", DROP_ON)
    assert mvc.vc.undo_stack.count() == 1
    mvc.vc.undo_stack.undo()
    assert _model_shape(mvc.model.get_root()) == ["A", "B", "C", "D"]
    assert _view_shape(mvc.view.tree) == ["A", "B", "C", "D"]


def test_drop_keeps_collapsed_groups_collapsed(mvc):
    mvc.load([("g1", ["A"]), "B", "C"])
    find_group_item(mvc.view.tree, "g1").setExpanded(False)  # user clicks the arrow
    assert mvc.model.find_item("g1").expanded is False
    mvc.drop(["C"], "B", DROP_ABOVE)
    assert find_group_item(mvc.view.tree, "g1").isExpanded() is False


def test_drop_reselects_moved_items(mvc):
    mvc.load(["A", "B", "C"])
    mvc.drop(["A"], "C", DROP_BELOW)
    assert mvc.view.get_selected_item_ids() == ["A"]


# ---------- visibility echo from tri-state groups ----------

def test_unchecking_one_child_does_not_recheck_it(mvc):
    mvc.load([("g1", ["A", "B"])])
    find_layer_item(mvc.view.tree, "A").setCheckState(0, Qt.CheckState.Unchecked)
    assert mvc.model.find_item("A").visible is False
    assert mvc.model.find_item("B").visible is True
    assert find_layer_item(mvc.view.tree, "A").checkState(0) == Qt.CheckState.Unchecked


# ---------- BetterLayerTree: moving set ----------

def test_moving_items_display_order_and_no_nested(mvc):
    mvc.load([("g1", ["A", "B"]), "C"])
    tree = mvc.view.tree
    for it in (find_layer_item(tree, "C"), find_layer_item(tree, "A"), find_group_item(tree, "g1")):
        it.setSelected(True)
    assert [it.data(0, ROLE_ID) for it in tree._moving_items()] == ["g1", "C"]
