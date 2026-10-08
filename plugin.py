"""Layer Order Plus — Plugin (orchestrator).

Creates the Model, View, ViewController, and Controller, wires them together,
and handles plugin lifecycle (initGui, unload) + project I/O (load/save tree
JSON via QgsProject entries).

Architecture (1.2.1+ MVC):
  model.py            — LayerOrderModel (plain Python, Qt-free, source of truth)
  view.py             — LayerOrderView (QDockWidget, pure UI, emits signals)
  view_controller.py  — ViewController (Model↔View sync + undo stack)
  controller.py       — LayerOrderController (QGIS↔Model sync + apply state)
  plugin.py           — BetterLayerOrderPlugin (orchestrator, lifecycle, project I/O)
  tree_widget.py      — BetterLayerTree (QTreeWidget, emits drop_intent)
  tree_utils.py       — pure tree helpers
  icons.py            — icon helpers
  undo.py             — TreeStateCommand (QUndoCommand subclass)

Undo integration with QGIS Edit menu is handled here (plugin.py owns the
QUndoGroup + Edit menu actions).
"""
from qgis.PyQt.QtCore import QObject, Qt, QTimer, QEvent
from qgis.PyQt.QtGui import QAction, QKeySequence
from qgis.PyQt.QtGui import QUndoGroup

from qgis.core import QgsProject, QgsMapLayer

from .model import LayerOrderModel
from .view import LayerOrderView
from .view_controller import ViewController
from .controller import LayerOrderController
from .logger import _log, _vlog, _vlog_method, _vlog_error


class BetterLayerOrderPlugin(QObject):
    """Plugin orchestrator — creates and wires MVC components.

    Owns:
    - The View (QDockWidget, registered with iface)
    - The ViewController (Model↔View sync + undo stack)
    - The Controller (QGIS↔Model sync)
    - The QUndoGroup + Edit menu actions (undo/redo integration)
    - Project I/O (read/write tree JSON via QgsProject entries)
    """

    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        self.action = None
        self.view = None
        self.model = None
        self.view_controller = None
        self.controller = None
        self._undo_group = None
        self._act_undo = None
        self._act_redo = None
        self._act_redo_alt = None
        self._filter_installed = False
        self._loading = False

    # ==================================================================
    # Plugin lifecycle
    # ==================================================================
    def initGui(self):
        _vlog_method("initGui")
        # Toggle action in the plugin menu
        self.action = QAction("Layer Order Plus", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setChecked(True)
        self.action.toggled.connect(self._toggle_dock)
        self.iface.addPluginToMenu("Layer Order Plus", self.action)

        # Create MVC components
        self.model = LayerOrderModel()
        self.view = LayerOrderView(self.iface.mainWindow())
        self.view_controller = ViewController(
            self.model, self.view,
            layer_icon_provider=self.controller_layer_icon_provider,
            parent=self.view
        )
        self.controller = LayerOrderController(
            self.view, self.view_controller, self.iface, parent=self.view
        )

        # Register the dock
        self.iface.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.view)

        # Suspend apply during initial setup
        self.controller.set_apply_suspended(True)

        # Connect project signals
        QgsProject.instance().cleared.connect(self._on_project_cleared)
        self.iface.projectRead.connect(self._on_project_read)
        self.iface.newProjectCreated.connect(self._on_project_read)

        # Setup undo integration with Edit menu
        self._setup_undo_integration()

        # Wire the View's undo/redo shortcuts to the ViewController's undo stack
        self.view.undo_shortcut_activated.connect(self.view_controller.undo_stack.undo)
        self.view.redo_shortcut_activated.connect(self.view_controller.undo_stack.redo)

        # Wire autosave — when the ViewController's Model changes, persist
        # the tree to the project + mark dirty. This ensures tree changes
        # survive a crash (not just a clean unload).
        self.view_controller.order_changed.connect(self._mark_dirty)
        self.view_controller.save_requested.connect(self._save_tree_json)

        # Load the current project (deferred to next event loop)
        QTimer.singleShot(0, self._on_project_read)

    def unload(self):
        _vlog_method("unload")
        # Teardown undo integration
        self._teardown_undo_integration()

        # Teardown controller (disconnects QGIS signals)
        if self.controller:
            self.controller.teardown()

        # Disconnect project signals
        for signal, slot in (
            (QgsProject.instance().cleared, self._on_project_cleared),
            (self.iface.projectRead, self._on_project_read),
            (self.iface.newProjectCreated, self._on_project_read),
        ):
            try:
                signal.disconnect(slot)
            except Exception:
                pass

        # Save the tree before unloading
        if self.view_controller:
            self._save_tree_json(self.view_controller.serialize())

        if self.view:
            self.iface.removeDockWidget(self.view)
            self.view.deleteLater()
            self.view = None

        if self.action:
            self.iface.removePluginMenu("Layer Order Plus", self.action)
            self.action.deleteLater()
            self.action = None

    # ==================================================================
    # Dock toggle
    # ==================================================================
    def _toggle_dock(self, on):
        _vlog_method("_toggle_dock")
        if self.view is None:
            return
        if on:
            self.view.show()
            self.view.raise_()
            if self._undo_group:
                self._undo_group.setActiveStack(self.view_controller.undo_stack)
        else:
            self.view.hide()

    # ==================================================================
    # Layer icon provider (passed to ViewController)
    # ==================================================================
    def controller_layer_icon_provider(self, layer_id):
        """Return a QIcon for a layer id — delegates to the controller."""
        if self.controller:
            return self.controller.layer_icon_provider(layer_id)
        from qgis.PyQt.QtGui import QIcon
        return QIcon()

    # ==================================================================
    # Project lifecycle
    # ==================================================================
    def _on_project_cleared(self):
        _vlog_method("_on_project_cleared")
        if self.view_controller:
            self.controller.set_apply_suspended(True)
            self.view_controller.clear()

    def _on_project_read(self):
        _vlog_method("_on_project_read")
        if not self.view_controller:
            return
        self._loading = True
        self.controller.set_apply_suspended(True)
        try:
            raw_json = self._load_tree_json()
            if raw_json:
                # Load saved tree from project
                self.view_controller.load_from_json(raw_json)
            else:
                # No saved tree — populate from current project layers in draw order.
                # Populate the Model directly, then rebuild the View once.
                # (No JSON round-trip — that was losing layer names in 1.2.1/1.2.2.)
                layers = self.controller.get_ordered_project_layers()
                self.view_controller.clear()
                with self.model.block_notifications():
                    for lyr in layers:
                        self.model.add_layer(lyr.id(), lyr.name())
                # Force a single View rebuild after the block
                self.view_controller._rebuild_view_from_model()
                self.view_controller._snapshot = self.model.serialize()

            # Connect rename + visibility for all existing layers
            for lyr in QgsProject.instance().mapLayers().values():
                self.controller.connect_layer(lyr)

            # Sync checkboxes from QGIS
            self.controller.sync_state_from_project()

            # Clear undo stack (fresh project)
            self.view_controller.undo_stack.clear()
        except Exception as e:
            from qgis.core import QgsMessageLog, Qgis
            QgsMessageLog.logMessage(f"_on_project_read FAILED: {e!r}", "LayerOrderPlus", Qgis.Critical)
            import traceback
            QgsMessageLog.logMessage(traceback.format_exc(), "LayerOrderPlus", Qgis.Critical)
        finally:
            self._loading = False
            self.controller.set_apply_suspended(False)
            self.controller.request_apply()

    # ==================================================================
    # Project I/O (tree JSON persistence)
    # ==================================================================
    def _load_tree_json(self):
        _vlog_method("_load_tree_json")
        return QgsProject.instance().readEntry("BetterLayerOrder", "tree_json", "")[0] or ""

    def _save_tree_json(self, raw: str):
        _vlog_method("_save_tree_json")
        proj = QgsProject.instance()
        proj.writeEntry("BetterLayerOrder", "tree_json", raw)
        proj.setDirty(True)

    def _mark_dirty(self):
        _vlog_method("_mark_dirty")
        """Mark the project dirty when the order changes (triggers autosave)."""
        if self._loading:
            return
        try:
            QgsProject.instance().setDirty(True)
        except Exception:
            pass

    # ==================================================================
    # Undo integration with QGIS Edit menu
    # ==================================================================
    def _setup_undo_integration(self):
        _vlog_method("_setup_undo_integration")
        """Wire the ViewController's undo stack to QGIS Edit menu + app shortcuts.

        Actions are added to BOTH the plugin menu AND the main window (so
        ApplicationShortcut shortcuts fire) AND the Edit menu (so users
        find them in the expected place).
        """
        mw = self.iface.mainWindow()

        self._undo_group = QUndoGroup(self)
        self._undo_group.addStack(self.view_controller.undo_stack)
        self._undo_group.setActiveStack(self.view_controller.undo_stack)

        # Create Edit menu actions
        self._act_undo = QAction("Undo layer order", mw)
        self._act_redo = QAction("Redo layer order", mw)
        self._act_redo_alt = QAction("Redo layer order", mw)

        self._act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self._act_undo.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self._act_redo.setShortcut(QKeySequence.StandardKey.Redo)
        self._act_redo.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self._act_redo_alt.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        self._act_redo_alt.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)

        self._act_undo.triggered.connect(self._undo_group.undo)
        self._act_redo.triggered.connect(self._redo_with_fallback)
        self._act_redo_alt.triggered.connect(self._redo_with_fallback)

        # Add actions to the main window so ApplicationShortcut shortcuts fire
        mw.addAction(self._act_undo)
        mw.addAction(self._act_redo)
        mw.addAction(self._act_redo_alt)

        # Install an event filter on the main window so we can intercept
        # Ctrl+Z / Ctrl+Y when no vector layer is being edited (otherwise
        # QGIS digitizing undo takes priority).
        mw.installEventFilter(self)
        self._filter_installed = True

        # Add to plugin menu
        self.iface.addPluginToMenu("Layer Order Plus", self._act_undo)
        self.iface.addPluginToMenu("Layer Order Plus", self._act_redo)

        # Also add to the Edit menu if available
        self._add_to_edit_menu()

        self._refresh_undo_actions()
        self.view_controller.undo_stack.cleanChanged.connect(self._refresh_undo_actions)
        self.view_controller.undo_stack.indexChanged.connect(self._refresh_undo_actions)
        self.view_controller.undo_stack.canUndoChanged.connect(self._refresh_undo_actions)
        self.view_controller.undo_stack.canRedoChanged.connect(self._refresh_undo_actions)

    def _find_edit_menu(self):
        _vlog_method("_find_edit_menu")
        """Find QGIS's Edit menu (QMenuBar → 'Edit' / 'Édition' / etc.)."""
        try:
            mw = self.iface.mainWindow()
            menubar = mw.menuBar()
            for action in menubar.actions():
                text = action.text().lower()
                # Match 'edit' / 'édition' / 'edición' etc.
                if "edit" in text or "édition" in text or "edición" in text:
                    menu = action.menu()
                    if menu is not None:
                        return menu
        except Exception:
            pass
        return None

    def _add_to_edit_menu(self):
        _vlog_method("_add_to_edit_menu")
        """Add Undo/Redo layer order actions to QGIS's Edit menu."""
        edit_menu = self._find_edit_menu()
        if edit_menu is None:
            return
        try:
            edit_menu.addSeparator()
            edit_menu.addAction(self._act_undo)
            edit_menu.addAction(self._act_redo)
            self._edit_menu = edit_menu  # remember for teardown
        except Exception:
            pass

    def _teardown_undo_integration(self):
        _vlog_method("_teardown_undo_integration")
        if self._filter_installed:
            try:
                self.iface.mainWindow().removeEventFilter(self)
            except Exception:
                pass
            self._filter_installed = False

        for act in (self._act_undo, self._act_redo, self._act_redo_alt):
            if act is not None:
                try:
                    self.iface.removePluginMenu("Layer Order Plus", act)
                except Exception:
                    pass
                # Remove from main window actions
                try:
                    self.iface.mainWindow().removeAction(act)
                except Exception:
                    pass
                act.deleteLater()
        self._act_undo = None
        self._act_redo = None
        self._act_redo_alt = None

        if self._undo_group is not None:
            self._undo_group.deleteLater()
            self._undo_group = None

    def _redo_with_fallback(self):
        _vlog_method("_redo_with_fallback")
        if self._undo_group and self._undo_group.canRedo():
            self._undo_group.redo()

    def _refresh_undo_actions(self):
        _vlog_method("_refresh_undo_actions")
        if self._act_undo is None or self._undo_group is None:
            return
        can_undo = self._undo_group.canUndo()
        can_redo = self._undo_group.canRedo()
        # Only enable when no vector layer is in edit mode (digitizing undo takes priority)
        editing = self._is_editing_layer()
        self._act_undo.setEnabled(can_undo and not editing)
        self._act_redo.setEnabled(can_redo and not editing)
        self._act_redo_alt.setEnabled(can_redo and not editing)

    def _is_editing_layer(self):
        _vlog_method("_is_editing_layer")
        """Return True if any vector layer is currently in edit mode."""
        try:
            for lyr in QgsProject.instance().mapLayers().values():
                if isinstance(lyr, QgsMapLayer) and hasattr(lyr, 'isEditable'):
                    if lyr.isEditable():
                        return True
        except Exception:
            pass
        return False

    # ==================================================================
    # Event filter (intercepts Ctrl+Z when not editing a layer)
    # ==================================================================
    def eventFilter(self, obj, event):
        _vlog_method("eventFilter")
        from qgis.PyQt.QtCore import QEvent
        if event.type() == QEvent.Type.ShortcutOverride:
            key = event.key()
            mods = event.modifiers()
            from qgis.PyQt.QtCore import Qt
            # Ctrl+Z (no Shift) = undo
            is_undo = (key == Qt.Key.Key_Z and mods == Qt.KeyboardModifier.ControlModifier)
            # Ctrl+Y OR Ctrl+Shift+Z = redo
            is_redo_y = (key == Qt.Key.Key_Y and mods == Qt.KeyboardModifier.ControlModifier)
            is_redo_shift = (key == Qt.Key.Key_Z and mods == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
            if (is_undo or is_redo_y or is_redo_shift) and not self._is_editing_layer():
                if is_undo and self._undo_group and self._undo_group.canUndo():
                    event.accept()
                    self._undo_group.undo()
                    return True
                if (is_redo_y or is_redo_shift) and self._undo_group and self._undo_group.canRedo():
                    event.accept()
                    self._undo_group.redo()
                    return True
        return super().eventFilter(obj, event)
