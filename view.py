"""Layer Order Plus — View (QDockWidget subclass, pure UI).

The View owns all UI widgets (tree, toolbar, filter, checkboxes) and exposes:
- Semantic methods for the ViewController to call (add_layer_item, move_item, etc.)
- Qt signals for user actions (create_group_requested, drop_intent, etc.)

The View does NOT:
- Import or touch the Model
- Import or touch QGIS (QgsProject, customLayerOrder, etc.)
- Contain business logic (drop translation, undo, apply state machine)

The View DOES use icons.py (leaf module) for icon rendering — that's a
display concern, not a business-logic concern.
"""
from qgis.PyQt.QtCore import Qt, QSize, pyqtSignal
from qgis.PyQt.QtGui import QKeySequence, QShortcut
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QMenu,
    QCheckBox,
)


from .tree_utils import (
    ROLE_TYPE,
    ROLE_ID,
    TYPE_GROUP,
    TYPE_LAYER,
    find_group_item,
    find_layer_item,
    index_in_parent,
    apply_name_filter,
    expand_item_and_ancestors,
    unique_group_name,
    collect_group_names,
)
from .icons import (
    icon_group,
    icon_add_group,
    icon_remove_group,
    icon_rename_group,
)
from .tree_widget import BetterLayerTree


class LayerOrderView(QDockWidget):
    """Pure UI view for Layer Order Plus.

    Owns: QTreeWidget, toolbar buttons, filter field, checkboxes, shortcuts.
    Emits semantic signals for user actions.
    Exposes semantic methods for the ViewController to update the display.

    The View is a QDockWidget so plugin.py can register it via
    iface.addDockWidget().
    """

    # ==================================================================
    # User action signals (ViewController listens to these)
    # ==================================================================
    create_group_requested = pyqtSignal()
    rename_group_requested = pyqtSignal()
    delete_group_requested = pyqtSignal()
    move_to_top_requested = pyqtSignal()
    move_to_bottom_requested = pyqtSignal()
    expand_groups_requested = pyqtSignal(list)    # group_ids
    collapse_groups_requested = pyqtSignal(list)  # group_ids
    filter_changed = pyqtSignal(str)
    control_toggled = pyqtSignal(bool)
    remove_empty_toggled = pyqtSignal(bool)
    layer_visibility_toggled = pyqtSignal(str, bool)  # layer_id, checked
    group_visibility_toggled = pyqtSignal(str, bool)  # group_id, checked (propagate to all child layers)
    item_double_clicked = pyqtSignal(str)             # item_id (group only — toggle expand)
    # drop_intent(moving_ids, target_id, position) — from BetterLayerTree
    drop_intent = pyqtSignal(list, str, str)
    # Local dock shortcuts (Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z) — re-emitted so
    # plugin.py / ViewController can connect without touching the QShortcut
    # objects directly.
    undo_shortcut_activated = pyqtSignal()
    redo_shortcut_activated = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Layer Order Plus", parent)
        self._syncing = False  # guard: suppress signals when ViewController is updating us
        self._build_ui()
        self._connect_signals()

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

        # Local shortcuts (plugin.py also hooks Edit menu / app shortcuts).
        # The undo/redo QShortcut.activated signals are connected to the
        # class-level undo_shortcut_activated / redo_shortcut_activated
        # pyqtSignals so plugin.py / ViewController can listen without
        # touching the QShortcut objects directly.
        self._shortcuts = []
        for seq in (QKeySequence.StandardKey.Undo, QKeySequence.StandardKey.Redo,
                    QKeySequence("Ctrl+Shift+Z")):
            sc = QShortcut(seq, self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self._shortcuts.append(sc)
        # Connect shortcut.activated → re-emit through class-level signals.
        # (Direct signal-to-signal connection: PyQt handles the emit.)
        self._shortcuts[0].activated.connect(self.undo_shortcut_activated)
        self._shortcuts[1].activated.connect(self.redo_shortcut_activated)
        self._shortcuts[2].activated.connect(self.redo_shortcut_activated)

    def _connect_signals(self):
        self.btn_add_group.clicked.connect(self.create_group_requested.emit)
        self.btn_rename_group.clicked.connect(self.rename_group_requested.emit)
        self.btn_del_group.clicked.connect(self.delete_group_requested.emit)
        self.chk_control.toggled.connect(self._on_control_toggled)
        self.chk_remove_empty.toggled.connect(self._on_remove_empty_toggled)
        self.ed_filter.textChanged.connect(self.filter_changed.emit)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.drop_intent.connect(self.drop_intent.emit)  # re-emit

    # ==================================================================
    # Internal signal handlers (translate raw Qt signals to semantic ones)
    # ==================================================================
    def _on_control_toggled(self, checked):
        if self._syncing:
            return
        self._apply_control_ui_state(checked)
        self.control_toggled.emit(checked)

    def _on_remove_empty_toggled(self, checked):
        if self._syncing:
            return
        self.remove_empty_toggled.emit(checked)

    def _on_item_double_clicked(self, item, column):
        if self._syncing or item is None:
            return
        if item.data(0, ROLE_TYPE) == TYPE_GROUP:
            self.item_double_clicked.emit(item.data(0, ROLE_ID))

    def _on_item_changed(self, item, column):
        """User toggled a checkbox — emit visibility signal.

        For layers: emit layer_visibility_toggled(layer_id, checked).
        For groups: emit group_visibility_toggled(group_id, checked) — the
        ViewController propagates this to all child layers in the Model.
        (Qt's ItemIsAutoTristate also propagates visually, but we route
        through the Model explicitly for testability + QGIS sync.)
        """
        if column != 0 or self._syncing:
            return
        t = item.data(0, ROLE_TYPE)
        if t == TYPE_LAYER:
            lid = item.data(0, ROLE_ID)
            checked = item.checkState(0) == Qt.CheckState.Checked
            self.layer_visibility_toggled.emit(lid, checked)
        elif t == TYPE_GROUP:
            gid = item.data(0, ROLE_ID)
            # PartiallyChecked → treat as Checked (cycle forward)
            state = item.checkState(0)
            checked = state != Qt.CheckState.Unchecked
            self.group_visibility_toggled.emit(gid, checked)

    def _on_context_menu(self, pos):
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

        groups = self.get_selected_group_ids()
        sel = self.tree.selectedItems()
        under = self.tree.itemAt(pos)
        if under is not None and under not in sel:
            self.tree.clearSelection()
            under.setSelected(True)
            self.tree.setCurrentItem(under)
            groups = self.get_selected_group_ids()
            sel = self.tree.selectedItems()

        act_rename.setEnabled(len(groups) == 1)
        act_delete.setEnabled(len(groups) >= 1)
        act_expand.setEnabled(len(groups) >= 1)
        act_collapse.setEnabled(len(groups) >= 1)
        act_top.setEnabled(bool(sel))
        act_bottom.setEnabled(bool(sel))

        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen == act_create:
            self.create_group_requested.emit()
        elif chosen == act_rename:
            self.rename_group_requested.emit()
        elif chosen == act_delete:
            self.delete_group_requested.emit()
        elif chosen == act_expand:
            self.expand_groups_requested.emit(groups)
        elif chosen == act_collapse:
            self.collapse_groups_requested.emit(groups)
        elif chosen == act_top:
            self.move_to_top_requested.emit()
        elif chosen == act_bottom:
            self.move_to_bottom_requested.emit()

    # ==================================================================
    # Control rendering order checkbox state
    # ==================================================================
    def is_control_enabled(self) -> bool:
        return bool(self.chk_control.isChecked())

    def set_control_enabled(self, enabled: bool):
        """Programmatically set the checkbox state (from QGIS hasCustomLayerOrder)."""
        self._syncing = True
        try:
            self.chk_control.setChecked(bool(enabled))
        finally:
            self._syncing = False
        self._apply_control_ui_state(enabled)

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
    # Remove-empty-groups checkbox
    # ==================================================================
    def set_remove_empty_enabled(self, checked: bool):
        """Programmatically set the remove-empty checkbox (from project setting)."""
        self._syncing = True
        try:
            self.chk_remove_empty.setChecked(bool(checked))
        finally:
            self._syncing = False

    # ==================================================================
    # Selection queries (ViewController calls these)
    # ==================================================================
    def get_selected_item_ids(self) -> list[str]:
        return [it.data(0, ROLE_ID) for it in self.tree.selectedItems()]

    def get_selected_group_ids(self) -> list[str]:
        return [it.data(0, ROLE_ID) for it in self.tree.selectedItems()
                if it.data(0, ROLE_TYPE) == TYPE_GROUP]

    def get_current_item_id(self) -> str | None:
        it = self.tree.currentItem()
        return it.data(0, ROLE_ID) if it is not None else None

    def get_selected_group_names(self) -> set[str]:
        return {it.text(0) for it in self.tree.selectedItems()
                if it.data(0, ROLE_TYPE) == TYPE_GROUP}

    def get_all_group_names(self) -> set[str]:
        return collect_group_names(self.tree)

    def get_unique_group_name(self, base: str = "New group") -> str:
        return unique_group_name(self.tree, base)

    def _update_group_actions_enabled(self):
        groups = self.get_selected_group_ids()
        n = len(groups)
        self.btn_rename_group.setEnabled(n == 1 and self.is_control_enabled())
        self.btn_del_group.setEnabled(n >= 1 and self.is_control_enabled())

    def on_selection_changed(self):
        """Called by ViewController when tree selection changes (to update button states)."""
        self._update_group_actions_enabled()

    # ==================================================================
    # View mutation methods (ViewController calls these in response to Model events)
    # ==================================================================
    def begin_sync(self):
        """Call before a batch of updates — suppresses itemChanged signals."""
        self._syncing = True
        self.tree.blockSignals(True)

    def end_sync(self):
        """Call after a batch of updates — re-enables signals."""
        self.tree.blockSignals(False)
        self._syncing = False

    def clear(self):
        """Clear the entire tree."""
        self._syncing = True
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
        finally:
            self.tree.blockSignals(False)
            self._syncing = False

    def rebuild_from_nodes(self, nodes, layer_icon_provider=None):
        """Full tree rebuild from a list of node dicts.

        Each node is a dict with keys: type, id, name, and for groups:
        expanded, children. For layers: visible.

        layer_icon_provider: optional callable(layer_id) -> QIcon.
        If None, a generic layer icon is used.
        """
        self._syncing = True
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            expand_targets = []
            for node in nodes:
                self._build_tree_item(node, None, expand_targets, layer_icon_provider)
            # Restore expanded state
            for item_id, expanded in expand_targets:
                it = find_group_item(self.tree, item_id)
                if it is not None:
                    try:
                        it.setExpanded(expanded)
                    except RuntimeError:
                        pass
        finally:
            self.tree.blockSignals(False)
            self._syncing = False

    def _build_tree_item(self, node, parent_item, expand_targets, layer_icon_provider):
        """Recursively build a QTreeWidgetItem from a node dict."""
        if node["type"] == TYPE_GROUP:
            it = QTreeWidgetItem([node.get("name", "Group")])
            it.setData(0, ROLE_TYPE, TYPE_GROUP)
            it.setData(0, ROLE_ID, node["id"])
            try:
                it.setFlags((it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
                            & ~Qt.ItemFlag.ItemIsEditable)
            except Exception:
                it.setFlags((it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                            & ~Qt.ItemFlag.ItemIsEditable)
            it.setIcon(0, icon_group())
            it.setCheckState(0, Qt.CheckState.Checked)
            if parent_item is None:
                self.tree.addTopLevelItem(it)
            else:
                parent_item.addChild(it)
            expand_targets.append((node["id"], node.get("expanded", True)))
            for ch in node.get("children", []):
                self._build_tree_item(ch, it, expand_targets, layer_icon_provider)
        else:  # layer
            it = QTreeWidgetItem([node.get("name", "")])
            it.setData(0, ROLE_TYPE, TYPE_LAYER)
            it.setData(0, ROLE_ID, node["id"])
            it.setFlags((it.flags() | Qt.ItemFlag.ItemIsUserCheckable) & ~Qt.ItemFlag.ItemIsEditable)
            if layer_icon_provider is not None:
                try:
                    icon = layer_icon_provider(node["id"])
                    if icon is not None and not icon.isNull():
                        it.setIcon(0, icon)
                except Exception:
                    pass
            it.setCheckState(0, Qt.CheckState.Checked if node.get("visible", True) else Qt.CheckState.Unchecked)
            if parent_item is None:
                self.tree.addTopLevelItem(it)
            else:
                parent_item.addChild(it)

    def add_layer_item(self, layer_id, name, visible, parent_id, index, icon=None):
        """Add a layer item to the tree at the specified position."""
        self._syncing = True
        self.tree.blockSignals(True)
        try:
            it = QTreeWidgetItem([name])
            it.setData(0, ROLE_TYPE, TYPE_LAYER)
            it.setData(0, ROLE_ID, layer_id)
            it.setFlags((it.flags() | Qt.ItemFlag.ItemIsUserCheckable) & ~Qt.ItemFlag.ItemIsEditable)
            if icon is not None:
                it.setIcon(0, icon)
            it.setCheckState(0, Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)
            if parent_id:
                parent_item = find_group_item(self.tree, parent_id)
                if parent_item is not None:
                    parent_item.insertChild(index, it)
                else:
                    self.tree.insertTopLevelItem(index, it)
            else:
                self.tree.insertTopLevelItem(index, it)
        finally:
            self.tree.blockSignals(False)
            self._syncing = False

    def remove_layer_item(self, layer_id):
        """Remove a layer item from the tree."""
        self._syncing = True
        self.tree.blockSignals(True)
        try:
            it = find_layer_item(self.tree, layer_id)
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
            self._syncing = False

    def rename_layer_item(self, layer_id, new_name):
        """Update a layer item's display text."""
        it = find_layer_item(self.tree, layer_id)
        if it is not None:
            self._syncing = True
            try:
                it.setText(0, new_name)
            finally:
                self._syncing = False

    def add_group_item(self, group_id, name, parent_id, index):
        """Add a group item to the tree at the specified position."""
        self._syncing = True
        self.tree.blockSignals(True)
        try:
            it = QTreeWidgetItem([name])
            it.setData(0, ROLE_TYPE, TYPE_GROUP)
            it.setData(0, ROLE_ID, group_id)
            try:
                it.setFlags((it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
                            & ~Qt.ItemFlag.ItemIsEditable)
            except Exception:
                it.setFlags((it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                            & ~Qt.ItemFlag.ItemIsEditable)
            it.setIcon(0, icon_group())
            it.setCheckState(0, Qt.CheckState.Checked)
            if parent_id:
                parent_item = find_group_item(self.tree, parent_id)
                if parent_item is not None:
                    parent_item.insertChild(index, it)
                else:
                    self.tree.insertTopLevelItem(index, it)
            else:
                self.tree.insertTopLevelItem(index, it)
        finally:
            self.tree.blockSignals(False)
            self._syncing = False

    def remove_group_item(self, group_id):
        """Remove a group item from the tree."""
        self._syncing = True
        self.tree.blockSignals(True)
        try:
            it = find_group_item(self.tree, group_id)
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
            self._syncing = False

    def rename_group_item(self, group_id, new_name):
        """Update a group item's display text."""
        it = find_group_item(self.tree, group_id)
        if it is not None:
            self._syncing = True
            try:
                it.setText(0, new_name)
            finally:
                self._syncing = False

    def set_visibility(self, layer_id, visible):
        """Update a layer item's checkbox (from Model visibility change)."""
        it = find_layer_item(self.tree, layer_id)
        if it is not None:
            self._syncing = True
            try:
                it.setCheckState(0, Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)
            finally:
                self._syncing = False

    def set_expanded(self, group_id, expanded):
        """Update a group item's expanded state."""
        it = find_group_item(self.tree, group_id)
        if it is not None:
            self._syncing = True
            try:
                it.setExpanded(expanded)
            finally:
                self._syncing = False

    def expand_and_select_item(self, item_id):
        """Expand an item's ancestors and select + scroll to it. Used after group creation."""
        it = (find_group_item(self.tree, item_id) or find_layer_item(self.tree, item_id))
        if it is not None:
            expand_item_and_ancestors(it)
            self.tree.setCurrentItem(it)
            self.tree.scrollToItem(it)

    def select_items(self, item_ids):
        """Select multiple items by id."""
        self.tree.clearSelection()
        for item_id in item_ids:
            it = (find_group_item(self.tree, item_id) or find_layer_item(self.tree, item_id))
            if it is not None:
                it.setSelected(True)

    # ==================================================================
    # Filter (pure View concern)
    # ==================================================================
    def apply_filter(self, text: str):
        self.tree.blockSignals(True)
        try:
            apply_name_filter(self.tree, text)
        finally:
            self.tree.blockSignals(False)

    # ==================================================================
    # Anchor (selection-based "where to insert new layers")
    # ==================================================================
    def get_anchor(self):
        """Return (anchor_type, anchor_id) for the current selection, or (None, None)."""
        it = self.tree.currentItem()
        if it is None:
            sel = self.tree.selectedItems()
            if sel:
                it = sel[0]
        if it is None:
            return (None, None)
        return (it.data(0, ROLE_TYPE), it.data(0, ROLE_ID))

    def resolve_anchor_for_insertion(self):
        """Return (parent_id, index) where a new item should be inserted.

        If a group is selected: insert at top of that group (index 0).
        If a layer is selected: insert after it in the same parent.
        If nothing selected: append to top-level (None, None).
        """
        anchor_type, anchor_id = self.get_anchor()
        if anchor_type == TYPE_GROUP and anchor_id:
            return (anchor_id, 0)
        if anchor_type == TYPE_LAYER and anchor_id:
            it = find_layer_item(self.tree, anchor_id)
            if it is not None:
                p = it.parent()
                if p is None:
                    idx = self.tree.indexOfTopLevelItem(it)
                    return (None, idx + 1)
                else:
                    return (p.data(0, ROLE_ID), p.indexOfChild(it) + 1)
        return (None, None)

    def get_index_in_parent(self, item_id):
        """Return the index of an item within its parent, or None."""
        it = (find_group_item(self.tree, item_id) or find_layer_item(self.tree, item_id))
        if it is None:
            return None
        return index_in_parent(self.tree, it)

    def get_item_depth(self, item_id):
        """Return the depth of an item (1 = top-level, 2 = one level deep, etc.)."""
        it = (find_group_item(self.tree, item_id) or find_layer_item(self.tree, item_id))
        if it is None:
            return 0
        d = 0
        cur = it
        while cur is not None:
            d += 1
            cur = cur.parent()
        return d
