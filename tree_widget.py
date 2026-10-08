"""BetterLayerTree — a QTreeWidget that reports intents and never acts on them.

Qt item views normally change themselves in response to the user: a drop
moves rows, a click on a checkbox flips it (and, with auto-tristate, its
children), a click on a branch arrow expands it. Here every one of those
built-in actions is disabled and replaced by a signal carrying the intent:

    drop_intent(moving_ids, target_id, position)
    expand_intent(group_id, expanded)
    check_intent(item_id, checked)

The ViewController decides what happens, updates the Model, and the View is
re-rendered from the Model. The tree's only own state is presentation:
selection, scroll position, hover, and the drop indicator.
"""
from qgis.PyQt.QtCore import QEvent, Qt, pyqtSignal
from qgis.PyQt.QtGui import QDropEvent
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
)

from .logger import _vlog
from .tree_utils import ROLE_ID, ROLE_TYPE, TYPE_GROUP


# Semantic drop positions
DROP_ON = "on"          # onto an item
DROP_ABOVE = "above"    # just above an item, same parent
DROP_BELOW = "below"    # just below an item, same parent
DROP_END = "end"        # empty area below the last row → bottom of the top level


class _CheckIntentDelegate(QStyledItemDelegate):
    """Turns checkbox clicks into an intent instead of toggling the item."""

    def __init__(self, tree: "BetterLayerTree"):
        super().__init__(tree)
        self._tree = tree

    def editorEvent(self, event, model, option, index):
        if not (index.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return super().editorEvent(event, model, option, index)
        t = event.type()
        if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                 QEvent.Type.MouseButtonDblClick):
            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)
            widget = option.widget
            style = widget.style() if widget is not None else QApplication.style()
            box = style.subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, widget)
            if not box.contains(event.position().toPoint()):
                return super().editorEvent(event, model, option, index)
            if t == QEvent.Type.MouseButtonRelease:
                self._tree._emit_check_intent(index)
            return True  # consumed: Qt must not call setData
        if t == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Select):
            self._tree._emit_check_intent(index)
            return True
        return super().editorEvent(event, model, option, index)


class BetterLayerTree(QTreeWidget):
    """QTreeWidget that emits intents instead of mutating itself."""

    drop_intent = pyqtSignal(list, str, str)    # moving_ids (display order), target_id, position
    expand_intent = pyqtSignal(str, bool)       # group_id, expanded
    check_intent = pyqtSignal(str, bool)        # item_id, checked

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        # The user can't expand/collapse directly; we report the intent
        self.setItemsExpandable(False)
        self.setExpandsOnDoubleClick(False)
        self.setItemDelegate(_CheckIntentDelegate(self))
        self.itemDoubleClicked.connect(self._on_double_clicked)

    # ------------------------------------------------------------------
    # Expand / collapse
    # ------------------------------------------------------------------
    def _toggle_intent(self, item: QTreeWidgetItem) -> bool:
        if item is None or item.data(0, ROLE_TYPE) != TYPE_GROUP:
            return False
        self.expand_intent.emit(item.data(0, ROLE_ID), not item.isExpanded())
        return True

    def _on_double_clicked(self, item, _column):
        self._toggle_intent(item)

    def mousePressEvent(self, e):
        item = self.itemAt(e.position().toPoint())
        if item is not None and item.childCount():
            rect = self.visualItemRect(item)
            x = e.position().toPoint().x()
            if rect.left() - self.indentation() <= x < rect.left():  # branch arrow
                self._toggle_intent(item)
                return
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        item = self.currentItem()
        if item is not None and item.data(0, ROLE_TYPE) == TYPE_GROUP:
            if e.key() == Qt.Key.Key_Right and not item.isExpanded() and item.childCount():
                self.expand_intent.emit(item.data(0, ROLE_ID), True)
                return
            if e.key() == Qt.Key.Key_Left and item.isExpanded():
                self.expand_intent.emit(item.data(0, ROLE_ID), False)
                return
        super().keyPressEvent(e)

    # ------------------------------------------------------------------
    # Check
    # ------------------------------------------------------------------
    def _emit_check_intent(self, index):
        item = self.itemFromIndex(index)
        if item is None:
            return
        # Partially checked groups and unchecked items ask to become checked
        self.check_intent.emit(item.data(0, ROLE_ID),
                               item.checkState(0) != Qt.CheckState.Checked)

    # ------------------------------------------------------------------
    # Drop
    # ------------------------------------------------------------------
    def _visual_key(self, item):
        """Tuple of row indices from the root — sorts items in display order."""
        key = []
        cur = item
        while cur is not None:
            p = cur.parent()
            key.append(p.indexOfChild(cur) if p is not None else self.indexOfTopLevelItem(cur))
            cur = p
        return tuple(reversed(key))

    def moving_items(self):
        """Selected visible items without a selected ancestor, in display order."""
        selected = [it for it in self.selectedItems() if not it.isHidden()]
        selected_ids = {id(x) for x in selected}

        def has_selected_ancestor(it):
            p = it.parent()
            while p is not None:
                if id(p) in selected_ids:
                    return True
                p = p.parent()
            return False

        return sorted((it for it in selected if not has_selected_ancestor(it)),
                      key=self._visual_key)

    @staticmethod
    def _is_ancestor(anc: QTreeWidgetItem, node: QTreeWidgetItem) -> bool:
        """True if `anc` is `node` or one of its ancestors."""
        cur = node
        while cur is not None:
            if cur is anc:
                return True
            cur = cur.parent()
        return False

    def dropEvent(self, e: QDropEvent):
        # Never let QTreeWidget move rows itself (super().dropEvent would).
        # IgnoreAction also stops startDrag() from deleting the source rows.
        e.setDropAction(Qt.DropAction.IgnoreAction)
        e.accept()
        if e.source() is not self:
            return
        moving = self.moving_items()
        if not moving:
            return

        pos = self.dropIndicatorPosition()
        target = self.itemAt(e.position().toPoint())
        if pos == QAbstractItemView.DropIndicatorPosition.OnViewport or target is None:
            position, target_id = DROP_END, ""
        else:
            if any(it is target for it in moving):
                return  # onto / next to itself
            if any(self._is_ancestor(it, target) for it in moving):
                return  # into its own subtree
            position = {
                QAbstractItemView.DropIndicatorPosition.OnItem: DROP_ON,
                QAbstractItemView.DropIndicatorPosition.AboveItem: DROP_ABOVE,
            }.get(pos, DROP_BELOW)
            target_id = target.data(0, ROLE_ID)

        moving_ids = [it.data(0, ROLE_ID) for it in moving]
        _vlog(f"[TW] drop_intent: moving={moving_ids} target={target_id} pos={position}")
        self.drop_intent.emit(moving_ids, target_id, position)
