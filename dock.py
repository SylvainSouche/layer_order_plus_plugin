# dock.py
"""Layer Order Plus dock widget (MVC — View + ViewController, 1.2.0 refactor).

The dock is now a thin View + ViewController over the LayerOrderModel
(model.py). Every state mutation goes through the Model; the dock listens
to Model events and updates the QTreeWidget incrementally. The QTreeWidget
is a shadow of the Model — rebuilt on `model_loaded`, updated per-event
otherwise.

The Model is Qt-free and fully unit-tested (tests/test_model.py, 76 tests).
The dock's job is: UI construction, QGIS signal handling (Controller aspect),
QTreeWidget ↔ Model sync (ViewController aspect), undo stack wiring, and
the apply-to-QGIS state machine.

Undo is still snapshot-based (TreeStateCommand) — snapshots come from
`self._model.serialize()`. The undo stack itself will move to a separate
Controller in 1.4.0.
"""
import json
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
from .model import (
    LayerOrderModel,
    GroupNode,
    LayerNode,
    EVENT_MODEL_LOADED,
    EVENT_LAYER_ADDED,
    EVENT_LAYER_REMOVED,
    EVENT_LAYER_RENAMED,
    EVENT_GROUP_CREATED,
    EVENT_GROUP_DELETED,
    EVENT_GROUP_RENAMED,
    EVENT_ITEM_MOVED,
    EVENT_VISIBILITY_CHANGED,
    EVENT_EXPANDED_CHANGED,
    EVENT_ORDER_CHANGED,
)


LOG_TAG = "LayerOrderPlus"

# Persistence format version (QualityOverhaul 2.4) — re-exported from the Model
# so existing imports keep working.
from .model import TREE_JSON_SCHEMA_VERSION  # noqa: E402


def _log(msg, level=Qgis.Info):
    """Centralised log helper. Visible in View → Panels → Log Messages."""
    try:
        QgsMessageLog.logMessage(str(msg), LOG_TAG, level)
    except Exception:
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
    """View + ViewController over LayerOrderModel.

    The Model is the single source of truth for the order tree. The dock:
    - Builds the QTreeWidget UI (View)
    - Listens to Model events and updates the QTreeWidget (ViewController)
    - Routes user actions (button clicks, drag-drop, context menu) to Model
      mutations (ViewController)
    - Listens to QGIS signals (layersAdded, layersWillBeRemoved) and routes
      them to Model mutations (Controller aspect — will split in 1.4.0)
    - Owns the undo stack and the apply-to-QGIS state machine
    """

    def __init__(self, iface):
        super().__init__("Layer Order Plus", iface.mainWindow())
        self.iface = iface
        self._save_cb = None

        # ---------------- Model (single source of truth) ----------------
        self._model = LayerOrderModel()
        self._model.add_listener(self._on_model_event)

        # ---------------- guards ----------------
        self._apply_suspended = True     # block apply during project load
        self._loading = False            # block autosave while building UI
        self._in_undo = False
        self._syncing_from_model = False  # block Model emits when we're the source

        # ---------------- persistent anchor ----------------
        self._anchor_type = None         # TYPE_LAYER | TYPE_GROUP
        self._anchor_id = None           # layer_id | group_id

        # ---------------- undo ----------------
        self._snapshot = ""
        self.undo_stack = QUndoStack(self)

        # ---------------- layer rename sync (QualityOverhaul 2.1) ----------------
        self._layer_rename_connections = {}  # layer_id → (layer, bound_slot)

        # ---------------- visibility checkbox sync (QualityOverhaul 3.1) ----------------
        self._in_visibility_sync = False
        self._visibility_connections = {}

        # safe apply debounce
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.timeout.connect(self._apply_now)

        # ---------------- UI ----------------
        self._build_ui()
        self._connect_signals()

        self._update_group_actions_enabled()
        self._sync_control_from_project()
        self._sync_remove_empty_from_project()

        _log(f"dock initialised; chk_control exists={hasattr(self, 'chk_control')}; "
             f"is_control_enabled={self.is_control_enabled()}; "
             f"remove_empty_groups={self._model.get_remove_empty_groups()}")

    # ==================================================================
    # UI construction
    # ==================================================================
    def _build_ui(self):
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

        # Filter field (QualityOverhaul 3.3)
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

        self.chk_remove_empty = QCheckBox("Remove empty groups on layer delete")
        self.chk_remove_empty.setToolTip(
            "When checked, order groups that become empty after their last layer is "
            "removed are deleted automatically. When unchecked, empty groups are kept."
        )
        lay.addWidget(self.chk_remove_empty)

        # Local shortcuts (plugin also hooks Edit menu / app shortcuts)
        for seq, slot in (
            (QKeySequence.StandardKey.Undo, self.undo_stack.undo),
            (QKeySequence.StandardKey.Redo, self.undo_stack.redo),
            (QKeySequence("Ctrl+Shift+Z"), self.undo_stack.redo),
        ):
            sc = QShortcut(seq, self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)

    def _connect_signals(self):
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
        self.tree.itemChanged.connect(self._on_tree_item_changed)

        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.currentItemChanged.connect(self._capture_anchor_from_current)

    # ==================================================================
    # Busy-state property (consolidates the 3-guard pattern)
    # ==================================================================
    @property
    def _is_busy(self) -> bool:
        """True when mutations should be no-ops (loading / undo / syncing)."""
        return self._loading or self._in_undo or self._syncing_from_model

    # ==================================================================
    # Model event dispatcher (ViewController: Model → QTreeWidget)
    # ==================================================================
    def _on_model_event(self, event_type: str, payload: dict):
        """Route Model events to QTreeWidget updates.

        This is the heart of the ViewController. Every Model mutation emits
        an event; this dispatcher updates the QTreeWidget to match.

        During `block_notifications()` (project load, undo replay) the Model
        suppresses per-node events and emits a single `model_loaded` —
        we then rebuild the QTreeWidget from scratch.
        """
        if event_type == EVENT_MODEL_LOADED:
            self._rebuild_tree_from_model()
        elif event_type == EVENT_LAYER_ADDED:
            self._on_model_layer_added(payload)
        elif event_type == EVENT_LAYER_REMOVED:
            self._on_model_layer_removed(payload)
        elif event_type == EVENT_LAYER_RENAMED:
            self._on_model_layer_renamed(payload)
        elif event_type == EVENT_GROUP_CREATED:
            self._on_model_group_created(payload)
        elif event_type == EVENT_GROUP_DELETED:
            self._on_model_group_deleted(payload)
        elif event_type == EVENT_GROUP_RENAMED:
            self._on_model_group_renamed(payload)
        elif event_type == EVENT_ITEM_MOVED:
            self._on_model_item_moved(payload)
        elif event_type == EVENT_VISIBILITY_CHANGED:
            self._on_model_visibility_changed(payload)
        elif event_type == EVENT_EXPANDED_CHANGED:
            self._on_model_expanded_changed(payload)
        elif event_type == EVENT_ORDER_CHANGED:
            # Coarse signal — Controller aspect applies to QGIS.
            # The actual apply is debounced via request_apply().
            self.request_apply()

    def _rebuild_tree_from_model(self):
        """Full QTreeWidget rebuild from the Model. Used on model_loaded."""
        self._syncing_from_model = True
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            for node in self._model.get_root():
                self._build_tree_item(node, None)
            # Restore expanded state for groups
            self._restore_expanded_state(self._model.get_root())
        finally:
            self.tree.blockSignals(False)
            self._syncing_from_model = False

    def _build_tree_item(self, node, parent_item):
        """Recursively build a QTreeWidgetItem from a Model node."""
        if isinstance(node, GroupNode):
            it = QTreeWidgetItem([node.name])
            it.setData(0, ROLE_TYPE, TYPE_GROUP)
            it.setData(0, ROLE_ID, node.id)
            try:
                it.setFlags((it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
                            & ~Qt.ItemFlag.ItemIsEditable)
            except Exception:
                it.setFlags((it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                            & ~Qt.ItemFlag.ItemIsEditable)
            it.setIcon(0, icon_group())
            it.setCheckState(0, Qt.CheckState.Checked)  # auto-tristate recomputes
            if parent_item is None:
                self.tree.addTopLevelItem(it)
            else:
                parent_item.addChild(it)
            for ch in node.children:
                self._build_tree_item(ch, it)
        else:  # LayerNode
            lyr = self._lookup_qgs_layer(node.id)
            it = QTreeWidgetItem([node.name])
            it.setData(0, ROLE_TYPE, TYPE_LAYER)
            it.setData(0, ROLE_ID, node.id)
            it.setFlags((it.flags() | Qt.ItemFlag.ItemIsUserCheckable) & ~Qt.ItemFlag.ItemIsEditable)
            if lyr is not None:
                it.setIcon(0, icon_for_layer(lyr))
            it.setCheckState(0, Qt.CheckState.Checked if node.visible else Qt.CheckState.Unchecked)
            if parent_item is None:
                self.tree.addTopLevelItem(it)
            else:
                parent_item.addChild(it)
            # Connect rename + visibility listeners
            if lyr is not None:
                self._connect_layer_rename(lyr)
                self._connect_layer_visibility(lyr)

    def _restore_expanded_state(self, nodes, parent_item=None):
        """Walk the Model tree and restore expanded state on QTreeWidget items."""
        for node in nodes:
            if isinstance(node, GroupNode):
                it = find_group_item(self.tree, node.id)
                if it is not None:
                    try:
                        it.setExpanded(node.expanded)
                    except RuntimeError:
                        pass
                self._restore_expanded_state(node.children, it)

    # ---------- per-event handlers ----------

    def _on_model_layer_added(self, payload):
        """Model added a layer — create the QTreeWidget item."""
        self._syncing_from_model = True
        self.tree.blockSignals(True)
        try:
            lyr = self._lookup_qgs_layer(payload["layer_id"])
            if lyr is None:
                _log(f"_on_model_layer_added: layer {payload['layer_id']} not found in project", Qgis.Warning)
                return
            it = QTreeWidgetItem([payload["name"]])
            it.setData(0, ROLE_TYPE, TYPE_LAYER)
            it.setData(0, ROLE_ID, payload["layer_id"])
            it.setFlags((it.flags() | Qt.ItemFlag.ItemIsUserCheckable) & ~Qt.ItemFlag.ItemIsEditable)
            it.setIcon(0, icon_for_layer(lyr))
            it.setCheckState(0, Qt.CheckState.Checked if payload.get("visible", True) else Qt.CheckState.Unchecked)
            parent_id = payload.get("parent_id")
            if parent_id:
                parent_item = find_group_item(self.tree, parent_id)
                if parent_item is not None:
                    parent_item.insertChild(payload["index"], it)
                else:
                    self.tree.insertTopLevelItem(payload["index"], it)
            else:
                self.tree.insertTopLevelItem(payload["index"], it)
            self._connect_layer_rename(lyr)
            self._connect_layer_visibility(lyr)
        finally:
            self.tree.blockSignals(False)
            self._syncing_from_model = False

    def _on_model_layer_removed(self, payload):
        """Model removed a layer — remove the QTreeWidget item."""
        self._syncing_from_model = True
        self.tree.blockSignals(True)
        try:
            it = find_layer_item(self.tree, payload["layer_id"])
            if it is not None:
                p = it.parent()
                if p is None:
                    idx = self.tree.indexOfTopLevelItem(it)
                    if idx >= 0:
                        self.tree.takeTopLevelItem(idx)
                else:
                    p.takeChild(p.indexOfChild(it))
            self._disconnect_layer_rename(payload["layer_id"])
            self._disconnect_layer_visibility(payload["layer_id"])
        finally:
            self.tree.blockSignals(False)
            self._syncing_from_model = False

    def _on_model_layer_renamed(self, payload):
        """Model renamed a layer — update the QTreeWidget item text."""
        it = find_layer_item(self.tree, payload["layer_id"])
        if it is not None:
            try:
                self._syncing_from_model = True
                it.setText(0, payload["new_name"])
            finally:
                self._syncing_from_model = False

    def _on_model_group_created(self, payload):
        """Model created a group — create the QTreeWidget item."""
        self._syncing_from_model = True
        self.tree.blockSignals(True)
        try:
            it = QTreeWidgetItem([payload["name"]])
            it.setData(0, ROLE_TYPE, TYPE_GROUP)
            it.setData(0, ROLE_ID, payload["group_id"])
            try:
                it.setFlags((it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
                            & ~Qt.ItemFlag.ItemIsEditable)
            except Exception:
                it.setFlags((it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                            & ~Qt.ItemFlag.ItemIsEditable)
            it.setIcon(0, icon_group())
            it.setCheckState(0, Qt.CheckState.Checked)
            parent_id = payload.get("parent_id")
            if parent_id:
                parent_item = find_group_item(self.tree, parent_id)
                if parent_item is not None:
                    parent_item.insertChild(payload["index"], it)
                else:
                    self.tree.insertTopLevelItem(payload["index"], it)
            else:
                self.tree.insertTopLevelItem(payload["index"], it)
        finally:
            self.tree.blockSignals(False)
            self._syncing_from_model = False

    def _on_model_group_deleted(self, payload):
        """Model deleted a group — remove the QTreeWidget item.

        If unwrap_children was used, the children were already promoted
        in the Model; we need to remove the group item and re-create the
        children at their new positions. Simplest: rebuild from Model.
        """
        if payload.get("unwrapped_children"):
            # Children were promoted — rebuild to get correct positions
            self._rebuild_tree_from_model()
        else:
            # Just remove the group item
            self._syncing_from_model = True
            self.tree.blockSignals(True)
            try:
                it = find_group_item(self.tree, payload["group_id"])
                if it is not None:
                    p = it.parent()
                    if p is None:
                        idx = self.tree.indexOfTopLevelItem(it)
                        if idx >= 0:
                            self.tree.takeTopLevelItem(idx)
                    else:
                        p.takeChild(p.indexOfChild(it))
            finally:
                self.tree.blockSignals(False)
                self._syncing_from_model = False

    def _on_model_group_renamed(self, payload):
        """Model renamed a group — update the QTreeWidget item text."""
        it = find_group_item(self.tree, payload["group_id"])
        if it is not None:
            try:
                self._syncing_from_model = True
                it.setText(0, payload["new_name"])
            finally:
                self._syncing_from_model = False

    def _on_model_item_moved(self, payload):
        """Model moved an item — rebuild QTreeWidget to get correct positions.

        Moving items in QTreeWidget while preserving selection and expanded
        state is fiddly; a full rebuild is simpler and correct. For large
        trees this could be optimized to a targeted take/insert.
        """
        self._rebuild_tree_from_model()

    def _on_model_visibility_changed(self, payload):
        """Model changed a layer's visibility — update the QTreeWidget checkbox."""
        it = find_layer_item(self.tree, payload["layer_id"])
        if it is not None:
            try:
                self._syncing_from_model = True
                it.setCheckState(0, Qt.CheckState.Checked if payload["visible"] else Qt.CheckState.Unchecked)
            finally:
                self._syncing_from_model = False

    def _on_model_expanded_changed(self, payload):
        """Model changed a group's expanded state — update the QTreeWidget item."""
        it = find_group_item(self.tree, payload["group_id"])
        if it is not None:
            try:
                self._syncing_from_model = True
                it.setExpanded(payload["expanded"])
            finally:
                self._syncing_from_model = False

    # ==================================================================
    # QGIS layer lookup (Controller aspect)
    # ==================================================================
    def _lookup_qgs_layer(self, layer_id):
        """Return the QgsMapLayer for an id, or None."""
        try:
            return QgsProject.instance().mapLayer(layer_id)
        except Exception:
            return None

    # ==================================================================
    # Control rendering order (stock Layer Order parity)
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
        self.ed_filter.setEnabled(enabled)
        if enabled:
            self._update_group_actions_enabled()
        else:
            self.btn_rename_group.setEnabled(False)
            self.btn_del_group.setEnabled(False)
            self.ed_filter.blockSignals(True)
            self.ed_filter.clear()
            self.ed_filter.blockSignals(False)
        self.tree.setStyleSheet("" if enabled else "QTreeWidget { color: palette(disabled); }")
        self.chk_remove_empty.setEnabled(enabled)

    # ==================================================================
    # remove-empty-groups setting (QualityOverhaul 2.2)
    # ==================================================================
    def _on_remove_empty_toggled(self, checked: bool):
        if self._loading:
            return
        self._model.set_remove_empty_groups(bool(checked))
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
        self._model.set_remove_empty_groups(checked)
        self.chk_remove_empty.blockSignals(True)
        self.chk_remove_empty.setChecked(checked)
        self.chk_remove_empty.blockSignals(False)

    # ==================================================================
    # name filter (QualityOverhaul 3.3) — pure View concern
    # ==================================================================
    def _on_filter_changed(self, text: str):
        self.tree.blockSignals(True)
        try:
            apply_name_filter(self.tree, text)
        finally:
            self.tree.blockSignals(False)

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
        self._disconnect_all_layer_renames()
        self._disconnect_all_layer_visibilities()
        try:
            self._model.clear()
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
            it = find_layer_item(self.tree, self._anchor_id)
            if it:
                return it
        if self._anchor_type == TYPE_GROUP and self._anchor_id:
            it = find_group_item(self.tree, self._anchor_id)
            if it:
                return it
        it = self.tree.currentItem()
        if it:
            return it
        sel = self.tree.selectedItems()
        if sel:
            return sel[0]
        return None

    # ==================================================================
    # tree helpers (thin wrappers — kept for backwards compat)
    # ==================================================================
    def find_layer_item(self, layer_id):
        return find_layer_item(self.tree, layer_id)

    def find_group_item(self, group_id):
        return find_group_item(self.tree, group_id)

    def _index_in_parent(self, item: QTreeWidgetItem) -> int:
        return index_in_parent(self.tree, item)

    def iter_all_layer_ids(self):
        yield from iter_all_layer_ids(self.tree)

    def _flatten_to_qgs_layers(self):
        """Flatten the Model tree to a list of QgsMapLayer (for setCustomLayerOrder)."""
        proj = QgsProject.instance()
        out = []
        for layer_id in self._model.get_flattened_layer_ids():
            lyr = proj.mapLayer(layer_id)
            if lyr:
                out.append(lyr)
        return out

    # ==================================================================
    # Layer rename sync (QualityOverhaul 2.1)
    # ==================================================================
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

    def _on_layer_renamed(self, lid, lyr):
        """Layer renamed in Layers panel → update Model (which updates the tree)."""
        if self._syncing_from_model:
            return
        try:
            new_name = lyr.name()
        except RuntimeError:
            return
        # Route through Model so the tree update goes through the event path
        self._model.rename_layer(lid, new_name)

    def _disconnect_all_layer_renames(self):
        for lid in list(self._layer_rename_connections.keys()):
            self._disconnect_layer_rename(lid)

    # ==================================================================
    # Visibility checkbox sync (QualityOverhaul 3.1)
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

    def _on_layer_visibility_external(self, lid):
        """Layer visibility toggled in Layers panel → update Model."""
        if self._in_visibility_sync:
            return
        ltl = self._find_layer_tree_layer(lid)
        if ltl is None:
            return
        try:
            visible = bool(ltl.itemVisibilityChecked())
        except RuntimeError:
            return
        # Route through Model
        self._model.set_visibility(lid, visible)

    def _on_tree_item_changed(self, item, column):
        """User toggled a checkbox in Plus → propagate to Model."""
        if column != 0:
            return
        if self._in_visibility_sync or self._syncing_from_model:
            return
        t = item.data(0, ROLE_TYPE)
        if t == TYPE_LAYER:
            self._on_layer_check_toggled(item)

    def _on_layer_check_toggled(self, item):
        lid = item.data(0, ROLE_ID)
        checked = item.checkState(0) == Qt.CheckState.Checked
        ltl = self._find_layer_tree_layer(lid)
        if ltl is None:
            return
        try:
            self._in_visibility_sync = True
            ltl.setItemVisibilityChecked(checked)
            try:
                root = QgsProject.instance().layerTreeRoot()
                root.emitVisibilityChanged()
            except Exception:
                pass
            # Also update Model so it stays in sync
            self._model.set_visibility(lid, checked)
            _log(f"_on_layer_check_toggled: {lid} visible={checked}")
        except Exception as e:
            _log(f"_on_layer_check_toggled FAILED: {e!r}", Qgis.Critical)
        finally:
            self._in_visibility_sync = False

    # ==================================================================
    # Layer ordering source (QualityOverhaul 1.2)
    # ==================================================================
    def _ordered_project_layers(self):
        """Return project layers in the order QGIS would draw them."""
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

    # ==================================================================
    # Group operations — route through Model
    # ==================================================================
    def create_group_from_selection(self):
        if not self.is_control_enabled() or self._is_busy:
            return
        try:
            before = self._model.serialize()

            default_name = unique_group_name(self.tree, "New group")
            name, ok = QInputDialog.getText(self, "New group", "Group name:", text=default_name)
            if not ok:
                return
            name = (name or "").strip() or default_name
            if name in collect_group_names(self.tree):
                name = unique_group_name(self.tree, name)

            selected = self.tree.selectedItems() or []
            selected_ids = {id(x) for x in selected}
            moving = [it for it in selected
                      if (it.parent() is None or id(it.parent()) not in selected_ids)]

            # Resolve insertion point
            anchor = self._resolve_anchor_item()
            if anchor is not None:
                parent_id = anchor.data(0, ROLE_ID) if anchor.data(0, ROLE_TYPE) == TYPE_GROUP else None
                idx = self._index_in_parent(anchor) + 1 if parent_id is None else None
            else:
                parent_id = None
                idx = None

            # Create the group via Model
            gid = self._model.create_group(name, parent_id=parent_id, index=idx)

            # Move selected items into the new group (reverse order so first selected ends up on top)
            for it in sorted(moving, key=self._index_in_parent, reverse=True):
                self._model.move_item(it.data(0, ROLE_ID), gid, 0)

            # Expand + select the new group
            grp_item = find_group_item(self.tree, gid)
            if grp_item is not None:
                expand_item_and_ancestors(grp_item)
                self.tree.setCurrentItem(grp_item)
                self.tree.scrollToItem(grp_item)
                self._model.set_expanded(gid, True)

            after = self._model.serialize()
            self._push_undo(before, after, "Create group")
            self._autosave()
        except Exception as e:
            _log(f"create_group_from_selection FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    def _selected_groups(self):
        return [it for it in self.tree.selectedItems() if it.data(0, ROLE_TYPE) == TYPE_GROUP]

    def _item_depth(self, item):
        d = 0
        cur = item
        while cur is not None:
            d += 1
            cur = cur.parent()
        return d

    def delete_selected_groups(self):
        if not self.is_control_enabled() or self._is_busy:
            return
        try:
            groups = self._selected_groups()
            if not groups:
                return
            before = self._model.serialize()
            # deepest first so nested selected groups are removed before parents
            groups.sort(key=self._item_depth, reverse=True)
            removed = 0
            for cur in groups:
                if cur.treeWidget() is not self.tree:
                    continue
                self._model.delete_group(cur.data(0, ROLE_ID), unwrap_children=True)
                removed += 1
            if removed == 0:
                return
            after = self._model.serialize()
            self._push_undo(before, after, "Delete group" if removed == 1 else f"Delete {removed} groups")
            self._autosave()
            self._update_group_actions_enabled()
        except Exception as e:
            _log(f"delete_selected_groups FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    def rename_selected_group(self):
        if not self.is_control_enabled() or self._is_busy:
            return
        try:
            groups = self._selected_groups()
            if len(groups) != 1:
                return
            grp = groups[0]
            before = self._model.serialize()
            current = grp.text(0)
            name, ok = QInputDialog.getText(self, "Rename group", "Group name:", text=current)
            if not ok:
                return
            name = (name or "").strip()
            if not name or name == current:
                return
            others = collect_group_names(self.tree) - {current}
            if name in others:
                name = unique_group_name(self.tree, name)
            self._model.rename_group(grp.data(0, ROLE_ID), name)
            after = self._model.serialize()
            self._push_undo(before, after, "Rename group")
            self._autosave()
        except Exception as e:
            _log(f"rename_selected_group FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    def _on_item_double_clicked(self, item, column):
        if not self.is_control_enabled() or self._is_busy:
            return
        if item is None:
            return
        if item.data(0, ROLE_TYPE) == TYPE_GROUP:
            new_state = not item.isExpanded()
            item.setExpanded(new_state)
            self._model.set_expanded(item.data(0, ROLE_ID), new_state)

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
        under = self.tree.itemAt(pos)
        if under is not None and under not in sel:
            self.tree.clearSelection()
            under.setSelected(True)
            self.tree.setCurrentItem(under)
            groups = self._selected_groups()
            sel = self.tree.selectedItems()

        act_rename.setEnabled(len(groups) == 1)
        act_delete.setEnabled(len(groups) >= 1)
        act_expand.setEnabled(len(groups) >= 1)
        act_collapse.setEnabled(len(groups) >= 1)
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
                self._model.set_expanded(g.data(0, ROLE_ID), True)
        elif chosen == act_collapse:
            for g in groups:
                g.setExpanded(False)
                self._model.set_expanded(g.data(0, ROLE_ID), False)
        elif chosen == act_top:
            self._move_selected_to_boundary(to_top=True)
        elif chosen == act_bottom:
            self._move_selected_to_boundary(to_top=False)

    def _move_selected_to_boundary(self, to_top: bool):
        if not self.is_control_enabled() or self._is_busy:
            return
        try:
            sel = self.tree.selectedItems()
            if not sel:
                return
            before = self._model.serialize()
            item_ids = [it.data(0, ROLE_ID) for it in sel]
            self._model.move_items_to_boundary(item_ids, to_top=to_top)
            # Re-select (rebuild loses selection)
            for it in sel:
                try:
                    it.setSelected(True)
                except RuntimeError:
                    pass
            after = self._model.serialize()
            if before != after:
                self._push_undo(before, after, "Move to top" if to_top else "Move to bottom")
                self._autosave()
                _log(f"_move_selected_to_boundary({'top' if to_top else 'bottom'}): "
                     f"moved {len(sel)} item(s)")
        except Exception as e:
            _log(f"_move_selected_to_boundary FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

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
        if self._is_busy:
            return
        self._save_cb(self._model.serialize())

    def _serialize_tree(self) -> str:
        """Snapshot the Model for undo / autosave."""
        return self._model.serialize()

    # ==================================================================
    # loading
    # ==================================================================
    def load_from_project(self, raw_json: str):
        _log(f"load_from_project: json_len={len(raw_json) if raw_json else 0}")
        self._loading = True
        # Drop rename + visibility listeners — they'll be reconnected as
        # the rebuild walks the new tree.
        self._disconnect_all_layer_renames()
        self._disconnect_all_layer_visibilities()
        try:
            # Load into Model inside a notification block — the dock will
            # rebuild the QTreeWidget once on model_loaded.
            with self._model.block_notifications():
                self._model.load_from_json(raw_json)
                # If no saved JSON, populate from current project layers
                if not raw_json:
                    for lyr in self._ordered_project_layers():
                        self._model.add_layer(lyr.id(), lyr.name())
            # Force a rebuild (block_notifications suppresses model_loaded,
            # so we need to trigger it manually)
            self._rebuild_tree_from_model()
            self._snapshot = self._model.serialize()
        finally:
            self._loading = False
            self._sync_control_from_project()
            self._sync_remove_empty_from_project()

    # ==================================================================
    # layer add/remove (Controller aspect — QGIS signals → Model)
    # ==================================================================
    def on_layers_added(self, layers):
        _log(f"on_layers_added: received {len(layers) if layers else 0} layer(s); "
             f"_loading={self._loading}, _in_undo={self._in_undo}, "
             f"is_control_enabled={self.is_control_enabled()}")
        if self._is_busy:
            _log("on_layers_added: skipped (busy)", Qgis.Warning)
            return
        try:
            before = self._model.serialize()
            existing = set(self._model.iter_layer_ids())
            new_layers = [l for l in layers if l.id() not in existing]
            if not new_layers:
                _log("on_layers_added: no new layers (all already present)")
                return

            anchor = self._resolve_anchor_item()
            if anchor is not None:
                if anchor.data(0, ROLE_TYPE) == TYPE_LAYER:
                    parent_id = None  # top-level (layers don't have group parents by default)
                    idx = self._index_in_parent(anchor)
                else:
                    parent_id = anchor.data(0, ROLE_ID)
                    idx = 0
            else:
                parent_id = None
                idx = None  # append

            for lyr in new_layers:
                self._model.add_layer(lyr.id(), lyr.name(),
                                      parent_id=parent_id, index=idx)
                if idx is not None:
                    idx += 1

            after = self._model.serialize()
            self._push_undo(before, after, "Add layers")
            self._autosave()
            _log(f"on_layers_added: inserted {len(new_layers)} layer(s); "
                 f"tree now has {sum(1 for _ in self._model.iter_layer_ids())} layer items")
        except Exception as e:
            _log(f"on_layers_added FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    def on_layers_removed(self, layer_ids):
        _log(f"on_layers_removed: received {len(layer_ids) if layer_ids else 0} id(s); "
             f"_loading={self._loading}, _in_undo={self._in_undo}")
        if self._is_busy:
            return
        try:
            before = self._model.serialize()
            for lid in (layer_ids or []):
                self._model.remove_layer(lid)
            after = self._model.serialize()
            self._push_undo(before, after, "Remove layers")
            self._autosave()
            _log(f"on_layers_removed: pruned {len(layer_ids or [])} id(s); "
                 f"tree now has {sum(1 for _ in self._model.iter_layer_ids())} layer items")
        except Exception as e:
            _log(f"on_layers_removed FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    # ==================================================================
    # apply to QGIS (Controller aspect — Model → QGIS customLayerOrder)
    # ==================================================================
    def request_apply(self):
        if self._apply_suspended or self._is_busy:
            return
        self._apply_timer.start(50)

    def _apply_now(self):
        if self._apply_suspended or self._is_busy:
            return
        self._apply_custom_order()

    def _apply_now_force(self):
        """Apply custom layer order even during undo/loading guards."""
        self._apply_custom_order()

    def _apply_custom_order(self):
        try:
            root = QgsProject.instance().layerTreeRoot()
            if not self.is_control_enabled():
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
            _log(traceback.format_exc(), Qgis.Critical)

    # ==================================================================
    # events (undo hooks)
    # ==================================================================
    def _on_rows_moved(self, *args, **kwargs):
        if not self.is_control_enabled() or self._is_busy:
            return
        # Dual-undo guard (QualityOverhaul 1.1 / 4.5)
        if getattr(self.tree, "_just_custom_dropped", False):
            _log("_on_rows_moved: skipped (custom drop already pushed undo)")
            return
        before = self._snapshot or self._model.serialize()
        after = self._model.serialize()
        self.request_apply()
        self._push_undo(before, after, "Reorder layers")
        self._autosave()

    def _push_undo(self, before: str, after: str, text: str):
        if self._is_busy:
            return
        if (before or "") == (after or ""):
            self._snapshot = after or ""
            return
        self.undo_stack.push(TreeStateCommand(self, before, after, text))
        self._snapshot = after or ""

    def _apply_tree_state_from_undo(self, raw_json: str):
        self._in_undo = True
        try:
            # Load into Model inside a block — dock rebuilds on exit
            with self._model.block_notifications():
                self._model.load_from_json(raw_json)
            self._rebuild_tree_from_model()
            self._apply_now_force()
            self._snapshot = self._model.serialize()
        finally:
            self._in_undo = False

    def _on_tree_changed_external(self, before_json: str, after_json: str):
        """Called by BetterLayerTree.dropEvent after a custom drag-drop.

        The QTreeWidget has already been mutated by the drop; we need to
        sync the Model to match. Easiest: deserialize after_json into Model.
        """
        if not self.is_control_enabled() or self._is_busy:
            return
        try:
            # The dropEvent already manipulated the QTreeWidget directly.
            # Rebuild the Model from the QTreeWidget's current state.
            # We do this by serializing the tree and loading into Model.
            # But _serialize_tree now reads from Model — so we need to
            # reconstruct the Model from the QTreeWidget here.
            self._sync_model_from_tree()
            self.request_apply()
            self._push_undo(before_json, after_json, "Reorder layers")
            self._autosave()
        except Exception as e:
            _log(f"_on_tree_changed_external FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    def _sync_model_from_tree(self):
        """Rebuild the Model from the current QTreeWidget state.

        Used after BetterLayerTree.dropEvent mutates the tree directly.
        In a pure MVC architecture the dropEvent would route through the
        Model, but BetterLayerTree's custom drop logic (OnItem+layer creates
        a group, etc.) is complex enough that we keep it operating on the
        QTreeWidget and sync back to Model afterwards.
        """
        self._syncing_from_model = True
        try:
            with self._model.block_notifications():
                # Serialize the QTreeWidget manually (can't use _serialize_tree
                # because that reads from Model now)
                raw = self._serialize_tree_widget()
                self._model.load_from_json(raw)
            # Update visibility flags on Model layer nodes from checkboxes
            for lid in self._model.iter_layer_ids():
                it = find_layer_item(self.tree, lid)
                if it is not None:
                    visible = it.checkState(0) == Qt.CheckState.Checked
                    node = self._model.find_item(lid)
                    if isinstance(node, LayerNode):
                        node.visible = visible
        finally:
            self._syncing_from_model = False

    def _serialize_tree_widget(self) -> str:
        """Serialize the QTreeWidget directly (not the Model).

        Used by _sync_model_from_tree after dropEvent mutates the tree.
        """
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
            return {
                "type": TYPE_LAYER,
                "id": item.data(0, ROLE_ID),
                "name": item.text(0),
                "visible": item.checkState(0) == Qt.CheckState.Checked,
            }

        return json.dumps(
            {
                "version": TREE_JSON_SCHEMA_VERSION,
                "children": [ser_item(self.tree.topLevelItem(i))
                             for i in range(self.tree.topLevelItemCount())],
            },
            ensure_ascii=False,
        )
