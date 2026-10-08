"""View ↔ ViewController ↔ Model, end to end (no QGIS).

Intents are injected the way the View emits them; we then check the Model,
and that the View is an exact projection of the Model. Tests at the bottom
check that the View never acts on its own input.
"""
import types

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest

from layer_order_plus_qgis4.model import GroupNode, LayerNode, LayerOrderModel
from layer_order_plus_qgis4.tree_utils import ROLE_ID, ROLE_TYPE, TYPE_GROUP, find_group_item, find_layer_item
from layer_order_plus_qgis4.tree_widget import DROP_ABOVE, DROP_BELOW, DROP_END, DROP_ON

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
    model = LayerOrderModel()
    model.set_control_enabled(True)
    vc = ViewController(model, view)
    requests = []
    vc.visibility_requested.connect(lambda ids, on: requests.append(("visibility", ids, on)))
    vc.control_requested.connect(lambda on: requests.append(("control", on)))

    def load(spec):
        model.replace_root(_build(spec))

    def drop(moving, target, pos):
        vc.handle_drop(list(moving), target, pos)
        shape = _model_shape(model.get_root())
        assert _view_shape(view.tree) == shape, "View diverged from Model"
        return shape

    yield types.SimpleNamespace(view=view, model=model, vc=vc, load=load, drop=drop,
                                requests=requests)
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


# ---------- drops ----------

def test_drop_two_items_above(mvc):
    mvc.load(["A", "B", "C", "D", "E"])
    assert mvc.drop(["A", "B"], "E", DROP_ABOVE) == ["C", "D", "A", "B", "E"]


def test_drop_two_items_below(mvc):
    mvc.load(["A", "B", "C", "D", "E"])
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
    mvc.vc.undo_stack.redo()
    assert _view_shape(mvc.view.tree) == _model_shape(mvc.model.get_root())


def test_drop_reselects_moved_items(mvc):
    mvc.load(["A", "B", "C"])
    mvc.drop(["A"], "C", DROP_BELOW)
    assert mvc.view.selected_ids() == ["A"]


# ---------- undo never fights QGIS ----------

def test_undo_keeps_layers_added_since(mvc):
    mvc.load(["A", "B", "C"])
    mvc.drop(["A"], "C", DROP_BELOW)          # B C A
    mvc.model.add_layer_beside("N", "N", True, "C", after=True)   # QGIS adds N
    mvc.vc.undo_stack.undo()
    assert _model_shape(mvc.model.get_root()) == ["A", "B", "C", "N"]


def test_undo_does_not_resurrect_removed_layers(mvc):
    mvc.load(["A", "B", "C"])
    mvc.drop(["A"], "C", DROP_BELOW)
    mvc.model.remove_layer("B")                # QGIS removes B
    mvc.vc.undo_stack.undo()
    assert _model_shape(mvc.model.get_root()) == ["A", "C"]


def test_undo_keeps_current_visibility(mvc):
    mvc.load(["A", "B"])
    mvc.drop(["A"], "B", DROP_BELOW)
    mvc.model.set_visibility("A", False)       # QGIS hides A
    mvc.vc.undo_stack.undo()
    assert mvc.model.find_item("A").visible is False
    assert find_layer_item(mvc.view.tree, "A").checkState(0) == Qt.CheckState.Unchecked


# ---------- intents that are not Model edits ----------

def test_check_intent_requests_visibility_for_group_layers(mvc):
    mvc.load([("g1", ["A", ("g2", ["B"])]), "C"])
    mvc.view.check_requested.emit("g1", False)
    assert mvc.requests == [("visibility", ["A", "B"], False)]
    # Nothing changes until QGIS answers through the Model
    assert find_layer_item(mvc.view.tree, "A").checkState(0) == Qt.CheckState.Checked


def test_visibility_rendered_with_group_state(mvc):
    mvc.load([("g1", ["A", "B"])])
    mvc.model.set_visibility("A", False)
    assert find_group_item(mvc.view.tree, "g1").checkState(0) == Qt.CheckState.PartiallyChecked
    mvc.model.set_visibility("B", False)
    assert find_group_item(mvc.view.tree, "g1").checkState(0) == Qt.CheckState.Unchecked


def test_expand_intent_goes_through_model(mvc):
    mvc.load([("g1", ["A"]), "B", "C"])
    mvc.view.tree.expand_intent.emit("g1", False)
    assert mvc.model.find_item("g1").expanded is False
    assert find_group_item(mvc.view.tree, "g1").isExpanded() is False
    mvc.drop(["C"], "B", DROP_ABOVE)           # re-render keeps it collapsed
    assert find_group_item(mvc.view.tree, "g1").isExpanded() is False


def test_control_click_only_requests(mvc):
    box = mvc.view.chk_control
    assert box.isChecked()
    box.click()
    assert mvc.requests == [("control", False)]
    assert box.isChecked(), "the box must keep showing the real state"
    mvc.model.set_control_enabled(False)       # QGIS answered
    assert not box.isChecked()


def test_remove_empty_click_updates_model_setting(mvc):
    mvc.view.chk_remove_empty.click()
    assert mvc.model.get_remove_empty_groups() is False
    assert not mvc.view.chk_remove_empty.isChecked()


# ---------- the tree never acts on its own ----------

def _show(mvc):
    mvc.view.resize(300, 400)
    mvc.view.show()
    QTest.qWaitForWindowExposed(mvc.view)


def test_checkbox_click_does_not_toggle_item(mvc):
    mvc.load(["A", "B"])
    _show(mvc)
    tree = mvc.view.tree
    item = find_layer_item(tree, "A")
    seen = []
    tree.check_intent.connect(lambda i, on: seen.append((i, on)))
    from PyQt6.QtWidgets import QStyle, QStyleOptionViewItem
    opt = QStyleOptionViewItem()
    index = tree.indexFromItem(item)
    tree.itemDelegate().initStyleOption(opt, index)
    opt.rect = tree.visualRect(index)
    box = tree.style().subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, tree)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=box.center())
    assert seen == [("A", False)]
    assert item.checkState(0) == Qt.CheckState.Checked


def test_branch_click_does_not_expand(mvc):
    mvc.load([("g1", ["A"])])
    mvc.model.set_expanded("g1", False)
    _show(mvc)
    tree = mvc.view.tree
    seen = []
    tree.expand_intent.disconnect()            # observe the intent only, nobody acts on it
    tree.expand_intent.connect(lambda g, on: seen.append((g, on)))
    item = find_group_item(tree, "g1")
    rect = tree.visualItemRect(item)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton,
                     pos=QPoint(rect.left() - tree.indentation() // 2, rect.center().y()))
    assert seen == [("g1", True)]
    assert item.isExpanded() is False


def test_moving_items_display_order_and_no_nested(mvc):
    mvc.load([("g1", ["A", "B"]), "C"])
    tree = mvc.view.tree
    for it in (find_layer_item(tree, "C"), find_layer_item(tree, "A"), find_group_item(tree, "g1")):
        it.setSelected(True)
    assert mvc.view.selected_ids() == ["g1", "C"]
