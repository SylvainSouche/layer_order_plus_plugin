"""Advanced Layer Order — ViewController: View intents → Model, Model events → View.

The ViewController is the only object that talks to both the View and the
Model, and it does exactly two things:

1. **Acts on intents.** Each intent from the View becomes either
   * one undoable Model transaction (create / rename / delete group, move,
     drop) — see ``_user_edit``; or
   * a plain Model update for UI-only document state (expanded) and
     document settings (remove empty groups); or
   * a *request* for state that QGIS owns (layer visibility, control of the
     rendering order). Those are re-emitted as ``visibility_requested`` /
     ``control_requested`` for the Controller; the result comes back
     through the Model like any other change.
2. **Renders.** Every Model event is turned into View ``render*`` calls.

It never touches QGIS and never reads state back from the View: intents
carry their data.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QIcon

from .compat import QUndoStack
from .i18n import (
    ADD_GROUP,
    GROUP_NAME,
    MOVE_DOWN,
    MOVE_ITEMS,
    MOVE_TO_BOTTOM,
    MOVE_TO_TOP,
    MOVE_UP,
    NEW_GROUP,
    REMOVE_GROUP,
    RENAME_GROUP,
    tr,
)
from .model import (
    EVENT_EXPANDED_CHANGED,
    EVENT_GROUP_RENAMED,
    EVENT_LAYER_RENAMED,
    EVENT_MODEL_LOADED,
    EVENT_ORDER_CHANGED,
    EVENT_SETTING_CHANGED,
    EVENT_VISIBILITY_CHANGED,
    SETTING_CONTROL_ENABLED,
    SETTING_REMOVE_EMPTY_GROUPS,
    TYPE_GROUP,
    TYPE_LAYER,
    GroupNode,
    LayerNode,
    LayerOrderModel,
)
from .tree_model import DROP_ABOVE, DROP_BELOW, DROP_END, DROP_ON
from .undo import TreeStateCommand
from .view import LayerOrderView

log = logging.getLogger("AdvancedLayerOrder.view_controller")


class ViewController(QObject):
    """Mediator between LayerOrderView and LayerOrderModel. Owns the undo stack."""

    # Requests for QGIS-owned state — plugin.py wires them to the Controller
    visibility_requested = pyqtSignal(list, bool)   # layer ids, visible
    control_requested = pyqtSignal(bool)            # take over the rendering order

    def __init__(self, model: LayerOrderModel, view: LayerOrderView,
                 layer_icon: Callable[[str], QIcon] | None = None, parent=None):
        super().__init__(parent)
        self._model = model
        self._view = view
        self._layer_icon = layer_icon or (lambda _lid: None)
        self.undo_stack = QUndoStack(self)

        model.add_listener(self._on_model_event)
        v = view
        v.create_group_requested.connect(self._on_create_group)
        v.rename_group_requested.connect(self._on_rename_group)
        v.delete_groups_requested.connect(self._on_delete_groups)
        v.move_to_boundary_requested.connect(self._on_move_to_boundary)
        v.move_by_one_requested.connect(self._on_move_by_one)
        v.expand_requested.connect(self._on_expand)
        v.check_requested.connect(self._on_check)
        v.drop_requested.connect(self._on_drop)
        v.undo_requested.connect(self._on_undo_requested)   # a method, not a lambda: see LayerOrderView
        stack = self.undo_stack
        for sig in (stack.canUndoChanged, stack.canRedoChanged, stack.undoTextChanged, stack.redoTextChanged):
            sig.connect(self._render_undo_state)
        v.control_toggled.connect(self.control_requested)
        v.remove_empty_toggled.connect(self._model.set_remove_empty_groups)
        self._render_all()

    def record_step(self, before: str, after: str, text: str) -> None:
        """Add an already-applied change made outside the View (QGIS's Layer
        Order panel) to the undo history, so the layer order has one linear
        history whichever panel changed it."""
        if before != after:
            self.undo_stack.push(TreeStateCommand(self._model, before, after, text))

    def reset_history(self) -> None:
        """Forget undo history (new project)."""
        self.undo_stack.clear()

    # ==================================================================
    # Model → View
    # ==================================================================
    def _on_model_event(self, event_type: str, payload: dict) -> None:
        v = self._view
        if event_type == EVENT_MODEL_LOADED:
            self._render_all()
        elif event_type == EVENT_ORDER_CHANGED:
            # Every structural change ends with ORDER_CHANGED: render once, here
            # (unless it trails a model_loaded that was just rendered)
            if not payload.get("resync"):
                self._render_tree()
        elif event_type == EVENT_LAYER_RENAMED:
            v.render_name(payload["layer_id"], payload["new_name"])
        elif event_type == EVENT_GROUP_RENAMED:
            v.render_name(payload["group_id"], payload["new_name"])
        elif event_type == EVENT_VISIBILITY_CHANGED:
            v.render_visibility(payload["layer_id"], payload["visible"])
        elif event_type == EVENT_EXPANDED_CHANGED:
            v.render_expanded(payload["group_id"], payload["expanded"])
        elif event_type == EVENT_SETTING_CHANGED:
            self._render_setting(payload["key"], payload["value"])

    def _render_undo_state(self, *_args) -> None:
        s = self.undo_stack
        self._view.render_undo_state(s.canUndo(), s.undoText(), s.canRedo(), s.redoText())

    def _render_all(self) -> None:
        self._render_tree()
        self._render_undo_state()
        for key in (SETTING_CONTROL_ENABLED, SETTING_REMOVE_EMPTY_GROUPS):
            self._render_setting(key, self._model.get_setting(key))

    def _render_setting(self, key: str, value) -> None:
        if key == SETTING_CONTROL_ENABLED:
            self._view.render_control_enabled(value)
        elif key == SETTING_REMOVE_EMPTY_GROUPS:
            self._view.render_remove_empty(value)

    def _render_tree(self) -> None:
        def as_dict(node):
            if isinstance(node, GroupNode):
                return {"type": TYPE_GROUP, "id": node.id, "name": node.name,
                        "expanded": node.expanded,
                        "children": [as_dict(ch) for ch in node.children]}
            return {"type": TYPE_LAYER, "id": node.id, "name": node.name,
                    "visible": node.visible, "icon": self._layer_icon(node.id)}
        self._view.render([as_dict(n) for n in self._model.get_root()])

    # ==================================================================
    # Undoable transactions
    # ==================================================================
    @contextmanager
    def _user_edit(self, text: str):
        """Run one user action as a single Model transaction + undo step.

        Inside, Model events are batched (one resync on exit renders the
        View and lets the Controller apply the new order once).
        """
        before = self._model.serialize()
        with self._model.block_notifications():
            yield
        after = self._model.serialize()
        if after != before:
            self.undo_stack.push(TreeStateCommand(self._model, before, after, text))

    # ==================================================================
    # Intents → Model
    # ==================================================================
    def _on_create_group(self, selected_ids: list) -> None:
        model = self._model
        selected = self._in_tree_order(selected_ids)
        default = model.unique_group_name(tr(NEW_GROUP))
        name = self._view.ask_text(tr(NEW_GROUP), f"{tr(GROUP_NAME)}:", default)
        if name is None:
            return
        if name in model.get_group_names():
            name = model.unique_group_name(name)

        if len(selected) == 1 and isinstance(model.find_item(selected[0]), GroupNode):
            # One group selected → new empty subgroup at its top
            parent_id, index, movers = selected[0], 0, []
        elif selected:
            # Wrap the selection: new group as their sibling, after the last one
            parents = {getattr(model.find_parent(i), "id", None) for i in selected}
            if len(parents) == 1:
                parent_id = parents.pop()
                index = max(model.get_index_in_parent(i) for i in selected) + 1
            else:
                parent_id, index = None, None
            movers = selected
        else:
            parent_id, index, movers = None, None, []

        with self._user_edit(tr(ADD_GROUP)):
            gid = model.create_group(name, parent_id=parent_id, index=index)
            if movers:
                model.move_items(movers, gid)
            model.set_expanded(gid, True)
        self._view.render_selection([gid])

    def _on_rename_group(self, group_id: str) -> None:
        node = self._model.find_item(group_id)
        if not isinstance(node, GroupNode):
            return
        name = self._view.ask_text(tr(RENAME_GROUP), f"{tr(GROUP_NAME)}:", node.name)
        if name is None or name == node.name:
            return
        if name in self._model.get_group_names() - {node.name}:
            name = self._model.unique_group_name(name)
        with self._user_edit(tr(RENAME_GROUP)):
            self._model.rename_group(group_id, name)

    def _on_delete_groups(self, group_ids: list) -> None:
        if not group_ids:
            return
        ordered = sorted(group_ids, key=self._model.get_depth, reverse=True)  # deepest first
        with self._user_edit(tr(REMOVE_GROUP)):
            for gid in ordered:
                self._model.delete_group(gid, unwrap_children=True)

    def _on_move_to_boundary(self, item_ids: list, to_top: bool) -> None:
        if not item_ids:
            return
        with self._user_edit(tr(MOVE_TO_TOP if to_top else MOVE_TO_BOTTOM)):
            self._model.move_items_to_boundary(item_ids, to_top=to_top)
        self._view.render_selection(item_ids)

    def _on_undo_requested(self, undo: bool) -> None:
        if undo:
            self.undo_stack.undo()
        else:
            self.undo_stack.redo()

    def _on_move_by_one(self, item_ids: list, up: bool) -> None:
        if not item_ids:
            return
        with self._user_edit(tr(MOVE_UP if up else MOVE_DOWN)):
            self._model.move_items_by_one(item_ids, up)
        self._view.render_selection(self._in_tree_order(item_ids))

    def _on_expand(self, group_ids: list, expanded: bool) -> None:
        # Expansion is UI state stored in the document; it is not undoable
        for gid in group_ids:
            self._model.set_expanded(gid, expanded)

    def _on_check(self, item_id: str, checked: bool) -> None:
        # Visibility belongs to QGIS: ask for it, the Model will follow
        layer_ids = self._model.descendant_layer_ids(item_id)
        if layer_ids:
            self.visibility_requested.emit(layer_ids, checked)

    def _on_drop(self, moving_ids: list, target_id: str, position: str) -> None:
        # Deferred: the View is still inside QDrag/dropEvent; re-rendering it
        # from there would delete items Qt is still using.
        ids = list(moving_ids)
        QTimer.singleShot(0, lambda: self.handle_drop(ids, target_id, position))

    def handle_drop(self, moving_ids: list, target_id: str, position: str) -> None:
        """Apply a drop intent. Every case is one atomic Model.move_items()
        anchored on a sibling id, so multi-item drops keep their order."""
        model = self._model
        movers = self._in_tree_order(moving_ids)
        if not movers:
            return
        log.debug("drop: moving=%s target=%s pos=%s", movers, target_id, position)

        if position == DROP_END:
            with self._user_edit(tr(MOVE_ITEMS)):
                model.move_items(movers, None, None)
            self._view.render_selection(movers)
            return

        target = model.find_item(target_id)
        if target is None or target_id in movers:
            log.debug("drop: invalid target %s", target_id)
            return
        parent = model.find_parent(target_id)
        parent_id = parent.id if parent is not None else None

        with self._user_edit(tr(MOVE_ITEMS)):
            if position == DROP_ON and isinstance(target, LayerNode):
                # Onto a layer → new group at the target's slot: [target, movers...]
                gid = model.create_group(model.unique_group_name(tr(NEW_GROUP)), parent_id=parent_id,
                                         index=model.get_index_in_parent(target_id))
                if model.move_items([target_id, *movers], gid):
                    model.set_expanded(gid, True)
                else:
                    model.delete_group(gid, unwrap_children=False)
            elif position == DROP_ON:
                model.move_items(movers, target_id, None)        # end of the group
            elif position == DROP_ABOVE:
                model.move_items(movers, parent_id, target_id)
            elif position == DROP_BELOW:
                model.move_items(movers, parent_id, self._next_sibling_id(target_id, set(movers)))
        self._view.render_selection(movers)

    # ==================================================================
    # Helpers
    # ==================================================================
    def _in_tree_order(self, item_ids) -> list:
        """Known ids from `item_ids`, de-duplicated, in display order."""
        order = self._model.tree_order_key()
        return sorted((i for i in dict.fromkeys(item_ids) if i in order), key=order.get)

    def _next_sibling_id(self, item_id: str, exclude: set) -> str | None:
        """First sibling after `item_id` not in `exclude`, or None (= end)."""
        parent = self._model.find_parent(item_id)
        siblings = parent.children if parent is not None else self._model.get_root()
        idx = self._model.get_index_in_parent(item_id)
        return next((s.id for s in siblings[idx + 1:] if s.id not in exclude), None)
