"""Replay docs/TEST_SCENARIO.md on the real test project, headless.

The manual plan states, for each step, what ALO, QGIS's Layer Order panel
and the map must show. This check performs the same steps through the same
entry points the UI uses (View intents, native-panel row moves, layer-tree
API) and asserts those expectations — so the documented results are
verified, and only what a human must see (real mouse, real rendering on
screen, restarts) is left to the manual run.
"""
import os
import shutil
import sys
import tempfile

from harness import FakeIface, check, finish, pump
from qgis.core import QgsMapRendererParallelJob, QgsMapSettings, QgsProject, QgsRectangle, QgsVectorLayer
from qgis.gui import QgsCustomLayerOrderWidget, QgsLayerTreeMapCanvasBridge, QgsMapCanvas
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QListView

from advanced_layer_order.controller import LayerOrderController
from advanced_layer_order.model import GroupNode, LayerNode, LayerOrderModel
from advanced_layer_order.reconcile import (
    reconcile_tree,  # noqa: F401  (native drags go through the Controller)
)
from advanced_layer_order.tree_model import DROP_ABOVE, DROP_BELOW, DROP_END, DROP_ON
from advanced_layer_order.view import LayerOrderView
from advanced_layer_order.view_controller import ViewController

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
sys.path.insert(0, DATA)
import make_test_project as spec  # noqa: E402

WORK = tempfile.mkdtemp()
for f in ("test.qgz", "test_layers.gpkg"):
    shutil.copy(os.path.join(DATA, f), WORK)       # never touch the committed data
PROJECT = os.path.join(WORK, "test.qgz")
COLOURS = {name: colour for name, (colour, _a) in {**spec.LAYERS, **spec.EXTRA}.items()}
NAME_OF = {v: k for k, v in COLOURS.items()}

proj = QgsProject.instance()
root = proj.layerTreeRoot()
canvas = QgsMapCanvas()
bridge = QgsLayerTreeMapCanvasBridge(root, canvas)
native = QgsCustomLayerOrderWidget(bridge)
native.show()
native_list = native.findChild(QListView).model()

model = LayerOrderModel()
view = LayerOrderView()
vc = ViewController(model, view)
ctl = LayerOrderController(model, FakeIface())
ctl.project_loaded.connect(vc.reset_history)
ctl.external_edit.connect(vc.record_step)
vc.visibility_requested.connect(ctl.set_layers_visible)
vc.control_requested.connect(ctl.set_control_enabled)


def lid(name):
    return next(lyr.id() for lyr in proj.mapLayers().values() if lyr.name() == name)


def gid(name):
    return next(n.id for n, _ in model.walk() if isinstance(n, GroupNode) and n.name == name)


def name_of(layer_id):
    lyr = proj.mapLayer(layer_id)
    return lyr.name() if lyr else layer_id


def alo():
    def fmt(nodes):
        return ", ".join(name_of(n.id) if isinstance(n, LayerNode) else f"{n.name}[{fmt(n.children)}]"
                         for n in nodes)
    return fmt(model.get_root())


def native_rows():
    return " ".join(str(native_list.data(native_list.index(i, 0))) for i in range(native_list.rowCount()))


def centre():
    visible = [lyr for lyr in root.layerOrder() if root.findLayer(lyr.id()).isVisible()]
    settings = QgsMapSettings()
    settings.setLayers(visible)
    settings.setDestinationCrs(proj.crs())
    settings.setExtent(QgsRectangle(-8500, -8000, 16500, 8000))
    settings.setOutputSize(QSize(500, 320))
    settings.setBackgroundColor(QColor("white"))
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    p = settings.mapToPixel().transform(0, 0)
    return NAME_OF.get(QColor(job.renderedImage().pixel(int(p.x()), int(p.y()))).name(), "white")


def expect(step, alo_tree=None, native=None, top=None):
    pump(80)
    if alo_tree is not None:
        check(alo() == alo_tree, f"{step} ALO: {alo()!r} != {alo_tree!r}")
    if native is not None:
        check(native_rows() == native, f"{step} Native: {native_rows()!r} != {native!r}")
    if top is not None:
        check(centre() == top, f"{step} map centre: {centre()} != {top}")


def open_project():
    proj.read(PROJECT)
    ctl.load_project()
    pump()


def baseline():
    open_project()
    if not model.get_control_enabled():
        view.chk_control.click()
    for name, ids in (("AB", ["A", "B"]), ("CD", ["C", "D"])):
        view.ask_text = lambda *a, n=name: n
        view.create_group_requested.emit([lid(x) for x in ids])
    view.ask_text = lambda *a: "ABCD"
    view.create_group_requested.emit([gid("AB"), gid("CD")])
    pump()


def drop(moving, target, position):
    vc.handle_drop([gid(m) if m in ("AB", "CD", "ABCD") else lid(m) for m in moving],
                   (gid(target) if target in ("AB", "CD", "ABCD") else lid(target)) if target else "",
                   position)


def native_drag(layer, new_order):
    """QGIS's Layer Order panel: insert at the new row, then remove the old."""
    cur = [lyr.id() for lyr in root.customLayerOrder()]
    target = [lid(c) for c in new_order.split()]
    moved = lid(layer)
    dup = list(cur)
    dup.insert(target.index(moved) + (1 if cur.index(moved) < target.index(moved) else 0), moved)
    root.setCustomLayerOrder([proj.mapLayer(i) for i in dup])
    pump(30)
    root.setCustomLayerOrder([proj.mapLayer(i) for i in target])


BASE = "ABCD[AB[A, B], CD[C, D]], E"

# 1. Opening the project and control
open_project()
expect("1.1", "A, B, C, D, E", "A B C D E", "A")
check(not model.get_control_enabled(), "1.1 control off")
view.chk_control.click()
expect("1.2", native="A B C D E", top="A")
check(root.hasCustomLayerOrder(), "1.2 control on")
view.chk_control.click()
expect("1.3", native="A B C D E", top="A")
root.setHasCustomLayerOrder(True)                     # 1.4: native checkbox
expect("1.4", native="A B C D E", top="A")
check(model.get_control_enabled(), "1.4 ALO follows")
e_node = root.findLayer(lid("E"))                     # 1.5: Layers panel, E to the top
root.insertChildNode(0, e_node.clone())
root.removeChildNode(e_node)
expect("1.5", "A, B, C, D, E", "A B C D E", "A")
view.chk_control.click()                              # 1.6
expect("1.6", native="E A B C D", top="E")

# 2. Groups
baseline()
expect("2.3", BASE, "A B C D E", "A")

# 3. Drag and drop (each from the baseline)
for step, args, tree, order, top in [
    ("3.1", (["E"], "A", DROP_ABOVE), "ABCD[AB[E, A, B], CD[C, D]]", "E A B C D", "E"),
    ("3.2", (["E"], "D", DROP_BELOW), "ABCD[AB[A, B], CD[C, D, E]]", "A B C D E", "A"),
    ("3.3", (["E"], "AB", DROP_ON), "ABCD[AB[A, B, E], CD[C, D]]", "A B E C D", "A"),
    ("3.4", (["C"], "", DROP_END), "ABCD[AB[A, B], CD[D]], E, C", "A B D E C", "A"),
    ("3.5", (["E"], "C", DROP_ON), "ABCD[AB[A, B], CD[New group[C, E], D]]", "A B C E D", "A"),
    ("3.7", (["D", "C"], "A", DROP_ABOVE), "ABCD[AB[C, D, A, B], CD[]], E", "C D A B E", "C"),
]:
    baseline()
    drop(*args)
    expect(step, tree, order, top)

# 4. Move up / down (cumulative)
# Regression: opening a project must not inherit the previous project's
# groups (its layers' removal used to be saved into the new project)
open_project()
check(alo() == "A, B, C, D, E", f"previous project's groups leaked: {alo()!r}")
baseline()
for step, ids, up, tree, order, top in [
    ("4.1", ["B"], True, "ABCD[AB[B, A], CD[C, D]], E", "B A C D E", "B"),
    ("4.2", ["B"], True, "ABCD[AB[B, A], CD[C, D]], E", "B A C D E", "B"),
    ("4.3", ["CD"], True, "ABCD[CD[C, D], AB[B, A]], E", "C D B A E", "C"),
    ("4.4", ["C", "D"], False, "ABCD[CD[C, D], AB[B, A]], E", "C D B A E", "C"),
]:
    view.move_by_one_requested.emit([gid(i) if i in ("AB", "CD") else lid(i) for i in ids], up)
    expect(step, tree, order, top)
view.btn_undo.click()
view.btn_undo.click()
expect("4.5", BASE, "A B C D E", "A")
view.move_by_one_requested.emit([lid("E")], True)
expect("4.6", "E, ABCD[AB[A, B], CD[C, D]]", "E A B C D", "E")

# 5. Dragging in QGIS's Layer Order panel (cumulative)
baseline()
for step, layer, order, tree, top in [
    ("5.1", "E", "A E B C D", "ABCD[AB[A, E, B], CD[C, D]]", "A"),
    ("5.2", "E", "E A B C D", "ABCD[AB[E, A, B], CD[C, D]]", "E"),
    ("5.3", "E", "A E B C D", "ABCD[AB[A, E, B], CD[C, D]]", "A"),
    ("5.4", "E", "A B C D E", "ABCD[AB[A, B], CD[C, D]], E", "A"),
    ("5.5", "E", "A B C E D", "ABCD[AB[A, B], CD[C, E, D]]", "A"),
    ("5.6", "B", "B A C E D", "ABCD[AB[B, A], CD[C, E, D]]", "B"),
]:
    native_drag(layer, order)
    expect(step, tree, order, top)

# 6. Visibility
baseline()
view.check_requested.emit(lid("C"), False)
expect("6.1", top="A")
check(not root.findLayer(lid("C")).itemVisibilityChecked(), "6.1 C unticked in Layers panel")
root.findLayer(lid("C")).setItemVisibilityChecked(True)
expect("6.2", top="A")
check(model.find_item(lid("C")).visible, "6.2 ALO follows")
view.check_requested.emit(gid("AB"), False)
expect("6.3", top="C")
view.check_requested.emit(gid("AB"), True)
expect("6.4", top="A")
qg = root.insertGroup(0, "QG")
d_node = root.findLayer(lid("D"))
qg.addChildNode(d_node.clone())
root.removeChildNode(d_node)
qg.setItemVisibilityChecked(False)
pump()
check(model.find_item(lid("D")).visible is False, "6.5 D shown unticked in ALO")
view.check_requested.emit(lid("D"), True)
pump()
check(qg.itemVisibilityChecked() and model.find_item(lid("D")).visible, "6.6 QG ticked again")

# 7. Layers added, renamed, removed
baseline()
f_layer = QgsVectorLayer(f"{os.path.join(WORK, 'test_layers.gpkg')}|layername=F", "F", "ogr")
c_node = root.findLayer(lid("C"))
proj.addMapLayer(f_layer, False)
c_node.parent().insertLayer(c_node.parent().children().index(c_node), f_layer)
expect("7.1", "ABCD[AB[A, B], CD[F, C, D]], E", "A B F C D E", "A")
f_layer.setName("F2")
expect("7.2", "ABCD[AB[A, B], CD[F2, C, D]], E", "A B F2 C D E")
view.move_by_one_requested.emit([lid("B")], True)     # an ALO edit after F was added
expect("7.3", "ABCD[AB[B, A], CD[F2, C, D]], E", "B A F2 C D E", "B")
view.btn_undo.click()                                  # undoes the move only
expect("7.3", "ABCD[AB[A, B], CD[F2, C, D]], E", "A B F2 C D E", "A")
proj.removeMapLayer(f_layer.id())
expect("7.4", BASE, "A B C D E", "A")
proj.removeMapLayers([lid("C"), lid("D")])
expect("7.5", "ABCD[AB[A, B]], E", "A B E", "A")
baseline()
view.chk_remove_empty.click()
proj.removeMapLayers([lid("C"), lid("D")])
expect("7.6", "ABCD[AB[A, B], CD[]], E", "A B E")
baseline()
if not model.get_remove_empty_groups():
    view.chk_remove_empty.click()
view.ask_text = lambda *a: "Later"
view.create_group_requested.emit([])
proj.removeMapLayer(lid("E"))
expect("7.7", "ABCD[AB[A, B], CD[C, D]], Later[]", "A B C D")
finish()
