"""LayerOrderTree — the QTreeView of the dock. It reports, it never acts.

Drops and checkbox clicks already arrive as intents through the item model
(tree_model.py). What is left here is expansion: Qt would expand or
collapse a group by itself on a branch-arrow click, a double-click or the
Left/Right keys. Those are disabled and reported as

    expand_intent(group_id, expanded)

The ViewController records the wish in the Model, which renders it back.

Ctrl+↑ / Ctrl+↓ (Cmd on macOS) are reported as ``move_intent(up)``; the
dock adds the current selection.
"""
from qgis.PyQt.QtCore import QModelIndex, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import QAbstractItemView, QTreeView

from .model import TYPE_GROUP
from .tree_model import ROLE_ID, ROLE_TYPE


class LayerOrderTree(QTreeView):
    expand_intent = pyqtSignal(str, bool)   # group id, expanded
    move_intent = pyqtSignal(bool)          # up

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setRootIsDecorated(True)
        self.setItemsExpandable(False)        # we report instead
        self.setExpandsOnDoubleClick(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.doubleClicked.connect(self._toggle_intent)

    def _toggle_intent(self, index: QModelIndex) -> bool:
        if not index.isValid() or index.data(ROLE_TYPE) != TYPE_GROUP:
            return False
        self.expand_intent.emit(index.data(ROLE_ID), not self.isExpanded(index))
        return True

    def mousePressEvent(self, e):
        index = self.indexAt(e.position().toPoint())
        if index.isValid() and self.model().hasChildren(index):
            left = self.visualRect(index).left()
            if left - self.indentation() <= e.position().toPoint().x() < left:  # branch arrow
                self._toggle_intent(index)
                return
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        # Arrow keys carry KeypadModifier on macOS: ignore it
        mods = e.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if mods == Qt.KeyboardModifier.ControlModifier and e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.move_intent.emit(e.key() == Qt.Key.Key_Up)
            return
        index = self.currentIndex()
        if index.isValid() and index.data(ROLE_TYPE) == TYPE_GROUP:
            if e.key() == Qt.Key.Key_Right and not self.isExpanded(index) and self.model().hasChildren(index):
                self.expand_intent.emit(index.data(ROLE_ID), True)
                return
            if e.key() == Qt.Key.Key_Left and self.isExpanded(index):
                self.expand_intent.emit(index.data(ROLE_ID), False)
                return
        super().keyPressEvent(e)
