# plugin.py
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtCore import QObject, Qt, QTimer
from qgis.core import QgsProject

from .dock import BetterLayerOrderDock


class BetterLayerOrderPlugin(QObject):
    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        self.action = None
        self.dock = None

    def initGui(self):
        self.action = QAction("Better Layer Order", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setChecked(True)
        self.action.toggled.connect(self._toggle_dock)
        self.iface.addPluginToMenu("Better Layer Order", self.action)

        self.dock = BetterLayerOrderDock(self.iface)
        self.dock.set_save_callback(self._save_tree_json)
        self.iface.addDockWidget(Qt.LeftDockWidgetArea, self.dock)

        # block apply + block autosave until project is fully read
        self.dock.set_apply_suspended(True)

        # project lifecycle hooks
        QgsProject.instance().cleared.connect(self._on_project_cleared)
        self.iface.projectRead.connect(self._on_project_read)
        self.iface.newProjectCreated.connect(self._on_project_read)

        QgsProject.instance().layersAdded.connect(self._on_layers_added)
        QgsProject.instance().layersWillBeRemoved.connect(self._on_layers_removed)

        # if QGIS is already fully loaded (manual plugin enable), run soon
        QTimer.singleShot(0, self._on_project_read)

    def unload(self):
        try:
            QgsProject.instance().layersAdded.disconnect(self._on_layers_added)
            QgsProject.instance().layersWillBeRemoved.disconnect(self._on_layers_removed)
        except Exception:
            pass

        try:
            QgsProject.instance().cleared.disconnect(self._on_project_cleared)
        except Exception:
            pass
        try:
            self.iface.projectRead.disconnect(self._on_project_read)
        except Exception:
            pass
        try:
            self.iface.newProjectCreated.disconnect(self._on_project_read)
        except Exception:
            pass

        if self.dock:
            # persist last state
            self._save_tree_json(self.dock._serialize_tree())
            self.dock.deleteLater()
            self.dock = None

        if self.action:
            self.iface.removePluginMenu("Better Layer Order", self.action)
            self.action.deleteLater()
            self.action = None

    def _toggle_dock(self, on):
        if self.dock:
            self.dock.setVisible(bool(on))
            if on:
                self.dock.raise_()

    def _on_project_cleared(self):
        # project is about to be read/opened
        if self.dock:
            self.dock.set_apply_suspended(True)
            self.dock.clear_tree_ui()  # optional but prevents showing stale tree

    def _on_project_read(self):
        # project finished reading: now layers exist -> safe to rebuild tree
        if not self.dock:
            return

        self.dock.set_apply_suspended(True)          # keep blocked while rebuilding
        self.dock.load_from_project(self._load_tree_json())
        self.dock.set_apply_suspended(False)         # allow apply + autosave
        self.dock.request_apply()

    def _load_tree_json(self):
        proj = QgsProject.instance()
        return proj.readEntry("BetterLayerOrder", "tree_json", "")[0] or ""

    def _save_tree_json(self, raw: str):
        proj = QgsProject.instance()
        proj.writeEntry("BetterLayerOrder", "tree_json", raw)
        proj.setDirty(True)

    def _on_layers_added(self, layers):
        if self.dock:
            self.dock.on_layers_added(layers)

    def _on_layers_removed(self, layer_ids):
        if self.dock:
            self.dock.on_layers_removed(layer_ids)
