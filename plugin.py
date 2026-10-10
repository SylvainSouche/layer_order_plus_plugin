"""Advanced Layer Order — Plugin: builds the pieces, wires them, owns the lifecycle.

Architecture
------------
    model.py            LayerOrderModel   — the document; single source of truth (no Qt)
    view.py             LayerOrderView    — renders, emits intents (no logic)
    tree_model.py       LayerOrderItemModel — Qt item model of the View; drops/checks → intents
    tree_view.py        LayerOrderTree    — QTreeView; expand/collapse → intents
    view_controller.py  ViewController    — intents → Model (+ undo), Model → View
    controller.py       LayerOrderController — QGIS ↔ Model
    reconcile.py        reconcile_tree    — infer groups from a flat QGIS order (pure)
    undo.py             TreeStateCommand  — document snapshot undo step

Nothing but this module knows all the pieces. The only cross-wiring is the
ViewController's requests for QGIS-owned state (visibility, control of the
rendering order), which go to the Controller.

This module also integrates the undo stack with QGIS (Edit menu, Ctrl+Z
when no layer is being edited).
"""
from qgis.core import QgsProject
from qgis.PyQt.QtCore import QEvent, QObject, Qt, QTimer
from qgis.PyQt.QtGui import QAction

from .controller import LayerOrderController
from .icons import icon_for_layer_id
from .logger import set_verbose
from .model import LayerOrderModel
from .view import LayerOrderView
from .view_controller import ViewController

MENU = "Advanced Layer Order"


class AdvancedLayerOrderPlugin(QObject):
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
        self._edit_separator = None

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
        ctl.external_edit.connect(vc.record_step)

        # Debug logging is infrastructure, not document state
        self.view.verbose_toggled.connect(self._set_verbose)

        self.iface.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.view)
        # Qt's own show/hide action for the dock. Don't wire visibilityChanged
        # to a home-made toggle: it also fires when the dock is merely hidden
        # behind another tab or while QGIS rearranges panels (closing a
        # project), and the toggle then really closed the dock.
        self._toggle_action = self.view.toggleViewAction()
        self._toggle_action.setText(MENU)
        self.iface.addPluginToMenu(MENU, self._toggle_action)

        self._setup_undo_integration()

        # Load whatever project is open once QGIS has finished starting up
        QTimer.singleShot(0, self.controller.load_project)

    def unload(self):
        self._teardown_undo_integration()
        if self.controller is not None:
            self.controller.teardown()
        if self._toggle_action is not None:
            self.iface.removePluginMenu(MENU, self._toggle_action)   # owned by the dock
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
        """Undo/redo for layer-order edits, without fighting QGIS's own undo.

        * Menu: "Undo/Redo layer order" in the plugin menu and the Edit menu.
          They carry no shortcut: a second Ctrl+Z shortcut next to QGIS's
          own would make both ambiguous.
        * Keyboard, focus in the panel: the tree claims the keys itself
          (ShortcutOverride, see tree_view.py), even while a layer is edited.
        * Keyboard, focus elsewhere: Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z key
          presses that nothing else handled reach the main window, where an
          event filter applies them to our stack — unless a layer is being
          edited, in which case QGIS's digitizing undo has priority.
        QGIS's own undo is the edit buffer of the layer being edited; a
        plugin can't add steps to it, hence our own stack.
        """
        mw = self.iface.mainWindow()
        stack = self.view_controller.undo_stack
        act_undo = QAction("Undo layer order", mw)
        act_undo.triggered.connect(stack.undo)
        act_redo = QAction("Redo layer order", mw)
        act_redo.triggered.connect(stack.redo)
        self._undo_actions = [act_undo, act_redo]
        for act in self._undo_actions:
            self.iface.addPluginToMenu(MENU, act)
        self._edit_menu = self._find_edit_menu()
        if self._edit_menu is not None:
            self._edit_separator = self._edit_menu.addSeparator()
            self._edit_menu.addActions(self._undo_actions)

        stack.canUndoChanged.connect(self._refresh_undo_actions)
        stack.canRedoChanged.connect(self._refresh_undo_actions)
        self._refresh_undo_actions()
        mw.installEventFilter(self)

    def _teardown_undo_integration(self):
        self.iface.mainWindow().removeEventFilter(self)
        stack = self.view_controller.undo_stack
        stack.canUndoChanged.disconnect(self._refresh_undo_actions)
        stack.canRedoChanged.disconnect(self._refresh_undo_actions)
        for act in self._undo_actions:
            self.iface.removePluginMenu(MENU, act)
            if self._edit_menu is not None:
                self._edit_menu.removeAction(act)
            act.deleteLater()
        if self._edit_menu is not None:
            self._edit_menu.removeAction(self._edit_separator)
        self._undo_actions = []
        self._edit_menu = self._edit_separator = None

    def _find_edit_menu(self):
        """QGIS's Edit menu, by object name (locale-independent)."""
        for action in self.iface.mainWindow().menuBar().actions():
            menu = action.menu()
            if menu is not None and menu.objectName() == "mEditMenu":
                return menu
        return None

    def _refresh_undo_actions(self):
        stack = self.view_controller.undo_stack
        act_undo, act_redo = self._undo_actions
        act_undo.setEnabled(stack.canUndo())
        act_redo.setEnabled(stack.canRedo())

    @staticmethod
    def _is_editing_layer() -> bool:
        """True if any layer is in edit mode (digitizing undo has priority)."""
        return any(getattr(lyr, "isEditable", lambda: False)()
                   for lyr in QgsProject.instance().mapLayers().values())

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.KeyPress:
            return False
        ctrl = Qt.KeyboardModifier.ControlModifier
        key, mods = event.key(), event.modifiers()
        is_undo = key == Qt.Key.Key_Z and mods == ctrl
        is_redo = ((key == Qt.Key.Key_Y and mods == ctrl)
                   or (key == Qt.Key.Key_Z and mods == ctrl | Qt.KeyboardModifier.ShiftModifier))
        if not (is_undo or is_redo) or self._is_editing_layer():
            return False
        stack = self.view_controller.undo_stack
        if is_undo and stack.canUndo():
            stack.undo()
            return True
        if is_redo and stack.canRedo():
            stack.redo()
            return True
        return False
