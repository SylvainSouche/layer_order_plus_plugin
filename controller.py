"""Layer Order Plus — Controller (QGIS ↔ Model synchronization).

The Controller handles all interaction with QGIS:
- Listens to QGIS signals (layersAdded, layersWillBeRemoved, layer.nameChanged,
  QgsLayerTreeLayer.visibilityChanged) → routes to ViewController/Model
- Listens to Model order_changed → applies to QGIS (setCustomLayerOrder, mapCanvas.refresh)
- Owns the apply state machine (debounce, suspended, force)
- Owns the control-rendering-order checkbox sync (QGIS hasCustomLayerOrder)
- Owns the remove-empty-groups setting persistence (QgsProject entry)
- Provides the layer_icon_provider callable to the ViewController

The Controller does NOT:
- Touch the View directly (that's the ViewController's job)
- Own the undo stack (that's the ViewController's job)
- Build UI widgets
"""
import traceback

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QIcon

from qgis.core import (
    QgsProject,
    QgsMessageLog,
    Qgis,
)

from .icons import icon_for_layer
from .view import LayerOrderView


LOG_TAG = "LayerOrderPlus"


def _log(msg, level=Qgis.Info):
    try:
        QgsMessageLog.logMessage(str(msg), LOG_TAG, level)
    except Exception:
        try:
            print(f"[{LOG_TAG}] {msg}")
        except Exception:
            pass


class LayerOrderController(QObject):
    """Synchronizes QGIS state with the Model (via the ViewController).

    Responsibilities:
    - QGIS layer signals → ViewController.add_layers / remove_layers / rename_layer
    - QGIS layer visibility signals → ViewController.set_layer_visibility
    - Model order_changed → QGIS setCustomLayerOrder + mapCanvas.refresh
    - Control-rendering-order checkbox ↔ QGIS hasCustomLayerOrder
    - remove-empty-groups setting ↔ QgsProject entry
    - Apply state machine (debounce, suspended, force)
    """

    # Signal: emit when the controller wants to save the tree to the project
    autosave_requested = pyqtSignal(str)  # serialized tree JSON

    def __init__(self, view: LayerOrderView, view_controller, iface, parent=None):
        super().__init__(parent)
        self._view = view
        self._vc = view_controller
        self._iface = iface
        self._model = view_controller._model  # access for queries

        # Apply state machine
        self._apply_suspended = True
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.timeout.connect(self._apply_now)

        # Layer rename + visibility connections (layer_id → (layer, slot) or (ltl, slot))
        self._layer_rename_connections = {}
        self._visibility_connections = {}

        # Connect signals
        self._connect_qgis_signals()
        self._connect_view_signals()
        self._connect_vc_signals()
        self._connect_layer_tree_visibility()

        # Sync initial state from QGIS
        self._sync_control_from_project()
        self._sync_remove_empty_from_project()

    # ==================================================================
    # Layer icon provider (passed to ViewController for View rebuilds)
    # ==================================================================
    def layer_icon_provider(self, layer_id):
        """Return a QIcon for a layer id. Used by ViewController when building tree items."""
        try:
            lyr = QgsProject.instance().mapLayer(layer_id)
            if lyr is not None:
                return icon_for_layer(lyr)
        except Exception:
            pass
        return QIcon()

    # ==================================================================
    # QGIS signal connections
    # ==================================================================
    def _connect_qgis_signals(self):
        proj = QgsProject.instance()
        proj.layersAdded.connect(self._on_layers_added)
        proj.layersWillBeRemoved.connect(self._on_layers_removed)

    def _connect_view_signals(self):
        # Control checkbox — user toggles → sync to QGIS hasCustomLayerOrder
        self._view.control_toggled.connect(self._on_control_toggled)
        # Remove-empty checkbox — user toggles → persist to QgsProject
        self._view.remove_empty_toggled.connect(self._on_remove_empty_toggled)
        # Layer visibility toggled in View → sync to QgsLayerTreeLayer
        self._view.layer_visibility_toggled.connect(self._on_layer_visibility_toggled)

    def _connect_vc_signals(self):
        # ViewController emits order_changed when the Model's flattened order changes
        self._vc.order_changed.connect(self._on_order_changed)

    def _connect_layer_tree_visibility(self):
        """Connect to the QGIS layer tree root's visibilityChanged signal.

        This catches GROUP visibility changes in the Layers panel (which
        don't always fire per-layer visibilityChanged). When a group is
        toggled, we refresh the effective visibility of all layers.
        """
        try:
            root = QgsProject.instance().layerTreeRoot()
            # visibilityChanged on the root fires when any node's visibility changes
            root.visibilityChanged.connect(self._on_tree_visibility_changed)
        except Exception as e:
            _log(f"_connect_layer_tree_visibility: failed: {e!r}", Qgis.Warning)

    def _on_tree_visibility_changed(self, node):
        """A node's visibility changed in the Layers panel → refresh all layers.

        When a group is toggled, we need to update all child layers' effective
        visibility in the Model (and thus the View checkboxes).
        """
        # Refresh effective visibility for all layers in the Model
        for layer_id in list(self._vc.get_flattened_layer_ids()):
            visible = self._get_effective_visibility(layer_id)
            self._vc.set_layer_visibility(layer_id, visible)

    # ==================================================================
    # QGIS layer signal handlers
    # ==================================================================
    def _on_layers_added(self, layers):
        _log(f"_on_layers_added: received {len(layers) if layers else 0} layer(s)")
        try:
            layer_data = [(lyr.id(), lyr.name()) for lyr in layers]
            self._vc.add_layers(layer_data)
            # Connect rename + visibility for each new layer
            for lyr in layers:
                self._connect_layer_rename(lyr)
                self._connect_layer_visibility(lyr)
        except Exception as e:
            _log(f"_on_layers_added FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    def _on_layers_removed(self, layer_ids):
        _log(f"_on_layers_removed: received {len(layer_ids) if layer_ids else 0} id(s)")
        try:
            for lid in (layer_ids or []):
                self._disconnect_layer_rename(lid)
                self._disconnect_layer_visibility(lid)
            self._vc.remove_layers(list(layer_ids or []))
        except Exception as e:
            _log(f"_on_layers_removed FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    # ==================================================================
    # Layer rename sync (QGIS layer.nameChanged → ViewController)
    # ==================================================================
    def connect_layer(self, lyr):
        """Public API: connect rename + visibility listeners for a layer.

        Called by plugin.py on project load for every existing layer.
        Idempotent — safe to call multiple times for the same layer.
        """
        self._connect_layer_rename(lyr)
        self._connect_layer_visibility(lyr)

    def _connect_layer_rename(self, lyr):
        if lyr is None:
            return
        lid = lyr.id()
        if lid in self._layer_rename_connections:
            return
        try:
            slot = lambda *_a, _lid=lid, _lyr=lyr: self._on_layer_renamed(_lid, _lyr)
            lyr.nameChanged.connect(slot)
            self._layer_rename_connections[lid] = (lyr, slot)
        except Exception as e:
            _log(f"_connect_layer_rename: failed for {lid}: {e!r}", Qgis.Warning)

    def _disconnect_layer_rename(self, lid):
        entry = self._layer_rename_connections.pop(lid, None)
        if entry is None:
            return
        lyr, slot = entry
        try:
            lyr.nameChanged.disconnect(slot)
        except Exception:
            pass

    def _disconnect_all_layer_renames(self):
        for lid in list(self._layer_rename_connections.keys()):
            self._disconnect_layer_rename(lid)

    def _on_layer_renamed(self, lid, lyr):
        try:
            new_name = lyr.name()
        except RuntimeError:
            return
        self._vc.rename_layer(lid, new_name)

    # ==================================================================
    # Layer visibility sync (QgsLayerTreeLayer.visibilityChanged → ViewController)
    # ==================================================================
    def _find_layer_tree_layer(self, layer_id):
        try:
            root = QgsProject.instance().layerTreeRoot()
            return root.findLayer(layer_id)
        except Exception:
            return None

    def _connect_layer_visibility(self, lyr):
        if lyr is None:
            return
        lid = lyr.id()
        if lid in self._visibility_connections:
            return
        ltl = self._find_layer_tree_layer(lid)
        if ltl is None:
            return
        try:
            slot = lambda *_a, _lid=lid: self._on_layer_visibility_external(_lid)
            ltl.visibilityChanged.connect(slot)
            self._visibility_connections[lid] = (ltl, slot)
        except Exception as e:
            _log(f"_connect_layer_visibility: failed for {lid}: {e!r}", Qgis.Warning)

    def _disconnect_layer_visibility(self, lid):
        entry = self._visibility_connections.pop(lid, None)
        if entry is None:
            return
        ltl, slot = entry
        try:
            ltl.visibilityChanged.disconnect(slot)
        except Exception:
            pass

    def _disconnect_all_layer_visibilities(self):
        for lid in list(self._visibility_connections.keys()):
            self._disconnect_layer_visibility(lid)

    def _get_effective_visibility(self, layer_id):
        """Return the EFFECTIVE visibility of a layer (considering parent groups).

        QGIS's `itemVisibilityChecked()` returns the individual checkbox state,
        not the effective visibility. If a parent group is unchecked, the layer
        is effectively invisible even if its own checkbox is checked.
        We walk up the QgsLayerTree to compute the true effective state.
        """
        ltl = self._find_layer_tree_layer(layer_id)
        if ltl is None:
            return True
        try:
            if not ltl.itemVisibilityChecked():
                return False
            # Walk up the QGIS layer tree checking parent group visibility
            node = ltl.parent()
            while node is not None:
                if hasattr(node, 'itemVisibilityChecked'):
                    try:
                        if not node.itemVisibilityChecked():
                            return False
                    except Exception:
                        pass
                node = node.parent()
            return True
        except RuntimeError:
            return True

    def _on_layer_visibility_external(self, lid):
        """Layer visibility changed in the Layers panel → update Model.

        Uses EFFECTIVE visibility (considering parent group visibility) so
        that a layer inside an unchecked group shows as unchecked in Plus.
        """
        visible = self._get_effective_visibility(lid)
        self._vc.set_layer_visibility(lid, visible)

    def _on_layer_visibility_toggled(self, layer_id, checked):
        """User toggled visibility in the Plus panel → sync to QgsLayerTreeLayer."""
        ltl = self._find_layer_tree_layer(layer_id)
        if ltl is None:
            return
        try:
            ltl.setItemVisibilityChecked(checked)
            try:
                QgsProject.instance().layerTreeRoot().emitVisibilityChanged()
            except Exception:
                pass
            _log(f"_on_layer_visibility_toggled: {layer_id} visible={checked}")
        except Exception as e:
            _log(f"_on_layer_visibility_toggled FAILED: {e!r}", Qgis.Critical)

    # ==================================================================
    # Control rendering order checkbox (QGIS hasCustomLayerOrder ↔ View)
    # ==================================================================
    def _on_control_toggled(self, checked):
        """User toggled the control checkbox → sync to QGIS."""
        root = QgsProject.instance().layerTreeRoot()
        if checked:
            root.setHasCustomLayerOrder(True)
            self._apply_now_force()
        else:
            root.setHasCustomLayerOrder(False)
        try:
            QgsProject.instance().setDirty(True)
        except Exception:
            pass

    def _sync_control_from_project(self):
        """Read QGIS hasCustomLayerOrder → set the View checkbox."""
        root = QgsProject.instance().layerTreeRoot()
        checked = bool(root.hasCustomLayerOrder())
        self._view.set_control_enabled(checked)

    # ==================================================================
    # remove-empty-groups setting (QgsProject entry ↔ View checkbox)
    # ==================================================================
    def _on_remove_empty_toggled(self, checked):
        try:
            QgsProject.instance().writeEntry("BetterLayerOrder", "removeEmptyGroups", bool(checked))
            QgsProject.instance().setDirty(True)
            _log(f"_on_remove_empty_toggled: removeEmptyGroups={checked}")
        except Exception as e:
            _log(f"_on_remove_empty_toggled: failed to persist: {e!r}", Qgis.Warning)

    def _sync_remove_empty_from_project(self):
        try:
            val, ok = QgsProject.instance().readEntry("BetterLayerOrder", "removeEmptyGroups", "1")
            checked = bool(int(val)) if ok and val not in ("", None) else True
        except Exception:
            checked = True
        self._view.set_remove_empty_enabled(checked)

    # ==================================================================
    # Apply state machine (Model order_changed → QGIS setCustomLayerOrder)
    # ==================================================================
    def _on_order_changed(self):
        """Model's flattened order changed → debounce an apply to QGIS."""
        self.request_apply()

    def request_apply(self):
        if self._apply_suspended:
            return
        self._apply_timer.start(50)

    def _apply_now(self):
        if self._apply_suspended:
            return
        self._apply_custom_order()

    def _apply_now_force(self):
        """Apply even when suspended (used by control checkbox toggle)."""
        self._apply_custom_order()

    def _apply_custom_order(self):
        try:
            root = QgsProject.instance().layerTreeRoot()
            if not self._view.is_control_enabled():
                root.setHasCustomLayerOrder(False)
                _log("_apply_custom_order: skipped (control unchecked)")
                return
            # Flatten the Model's layer ids → QgsMapLayer list
            proj = QgsProject.instance()
            layers = []
            for layer_id in self._vc.get_flattened_layer_ids():
                lyr = proj.mapLayer(layer_id)
                if lyr:
                    layers.append(lyr)
            root.setHasCustomLayerOrder(True)
            root.setCustomLayerOrder(layers)
            _log(f"_apply_custom_order: applied {len(layers)} layer(s) to custom order")
            try:
                self._iface.mapCanvas().refresh()
            except Exception as e:
                _log(f"_apply_custom_order: mapCanvas.refresh failed: {e!r}", Qgis.Warning)
        except Exception as e:
            _log(f"_apply_custom_order FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    # ==================================================================
    # External control (plugin.py calls these during project load)
    # ==================================================================
    def set_apply_suspended(self, suspended: bool):
        self._apply_suspended = bool(suspended)
        if not self._apply_suspended:
            self.request_apply()

    def sync_state_from_project(self):
        """Called after project load — sync control + remove-empty checkboxes."""
        self._sync_control_from_project()
        self._sync_remove_empty_from_project()

    def teardown(self):
        """Disconnect all QGIS signal listeners — called on plugin unload."""
        self._disconnect_all_layer_renames()
        self._disconnect_all_layer_visibilities()
        try:
            proj = QgsProject.instance()
            proj.layersAdded.disconnect(self._on_layers_added)
            proj.layersWillBeRemoved.disconnect(self._on_layers_removed)
        except Exception:
            pass
        try:
            root = QgsProject.instance().layerTreeRoot()
            root.visibilityChanged.disconnect(self._on_tree_visibility_changed)
        except Exception:
            pass

    # ==================================================================
    # Initial layer population (called by plugin.py on project load)
    # ==================================================================
    def get_ordered_project_layers(self):
        """Return project layers in the order QGIS would draw them."""
        proj = QgsProject.instance()
        root = proj.layerTreeRoot()
        try:
            if root.hasCustomLayerOrder():
                clo = root.customLayerOrder() or []
                if clo:
                    _log(f"get_ordered_project_layers: using customLayerOrder ({len(clo)} layers)")
                    return list(clo)
            if hasattr(root, "layerOrder"):
                lo = root.layerOrder() or []
                if lo:
                    _log(f"get_ordered_project_layers: using layerOrder ({len(lo)} layers)")
                    return list(lo)
        except Exception as e:
            _log(f"get_ordered_project_layers: order API failed ({e!r}); falling back to mapLayers", Qgis.Warning)
        ml = list(proj.mapLayers().values())
        _log(f"get_ordered_project_layers: fallback to mapLayers ({len(ml)} layers, unordered)", Qgis.Warning)
        return ml
