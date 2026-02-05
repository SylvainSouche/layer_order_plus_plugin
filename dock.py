# dock.py
import json
import uuid

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QDropEvent, QKeySequence
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QHBoxLayout,
    QPushButton,
    QShortcut,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QUndoCommand,
    QUndoStack,
)

from qgis.core import QgsProject


ROLE_TYPE = Qt.UserRole + 1   # "group" | "layer"
ROLE_ID   = Qt.UserRole + 2   # group_id | layer_id

TYPE_GROUP = "group"
TYPE_LAYER = "layer"


def _new_group_id():
    return "grp_" + uuid.uuid4().hex[:10]


class TreeStateCommand(QUndoCommand):
    def __init__(self, dock, before_json: str, after_json: str, text: str):
        super().__init__(text)
        self._dock = dock
        self._before = before_json
        self._after = after_json

    def undo(self):
        self._dock._apply_tree_state_from_undo(self._before)

    def redo(self):
        self._dock._apply_tree_state_from_undo(self._after)


class BetterLayerTree(QTreeWidget):
    """
    Drop rules:
      - OnItem on GROUP  -> move items to TOP of that group
      - OnItem on LAYER  -> move items ABOVE that layer (same parent as target layer)
      - AboveItem        -> move items ABOVE target (same parent as target)
      - BelowItem        -> move items BELOW target (same parent as target)
    Safeguards:
      - ignore OnItem drop onto a moving item
      - ignore moving an item/group onto its own descendant
    """
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._after_drop_cb = None         # (before_json, after_json) -> None
        self._state_provider = None        # () -> json_str
        self._just_custom_dropped = False

    def set_after_drop_callback(self, cb):
        self._after_drop_cb = cb

    def set_state_provider(self, cb):
        self._state_provider = cb

    def _event_pos_point(self, e: QDropEvent):
        return e.position().toPoint() if hasattr(e, "position") else e.pos()

    def _index_in_parent(self, item: QTreeWidgetItem) -> int:
        p = item.parent()
        return p.indexOfChild(item) if p is not None else self.indexOfTopLevelItem(item)

    def _take_item(self, item: QTreeWidgetItem) -> QTreeWidgetItem:
        p = item.parent()
        if p is None:
            return self.takeTopLevelItem(self.indexOfTopLevelItem(item))
        return p.takeChild(p.indexOfChild(item))

    def _insert_item(self, parent: QTreeWidgetItem, idx: int, item: QTreeWidgetItem):
        if parent is None:
            self.insertTopLevelItem(idx, item)
        else:
            parent.insertChild(idx, item)

    def _path_key(self, item: QTreeWidgetItem):
        path = []
        cur = item
        while cur is not None:
            path.append(self._index_in_parent(cur))
            cur = cur.parent()
        return tuple(reversed(path))

    def _is_ancestor(self, anc: QTreeWidgetItem, node: QTreeWidgetItem) -> bool:
        anc_id = id(anc)
        cur = node
        while cur is not None:
            if id(cur) == anc_id:
                return True
            cur = cur.parent()
        return False

    def dropEvent(self, e: QDropEvent):
        p = self._event_pos_point(e)
        pos = self.dropIndicatorPosition()

        if pos in (QAbstractItemView.OnItem, QAbstractItemView.AboveItem, QAbstractItemView.BelowItem):
            target = self.itemAt(p)
            if target is None or not self._state_provider:
                super().dropEvent(e)
                return

            before = self._state_provider()

            selected = self.selectedItems()
            selected_ids = {id(x) for x in selected}

            moving = [it for it in selected if (it.parent() is None or id(it.parent()) not in selected_ids)]
            if not moving:
                e.ignore()
                return

            moving_ids = {id(x) for x in moving}

            if pos == QAbstractItemView.OnItem and id(target) in moving_ids:
                e.ignore()
                return

            for it in moving:
                if self._is_ancestor(it, target):
                    e.ignore()
                    return

            tt = target.data(0, ROLE_TYPE)

            if pos == QAbstractItemView.OnItem:
                if tt == TYPE_GROUP:
                    dest_parent = target
                    dest_index = 0
                else:
                    dest_parent = target.parent()
                    dest_index = self._index_in_parent(target)
            elif pos == QAbstractItemView.AboveItem:
                dest_parent = target.parent()
                dest_index = self._index_in_parent(target)
            else:  # BelowItem
                dest_parent = target.parent()
                dest_index = self._index_in_parent(target) + 1

            moving.sort(key=self._path_key)

            for it in moving:
                if it.parent() == dest_parent and self._index_in_parent(it) < dest_index:
                    dest_index -= 1

            self._just_custom_dropped = True

            taken = []
            for it in reversed(moving):
                taken.append(self._take_item(it))
            taken.reverse()

            for it in taken:
                self._insert_item(dest_parent, dest_index, it)
                dest_index += 1

            self.clearSelection()
            for it in taken:
                it.setSelected(True)

            e.accept()

            after = self._state_provider()
            if self._after_drop_cb:
                self._after_drop_cb(before, after)

            QTimer.singleShot(0, lambda: setattr(self, "_just_custom_dropped", False))
            return

        super().dropEvent(e)


class BetterLayerOrderDock(QDockWidget):
    def __init__(self, iface):
        super().__init__("Better Layer Order", iface.mainWindow())
        self.iface = iface
        self._save_cb = None

        # HARD GUARD: do not apply custom order while project is loading
        self._apply_suspended = True

        # loading guard: do not autosave while building UI from persisted state
        self._loading = False

        # undo
        self._in_undo = False
        self._snapshot = ""
        self.undo_stack = QUndoStack(self)

        # rename debounce
        self._rename_pending = False
        self._rename_before = ""
        self._rename_timer = QTimer(self)
        self._rename_timer.setSingleShot(True)
        self._rename_timer.timeout.connect(self._commit_rename_undo)

        # safe apply debounce
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.timeout.connect(self._apply_now)

        rootw = QWidget()
        self.setWidget(rootw)

        lay = QVBoxLayout(rootw)
        head = QHBoxLayout()
        lay.addLayout(head)

        self.btn_add_group = QPushButton("Create group")
        self.btn_del_group = QPushButton("Delete group")
        head.addWidget(self.btn_add_group)
        head.addWidget(self.btn_del_group)
        head.addStretch(1)

        self.tree = BetterLayerTree()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setDragDropMode(QAbstractItemView.InternalMove)
        lay.addWidget(self.tree)

        # shortcuts
        QShortcut(QKeySequence.Undo, self, activated=self.undo_stack.undo)
        QShortcut(QKeySequence.Redo, self, activated=self.undo_stack.redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self.undo_stack.redo)

        # tree callbacks
        self.tree.set_state_provider(self._serialize_tree)
        self.tree.set_after_drop_callback(self._on_tree_changed_external)

        self.btn_add_group.clicked.connect(self.create_group_from_selection)
        self.btn_del_group.clicked.connect(self.delete_selected_group)

        self.tree.model().rowsMoved.connect(self._on_rows_moved)
        self.tree.itemChanged.connect(self._on_item_changed)

    # ---------------- external control ----------------
    def set_apply_suspended(self, suspended: bool):
        self._apply_suspended = bool(suspended)
        if not self._apply_suspended:
            self.request_apply()

    # ---------------- persistence ----------------
    def set_save_callback(self, cb):
        self._save_cb = cb

    def _autosave(self):
        if not self._save_cb:
            return
        if self._loading:
            return
        if self._in_undo:
            return
        if self._apply_suspended:
            return
        self._save_cb(self._serialize_tree())

    def _serialize_tree(self) -> str:
        def ser_item(item: QTreeWidgetItem):
            t = item.data(0, ROLE_TYPE)
            if t == TYPE_GROUP:
                out = {
                    "type": TYPE_GROUP,
                    "id": item.data(0, ROLE_ID),
                    "name": item.text(0),
                    "expanded": True,
                    "children": [],
                }
                for i in range(item.childCount()):
                    out["children"].append(ser_item(item.child(i)))
                return out
            return {"type": TYPE_LAYER, "id": item.data(0, ROLE_ID)}

        root = {"children": []}
        for i in range(self.tree.topLevelItemCount()):
            root["children"].append(ser_item(self.tree.topLevelItem(i)))
        return json.dumps(root, ensure_ascii=False)

    def load_from_project(self, raw_json: str):
        self._loading = True
        self.tree.blockSignals(True)
        try:
            self.tree.clear()

            if not (raw_json or "").strip():
                self._populate_all_layers_top_level()
                self._snapshot = self._serialize_tree()
                return

            try:
                obj = json.loads(raw_json)
            except Exception:
                self._populate_all_layers_top_level()
                self._snapshot = self._serialize_tree()
                return

            proj = QgsProject.instance()

            def build(parent_item, node):
                ntype = node.get("type")

                if ntype == TYPE_GROUP:
                    it = QTreeWidgetItem()
                    it.setText(0, node.get("name", "Group"))
                    it.setData(0, ROLE_TYPE, TYPE_GROUP)
                    it.setData(0, ROLE_ID, node.get("id", _new_group_id()))
                    it.setFlags(it.flags() | Qt.ItemIsEditable)
                    it.setExpanded(True)

                    if parent_item is None:
                        self.tree.addTopLevelItem(it)
                    else:
                        parent_item.addChild(it)

                    for ch in node.get("children", []):
                        build(it, ch)
                    it.setExpanded(True)
                    return

                if ntype == TYPE_LAYER:
                    lyr_id = node.get("id", "")
                    lyr = proj.mapLayer(lyr_id)
                    if not lyr:
                        return
                    self._add_layer_item(parent_item, lyr)

            for top in obj.get("children", []):
                build(None, top)

            self._append_missing_layers()
            self._snapshot = self._serialize_tree()
        finally:
            self.tree.blockSignals(False)
            self._loading = False

    # ---------------- population ----------------
    def _insert_layer_item(self, parent_item, index: int, lyr):
        it = QTreeWidgetItem()
        it.setText(0, lyr.name())
        it.setData(0, ROLE_TYPE, TYPE_LAYER)
        it.setData(0, ROLE_ID, lyr.id())
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)

        if parent_item is None:
            self.tree.insertTopLevelItem(index, it)
        else:
            parent_item.insertChild(index, it)
        return it

    def _populate_all_layers_top_level(self):
        proj = QgsProject.instance()
        for lyr in proj.mapLayers().values():
            self._add_layer_item(None, lyr)

    def _append_missing_layers(self):
        proj = QgsProject.instance()
        existing = set(self._iter_all_layer_ids())
        for lyr in proj.mapLayers().values():
            if lyr.id() not in existing:
                self._add_layer_item(None, lyr)

    def _iter_all_layer_ids(self):
        ids = []

        def walk(item):
            if item.data(0, ROLE_TYPE) == TYPE_LAYER:
                ids.append(item.data(0, ROLE_ID))
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        return ids

    def _add_layer_item(self, parent_item, lyr):
        it = QTreeWidgetItem()
        it.setText(0, lyr.name())
        it.setData(0, ROLE_TYPE, TYPE_LAYER)
        it.setData(0, ROLE_ID, lyr.id())
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)

        if parent_item is None:
            self.tree.addTopLevelItem(it)
        else:
            parent_item.addChild(it)
        return it

    # ---------------- grouping behavior ----------------
    def create_group_from_selection(self):
        if self._in_undo or self._loading:
            return

        before = self._serialize_tree()

        sel = self.tree.selectedItems()
        if not sel:
            return

        parent = sel[0].parent()
        if any(s.parent() != parent for s in sel):
            return

        new_group = QTreeWidgetItem()
        new_group.setText(0, "New group")
        new_group.setData(0, ROLE_TYPE, TYPE_GROUP)
        new_group.setData(0, ROLE_ID, _new_group_id())
        new_group.setFlags(new_group.flags() | Qt.ItemIsEditable)
        new_group.setExpanded(True)

        siblings = self._children_list(parent)
        first_idx = min(siblings.index(s) for s in sel)

        if parent is None:
            self.tree.insertTopLevelItem(first_idx, new_group)
        else:
            parent.insertChild(first_idx, new_group)

        sel_sorted = sorted(sel, key=lambda x: siblings.index(x))
        for item in sel_sorted:
            self._take_item(item)
            new_group.addChild(item)

        new_group.setExpanded(True)
        self.tree.setCurrentItem(new_group)
        self.tree.editItem(new_group, 0)

        self.request_apply()
        after = self._serialize_tree()
        self._push_undo(before, after, "Create group")
        self._autosave()

    def delete_selected_group(self):
        if self._in_undo or self._loading:
            return

        before = self._serialize_tree()

        sel = self.tree.selectedItems()
        if not sel:
            return

        g = sel[0]
        if g.data(0, ROLE_TYPE) != TYPE_GROUP:
            return

        parent = g.parent()
        idx = self._index_in_parent(g)

        children = []
        while g.childCount():
            children.append(g.takeChild(0))

        self._take_item(g)

        if parent is None:
            for i, ch in enumerate(children):
                self.tree.insertTopLevelItem(idx + i, ch)
        else:
            for i, ch in enumerate(children):
                parent.insertChild(idx + i, ch)

        self.request_apply()
        after = self._serialize_tree()
        self._push_undo(before, after, "Delete group")
        self._autosave()

    # ---------------- layer order integration ----------------
    def _flatten_to_qgs_layers(self):
        proj = QgsProject.instance()
        out = []

        def walk(item):
            t = item.data(0, ROLE_TYPE)
            if t == TYPE_LAYER:
                lyr = proj.mapLayer(item.data(0, ROLE_ID))
                if lyr:
                    out.append(lyr)
            else:
                for i in range(item.childCount()):
                    walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        return out

    # ---------------- QGIS events ----------------
    def on_layers_added(self, layers):
        if self._in_undo or self._loading:
            return

        before = self._serialize_tree()

        existing = set(self._iter_all_layer_ids())
        new_layers = [lyr for lyr in layers if lyr.id() not in existing]
        if not new_layers:
            return

        anchor = self.tree.currentItem() or (self.tree.selectedItems()[0] if self.tree.selectedItems() else None)

        dest_parent = None
        dest_index = 0

        if anchor is not None:
            atype = anchor.data(0, ROLE_TYPE)
            if atype == TYPE_LAYER:
                dest_parent = anchor.parent()
                dest_index = self._index_in_parent(anchor)
            elif atype == TYPE_GROUP:
                dest_parent = anchor
                dest_index = 0

        for lyr in new_layers:
            self._insert_layer_item(dest_parent, dest_index, lyr)
            dest_index += 1

        self.request_apply()
        after = self._serialize_tree()
        self._push_undo(before, after, "Add layers")
        self._autosave()

    def on_layers_removed(self, layer_ids):
        if self._in_undo or self._loading:
            return

        before = self._serialize_tree()

        layer_ids = set(layer_ids)

        def walk_and_clean(parent_item):
            items = self._children_list(parent_item)
            for it in list(items):
                t = it.data(0, ROLE_TYPE)
                if t == TYPE_LAYER and it.data(0, ROLE_ID) in layer_ids:
                    self._take_item(it)
                elif t == TYPE_GROUP:
                    walk_and_clean(it)

        walk_and_clean(None)

        self.request_apply()
        after = self._serialize_tree()
        self._push_undo(before, after, "Remove layers")
        self._autosave()

    # ---------------- events ----------------
    def _on_tree_changed_external(self, before_json: str, after_json: str):
        if self._in_undo or self._loading:
            return
        self.request_apply()
        self._push_undo(before_json, after_json, "Reorder layers")
        self._autosave()

    def _on_rows_moved(self, *args):
        if self._in_undo or self._loading:
            return
        if getattr(self.tree, "_just_custom_dropped", False):
            return

        before = self._snapshot or self._serialize_tree()
        after = self._serialize_tree()

        self.request_apply()
        self._push_undo(before, after, "Reorder layers")
        self._autosave()

    def _on_item_changed(self, item, col):
        if self._in_undo or self._loading:
            return
        if item.data(0, ROLE_TYPE) != TYPE_GROUP:
            return

        if not self._rename_pending:
            self._rename_pending = True
            self._rename_before = self._snapshot or self._serialize_tree()

        self._rename_timer.start(300)

    def _commit_rename_undo(self):
        if self._in_undo or self._loading:
            self._rename_pending = False
            self._rename_before = ""
            return

        before = self._rename_before or (self._snapshot or self._serialize_tree())
        after = self._serialize_tree()

        self.request_apply()
        self._push_undo(before, after, "Rename group")
        self._autosave()

        self._rename_pending = False
        self._rename_before = ""

    # ---------------- helpers ----------------
    def _children_list(self, parent_item):
        if parent_item is None:
            return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        return [parent_item.child(i) for i in range(parent_item.childCount())]

    def _index_in_parent(self, item):
        p = item.parent()
        sibs = self._children_list(p)
        return sibs.index(item)

    def _take_item(self, item):
        p = item.parent()
        if p is None:
            idx = self.tree.indexOfTopLevelItem(item)
            return self.tree.takeTopLevelItem(idx)
        idx = p.indexOfChild(item)
        return p.takeChild(idx)

    # ---------------- undo core ----------------
    def _apply_tree_state_from_undo(self, raw_json: str):
        self._in_undo = True
        try:
            self.load_from_project(raw_json)  # load has its own guard
            self.request_apply()
            self._snapshot = self._serialize_tree()
        finally:
            self._in_undo = False

    def _push_undo(self, before: str, after: str, text: str):
        if self._in_undo or self._loading:
            return
        if (before or "") == (after or ""):
            self._snapshot = after or ""
            return
        self.undo_stack.push(TreeStateCommand(self, before, after, text))
        self._snapshot = after or ""

    # ---------------- safe apply (no crash) ----------------
    def request_apply(self):
        if self._in_undo or self._loading:
            return
        if self._apply_suspended:
            return
        self._apply_timer.start(50)

    def _apply_now(self):
        if self._in_undo or self._loading:
            return
        if self._apply_suspended:
            return

        proj = QgsProject.instance()
        root = proj.layerTreeRoot()

        layers = self._flatten_to_qgs_layers()

        if layers:
            root.setHasCustomLayerOrder(True)
            root.setCustomLayerOrder(layers)
        else:
            root.setHasCustomLayerOrder(False)

        if self.iface:
            try:
                self.iface.layerTreeView().refresh()
            except Exception:
                pass
            try:
                self.iface.mapCanvas().refresh()
            except Exception:
                pass
    def clear_tree_ui(self):
        # called on project cleared to avoid stale UI
        self._loading = True
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            self._snapshot = ""
        finally:
            self.tree.blockSignals(False)
            self._loading = False