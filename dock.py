# dock.py
import json
import uuid

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QDropEvent, QKeySequence, QUndoCommand, QUndoStack
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
    QInputDialog,
)

from qgis.core import QgsProject, QgsApplication, QgsIconUtils, QgsMapLayer


ROLE_TYPE = Qt.ItemDataRole.UserRole + 1   # "group" | "layer"
ROLE_ID   = Qt.ItemDataRole.UserRole + 2   # group_id | layer_id

TYPE_GROUP = "group"
TYPE_LAYER = "layer"



def _new_group_id():
    return "grp_" + uuid.uuid4().hex[:10]


def _icon_group():
    """Folder icon for order-groups (UI only, not Layers-panel groups)."""
    icon = QgsApplication.getThemeIcon("/mIconFolder.svg")
    if icon.isNull():
        icon = QgsApplication.getThemeIcon("mIconFolder.svg")
    return icon


def _icon_for_layer(layer):
    """Real QGIS layer-type icon (point/line/polygon/raster/…)."""
    if layer is None:
        return QgsApplication.getThemeIcon("/mIconLayer.png")
    try:
        icon = QgsIconUtils.iconForLayer(layer)
        if icon is not None and not icon.isNull():
            return icon
    except Exception:
        pass
    return QgsApplication.getThemeIcon("/mIconLayer.png")


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

        if pos in (QAbstractItemView.DropIndicatorPosition.OnItem, QAbstractItemView.DropIndicatorPosition.AboveItem, QAbstractItemView.DropIndicatorPosition.BelowItem):
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

            if pos == QAbstractItemView.DropIndicatorPosition.OnItem and id(target) in moving_ids:
                e.ignore()
                return

            for it in moving:
                if self._is_ancestor(it, target):
                    e.ignore()
                    return

            tt = target.data(0, ROLE_TYPE)

            if pos == QAbstractItemView.DropIndicatorPosition.OnItem:
                if tt == TYPE_GROUP:
                    dest_parent = target
                    dest_index = 0
                else:
                    dest_parent = target.parent()
                    dest_index = self._index_in_parent(target)
            elif pos == QAbstractItemView.DropIndicatorPosition.AboveItem:
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

        # ---------------- UI ----------------
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
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        lay.addWidget(self.tree)

        # shortcuts
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self.undo_stack.undo)
        QShortcut(QKeySequence.StandardKey.Redo, self, activated=self.undo_stack.redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self.undo_stack.redo)

        # tree callbacks
        self.tree.set_state_provider(self._serialize_tree)
        self.tree.set_after_drop_callback(self._on_tree_changed_external)

        self.btn_add_group.clicked.connect(self.create_group_from_selection)
        self.btn_del_group.clicked.connect(self.delete_selected_group)

        self.tree.model().rowsMoved.connect(self._on_rows_moved)
        self.tree.itemChanged.connect(self._on_item_changed)

        # anchor capture (persistent)
        self.tree.itemSelectionChanged.connect(self._capture_anchor_from_selection)
        self.tree.currentItemChanged.connect(self._capture_anchor_from_current)

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
            it = self._find_layer_item(self._anchor_id)
            if it:
                return it
        if self._anchor_type == TYPE_GROUP and self._anchor_id:
            it = self._find_group_item(self._anchor_id)
            if it:
                return it

        it = self.tree.currentItem()
        if it:
            return it
        sel = self.tree.selectedItems()
        if sel:
            return sel[0]
        return None

    def _find_layer_item(self, layer_id):
        def walk(it):
            if it.data(0, ROLE_TYPE) == TYPE_LAYER and it.data(0, ROLE_ID) == layer_id:
                return it
            for i in range(it.childCount()):
                r = walk(it.child(i))
                if r:
                    return r
            return None

        for i in range(self.tree.topLevelItemCount()):
            r = walk(self.tree.topLevelItem(i))
            if r:
                return r
        return None

    def _find_group_item(self, group_id):
        def walk(it):
            if it.data(0, ROLE_TYPE) == TYPE_GROUP and it.data(0, ROLE_ID) == group_id:
                return it
            for i in range(it.childCount()):
                r = walk(it.child(i))
                if r:
                    return r
            return None

        for i in range(self.tree.topLevelItemCount()):
            r = walk(self.tree.topLevelItem(i))
            if r:
                return r
        return None

    # ==================================================================
    # tree helpers
    # ==================================================================
    def _index_in_parent(self, item: QTreeWidgetItem) -> int:
        p = item.parent()
        return p.indexOfChild(item) if p is not None else self.tree.indexOfTopLevelItem(item)

    def _iter_all_layer_ids(self):
        def walk(it):
            if it.data(0, ROLE_TYPE) == TYPE_LAYER:
                yield it.data(0, ROLE_ID)
            for i in range(it.childCount()):
                yield from walk(it.child(i))

        for i in range(self.tree.topLevelItemCount()):
            yield from walk(self.tree.topLevelItem(i))

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

    def _add_layer_item(self, parent: QTreeWidgetItem, lyr):
        it = QTreeWidgetItem([lyr.name()])
        it.setData(0, ROLE_TYPE, TYPE_LAYER)
        it.setData(0, ROLE_ID, lyr.id())
        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        it.setIcon(0, _icon_for_layer(lyr))
        if parent is None:
            self.tree.addTopLevelItem(it)
        else:
            parent.addChild(it)
        return it

    def _insert_layer_item(self, parent: QTreeWidgetItem, idx: int, lyr):
        it = QTreeWidgetItem([lyr.name()])
        it.setData(0, ROLE_TYPE, TYPE_LAYER)
        it.setData(0, ROLE_ID, lyr.id())
        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        it.setIcon(0, _icon_for_layer(lyr))
        if parent is None:
            self.tree.insertTopLevelItem(idx, it)
        else:
            parent.insertChild(idx, it)
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

    # ==================================================================
    # group operations (FIXES THE CRASH)
    # ==================================================================
    def create_group_from_selection(self):
        before = self._snapshot or self._serialize_tree()

        name, ok = QInputDialog.getText(self, "New group", "Group name:", text="New group")
        if not ok:
            return
        name = (name or "").strip() or "New group"

        selected = self.tree.selectedItems() or []
        selected_ids = {id(x) for x in selected}

        moving = [it for it in selected if (it.parent() is None or id(it.parent()) not in selected_ids)]

        grp = QTreeWidgetItem([name])
        grp.setData(0, ROLE_TYPE, TYPE_GROUP)
        grp.setData(0, ROLE_ID, _new_group_id())
        grp.setFlags(grp.flags() | Qt.ItemFlag.ItemIsEditable)
        grp.setIcon(0, _icon_group())

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

        grp.setExpanded(True)
        self.tree.setCurrentItem(grp)

        self.request_apply()
        after = self._serialize_tree()
        self._push_undo(before, after, "Create group")
        self._autosave()

    def delete_selected_group(self):
        before = self._snapshot or self._serialize_tree()
        cur = self.tree.currentItem()
        if cur is None or cur.data(0, ROLE_TYPE) != TYPE_GROUP:
            return

        parent = cur.parent()
        insert_at = self._index_in_parent(cur)

        # move children out of group (preserve order)
        children = []
        while cur.childCount():
            children.append(cur.takeChild(0))

        # remove group
        if parent is None:
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(cur))
        else:
            parent.takeChild(parent.indexOfChild(cur))

        # reinsert children where group was
        for ch in children:
            if parent is None:
                self.tree.insertTopLevelItem(insert_at, ch)
            else:
                parent.insertChild(insert_at, ch)
            insert_at += 1

        self.request_apply()
        after = self._serialize_tree()
        self._push_undo(before, after, "Delete group")
        self._autosave()

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
                return {
                    "type": TYPE_GROUP,
                    "id": item.data(0, ROLE_ID),
                    "name": item.text(0),
                    "expanded": True,
                    "children": [ser_item(item.child(i)) for i in range(item.childCount())],
                }
            return {"type": TYPE_LAYER, "id": item.data(0, ROLE_ID)}

        return json.dumps(
            {"children": [ser_item(self.tree.topLevelItem(i))
                          for i in range(self.tree.topLevelItemCount())]},
            ensure_ascii=False
        )

    # ==================================================================
    # loading
    # ==================================================================
    def load_from_project(self, raw_json: str):
        self._loading = True
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            if not raw_json:
                self._populate_all_layers_top_level()
                self._snapshot = self._serialize_tree()
                return

            obj = json.loads(raw_json)
            proj = QgsProject.instance()

            def add_child(parent_item, child_item):
                if parent_item is None:
                    self.tree.addTopLevelItem(child_item)
                else:
                    parent_item.addChild(child_item)

            def build(parent, node):
                if node["type"] == TYPE_GROUP:
                    it = QTreeWidgetItem([node.get("name", "Group")])
                    it.setData(0, ROLE_TYPE, TYPE_GROUP)
                    it.setData(0, ROLE_ID, node.get("id", _new_group_id()))
                    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
                    it.setIcon(0, _icon_group())
                    add_child(parent, it)
                    for ch in node.get("children", []):
                        build(it, ch)
                else:
                    lyr = proj.mapLayer(node["id"])
                    if lyr:
                        self._add_layer_item(parent, lyr)

            for top in obj.get("children", []):
                build(None, top)

            self._append_missing_layers()
            self._snapshot = self._serialize_tree()
        finally:
            self.tree.blockSignals(False)
            self._loading = False

    # ==================================================================
    # layer add/remove (ANCHOR-BASED)
    # ==================================================================
    def on_layers_added(self, layers):
        if self._loading or self._in_undo:
            return

        before = self._serialize_tree()
        existing = set(self._iter_all_layer_ids())
        new_layers = [l for l in layers if l.id() not in existing]
        if not new_layers:
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

    def on_layers_removed(self, layer_ids):
        if self._loading or self._in_undo:
            return

        before = self._serialize_tree()
        remove_set = set(layer_ids or [])

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
        finally:
            self.tree.blockSignals(False)
            self._loading = False

        self.request_apply()
        after = self._serialize_tree()
        self._push_undo(before, after, "Remove layers")
        self._autosave()

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
        root = QgsProject.instance().layerTreeRoot()
        layers = self._flatten_to_qgs_layers()
        # Keep custom order active once the panel has been used; empty list
        # still means "custom order on, nothing drawn from this panel".
        root.setHasCustomLayerOrder(True)
        root.setCustomLayerOrder(layers)

        try:
            self.iface.mapCanvas().refresh()
        except Exception:
            pass

    # ==================================================================
    # events (undo hooks)
    # ==================================================================
    def _on_rows_moved(self, *args, **kwargs):
        if self._in_undo or self._loading:
            return
        # InternalMove not always triggers our custom drop path; take snapshot-based undo.
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
        if self._in_undo or self._loading:
            return

        self.request_apply()
        self._push_undo(before_json, after_json, "Reorder layers")
        self._autosave()