"""Plugin load/unload (twice) through classFactory, Edit-menu integration,
and keyboard undo/redo: one key press, one step."""
import importlib

from harness import FakeIface, check, finish, open_test_project, pump
from qgis.core import QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QAction, QKeySequence
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QDockWidget, QLineEdit

open_test_project()
package = importlib.import_module("advanced_layer_order")
iface = FakeIface()
edit_menu = iface.mw.menuBar().actions()[0].menu()
for _ in range(2):
    plugin = package.classFactory(iface)
    plugin.initGui()
    pump()
    check(plugin.view.item_model.rowCount() == 5, "tree populated")
    check(len(edit_menu.actions()) == 3, "Edit menu: separator + undo + redo")
    if not plugin.model.get_control_enabled():
        plugin.view.chk_control.click()
        pump()
    stack = plugin.view_controller.undo_stack
    ids = plugin.model.get_flattened_layer_ids()
    plugin.view_controller.handle_drop([ids[0]], "", "end")
    plugin.view_controller.handle_drop([ids[1]], "", "end")
    pump()
    before = stack.index()
    iface.mw.show()
    plugin.view.tree.setFocus()
    QTest.keyClick(plugin.view.tree, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    pump()
    check(stack.index() == before - 1, "Ctrl+Z undoes exactly one step")
    QTest.keyClick(plugin.view.tree, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    pump()
    check(stack.index() == before, "Ctrl+Y redoes exactly one step")
    # A layer in edit mode enables QGIS's own Undo (Ctrl+Z). Focus in the
    # panel: Ctrl+Z undoes the layer order; focus elsewhere: QGIS's undo.
    editing = QgsVectorLayer("Point?crs=EPSG:4326", "scratch", "memory")
    QgsProject.instance().addMapLayer(editing)
    editing.startEditing()
    qgis_undo = QAction("Undo", iface.mw)
    qgis_undo.setShortcut(QKeySequence("Ctrl+Z"))
    qgis_fired = []
    qgis_undo.triggered.connect(lambda _=False, fired=qgis_fired: fired.append(True))
    iface.mw.addAction(qgis_undo)
    plugin.view_controller.handle_drop([ids[0]], "", "end")
    pump()
    before = stack.index()
    plugin.view.tree.setFocus()
    QTest.keyClick(plugin.view.tree, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    pump()
    check(stack.index() == before - 1 and not qgis_fired, "Ctrl+Z in the panel while a layer is edited")
    other = QLineEdit(iface.mw)
    other.setReadOnly(True)                                # no text undo of its own
    other.setFocus()
    QTest.keyClick(other, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    pump()
    check(qgis_fired and stack.index() == before - 1, "Ctrl+Z elsewhere goes to QGIS's undo while editing")
    iface.mw.removeAction(qgis_undo)
    editing.rollBack()
    QgsProject.instance().removeMapLayer(editing.id())

    # The dock must survive being tabbed behind another panel and a project
    # being closed/reopened (it used to close itself on visibilityChanged)
    other = QDockWidget("Other panel", iface.mw)
    iface.mw.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, other)
    iface.mw.tabifyDockWidget(plugin.view, other)
    other.show()
    other.raise_()
    pump()
    QgsProject.instance().clear()
    open_test_project()
    iface.projectRead.emit()
    pump()
    plugin.view.raise_()
    pump()
    check(plugin.view.isVisible(), "dock closed itself after being tabbed / project closed")
    iface.mw.removeDockWidget(other)
    plugin.unload()
    pump()
    check(len(edit_menu.actions()) == 0, "Edit menu cleaned on unload")
finish()
