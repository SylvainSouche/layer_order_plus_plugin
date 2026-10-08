"""Layer Order Plus — Controller: QGIS ↔ Model, nothing else.

The Controller is the only object that talks to QGIS. It never sees the
View or the ViewController.

QGIS → Model
    * project open / new / close          → load the document, sync layer set
    * layer added / removed / renamed     → mirror into the Model
    * layer tree visibility               → mirror effective visibility
    * hasCustomLayerOrder                 → mirror ``control_enabled``
    * custom order changed elsewhere      → reconcile (see reconcile.py)

Model → QGIS
    * order_changed                       → setCustomLayerOrder (debounced)
    * document changes                    → tree_json project entry + dirty
    * remove_empty_groups setting         → project entry

Requests (wired by plugin.py from the ViewController)
    * set_layers_visible(ids, visible)    → layer tree checkboxes
    * set_control_enabled(on)             → setHasCustomLayerOrder

Every QGIS signal we cause ourselves fires synchronously while
``_applying`` is set; that one flag is how our own echoes are recognised.
"""
import traceback

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.core import Qgis, QgsLayerTree, QgsProject

from .logger import _log, _vlog
from .model import (
    EVENT_EXPANDED_CHANGED,
    EVENT_GROUP_RENAMED,
    EVENT_LAYER_RENAMED,
    EVENT_MODEL_LOADED,
    EVENT_ORDER_CHANGED,
    EVENT_SETTING_CHANGED,
    SETTING_REMOVE_EMPTY_GROUPS,
    LayerOrderModel,
)
from .reconcile import reconcile_tree

ENTRY_SCOPE = "BetterLayerOrder"      # historical project-entry scope, kept for compatibility
ENTRY_TREE = "tree_json"
ENTRY_REMOVE_EMPTY = "removeEmptyGroups"

APPLY_DEBOUNCE_MS = 50

_DOCUMENT_EVENTS = frozenset({
    EVENT_MODEL_LOADED, EVENT_ORDER_CHANGED, EVENT_GROUP_RENAMED,
    EVENT_LAYER_RENAMED, EVENT_EXPANDED_CHANGED,
})


class LayerOrderController(QObject):
    """Keeps the Model and QGIS in sync. See module docstring."""

    project_loaded = pyqtSignal()   # a project's document is now in the Model

    def __init__(self, model: LayerOrderModel, iface, parent=None):
        super().__init__(parent)
        self._model = model
        self._iface = iface
        self._applying = False   # we are writing to QGIS right now
        self._loading = False    # a project is being loaded into the Model

        self._apply_timer = self._timer(APPLY_DEBOUNCE_MS, self._apply_custom_order)
        # The stock Layer Order panel changes the order in two steps per drag
        # (insert at the new row — layer listed twice — then remove the old
        # row). Reconcile once, on the final order, one loop turn later.
        self._reconcile_timer = self._timer(0, self._reconcile_pending)
        self._dragged_hint: set = set()
        # New layers have no layer-tree node yet when layersAdded fires, so
        # placing them (next to their Layers-panel neighbours) waits a turn.
        self._place_timer = self._timer(0, self._place_pending_layers)
        self._pending_layers: list = []

        model.add_listener(self._on_model_event)
        self._connections = self._qgis_connections()
        for signal, slot in self._connections:
            signal.connect(slot)

    def _timer(self, ms: int, slot) -> QTimer:
        t = QTimer(self)
        t.setSingleShot(True)
        t.setInterval(ms)
        t.timeout.connect(slot)
        return t

    def _qgis_connections(self):
        proj = QgsProject.instance()
        root = proj.layerTreeRoot()
        return [
            (proj.layersAdded, self._on_layers_added),
            (proj.layersWillBeRemoved, self._on_layers_removed),
            (proj.cleared, self._on_project_cleared),
            (self._iface.projectRead, self.load_project),
            (self._iface.newProjectCreated, self.load_project),
            (root.nameChanged, self._on_tree_name_changed),
            (root.visibilityChanged, self._on_tree_visibility_changed),
            (root.hasCustomLayerOrderChanged, self._on_has_custom_order_changed),
            (root.customLayerOrderChanged, self._on_custom_order_changed),
        ]

    def teardown(self) -> None:
        """Disconnect from QGIS and save the document (plugin unload)."""
        for t in (self._apply_timer, self._reconcile_timer, self._place_timer):
            t.stop()
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except TypeError:
                pass  # already disconnected
        self._model.remove_listener(self._on_model_event)
        self._save_document()

    # ==================================================================
    # Requests (from the ViewController, via plugin.py)
    # ==================================================================
    def set_layers_visible(self, layer_ids: list, visible: bool) -> None:
        """Check/uncheck layers in the QGIS layer tree.

        Checking also checks the parent groups, so the layer really shows
        (the Model mirrors effective visibility). The Model follows through
        the visibilityChanged signal.
        """
        root = QgsProject.instance().layerTreeRoot()
        for lid in layer_ids:
            node = root.findLayer(lid)
            if node is None:
                continue
            if visible:
                node.setItemVisibilityCheckedParentRecursive(True)
            else:
                node.setItemVisibilityChecked(False)

    def set_control_enabled(self, enabled: bool) -> None:
        """Let Plus drive (or stop driving) the rendering order."""
        QgsProject.instance().layerTreeRoot().setHasCustomLayerOrder(bool(enabled))
        QgsProject.instance().setDirty(True)

    # ==================================================================
    # Project lifecycle
    # ==================================================================
    def _on_project_cleared(self) -> None:
        self._apply_timer.stop()
        self._pending_layers.clear()
        self._loading = True
        try:
            self._model.clear()
        finally:
            self._loading = False

    def load_project(self) -> None:
        """Load the current project's document into the Model.

        The saved tree gives the structure; QGIS gives the facts: layers
        that no longer exist are dropped, new ones are placed next to their
        Layers-panel neighbours, names and visibility come from QGIS.
        """
        proj = QgsProject.instance()
        root = proj.layerTreeRoot()
        model = self._model
        self._apply_timer.stop()
        self._pending_layers.clear()
        self._loading = True
        try:
            raw = proj.readEntry(ENTRY_SCOPE, ENTRY_TREE, "")[0] or ""
            remove_empty, ok = proj.readBoolEntry(ENTRY_SCOPE, ENTRY_REMOVE_EMPTY, True)
            with model.block_notifications():
                model.set_remove_empty_groups(remove_empty if ok else True)
                model.set_control_enabled(root.hasCustomLayerOrder())
                model.load_from_json(raw)
                self._sync_layer_set()
        except Exception as e:
            _log(f"[C] load_project FAILED: {e!r}\n{traceback.format_exc()}", Qgis.Critical)
        finally:
            self._loading = False
        self.project_loaded.emit()
        if model.get_control_enabled():
            self._apply_custom_order()   # QGIS may hold a stale order (1.2.22)
        self._refresh_native_panel()

    def _sync_layer_set(self) -> None:
        """Make the Model's layers exactly QGIS's layers, with QGIS's names/visibility."""
        model = self._model
        root = QgsProject.instance().layerTreeRoot()
        qgis_layers = {lyr.id(): lyr for lyr in QgsProject.instance().mapLayers().values()}
        for lid in list(model.iter_layer_ids()):
            if lid not in qgis_layers:
                model.remove_layer(lid)
        if not any(True for _ in model.iter_layer_ids()):
            # Fresh document: take QGIS's draw order as is
            for lyr in self._qgis_draw_order():
                model.add_layer(lyr.id(), lyr.name())
        else:
            self._place_layers([lid for lid in self._layer_panel_order()
                                if lid not in set(model.iter_layer_ids())])
        for lid, lyr in qgis_layers.items():
            model.rename_layer(lid, lyr.name())
            node = root.findLayer(lid)
            model.set_visibility(lid, node.isVisible() if node is not None else True)

    @staticmethod
    def _qgis_draw_order() -> list:
        root = QgsProject.instance().layerTreeRoot()
        if root.hasCustomLayerOrder() and root.customLayerOrder():
            return list(root.customLayerOrder())
        return list(root.layerOrder())

    @staticmethod
    def _layer_panel_order() -> list:
        """Layer ids in Layers-panel order (top first), ignoring any custom order."""
        return [n.layerId() for n in QgsProject.instance().layerTreeRoot().findLayers()]

    # ==================================================================
    # Layers added / removed / renamed / visibility
    # ==================================================================
    def _on_layers_added(self, layers) -> None:
        if self._loading:
            return
        self._pending_layers.extend(lyr.id() for lyr in layers)
        self._place_timer.start()

    def _place_pending_layers(self) -> None:
        pending, self._pending_layers = self._pending_layers, []
        known = set(self._model.iter_layer_ids())
        self._place_layers([lid for lid in dict.fromkeys(pending) if lid not in known])

    def _place_layers(self, layer_ids: list) -> None:
        """Insert layers next to their nearest Layers-panel neighbour already
        in the Model (before the one below it, else after the one above)."""
        proj = QgsProject.instance()
        root = proj.layerTreeRoot()
        order = self._layer_panel_order()
        known = set(self._model.iter_layer_ids())
        for lid in layer_ids:
            lyr = proj.mapLayer(lid)
            if lyr is None or lid in known:
                continue
            pos = order.index(lid) if lid in order else len(order)
            below = next((x for x in order[pos + 1:] if x in known), None)
            above = next((x for x in reversed(order[:pos]) if x in known), None)
            node = root.findLayer(lid)
            visible = node.isVisible() if node is not None else True
            if below is not None:
                self._model.add_layer_beside(lid, lyr.name(), visible, below, after=False)
            elif above is not None:
                self._model.add_layer_beside(lid, lyr.name(), visible, above, after=True)
            else:
                self._model.add_layer(lid, lyr.name(), visible)
            known.add(lid)

    def _on_layers_removed(self, layer_ids) -> None:
        if self._loading:
            return
        removed = set(layer_ids or [])
        self._pending_layers = [lid for lid in self._pending_layers if lid not in removed]
        for lid in removed:
            self._model.remove_layer(lid)

    def _on_tree_name_changed(self, node, name) -> None:
        if QgsLayerTree.isLayer(node):
            self._model.rename_layer(node.layerId(), name)

    def _on_tree_visibility_changed(self, node) -> None:
        layers = node.findLayers() if QgsLayerTree.isGroup(node) else [node]
        for ltl in layers:
            self._model.set_visibility(ltl.layerId(), ltl.isVisible())

    # ==================================================================
    # Rendering-order control
    # ==================================================================
    def _on_has_custom_order_changed(self, *_args) -> None:
        enabled = QgsProject.instance().layerTreeRoot().hasCustomLayerOrder()
        self._model.set_control_enabled(enabled)
        if self._applying or self._loading:
            return  # our own apply / the load refreshes when it is done
        if enabled:
            # Plus takes over: push its order (QGIS's may be stale)
            self._apply_custom_order()
        self._refresh_native_panel()

    def _refresh_native_panel(self) -> None:
        """Make QGIS's own Layer Order panel show the real order.

        That panel re-reads layerOrder() only on customLayerOrderChanged,
        never when hasCustomLayerOrder flips, and QGIS skips the signal when
        the order is unchanged. Turning control on (or off, or loading a
        project) can therefore leave it showing the previous order while the
        map uses another. Setting the same order through an empty one forces
        the signal; _applying keeps us from reacting to our own echo.
        """
        root = QgsProject.instance().layerTreeRoot()
        order = root.customLayerOrder()
        self._applying = True
        try:
            root.setCustomLayerOrder([])
            root.setCustomLayerOrder(order)
        finally:
            self._applying = False

    def _on_custom_order_changed(self) -> None:
        """Custom order changed outside Plus → schedule a reconcile.

        Skipped for our own writes, while loading, and while a Plus change
        is waiting to be applied (QGIS's order is then stale and reconciling
        would revert the user's edit). The stock panel's intermediate state
        (a layer listed twice) is only used as a hint of what was dragged.
        """
        if (self._applying or self._loading or self._apply_timer.isActive()
                or not self._model.get_control_enabled()):
            return
        seen: set = set()
        for lyr in QgsProject.instance().layerTreeRoot().customLayerOrder():
            if lyr.id() in seen:
                self._dragged_hint.add(lyr.id())
            seen.add(lyr.id())
        self._reconcile_timer.start()

    def _reconcile_pending(self) -> None:
        hint, self._dragged_hint = self._dragged_hint, set()
        if (self._applying or self._loading or self._apply_timer.isActive()
                or not self._model.get_control_enabled()):
            return
        qgis_order = [lyr.id() for lyr in QgsProject.instance().layerTreeRoot().customLayerOrder()]
        if qgis_order == self._model.get_flattened_layer_ids():
            return
        new_root = reconcile_tree(self._model.get_root(), qgis_order, hint)
        if new_root is None:
            # Not a pure reorder (layers being added/removed): those paths handle it
            _vlog("[C] reconcile skipped: QGIS order is not a permutation of Plus layers")
            return
        _log(f"[C] reconciled with QGIS order (moved hint: {sorted(hint)})")
        self._model.replace_root(new_root)

    def _apply_custom_order(self) -> None:
        """Write the Model's flattened order to QGIS's custom layer order."""
        self._apply_timer.stop()
        if self._applying or not self._model.get_control_enabled():
            return
        proj = QgsProject.instance()
        layers = [lyr for lyr in (proj.mapLayer(lid) for lid in self._model.get_flattened_layer_ids())
                  if lyr is not None]
        self._applying = True
        try:
            root = proj.layerTreeRoot()
            root.setHasCustomLayerOrder(True)
            root.setCustomLayerOrder(layers)
            _vlog(f"[C] applied {len(layers)} layer(s) to custom order")
        finally:
            self._applying = False
        self._iface.mapCanvas().refresh()

    # ==================================================================
    # Model → QGIS
    # ==================================================================
    def _on_model_event(self, event_type: str, payload: dict) -> None:
        if self._loading:
            return
        if event_type == EVENT_ORDER_CHANGED:
            self._apply_timer.start()
        if event_type in _DOCUMENT_EVENTS:
            self._save_document()
        elif event_type == EVENT_SETTING_CHANGED and payload["key"] == SETTING_REMOVE_EMPTY_GROUPS:
            proj = QgsProject.instance()
            proj.writeEntry(ENTRY_SCOPE, ENTRY_REMOVE_EMPTY, bool(payload["value"]))
            proj.setDirty(True)

    def _save_document(self) -> None:
        """Store the document in the project (saved to disk with the project)."""
        proj = QgsProject.instance()
        raw = self._model.serialize()
        if proj.readEntry(ENTRY_SCOPE, ENTRY_TREE, "")[0] != raw:
            proj.writeEntry(ENTRY_SCOPE, ENTRY_TREE, raw)
            proj.setDirty(True)
