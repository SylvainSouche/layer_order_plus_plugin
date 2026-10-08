"""Full user scenario: groups, stock-panel moves, Plus drops, visibility,
layers added/removed, undo, project reload, control off."""
from harness import Rig, check, finish, pump
from qgis.core import QgsVectorLayer

from layer_order_plus_qgis4.tree_model import DROP_END, DROP_ON

r = Rig()
r.fresh_abcde()
r.take_control()
check(r.root.hasCustomLayerOrder() and r.qgis_order() == r.plus_order(), "control on")

lid = r.ids
ab = r.create_group("AB", [lid["A"], lid["B"]])
cd = r.create_group("CD", [lid["C"], lid["D"]])
abcd = r.create_group("ABCD", [ab, cd])
check(r.shape() == [("ABCD", [("AB", ["A", "B"]), ("CD", ["C", "D"])]), "E"], f"groups {r.shape()}")

for order in ["ABCED", "ABECD", "AEBCD"]:
    r.native_drag("E", order)
    check(r.qgis_order() == r.plus_order() == order, f"native drag to {order}")
check(r.shape() == [("ABCD", [("AB", ["A", "E", "B"]), ("CD", ["C", "D"])])], f"after native {r.shape()}")

r.view.drop_requested.emit([cd], "", DROP_END)
pump()
r.view.drop_requested.emit([cd], abcd, DROP_ON)
pump()
check(r.qgis_order() == r.plus_order(), "plus drops applied")

# Visibility both ways, including a layer inside an unchecked QGIS group
r.view.check_requested.emit(ab, False)
pump()
check(not r.root.findLayer(lid["A"]).itemVisibilityChecked() and not r.model.find_item(lid["A"]).visible,
      "group uncheck in Plus reaches QGIS")
r.root.findLayer(lid["A"]).setItemVisibilityChecked(True)
pump()
check(r.model.find_item(lid["A"]).visible, "QGIS check reaches Plus")
qg = r.root.insertGroup(0, "QG")
node = r.root.findLayer(lid["D"])
qg.addChildNode(node.clone())
r.root.removeChildNode(node)
qg.setItemVisibilityChecked(False)
pump()
check(r.model.find_item(lid["D"]).visible is False, "effective visibility mirrored")
r.view.check_requested.emit(lid["D"], True)
pump()
check(qg.itemVisibilityChecked() and r.model.find_item(lid["D"]).visible, "checking re-enables parent")

# A layer added next to C in the Layers panel lands next to C in Plus
new = QgsVectorLayer("Point?crs=EPSG:4326", "N", "memory")
r.project.addMapLayer(new, False)
parent = r.root.findLayer(lid["C"]).parent()
parent.insertLayer(parent.children().index(r.root.findLayer(lid["C"])), new)
pump()
check("N" in r.plus_order() and r.qgis_order() == r.plus_order(), f"layer placed {r.shape()}")
r.vc.undo_stack.undo()
pump()
check("N" in r.plus_order() and r.qgis_order() == r.plus_order(), "undo keeps layers QGIS has")
r.project.removeMapLayer(new.id())
pump()
check("N" not in r.plus_order() and r.qgis_order() == r.plus_order(), "layer removed")

saved = r.shape()
r.ctl.load_project()
pump()
check(r.shape() == saved and not r.vc.undo_stack.canUndo(), "project reload")

r.view.chk_control.click()
pump()
check(not r.model.get_control_enabled() and not r.root.hasCustomLayerOrder(), "control off")
finish()
