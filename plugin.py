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

Undo / redo of the layer order is deliberately **not** integrated with
QGIS (no Edit-menu entries, no Ctrl+Z): QGIS's undo is the edit buffer of
the layer being edited, and sharing its keys or menu was ambiguous. The
panel has its own Undo / Redo buttons.
"""
from qgis.PyQt.QtCore import QObject, Qt, QTimer

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

        # Load whatever project is open once QGIS has finished starting up
        QTimer.singleShot(0, self.controller.load_project)

    def unload(self):
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
