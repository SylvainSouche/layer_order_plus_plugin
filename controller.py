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
        self._in_apply = False      # guard: our own setCustomLayerOrder triggers customLayerOrderChanged
        self._in_layer_add_remove = False  # guard: layer add/remove triggers order changes
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
        _vlog_method("_connect_qgis_signals")
        proj = QgsProject.instance()
        proj.layersAdded.connect(self._on_layers_added)
        proj.layersWillBeRemoved.connect(self._on_layers_removed)

    def _connect_view_signals(self):
        _vlog_method("_connect_view_signals")
        # Control checkbox — user toggles → sync to QGIS hasCustomLayerOrder
        self._view.control_toggled.connect(self._on_control_toggled)
        # Remove-empty checkbox — user toggles → persist to QgsProject
        self._view.remove_empty_toggled.connect(self._on_remove_empty_toggled)
        # Layer visibility toggled in View → sync to QgsLayerTreeLayer
        self._view.layer_visibility_toggled.connect(self._on_layer_visibility_toggled)

    def _connect_vc_signals(self):
        _vlog_method("_connect_vc_signals")
        # ViewController emits order_changed when the Model's flattened order changes
        self._vc.order_changed.connect(self._on_order_changed)

    def _connect_layer_tree_visibility(self):
        _vlog_method("_connect_layer_tree_visibility")
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
        _vlog_method("_on_has_custom_order_changed")
        """Stock Layer Order checkbox toggled → sync Plus checkbox.

        When the user activates 'Control rendering order' in the stock panel:
        - If Plus already has a saved tree (from project), PUSH our order to
          QGIS (we take priority — the user chose to use Plus).
        - If Plus has no saved tree, PULL from QGIS's customLayerOrder.

        This prevents the "B first A second" mismatch where the stock panel
        shows a different order than Plus.
        """
        self._sync_control_from_project()
        root = QgsProject.instance().layerTreeRoot()
        if root.hasCustomLayerOrder():
            our_order_ids = self._vc.get_flattened_layer_ids()
            qgis_order_ids = [lyr.id() for lyr in root.customLayerOrder()]
            if our_order_ids and our_order_ids != qgis_order_ids:
                # Plus has a saved tree — push our order to QGIS
                _log("_on_has_custom_order_changed: pushing Plus order to QGIS")
                self._apply_now_force()
            elif not our_order_ids and qgis_order_ids:
                # Plus is empty — pull from QGIS
                _log("_on_has_custom_order_changed: pulling from customLayerOrder")
                self._reconcile_order_with_qgis(qgis_order_ids)

    def _on_custom_order_changed(self):
        _vlog_method("_on_custom_order_changed")
        """Stock Layer Order panel moved a layer → update Model.

        Guards:
        - Skip if we're already reconciling (prevents feedback loop).
        - Skip if we're applying (our own _apply_custom_order triggers this
          signal — we must not reconcile our own changes).
        - Skip if layers are being added/removed (those trigger order changes
          that are NOT user reorders in the native panel).
        """
        if not self._view.is_control_enabled():
            return
        if self._in_reconcile:
            return
        if self._in_apply:
            return
        if self._in_layer_add_remove:
            return
        self._in_reconcile = True
        try:
            root = QgsProject.instance().layerTreeRoot()
            qgis_order = [lyr.id() for lyr in root.customLayerOrder()]
            our_order = self._vc.get_flattened_layer_ids()
            if qgis_order == our_order:
                return
            # Only reconcile if the layer SETS are the same (same layers,
            # different order). If layer sets differ, it's an add/remove,
            # not a reorder — let on_layers_added/removed handle it.
            if set(qgis_order) != set(our_order):
                _log("_on_custom_order_changed: layer sets differ (add/remove) — skipping reconcile")
                return
            _log(f"_on_custom_order_changed: QGIS order differs from Plus — reconciling")
            self._reconcile_order_with_qgis(qgis_order)
        except Exception as e:
            _log(f"_on_custom_order_changed FAILED: {e!r}", Qgis.Critical)
        finally:
            self._in_reconcile = False

    def _reconcile_order_with_qgis(self, qgis_order):
        _vlog_method("_reconcile_order_with_qgis")
        """Reconcile the Model's tree to match QGIS's flat order.

        RECURSIVE CONTIGUITY ALGORITHM (1.2.16):

        The tree is rebuilt recursively from the flat order + existing
        group structure. At each level:

        1. Walk the flat order slice for this level.
        2. For each existing child group at this level, find the min/max
           position of its DIRECT leaf children (not nested) in the flat
           order. This defines the group's "span" at this level.
        3. Layers between a group's min and max → belong to that group.
        4. Layers outside any group's span → stay at this level.
        5. Recurse into each group to rebuild its children.

        Key insight: we use DIRECT children (not all descendants) to
        compute spans. This respects nesting: a parent group's span is
        determined by its top-level children (which include subgroups),
        not by deeply nested layers.

        Rules (from user):
        1. Moving layers within a group → reorder leaves, group untouched.
        2. Moving a layer adjacent to a group → sibling, NOT a child.
        3. Moving a layer between a group's children → becomes part of group.
        4. Moving a layer out from between group's children → leaves the group.
        5. Empty groups preserved (reorder is not a delete).
        6. No warning popup — automatic, non-destructive.
        """
        from .model import GroupNode, LayerNode
        model = self._vc._model

        _log(f"_reconcile: qgis_order={qgis_order}")
        _log(f"_reconcile: before, our_order={self._vc.get_flattened_layer_ids()}")

        # Collect group metadata (id → (name, expanded)) for preservation
        group_meta = {}
        def collect_meta(node):
            if isinstance(node, GroupNode):
                group_meta[node.id] = (node.name, node.expanded)
                for ch in node.children:
                    collect_meta(ch)
        for top in model.get_root():
            collect_meta(top)

        # Get layer names from QGIS
        proj = QgsProject.instance()
        layer_names = {}
        for lid in qgis_order:
            lyr = proj.mapLayer(lid)
            layer_names[lid] = lyr.name() if lyr else lid

        # Recursive rebuild
        new_root = self._rebuild_level(
            qgis_order, 0, len(qgis_order),
            model.get_root(), group_meta, layer_names
        )

        with model.block_notifications():
            model._root = new_root
        self._vc._rebuild_view_from_model()
        _log(f"_reconcile: after, our_order={self._vc.get_flattened_layer_ids()}")

    def _rebuild_level(self, qgis_order, start, end, current_children,
                       group_meta, layer_names):
        """Rebuild one level of the tree from qgis_order[start:end].

        current_children: the current children at this level (from the old tree).
        Returns a list of LayerNode / GroupNode for this level.

        For each existing group at this level, we compute its span based on
        ALL its direct children (both layers and subgroups). For subgroups,
        we compute their span first (recursively), so the parent's span
        correctly covers the subgroup's entire block.
        """
        from .model import GroupNode, LayerNode

        # Step 1: compute spans for all groups at this level.
        # A group's span is [min, max] of all its direct children's positions.
        # For direct LayerNode children → their position in qgis_order.
        # For direct GroupNode children → we need their span first (recurse
        # to compute it). We do this bottom-up: process innermost groups first.

        # Collect groups at this level
        groups_here = [child for child in current_children if isinstance(child, GroupNode)]

        # For each group, collect the positions of its DIRECT children
        # (both layers and subgroups). For subgroups, recursively compute
        # their span first.
        group_spans = {}  # group_id → (min_pos, max_pos)

        def compute_group_span(group_node):
            """Compute the span of a group based on its direct children.
            Recursively computes subgroup spans first."""
            positions = []
            for ch in group_node.children:
                if isinstance(ch, LayerNode):
                    if ch.id in qgis_order:
                        pos = qgis_order.index(ch.id)
                        if start <= pos < end:
                            positions.append(pos)
                elif isinstance(ch, GroupNode):
                    # Recursively compute subgroup's span
                    sub_span = compute_group_span(ch)
                    if sub_span is not None:
                        positions.extend(sub_span)
            if positions:
                return (min(positions), max(positions))
            return None

        for grp in groups_here:
            span = compute_group_span(grp)
            if span is not None:
                group_spans[grp.id] = span

        # Step 2: walk qgis_order[start:end] and assign each layer to a group
        # or to this level. Sort groups by min position so we process them
        # in order (groups at the same level don't overlap).
        sorted_groups = sorted(group_spans.items(), key=lambda x: x[1][0])

        result = []
        i = start
        while i < end:
            lid = qgis_order[i]
            # Find which group (at this level) this layer belongs to
            assigned_group = None
            for gid, (gmin, gmax) in sorted_groups:
                if gmin <= i <= gmax:
                    assigned_group = gid
                    break

            if assigned_group is None:
                # This layer stays at the current level
                result.append(LayerNode(id=lid, name=layer_names.get(lid, lid)))
                i += 1
            else:
                # This layer is inside a group's span.
                # Take everything from i to the group's max position.
                gmin, gmax = group_spans[assigned_group]
                group_end = gmax + 1  # exclusive

                # Get the group's current children (for recursion)
                old_group_node = None
                for child in current_children:
                    if isinstance(child, GroupNode) and child.id == assigned_group:
                        old_group_node = child
                        break

                # Recurse: rebuild the group's children from the slice
                old_children = old_group_node.children if old_group_node else []
                new_children = self._rebuild_level(
                    qgis_order, i, group_end,
                    old_children, group_meta, layer_names
                )

                name, expanded = group_meta.get(assigned_group, ("Group", True))
                result.append(GroupNode(id=assigned_group, name=name,
                                        expanded=expanded, children=new_children))
                i = group_end

        return result

    def _find_conflicting_groups(self, qgis_order):
        """Return list of group_ids whose layers are NOT contiguous in qgis_order.

        A group is "contiguous" if all its descendant layers appear as a
        contiguous block in qgis_order (no outside layers interleaved).
        Non-contiguous groups must be deleted to honour the QGIS order.
        """
        _vlog_method("_find_conflicting_groups")
        from .model import GroupNode, LayerNode
        model = self._vc._model
        conflicting = []

        def collect_layer_ids(node):
            ids = []
            if isinstance(node, LayerNode):
                ids.append(node.id)
            elif isinstance(node, GroupNode):
                for ch in node.children:
                    ids.extend(collect_layer_ids(ch))
            return ids

        def walk_groups(node):
            if isinstance(node, GroupNode):
                group_layer_ids = collect_layer_ids(node)
                if group_layer_ids:
                    positions = [qgis_order.index(lid) for lid in group_layer_ids
                                 if lid in qgis_order]
                    if positions:
                        is_contiguous = (max(positions) - min(positions) + 1 == len(positions))
                        _log(f"  group '{node.name}': layers at positions {positions} "
                             f"→ {'contiguous' if is_contiguous else 'NON-CONTIGUOUS (conflicting)'}")
                        if not is_contiguous:
                            conflicting.append(node.id)
                for ch in node.children:
                    walk_groups(ch)

        for top in model.get_root():
            walk_groups(top)
        return conflicting

    def _reorder_all_levels_to_match_qgis(self, qgis_order):
        """Reorder ALL levels of the Model tree to match QGIS's flat order.

        For each node (top-level or within a group), sort its direct children
        by the minimum qgis_order position of any descendant layer. This
        handles:
        - Top-level group/layer reordering
        - Within-group layer reordering (when layers are swapped in native)
        - Nested group reordering

        Groups that have no layers in qgis_order are kept at their current
        relative position (appended at the end).
        """
        _vlog_method("_reorder_all_levels_to_match_qgis")
        from .model import GroupNode, LayerNode
        model = self._vc._model

        # Build mapping: layer_id → position in qgis_order
        layer_pos = {}
        for i, lid in enumerate(qgis_order):
            layer_pos[lid] = i

        def min_descendant_position(node):
            """Return the minimum qgis_order position of any descendant layer."""
            if isinstance(node, LayerNode):
                return layer_pos.get(node.id, 999999)
            if isinstance(node, GroupNode):
                positions = [min_descendant_position(ch) for ch in node.children]
                return min(positions) if positions else 999999
            return 999999

        def reorder_children(parent_children):
            """Sort a list of children by their min descendant qgis_order position."""
            # Sort by min position; items with no layers stay at the end (stable)
            parent_children.sort(key=lambda n: min_descendant_position(n))

        def reorder_recursive(node):
            if isinstance(node, GroupNode):
                reorder_children(node.children)
                for ch in node.children:
                    reorder_recursive(ch)

        with model.block_notifications():
            # Reorder top-level
            reorder_children(model._root)
            # Recurse into groups
            for top in model._root:
                reorder_recursive(top)
        self._vc._rebuild_view_from_model()

    def _on_tree_visibility_changed(self, node):
        _vlog_method("_on_tree_visibility_changed")
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
        _vlog_method("_on_layers_added")
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
        _vlog_method("_on_layers_removed")
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
        _vlog_method("connect_layer")
        """Public API: connect rename + visibility listeners for a layer.

        Called by plugin.py on project load for every existing layer.
        Idempotent — safe to call multiple times for the same layer.
        """
        self._connect_layer_rename(lyr)
        self._connect_layer_visibility(lyr)

    def _connect_layer_rename(self, lyr):
        _vlog_method("_connect_layer_rename")
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
        _vlog_method("_disconnect_layer_rename")
        entry = self._layer_rename_connections.pop(lid, None)
        if entry is None:
            return
        lyr, slot = entry
        try:
            lyr.nameChanged.disconnect(slot)
        except Exception:
            pass

    def _disconnect_all_layer_renames(self):
        _vlog_method("_disconnect_all_layer_renames")
        for lid in list(self._layer_rename_connections.keys()):
            self._disconnect_layer_rename(lid)

    def _on_layer_renamed(self, lid, lyr):
        _vlog_method("_on_layer_renamed")
        try:
            new_name = lyr.name()
        except RuntimeError:
            return
        self._vc.rename_layer(lid, new_name)

    # ==================================================================
    # Layer visibility sync (QgsLayerTreeLayer.visibilityChanged → ViewController)
    # ==================================================================
    def _find_layer_tree_layer(self, layer_id):
        _vlog_method("_find_layer_tree_layer")
        try:
            root = QgsProject.instance().layerTreeRoot()
            return root.findLayer(layer_id)
        except Exception:
            return None

    def _connect_layer_visibility(self, lyr):
        _vlog_method("_connect_layer_visibility")
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
        _vlog_method("_disconnect_layer_visibility")
        entry = self._visibility_connections.pop(lid, None)
        if entry is None:
            return
        ltl, slot = entry
        try:
            ltl.visibilityChanged.disconnect(slot)
        except Exception:
            pass

    def _disconnect_all_layer_visibilities(self):
        _vlog_method("_disconnect_all_layer_visibilities")
        for lid in list(self._visibility_connections.keys()):
            self._disconnect_layer_visibility(lid)

    def _get_effective_visibility(self, layer_id):
        _vlog_method("_get_effective_visibility")
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
        _vlog_method("_on_layer_visibility_external")
        """Layer visibility changed in the Layers panel → update Model.

        Uses EFFECTIVE visibility (considering parent group visibility) so
        that a layer inside an unchecked group shows as unchecked in Plus.
        """
        visible = self._get_effective_visibility(lid)
        self._vc.set_layer_visibility(lid, visible)

    def _on_layer_visibility_toggled(self, layer_id, checked):
        _vlog_method("_on_layer_visibility_toggled")
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
        _vlog_method("_on_control_toggled")
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
        _vlog_method("_sync_control_from_project")
        """Read QGIS hasCustomLayerOrder → set the View checkbox."""
        root = QgsProject.instance().layerTreeRoot()
        checked = bool(root.hasCustomLayerOrder())
        self._view.set_control_enabled(checked)

    # ==================================================================
    # remove-empty-groups setting (QgsProject entry ↔ View checkbox)
    # ==================================================================
    def _on_remove_empty_toggled(self, checked):
        _vlog_method("_on_remove_empty_toggled")
        try:
            QgsProject.instance().writeEntry("BetterLayerOrder", "removeEmptyGroups", bool(checked))
            QgsProject.instance().setDirty(True)
            _log(f"_on_remove_empty_toggled: removeEmptyGroups={checked}")
        except Exception as e:
            _log(f"_on_remove_empty_toggled: failed to persist: {e!r}", Qgis.Warning)

    def _sync_remove_empty_from_project(self):
        _vlog_method("_sync_remove_empty_from_project")
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
        _vlog_method("_on_order_changed")
        """Model's flattened order changed → debounce an apply to QGIS."""
        self.request_apply()

    def request_apply(self):
        _vlog_method("request_apply")
        if self._apply_suspended:
            return
        self._apply_timer.start(50)

    def _apply_now(self):
        _vlog_method("_apply_now")
        if self._apply_suspended:
            return
        self._apply_custom_order()

    def _apply_now_force(self):
        _vlog_method("_apply_now_force")
        """Apply even when suspended (used by control checkbox toggle)."""
        self._apply_custom_order()

    def _apply_custom_order(self):
        _vlog_method("_apply_custom_order")
        # Set _in_apply so _on_custom_order_changed knows this order change
        # came from us (not from the user reordering in the native panel).
        # The flag stays set for 200ms after apply completes to catch
        # queued customLayerOrderChanged signals (Qt may queue the signal
        # and fire it on the next event loop iteration, after _in_apply
        # would have been cleared by the finally block).
        self._in_apply = True
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
        finally:
            # Keep _in_apply True for 200ms to catch queued signals
            QTimer.singleShot(200, self._clear_in_apply)

    def _clear_in_apply(self):
        """Clear the _in_apply flag after a short delay."""
        self._in_apply = False

    # ==================================================================
    # External control (plugin.py calls these during project load)
    # ==================================================================
    def set_apply_suspended(self, suspended: bool):
        _vlog_method("set_apply_suspended")
        self._apply_suspended = bool(suspended)
        if not self._apply_suspended:
            self.request_apply()

    def sync_state_from_project(self):
        _vlog_method("sync_state_from_project")
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
        _vlog_method("get_ordered_project_layers")
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
