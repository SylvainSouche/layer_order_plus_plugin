"""Advanced Layer Order — View (QDockWidget): renders, and reports intents.

Contract
--------
* **Out**: every user action leaves the View as an intent signal that
  carries everything needed to act on it (ids, target, flag). The View
  never decides what an action means and never changes what it displays
  in response to its own input: drops, checkbox clicks, branch arrows and
  settings boxes are only reported.
* **In**: the ViewController tells the View what to display through the
  ``render*`` methods. That is the only way displayed state changes.

Pure presentation state stays here: selection, scroll position,
enabled/disabled look. The View knows nothing of the Model, QGIS, or undo.

Parts: ``LayerOrderItemModel`` (tree_model.py) presents the rendered tree
and turns drops / checkbox clicks into intents; ``LayerOrderTree``
(tree_view.py) displays it and reports expand/collapse.
"""
from __future__ import annotations

from qgis.PyQt.QtCore import QItemSelectionModel, QSize, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDockWidget,
    QHBoxLayout,
    QInputDialog,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .icons import icon_add_group, icon_move_down, icon_move_up, icon_remove_group, icon_rename_group
from .model import TYPE_GROUP
from .tree_model import ROLE_ID, ROLE_TYPE, LayerOrderItemModel
from .tree_view import LayerOrderTree

_TREE_TOOLTIP = (
    "Drag layers and groups to set draw order (top of tree = drawn on top).\n\n"
    "Drop rules:\n"
    "  • Drop ON a layer → creates a new group containing the target layer and the dropped items\n"
    "  • Drop ON a group → moves the dropped items to the end of that group\n"
    "  • Drop ABOVE an item → reorders just above that item\n"
    "  • Drop BELOW an item → reorders just below that item\n"
    "  • Drop in the empty area → moves to the bottom of the list\n\n"
    "Move up / down: toolbar arrows or Ctrl+↑ / Ctrl+↓ (⌘ on macOS); items stay in their group.\n"
    "Right-click for: Create / Rename / Delete group, Expand / Collapse,\n"
    "Move up / down / to top / to bottom.\n"
    "Double-click a group to expand or collapse it."
)


class LayerOrderView(QDockWidget):
    """Pure UI for Advanced Layer Order. See module docstring for the contract."""

    # ==================================================================
    # Intents (ViewController listens)
    # ==================================================================
    create_group_requested = pyqtSignal(list)            # selected item ids (display order)
    rename_group_requested = pyqtSignal(str)             # group id
    delete_groups_requested = pyqtSignal(list)           # group ids
    move_to_boundary_requested = pyqtSignal(list, bool)  # item ids, to_top
    move_by_one_requested = pyqtSignal(list, bool)       # item ids, up
    expand_requested = pyqtSignal(list, bool)            # group ids, expanded
    check_requested = pyqtSignal(str, bool)              # item id (layer or group), checked
    drop_requested = pyqtSignal(list, str, str)          # moving ids, target id, DROP_* position
    control_toggled = pyqtSignal(bool)
    remove_empty_toggled = pyqtSignal(bool)
    verbose_toggled = pyqtSignal(bool)
    undo_requested = pyqtSignal(bool)                    # True = undo, False = redo (keys in the panel)

    def __init__(self, parent=None):
        super().__init__("Advanced Layer Order", parent)
        # Lets QGIS save and restore the dock's place and visibility
        self.setObjectName("AdvancedLayerOrderDock")
        self.item_model = LayerOrderItemModel(self)
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
        self.btn_move_up = tool_button(icon_move_up(), "Move up (Ctrl+↑)")
        self.btn_move_down = tool_button(icon_move_down(), "Move down (Ctrl+↓)")
        for b in (self.btn_add_group, self.btn_rename_group, self.btn_del_group):
            head.addWidget(b)
        head.addSpacing(8)
        head.addWidget(self.btn_move_up)
        head.addWidget(self.btn_move_down)
        head.addStretch(1)

        self.tree = LayerOrderTree()
        self.tree.setModel(self.item_model)
        self.tree.setToolTip(_TREE_TOOLTIP)
        self.tree.setIconSize(QSize(16, 16))
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
        self.chk_verbose = QCheckBox("Verbose logging (AdvancedLayerOrder log tab)")
        self.chk_verbose.setToolTip(
            "Log every step of drag-drop and sync to the AdvancedLayerOrder tab in "
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
        self.btn_move_up.clicked.connect(lambda: self._request_move_by_one(True))
        self.btn_move_down.clicked.connect(lambda: self._request_move_by_one(False))
        self.tree.move_intent.connect(self._request_move_by_one)
        self._report_only(self.chk_control, self.control_toggled)
        self._report_only(self.chk_remove_empty, self.remove_empty_toggled)
        self._report_only(self.chk_verbose, self.verbose_toggled)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.selectionModel().selectionChanged.connect(self._update_group_buttons)
        self.tree.expand_intent.connect(lambda gid, on: self.expand_requested.emit([gid], on))
        self.tree.undo_intent.connect(self.undo_requested)
        self.item_model.drop_intent.connect(self.drop_requested)
        self.item_model.check_intent.connect(self.check_requested)

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
        selected = {ix.data(ROLE_ID): ix for ix in self.tree.selectionModel().selectedIndexes()}

        def has_selected_ancestor(ix):
            p = ix.parent()
            while p.isValid():
                if p.data(ROLE_ID) in selected:
                    return True
                p = p.parent()
            return False

        top = {i for i, ix in selected.items() if not has_selected_ancestor(ix)}
        return [i for i in self.item_model.nodes_in_order() if i in top]

    def selected_group_ids(self) -> list[str]:
        return [ix.data(ROLE_ID) for ix in self.tree.selectionModel().selectedIndexes()
                if ix.data(ROLE_TYPE) == TYPE_GROUP]

    def _request_move_by_one(self, up: bool):
        ids = self.selected_ids()
        if ids:
            self.move_by_one_requested.emit(ids, up)

    def _request_rename(self):
        groups = self.selected_group_ids()
        if len(groups) == 1:
            self.rename_group_requested.emit(groups[0])

    def _update_group_buttons(self, *_args):
        n = len(self.selected_group_ids())
        enabled = self.chk_control.isChecked()
        any_selected = self.tree.selectionModel().hasSelection()
        self.btn_rename_group.setEnabled(enabled and n == 1)
        self.btn_del_group.setEnabled(enabled and n >= 1)
        self.btn_move_up.setEnabled(enabled and any_selected)
        self.btn_move_down.setEnabled(enabled and any_selected)

    def _on_context_menu(self, pos):
        if not self.chk_control.isChecked():
            return
        under = self.tree.indexAt(pos)
        selection = self.tree.selectionModel()
        if under.isValid() and not selection.isSelected(under):
            self.tree.setCurrentIndex(under)   # selects it alone
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
        act_up = menu.addAction(icon_move_up(), "Move up")
        act_down = menu.addAction(icon_move_down(), "Move down")
        act_top = menu.addAction("Move to top")
        act_bottom = menu.addAction("Move to bottom")
        act_rename.setEnabled(len(groups) == 1)
        for a in (act_delete, act_expand, act_collapse):
            a.setEnabled(bool(groups))
        for a in (act_up, act_down, act_top, act_bottom):
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
        elif chosen is act_up:
            self.move_by_one_requested.emit(ids, True)
        elif chosen is act_down:
            self.move_by_one_requested.emit(ids, False)
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
        """Show a tree of node dicts.

        Group: {"type": "group", "id", "name", "expanded", "children"}
        Layer: {"type": "layer", "id", "name", "visible", "icon"}
        """
        scroll = self.tree.verticalScrollBar().value()
        self.item_model.render(nodes)

        def expand(ns):
            for n in ns:
                if n["type"] == TYPE_GROUP:
                    self.tree.setExpanded(self.item_model.index_of(n["id"]), bool(n.get("expanded", True)))
                    expand(n.get("children", []))
        expand(nodes)
        self.tree.verticalScrollBar().setValue(scroll)
        self._update_group_buttons()

    def render_name(self, item_id: str, name: str):
        self.item_model.set_name(item_id, name)

    def render_visibility(self, layer_id: str, visible: bool):
        self.item_model.set_visible(layer_id, visible)

    def render_expanded(self, group_id: str, expanded: bool):
        self.tree.setExpanded(self.item_model.index_of(group_id), expanded)

    def render_selection(self, item_ids: list[str]):
        """Select `item_ids`; the first becomes current and is scrolled to."""
        selection = self.tree.selectionModel()
        selection.clearSelection()
        indexes = [ix for ix in map(self.item_model.index_of, item_ids) if ix.isValid()]
        for ix in indexes:
            selection.select(ix, QItemSelectionModel.SelectionFlag.Select)
        if indexes:
            selection.setCurrentIndex(indexes[0], QItemSelectionModel.SelectionFlag.NoUpdate)
            self.tree.scrollTo(indexes[0])

    def render_control_enabled(self, enabled: bool):
        self.chk_control.setChecked(bool(enabled))
        for w in (self.tree, self.btn_add_group, self.chk_remove_empty):
            w.setEnabled(enabled)
        self._update_group_buttons()

    def render_remove_empty(self, checked: bool):
        self.chk_remove_empty.setChecked(bool(checked))

    def render_verbose(self, checked: bool):
        self.chk_verbose.setChecked(bool(checked))
