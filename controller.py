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
from .logger import _log, _vlog, _vlog_method, _vlog_error

from .icons import icon_for_layer
from .view import LayerOrderView


LOG_TAG = "LayerOrderPlus"


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
        self._in_reconcile = False  # guard against reconcile feedback loop
        self._applying = False     # True while WE write to QGIS (its signals fire synchronously)
        self._in_layer_add_remove = False  # guard: layer add/remove triggers order changes
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.timeout.connect(self._apply_now)
        # The stock Layer Order panel changes the order in two steps per drag
        # (insert at new row → layer briefly listed twice, then remove old
        # row). Reconcile once, on the final state, on the next loop turn.
        self._reconcile_timer = QTimer(self)
        self._reconcile_timer.setSingleShot(True)
        self._reconcile_timer.setInterval(0)
        self._reconcile_timer.timeout.connect(self._reconcile_pending)
        self._dragged_hint = set()  # layer ids seen duplicated in the intermediate state

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
        _vlog_method("_connect_qgis_signals", "[C]")
        proj = QgsProject.instance()
        proj.layersAdded.connect(self._on_layers_added)
        proj.layersWillBeRemoved.connect(self._on_layers_removed)

    def _connect_view_signals(self):
        _vlog_method("_connect_view_signals", "[C]")
        # Control checkbox — user toggles → sync to QGIS hasCustomLayerOrder
        self._view.control_toggled.connect(self._on_control_toggled)
        # Remove-empty checkbox — user toggles → persist to QgsProject
        self._view.remove_empty_toggled.connect(self._on_remove_empty_toggled)
        # Layer visibility toggled in View → sync to QgsLayerTreeLayer
        self._view.layer_visibility_toggled.connect(self._on_layer_visibility_toggled)

    def _connect_vc_signals(self):
        _vlog_method("_connect_vc_signals", "[C]")
        # ViewController emits order_changed when the Model's flattened order changes
        self._vc.order_changed.connect(self._on_order_changed)

    def _connect_layer_tree_visibility(self):
        _vlog_method("_connect_layer_tree_visibility", "[C]")
        """Connect to the QGIS layer tree root's signals.

        - visibilityChanged: catches GROUP visibility changes in the Layers panel
        - hasCustomLayerOrderChanged: syncs the control checkbox with stock Layer Order
        - customLayerOrderChanged: syncs layer moves from stock Layer Order to Plus
        """
        try:
            root = QgsProject.instance().layerTreeRoot()
            root.visibilityChanged.connect(self._on_tree_visibility_changed)
            # Sync control checkbox with stock Layer Order panel
            if hasattr(root, 'hasCustomLayerOrderChanged'):
                root.hasCustomLayerOrderChanged.connect(self._on_has_custom_order_changed)
            # Sync layer moves from stock Layer Order panel
            if hasattr(root, 'customLayerOrderChanged'):
                root.customLayerOrderChanged.connect(self._on_custom_order_changed)
        except Exception as e:
            _log(f"_connect_layer_tree_visibility: failed: {e!r}", Qgis.Warning)

    def _on_has_custom_order_changed(self):
        _vlog_method("_on_has_custom_order_changed", "[C]")
        """Stock Layer Order checkbox toggled → sync Plus checkbox.

        When the user activates 'Control rendering order' in the stock panel:
        - If Plus already has a saved tree (from project), PUSH our order to
          QGIS (we take priority — the user chose to use Plus).
        - If Plus has no saved tree, PULL from QGIS's customLayerOrder.

        This prevents the "B first A second" mismatch where the stock panel
        shows a different order than Plus.
        """
        self._sync_control_from_project()
        if self._applying:
            return  # our own setHasCustomLayerOrder(True) inside _apply_custom_order
        root = QgsProject.instance().layerTreeRoot()
        if root.hasCustomLayerOrder():
            our_order_ids = self._vc.get_flattened_layer_ids()
            qgis_order_ids = [lyr.id() for lyr in root.customLayerOrder()]
            if our_order_ids and our_order_ids != qgis_order_ids:
                # Plus has a saved tree — push our order to QGIS
                _log("[C] _on_has_custom_order_changed: pushing Plus order to QGIS")
                self._apply_now_force()
            elif not our_order_ids and qgis_order_ids:
                # Plus is empty — pull from QGIS
                _log("[C] _on_has_custom_order_changed: pulling from customLayerOrder")
                from .model import LayerNode
                self._vc.replace_root_from_external(
                    [LayerNode(id=lyr.id(), name=lyr.name()) for lyr in root.customLayerOrder()])

    def _on_custom_order_changed(self):
        _vlog_method("_on_custom_order_changed", "[C]")
        """QGIS custom order changed (stock Layer Order panel) → schedule a reconcile.

        Guards:
        - Skip our own writes (_applying: QGIS emits synchronously from
          setCustomLayerOrder).
        - Skip while a Plus change is waiting in the debounce timer: QGIS's
          order is stale and reconciling would revert the user's edit.
        - Skip during layer add/remove.
        The actual reconcile is deferred (see _reconcile_timer) so the stock
        panel's intermediate "layer listed twice" state is never used as a
        target — it is only used as a hint of which layer was dragged.
        """
        if not self._view.is_control_enabled():
            return
        if self._in_reconcile:
            return
        if self._applying:
            _vlog("[C] _on_custom_order_changed: skipped (our own apply)")
            return
        if self._apply_timer.isActive():
            _vlog("[C] _on_custom_order_changed: skipped (Plus change pending)")
            return
        if self._in_layer_add_remove:
            return
        try:
            ids = [lyr.id() for lyr in QgsProject.instance().layerTreeRoot().customLayerOrder()]
        except Exception:
            return
        seen = set()
        for lid in ids:
            if lid in seen:
                self._dragged_hint.add(lid)
            seen.add(lid)
        self._reconcile_timer.start()

    def _reconcile_pending(self):
        _vlog_method("_reconcile_pending", "[C]")
        hint, self._dragged_hint = self._dragged_hint, set()
        if not self._view.is_control_enabled() or self._applying or self._apply_timer.isActive():
            return
        self._in_reconcile = True
        try:
            root = QgsProject.instance().layerTreeRoot()
            qgis_order = [lyr.id() for lyr in root.customLayerOrder()]
            our_order = self._vc.get_flattened_layer_ids()
            if qgis_order == our_order:
                return
            if len(set(qgis_order)) != len(qgis_order) or set(qgis_order) != set(our_order):
                # Not a pure reorder (add/remove in progress, or still an
                # intermediate state) — on_layers_added/removed handle sets
                _log("[C] _reconcile_pending: QGIS order is not a reorder of Plus — skipping")
                return
            self._reconcile_order_with_qgis(qgis_order, hint)
        except Exception as e:
            _log(f"[C] _reconcile_pending FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)
        finally:
            self._in_reconcile = False

    def _reconcile_order_with_qgis(self, qgis_order, moved_hint=()):
        _vlog_method("_reconcile_order_with_qgis", "[C]")
        """Rebuild the Model's tree to match QGIS's flat order (see reconcile.py)."""
        from .reconcile import reconcile_tree
        _log(f"[C] _reconcile: qgis_order={qgis_order} hint={sorted(moved_hint)}")
        new_root = reconcile_tree(self._model.get_root(), qgis_order, moved_hint)
        if new_root is None:
            _log("[C] _reconcile: not a permutation of the Plus layers — skipped", Qgis.Warning)
            return
        self._vc.replace_root_from_external(new_root)
        _log(f"[C] _reconcile: after, our_order={self._vc.get_flattened_layer_ids()}")

    def _on_tree_visibility_changed(self, node):
        _vlog_method("_on_tree_visibility_changed", "[C]")
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
        _vlog_method("_on_layers_added", "[C]")
        _log(f"_on_layers_added: received {len(layers) if layers else 0} layer(s)")
        self._in_layer_add_remove = True
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
        finally:
            self._in_layer_add_remove = False

    def _on_layers_removed(self, layer_ids):
        _vlog_method("_on_layers_removed", "[C]")
        _log(f"_on_layers_removed: received {len(layer_ids) if layer_ids else 0} id(s)")
        self._in_layer_add_remove = True
        try:
            for lid in (layer_ids or []):
                self._disconnect_layer_rename(lid)
                self._disconnect_layer_visibility(lid)
            self._vc.remove_layers(list(layer_ids or []))
        except Exception as e:
            _log(f"_on_layers_removed FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)
        finally:
            self._in_layer_add_remove = False

    # ==================================================================
    # Layer rename sync (QGIS layer.nameChanged → ViewController)
    # ==================================================================
    def connect_layer(self, lyr):
        _vlog_method("connect_layer", "[C]")
        """Public API: connect rename + visibility listeners for a layer.

        Called by plugin.py on project load for every existing layer.
        Idempotent — safe to call multiple times for the same layer.
        """
        self._connect_layer_rename(lyr)
        self._connect_layer_visibility(lyr)

    def _connect_layer_rename(self, lyr):
        _vlog_method("_connect_layer_rename", "[C]")
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
        _vlog_method("_disconnect_layer_rename", "[C]")
        entry = self._layer_rename_connections.pop(lid, None)
        if entry is None:
            return
        lyr, slot = entry
        try:
            lyr.nameChanged.disconnect(slot)
        except Exception:
            pass

    def _disconnect_all_layer_renames(self):
        _vlog_method("_disconnect_all_layer_renames", "[C]")
        for lid in list(self._layer_rename_connections.keys()):
            self._disconnect_layer_rename(lid)

    def _on_layer_renamed(self, lid, lyr):
        _vlog_method("_on_layer_renamed", "[C]")
        try:
            new_name = lyr.name()
        except RuntimeError:
            return
        self._vc.rename_layer(lid, new_name)

    # ==================================================================
    # Layer visibility sync (QgsLayerTreeLayer.visibilityChanged → ViewController)
    # ==================================================================
    def _find_layer_tree_layer(self, layer_id):
        _vlog_method("_find_layer_tree_layer", "[C]")
        try:
            root = QgsProject.instance().layerTreeRoot()
            return root.findLayer(layer_id)
        except Exception:
            return None

    def _connect_layer_visibility(self, lyr):
        _vlog_method("_connect_layer_visibility", "[C]")
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
        _vlog_method("_disconnect_layer_visibility", "[C]")
        entry = self._visibility_connections.pop(lid, None)
        if entry is None:
            return
        ltl, slot = entry
        try:
            ltl.visibilityChanged.disconnect(slot)
        except Exception:
            pass

    def _disconnect_all_layer_visibilities(self):
        _vlog_method("_disconnect_all_layer_visibilities", "[C]")
        for lid in list(self._visibility_connections.keys()):
            self._disconnect_layer_visibility(lid)

    def _get_effective_visibility(self, layer_id):
        _vlog_method("_get_effective_visibility", "[C]")
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
        _vlog_method("_on_layer_visibility_external", "[C]")
        """Layer visibility changed in the Layers panel → update Model.

        Uses EFFECTIVE visibility (considering parent group visibility) so
        that a layer inside an unchecked group shows as unchecked in Plus.
        """
        visible = self._get_effective_visibility(lid)
        self._vc.set_layer_visibility(lid, visible)

    def _on_layer_visibility_toggled(self, layer_id, checked):
        _vlog_method("_on_layer_visibility_toggled", "[C]")
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
        _vlog_method("_on_control_toggled", "[C]")
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
        _vlog_method("_sync_control_from_project", "[C]")
        """Read QGIS hasCustomLayerOrder → set the View checkbox."""
        root = QgsProject.instance().layerTreeRoot()
        checked = bool(root.hasCustomLayerOrder())
        self._view.set_control_enabled(checked)

    # ==================================================================
    # remove-empty-groups setting (QgsProject entry ↔ View checkbox)
    # ==================================================================
    def _on_remove_empty_toggled(self, checked):
        _vlog_method("_on_remove_empty_toggled", "[C]")
        try:
            QgsProject.instance().writeEntry("BetterLayerOrder", "removeEmptyGroups", bool(checked))
            QgsProject.instance().setDirty(True)
            _log(f"_on_remove_empty_toggled: removeEmptyGroups={checked}")
        except Exception as e:
            _log(f"_on_remove_empty_toggled: failed to persist: {e!r}", Qgis.Warning)

    def _sync_remove_empty_from_project(self):
        _vlog_method("_sync_remove_empty_from_project", "[C]")
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
        _vlog_method("_on_order_changed", "[C]")
        """Model's flattened order changed → debounce an apply to QGIS."""
        self.request_apply()

    def request_apply(self):
        _vlog_method("request_apply", "[C]")
        if self._apply_suspended:
            return
        self._apply_timer.start(50)

    def _apply_now(self):
        _vlog_method("_apply_now", "[C]")
        if self._apply_suspended:
            return
        self._apply_custom_order()

    def _apply_now_force(self):
        _vlog_method("_apply_now_force", "[C]")
        """Apply even when suspended (used by control checkbox toggle)."""
        self._apply_custom_order()

    def _apply_custom_order(self):
        _vlog_method("_apply_custom_order", "[C]")
        # QGIS emits hasCustomLayerOrderChanged / customLayerOrderChanged
        # synchronously from the setters below; _applying tells our own
        # handlers to ignore them.
        if self._applying:
            return
        self._apply_timer.stop()
        self._applying = True
        try:
            root = QgsProject.instance().layerTreeRoot()
            if not self._view.is_control_enabled():
                root.setHasCustomLayerOrder(False)
                _log("[C] _apply_custom_order: skipped (control unchecked)")
                return
            # Flatten the Model's layer ids → QgsMapLayer list
            # Deduplicate — a layer should only appear once in customLayerOrder
            proj = QgsProject.instance()
            layers = []
            seen_ids = set()
            for layer_id in self._vc.get_flattened_layer_ids():
                if layer_id in seen_ids:
                    _log(f"[C] _apply_custom_order: WARNING duplicate layer {layer_id} — skipping", Qgis.Warning)
                    continue
                seen_ids.add(layer_id)
                lyr = proj.mapLayer(layer_id)
                if lyr:
                    layers.append(lyr)
            root.setHasCustomLayerOrder(True)
            root.setCustomLayerOrder(layers)
            _log(f"[C] _apply_custom_order: applied {len(layers)} layer(s) to custom order")
            try:
                self._iface.mapCanvas().refresh()
            except Exception as e:
                _log(f"[C] _apply_custom_order: mapCanvas.refresh failed: {e!r}", Qgis.Warning)
        except Exception as e:
            _log(f"[C] _apply_custom_order FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)
        finally:
            self._applying = False

    # ==================================================================
    # External control (plugin.py calls these during project load)
    # ==================================================================
    def set_apply_suspended(self, suspended: bool):
        _vlog_method("set_apply_suspended", "[C]")
        self._apply_suspended = bool(suspended)
        if not self._apply_suspended:
            self.request_apply()

    def sync_state_from_project(self):
        _vlog_method("sync_state_from_project", "[C]")
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
        try:
            root = QgsProject.instance().layerTreeRoot()
            if hasattr(root, 'hasCustomLayerOrderChanged'):
                root.hasCustomLayerOrderChanged.disconnect(self._on_has_custom_order_changed)
        except Exception:
            pass
        try:
            root = QgsProject.instance().layerTreeRoot()
            if hasattr(root, 'customLayerOrderChanged'):
                root.customLayerOrderChanged.disconnect(self._on_custom_order_changed)
        except Exception:
            pass

    # ==================================================================
    # Initial layer population (called by plugin.py on project load)
    # ==================================================================
    def get_ordered_project_layers(self):
        _vlog_method("get_ordered_project_layers", "[C]")
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
