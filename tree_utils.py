"""QTreeWidget helpers for the View (stateless, presentation only)."""
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QTreeWidget, QTreeWidgetItem

from .model import TYPE_GROUP, TYPE_LAYER

ROLE_TYPE = Qt.ItemDataRole.UserRole + 1      # TYPE_GROUP | TYPE_LAYER
ROLE_ID = Qt.ItemDataRole.UserRole + 2        # group id | layer id
ROLE_EXPANDED = Qt.ItemDataRole.UserRole + 3  # expanded state last rendered (groups)

__all__ = ["ROLE_TYPE", "ROLE_ID", "ROLE_EXPANDED", "TYPE_GROUP", "TYPE_LAYER",
           "find_group_item", "find_layer_item", "apply_name_filter"]


def _find_item(tree: QTreeWidget, item_type: str, item_id: str):
    if not item_id:
        return None

    def walk(it: QTreeWidgetItem):
        if it.data(0, ROLE_TYPE) == item_type and it.data(0, ROLE_ID) == item_id:
            return it
        for i in range(it.childCount()):
            found = walk(it.child(i))
            if found is not None:
                return found
        return None

    for i in range(tree.topLevelItemCount()):
        found = walk(tree.topLevelItem(i))
        if found is not None:
            return found
    return None


def find_group_item(tree: QTreeWidget, group_id: str):
    """The group item with this id, or None."""
    return _find_item(tree, TYPE_GROUP, group_id)


def find_layer_item(tree: QTreeWidget, layer_id: str):
    """The layer item with this id, or None."""
    return _find_item(tree, TYPE_LAYER, layer_id)


def apply_name_filter(tree: QTreeWidget, needle: str) -> None:
    """Hide items not matching `needle` (case-insensitive substring).

    An item is shown if it matches, an ancestor matches (the whole matching
    subtree is shown) or a descendant matches (groups leading to a match are
    shown and expanded). An empty needle shows everything. Items are only
    hidden, never removed.
    """
    needle = (needle or "").strip().lower()

    def walk(it: QTreeWidgetItem, ancestor_matches: bool) -> bool:
        self_match = not needle or needle in (it.text(0) or "").lower()
        show_subtree = ancestor_matches or self_match
        descendant_match = False
        for i in range(it.childCount()):
            descendant_match |= walk(it.child(i), show_subtree)
        shown = show_subtree or descendant_match
        it.setHidden(not shown)
        if needle and descendant_match and not self_match:
            it.setExpanded(True)
        return shown

    for i in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(i), False)
