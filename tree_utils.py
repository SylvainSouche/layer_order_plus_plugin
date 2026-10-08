"""QTreeWidget helpers for the View (stateless, presentation only)."""
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QTreeWidget, QTreeWidgetItem

from .model import TYPE_GROUP, TYPE_LAYER

ROLE_TYPE = Qt.ItemDataRole.UserRole + 1      # TYPE_GROUP | TYPE_LAYER
ROLE_ID = Qt.ItemDataRole.UserRole + 2        # group id | layer id

__all__ = [
    "ROLE_ID",
    "ROLE_TYPE",
    "TYPE_GROUP",
    "TYPE_LAYER",
    "find_group_item",
    "find_layer_item",
]


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
