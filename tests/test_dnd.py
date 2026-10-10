"""View ↔ ViewController ↔ Model, end to end (no QGIS).

Intents are injected the way the View emits them; we then check the Model,
and that the View is an exact projection of the Model. Tests at the bottom
check that the View never acts on its own input.
"""
import types

import pytest
from qgis.PyQt.QtCore import QMimeData, QModelIndex, QPoint, Qt
from qgis.PyQt.QtTest import QTest

from advanced_layer_order.model import GroupNode, LayerNode, LayerOrderModel
from advanced_layer_order.tree_model import (
    DROP_ABOVE,
    DROP_BELOW,
    DROP_END,
    DROP_ON,
    MIME_IDS,
    ROLE_ID,
    ROLE_TYPE,
)

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


def _view_shape(view):
    m = view.item_model

    def walk(parent):
        out = []
        for row in range(m.rowCount(parent)):
            ix = m.index(row, 0, parent)
            out.append((ix.data(ROLE_ID), walk(ix)) if ix.data(ROLE_TYPE) == "group" else ix.data(ROLE_ID))
        return out
    return walk(QModelIndex())


def _check(view, item_id):
    return view.item_model.index_of(item_id).data(Qt.ItemDataRole.CheckStateRole)


def _expanded(view, group_id):
    return view.tree.isExpanded(view.item_model.index_of(group_id))


@pytest.fixture
def mvc(qapp):
    from advanced_layer_order.view import LayerOrderView
    from advanced_layer_order.view_controller import ViewController

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
        assert _view_shape(view) == shape, "View diverged from Model"
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
    assert _view_shape(mvc.view) == ["A", "B", "C", "D"]
    mvc.vc.undo_stack.redo()
    assert _view_shape(mvc.view) == _model_shape(mvc.model.get_root())


def test_drop_reselects_moved_items(mvc):
    mvc.load(["A", "B", "C"])
    mvc.drop(["A"], "C", DROP_BELOW)
    assert mvc.view.selected_ids() == ["A"]


# ---------- move up / down ----------

def test_move_buttons_are_one_undo_step_and_keep_selection(mvc):
    mvc.load([("g1", ["A", "B", "C"]), "D"])
    mvc.view.render_selection(["C", "B"])
    mvc.view.btn_move_up.click()
    assert _model_shape(mvc.model.get_root()) == [("g1", ["B", "C", "A"]), "D"]
    assert _view_shape(mvc.view) == _model_shape(mvc.model.get_root())
    assert mvc.view.selected_ids() == ["B", "C"]
    mvc.view.btn_move_up.click()                   # blocked at the top of g1
    assert mvc.vc.undo_stack.count() == 1
    mvc.view.btn_move_down.click()
    mvc.vc.undo_stack.undo()
    mvc.vc.undo_stack.undo()
    assert _model_shape(mvc.model.get_root()) == [("g1", ["A", "B", "C"]), "D"]


def test_move_shortcut_is_an_intent(mvc):
    mvc.load(["A", "B"])
    mvc.view.render_selection(["B"])
    seen = []
    mvc.view.move_by_one_requested.connect(lambda ids, up: seen.append((ids, up)))
    QTest.keyClick(mvc.view.tree, Qt.Key.Key_Up, Qt.KeyboardModifier.ControlModifier)
    assert seen == [(["B"], True)]
    assert _model_shape(mvc.model.get_root()) == ["B", "A"]
    keypad = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.KeypadModifier
    QTest.keyClick(mvc.view.tree, Qt.Key.Key_Down, keypad)          # how macOS sends it
    assert seen[-1] == (["B"], False)


def test_move_buttons_follow_selection_and_control(mvc):
    mvc.load(["A", "B"])
    assert not mvc.view.btn_move_up.isEnabled()
    mvc.view.render_selection(["A"])
    assert mvc.view.btn_move_up.isEnabled() and mvc.view.btn_move_down.isEnabled()
    mvc.model.set_control_enabled(False)
    assert not mvc.view.btn_move_up.isEnabled()


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
    assert _check(mvc.view, "A") == Qt.CheckState.Unchecked


# ---------- intents that are not Model edits ----------

def test_check_intent_requests_visibility_for_group_layers(mvc):
    mvc.load([("g1", ["A", ("g2", ["B"])]), "C"])
    mvc.view.check_requested.emit("g1", False)
    assert mvc.requests == [("visibility", ["A", "B"], False)]
    # Nothing changes until QGIS answers through the Model
    assert _check(mvc.view, "A") == Qt.CheckState.Checked


def test_visibility_rendered_with_group_state(mvc):
    mvc.load([("g1", ["A", "B"])])
    mvc.model.set_visibility("A", False)
    assert _check(mvc.view, "g1") == Qt.CheckState.PartiallyChecked
    mvc.model.set_visibility("B", False)
    assert _check(mvc.view, "g1") == Qt.CheckState.Unchecked


def test_expand_intent_goes_through_model(mvc):
    mvc.load([("g1", ["A"]), "B", "C"])
    mvc.view.tree.expand_intent.emit("g1", False)
    assert mvc.model.find_item("g1").expanded is False
    assert not _expanded(mvc.view, "g1")
    mvc.drop(["C"], "B", DROP_ABOVE)           # re-render keeps it collapsed
    assert not _expanded(mvc.view, "g1")


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
    from qgis.PyQt.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem
    mvc.load(["A", "B"])
    _show(mvc)
    tree, index = mvc.view.tree, mvc.view.item_model.index_of("A")
    seen = []
    mvc.view.item_model.check_intent.connect(lambda i, on: seen.append((i, on)))
    if hasattr(tree, "initViewItemOption"):            # Qt 6
        opt = QStyleOptionViewItem()
        tree.initViewItemOption(opt)
    else:                                               # Qt 5
        opt = tree.viewOptions()
    QStyledItemDelegate().initStyleOption(opt, index)   # Python-created: protected API allowed
    opt.rect = tree.visualRect(index)
    box = tree.style().subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, tree)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=box.center())
    assert seen == [("A", False)]
    assert _check(mvc.view, "A") == Qt.CheckState.Checked


def test_branch_click_does_not_expand(mvc):
    mvc.load([("g1", ["A"])])
    mvc.model.set_expanded("g1", False)
    _show(mvc)
    tree = mvc.view.tree
    seen = []
    tree.expand_intent.disconnect()            # observe the intent only, nobody acts on it
    tree.expand_intent.connect(lambda g, on: seen.append((g, on)))
    rect = tree.visualRect(mvc.view.item_model.index_of("g1"))
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton,
                     pos=QPoint(rect.left() - tree.indentation() // 2, rect.center().y()))
    assert seen == [("g1", True)]
    assert not _expanded(mvc.view, "g1")


def test_selected_ids_display_order_and_no_nested(mvc):
    mvc.load([("g1", ["A", "B"]), "C"])
    mvc.view.render_selection(["C", "A", "g1"])
    assert mvc.view.selected_ids() == ["g1", "C"]


# ---------- item model: Qt drop → intent ----------

def _mime(ids):
    import json
    m = QMimeData()
    m.setData(MIME_IDS, json.dumps(ids).encode())
    return m


@pytest.fixture
def item_model(mvc):
    mvc.load(["X", ("g1", ["A", "B", "C"]), "Y"])
    seen = []
    mvc.view.drop_requested.disconnect()       # observe only
    mvc.view.item_model.drop_intent.connect(lambda *a: seen.append(a))
    return mvc.view.item_model, seen


@pytest.mark.parametrize("row, parent, moving, expected", [
    (-1, "g1", ["Y"], ("g1", DROP_ON)),          # onto a group
    (-1, "A", ["Y"], ("A", DROP_ON)),            # onto a layer
    (0, "g1", ["Y"], ("A", DROP_ABOVE)),         # first row of a group
    (3, "g1", ["Y"], ("C", DROP_BELOW)),         # after the last child
    (1, "g1", ["B"], ("C", DROP_ABOVE)),         # next to itself → next non-mover
    (-1, None, ["A"], ("", DROP_END)),           # empty area
    (3, None, ["A"], ("Y", DROP_BELOW)),         # after the last top-level row
])
def test_drop_translation(item_model, row, parent, moving, expected):
    m, seen = item_model
    parent_ix = m.index_of(parent) if parent else QModelIndex()
    assert m.dropMimeData(_mime(moving), Qt.DropAction.MoveAction, row, 0, parent_ix) is False
    assert seen == [(moving, *expected)]


def test_drop_into_own_subtree_refused(item_model):
    m, seen = item_model
    assert not m.canDropMimeData(_mime(["g1"]), Qt.DropAction.MoveAction, 0, 0, m.index_of("A"))
    assert not m.dropMimeData(_mime(["g1"]), Qt.DropAction.MoveAction, -1, 0, m.index_of("g1"))
    assert seen == []


def test_render_keeps_selection_and_expansion(mvc):
    mvc.load(["A", ("g1", ["B"]), "C"])
    mvc.view.render_selection(["B"])
    mvc.drop(["C"], "A", DROP_ABOVE)           # same items, new arrangement
    assert mvc.view.selected_ids() == ["C"]    # the ViewController selects moved items
    mvc.view.render_selection(["B"])
    mvc.model.rename_layer("A", "renamed")     # display-only change
    assert mvc.view.selected_ids() == ["B"]
    assert mvc.view.item_model.index_of("A").data() == "renamed"


# ---------- undo keys while QGIS's own Undo holds Ctrl+Z ----------

def test_undo_keys_in_panel_beat_an_enabled_app_undo_shortcut(mvc):
    """A layer in edit mode enables QGIS's Undo (Ctrl+Z, window-wide). With
    the focus in the panel, Ctrl+Z / Ctrl+Y must act on the layer order."""
    from qgis.PyQt.QtGui import QKeySequence
    from qgis.PyQt.QtWidgets import QMainWindow
    try:
        from qgis.PyQt.QtGui import QAction  # Qt 6
    except ImportError:
        from qgis.PyQt.QtWidgets import QAction  # Qt 5
    window = QMainWindow()
    window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, mvc.view)
    qgis_undo = QAction("Undo", window)
    qgis_undo.setShortcut(QKeySequence("Ctrl+Z"))
    fired = []
    qgis_undo.triggered.connect(lambda: fired.append("qgis undo"))
    window.addAction(qgis_undo)
    window.show()
    QTest.qWaitForWindowExposed(window)

    mvc.load(["A", "B", "C"])
    mvc.drop(["A"], "C", DROP_BELOW)                        # B C A
    mvc.view.tree.setFocus()
    QTest.keyClick(mvc.view.tree, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert _model_shape(mvc.model.get_root()) == ["A", "B", "C"]
    assert fired == []
    QTest.keyClick(mvc.view.tree, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert _model_shape(mvc.model.get_root()) == ["B", "C", "A"]
    window.removeDockWidget(mvc.view)
    window.deleteLater()
