"""BetterLayerTree — QTreeWidget subclass with semantic drop-intent emission.

In the MVC architecture (1.2.1+), this widget is a dumb View component:
it does NOT mutate the tree on drop. Instead, it computes the semantic
intent (which items are moving, what's the target, what's the position)
and emits `drop_intent(moving_ids, target_id, position)`.

The ViewController receives the signal and translates it to Model mutations
(create group, move items). The Model emits events, the ViewController
updates the View via its semantic methods (add_group_item, move_item, etc.).

This keeps the tree widget free of business logic — it only knows about
selection, drop indicators, and cycle prevention (a visual concern).
"""
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QDropEvent
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QTreeWidget,
    QTreeWidgetItem,
)

from .logger import _vlog, _vlog_method
from .tree_utils import (
    ROLE_TYPE,
    ROLE_ID,
    TYPE_GROUP,
    TYPE_LAYER,
)


# Semantic drop positions (emitted to ViewController)
DROP_ON = "on"
DROP_ABOVE = "above"
DROP_BELOW = "below"
DROP_END = "end"      # empty area below the last row → bottom of top level


class BetterLayerTree(QTreeWidget):
    """QTreeWidget that emits semantic drop intents instead of mutating on drop.

    Signals:
        drop_intent(moving_ids: list[str], target_id: str, position: str)
            Emitted when the user drops items. `position` is one of
            DROP_ON / DROP_ABOVE / DROP_BELOW, or DROP_END (empty area,
            target_id ""). `moving_ids` is in display order. The
            ViewController translates this to Model mutations.
    """

    drop_intent = pyqtSignal(list, str, str)

    def __init__(self, *a, **k):
        super().__init__(*a, **k)

    # ------------------------------------------------------------------
    # event helpers
    # ------------------------------------------------------------------
    def _event_pos_point(self, e: QDropEvent):
        _vlog_method("_event_pos_point", "[TW]")
        return e.position().toPoint() if hasattr(e, "position") else e.pos()

    def _is_ancestor(self, anc: QTreeWidgetItem, node: QTreeWidgetItem) -> bool:
        """Return True if `anc` is `node` or an ancestor of `node` (visual cycle check)."""
        anc_id = id(anc)
        cur = node
        while cur is not None:
            if id(cur) == anc_id:
                return True
            cur = cur.parent()
        return False

    # ------------------------------------------------------------------
    # drop handling — emit semantic intent, do NOT mutate
    # ------------------------------------------------------------------
    def _moving_items(self):
        """Selected, visible items whose ancestors are not also selected, in display order."""
        selected = [it for it in self.selectedItems() if not it.isHidden()]
        selected_ids = {id(x) for x in selected}

        def has_selected_ancestor(it):
            p = it.parent()
            while p is not None:
                if id(p) in selected_ids:
                    return True
                p = p.parent()
            return False

        moving = [it for it in selected if not has_selected_ancestor(it)]
        # selectedItems() is in click order; the Model needs display order
        moving.sort(key=self._visual_key)
        return moving

    def _visual_key(self, item):
        """Tuple of row indices from the root — sorts items in display order."""
        key = []
        cur = item
        while cur is not None:
            p = cur.parent()
            key.append(p.indexOfChild(cur) if p is not None else self.indexOfTopLevelItem(cur))
            cur = p
        return tuple(reversed(key))

    def dropEvent(self, e: QDropEvent):
        # Whatever happens, never let QTreeWidget move items itself: the View
        # must only change in response to Model events. Calling
        # super().dropEvent() here would rearrange QTreeWidgetItems behind
        # the Model's back (this is what happened on drops in empty space).
        e.setDropAction(Qt.DropAction.IgnoreAction)
        e.accept()

        if e.source() is not self:
            return

        moving = self._moving_items()
        if not moving:
            return

        pos = self.dropIndicatorPosition()
        target = self.itemAt(self._event_pos_point(e))

        if pos == QAbstractItemView.DropIndicatorPosition.OnViewport or target is None:
            # Empty area below the last row → move to the bottom of the top level
            position = DROP_END
            target_id = ""
        else:
            if pos == QAbstractItemView.DropIndicatorPosition.OnItem:
                position = DROP_ON
            elif pos == QAbstractItemView.DropIndicatorPosition.AboveItem:
                position = DROP_ABOVE
            else:
                position = DROP_BELOW
            # Dropping onto (or next to) one of the dragged items is a no-op
            if any(it is target for it in moving):
                return
            # Cycle prevention: can't move an item into its own subtree
            for it in moving:
                if self._is_ancestor(it, target):
                    return
            target_id = target.data(0, ROLE_ID)

        moving_item_ids = [it.data(0, ROLE_ID) for it in moving]
        _vlog(f"[TW] drop_intent: moving={moving_item_ids} target={target_id} pos={position}")
        self.drop_intent.emit(moving_item_ids, target_id, position)
