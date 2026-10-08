"""QGIS's Layer Order panel shows the real order after control is toggled
from either checkbox (it only refreshes on customLayerOrderChanged)."""
from harness import Rig, check, finish, pump
from qgis.core import QgsProject
from qgis.gui import QgsCustomLayerOrderWidget, QgsLayerTreeMapCanvasBridge, QgsMapCanvas
from qgis.PyQt.QtWidgets import QCheckBox, QListView

root = QgsProject.instance().layerTreeRoot()
canvas = QgsMapCanvas()
bridge = QgsLayerTreeMapCanvasBridge(root, canvas)
native = QgsCustomLayerOrderWidget(bridge)
native.show()
native_box = native.findChild(QCheckBox)
native_list = native.findChild(QListView).model()

for which in ("plus", "native"):
    r = Rig()   # the panel exists before the project opens, as in QGIS
    box = r.view.chk_control if which == "plus" else native_box

    def native_rows():
        return "".join(str(native_list.data(native_list.index(i, 0))) for i in range(native_list.rowCount()))

    if r.model.get_control_enabled():
        box.click()
        pump()
    box.click()
    pump()
    check(native_rows() == r.plus_order(), f"{which} ON: native {native_rows()} vs Plus {r.plus_order()}")
    box.click()
    pump()
    tree = "".join(r.name(lyr.id()) for lyr in root.layerOrder())
    check(native_rows() == tree, f"{which} OFF: native {native_rows()} vs map {tree}")
finish()
