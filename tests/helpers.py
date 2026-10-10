"""Test helpers built on the Model's public API."""
from advanced_layer_order.model import GroupNode, LayerOrderModel


def move_item(model: LayerOrderModel, item_id: str, new_parent_id, new_index: int) -> bool:
    """Index-based form of Model.move_items(), handy in tests.

    `new_index` is a position in the target parent *before* the item is
    taken out (i.e. "insert before the node currently at new_index").
    """
    parent = model.find_item(new_parent_id) if new_parent_id else None
    siblings = parent.children if isinstance(parent, GroupNode) else model.get_root()
    before = siblings[new_index].id if 0 <= new_index < len(siblings) else None
    if before == item_id:
        return False
    return model.move_items([item_id], new_parent_id, before)
