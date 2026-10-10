"""LayerOrderItemModel — the Qt item model behind the tree (part of the View).

It presents what the ViewController last rendered (node dicts) to a
QTreeView, and turns Qt's editing entry points into intents:

* ``dropMimeData`` → ``drop_intent(moving_ids, target_id, position)``
* ``setData(CheckStateRole)`` → ``check_intent(item_id, checked)``

Both return False, so Qt never changes anything itself (a drop moves no
row, a click flips no checkbox). The content only changes through
``render()``, ``set_name()`` and ``set_visible()``.

``render()`` keeps item identity: when the set of ids is unchanged it is a
layout change with persistent indexes remapped by id, so the view keeps
its selection, expansion and scroll position on its own.
"""
from __future__ import annotations

import json

from qgis.PyQt.QtCore import QAbstractItemModel, QMimeData, QModelIndex, Qt, pyqtSignal
from qgis.PyQt.QtGui import QIcon

from .icons import icon_group
from .model import TYPE_GROUP, TYPE_LAYER

# Semantic drop positions
DROP_ON = "on"          # onto an item
DROP_ABOVE = "above"    # just above an item, same parent
DROP_BELOW = "below"    # just below an item, same parent
DROP_END = "end"        # empty area below the last row → bottom of the top level

ROLE_ID = Qt.ItemDataRole.UserRole + 1
ROLE_TYPE = Qt.ItemDataRole.UserRole + 2

MIME_IDS = "application/x-advanced-layer-order-ids"

_FLAGS = (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable
          | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled)


class _Item:
    """One rendered row. Plain data; the tree of these is replaced on render
    (never restructured), so each item's row is fixed at construction. A
    group's checkbox comes from its visible/total layer counts, kept up to
    date by LayerOrderItemModel.set_visible: both are O(1) to read."""

    __slots__ = ("children", "icon", "id", "layers", "name", "parent", "row_",
                 "shown", "type", "visible")

    def __init__(self, node: dict | None, parent: _Item | None):
        self.parent = parent
        node = node or {"type": TYPE_GROUP, "id": "", "name": ""}
        self.id: str = node["id"]
        self.type: str = node["type"]
        self.name: str = node.get("name", "")
        self.visible: bool = bool(node.get("visible", True))
        self.icon: QIcon | None = node.get("icon")
        self.children = [_Item(ch, self) for ch in node.get("children", [])]
        self.row_ = 0
        for i, ch in enumerate(self.children):
            ch.row_ = i
        if self.type == TYPE_LAYER:
            self.layers, self.shown = 1, int(self.visible)
        else:
            self.layers = sum(ch.layers for ch in self.children)
            self.shown = sum(ch.shown for ch in self.children)

    def row(self) -> int:
        return self.row_

    def walk(self):
        for ch in self.children:
            yield ch
            yield from ch.walk()

    def check_state(self) -> Qt.CheckState:
        if self.type == TYPE_LAYER:
            return Qt.CheckState.Checked if self.visible else Qt.CheckState.Unchecked
        if self.layers and not self.shown:
            return Qt.CheckState.Unchecked
        if 0 < self.shown < self.layers:
            return Qt.CheckState.PartiallyChecked
        return Qt.CheckState.Checked


class LayerOrderItemModel(QAbstractItemModel):
    """Read-only presentation of the rendered tree; edits become intents."""

    drop_intent = pyqtSignal(list, str, str)   # moving ids, target id, DROP_* position
    check_intent = pyqtSignal(str, bool)       # item id, checked

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root = _Item(None, None)
        self._by_id: dict[str, _Item] = {}

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def render(self, nodes: list[dict]) -> None:
        """Show `nodes` (see LayerOrderView.render for the dict shape)."""
        new_root = _Item({"type": TYPE_GROUP, "id": "", "children": nodes}, None)
        new_by_id = {it.id: it for it in new_root.walk()}
        if new_by_id.keys() == self._by_id.keys():
            # Same items, new arrangement/data: keep identity for the view
            self.layoutAboutToBeChanged.emit()
            old = self.persistentIndexList()
            ids = [ix.data(ROLE_ID) for ix in old]
            self._root, self._by_id = new_root, new_by_id
            self.changePersistentIndexList(old, [self.index_of(i) for i in ids])
            self.layoutChanged.emit()
            self._emit_data_changed(self._root)
        else:
            self.beginResetModel()
            self._root, self._by_id = new_root, new_by_id
            self.endResetModel()

    def set_name(self, item_id: str, name: str) -> None:
        item = self._by_id.get(item_id)
        if item is not None:
            item.name = name
            ix = self.index_of(item_id)
            self.dataChanged.emit(ix, ix, [Qt.ItemDataRole.DisplayRole])

    def set_visible(self, layer_id: str, visible: bool) -> None:
        """Update a layer's checkbox and the derived state of its groups."""
        item = self._by_id.get(layer_id)
        if item is None or item.visible == bool(visible):
            return
        item.visible = bool(visible)
        delta = 1 if item.visible else -1
        node = item
        while node is not None:
            node.shown += delta
            node = node.parent
        while item is not None and item is not self._root:
            ix = self.index_of(item.id)
            self.dataChanged.emit(ix, ix, [Qt.ItemDataRole.CheckStateRole])
            item = item.parent

    def index_of(self, item_id: str) -> QModelIndex:
        item = self._by_id.get(item_id)
        if item is None:
            return QModelIndex()
        return self.createIndex(item.row(), 0, item)

    def nodes_in_order(self) -> list[str]:
        """All ids in display (pre-order) order."""
        return [it.id for it in self._root.walk()]

    def _emit_data_changed(self, parent: _Item) -> None:
        if parent.children:
            first = self.createIndex(0, 0, parent.children[0])
            last = self.createIndex(len(parent.children) - 1, 0, parent.children[-1])
            self.dataChanged.emit(first, last)
        for ch in parent.children:
            self._emit_data_changed(ch)

    # ------------------------------------------------------------------
    # QAbstractItemModel — structure
    # ------------------------------------------------------------------
    def _item(self, index: QModelIndex) -> _Item:
        return index.internalPointer() if index.isValid() else self._root

    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if column != 0 or not self.hasIndex(row, column, parent):
            return QModelIndex()
        return self.createIndex(row, 0, self._item(parent).children[row])

    def parent(self, index: QModelIndex = None):
        if index is None:
            return super().parent()  # QObject.parent()
        if not index.isValid():
            return QModelIndex()
        p = index.internalPointer().parent
        if p is None or p is self._root:
            return QModelIndex()
        return self.createIndex(p.row(), 0, p)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.column() > 0 else len(self._item(parent).children)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 1

    # ------------------------------------------------------------------
    # QAbstractItemModel — data
    # ------------------------------------------------------------------
    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item: _Item = index.internalPointer()
        if role == Qt.ItemDataRole.DisplayRole:
            return item.name
        if role == Qt.ItemDataRole.DecorationRole:
            return icon_group() if item.type == TYPE_GROUP else item.icon
        if role == Qt.ItemDataRole.CheckStateRole:
            return item.check_state()
        if role == ROLE_ID:
            return item.id
        if role == ROLE_TYPE:
            return item.type
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        # The root accepts drops too (empty area below the last row)
        return _FLAGS if index.isValid() else Qt.ItemFlag.ItemIsDropEnabled

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:
        """A checkbox click: report it, change nothing."""
        if index.isValid() and role == Qt.ItemDataRole.CheckStateRole:
            item: _Item = index.internalPointer()
            self.check_intent.emit(item.id, item.check_state() != Qt.CheckState.Checked)
        return False

    # ------------------------------------------------------------------
    # QAbstractItemModel — drag and drop
    # ------------------------------------------------------------------
    def supportedDragActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def mimeTypes(self) -> list[str]:
        return [MIME_IDS]

    def mimeData(self, indexes) -> QMimeData:
        ids = list(dict.fromkeys(ix.data(ROLE_ID) for ix in indexes if ix.isValid()))
        mime = QMimeData()
        mime.setData(MIME_IDS, json.dumps(ids).encode("utf-8"))
        return mime

    @staticmethod
    def _moving_ids(mime: QMimeData) -> list[str]:
        if mime is None or not mime.hasFormat(MIME_IDS):
            return []
        try:
            return [str(i) for i in json.loads(bytes(mime.data(MIME_IDS)).decode("utf-8"))]
        except ValueError:
            return []

    def canDropMimeData(self, mime, action, row, column, parent) -> bool:
        moving = set(self._moving_ids(mime))
        if not moving:
            return False
        item = self._item(parent)
        while item is not None and item is not self._root:
            if item.id in moving:
                return False  # into a dragged item or its subtree
            item = item.parent
        return True

    def dropMimeData(self, mime, action, row, column, parent) -> bool:
        """Translate Qt's (row, parent) drop into an intent; never mutate."""
        if not self.canDropMimeData(mime, action, row, column, parent):
            return False
        moving = self._moving_ids(mime)
        target, position = self._drop_target(set(moving), row, parent)
        self.drop_intent.emit(moving, target, position)
        return False  # nothing was moved here: Qt must not remove source rows

    def _drop_target(self, moving: set, row: int, parent: QModelIndex) -> tuple[str, str]:
        group = self._item(parent)
        if row < 0:
            return (group.id, DROP_ON) if parent.isValid() else ("", DROP_END)
        siblings = group.children
        # Anchor on the nearest sibling that is not being dragged
        after = next((s for s in siblings[row:] if s.id not in moving), None)
        if after is not None:
            return after.id, DROP_ABOVE
        before = next((s for s in reversed(siblings[:row]) if s.id not in moving), None)
        if before is not None:
            return before.id, DROP_BELOW
        return (group.id, DROP_ON) if parent.isValid() else ("", DROP_END)
