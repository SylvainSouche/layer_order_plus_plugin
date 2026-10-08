"""Plugin load/unload (twice) through classFactory, Edit-menu integration,
and keyboard undo/redo: one key press, one step."""
import importlib

from harness import FakeIface, check, finish, open_test_project, pump
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtTest import QTest

open_test_project()
package = importlib.import_module("layer_order_plus_qgis4")
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
    plugin.unload()
    pump()
    check(len(edit_menu.actions()) == 0, "Edit menu cleaned on unload")
finish()
