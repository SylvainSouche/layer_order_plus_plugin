"""Icon helpers for Layer Order Plus.

Bundled SVG icons take priority (always work offline), then QGIS theme icons,
then Qt standard pixmaps as final fallback. This keeps the panel usable even
when QGIS's theme is missing or incomplete.
"""
import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QApplication, QStyle

from qgis.core import QgsApplication, QgsIconUtils, QgsProject, QgsVectorLayer


_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_ICONS_DIR = os.path.join(_PLUGIN_DIR, "icons")


def bundled_icon(filename: str) -> QIcon:
    """Load an icon shipped with the plugin (always works offline / no theme)."""
    path = os.path.join(_ICONS_DIR, filename)
    if os.path.isfile(path):
        icon = QIcon(path)
        if not icon.isNull():
            return icon
    return QIcon()


def theme_icon(*names) -> QIcon:
    """Try several QGIS theme paths; return the first non-null icon."""
    for name in names:
        for candidate in (name, name.lstrip("/"), "/" + name.lstrip("/")):
            try:
                icon = QgsApplication.getThemeIcon(candidate)
            except Exception:
                icon = QIcon()
            if icon is not None and not icon.isNull():
                return icon
    return QIcon()


def _qt_standard(pixmap) -> QIcon:
    """Qt standard pixmap icon, tolerant of scoped/unscoped enum access."""
    try:
        return QApplication.style().standardIcon(pixmap)
    except Exception:
        try:
            return QApplication.style().standardIcon(
                QStyle.StandardPixmap(pixmap)
            )
        except Exception:
            return QIcon()


def icon_group() -> QIcon:
    """Folder icon for order-groups."""
    icon = bundled_icon("folder.svg")
    if not icon.isNull():
        return icon
    icon = theme_icon("/mIconFolder.svg", "/mIconFolderOpen.svg")
    if not icon.isNull():
        return icon
    try:
        return _qt_standard(QStyle.StandardPixmap.SP_DirIcon)
    except Exception:
        return QIcon()


def icon_add_group() -> QIcon:
    icon = bundled_icon("add_group.svg")
    if not icon.isNull():
        return icon
    icon = theme_icon("/mActionAddGroup.svg", "/mIconAddGroup.svg")
    if not icon.isNull():
        return icon
    return icon_group()


def icon_remove_group() -> QIcon:
    icon = bundled_icon("remove_group.svg")
    if not icon.isNull():
        return icon
    icon = theme_icon(
        "/mActionRemoveSelectedLayer.svg",
        "/mActionDeleteSelected.svg",
        "/mIconRemove.svg",
    )
    if not icon.isNull():
        return icon
    try:
        return _qt_standard(QStyle.StandardPixmap.SP_TrashIcon)
    except Exception:
        return icon_group()


def icon_rename_group() -> QIcon:
    icon = bundled_icon("rename_group.svg")
    if not icon.isNull():
        return icon
    icon = theme_icon("/mActionRenameLayer.svg")
    if not icon.isNull():
        return icon
    try:
        return _qt_standard(QStyle.StandardPixmap.SP_FileDialogDetailedView)
    except Exception:
        return icon_group()


def icon_for_layer(layer) -> QIcon:
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
                        icon = bundled_icon("point.svg")
                        return icon if not icon.isNull() else theme_icon("/mIconPointLayer.svg")
                    if "Line" in gname:
                        icon = bundled_icon("line.svg")
                        return icon if not icon.isNull() else theme_icon("/mIconLineLayer.svg")
                    if "Polygon" in gname:
                        icon = bundled_icon("polygon.svg")
                        return icon if not icon.isNull() else theme_icon("/mIconPolygonLayer.svg")
                except Exception:
                    pass
            name = str(layer.type())
            if "Raster" in name:
                icon = bundled_icon("raster.svg")
                if not icon.isNull():
                    return icon
                return theme_icon("/mIconRaster.svg")
        except Exception:
            pass
    icon = bundled_icon("layer.svg")
    if not icon.isNull():
        return icon
    icon = theme_icon("/mIconLayer.png")
    if not icon.isNull():
        return icon
    try:
        return _qt_standard(QStyle.StandardPixmap.SP_FileIcon)
    except Exception:
        return QIcon()


def icon_for_layer_id(layer_id: str) -> QIcon:
    """Icon for a project layer id (generic layer icon if the layer is gone)."""
    return icon_for_layer(QgsProject.instance().mapLayer(layer_id))
