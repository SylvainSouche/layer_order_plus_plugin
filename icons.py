"""Icons for Layer Order Plus.

Toolbar and group icons are SVGs shipped in ``icons/``; layer icons come
from QGIS (``QgsIconUtils.iconForLayer``), with a bundled generic icon for
layers QGIS no longer knows.
"""
import os

from qgis.core import QgsIconUtils, QgsProject
from qgis.PyQt.QtGui import QIcon

_ICONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")


def bundled_icon(filename: str) -> QIcon:
    """An icon shipped with the plugin (empty QIcon if the file is missing)."""
    path = os.path.join(_ICONS_DIR, filename)
    return QIcon(path) if os.path.isfile(path) else QIcon()


def icon_group() -> QIcon:
    return bundled_icon("folder.svg")


def icon_add_group() -> QIcon:
    return bundled_icon("add_group.svg")


def icon_remove_group() -> QIcon:
    return bundled_icon("remove_group.svg")


def icon_rename_group() -> QIcon:
    return bundled_icon("rename_group.svg")


def icon_move_up() -> QIcon:
    return bundled_icon("move_up.svg")


def icon_move_down() -> QIcon:
    return bundled_icon("move_down.svg")


def icon_for_layer(layer) -> QIcon:
    """QGIS's own icon for the layer type/geometry; generic icon if None."""
    if layer is not None:
        icon = QgsIconUtils.iconForLayer(layer)
        if not icon.isNull():
            return icon
    return bundled_icon("layer.svg")


def icon_for_layer_id(layer_id: str) -> QIcon:
    """Icon for a project layer id."""
    return icon_for_layer(QgsProject.instance().mapLayer(layer_id))
