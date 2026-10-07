"""BetterLayerTree — QTreeWidget subclass with custom drag-drop rules.

Drop rules:
  - OnItem on GROUP  -> move items to TOP of that group
  - OnItem on LAYER  -> create a new group (default name), put target + dropped items in it
  - AboveItem        -> move items ABOVE target (same parent as target)
  - BelowItem        -> move items BELOW target (same parent as target)

Safeguards:
  - ignore OnItem drop onto a moving item
  - ignore moving an item/group onto its own descendant
"""
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QDropEvent
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QTreeWidget,
    QTreeWidgetItem,
)

from .tree_utils import (
    ROLE_TYPE,
    ROLE_ID,
    TYPE_GROUP,
    TYPE_LAYER,
    new_group_id,
    unique_group_name,
    expand_item_and_ancestors,
    find_group_item,
    index_in_parent as _index_in_parent_helper,
)
from .icons import icon_group


class BetterLayerTree(QTreeWidget):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._after_drop_cb = None         # (before_json, after_json) -> None
        self._state_provider = None        # () -> json_str
        self._just_custom_dropped = False

    def set_after_drop_callback(self, cb):
        self._after_drop_cb = cb

    def set_state_provider(self, cb):
        self._state_provider = cb

    # ------------------------------------------------------------------
    # event helpers
    # ------------------------------------------------------------------
    def _event_pos_point(self, e: QDropEvent):
        return e.position().toPoint() if hasattr(e, "position") else e.pos()

    def _index_in_parent(self, item: QTreeWidgetItem) -> int:
        return _index_in_parent_helper(self, item)

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

    # ------------------------------------------------------------------
    # drop handling
    # ------------------------------------------------------------------
    def dropEvent(self, e: QDropEvent):
        p = self._event_pos_point(e)
        pos = self.dropIndicatorPosition()

        if pos in (QAbstractItemView.DropIndicatorPosition.OnItem,
                   QAbstractItemView.DropIndicatorPosition.AboveItem,
                   QAbstractItemView.DropIndicatorPosition.BelowItem):
            target = self.itemAt(p)
            if target is None or not self._state_provider:
                super().dropEvent(e)
                return

            before = self._state_provider()

            selected = self.selectedItems()
            selected_ids = {id(x) for x in selected}

            moving = [it for it in selected
                      if (it.parent() is None or id(it.parent()) not in selected_ids)]
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
                gname = unique_group_name(self, "New group")
                grp = QTreeWidgetItem([gname])
                grp.setData(0, ROLE_TYPE, TYPE_GROUP)
                grp.setData(0, ROLE_ID, new_group_id())
                try:
                    grp.setFlags((grp.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
                                 & ~Qt.ItemFlag.ItemIsEditable)
                except Exception:
                    grp.setFlags((grp.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                                 & ~Qt.ItemFlag.ItemIsEditable)
                grp.setIcon(0, icon_group())
                grp.setCheckState(0, Qt.CheckState.Checked)  # recomputed once children are added

                self._insert_item(grp_parent, grp_index, grp)

                # Children: target first, then dropped items (stable order)
                grp.addChild(target_taken)
                for it in taken:
                    grp.addChild(it)

                self.clearSelection()
                grp.setSelected(True)
                self.setCurrentItem(grp)

                # Expand now and again after Qt finishes the drop (avoids collapse).
                # Use group id — the QTreeWidgetItem pointer can be invalidated if
                # another handler rebuilds/touches the tree before the timer fires.
                group_id = grp.data(0, ROLE_ID)
                expand_item_and_ancestors(grp)

                def _keep_expanded(gid=group_id, tree=self):
                    setattr(tree, "_just_custom_dropped", False)
                    try:
                        item = find_group_item(tree, gid)
                        if item is not None:
                            expand_item_and_ancestors(item)
                            tree.scrollToItem(item)
                            tree.clearSelection()
                            item.setSelected(True)
                            tree.setCurrentItem(item)
                    except RuntimeError:
                        pass

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
