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


class BetterLayerTree(QTreeWidget):
    """QTreeWidget that emits semantic drop intents instead of mutating on drop.

    Signals:
        drop_intent(moving_ids: list[str], target_id: str, position: str)
            Emitted when the user drops items. `position` is one of
            DROP_ON / DROP_ABOVE / DROP_BELOW. The ViewController translates
            this to Model mutations.
    """

    drop_intent = pyqtSignal(list, str, str)

    def __init__(self, *a, **k):
        super().__init__(*a, **k)

    # ------------------------------------------------------------------
    # event helpers
    # ------------------------------------------------------------------
    def _event_pos_point(self, e: QDropEvent):
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
    def dropEvent(self, e: QDropEvent):
        p = self._event_pos_point(e)
        pos = self.dropIndicatorPosition()

        if pos not in (QAbstractItemView.DropIndicatorPosition.OnItem,
                       QAbstractItemView.DropIndicatorPosition.AboveItem,
                       QAbstractItemView.DropIndicatorPosition.BelowItem):
            super().dropEvent(e)
            return

        target = self.itemAt(p)
        if target is None:
            super().dropEvent(e)
            return

        selected = self.selectedItems()
        selected_ids = {id(x) for x in selected}

        # Filter: don't move a child if its parent is also moving
        moving = [it for it in selected
                  if (it.parent() is None or id(it.parent()) not in selected_ids)]
        if not moving:
            e.ignore()
            return

        moving_ids = {id(x) for x in moving}

        # Can't drop ON a moving item
        if pos == QAbstractItemView.DropIndicatorPosition.OnItem and id(target) in moving_ids:
            e.ignore()
            return

        # Cycle prevention: can't move an item onto its own descendant
        for it in moving:
            if self._is_ancestor(it, target):
                e.ignore()
                return

        # Map Qt drop position to semantic string
        if pos == QAbstractItemView.DropIndicatorPosition.OnItem:
            position = DROP_ON
        elif pos == QAbstractItemView.DropIndicatorPosition.AboveItem:
            position = DROP_ABOVE
        else:
            position = DROP_BELOW

        # Emit the semantic intent — ViewController will translate to Model mutations
        moving_item_ids = [it.data(0, ROLE_ID) for it in moving]
        target_id = target.data(0, ROLE_ID)
        self.drop_intent.emit(moving_item_ids, target_id, position)

        # Accept the event so Qt doesn't show "failed drop" feedback.
        # The View will be updated via Model events on the next event loop cycle.
        e.accept()
