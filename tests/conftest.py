"""Test configuration for Layer Order Plus unit tests.

These tests run WITHOUT QGIS installed. We provide stub `qgis.*` modules so
that `from qgis.PyQt.QtCore import Qt` and similar imports resolve to PyQt6
directly, and `qgis.core` symbols (QgsProject, QgsMessageLog, Qgis, etc.)
resolve to lightweight mocks.

The stubs are installed once at collection time via sys.modules.
"""
import os
import sys
import types

import pytest


# ----------------------------------------------------------------------
# Build a fake `qgis` package that aliases qgis.PyQt -> PyQt6 and provides
# minimal qgis.core / qgis.gui stubs. This lets the plugin modules import
# without a real QGIS installation.
# ----------------------------------------------------------------------
def _install_qgis_stubs():
    if "qgis" in sys.modules:
        return  # already installed (e.g. running inside QGIS)

    # Top-level qgis package
    qgis_pkg = types.ModuleType("qgis")
    qgis_pkg.__path__ = []
    sys.modules["qgis"] = qgis_pkg

    # qgis.PyQt — alias to the real PyQt6
    pyqt_pkg = types.ModuleType("qgis.PyQt")
    pyqt_pkg.__path__ = []
    sys.modules["qgis.PyQt"] = pyqt_pkg

    # Re-export PyQt6 submodules under qgis.PyQt
    for sub in ("QtCore", "QtGui", "QtWidgets"):
        real = __import__(f"PyQt6.{sub}", fromlist=[sub])
        sys.modules[f"qgis.PyQt.{sub}"] = real
        setattr(pyqt_pkg, sub, real)

    # qgis.core — stub the symbols the plugin uses
    qgis_core = types.ModuleType("qgis.core")

    # Qgis — minimal enum-like for severity levels
    class _Qgis:
        Info = 0
        Warning = 1
        Critical = 2
        Success = 3
    qgis_core.Qgis = _Qgis

    # QgsMessageLog — capture log messages for assertions
    class _QgsMessageLog:
        @staticmethod
        def logMessage(msg, tag, level=_Qgis.Info):
            # No-op in tests; could capture if needed
            pass
    qgis_core.QgsMessageLog = _QgsMessageLog

    # QgsProject — minimal stub (tests should mock specifics as needed)
    class _QgsProject:
        _instance = None
        @classmethod
        def instance(cls):
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance
        def layerTreeRoot(self):
            return _MockLayerTreeRoot()
        def mapLayers(self):
            return {}
        def mapLayer(self, lid):
            return None
        def readEntry(self, namespace, key, default=""):
            return (default, False)
        def writeEntry(self, namespace, key, value):
            pass
        def setDirty(self, dirty):
            pass
    qgis_core.QgsProject = _QgsProject

    # Other classes referenced in imports — they won't be exercised in tests
    # that focus on tree_utils, but they need to exist for module import.
    class _QgsApplication:
        @staticmethod
        def getThemeIcon(name):
            from PyQt6.QtGui import QIcon
            return QIcon()
    qgis_core.QgsApplication = _QgsApplication

    class _QgsIconUtils:
        @staticmethod
        def iconForLayer(layer):
            from PyQt6.QtGui import QIcon
            return QIcon()
        @staticmethod
        def iconForWkbType(wkb):
            from PyQt6.QtGui import QIcon
            return QIcon()
    qgis_core.QgsIconUtils = _QgsIconUtils

    class _QgsLayerTree:
        @staticmethod
        def isLayer(node):
            return hasattr(node, "layerId")
        @staticmethod
        def isGroup(node):
            return hasattr(node, "findLayers")
    qgis_core.QgsLayerTree = _QgsLayerTree

    qgis_core.QgsMapLayer = type("QgsMapLayer", (), {})
    qgis_core.QgsVectorLayer = type("QgsVectorLayer", (), {})

    # Mock layer tree root + layer tree layer for visibility tests
    class _MockLayerTreeLayer:
        def __init__(self, layer_id, name, visible=True):
            self._id = layer_id
            self._name = name
            self._visible = visible
            self._visibility_changed_callbacks = []
        def id(self):
            return self._id
        def name(self):
            return self._name
        def itemVisibilityChecked(self):
            return self._visible
        def setItemVisibilityChecked(self, checked):
            self._visible = bool(checked)
            for cb in self._visibility_changed_callbacks:
                try:
                    cb()
                except Exception:
                    pass
        @property
        def visibilityChanged(self):
            return _MockSignal(self._visibility_changed_callbacks)

    class _MockLayerTreeRoot:
        def __init__(self):
            self._layers = {}
        def findLayer(self, layer_id):
            return self._layers.get(layer_id)
        def hasCustomLayerOrder(self):
            return False
        def customLayerOrder(self):
            return []
        def layerOrder(self):
            return []
        def setHasCustomLayerOrder(self, on):
            pass
        def setCustomLayerOrder(self, layers):
            pass
        def emitVisibilityChanged(self):
            pass
        # Test helper: register a layer
        def _register_layer(self, layer_id, name, visible=True):
            ltl = _MockLayerTreeLayer(layer_id, name, visible)
            self._layers[layer_id] = ltl
            return ltl

    qgis_core._MockLayerTreeRoot = _MockLayerTreeRoot
    qgis_core._MockLayerTreeLayer = _MockLayerTreeLayer

    sys.modules["qgis.core"] = qgis_core

    # qgis.gui — stub (not used by tests directly but imported by plugin.py)
    qgis_gui = types.ModuleType("qgis.gui")
    sys.modules["qgis.gui"] = qgis_gui


class _MockSignal:
    """Minimal Qt-signal-like object for testing."""
    def __init__(self, callbacks):
        self._callbacks = callbacks
    def connect(self, slot):
        self._callbacks.append(slot)
    def disconnect(self, slot):
        try:
            self._callbacks.remove(slot)
        except ValueError:
            pass


_install_qgis_stubs()


def _register_plugin_package():
    """Make the plugin importable as `layer_order_plus_qgis4` (its folder name
    differs) without running __init__.py, for every test module."""
    if "layer_order_plus_qgis4" in sys.modules:
        return
    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pkg = types.ModuleType("layer_order_plus_qgis4")
    pkg.__path__ = [plugin_root]
    sys.modules["layer_order_plus_qgis4"] = pkg


_register_plugin_package()


# ----------------------------------------------------------------------
# pytest fixtures
# ----------------------------------------------------------------------
@pytest.fixture
def qapp():
    """Provide a QApplication with offscreen platform so QTreeWidget works headless."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def tree(qapp):
    """Provide a fresh LayerOrderTree (QTreeWidget subclass)."""
    # Import after stubs are installed
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from layer_order_plus_qgis4.tree_widget import LayerOrderTree
    t = LayerOrderTree()
    yield t
    t.deleteLater()
