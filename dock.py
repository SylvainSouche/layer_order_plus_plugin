"""Layer Order Plus — dock.py (deprecated compat shim, 1.2.1+).

The monolithic BetterLayerOrderDock class has been split into:
  view.py             — LayerOrderView (QDockWidget, pure UI)
  view_controller.py  — ViewController (Model↔View sync + undo)
  controller.py       — LayerOrderController (QGIS↔Model sync)
  plugin.py           — BetterLayerOrderPlugin (orchestrator)

This file re-exports LayerOrderView as BetterLayerOrderDock for backwards
compatibility with any external code that imported it. New code should
import from view.py directly.
"""
from .view import LayerOrderView as BetterLayerOrderDock  # noqa: F401

# Backwards-compat aliases for the underscore-prefixed helper names that
# used to live in dock.py. They now live in tree_utils.py and icons.py.
from .tree_utils import (
    new_group_id as _new_group_id,
    collect_group_names as _collect_group_names,
    unique_group_name as _unique_group_name,
    expand_item_and_ancestors as _expand_item_and_ancestors,
    find_group_item as _find_group_item,
    find_layer_item as _find_layer_item,
    iter_all_layer_ids as _iter_all_layer_ids,
)
from .icons import (
    icon_group as _icon_group,
    icon_add_group as _icon_add_group,
    icon_remove_group as _icon_remove_group,
    icon_rename_group as _icon_rename_group,
    icon_for_layer as _icon_for_layer,
)
from .model import TREE_JSON_SCHEMA_VERSION  # noqa: F401
from qgis.core import Qgis, QgsMessageLog

LOG_TAG = "LayerOrderPlus"


def _log(msg, level=Qgis.Info):
    """Centralised log helper (kept for backwards compat)."""
    try:
        QgsMessageLog.logMessage(str(msg), LOG_TAG, level)
    except Exception:
        try:
            print(f"[{LOG_TAG}] {msg}")
        except Exception:
            pass
