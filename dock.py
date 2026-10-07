# dock.py
import json
import os
import uuid

from qgis.PyQt.QtCore import Qt, QTimer, QSize
from qgis.PyQt.QtGui import QDropEvent, QKeySequence, QUndoCommand, QUndoStack, QIcon
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QPushButton,
    QShortcut,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QInputDialog,
    QMenu,
)

from qgis.core import QgsProject, QgsApplication, QgsIconUtils, QgsMapLayer, QgsVectorLayer


ROLE_TYPE = Qt.ItemDataRole.UserRole + 1   # "group" | "layer"
ROLE_ID   = Qt.ItemDataRole.UserRole + 2   # group_id | layer_id

TYPE_GROUP = "group"
TYPE_LAYER = "layer"



def _new_group_id():
    return "grp_" + uuid.uuid4().hex[:10]


def _collect_group_names(tree: QTreeWidget) -> set:
    names = set()

    def walk(item: QTreeWidgetItem):
        if item.data(0, ROLE_TYPE) == TYPE_GROUP:
            names.add(item.text(0))
        for i in range(item.childCount()):
            walk(item.child(i))

    for i in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(i))
    return names


def _unique_group_name(tree: QTreeWidget, base: str = "New group") -> str:
    """Return base, or 'base 2', 'base 3', … if base is already used."""
    existing = _collect_group_names(tree)
    if base not in existing:
        return base
    n = 2
    while f"{base} {n}" in existing:
        n += 1
    return f"{base} {n}"


def _expand_item_and_ancestors(item: QTreeWidgetItem):
    """Expand item and every parent so the new group is visible."""
    cur = item
    while cur is not None:
        cur.setExpanded(True)
        cur = cur.parent()


_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_ICONS_DIR = os.path.join(_PLUGIN_DIR, "icons")


def _bundled_icon(filename: str) -> QIcon:
    """Load an icon shipped with the plugin (always works offline / no theme)."""
    path = os.path.join(_ICONS_DIR, filename)
    if os.path.isfile(path):
        icon = QIcon(path)
        if not icon.isNull():
            return icon
    return QIcon()


def _theme_icon(*names) -> QIcon:
    for name in names:
        for candidate in (name, name.lstrip("/"), "/" + name.lstrip("/")):
            try:
                icon = QgsApplication.getThemeIcon(candidate)
            except Exception:
                icon = QIcon()
            if icon is not None and not icon.isNull():
                return icon
    return QIcon()


def _icon_group() -> QIcon:
    """Folder icon for order-groups."""
    icon = _bundled_icon("folder.svg")
    if not icon.isNull():
        return icon
    icon = _theme_icon("/mIconFolder.svg", "/mIconFolderOpen.svg")
    if not icon.isNull():
        return icon
    try:
        return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
    except Exception:
        try:
            return QApplication.style().standardIcon(QStyle.SP_DirIcon)
        except Exception:
            return QIcon()


def _icon_add_group() -> QIcon:
    icon = _bundled_icon("add_group.svg")
    if not icon.isNull():
        return icon
    icon = _theme_icon("/mActionAddGroup.svg", "/mIconAddGroup.svg")
    if not icon.isNull():
        return icon
    return _icon_group()


def _icon_remove_group() -> QIcon:
    icon = _bundled_icon("remove_group.svg")
    if not icon.isNull():
        return icon
    icon = _theme_icon(
        "/mActionRemoveSelectedLayer.svg",
        "/mActionDeleteSelected.svg",
        "/mIconRemove.svg",
    )
    if not icon.isNull():
        return icon
    try:
        return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
    except Exception:
        return _icon_group()


def _icon_rename_group() -> QIcon:
    icon = _bundled_icon("rename_group.svg")
    if not icon.isNull():
        return icon
    icon = _theme_icon("/mActionRenameLayer.svg")
    if not icon.isNull():
        return icon
    try:
        return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView)
    except Exception:
        return _icon_group()


def _icon_for_layer(layer) -> QIcon:
    """Prefer QGIS type icon, then bundled SVG by geometry/type, then generic."""
    if layer is not None:
        try:
            icon = QgsIconUtils.iconForLayer(layer)
            if icon is not None and not icon.isNull():
                return icon
        except Exception:
            pass
        try:
            if isinstance(layer, QgsVectorLayer):
                try:
                    icon = QgsIconUtils.iconForWkbType(layer.wkbType())
                    if icon is not None and not icon.isNull():
                        return icon
                except Exception:
                    pass
                try:
                    gname = str(layer.geometryType())
                    if "Point" in gname:
                        icon = _bundled_icon("point.svg")
                        return icon if not icon.isNull() else _theme_icon("/mIconPointLayer.svg")
                    if "Line" in gname:
                        icon = _bundled_icon("line.svg")
                        return icon if not icon.isNull() else _theme_icon("/mIconLineLayer.svg")
                    if "Polygon" in gname:
                        icon = _bundled_icon("polygon.svg")
                        return icon if not icon.isNull() else _theme_icon("/mIconPolygonLayer.svg")
                except Exception:
                    pass
            name = str(layer.type())
            if "Raster" in name:
                icon = _bundled_icon("raster.svg")
                if not icon.isNull():
                    return icon
                return _theme_icon("/mIconRaster.svg")
        except Exception:
            pass
    icon = _bundled_icon("layer.svg")
    if not icon.isNull():
        return icon
    icon = _theme_icon("/mIconLayer.png")
    if not icon.isNull():
        return icon
    try:
        return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
    except Exception:
        return QIcon()


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
      - OnItem on LAYER  -> create a new group (default name), put target + dropped items in it
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

            self._just_custom_dropped = True

            # --- OnItem + LAYER: create new group with target + dropped items ---
            if pos == QAbstractItemView.DropIndicatorPosition.OnItem and tt == TYPE_LAYER:
                # Ignore if target is among movers (already checked) or would cycle
                for it in moving:
                    if self._is_ancestor(it, target):
                        e.ignore()
                        self._just_custom_dropped = False
                        return

                moving.sort(key=self._path_key)

                # Where the new group will sit (target's current slot)
                grp_parent = target.parent()
                grp_index = self._index_in_parent(target)

                # Adjust index if we remove movers that sit before the target in same parent
                for it in moving:
                    if it.parent() == grp_parent and self._index_in_parent(it) < grp_index:
                        grp_index -= 1

                # Take movers out of the tree first
                taken = []
                for it in reversed(moving):
                    taken.append(self._take_item(it))
                taken.reverse()

                # Take target out (still a valid QTreeWidgetItem)
                target_taken = self._take_item(target)

                # Build group with a unique default name
                gname = _unique_group_name(self, "New group")
                grp = QTreeWidgetItem([gname])
                grp.setData(0, ROLE_TYPE, TYPE_GROUP)
                grp.setData(0, ROLE_ID, _new_group_id())
                try:
                    grp.setFlags(grp.flags() & ~Qt.ItemFlag.ItemIsEditable)
                except Exception:
                    grp.setFlags(grp.flags() & ~Qt.ItemFlag.ItemIsEditable)
                grp.setIcon(0, _icon_group())

                self._insert_item(grp_parent, grp_index, grp)

                # Children: target first, then dropped items (stable order)
                grp.addChild(target_taken)
                for it in taken:
                    grp.addChild(it)

                self.clearSelection()
                grp.setSelected(True)
                self.setCurrentItem(grp)

                # Expand now and again after Qt finishes the drop (avoids collapse)
                _expand_item_and_ancestors(grp)

                def _keep_expanded(g=grp):
                    setattr(self, "_just_custom_dropped", False)
                    if g is not None:
                        _expand_item_and_ancestors(g)
                        self.scrollToItem(g)

                e.accept()
                after = self._state_provider()
                if self._after_drop_cb:
                    self._after_drop_cb(before, after)
                QTimer.singleShot(0, _keep_expanded)
                return

            # --- OnItem + GROUP / Above / Below: classic move ---
            if pos == QAbstractItemView.DropIndicatorPosition.OnItem:
                # group target: move into top of group
                dest_parent = target
                dest_index = 0
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

        def _tb_btn(icon, tip):
            b = QPushButton()
            b.setIcon(icon)
            b.setToolTip(tip)
            b.setFlat(True)
            b.setFixedSize(28, 28)
            b.setIconSize(QSize(16, 16))
            return b

        self.btn_add_group = _tb_btn(_icon_add_group(), "Create group")
        self.btn_rename_group = _tb_btn(_icon_rename_group(), "Rename group")
        self.btn_del_group = _tb_btn(_icon_remove_group(), "Delete group")

        head.addWidget(self.btn_add_group)
        head.addWidget(self.btn_rename_group)
        head.addWidget(self.btn_del_group)
        head.addStretch(1)

        self.tree = BetterLayerTree()
        self.tree.setHeaderHidden(True)
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

        # Local shortcuts when the dock has focus (plugin also hooks Edit menu / app shortcuts)
        for seq, slot in (
            (QKeySequence.StandardKey.Undo, self.undo_stack.undo),
            (QKeySequence.StandardKey.Redo, self.undo_stack.redo),
            (QKeySequence("Ctrl+Shift+Z"), self.undo_stack.redo),
        ):
            sc = QShortcut(seq, self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)

        # tree callbacks
        self.tree.set_state_provider(self._serialize_tree)
        self.tree.set_after_drop_callback(self._on_tree_changed_external)

        self.btn_add_group.clicked.connect(self.create_group_from_selection)
        self.btn_rename_group.clicked.connect(self.rename_selected_group)
        self.btn_del_group.clicked.connect(self.delete_selected_groups)

        self.tree.model().rowsMoved.connect(self._on_rows_moved)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)

        # anchor capture + toolbar enable state
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.currentItemChanged.connect(self._capture_anchor_from_current)

        self._update_group_actions_enabled()

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

        default_name = _unique_group_name(self.tree, "New group")
        name, ok = QInputDialog.getText(self, "New group", "Group name:", text=default_name)
        if not ok:
            return
        name = (name or "").strip() or default_name
        # If user kept a name that collides, make it unique
        if name in _collect_group_names(self.tree):
            name = _unique_group_name(self.tree, name)

        selected = self.tree.selectedItems() or []
        selected_ids = {id(x) for x in selected}

        moving = [it for it in selected if (it.parent() is None or id(it.parent()) not in selected_ids)]

        grp = QTreeWidgetItem([name])
        grp.setData(0, ROLE_TYPE, TYPE_GROUP)
        grp.setData(0, ROLE_ID, _new_group_id())
        grp.setFlags(grp.flags() & ~Qt.ItemFlag.ItemIsEditable)
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

        _expand_item_and_ancestors(grp)
        self.tree.setCurrentItem(grp)
        self.tree.scrollToItem(grp)

        self.request_apply()
        after = self._serialize_tree()
        self._push_undo(before, after, "Create group")
        self._autosave()

    def _selected_groups(self):
        return [it for it in self.tree.selectedItems() if it.data(0, ROLE_TYPE) == TYPE_GROUP]

    def _item_depth(self, item):
        d = 0
        cur = item
        while cur is not None:
            d += 1
            cur = cur.parent()
        return d

    def _unwrap_and_remove_group(self, cur):
        """Move children out in place, then remove the group item."""
        parent = cur.parent()
        insert_at = self._index_in_parent(cur)
        children = []
        while cur.childCount():
            children.append(cur.takeChild(0))
        if parent is None:
            idx = self.tree.indexOfTopLevelItem(cur)
            if idx < 0:
                return
            self.tree.takeTopLevelItem(idx)
        else:
            parent.takeChild(parent.indexOfChild(cur))
        for ch in children:
            if parent is None:
                self.tree.insertTopLevelItem(insert_at, ch)
            else:
                parent.insertChild(insert_at, ch)
            insert_at += 1

    def delete_selected_groups(self):
        groups = self._selected_groups()
        if not groups:
            return
        before = self._snapshot or self._serialize_tree()
        # deepest first so nested selected groups are removed before parents
        groups.sort(key=self._item_depth, reverse=True)
        removed = 0
        for cur in groups:
            # still in tree?
            if cur.treeWidget() is not self.tree:
                continue
            self._unwrap_and_remove_group(cur)
            removed += 1
        if removed == 0:
            return
        self.request_apply()
        after = self._serialize_tree()
        self._push_undo(before, after, "Delete group" if removed == 1 else f"Delete {removed} groups")
        self._autosave()
        self._update_group_actions_enabled()

    def rename_selected_group(self):
        groups = self._selected_groups()
        if len(groups) != 1:
            return
        grp = groups[0]
        before = self._snapshot or self._serialize_tree()
        current = grp.text(0)
        name, ok = QInputDialog.getText(self, "Rename group", "Group name:", text=current)
        if not ok:
            return
        name = (name or "").strip()
        if not name or name == current:
            return
        # allow same name as self; uniquify only against others
        others = _collect_group_names(self.tree) - {current}
        if name in others:
            name = _unique_group_name(self.tree, name)
        grp.setText(0, name)
        self.request_apply()
        after = self._serialize_tree()
        self._push_undo(before, after, "Rename group")
        self._autosave()

    def _on_item_double_clicked(self, item, column):
        if item is None:
            return
        if item.data(0, ROLE_TYPE) == TYPE_GROUP:
            item.setExpanded(not item.isExpanded())
        # layers: do nothing (no rename, no toggle)

    def _on_tree_context_menu(self, pos):
        menu = QMenu(self)
        act_create = menu.addAction(_icon_add_group(), "Create group")
        act_rename = menu.addAction(_icon_rename_group(), "Rename group")
        act_delete = menu.addAction(_icon_remove_group(), "Delete group")

        groups = self._selected_groups()
        # if right-click on an item not in selection, select it first
        under = self.tree.itemAt(pos)
        if under is not None and under not in self.tree.selectedItems():
            self.tree.clearSelection()
            under.setSelected(True)
            self.tree.setCurrentItem(under)
            groups = self._selected_groups()

        act_rename.setEnabled(len(groups) == 1)
        act_delete.setEnabled(len(groups) >= 1)

        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen == act_create:
            self.create_group_from_selection()
        elif chosen == act_rename:
            self.rename_selected_group()
        elif chosen == act_delete:
            self.delete_selected_groups()

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
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
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

    # Inline edit disabled; rename goes through rename_selected_group() dialog.

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