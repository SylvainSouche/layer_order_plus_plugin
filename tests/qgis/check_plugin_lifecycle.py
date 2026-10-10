"""Plugin load/unload (twice) through classFactory; the panel's Undo / Redo
buttons; QGIS's Edit menu and Ctrl+Z left to QGIS; dock survives project
changes."""
import importlib

from harness import FakeIface, check, finish, open_test_project, pump
from qgis.core import QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QAction, QKeySequence
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QDockWidget

open_test_project()
package = importlib.import_module("advanced_layer_order")
iface = FakeIface()
edit_menu = iface.mw.menuBar().actions()[0].menu()
for _ in range(2):
    plugin = package.classFactory(iface)
    plugin.initGui()
    pump()
    check(plugin.view.item_model.rowCount() == 5, "tree populated")
    check(len(edit_menu.actions()) == 0, "QGIS's Edit menu left alone")
    if not plugin.model.get_control_enabled():
        plugin.view.chk_control.click()
        pump()
    stack = plugin.view_controller.undo_stack
    view = plugin.view
    check(not view.btn_undo.isEnabled() and not view.btn_redo.isEnabled(), "buttons idle on a fresh project")
    ids = plugin.model.get_flattened_layer_ids()
    plugin.view_controller.handle_drop([ids[0]], "", "end")
    plugin.view_controller.handle_drop([ids[1]], "", "end")
    pump()
    before = stack.index()
    check(view.btn_undo.isEnabled() and view.btn_undo.toolTip() == "Undo: Move Items",
          "Undo button names the step")
    view.btn_undo.click()
    pump()
    check(stack.index() == before - 1 and view.btn_redo.isEnabled(), "Undo button: one step")
    view.btn_redo.click()
    pump()
    check(stack.index() == before and not view.btn_redo.isEnabled(), "Redo button: one step")

    # Ctrl+Z is QGIS's, even with the focus in the panel (a layer being
    # edited enables QGIS's own Undo)
    editing = QgsVectorLayer("Point?crs=EPSG:4326", "scratch", "memory")
    QgsProject.instance().addMapLayer(editing)
    editing.startEditing()
    qgis_undo = QAction("Undo", iface.mw)
    qgis_undo.setShortcut(QKeySequence("Ctrl+Z"))
    qgis_fired = []
    qgis_undo.triggered.connect(lambda _=False, fired=qgis_fired: fired.append(True))
    iface.mw.addAction(qgis_undo)
    iface.mw.show()
    QTest.qWaitForWindowExposed(iface.mw)
    iface.mw.activateWindow()                  # window shortcuts need the active window
    view.tree.setFocus()
    pump()
    QTest.keyClick(view.tree, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    pump()
    check(qgis_fired and stack.index() == before, "Ctrl+Z goes to QGIS, the layer order is untouched")
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
    check(len(edit_menu.actions()) == 0, "Edit menu still untouched after unload")
finish()
