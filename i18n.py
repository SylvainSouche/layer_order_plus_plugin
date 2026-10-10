"""User-visible strings, translated by QGIS's own catalog.

The plugin ships no translation files. Every label it shows that QGIS also
has is looked up with QGIS's context and English text, so the translator
QGIS installs at startup shows it in the user's language. Labels QGIS has
no equivalent for stay in English. Each (context, text) pair below exists
in QGIS 3.40 and 4.2 (tests/qgis/check_i18n.py checks the installed QGIS).
"""
from __future__ import annotations

from qgis.PyQt.QtCore import QCoreApplication

ADD_GROUP = ("QgsLayerTreeViewDefaultActions", "&Add Group")
RENAME_GROUP = ("QgsLayerTreeViewDefaultActions", "Re&name Group")
REMOVE_GROUP = ("QgsAppLayerTreeViewMenuProvider", "&Remove Group…")
MOVE_UP = ("QgsEffectStackPropertiesWidgetBase", "Move up")
MOVE_DOWN = ("QgsEffectStackPropertiesWidgetBase", "Move down")
MOVE_TO_TOP = ("QgsLayerTreeViewDefaultActions", "Move to &Top")
MOVE_TO_BOTTOM = ("QgsLayerTreeViewDefaultActions", "Move to &Bottom")
MOVE_ITEMS = ("QgsGraphicsViewMouseHandles", "Move Items")
UNDO = ("UndoWidget", "Undo")
REDO = ("UndoWidget", "Redo")
NEW_GROUP = ("QgsModelDesignerDialog", "New Group")
GROUP_NAME = ("QgsModelOutputReorderWidgetBase", "Group name")
CONTROL_RENDERING_ORDER = ("QgsCustomLayerOrderWidget", "Control rendering order")
LAYER_ORDER = ("QgisApp", "Layer Order")
DRAG_TO_REORDER = ("QgsDiagramPropertiesBase", "Drag and drop to reorder")

ENTRIES = (ADD_GROUP, RENAME_GROUP, REMOVE_GROUP, MOVE_UP, MOVE_DOWN, MOVE_TO_TOP,
           MOVE_TO_BOTTOM, MOVE_ITEMS, UNDO, REDO, NEW_GROUP, GROUP_NAME,
           CONTROL_RENDERING_ORDER, LAYER_ORDER, DRAG_TO_REORDER)


def tr(entry: tuple[str, str]) -> str:
    """`entry` in the user's language, without accelerator (&) or ellipsis."""
    text = QCoreApplication.translate(*entry)
    return text.replace("&", "").rstrip(".… ")
