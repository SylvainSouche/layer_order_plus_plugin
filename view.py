"""Layer Order Plus — View (QDockWidget): renders, and reports intents.

Contract
--------
* **Out**: every user action leaves the View as an intent signal that
  carries everything needed to act on it (ids, target, flag). The View
  never decides what an action means and never changes what it displays
  in response to its own input: a click on a checkbox, a branch arrow or a
  settings box is reverted/ignored locally and only reported.
* **In**: the ViewController tells the View what to display through the
  ``render*`` methods. That is the only way displayed state changes.

Pure presentation state stays here: selection, scroll position,
enabled/disabled look. The View knows nothing of the Model, QGIS,
or undo.
"""

from qgis.PyQt.QtCore import QItemSelectionModel, QSize, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDockWidget,
    QHBoxLayout,
    QInputDialog,
    QMenu,
    QPushButton,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .icons import icon_add_group, icon_group, icon_remove_group, icon_rename_group
from .tree_utils import (
    ROLE_ID,
    ROLE_TYPE,
    TYPE_GROUP,
    find_group_item,
    find_layer_item,
)
from .tree_widget import LayerOrderTree

_TREE_TOOLTIP = (
    "Drag layers and groups to set draw order (top of tree = drawn on top).\n\n"
    "Drop rules:\n"
    "  • Drop ON a layer → creates a new group containing the target layer and the dropped items\n"
    "  • Drop ON a group → moves the dropped items to the end of that group\n"
    "  • Drop ABOVE an item → reorders just above that item\n"
    "  • Drop BELOW an item → reorders just below that item\n"
    "  • Drop in the empty area → moves to the bottom of the list\n\n"
    "Right-click for: Create / Rename / Delete group, Expand / Collapse, Move to top / bottom.\n"
    "Double-click a group to expand or collapse it."
)


class LayerOrderView(QDockWidget):
    """Pure UI for Layer Order Plus. See module docstring for the contract."""

    # ==================================================================
    # Intents (ViewController listens)
    # ==================================================================
    create_group_requested = pyqtSignal(list)            # selected item ids (display order)
    rename_group_requested = pyqtSignal(str)             # group id
    delete_groups_requested = pyqtSignal(list)           # group ids
    move_to_boundary_requested = pyqtSignal(list, bool)  # item ids, to_top
    expand_requested = pyqtSignal(list, bool)            # group ids, expanded
    check_requested = pyqtSignal(str, bool)              # item id (layer or group), checked
    drop_requested = pyqtSignal(list, str, str)          # moving ids, target id, DROP_* position
    control_toggled = pyqtSignal(bool)
    remove_empty_toggled = pyqtSignal(bool)
    verbose_toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__("Layer Order Plus", parent)
        self._build_ui()
        self._connect_signals()

    # ==================================================================
    # Construction
    # ==================================================================
    def _build_ui(self):
        rootw = QWidget()
        self.setWidget(rootw)
        lay = QVBoxLayout(rootw)
        head = QHBoxLayout()
        lay.addLayout(head)

        def tool_button(icon, tip):
            b = QPushButton()
            b.setIcon(icon)
            b.setToolTip(tip)
            b.setFlat(True)
            b.setFixedSize(28, 28)
            b.setIconSize(QSize(16, 16))
            return b

        self.btn_add_group = tool_button(icon_add_group(), "Create group")
        self.btn_rename_group = tool_button(icon_rename_group(), "Rename group")
        self.btn_del_group = tool_button(icon_remove_group(), "Delete group")
        for b in (self.btn_add_group, self.btn_rename_group, self.btn_del_group):
            head.addWidget(b)
        head.addStretch(1)

        self.tree = LayerOrderTree()
        self.tree.setHeaderHidden(True)
        self.tree.setToolTip(_TREE_TOOLTIP)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setUniformRowHeights(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        lay.addWidget(self.tree)

        self.chk_control = QCheckBox("Control rendering order")
        self.chk_control.setToolTip(
            "When checked, this panel drives the map draw order (custom layer order). "
            "When unchecked, QGIS uses the default Layers-panel order."
        )
        self.chk_remove_empty = QCheckBox("Remove empty groups on layer delete")
        self.chk_remove_empty.setToolTip(
            "When checked, order groups that become empty after their last layer is "
            "removed are deleted automatically. When unchecked, empty groups are kept."
        )
        self.chk_verbose = QCheckBox("Verbose logging (LayerOrderPlus log tab)")
        self.chk_verbose.setToolTip(
            "Log every step of drag-drop and sync to the LayerOrderPlus tab in "
            "View → Panels → Log Messages. OFF by default."
        )
        for c in (self.chk_control, self.chk_remove_empty, self.chk_verbose):
            lay.addWidget(c)

    def _connect_signals(self):
        self.btn_add_group.clicked.connect(
            lambda: self.create_group_requested.emit(self.selected_ids()))
        self.btn_rename_group.clicked.connect(self._request_rename)
        self.btn_del_group.clicked.connect(
            lambda: self.delete_groups_requested.emit(self.selected_group_ids()))
        self._report_only(self.chk_control, self.control_toggled)
        self._report_only(self.chk_remove_empty, self.remove_empty_toggled)
        self._report_only(self.chk_verbose, self.verbose_toggled)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.itemSelectionChanged.connect(self._update_group_buttons)
        self.tree.drop_intent.connect(self.drop_requested)
        self.tree.expand_intent.connect(lambda gid, on: self.expand_requested.emit([gid], on))
        self.tree.check_intent.connect(self.check_requested)

    @staticmethod
    def _report_only(box: QCheckBox, intent):
        """A click reports the wish; the box keeps showing the real state
        until the ViewController renders the outcome."""
        def on_clicked(checked):
            box.blockSignals(True)
            box.setChecked(not checked)
            box.blockSignals(False)
            intent.emit(checked)
        box.clicked.connect(on_clicked)

    # ==================================================================
    # Selection (presentation state)
    # ==================================================================
    def selected_ids(self) -> list[str]:
        """Selected items without a selected ancestor, in display order."""
        return [it.data(0, ROLE_ID) for it in self.tree.moving_items()]

    def selected_group_ids(self) -> list[str]:
        return [it.data(0, ROLE_ID) for it in self.tree.selectedItems()
                if it.data(0, ROLE_TYPE) == TYPE_GROUP]

    def _request_rename(self):
        groups = self.selected_group_ids()
        if len(groups) == 1:
            self.rename_group_requested.emit(groups[0])

    def _update_group_buttons(self):
        n = len(self.selected_group_ids())
        enabled = self.chk_control.isChecked()
        self.btn_rename_group.setEnabled(enabled and n == 1)
        self.btn_del_group.setEnabled(enabled and n >= 1)

    def _on_context_menu(self, pos):
        if not self.chk_control.isChecked():
            return
        under = self.tree.itemAt(pos)
        if under is not None and not under.isSelected():
            self.tree.clearSelection()
            under.setSelected(True)
            self.tree.setCurrentItem(under)
        ids = self.selected_ids()
        groups = self.selected_group_ids()

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
        act_rename.setEnabled(len(groups) == 1)
        for a in (act_delete, act_expand, act_collapse):
            a.setEnabled(bool(groups))
        for a in (act_top, act_bottom):
            a.setEnabled(bool(ids))

        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is act_create:
            self.create_group_requested.emit(ids)
        elif chosen is act_rename:
            self.rename_group_requested.emit(groups[0])
        elif chosen is act_delete:
            self.delete_groups_requested.emit(groups)
        elif chosen is act_expand:
            self.expand_requested.emit(groups, True)
        elif chosen is act_collapse:
            self.expand_requested.emit(groups, False)
        elif chosen is act_top:
            self.move_to_boundary_requested.emit(ids, True)
        elif chosen is act_bottom:
            self.move_to_boundary_requested.emit(ids, False)

    # ==================================================================
    # Dialogs
    # ==================================================================
    def ask_text(self, title: str, label: str, default: str) -> str | None:
        """Modal text prompt. Returns the stripped text, or None if cancelled/empty."""
        text, ok = QInputDialog.getText(self, title, label, text=default)
        text = (text or "").strip()
        return text if ok and text else None

    # ==================================================================
    # Rendering (ViewController → View)
    # ==================================================================
    def render(self, nodes: list[dict]):
        """Rebuild the tree from node dicts, keeping scroll position.

        Group: {"type": "group", "id", "name", "expanded", "children"}
        Layer: {"type": "layer", "id", "name", "visible", "icon"}
        """
        tree = self.tree
        scroll = tree.verticalScrollBar().value()
        tree.blockSignals(True)
        try:
            tree.clear()
            for node in nodes:
                self._build_item(node, None)
            tree.doItemsLayout()  # so the scroll range is up to date
            tree.verticalScrollBar().setValue(scroll)
        finally:
            tree.blockSignals(False)
        self._update_group_buttons()

    def _build_item(self, node: dict, parent: QTreeWidgetItem | None) -> QTreeWidgetItem:
        it = QTreeWidgetItem([node.get("name", "")])
        it.setData(0, ROLE_TYPE, node["type"])
        it.setData(0, ROLE_ID, node["id"])
        it.setFlags((it.flags() | Qt.ItemFlag.ItemIsUserCheckable) & ~Qt.ItemFlag.ItemIsEditable)
        if parent is None:
            self.tree.addTopLevelItem(it)
        else:
            parent.addChild(it)
        if node["type"] == TYPE_GROUP:
            it.setIcon(0, icon_group())
            for ch in node.get("children", []):
                self._build_item(ch, it)
            it.setExpanded(bool(node.get("expanded", True)))
            it.setCheckState(0, self._group_state(it))
        else:
            icon = node.get("icon")
            if icon is not None and not icon.isNull():
                it.setIcon(0, icon)
            it.setCheckState(0, Qt.CheckState.Checked if node.get("visible", True)
                             else Qt.CheckState.Unchecked)
        return it

    @staticmethod
    def _group_state(group: QTreeWidgetItem) -> Qt.CheckState:
        """Checked if every child is, Unchecked if none is, else PartiallyChecked."""
        states = {group.child(i).checkState(0) for i in range(group.childCount())}
        if not states or states == {Qt.CheckState.Checked}:
            return Qt.CheckState.Checked
        if states == {Qt.CheckState.Unchecked}:
            return Qt.CheckState.Unchecked
        return Qt.CheckState.PartiallyChecked

    def _find(self, item_id: str) -> QTreeWidgetItem | None:
        return find_group_item(self.tree, item_id) or find_layer_item(self.tree, item_id)

    def render_name(self, item_id: str, name: str):
        it = self._find(item_id)
        if it is not None:
            it.setText(0, name)

    def render_visibility(self, layer_id: str, visible: bool):
        it = find_layer_item(self.tree, layer_id)
        if it is None:
            return
        it.setCheckState(0, Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)
        p = it.parent()
        while p is not None:
            p.setCheckState(0, self._group_state(p))
            p = p.parent()

    def render_expanded(self, group_id: str, expanded: bool):
        it = find_group_item(self.tree, group_id)
        if it is not None:
            it.setExpanded(expanded)

    def render_selection(self, item_ids: list[str]):
        """Select `item_ids`; the first becomes current and is scrolled to."""
        self.tree.clearSelection()
        first = None
        for item_id in item_ids:
            it = self._find(item_id)
            if it is not None:
                it.setSelected(True)
                first = first or it
        if first is not None:
            self.tree.setCurrentItem(first, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
            self.tree.scrollToItem(first)

    def render_control_enabled(self, enabled: bool):
        self.chk_control.setChecked(bool(enabled))
        for w in (self.tree, self.btn_add_group, self.chk_remove_empty):
            w.setEnabled(enabled)
        self.tree.setStyleSheet("" if enabled else "QTreeWidget { color: palette(disabled); }")
        self._update_group_buttons()

    def render_remove_empty(self, checked: bool):
        self.chk_remove_empty.setChecked(bool(checked))

    def render_verbose(self, checked: bool):
        self.chk_verbose.setChecked(bool(checked))
