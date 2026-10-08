"""Shared harness for the in-QGIS checks (run by scripts/run_qgis_checks.sh).

These checks need a real QGIS (they exercise QgsProject / QgsLayerTree
signals that the unit-test stubs cannot reproduce). Each check is a script
run in its own process with QGIS's bundled Python.
"""
import os
import sys
import time

from qgis.core import QgsApplication, QgsProject, QgsVectorLayer

APP = QgsApplication([], True)
APP.initQgis()

from qgis.PyQt.QtCore import QCoreApplication, QObject, pyqtSignal  # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow  # noqa: E402

from layer_order_plus_qgis4.controller import ENTRY_SCOPE, ENTRY_TREE, LayerOrderController  # noqa: E402
from layer_order_plus_qgis4.model import GroupNode, LayerOrderModel  # noqa: E402
from layer_order_plus_qgis4.view import LayerOrderView  # noqa: E402
from layer_order_plus_qgis4.view_controller import ViewController  # noqa: E402


def open_test_project() -> None:
    """A fresh project like the one the bugs were reported on.

    Layers A–E, Layers panel order E..A (QGIS puts new layers on top), a
    saved custom order A..E with control OFF, and a saved Plus tree A..E.
    """
    proj = QgsProject.instance()
    proj.clear()
    layers = [QgsVectorLayer("Point?crs=EPSG:4326", name, "memory") for name in "ABCDE"]
    for lyr in layers:
        proj.addMapLayer(lyr, False)
        proj.layerTreeRoot().insertLayer(0, lyr)
    root = proj.layerTreeRoot()
    root.setCustomLayerOrder(layers)
    root.setHasCustomLayerOrder(False)
    saved = LayerOrderModel()
    for lyr in layers:
        saved.add_layer(lyr.id(), lyr.name())
    proj.writeEntry(ENTRY_SCOPE, ENTRY_TREE, saved.serialize())


class _Canvas:
    def refresh(self):
        pass


class FakeIface(QObject):
    """The part of QgisInterface the plugin uses."""
    projectRead = pyqtSignal()
    newProjectCreated = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.mw = QMainWindow()
        self.mw.menuBar().addMenu("&Edit").setObjectName("mEditMenu")

    def mainWindow(self):
        return self.mw

    def mapCanvas(self):
        return _Canvas()

    def addDockWidget(self, area, w):
        self.mw.addDockWidget(area, w)

    def removeDockWidget(self, w):
        self.mw.removeDockWidget(w)

    def addPluginToMenu(self, menu, action):
        pass

    def removePluginMenu(self, menu, action):
        pass


def pump(ms: int = 120) -> None:
    """Run the event loop for `ms` (timers, deferred drops)."""
    end = time.time() + ms / 1000
    while time.time() < end:
        QCoreApplication.processEvents()


class Rig:
    """Model + View + ViewController + Controller on the test project."""

    def __init__(self):
        open_test_project()
        self.project = QgsProject.instance()
        self.root = self.project.layerTreeRoot()
        self.model = LayerOrderModel()
        self.view = LayerOrderView()
        self.vc = ViewController(self.model, self.view)
        self.ctl = LayerOrderController(self.model, FakeIface())
        self.vc.visibility_requested.connect(self.ctl.set_layers_visible)
        self.vc.control_requested.connect(self.ctl.set_control_enabled)
        self.ctl.project_loaded.connect(self.vc.reset_history)
        self.ctl.load_project()
        pump()
        self.ids = {lyr.name(): lyr.id() for lyr in self.project.mapLayers().values()}

    def name(self, lid):
        lyr = self.project.mapLayer(lid)
        return lyr.name() if lyr else lid

    def shape(self, nodes=None):
        nodes = self.model.get_root() if nodes is None else nodes
        return [self.name(n.id) if not isinstance(n, GroupNode) else (n.name, self.shape(n.children))
                for n in nodes]

    def qgis_order(self) -> str:
        return "".join(self.name(lyr.id()) for lyr in self.root.customLayerOrder())

    def plus_order(self) -> str:
        return "".join(self.name(i) for i in self.model.get_flattened_layer_ids())

    def group_ids(self) -> dict:
        return {n.name: n.id for n, _ in self.model.walk() if isinstance(n, GroupNode)}

    def take_control(self):
        if not self.model.get_control_enabled():
            self.view.chk_control.click()
            pump()

    def fresh_abcde(self):
        """Ungrouped A..E, top to bottom."""
        layers = sorted(self.project.mapLayers().values(), key=lambda lyr: lyr.name())
        with self.model.block_notifications():
            self.model.replace_root([])
            for lyr in layers:
                self.model.add_layer(lyr.id(), lyr.name())
        pump()

    def create_group(self, name, ids):
        self.view.ask_text = lambda *a: name
        self.view.create_group_requested.emit(ids)
        pump()
        return self.group_ids()[name]

    def native_drag(self, layer: str, new_order: str, between_steps_ms: int = 120):
        """What QGIS's Layer Order panel does on a drag: insert the layer at its
        new row (listed twice), then remove the old row — with the event
        loop running in between, as in the drag's own event loop."""
        cur = [lyr.id() for lyr in self.root.customLayerOrder()]
        target = [self.ids[c] for c in new_order]
        lid = self.ids[layer]
        dup = list(cur)
        dup.insert(target.index(lid) + (1 if cur.index(lid) < target.index(lid) else 0), lid)
        self.root.setCustomLayerOrder([self.project.mapLayer(i) for i in dup])
        pump(between_steps_ms)
        self.root.setCustomLayerOrder([self.project.mapLayer(i) for i in target])
        pump(between_steps_ms)


def check(condition, message=""):
    if not condition:
        print(f"FAIL: {message}")
        sys.exit(1)


def finish() -> None:
    """Report success and leave without QGIS's C++ teardown, which can abort
    at interpreter exit when widgets (canvas, bridges) outlive the app."""
    print("OK", flush=True)
    os._exit(0)
