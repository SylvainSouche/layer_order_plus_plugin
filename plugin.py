# plugin.py
from qgis.PyQt.QtCore import QObject, Qt, QTimer, QEvent
from qgis.PyQt.QtGui import QAction, QKeySequence
from qgis.PyQt.QtGui import QUndoGroup
from qgis.core import QgsProject, QgsMapLayer

from .dock import BetterLayerOrderDock


class BetterLayerOrderPlugin(QObject):
    """
    Undo integration with QGIS Edit menu and app shortcuts.

    - Dock owns a QUndoStack for order/group changes.
    - QUndoGroup + Edit menu actions "Undo layer order" / "Redo layer order".
    - Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z when no vector layer is in edit mode
      (digitizing undo keeps priority while editing).
    """

    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        self.action = None
        self.dock = None
        self._undo_group = None
        self._act_undo = None
        self._act_redo = None
        self._act_redo_alt = None
        self._filter_installed = False

    def initGui(self):
        self.action = QAction("Layer Order Plus", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setChecked(True)
        self.action.toggled.connect(self._toggle_dock)
        self.iface.addPluginToMenu("Layer Order Plus", self.action)

        self.dock = BetterLayerOrderDock(self.iface)
        self.dock.set_save_callback(self._save_tree_json)
        self.iface.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock)

        self.dock.set_apply_suspended(True)

        QgsProject.instance().cleared.connect(self._on_project_cleared)
        self.iface.projectRead.connect(self._on_project_read)
        self.iface.newProjectCreated.connect(self._on_project_read)

        QgsProject.instance().layersAdded.connect(self._on_layers_added)
        QgsProject.instance().layersWillBeRemoved.connect(self._on_layers_removed)

        self._setup_undo_integration()

        QTimer.singleShot(0, self._on_project_read)

    def unload(self):
        self._teardown_undo_integration()

        for signal, slot in (
            (QgsProject.instance().layersAdded, self._on_layers_added),
            (QgsProject.instance().layersWillBeRemoved, self._on_layers_removed),
            (QgsProject.instance().cleared, self._on_project_cleared),
            (self.iface.projectRead, self._on_project_read),
            (self.iface.newProjectCreated, self._on_project_read),
        ):
            try:
                signal.disconnect(slot)
            except Exception:
                pass

        if self.dock:
            self._save_tree_json(self.dock._serialize_tree())
            try:
                self.dock._disconnect_all_layer_renames()
            except Exception:
                pass
            self.dock.deleteLater()
            self.dock = None

        if self.action:
            self.iface.removePluginMenu("Layer Order Plus", self.action)
            self.action.deleteLater()
            self.action = None

    def _setup_undo_integration(self):
        if not self.dock:
            return
        mw = self.iface.mainWindow()
        stack = self.dock.undo_stack

        self._undo_group = QUndoGroup(mw)
        self._undo_group.addStack(stack)
        self._undo_group.setActiveStack(stack)

        self._act_undo = self._undo_group.createUndoAction(mw, "Undo layer order")
        self._act_redo = self._undo_group.createRedoAction(mw, "Redo layer order")
        self._act_undo.setText("Undo layer order")
        self._act_redo.setText("Redo layer order")
        self._act_undo.setToolTip("Undo last Layer Order Plus change")
        self._act_redo.setToolTip("Redo last Layer Order Plus change")

        self._act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self._act_redo.setShortcut(QKeySequence.StandardKey.Redo)
        self._act_undo.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self._act_redo.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)

        self._act_redo_alt = QAction("Redo layer order", mw)
        self._act_redo_alt.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        self._act_redo_alt.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self._act_redo_alt.triggered.connect(self._try_redo)

        stack.canUndoChanged.connect(self._refresh_undo_actions)
        stack.canRedoChanged.connect(self._refresh_undo_actions)

        mw.installEventFilter(self)
        self._filter_installed = True

        edit_menu = self._find_edit_menu()
        if edit_menu is not None:
            edit_menu.addSeparator()
            edit_menu.addAction(self._act_undo)
            edit_menu.addAction(self._act_redo)

        mw.addAction(self._act_undo)
        mw.addAction(self._act_redo)
        mw.addAction(self._act_redo_alt)

        self.dock.visibilityChanged.connect(self._on_dock_visibility)
        self._refresh_undo_actions()

    def _teardown_undo_integration(self):
        mw = self.iface.mainWindow()
        if self._filter_installed:
            try:
                mw.removeEventFilter(self)
            except Exception:
                pass
            self._filter_installed = False

        for act in (self._act_undo, self._act_redo, self._act_redo_alt):
            if act is None:
                continue
            try:
                mw.removeAction(act)
            except Exception:
                pass
            try:
                act.deleteLater()
            except Exception:
                pass

        self._act_undo = None
        self._act_redo = None
        self._act_redo_alt = None
        self._undo_group = None

    def _find_edit_menu(self):
        mw = self.iface.mainWindow()
        bar = mw.menuBar() if mw else None
        if bar is None:
            return None
        titles = {
            "edit", "édition", "edition", "bearbeiten",
            "modifica", "editar", "edycja",
        }
        for action in bar.actions():
            menu = action.menu()
            if menu is None:
                continue
            t = action.text().replace("&", "").strip().lower()
            if t in titles or t.startswith("edit"):
                return menu
        return None

    def _any_layer_editing(self) -> bool:
        for layer in QgsProject.instance().mapLayers().values():
            try:
                if hasattr(layer, "isEditable") and layer.isEditable():
                    return True
            except Exception:
                pass
        return False

    def _should_handle_order_undo(self) -> bool:
        if not self.dock or not self.dock.isVisible():
            return False
        if self._any_layer_editing():
            return False
        return self.dock.undo_stack.canUndo()

    def _should_handle_order_redo(self) -> bool:
        if not self.dock or not self.dock.isVisible():
            return False
        if self._any_layer_editing():
            return False
        return self.dock.undo_stack.canRedo()

    def _refresh_undo_actions(self, *args):
        editing = self._any_layer_editing()
        if self._act_undo and self.dock:
            self._act_undo.setEnabled(
                (not editing) and self.dock.undo_stack.canUndo()
            )
        if self._act_redo and self.dock:
            self._act_redo.setEnabled(
                (not editing) and self.dock.undo_stack.canRedo()
            )
        if self._act_redo_alt and self._act_redo:
            self._act_redo_alt.setEnabled(self._act_redo.isEnabled())

    def _try_undo(self) -> bool:
        if self._should_handle_order_undo():
            self.dock.undo_stack.undo()
            return True
        return False

    def _try_redo(self) -> bool:
        if self._should_handle_order_redo():
            self.dock.undo_stack.redo()
            return True
        return False

    def _on_dock_visibility(self, visible: bool):
        if visible and self._undo_group and self.dock:
            self._undo_group.setActiveStack(self.dock.undo_stack)
        self._refresh_undo_actions()

    def eventFilter(self, obj, event):
        et = event.type()
        if et == QEvent.Type.ShortcutOverride:
            try:
                if event.matches(QKeySequence.StandardKey.Undo) and self._should_handle_order_undo():
                    event.accept()
                    return True
                if event.matches(QKeySequence.StandardKey.Redo) and self._should_handle_order_redo():
                    event.accept()
                    return True
            except Exception:
                pass
        elif et == QEvent.Type.KeyPress:
            try:
                if event.matches(QKeySequence.StandardKey.Undo) and self._try_undo():
                    return True
                if event.matches(QKeySequence.StandardKey.Redo) and self._try_redo():
                    return True
                if (
                    event.modifiers() & Qt.KeyboardModifier.ControlModifier
                    and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                    and event.key() == Qt.Key.Key_Z
                    and self._try_redo()
                ):
                    return True
            except Exception:
                pass
        return super().eventFilter(obj, event)

    def _toggle_dock(self, on):
        if self.dock:
            self.dock.setVisible(bool(on))
            if on:
                self.dock.raise_()
                if self._undo_group:
                    self._undo_group.setActiveStack(self.dock.undo_stack)

    def _on_project_cleared(self):
        if self.dock:
            self.dock.set_apply_suspended(True)
            self.dock.clear_tree_ui()
            self.dock.undo_stack.clear()
            self._refresh_undo_actions()

    def _on_project_read(self):
        if not self.dock:
            return
        self.dock.set_apply_suspended(True)
        self.dock.load_from_project(self._load_tree_json())
        self.dock.set_apply_suspended(False)
        self.dock.request_apply()
        self.dock.undo_stack.clear()
        self._refresh_undo_actions()

    def _load_tree_json(self):
        return QgsProject.instance().readEntry("BetterLayerOrder", "tree_json", "")[0] or ""

    def _save_tree_json(self, raw: str):
        proj = QgsProject.instance()
        proj.writeEntry("BetterLayerOrder", "tree_json", raw)
        proj.setDirty(True)

    def _on_layers_added(self, layers):
        if self.dock:
            self.dock.on_layers_added(layers)

    def _on_layers_removed(self, layer_ids):
        if self.dock:
            self.dock.on_layers_removed(layer_ids)
