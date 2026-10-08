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
from contextlib import contextmanager

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
from .tree_widget import DROP_ON, DROP_ABOVE, DROP_BELOW, DROP_END
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
        v.expanded_changed.connect(self._on_expanded_changed)
        v.drop_intent.connect(self._on_drop_intent)

    # ==================================================================
    # Model event dispatcher → View updates
    # ==================================================================
    def _on_model_event(self, event_type: str, payload: dict):
        _vlog_method("_on_model_event", "[VC]")
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
        _vlog_method("_rebuild_view_from_model", "[VC]")
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

    @contextmanager
    def _user_edit(self, text: str):
        """Run a user action as ONE Model transaction.

        Model events are suppressed for the duration (the trailing coalesced
        ORDER_CHANGED still reaches the Controller), then the View is rebuilt
        once from the Model, and a single undo entry is pushed. This keeps
        the View a pure projection of the Model: no incremental View patching
        in the middle of a multi-step mutation.
        """
        before = self._model.serialize()
        try:
            with self._model.block_notifications():
                yield
        finally:
            after = self._model.serialize()
            if after != before:
                self._rebuild_view_from_model()
                self._push_undo(before, after, text)
                self._autosave()

    # ==================================================================
    # View signal handlers → Model mutations
    # ==================================================================
    def _on_create_group(self):
        _vlog_method("_on_create_group", "[VC]")
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

            selected_ids = self._in_tree_order(self._view.get_selected_item_ids())

            # Determine where to create the new group.
            # If multiple items are selected, the new group should be a SIBLING
            # of the selected items (at their common parent level), NOT a child
            # of one of them. Insert after the last selected item.
            if len(selected_ids) >= 2:
                # Find the common parent + insertion index
                parent_id, index = self._resolve_common_parent_for_creation(selected_ids)
            else:
                parent_id, index = self._view.resolve_anchor_for_insertion()

            with self._user_edit("Create group"):
                gid = self._model.create_group(name, parent_id=parent_id, index=index)
                # Move selected items into the new group, preserving display order
                if selected_ids:
                    self._model.move_items(selected_ids, gid)
                self._model.set_expanded(gid, True)
            self._view.expand_and_select_item(gid)
        except Exception as e:
            _log(f"_on_create_group FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    def _resolve_common_parent_for_creation(self, selected_ids):
        """Find the common parent + insertion index for a new group.

        When creating a group from multiple selected items, the new group
        should be a sibling of the selected items (at their common parent
        level), inserted after the last selected item.

        Returns (parent_id, index) where parent_id is the common parent
        (or None for top-level) and index is the insertion position.
        """
        _vlog_method("_resolve_common_parent_for_creation", "[VC]")
        # Find the parent of each selected item
        parents = set()
        max_index = -1
        for item_id in selected_ids:
            parent = self._model.find_parent(item_id)
            parent_id = parent.id if parent is not None else None
            parents.add(parent_id)
            idx = self._model.get_index_in_parent(item_id)
            if idx is not None and idx > max_index:
                max_index = idx

        # If all items share the same parent, create the new group there
        if len(parents) == 1:
            common_parent_id = parents.pop()
            # Insert after the last selected item
            return (common_parent_id, max_index + 1)
        else:
            # Items have different parents — fall back to top-level
            return (None, None)

    def _on_rename_group(self):
        _vlog_method("_on_rename_group", "[VC]")
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
            with self._user_edit("Rename group"):
                self._model.rename_group(gid, name)
        except Exception as e:
            _log(f"_on_rename_group FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    def _on_delete_groups(self):
        _vlog_method("_on_delete_groups", "[VC]")
        if self._in_undo:
            return
        try:
            group_ids = self._view.get_selected_group_ids()
            if not group_ids:
                return
            # Delete deepest first (sort by depth descending)
            group_ids.sort(key=lambda gid: self._view.get_item_depth(gid), reverse=True)
            n = len(group_ids)
            with self._user_edit("Delete group" if n == 1 else f"Delete {n} groups"):
                for gid in group_ids:
                    self._model.delete_group(gid, unwrap_children=True)
        except Exception as e:
            _log(f"_on_delete_groups FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    def _on_move_to_boundary(self, to_top: bool):
        _vlog_method("_on_move_to_boundary", "[VC]")
        if self._in_undo:
            return
        try:
            item_ids = self._view.get_selected_item_ids()
            if not item_ids:
                return
            with self._user_edit("Move to top" if to_top else "Move to bottom"):
                self._model.move_items_to_boundary(item_ids, to_top=to_top)
            self._view.select_items(item_ids)
        except Exception as e:
            _log(f"_on_move_to_boundary FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    def _on_expand_groups(self, group_ids):
        _vlog_method("_on_expand_groups", "[VC]")
        for gid in group_ids:
            self._model.set_expanded(gid, True)

    def _on_collapse_groups(self, group_ids):
        _vlog_method("_on_collapse_groups", "[VC]")
        for gid in group_ids:
            self._model.set_expanded(gid, False)

    def _on_filter_changed(self, text):
        _vlog_method("_on_filter_changed", "[VC]")
        """Store the filter text so it can be reapplied after rebuilds."""
        self._filter_text = text or ""
        self._view.apply_filter(text)

    def _on_control_toggled(self, checked):
        _vlog_method("_on_control_toggled", "[VC]")
        # Controller handles QGIS hasCustomLayerOrder sync directly.
        # VC doesn't need to do anything here.
        pass

    def _on_remove_empty_toggled(self, checked):
        _vlog_method("_on_remove_empty_toggled", "[VC]")
        self._model.set_remove_empty_groups(bool(checked))

    def _on_layer_visibility_toggled(self, layer_id, checked):
        _vlog_method("_on_layer_visibility_toggled", "[VC]")
        """User toggled a layer checkbox in the View → update Model.

        The Controller also listens to this signal to sync with
        QgsLayerTreeLayer.setItemVisibilityChecked.
        """
        self._model.set_visibility(layer_id, checked)

    def _on_group_visibility_toggled(self, group_id, checked):
        _vlog_method("_on_group_visibility_toggled", "[VC]")
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

    def _on_expanded_changed(self, group_id, expanded):
        _vlog_method("_on_expanded_changed", "[VC]")
        """User expanded/collapsed a group in the View → record it in the Model.

        Without this the Model keeps a stale `expanded` flag and every
        rebuild (i.e. every drop) re-opens or re-closes groups.
        """
        self._model.set_expanded(group_id, expanded)

    def _on_drop_intent(self, moving_ids, target_id, position):
        _vlog_method("_on_drop_intent", "[VC]")
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
        _vlog_method("_handle_drop_intent", "[VC]")
        """Actual drop handling — runs on next event loop iteration.

        Every case is a single atomic Model.move_items() call anchored on a
        sibling id (not an index), so multi-item drops can't be thrown off
        by indices shifting while the movers are taken out.
        """
        if self._in_undo:
            return
        try:
            movers = self._in_tree_order(moving_ids)
            if not movers:
                return
            _log(f"[VC] _handle_drop_intent: moving={movers} target={target_id} pos={position}")

            if position == DROP_END:
                with self._user_edit("Reorder layers"):
                    self._model.move_items(movers, None, None)
                self._view.select_items(movers)
                return

            target = self._model.find_item(target_id)
            if target is None or target_id in movers:
                _log(f"[VC] _handle_drop_intent: invalid target {target_id}")
                return
            parent = self._model.find_parent(target_id)
            parent_id = parent.id if parent is not None else None

            with self._user_edit("Reorder layers"):
                if position == DROP_ON and isinstance(target, LayerNode):
                    # Drop ON a layer → new group at the target's slot holding
                    # [target, movers...]
                    gid = self._model.create_group(
                        self._view.get_unique_group_name("New group"),
                        parent_id=parent_id,
                        index=self._model.get_index_in_parent(target_id))
                    if self._model.move_items([target_id] + movers, gid):
                        self._model.set_expanded(gid, True)
                    else:
                        self._model.delete_group(gid, unwrap_children=False)
                elif position == DROP_ON:
                    # Drop ON a group → append to the end of the group
                    self._model.move_items(movers, target_id, None)
                elif position == DROP_ABOVE:
                    self._model.move_items(movers, parent_id, target_id)
                elif position == DROP_BELOW:
                    self._model.move_items(movers, parent_id,
                                           self._next_sibling_id(target_id, set(movers)))

            self._view.select_items(movers)
        except Exception as e:
            _log(f"[VC] _handle_drop_intent FAILED: {e!r}", Qgis.Critical)
            _log(traceback.format_exc(), Qgis.Critical)

    def _in_tree_order(self, item_ids):
        """Return the known ids from `item_ids` sorted in display (pre-order) order."""
        order = self._model.tree_order_key()
        return sorted((i for i in dict.fromkeys(item_ids) if i in order), key=order.get)

    def _next_sibling_id(self, item_id, exclude):
        """Id of the first sibling after `item_id` not in `exclude`, or None (= end)."""
        parent = self._model.find_parent(item_id)
        siblings = parent.children if parent is not None else self._model.get_root()
        idx = self._model.get_index_in_parent(item_id)
        for sib in siblings[idx + 1:]:
            if sib.id not in exclude:
                return sib.id
        return None

    # ==================================================================
    # Undo
    # ==================================================================
    def _push_undo(self, before: str, after: str, text: str):
        _vlog_method("_push_undo", "[VC]")
        if self._in_undo:
            return
        if (before or "") == (after or ""):
            self._snapshot = after or ""
            return
        self.undo_stack.push(TreeStateCommand(self, before, after, text))
        self._snapshot = after or ""

    def _apply_tree_state_from_undo(self, raw_json: str):
        _vlog_method("_apply_tree_state_from_undo", "[VC]")
        """Called by TreeStateCommand.undo/redo.

        Loads the saved JSON into the Model, rebuilds the View, and
        emits order_changed so the Controller re-applies to QGIS.
        The _in_undo guard prevents _push_undo from recording the
        undo's own changes as a new undo entry.
        """
        self._in_undo = True
        try:
            with self._model.block_notifications():
                self._model.load_from_json(raw_json)
            self._rebuild_view_from_model()
            self._snapshot = self._model.serialize()
            # Emit order_changed so the Controller applies to QGIS.
            # (block_notifications coalesced the ORDER_CHANGED; we emit
            # it manually here since we're past the block.)
            self.order_changed.emit()
            # Also trigger autosave so the restored state is persisted
            self._autosave()
        finally:
            self._in_undo = False

    # ==================================================================
    # Public API for plugin.py
    # ==================================================================
    def load_from_json(self, raw_json: str):
        _vlog_method("load_from_json", "[VC]")
        """Load a serialized tree into the Model (triggers View rebuild)."""
        with self._model.block_notifications():
            self._model.load_from_json(raw_json)
        self._rebuild_view_from_model()
        self._snapshot = self._model.serialize()

    def replace_root_from_external(self, new_root):
        _vlog_method("replace_root_from_external", "[VC]")
        """Swap in a tree computed outside Plus (reconcile with the stock
        Layer Order panel). Not an undoable user action, but the View, the
        snapshot and the saved project entry must all follow the Model."""
        with self._model.block_notifications():
            self._model._root = new_root
        self._rebuild_view_from_model()
        self._snapshot = self._model.serialize()
        self._autosave()

    def serialize(self) -> str:
        """Return the current Model state as JSON."""
        return self._model.serialize()

    def clear(self):
        with self._model.block_notifications():
            self._model.clear()
        self._rebuild_view_from_model()
        self._snapshot = ""

    def add_layers(self, layers):
        _vlog_method("add_layers", "[VC]")
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
        _vlog_method("remove_layers", "[VC]")
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
        _vlog_method("rename_layer", "[VC]")
        """Called when a layer is renamed in the Layers panel."""
        if self._in_undo:
            return
        self._model.rename_layer(layer_id, new_name)

    def set_layer_visibility(self, layer_id, visible):
        _vlog_method("set_layer_visibility", "[VC]")
        """Called when visibility changes in the Layers panel."""
        if self._in_undo:
            return
        self._model.set_visibility(layer_id, visible)

    def get_flattened_layer_ids(self):
        _vlog_method("get_flattened_layer_ids", "[VC]")
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
