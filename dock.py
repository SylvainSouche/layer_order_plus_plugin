# dock.py
"""Layer Order Plus dock widget.

This module owns the BetterLayerOrderDock class and the orchestration logic
(apply state machine, undo wiring, signal handling, project I/O). Pure tree
helpers live in tree_utils.py; icon helpers in icons.py; the BetterLayerTree
QTreeWidget subclass in tree_widget.py; the TreeStateCommand in undo.py.
"""
import json
import os
import traceback

from qgis.PyQt.QtCore import Qt, QTimer, QSize
from qgis.PyQt.QtGui import QKeySequence, QShortcut, QUndoStack
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QStyle,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QInputDialog,
    QMenu,
    QCheckBox,
)

from qgis.core import (
    QgsProject,
    QgsApplication,
    QgsMapLayer,
    QgsMessageLog,
    Qgis,
)

from .tree_utils import (
    ROLE_TYPE,
    ROLE_ID,
    TYPE_GROUP,
    TYPE_LAYER,
    new_group_id,
    collect_group_names,
    unique_group_name,
    expand_item_and_ancestors,
    find_group_item,
    find_layer_item,
    iter_all_layer_ids,
    index_in_parent,
    prune_empty_groups,
    apply_name_filter,
)
from .icons import (
    icon_group,
    icon_add_group,
    icon_remove_group,
    icon_rename_group,
    icon_for_layer,
)
from .tree_widget import BetterLayerTree
from .undo import TreeStateCommand


LOG_TAG = "LayerOrderPlus"

# Persistence format version (QualityOverhaul 2.4).
# v1: initial schema — children list, groups carry name/id/expanded/children,
#     layers carry id. Older saved trees (no "version" field) are treated as v1.
TREE_JSON_SCHEMA_VERSION = 1


def _log(msg, level=Qgis.Info):
    """Centralised log helper. Visible in View → Panels → Log Messages."""
    try:
        QgsMessageLog.logMessage(str(msg), LOG_TAG, level)
    except Exception:
        # If QgsMessageLog itself fails (very early load), fall back to print.
        try:
            print(f"[{LOG_TAG}] {msg}")
        except Exception:
            pass


# Backwards-compat aliases — keep the underscore-prefixed names working so
# any external code or future tests that imported them still resolve.
_new_group_id = new_group_id
_collect_group_names = collect_group_names
_unique_group_name = unique_group_name
_expand_item_and_ancestors = expand_item_and_ancestors
_find_group_item = find_group_item
_find_layer_item = find_layer_item
_iter_all_layer_ids = iter_all_layer_ids
_icon_group = icon_group
_icon_add_group = icon_add_group
_icon_remove_group = icon_remove_group
_icon_rename_group = icon_rename_group
_icon_for_layer = icon_for_layer

class BetterLayerOrderDock(QDockWidget):
    def __init__(self, iface):
        super().__init__("Layer Order Plus", iface.mainWindow())
        self.iface = iface
        self._save_cb = None

        # ---------------- guards ----------------
        self._apply_suspended = True     # block apply during project load
        self._loading = False            # block autosave while building UI
        self._in_undo = False

        # ---------------- persistent anchor ----------------
        self._anchor_type = None         # TYPE_LAYER | TYPE_GROUP
        self._anchor_id = None           # layer_id | group_id

        # ---------------- undo ----------------
        self._snapshot = ""
        self.undo_stack = QUndoStack(self)

        # ---------------- layer rename sync (QualityOverhaul 2.1) ----------------
        # Map layer_id → (QTreeWidgetItem, layer) so we can update the tree label
        # when the user renames a layer in the Layers panel.
        # We use the layer's nameChanged signal; connections are stored so we can
        # disconnect on unload / layer removal.
        self._layer_rename_connections = {}  # layer_id → (layer, bound_slot)

        # ---------------- visibility checkbox sync (QualityOverhaul 3.1) ----------------
        # When True, itemChanged handlers ignore check-state changes (we are the
        # source, so we don't need to propagate back to the layer tree).
        self._in_visibility_sync = False
        # Per-layer visibilityChanged connections: layer_id → (layer_tree_layer, slot)
        # We listen on QgsLayerTreeLayer (not QgsMapLayer) because that's where
        # the Layers panel toggles.
        self._visibility_connections = {}

        # safe apply debounce
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.timeout.connect(self._apply_now)

        # ---------------- UI ----------------
        rootw = QWidget()
        self.setWidget(rootw)

        lay = QVBoxLayout(rootw)
        head = QHBoxLayout()
        lay.addLayout(head)

        def _tb_btn(icon, tip):
            b = QPushButton()
            b.setIcon(icon)
            b.setToolTip(tip)
            b.setFlat(True)
            b.setFixedSize(28, 28)
            b.setIconSize(QSize(16, 16))
            return b

        self.btn_add_group = _tb_btn(icon_add_group(), "Create group")
        self.btn_rename_group = _tb_btn(icon_rename_group(), "Rename group")
        self.btn_del_group = _tb_btn(icon_remove_group(), "Delete group")

        head.addWidget(self.btn_add_group)
        head.addWidget(self.btn_rename_group)
        head.addWidget(self.btn_del_group)
        head.addStretch(1)

        # QualityOverhaul 3.3: case-insensitive name substring filter.
        # Hidden items stay in the tree (just not visible); clearing the
        # field restores the full tree.
        self.ed_filter = QLineEdit()
        self.ed_filter.setPlaceholderText("Filter by name…")
        self.ed_filter.setClearButtonEnabled(True)
        self.ed_filter.setToolTip(
            "Case-insensitive substring filter on layer and group names.\n"
            "Items are hidden, not removed — clear to restore."
        )
        lay.addWidget(self.ed_filter)

        self.tree = BetterLayerTree()
        self.tree.setHeaderHidden(True)
        # QualityOverhaul 3.5: explain drop rules to the user.
        self.tree.setToolTip(
            "Drag layers and groups to set draw order (top of tree = drawn on top).\n\n"
            "Drop rules:\n"
            "  • Drop ON a layer → creates a new group containing the target layer and the dropped items\n"
            "  • Drop ON a group → moves the dropped items into that group (top)\n"
            "  • Drop ABOVE an item → reorders just above that item\n"
            "  • Drop BELOW an item → reorders just below that item\n\n"
            "Right-click for: Create / Rename / Delete group, Expand / Collapse, Move to top / bottom.\n"
            "Double-click a group to expand or collapse it."
        )
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setUniformRowHeights(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setAnimated(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        lay.addWidget(self.tree)

        self.chk_control = QCheckBox("Control rendering order")
        self.chk_control.setToolTip(
            "When checked, this panel drives the map draw order (custom layer order). "
            "When unchecked, QGIS uses the default Layers-panel order."
        )
        lay.addWidget(self.chk_control)

        # QualityOverhaul 2.2: optional cleanup of empty groups after layer remove.
        # Persisted per-project via QgsProject entry "removeEmptyGroups" (default: True).
        self.chk_remove_empty = QCheckBox("Remove empty groups on layer delete")
        self.chk_remove_empty.setToolTip(
            "When checked, order groups that become empty after their last layer is "
            "removed are deleted automatically. When unchecked, empty groups are kept."
        )
        lay.addWidget(self.chk_remove_empty)

        # Local shortcuts when the dock has focus (plugin also hooks Edit menu / app shortcuts)
        for seq, slot in (
            (QKeySequence.StandardKey.Undo, self.undo_stack.undo),
            (QKeySequence.StandardKey.Redo, self.undo_stack.redo),
            (QKeySequence("Ctrl+Shift+Z"), self.undo_stack.redo),
        ):
            sc = QShortcut(seq, self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)

        # tree callbacks
        self.tree.set_state_provider(self._serialize_tree)
        self.tree.set_after_drop_callback(self._on_tree_changed_external)

        self.btn_add_group.clicked.connect(self.create_group_from_selection)
        self.btn_rename_group.clicked.connect(self.rename_selected_group)
        self.btn_del_group.clicked.connect(self.delete_selected_groups)
        self.chk_control.toggled.connect(self._on_control_toggled)
        self.chk_remove_empty.toggled.connect(self._on_remove_empty_toggled)
        self.ed_filter.textChanged.connect(self._on_filter_changed)

        self.tree.model().rowsMoved.connect(self._on_rows_moved)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        # QualityOverhaul 3.1: visibility checkbox propagation
        self.tree.itemChanged.connect(self._on_tree_item_changed)

        # anchor capture + toolbar enable state
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.currentItemChanged.connect(self._capture_anchor_from_current)

        self._update_group_actions_enabled()
        self._sync_control_from_project()
        self._sync_remove_empty_from_project()

        _log(f"dock initialised; chk_control exists={hasattr(self, 'chk_control')}; "
             f"is_control_enabled={self.is_control_enabled()}; "
             f"remove_empty_groups={self._should_remove_empty_groups()}")

    # ==================================================================
    # control rendering order (stock Layer Order parity)
    # ==================================================================
    def is_control_enabled(self) -> bool:
        return bool(self.chk_control.isChecked())

    def _sync_control_from_project(self):
        root = QgsProject.instance().layerTreeRoot()
        checked = bool(root.hasCustomLayerOrder())
        self.chk_control.blockSignals(True)
        self.chk_control.setChecked(checked)
        self.chk_control.blockSignals(False)
        self._apply_control_ui_state(checked)

    def _on_control_toggled(self, checked: bool):
        if self._loading:
            self._apply_control_ui_state(checked)
            return
        root = QgsProject.instance().layerTreeRoot()
        if checked:
            root.setHasCustomLayerOrder(True)
            self._apply_now_force()
        else:
            root.setHasCustomLayerOrder(False)
        self._apply_control_ui_state(checked)
        try:
            QgsProject.instance().setDirty(True)
        except Exception:
            pass

    def _apply_control_ui_state(self, enabled: bool):
        self.tree.setEnabled(enabled)
        self.btn_add_group.setEnabled(enabled)
        # Filter box also follows control state — filtering an inactive tree
        # is meaningless.
        self.ed_filter.setEnabled(enabled)
        if enabled:
            self._update_group_actions_enabled()
        else:
            self.btn_rename_group.setEnabled(False)
            self.btn_del_group.setEnabled(False)
            # Clear filter so the tree is fully visible when re-enabled
            self.ed_filter.blockSignals(True)
            self.ed_filter.clear()
            self.ed_filter.blockSignals(False)
        self.tree.setStyleSheet("" if enabled else "QTreeWidget { color: palette(disabled); }")
        # The empty-group cleanup checkbox follows control state — meaningless
        # when the panel is not driving order.
        self.chk_remove_empty.setEnabled(enabled)

    # ==================================================================
    # remove-empty-groups setting (QualityOverhaul 2.2)
    # ==================================================================
    def _on_remove_empty_toggled(self, checked: bool):
        if self._loading:
            return
        try:
            QgsProject.instance().writeEntry("BetterLayerOrder", "removeEmptyGroups", bool(checked))
            QgsProject.instance().setDirty(True)
            _log(f"_on_remove_empty_toggled: removeEmptyGroups={checked}")
        except Exception as e:
            _log(f"_on_remove_empty_toggled: failed to persist: {e!r}", Qgis.Warning)

    def _sync_remove_empty_from_project(self):
        """Read the persisted setting; default True when missing."""
        try:
            val, ok = QgsProject.instance().readEntry("BetterLayerOrder", "removeEmptyGroups", "1")
            checked = bool(int(val)) if ok and val not in ("", None) else True
        except Exception:
            checked = True
        self.chk_remove_empty.blockSignals(True)
        self.chk_remove_empty.setChecked(checked)
        self.chk_remove_empty.blockSignals(False)

    def _should_remove_empty_groups(self) -> bool:
        return bool(self.chk_remove_empty.isChecked())

    # ==================================================================
    # name filter (QualityOverhaul 3.3)
    # ==================================================================
    def _on_filter_changed(self, text: str):
        """Case-insensitive substring filter — delegates to tree_utils.apply_name_filter."""
        self.tree.blockSignals(True)
        try:
            apply_name_filter(self.tree, text)
        finally:
            self.tree.blockSignals(False)

    def _prune_empty_groups(self):
        """Remove empty groups from the tree. Returns count removed.

        Delegates to tree_utils.prune_empty_groups (QualityOverhaul 4.2 dedup).
        """
        removed = prune_empty_groups(self.tree)
        if removed:
            _log(f"_prune_empty_groups: removed {removed} empty group(s)")
        return removed

    # ==================================================================
    # external control
    # ==================================================================
    def set_apply_suspended(self, suspended: bool):
        self._apply_suspended = bool(suspended)
        if not self._apply_suspended:
            self.request_apply()

    def set_save_callback(self, cb):
        self._save_cb = cb

    def clear_tree_ui(self):
        self._loading = True
        self.tree.blockSignals(True)
        # Drop rename + visibility listeners — tree is being wiped.
        self._disconnect_all_layer_renames()
        self._disconnect_all_layer_visibilities()
        try:
            self.tree.clear()
            self._snapshot = ""
            self._anchor_type = None
            self._anchor_id = None
        finally:
            self.tree.blockSignals(False)
            self._loading = False

    # ==================================================================
    # anchor handling
    # ==================================================================
    def _capture_anchor_from_selection(self):
        sel = self.tree.selectedItems()
        if not sel:
            return
        self._set_anchor(sel[0])

    def _capture_anchor_from_current(self, current, previous):
        if current is None:
            return
        self._set_anchor(current)

    def _set_anchor(self, item: QTreeWidgetItem):
        t = item.data(0, ROLE_TYPE)
        if t == TYPE_LAYER:
            self._anchor_type = TYPE_LAYER
            self._anchor_id = item.data(0, ROLE_ID)
        elif t == TYPE_GROUP:
            self._anchor_type = TYPE_GROUP
            self._anchor_id = item.data(0, ROLE_ID)

    def _resolve_anchor_item(self):
        if self._anchor_type == TYPE_LAYER and self._anchor_id:
            it = self.find_layer_item(self._anchor_id)
            if it:
                return it
        if self._anchor_type == TYPE_GROUP and self._anchor_id:
            it = self.find_group_item(self._anchor_id)
            if it:
                return it

        it = self.tree.currentItem()
        if it:
            return it
        sel = self.tree.selectedItems()
        if sel:
            return sel[0]
        return None

    def find_layer_item(self, layer_id):
        """Delegates to tree_utils.find_layer_item (QualityOverhaul 4.2 dedup)."""
        return find_layer_item(self.tree, layer_id)

    def find_group_item(self, group_id):
        """Delegates to tree_utils.find_group_item (QualityOverhaul 4.2 dedup)."""
        return find_group_item(self.tree, group_id)

        for i in range(self.tree.topLevelItemCount()):
            r = walk(self.tree.topLevelItem(i))
            if r:
                return r
        return None

    # ==================================================================
    # tree helpers
    # ==================================================================
    def _index_in_parent(self, item: QTreeWidgetItem) -> int:
        """Delegates to tree_utils.index_in_parent (QualityOverhaul 4.2 dedup)."""
        return index_in_parent(self.tree, item)

    def iter_all_layer_ids(self):
        """Yield every layer id in the tree. Delegates to tree_utils."""
        yield from iter_all_layer_ids(self.tree)

    def _flatten_to_qgs_layers(self):
        proj = QgsProject.instance()
        out = []

        def walk(it):
            t = it.data(0, ROLE_TYPE)
            if t == TYPE_LAYER:
                lyr = proj.mapLayer(it.data(0, ROLE_ID))
                if lyr:
                    out.append(lyr)
                return
            for i in range(it.childCount()):
                walk(it.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

        return out

    def _make_layer_item(self, lyr) -> QTreeWidgetItem:
        """Build a layer QTreeWidgetItem (QualityOverhaul 4.2 dedup).

        Shared by _add_layer_item (append) and _insert_layer_item (insert at idx).
        """
        it = QTreeWidgetItem([lyr.name()])
        it.setData(0, ROLE_TYPE, TYPE_LAYER)
        it.setData(0, ROLE_ID, lyr.id())
        it.setFlags((it.flags() | Qt.ItemFlag.ItemIsUserCheckable) & ~Qt.ItemFlag.ItemIsEditable)
        it.setIcon(0, icon_for_layer(lyr))
        # QualityOverhaul 3.1: initial visibility from the layer tree.
        self._set_layer_item_check_state(it, lyr)
        self._connect_layer_rename(lyr)
        self._connect_layer_visibility(lyr)
        return it

    def _add_layer_item(self, parent, lyr):
        """Append a layer item to `parent` (or top-level if parent is None)."""
        it = self._make_layer_item(lyr)
        if parent is None:
            self.tree.addTopLevelItem(it)
        else:
            parent.addChild(it)
        return it

    def _insert_layer_item(self, parent, idx, lyr):
        """Insert a layer item at `idx` under `parent` (or top-level if None)."""
        it = self._make_layer_item(lyr)
        if parent is None:
            self.tree.insertTopLevelItem(idx, it)
        else:
            parent.insertChild(idx, it)
        return it

    # ------------------------------------------------------------------
    # Layer rename sync (QualityOverhaul 2.1)
    # ------------------------------------------------------------------
    def _connect_layer_rename(self, lyr):
        """Connect layer.nameChanged once per layer; idempotent."""
        if lyr is None:
            return
        lid = lyr.id()
        if lid in self._layer_rename_connections:
            return  # already connected
        try:
            slot = lambda *_a, _lid=lid, _lyr=lyr: self._on_layer_renamed(_lid, _lyr)
            lyr.nameChanged.connect(slot)
            self._layer_rename_connections[lid] = (lyr, slot)
        except Exception as e:
            _log(f"_connect_layer_rename: failed for {lid}: {e!r}", Qgis.Warning)

    def _disconnect_layer_rename(self, lid):
        """Disconnect (and forget) the rename listener for a layer id."""
        entry = self._layer_rename_connections.pop(lid, None)
        if entry is None:
            return
        lyr, slot = entry
        try:
            lyr.nameChanged.disconnect(slot)
        except Exception:
            pass  # already disconnected or layer gone

    def _on_layer_renamed(self, lid, lyr):
        """Update the tree label when a layer is renamed externally."""
        try:
            new_name = lyr.name()
        except RuntimeError:
            # Layer was deleted; the on_layers_removed path will clean up.
            return
        it = self.find_layer_item(lid)
        if it is None:
            return
        try:
            if it.text(0) != new_name:
                it.setText(0, new_name)
                _log(f"_on_layer_renamed: {lid} → '{new_name}'")
        except RuntimeError:
            pass  # item already gone

    def _disconnect_all_layer_renames(self):
        """Disconnect every rename listener — used on unload / project clear."""
        for lid in list(self._layer_rename_connections.keys()):
            self._disconnect_layer_rename(lid)

    # ------------------------------------------------------------------
    # Visibility checkbox sync (QualityOverhaul 3.1)
    # ------------------------------------------------------------------
    def _find_layer_tree_layer(self, layer_id):
        """Return the QgsLayerTreeLayer for a layer id, or None."""
        try:
            root = QgsProject.instance().layerTreeRoot()
            return root.findLayer(layer_id)
        except Exception:
            return None

    def _set_layer_item_check_state(self, item, lyr):
        """Initialise a layer item's check state from the layer tree."""
        try:
            ltl = self._find_layer_tree_layer(lyr.id())
            visible = bool(ltl.itemVisibilityChecked()) if ltl is not None else True
        except Exception:
            visible = True
        # Suppress itemChanged during programmatic state set
        was = self._in_visibility_sync
        self._in_visibility_sync = True
        try:
            item.setCheckState(0, Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)
        finally:
            self._in_visibility_sync = was

    def _connect_layer_visibility(self, lyr):
        """Connect QgsLayerTreeLayer.visibilityChanged for this layer (idempotent)."""
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

    def _on_layer_visibility_external(self, lid):
        """A layer's visibility changed in the Layers panel — sync the tree item."""
        if self._in_visibility_sync:
            return
        it = self.find_layer_item(lid)
        if it is None:
            return
        ltl = self._find_layer_tree_layer(lid)
        if ltl is None:
            return
        try:
            visible = bool(ltl.itemVisibilityChecked())
        except RuntimeError:
            return
        self._in_visibility_sync = True
        try:
            it.setCheckState(0, Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)
            # Group tri-state is updated automatically by Qt (ItemIsAutoTristate).
        finally:
            self._in_visibility_sync = False

    def _on_tree_item_changed(self, item, column):
        """Handle user checkbox toggles. Propagate to the layer tree.

        Note: with ItemIsAutoTristate on groups, Qt propagates parent toggles
        to children automatically and fires itemChanged for each child layer.
        So we only need to handle TYPE_LAYER here — group toggles resolve
        themselves through the per-layer handlers.
        """
        if column != 0:
            return
        if self._in_visibility_sync:
            return
        t = item.data(0, ROLE_TYPE)
        if t == TYPE_LAYER:
            self._on_layer_check_toggled(item)
        # Group check-state changes are driven by auto-tristate and resolved
        # via the per-layer itemChanged fires that follow.

    def _on_layer_check_toggled(self, item):
        lid = item.data(0, ROLE_ID)
        checked = item.checkState(0) == Qt.CheckState.Checked
        ltl = self._find_layer_tree_layer(lid)
        if ltl is None:
            return
        try:
            ltl.setItemVisibilityChecked(checked)
            # Trigger a map refresh via the layer tree (QGIS handles canvas refresh)
            try:
                root = QgsProject.instance().layerTreeRoot()
                root.emitVisibilityChanged()
            except Exception:
                pass
            _log(f"_on_layer_check_toggled: {lid} visible={checked}")
        except Exception as e:
            _log(f"_on_layer_check_toggled FAILED: {e!r}", Qgis.Critical)
            return
        # Group tri-state is updated automatically by Qt (ItemIsAutoTristate).

    def _ordered_project_layers(self):
        """
        Return project layers in the order QGIS would actually draw them.

        Preference (QualityOverhaul 1.2):
          1. root.customLayerOrder()           — when hasCustomLayerOrder is True
          2. root.layerOrder()                 — current legend/draw order
          3. proj.mapLayers().values()         — final fallback (unordered)

        Falls back silently and logs which path was taken, so the log tab
        shows why a project opened in a particular order.
        """
        proj = QgsProject.instance()
        root = proj.layerTreeRoot()
        try:
            if root.hasCustomLayerOrder():
                clo = root.customLayerOrder() or []
                if clo:
                    _log(f"_ordered_project_layers: using customLayerOrder ({len(clo)} layers)")
                    return list(clo)
            if hasattr(root, "layerOrder"):
                lo = root.layerOrder() or []
                if lo:
                    _log(f"_ordered_project_layers: using layerOrder ({len(lo)} layers)")
                    return list(lo)
        except Exception as e:
            _log(f"_ordered_project_layers: order API failed ({e!r}); falling back to mapLayers", Qgis.Warning)
        ml = list(proj.mapLayers().values())
        _log(f"_ordered_project_layers: fallback to mapLayers ({len(ml)} layers, unordered)", Qgis.Warning)
        return ml

    def _populate_all_layers_top_level(self):
        for lyr in self._ordered_project_layers():
            self._add_layer_item(None, lyr)

    def _append_missing_layers(self):
        existing = set(self.iter_all_layer_ids())
        for lyr in self._ordered_project_layers():
            if lyr.id() not in existing:
                self._add_layer_item(None, lyr)

    # ==================================================================
    # group operations (FIXES THE CRASH)
    # ==================================================================
    def create_group_from_selection(self):
        if not self.is_control_enabled():
            return
        before = self._snapshot or self._serialize_tree()

        default_name = unique_group_name(self.tree, "New group")
        name, ok = QInputDialog.getText(self, "New group", "Group name:", text=default_name)
        if not ok:
            return
        name = (name or "").strip() or default_name
        # If user kept a name that collides, make it unique
        if name in collect_group_names(self.tree):
            name = unique_group_name(self.tree, name)

        selected = self.tree.selectedItems() or []
        selected_ids = {id(x) for x in selected}

        moving = [it for it in selected if (it.parent() is None or id(it.parent()) not in selected_ids)]

        grp = QTreeWidgetItem([name])
        grp.setData(0, ROLE_TYPE, TYPE_GROUP)
        grp.setData(0, ROLE_ID, new_group_id())
        try:
            grp.setFlags((grp.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate) & ~Qt.ItemFlag.ItemIsEditable)
        except Exception:
            grp.setFlags((grp.flags() | Qt.ItemFlag.ItemIsUserCheckable) & ~Qt.ItemFlag.ItemIsEditable)
        grp.setIcon(0, icon_group())
        grp.setCheckState(0, Qt.CheckState.Checked)  # recomputed once children added

        anchor = self._resolve_anchor_item()
        if anchor is not None:
            dest_parent = anchor if anchor.data(0, ROLE_TYPE) == TYPE_GROUP else anchor.parent()
            dest_index = 0 if dest_parent is None else dest_parent.childCount()
            if dest_parent is None:
                dest_index = self.tree.indexOfTopLevelItem(anchor) + 1
                self.tree.insertTopLevelItem(dest_index, grp)
            else:
                dest_parent.insertChild(dest_index, grp)
        else:
            self.tree.addTopLevelItem(grp)

        # Move selected items into the new group (if any)
        for it in sorted(moving, key=self._index_in_parent, reverse=True):
            p = it.parent()
            if p is None:
                taken = self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(it))
            else:
                taken = p.takeChild(p.indexOfChild(it))
            if taken is not None:
                grp.insertChild(0, taken)

        expand_item_and_ancestors(grp)
        self.tree.setCurrentItem(grp)
        self.tree.scrollToItem(grp)

        self.request_apply()
        after = self._serialize_tree()
        self._push_undo(before, after, "Create group")
        self._autosave()

    def _selected_groups(self):
        return [it for it in self.tree.selectedItems() if it.data(0, ROLE_TYPE) == TYPE_GROUP]

    def _item_depth(self, item):
        d = 0
        cur = item
        while cur is not None:
            d += 1
            cur = cur.parent()
        return d

    def _unwrap_and_remove_group(self, cur):
        """Move children out in place, then remove the group item."""
        parent = cur.parent()
        insert_at = self._index_in_parent(cur)
        children = []
        while cur.childCount():
            children.append(cur.takeChild(0))
        if parent is None:
            idx = self.tree.indexOfTopLevelItem(cur)
            if idx < 0:
                return
            self.tree.takeTopLevelItem(idx)
        else:
            parent.takeChild(parent.indexOfChild(cur))
        for ch in children:
            if parent is None:
                self.tree.insertTopLevelItem(insert_at, ch)
            else:
                parent.insertChild(insert_at, ch)
            insert_at += 1

    def delete_selected_groups(self):
        if not self.is_control_enabled():
            return
        groups = self._selected_groups()
        if not groups:
            return
        before = self._snapshot or self._serialize_tree()
        # deepest first so nested selected groups are removed before parents
        groups.sort(key=self._item_depth, reverse=True)
        removed = 0
        for cur in groups:
            # still in tree?
            if cur.treeWidget() is not self.tree:
                continue
            self._unwrap_and_remove_group(cur)
            removed += 1
        if removed == 0:
            return
        self.request_apply()
        after = self._serialize_tree()
        self._push_undo(before, after, "Delete group" if removed == 1 else f"Delete {removed} groups")
        self._autosave()
        self._update_group_actions_enabled()

    def rename_selected_group(self):
        if not self.is_control_enabled():
            return
        groups = self._selected_groups()
        if len(groups) != 1:
            return
        grp = groups[0]
        before = self._snapshot or self._serialize_tree()
        current = grp.text(0)
        name, ok = QInputDialog.getText(self, "Rename group", "Group name:", text=current)
        if not ok:
            return
        name = (name or "").strip()
        if not name or name == current:
            return
        # allow same name as self; uniquify only against others
        others = collect_group_names(self.tree) - {current}
        if name in others:
            name = unique_group_name(self.tree, name)
        grp.setText(0, name)
        self.request_apply()
        after = self._serialize_tree()
        self._push_undo(before, after, "Rename group")
        self._autosave()

    def _on_item_double_clicked(self, item, column):
        if not self.is_control_enabled():
            return
        if item is None:
            return
        if item.data(0, ROLE_TYPE) == TYPE_GROUP:
            item.setExpanded(not item.isExpanded())
        # layers: do nothing (no rename, no toggle)

    def _on_tree_context_menu(self, pos):
        if not self.is_control_enabled():
            return
        menu = QMenu(self)
        act_create = menu.addAction(icon_add_group(), "Create group")
        act_rename = menu.addAction(icon_rename_group(), "Rename group")
        act_delete = menu.addAction(icon_remove_group(), "Delete group")
        menu.addSeparator()
        act_expand = menu.addAction("Expand group")
        act_collapse = menu.addAction("Collapse group")
        menu.addSeparator()
        act_top = menu.addAction("Move to top")
        act_bottom = menu.addAction("Move to bottom")

        groups = self._selected_groups()
        sel = self.tree.selectedItems()
        # if right-click on an item not in selection, select it first
        under = self.tree.itemAt(pos)
        if under is not None and under not in sel:
            self.tree.clearSelection()
            under.setSelected(True)
            self.tree.setCurrentItem(under)
            groups = self._selected_groups()
            sel = self.tree.selectedItems()

        act_rename.setEnabled(len(groups) == 1)
        act_delete.setEnabled(len(groups) >= 1)
        # Expand/Collapse: enabled if at least one group is selected.
        # If clicked outside any item but groups exist in tree, applies to all groups.
        act_expand.setEnabled(len(groups) >= 1)
        act_collapse.setEnabled(len(groups) >= 1)
        # Move to top/bottom: enabled if at least one item is selected and the
        # selection isn't already at the boundary.
        act_top.setEnabled(bool(sel))
        act_bottom.setEnabled(bool(sel))

        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen == act_create:
            self.create_group_from_selection()
        elif chosen == act_rename:
            self.rename_selected_group()
        elif chosen == act_delete:
            self.delete_selected_groups()
        elif chosen == act_expand:
            for g in groups:
                g.setExpanded(True)
        elif chosen == act_collapse:
            for g in groups:
                g.setExpanded(False)
        elif chosen == act_top:
            self._move_selected_to_boundary(to_top=True)
        elif chosen == act_bottom:
            self._move_selected_to_boundary(to_top=False)

    def _move_selected_to_boundary(self, to_top: bool):
        """Move all selected items to the top (or bottom) of their respective parents."""
        if not self.is_control_enabled():
            return
        sel = self.tree.selectedItems()
        if not sel:
            return
        before = self._snapshot or self._serialize_tree()
        # Sort by current index, descending for take (so indices don't shift under us).
        # Group by parent so we operate within each parent's range.
        try:
            self.tree.blockSignals(True)
            for it in sorted(sel, key=self._index_in_parent, reverse=True):
                p = it.parent()
                if p is None:
                    cur_idx = self.tree.indexOfTopLevelItem(it)
                    taken = self.tree.takeTopLevelItem(cur_idx)
                    if to_top:
                        self.tree.insertTopLevelItem(0, taken)
                    else:
                        self.tree.insertTopLevelItem(self.tree.topLevelItemCount(), taken)
                else:
                    cur_idx = p.indexOfChild(it)
                    taken = p.takeChild(cur_idx)
                    if to_top:
                        p.insertChild(0, taken)
                    else:
                        p.insertChild(p.childCount(), taken)
        finally:
            self.tree.blockSignals(False)

        # Re-select the moved items (selection is lost on take).
        for it in sel:
            try:
                it.setSelected(True)
            except RuntimeError:
                pass

        after = self._serialize_tree()
        if before != after:
            self.request_apply()
            self._push_undo(before, after, "Move to top" if to_top else "Move to bottom")
            self._autosave()
            _log(f"_move_selected_to_boundary({'top' if to_top else 'bottom'}): "
                 f"moved {len(sel)} item(s)")

    def _on_selection_changed(self):
        self._capture_anchor_from_selection()
        self._update_group_actions_enabled()

    def _update_group_actions_enabled(self):
        groups = self._selected_groups()
        n = len(groups)
        if hasattr(self, "btn_rename_group"):
            self.btn_rename_group.setEnabled(n == 1)
        if hasattr(self, "btn_del_group"):
            self.btn_del_group.setEnabled(n >= 1)

    # ==================================================================
    # persistence
    # ==================================================================
    def _autosave(self):
        if not self._save_cb:
            return
        if self._loading or self._in_undo or self._apply_suspended:
            return
        self._save_cb(self._serialize_tree())

    def _serialize_tree(self) -> str:
        def ser_item(item):
            t = item.data(0, ROLE_TYPE)
            if t == TYPE_GROUP:
                try:
                    expanded = bool(item.isExpanded())
                except Exception:
                    expanded = True
                return {
                    "type": TYPE_GROUP,
                    "id": item.data(0, ROLE_ID),
                    "name": item.text(0),
                    "expanded": expanded,
                    "children": [ser_item(item.child(i)) for i in range(item.childCount())],
                }
            return {"type": TYPE_LAYER, "id": item.data(0, ROLE_ID)}

        return json.dumps(
            {
                "version": TREE_JSON_SCHEMA_VERSION,
                "children": [ser_item(self.tree.topLevelItem(i))
                             for i in range(self.tree.topLevelItemCount())],
            },
            ensure_ascii=False
        )

    # ==================================================================
    # loading
    # ==================================================================
    def load_from_project(self, raw_json: str):
        _log(f"load_from_project: json_len={len(raw_json) if raw_json else 0}")
        self._loading = True
        self.tree.blockSignals(True)
        # Drop rename + visibility listeners from the previous tree — they'll
        # be reconnected as items are built below.
        self._disconnect_all_layer_renames()
        self._disconnect_all_layer_visibilities()
        try:
            self.tree.clear()
            if not raw_json:
                self._populate_all_layers_top_level()
                self._snapshot = self._serialize_tree()
                return

            obj = json.loads(raw_json)
            # Schema version handling (QualityOverhaul 2.4).
            # Missing "version" → treat as v1 (legacy pre-1.0.19 saves).
            schema_ver = obj.get("version")
            if schema_ver is None:
                _log("load_from_project: no schema version in JSON — treating as v1 (legacy)")
                schema_ver = 1
            elif schema_ver != TREE_JSON_SCHEMA_VERSION:
                _log(f"load_from_project: schema version {schema_ver} != current "
                     f"{TREE_JSON_SCHEMA_VERSION} — attempting v1 load anyway", Qgis.Warning)
            else:
                _log(f"load_from_project: schema version {schema_ver}")

            proj = QgsProject.instance()
            # Collect (group_item, expanded_state) pairs to restore after the tree
            # is fully built — setting expanded during construction is unreliable.
            expand_targets = []

            def add_child(parent_item, child_item):
                if parent_item is None:
                    self.tree.addTopLevelItem(child_item)
                else:
                    parent_item.addChild(child_item)

            def build(parent, node):
                if node["type"] == TYPE_GROUP:
                    it = QTreeWidgetItem([node.get("name", "Group")])
                    it.setData(0, ROLE_TYPE, TYPE_GROUP)
                    it.setData(0, ROLE_ID, node.get("id", new_group_id()))
                    try:
                        it.setFlags((it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate) & ~Qt.ItemFlag.ItemIsEditable)
                    except Exception:
                        it.setFlags((it.flags() | Qt.ItemFlag.ItemIsUserCheckable) & ~Qt.ItemFlag.ItemIsEditable)
                    it.setIcon(0, icon_group())
                    it.setCheckState(0, Qt.CheckState.Checked)  # auto-tristate recomputes once children are added
                    add_child(parent, it)
                    # Default to expanded=True for legacy saves (matches pre-1.0.19 behaviour);
                    # otherwise honour the saved state.
                    expanded = node.get("expanded", True)
                    expand_targets.append((it, bool(expanded)))
                    for ch in node.get("children", []):
                        build(it, ch)
                else:
                    lyr = proj.mapLayer(node["id"])
                    if lyr:
                        self._add_layer_item(parent, lyr)

            for top in obj.get("children", []):
                build(None, top)

            self._append_missing_layers()

            # Restore expanded state now that all items exist.
            for it, expanded in expand_targets:
                try:
                    it.setExpanded(expanded)
                except RuntimeError:
                    pass  # item already gone

            self._snapshot = self._serialize_tree()
        finally:
            self.tree.blockSignals(False)
            self._loading = False
            self._sync_control_from_project()
            self._sync_remove_empty_from_project()

    # ==================================================================
    # layer add/remove (ANCHOR-BASED)
    # ==================================================================
    def on_layers_added(self, layers):
        _log(f"on_layers_added: received {len(layers) if layers else 0} layer(s); "
             f"_loading={self._loading}, _in_undo={self._in_undo}, "
             f"is_control_enabled={self.is_control_enabled()}")
        if self._loading or self._in_undo:
            _log("on_layers_added: skipped (loading or undo in progress)", Qgis.Warning)
            return

        try:
            before = self._serialize_tree()
            existing = set(self.iter_all_layer_ids())
            new_layers = [l for l in layers if l.id() not in existing]
            if not new_layers:
                _log("on_layers_added: no new layers (all already present)")
                return

            anchor = self._resolve_anchor_item()
            dest_parent = None
            dest_index = 0

            if anchor:
                if anchor.data(0, ROLE_TYPE) == TYPE_LAYER:
                    dest_parent = anchor.parent()
                    dest_index = self._index_in_parent(anchor)
                else:
                    dest_parent = anchor
                    dest_index = 0

            for lyr in new_layers:
                self._insert_layer_item(dest_parent, dest_index, lyr)
                dest_index += 1

            self.request_apply()
            after = self._serialize_tree()
            self._push_undo(before, after, "Add layers")
            self._autosave()
            _log(f"on_layers_added: inserted {len(new_layers)} layer(s); "
                 f"tree now has {sum(1 for _ in self.iter_all_layer_ids())} layer items")
        except Exception as e:
            _log(f"on_layers_added FAILED: {e!r}", Qgis.Critical)
            import traceback
            _log(traceback.format_exc(), Qgis.Critical)

    def on_layers_removed(self, layer_ids):
        _log(f"on_layers_removed: received {len(layer_ids) if layer_ids else 0} id(s); "
             f"_loading={self._loading}, _in_undo={self._in_undo}")
        if self._loading or self._in_undo:
            return

        try:
            before = self._serialize_tree()
            remove_set = set(layer_ids or [])

            # Disconnect rename + visibility listeners for the layers being removed.
            for lid in remove_set:
                self._disconnect_layer_rename(lid)
                self._disconnect_layer_visibility(lid)

            def prune(parent):
                i = 0
                while i < parent.childCount():
                    ch = parent.child(i)
                    t = ch.data(0, ROLE_TYPE)
                    if t == TYPE_LAYER and ch.data(0, ROLE_ID) in remove_set:
                        parent.takeChild(i)
                        continue
                    if t == TYPE_GROUP:
                        prune(ch)
                        # remove empty groups? keep them; do nothing
                    i += 1

            self._loading = True
            self.tree.blockSignals(True)
            try:
                for i in reversed(range(self.tree.topLevelItemCount())):
                    top = self.tree.topLevelItem(i)
                    t = top.data(0, ROLE_TYPE)
                    if t == TYPE_LAYER and top.data(0, ROLE_ID) in remove_set:
                        self.tree.takeTopLevelItem(i)
                    elif t == TYPE_GROUP:
                        prune(top)
                # QualityOverhaul 2.2: optionally drop groups that became empty.
                if self._should_remove_empty_groups():
                    self._prune_empty_groups()
            finally:
                self.tree.blockSignals(False)
                self._loading = False

            self.request_apply()
            after = self._serialize_tree()
            self._push_undo(before, after, "Remove layers")
            self._autosave()
            _log(f"on_layers_removed: pruned {len(remove_set)} id(s); "
                 f"tree now has {sum(1 for _ in self.iter_all_layer_ids())} layer items")
        except Exception as e:
            _log(f"on_layers_removed FAILED: {e!r}", Qgis.Critical)
            import traceback
            _log(traceback.format_exc(), Qgis.Critical)

    # ==================================================================
    # apply to QGIS
    # ==================================================================
    def request_apply(self):
        # Normal path: debounce. Blocked during project load / suspend.
        # Undo uses _apply_now_force() so order is restored immediately.
        if self._apply_suspended or self._loading or self._in_undo:
            return
        self._apply_timer.start(50)

    def _apply_now(self):
        if self._apply_suspended or self._loading or self._in_undo:
            return
        self._apply_custom_order()

    def _apply_now_force(self):
        """Apply custom layer order even during undo/loading guards."""
        self._apply_custom_order()

    def _apply_custom_order(self):
        try:
            root = QgsProject.instance().layerTreeRoot()
            if not self.is_control_enabled():
                # Panel must not force custom order while the checkbox is off
                root.setHasCustomLayerOrder(False)
                _log("_apply_custom_order: skipped (control unchecked)")
                return
            layers = self._flatten_to_qgs_layers()
            root.setHasCustomLayerOrder(True)
            root.setCustomLayerOrder(layers)
            _log(f"_apply_custom_order: applied {len(layers)} layer(s) to custom order")

            try:
                self.iface.mapCanvas().refresh()
            except Exception as e:
                _log(f"_apply_custom_order: mapCanvas.refresh failed: {e!r}", Qgis.Warning)
        except Exception as e:
            _log(f"_apply_custom_order FAILED: {e!r}", Qgis.Critical)
            import traceback
            _log(traceback.format_exc(), Qgis.Critical)

    # ==================================================================
    # events (undo hooks)
    # ==================================================================
    def _on_rows_moved(self, *args, **kwargs):
        if not self.is_control_enabled():
            return
        if self._in_undo or self._loading:
            return
        # Dual-undo guard (QualityOverhaul 1.1 / 4.5):
        # Custom dropEvent already pushed undo via _on_tree_changed_external.
        # rowsMoved also fires because take/insert manipulates the model, so
        # without this guard a single drag would push TWO undo entries.
        if getattr(self.tree, "_just_custom_dropped", False):
            _log("_on_rows_moved: skipped (custom drop already pushed undo)")
            return
        # InternalMove not always triggers our custom drop path; take snapshot-based undo.
        before = self._snapshot or self._serialize_tree()
        after = self._serialize_tree()
        self.request_apply()
        self._push_undo(before, after, "Reorder layers")
        self._autosave()

    # Inline edit disabled; rename goes through rename_selected_group() dialog.

    def _push_undo(self, before: str, after: str, text: str):
        if self._in_undo or self._loading:
            return
        if (before or "") == (after or ""):
            self._snapshot = after or ""
            return
        self.undo_stack.push(TreeStateCommand(self, before, after, text))
        self._snapshot = after or ""

    def _apply_tree_state_from_undo(self, raw_json: str):
        self._in_undo = True
        try:
            self.load_from_project(raw_json)
            # request_apply() is a no-op while _in_undo is True — force apply
            # so the map canvas order matches the restored tree.
            self._apply_now_force()
            self._snapshot = self._serialize_tree()
        finally:
            self._in_undo = False

    def _on_tree_changed_external(self, before_json: str, after_json: str):
        if not self.is_control_enabled():
            return
        if self._in_undo or self._loading:
            return

        self.request_apply()
        self._push_undo(before_json, after_json, "Reorder layers")
        self._autosave()