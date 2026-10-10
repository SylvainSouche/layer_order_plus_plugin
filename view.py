"""Advanced Layer Order — View (QDockWidget): renders, and reports intents.

Contract
--------
* **Out**: every user action leaves the View as an intent signal that
  carries everything needed to act on it (ids, target, flag). The View
  never decides what an action means and never changes what it displays
  in response to its own input: drops, checkbox clicks, branch arrows and
  the control box are only reported.
* **In**: the ViewController tells the View what to display through the
  ``render*`` methods. That is the only way displayed state changes.

Pure presentation state stays here: selection, scroll position,
enabled/disabled look. The View knows nothing of the Model, QGIS, or undo.

Parts: ``LayerOrderItemModel`` (tree_model.py) presents the rendered tree
and turns drops / checkbox clicks into intents; ``LayerOrderTree``
(tree_view.py) displays it and reports expand/collapse.
"""
from __future__ import annotations

import os

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

from .i18n import (
    ADD_GROUP,
    CONTROL_RENDERING_ORDER,
    DRAG_TO_REORDER,
    MOVE_DOWN,
    MOVE_TO_BOTTOM,
    MOVE_TO_TOP,
    MOVE_UP,
    REDO,
    REMOVE_GROUP,
    RENAME_GROUP,
    UNDO,
    tr,
)
from .icons import (
    icon_add_group,
    icon_move_down,
    icon_move_up,
    icon_redo,
    icon_remove_group,
    icon_rename_group,
    icon_undo,
)
from .model import TYPE_GROUP
from .tree_model import ROLE_ID, ROLE_TYPE, LayerOrderItemModel
from .tree_view import LayerOrderTree


class _ReportingCheckBox(QCheckBox):
    """A checkbox whose click reports the wish instead of toggling: it keeps
    showing the real state until the ViewController renders the outcome.
    (A signal, not a closure: a closure capturing the box leaked it.)"""

    toggle_requested = pyqtSignal(bool)

    def nextCheckState(self):
        self.toggle_requested.emit(not self.isChecked())


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
    expand_requested = pyqtSignal(str, bool)             # group id, expanded
    check_requested = pyqtSignal(str, bool)              # item id (layer or group), checked
    drop_requested = pyqtSignal(list, str, str)          # moving ids, target id, DROP_* position
    control_toggled = pyqtSignal(bool)
    verbose_toggled = pyqtSignal(bool)
    undo_requested = pyqtSignal(bool)                    # True = undo, False = redo (toolbar buttons)

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

        self.btn_add_group = tool_button(icon_add_group(), tr(ADD_GROUP))
        self.btn_rename_group = tool_button(icon_rename_group(), tr(RENAME_GROUP))
        self.btn_del_group = tool_button(icon_remove_group(), tr(REMOVE_GROUP))
        self.btn_move_up = tool_button(icon_move_up(), f"{tr(MOVE_UP)} (Ctrl+↑)")
        self.btn_move_down = tool_button(icon_move_down(), f"{tr(MOVE_DOWN)} (Ctrl+↓)")
        for b in (self.btn_add_group, self.btn_rename_group, self.btn_del_group):
            head.addWidget(b)
        head.addSpacing(8)
        head.addWidget(self.btn_move_up)
        head.addWidget(self.btn_move_down)
        head.addSpacing(8)
        self.btn_undo = tool_button(icon_undo(), tr(UNDO))
        self.btn_redo = tool_button(icon_redo(), tr(REDO))
        head.addWidget(self.btn_undo)
        head.addWidget(self.btn_redo)
        self._undo_state = (False, "", False, "")
        head.addStretch(1)

        self.tree = LayerOrderTree()
        self.tree.setModel(self.item_model)
        self.tree.setToolTip(tr(DRAG_TO_REORDER))
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        lay.addWidget(self.tree)

        # Translated by QGIS (i18n.py)
        self.chk_control = _ReportingCheckBox(tr(CONTROL_RENDERING_ORDER))
        # Debug aid only: shown when QGIS runs with QGIS_DEBUG set
        # (Settings → Options → System → Environment)
        self.chk_verbose = _ReportingCheckBox("Verbose logging (AdvancedLayerOrder log tab)")
        self.chk_verbose.setVisible(bool(os.environ.get("QGIS_DEBUG")))
        for c in (self.chk_control, self.chk_verbose):
            lay.addWidget(c)

    def _connect_signals(self):
        # Bound methods and signals only: PyQt keeps a lambda or closure
        # that captures self alive from C++, where the GC can't see it, so
        # the panel was never freed on unload (tests/qgis/check_no_leaks.py).
        self.btn_add_group.clicked.connect(self._request_create_group)
        self.btn_rename_group.clicked.connect(self._request_rename)
        self.btn_del_group.clicked.connect(self._request_delete_groups)
        self.btn_move_up.clicked.connect(self._request_move_up)
        self.btn_move_down.clicked.connect(self._request_move_down)
        self.tree.move_intent.connect(self._request_move_by_one)
        self.chk_control.toggle_requested.connect(self.control_toggled)
        self.chk_verbose.toggle_requested.connect(self.verbose_toggled)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.selectionModel().selectionChanged.connect(self._update_group_buttons)
        self.tree.expand_intent.connect(self.expand_requested)
        self.btn_undo.clicked.connect(self._request_undo)
        self.btn_redo.clicked.connect(self._request_redo)
        self.item_model.drop_intent.connect(self.drop_requested)
        self.item_model.check_intent.connect(self.check_requested)

    def _request_create_group(self):
        self.create_group_requested.emit(self.selected_ids())

    def _request_delete_groups(self):
        self.delete_groups_requested.emit(self.selected_group_ids())

    def _request_move_up(self):
        self._request_move_by_one(True)

    def _request_move_down(self):
        self._request_move_by_one(False)

    def _request_undo(self):
        self.undo_requested.emit(True)

    def _request_redo(self):
        self.undo_requested.emit(False)

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
        can_undo, _undo_text, can_redo, _redo_text = self._undo_state
        self.btn_undo.setEnabled(enabled and can_undo)
        self.btn_redo.setEnabled(enabled and can_redo)

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
        act_create = menu.addAction(icon_add_group(), tr(ADD_GROUP))
        act_rename = menu.addAction(icon_rename_group(), tr(RENAME_GROUP))
        act_delete = menu.addAction(icon_remove_group(), tr(REMOVE_GROUP))
        menu.addSeparator()
        act_up = menu.addAction(icon_move_up(), tr(MOVE_UP))
        act_down = menu.addAction(icon_move_down(), tr(MOVE_DOWN))
        act_top = menu.addAction(tr(MOVE_TO_TOP))
        act_bottom = menu.addAction(tr(MOVE_TO_BOTTOM))
        act_rename.setEnabled(len(groups) == 1)
        act_delete.setEnabled(bool(groups))
        for a in (act_up, act_down, act_top, act_bottom):
            a.setEnabled(bool(ids))

        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is act_create:
            self.create_group_requested.emit(ids)
        elif chosen is act_rename:
            self.rename_group_requested.emit(groups[0])
        elif chosen is act_delete:
            self.delete_groups_requested.emit(groups)
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
        for w in (self.tree, self.btn_add_group):
            w.setEnabled(enabled)
        self._update_group_buttons()

    def render_undo_state(self, can_undo: bool, undo_text: str, can_redo: bool, redo_text: str):
        """Undo / Redo buttons: availability and the step they would act on."""
        self._undo_state = (can_undo, undo_text, can_redo, redo_text)
        undo, redo = tr(UNDO), tr(REDO)
        self.btn_undo.setToolTip(f"{undo}: {undo_text}" if can_undo and undo_text else undo)
        self.btn_redo.setToolTip(f"{redo}: {redo_text}" if can_redo and redo_text else redo)
        self._update_group_buttons()

    def render_verbose(self, checked: bool):
        self.chk_verbose.setChecked(bool(checked))
