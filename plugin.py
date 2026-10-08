"""Layer Order Plus — Plugin: builds the pieces, wires them, owns the lifecycle.

Architecture
------------
    model.py            LayerOrderModel   — the document; single source of truth (no Qt)
    view.py             LayerOrderView    — renders, emits intents (no logic)
    tree_widget.py      BetterLayerTree   — tree that reports drop/expand/check intents
    view_controller.py  ViewController    — intents → Model (+ undo), Model → View
    controller.py       LayerOrderController — QGIS ↔ Model
    reconcile.py        reconcile_tree    — infer groups from a flat QGIS order (pure)
    undo.py             TreeStateCommand  — document snapshot undo step

Nothing but this module knows all the pieces. The only cross-wiring is the
ViewController's requests for QGIS-owned state (visibility, control of the
rendering order), which go to the Controller.

This module also integrates the undo stack with QGIS (Edit menu, Ctrl+Z
when no vector layer is being edited).
"""
from qgis.PyQt.QtCore import QEvent, QObject, Qt, QTimer
from qgis.PyQt.QtGui import QAction, QKeySequence

from qgis.core import QgsProject

from .controller import LayerOrderController
from .icons import icon_for_layer_id
from .logger import _log, set_verbose
from .model import LayerOrderModel
from .view import LayerOrderView
from .view_controller import ViewController

MENU = "Layer Order Plus"


class BetterLayerOrderPlugin(QObject):
    """QGIS plugin entry point (see classFactory in __init__.py)."""

    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        self.model = None
        self.view = None
        self.view_controller = None
        self.controller = None
        self._toggle_action = None
        self._undo_actions = []
        self._edit_menu = None

    # ==================================================================
    # Lifecycle
    # ==================================================================
    def initGui(self):
        mw = self.iface.mainWindow()
        self.model = LayerOrderModel()
        self.view = LayerOrderView(mw)
        self.view_controller = ViewController(self.model, self.view,
                                              layer_icon=icon_for_layer_id, parent=self.view)
        self.controller = LayerOrderController(self.model, self.iface, parent=self.view)

        # ViewController requests for QGIS-owned state → Controller
        vc, ctl = self.view_controller, self.controller
        vc.visibility_requested.connect(ctl.set_layers_visible)
        vc.control_requested.connect(ctl.set_control_enabled)
        ctl.project_loaded.connect(vc.reset_history)

        # Debug logging is infrastructure, not document state
        self.view.verbose_toggled.connect(self._set_verbose)

        self.iface.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.view)
        self._toggle_action = QAction(MENU, mw)
        self._toggle_action.setCheckable(True)
        self._toggle_action.setChecked(True)
        self._toggle_action.toggled.connect(self.view.setVisible)
        self.view.visibilityChanged.connect(self._toggle_action.setChecked)
        self.iface.addPluginToMenu(MENU, self._toggle_action)

        self._setup_undo_integration()

        # Load whatever project is open once QGIS has finished starting up
        QTimer.singleShot(0, self.controller.load_project)

    def unload(self):
        self._teardown_undo_integration()
        if self.controller is not None:
            self.controller.teardown()
        if self._toggle_action is not None:
            self.iface.removePluginMenu(MENU, self._toggle_action)
            self._toggle_action.deleteLater()
            self._toggle_action = None
        if self.view is not None:
            self.iface.removeDockWidget(self.view)
            self.view.deleteLater()   # also deletes the ViewController and Controller
        self.model = self.view = self.view_controller = self.controller = None

    def _set_verbose(self, on: bool):
        set_verbose(on)
        self.view.render_verbose(on)

    # ==================================================================
    # Undo integration with QGIS
    # ==================================================================
    def _setup_undo_integration(self):
        """Edit-menu actions + application-wide shortcuts for the undo stack.

        The actions are only enabled while no vector layer is being edited,
        so QGIS's digitizing undo keeps priority. The main-window event
        filter catches Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z that QGIS's own
        (disabled) undo action would otherwise swallow.
        """
        mw = self.iface.mainWindow()
        stack = self.view_controller.undo_stack
        act_undo = QAction("Undo layer order", mw)
        act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        act_undo.triggered.connect(stack.undo)
        act_redo = QAction("Redo layer order", mw)
        act_redo.setShortcut(QKeySequence.StandardKey.Redo)
        act_redo.triggered.connect(stack.redo)
        act_redo_alt = QAction("Redo layer order", mw)
        act_redo_alt.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        act_redo_alt.triggered.connect(stack.redo)
        self._undo_actions = [act_undo, act_redo, act_redo_alt]
        for act in self._undo_actions:
            act.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            mw.addAction(act)

        self.iface.addPluginToMenu(MENU, act_undo)
        self.iface.addPluginToMenu(MENU, act_redo)
        self._edit_menu = self._find_edit_menu()
        if self._edit_menu is not None:
            self._undo_actions.append(self._edit_menu.addSeparator())
            self._edit_menu.addAction(act_undo)
            self._edit_menu.addAction(act_redo)

        self.view.undo_shortcut_activated.connect(stack.undo)
        self.view.redo_shortcut_activated.connect(stack.redo)
        for sig in (stack.canUndoChanged, stack.canRedoChanged, stack.indexChanged):
            sig.connect(self._refresh_undo_actions)
        self._refresh_undo_actions()
        mw.installEventFilter(self)

    def _teardown_undo_integration(self):
        mw = self.iface.mainWindow()
        mw.removeEventFilter(self)
        for act in self._undo_actions:
            self.iface.removePluginMenu(MENU, act)
            mw.removeAction(act)
            if self._edit_menu is not None:
                self._edit_menu.removeAction(act)
            act.deleteLater()
        self._undo_actions = []
        self._edit_menu = None

    def _find_edit_menu(self):
        """QGIS's Edit menu, matched by its object name (locale-independent),
        falling back to the title for older builds."""
        for action in self.iface.mainWindow().menuBar().actions():
            menu = action.menu()
            if menu is None:
                continue
            if menu.objectName() == "mEditMenu":
                return menu
            text = action.text().replace("&", "").lower()
            if text in ("edit", "édition", "edición", "bearbeiten", "modifica"):
                return menu
        return None

    def _refresh_undo_actions(self):
        stack = self.view_controller.undo_stack
        editing = self._is_editing_layer()
        act_undo, act_redo, act_redo_alt = self._undo_actions[:3]
        act_undo.setEnabled(stack.canUndo() and not editing)
        act_redo.setEnabled(stack.canRedo() and not editing)
        act_redo_alt.setEnabled(stack.canRedo() and not editing)

    @staticmethod
    def _is_editing_layer() -> bool:
        """True if any layer is in edit mode (digitizing undo has priority)."""
        return any(getattr(lyr, "isEditable", lambda: False)()
                   for lyr in QgsProject.instance().mapLayers().values())

    def eventFilter(self, obj, event):
        if event.type() not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            return super().eventFilter(obj, event)
        ctrl = Qt.KeyboardModifier.ControlModifier
        shift = Qt.KeyboardModifier.ShiftModifier
        key, mods = event.key(), event.modifiers()
        is_undo = key == Qt.Key.Key_Z and mods == ctrl
        is_redo = (key == Qt.Key.Key_Y and mods == ctrl) or (key == Qt.Key.Key_Z and mods == ctrl | shift)
        if not (is_undo or is_redo) or self._is_editing_layer():
            return super().eventFilter(obj, event)
        stack = self.view_controller.undo_stack
        if is_undo and stack.canUndo():
            event.accept()
            stack.undo()
            return True
        if is_redo and stack.canRedo():
            event.accept()
            stack.redo()
            return True
        return super().eventFilter(obj, event)
