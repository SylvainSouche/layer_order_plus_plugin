"""Layer Order Plus — ViewController (Model ↔ View synchronization).

The ViewController is the bridge between the Model and the View:
- Listens to Model events → calls View methods to update the display
- Listens to View signals → calls Model methods to update the state
- Owns the undo stack (snapshots Model state before/after user actions)
- Translates semantic drop intents to Model mutations

The ViewController does NOT:
- Touch QGIS directly (that's the Controller's job)
- Know about QgsProject, customLayerOrder, mapCanvas, etc.
- Build UI widgets (that's the View's job)

The ViewController DOES need a layer_icon_provider callable (supplied by
the Controller) to pass layer icons to the View when building layer items.
"""
import json
import traceback

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QUndoStack

from qgis.core import Qgis
from .logger import _log, _vlog, _vlog_method, _vlog_error

from .model import (
    LayerOrderModel,
    GroupNode,
    LayerNode,
    EVENT_MODEL_LOADED,
    EVENT_LAYER_ADDED,
    EVENT_LAYER_REMOVED,
    EVENT_LAYER_RENAMED,
    EVENT_GROUP_CREATED,
    EVENT_GROUP_DELETED,
    EVENT_GROUP_RENAMED,
    EVENT_ITEM_MOVED,
    EVENT_VISIBILITY_CHANGED,
    EVENT_EXPANDED_CHANGED,
    EVENT_ORDER_CHANGED,
    TREE_JSON_SCHEMA_VERSION,
)
from .tree_utils import TYPE_GROUP, TYPE_LAYER
from .tree_widget import DROP_ON, DROP_ABOVE, DROP_BELOW
from .undo import TreeStateCommand
from .view import LayerOrderView


LOG_TAG = "LayerOrderPlus"


def _log(msg, level=Qgis.Info):
    from qgis.core import QgsMessageLog
    try:
        QgsMessageLog.logMessage(str(msg), LOG_TAG, level)
    except Exception:
        try:
            print(f"[{LOG_TAG}] {msg}")
        except Exception:
            pass


class ViewController(QObject):
    """Bridge between LayerOrderModel and LayerOrderView.

    Owns the undo stack. Translates user actions (View signals) to Model
    mutations, and Model events to View updates.
    """

    # Signal emitted when the Model's order changes (Controller listens to apply to QGIS)
    order_changed = pyqtSignal()
    # Signal emitted when the Model is bulk-loaded (Controller listens to re-apply)
    model_loaded = pyqtSignal()
    # Signal emitted when the tree state changes and should be persisted
    save_requested = pyqtSignal(str)  # serialized tree JSON

    def __init__(self, model: LayerOrderModel, view: LayerOrderView,
                 layer_icon_provider=None, parent=None):
        super().__init__(parent)
        self._model = model
        self._view = view
        self._layer_icon_provider = layer_icon_provider  # callable(layer_id) -> QIcon
        self._in_undo = False
        self._snapshot = ""
        self._filter_text = ""  # stored so we can reapply after rebuilds

        # Undo stack
        self.undo_stack = QUndoStack(self)

        # Register as Model listener
        self._model.add_listener(self._on_model_event)

        # Connect View signals
        self._connect_view_signals()

    # ==================================================================
    # View signal connections
    # ==================================================================
    def _connect_view_signals(self):
        v = self._view
        v.create_group_requested.connect(self._on_create_group)
        v.rename_group_requested.connect(self._on_rename_group)
        v.delete_group_requested.connect(self._on_delete_groups)
        v.move_to_top_requested.connect(lambda: self._on_move_to_boundary(to_top=True))
        v.move_to_bottom_requested.connect(lambda: self._on_move_to_boundary(to_top=False))
        v.expand_groups_requested.connect(self._on_expand_groups)
        v.collapse_groups_requested.connect(self._on_collapse_groups)
        v.filter_changed.connect(self._on_filter_changed)
        v.control_toggled.connect(self._on_control_toggled)  # re-emitted to Controller
        v.remove_empty_toggled.connect(self._on_remove_empty_toggled)
        v.layer_visibility_toggled.connect(self._on_layer_visibility_toggled)
        v.group_visibility_toggled.connect(self._on_group_visibility_toggled)
        v.item_double_clicked.connect(self._on_item_double_clicked)
        v.drop_intent.connect(self._on_drop_intent)

    # ==================================================================
    # Model event dispatcher → View updates
    # ==================================================================
    def _on_model_event(self, event_type: str, payload: dict):
        _vlog_method("_on_model_event")
        """Route Model events to View updates."""
        if event_type == EVENT_MODEL_LOADED:
            self._rebuild_view_from_model()
            self.model_loaded.emit()
        elif event_type == EVENT_LAYER_ADDED:
            icon = self._layer_icon_provider(payload["layer_id"]) if self._layer_icon_provider else None
            self._view.add_layer_item(
                payload["layer_id"], payload["name"], payload.get("visible", True),
                payload.get("parent_id"), payload["index"], icon=icon
            )
        elif event_type == EVENT_LAYER_REMOVED:
            self._view.remove_layer_item(payload["layer_id"])
        elif event_type == EVENT_LAYER_RENAMED:
            self._view.rename_layer_item(payload["layer_id"], payload["new_name"])
        elif event_type == EVENT_GROUP_CREATED:
            self._view.add_group_item(payload["group_id"], payload["name"],
                                      payload.get("parent_id"), payload["index"])
        elif event_type == EVENT_GROUP_DELETED:
            if payload.get("unwrapped_children"):
                self._rebuild_view_from_model()
            else:
                self._view.remove_group_item(payload["group_id"])
        elif event_type == EVENT_GROUP_RENAMED:
            self._view.rename_group_item(payload["group_id"], payload["new_name"])
        elif event_type == EVENT_ITEM_MOVED:
            self._rebuild_view_from_model()
        elif event_type == EVENT_VISIBILITY_CHANGED:
            self._view.set_visibility(payload["layer_id"], payload["visible"])
        elif event_type == EVENT_EXPANDED_CHANGED:
            self._view.set_expanded(payload["group_id"], payload["expanded"])
        elif event_type == EVENT_ORDER_CHANGED:
            self.order_changed.emit()

    def _rebuild_view_from_model(self):
        _vlog_method("_rebuild_view_from_model")
        """Full View rebuild from the Model's current state.

        Also reapplies the current filter text so filtered-out items stay
        hidden after a rebuild (e.g. after a drag-drop).
        """
        nodes = self._serialize_model_to_nodes()
        self._view.rebuild_from_nodes(nodes, self._layer_icon_provider)
        # Reapply the current filter so items hidden by the filter stay hidden
        if self._filter_text:
            self._view.apply_filter(self._filter_text)

    def _serialize_model_to_nodes(self):
        """Convert the Model's tree to a list of node dicts for the View."""
        def ser_node(node):
            if isinstance(node, GroupNode):
                return {
                    "type": TYPE_GROUP,
                    "id": node.id,
                    "name": node.name,
                    "expanded": node.expanded,
                    "children": [ser_node(ch) for ch in node.children],
                }
            return {
                "type": TYPE_LAYER,
                "id": node.id,
                "name": node.name,
                "visible": node.visible,
            }
        return [ser_node(n) for n in self._model.get_root()]

    # ==================================================================
    # View signal handlers → Model mutations
    # ==================================================================
    def _on_create_group(self):
        _vlog_method("_on_create_group")
        if self._in_undo:
            return
        try:
            from qgis.PyQt.QtWidgets import QInputDialog
            default_name = self._view.get_unique_group_name("New group")
            name, ok = QInputDialog.getText(self._view, "New group", "Group name:", text=default_name)
            if not ok:
                return
            name = (name or "").strip() or default_name
            if name in self._view.get_all_group_names():
                name = self._view.get_unique_group_name(name)

            before = self._model.serialize()
            parent_id, index = self._view.resolve_anchor_for_insertion()
            gid = self._model.create_group(name, parent_id=parent_id, index=index)

            # Move selected items into the new group, preserving order.
            # Insert at incrementing indices so first selected ends up first.
            selected_ids = self._view.get_selected_item_ids()
            insert_index = 0
            for item_id in selected_ids:
                self._model.move_item(item_id, gid, insert_index)
                insert_index += 1

            self._view.expand_and_select_item(gid)
            self._model.set_expanded(gid, True)

            after = self._model.serialize()
            self._push_undo(before, after, "Create group")
            self._autosave()
        except Exception as e:
            _log(f"_on_create_group FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    def _on_rename_group(self):
        _vlog_method("_on_rename_group")
        if self._in_undo:
            return
        try:
            group_ids = self._view.get_selected_group_ids()
            if len(group_ids) != 1:
                return
            gid = group_ids[0]
            node = self._model.find_item(gid)
            if not isinstance(node, GroupNode):
                return
            current = node.name
            from qgis.PyQt.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(self._view, "Rename group", "Group name:", text=current)
            if not ok:
                return
            name = (name or "").strip()
            if not name or name == current:
                return
            others = self._view.get_all_group_names() - {current}
            if name in others:
                name = self._view.get_unique_group_name(name)
            before = self._model.serialize()
            self._model.rename_group(gid, name)
            after = self._model.serialize()
            self._push_undo(before, after, "Rename group")
            self._autosave()
        except Exception as e:
            _log(f"_on_rename_group FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    def _on_delete_groups(self):
        _vlog_method("_on_delete_groups")
        if self._in_undo:
            return
        try:
            group_ids = self._view.get_selected_group_ids()
            if not group_ids:
                return
            before = self._model.serialize()
            # Delete deepest first (sort by depth descending)
            group_ids.sort(key=lambda gid: self._view.get_item_depth(gid), reverse=True)
            removed = 0
            for gid in group_ids:
                self._model.delete_group(gid, unwrap_children=True)
                removed += 1
            if removed == 0:
                return
            after = self._model.serialize()
            self._push_undo(before, after, "Delete group" if removed == 1 else f"Delete {removed} groups")
            self._autosave()
        except Exception as e:
            _log(f"_on_delete_groups FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    def _on_move_to_boundary(self, to_top: bool):
        _vlog_method("_on_move_to_boundary")
        if self._in_undo:
            return
        try:
            item_ids = self._view.get_selected_item_ids()
            if not item_ids:
                return
            before = self._model.serialize()
            self._model.move_items_to_boundary(item_ids, to_top=to_top)
            after = self._model.serialize()
            if before != after:
                self._push_undo(before, after, "Move to top" if to_top else "Move to bottom")
                self._autosave()
        except Exception as e:
            _log(f"_on_move_to_boundary FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    def _on_expand_groups(self, group_ids):
        _vlog_method("_on_expand_groups")
        for gid in group_ids:
            self._model.set_expanded(gid, True)

    def _on_collapse_groups(self, group_ids):
        _vlog_method("_on_collapse_groups")
        for gid in group_ids:
            self._model.set_expanded(gid, False)

    def _on_filter_changed(self, text):
        _vlog_method("_on_filter_changed")
        """Store the filter text so it can be reapplied after rebuilds."""
        self._filter_text = text or ""
        self._view.apply_filter(text)

    def _on_control_toggled(self, checked):
        _vlog_method("_on_control_toggled")
        # Controller handles QGIS hasCustomLayerOrder sync directly.
        # VC doesn't need to do anything here.
        pass

    def _on_remove_empty_toggled(self, checked):
        _vlog_method("_on_remove_empty_toggled")
        self._model.set_remove_empty_groups(bool(checked))

    def _on_layer_visibility_toggled(self, layer_id, checked):
        _vlog_method("_on_layer_visibility_toggled")
        """User toggled a layer checkbox in the View → update Model.

        The Controller also listens to this signal to sync with
        QgsLayerTreeLayer.setItemVisibilityChecked.
        """
        self._model.set_visibility(layer_id, checked)

    def _on_group_visibility_toggled(self, group_id, checked):
        _vlog_method("_on_group_visibility_toggled")
        """User toggled a group checkbox → propagate to all descendant layers.

        Walks the Model tree under `group_id` and sets visibility on every
        LayerNode. The Model emits VISIBILITY_CHANGED for each, which the
        ViewController routes to the View (updating checkboxes) and the
        Controller routes to QGIS (setItemVisibilityChecked + map refresh).
        """
        node = self._model.find_item(group_id)
        if not isinstance(node, GroupNode):
            return
        # Collect all descendant layer ids
        layer_ids = []
        def walk(n):
            if isinstance(n, LayerNode):
                layer_ids.append(n.id)
            elif isinstance(n, GroupNode):
                for ch in n.children:
                    walk(ch)
        walk(node)
        for lid in layer_ids:
            self._model.set_visibility(lid, checked)

    def _on_item_double_clicked(self, group_id):
        _vlog_method("_on_item_double_clicked")
        """User double-clicked a group → toggle expanded state in Model."""
        node = self._model.find_item(group_id)
        if isinstance(node, GroupNode):
            self._model.set_expanded(group_id, not node.expanded)

    def _on_drop_intent(self, moving_ids, target_id, position):
        _vlog_method("_on_drop_intent")
        """Translate semantic drop intent to Model mutations.

        DEFERRED to the next event loop iteration via QTimer.singleShot(0).
        This is critical: if we mutate the Model synchronously inside
        dropEvent, each move_item triggers ITEM_MOVED → _rebuild_view_from_model
        which clears+rebuilds the QTreeWidget WHILE Qt's DnD state machine is
        still active. That corrupts the tree (lost items, broken structure).
        Deferring lets dropEvent return cleanly first.
        """
        if self._in_undo:
            return
        # Capture the ids — the QTreeWidgetItems they came from may be gone
        # by the time the deferred handler runs.
        ids = list(moving_ids)
        tid = target_id
        pos = position
        QTimer.singleShot(0, lambda: self._handle_drop_intent(ids, tid, pos))

    def _handle_drop_intent(self, moving_ids, target_id, position):
        _vlog_method("_handle_drop_intent")
        """Actual drop handling — runs on next event loop iteration."""
        if self._in_undo:
            return
        try:
            before = self._model.serialize()
            target = self._model.find_item(target_id)
            if target is None:
                return

            if position == DROP_ON and isinstance(target, LayerNode):
                # Drop ON a layer → create new group with target + movers
                parent = self._model.find_parent(target_id)
                parent_id = parent.id if parent is not None else None
                target_index = self._model.get_index_in_parent(target_id)
                gid = self._model.create_group("New group", parent_id=parent_id,
                                               index=target_index)
                # Move target into the new group first (index 0)
                self._model.move_item(target_id, gid, 0)
                # Then move each dropped item after the target, incrementing
                # the index so order is preserved: [target, mover1, mover2, ...]
                insert_index = 1
                for item_id in moving_ids:
                    self._model.move_item(item_id, gid, insert_index)
                    insert_index += 1
                self._model.set_expanded(gid, True)
                self._view.expand_and_select_item(gid)
            elif position == DROP_ON and isinstance(target, GroupNode):
                # Drop ON a group → move items into top of group, preserving order.
                # Insert at index 0, 1, 2, ... so first mover ends up on top.
                insert_index = 0
                for item_id in moving_ids:
                    self._model.move_item(item_id, target_id, insert_index)
                    insert_index += 1
            elif position == DROP_ABOVE:
                # Drop ABOVE → move to target's parent at target's index,
                # incrementing so order is preserved.
                parent = self._model.find_parent(target_id)
                parent_id = parent.id if parent is not None else None
                target_index = self._model.get_index_in_parent(target_id)
                insert_index = target_index
                for item_id in moving_ids:
                    self._model.move_item(item_id, parent_id, insert_index)
                    insert_index += 1
            elif position == DROP_BELOW:
                # Drop BELOW → move to target's parent at target's index + 1,
                # incrementing so order is preserved.
                parent = self._model.find_parent(target_id)
                parent_id = parent.id if parent is not None else None
                target_index = self._model.get_index_in_parent(target_id)
                insert_index = target_index + 1
                for item_id in moving_ids:
                    self._model.move_item(item_id, parent_id, insert_index)
                    insert_index += 1

            # Re-select the moved items (rebuild after ITEM_MOVED loses selection)
            self._view.select_items(moving_ids)

            after = self._model.serialize()
            if before != after:
                self._push_undo(before, after, "Reorder layers")
                self._autosave()
        except Exception as e:
            _log(f"_on_drop_intent FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    # ==================================================================
    # Undo
    # ==================================================================
    def _push_undo(self, before: str, after: str, text: str):
        _vlog_method("_push_undo")
        if self._in_undo:
            return
        if (before or "") == (after or ""):
            self._snapshot = after or ""
            return
        self.undo_stack.push(TreeStateCommand(self, before, after, text))
        self._snapshot = after or ""

    def _apply_tree_state_from_undo(self, raw_json: str):
        _vlog_method("_apply_tree_state_from_undo")
        """Called by TreeStateCommand.undo/redo."""
        self._in_undo = True
        try:
            with self._model.block_notifications():
                self._model.load_from_json(raw_json)
            self._rebuild_view_from_model()
            self._snapshot = self._model.serialize()
        finally:
            self._in_undo = False

    # ==================================================================
    # Public API for plugin.py
    # ==================================================================
    def load_from_json(self, raw_json: str):
        _vlog_method("load_from_json")
        """Load a serialized tree into the Model (triggers View rebuild)."""
        with self._model.block_notifications():
            self._model.load_from_json(raw_json)
        self._rebuild_view_from_model()
        self._snapshot = self._model.serialize()

    def serialize(self) -> str:
        """Return the current Model state as JSON."""
        return self._model.serialize()

    def clear(self):
        with self._model.block_notifications():
            self._model.clear()
        self._rebuild_view_from_model()
        self._snapshot = ""

    def add_layers(self, layers):
        _vlog_method("add_layers")
        """Add layers to the Model. layers: list of (layer_id, name) tuples."""
        if self._in_undo:
            return
        try:
            before = self._model.serialize()
            existing = set(self._model.iter_layer_ids())
            new_layers = [(lid, name) for lid, name in layers if lid not in existing]
            if not new_layers:
                return
            parent_id, index = self._view.resolve_anchor_for_insertion()
            for lid, name in new_layers:
                self._model.add_layer(lid, name, parent_id=parent_id, index=index)
                if index is not None:
                    index += 1
            after = self._model.serialize()
            self._push_undo(before, after, "Add layers")
            self._autosave()
        except Exception as e:
            _log(f"add_layers FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    def remove_layers(self, layer_ids):
        _vlog_method("remove_layers")
        if self._in_undo:
            return
        try:
            before = self._model.serialize()
            for lid in layer_ids:
                self._model.remove_layer(lid)
            after = self._model.serialize()
            self._push_undo(before, after, "Remove layers")
            self._autosave()
        except Exception as e:
            _log(f"remove_layers FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    def rename_layer(self, layer_id, new_name):
        _vlog_method("rename_layer")
        """Called when a layer is renamed in the Layers panel."""
        if self._in_undo:
            return
        self._model.rename_layer(layer_id, new_name)

    def set_layer_visibility(self, layer_id, visible):
        _vlog_method("set_layer_visibility")
        """Called when visibility changes in the Layers panel."""
        if self._in_undo:
            return
        self._model.set_visibility(layer_id, visible)

    def get_flattened_layer_ids(self):
        _vlog_method("get_flattened_layer_ids")
        return self._model.get_flattened_layer_ids()

    def _autosave(self):
        """Emit save_requested with the current serialized tree.

        plugin.py connects this signal to _save_tree_json (which writes
        QgsProject entry + setDirty). Without this, tree changes during
        a session would only be saved on plugin unload — risking data loss
        on crash.
        """
        if self._in_undo:
            return
        try:
            self.save_requested.emit(self._model.serialize())
        except Exception:
            pass
