"""Layer Order Plus — Model (plain Python, Qt-free).

The Model is the single source of truth for the order tree: groups, layers,
their nesting, expanded state, and per-layer visibility. It owns NO Qt code
and NO QGIS code — every external side-effect (writing customLayerOrder,
updating QTreeWidget, persisting to project) is the Controller's job in
response to Model events.

Notifications (observer pattern):
    Model emits coarse event tuples to registered listeners:
        (event_type: str, payload: dict)
    Use the `block_notifications()` context manager to suppress emits during
    bulk operations (project load, undo/redo replay). Other listeners see
    nothing during the block; the caller is responsible for emitting a
    `model_loaded` event after the block if a bulk replace happened.

This is the 1.2.0 extraction: Model exists alongside the existing dock.py,
which becomes a thin View+Controller+ViewController until 1.3.0/1.4.0 split
the roles into separate modules.
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager

try:
    from .logger import _vlog, _vlog_method
except ImportError:
    def _vlog(msg): pass
    def _vlog_method(name, tag=""): pass
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional, Union


# ====================================================================
# Schema constants
# ====================================================================

TREE_JSON_SCHEMA_VERSION = 1

TYPE_GROUP = "group"
TYPE_LAYER = "layer"


# ====================================================================
# Node types
# ====================================================================

@dataclass
class LayerNode:
    """A leaf node referencing a QGIS layer by id."""
    id: str                    # QGIS layer id
    name: str
    visible: bool = True

    @property
    def is_group(self) -> bool:
        return False


@dataclass
class GroupNode:
    """An order-group. Children are LayerNode | GroupNode in draw order."""
    id: str                    # "grp_" + 10 hex chars
    name: str
    expanded: bool = True
    children: list = field(default_factory=list)

    @property
    def is_group(self) -> bool:
        return True


Node = Union[GroupNode, LayerNode]


def new_group_id() -> str:
    """Generate a unique group id (stable format, easy to recognise in JSON)."""
    return "grp_" + uuid.uuid4().hex[:10]


# ====================================================================
# Event types
# ====================================================================

# Bulk replace — entire tree was swapped (project load, undo replay)
EVENT_MODEL_LOADED = "model_loaded"

# Per-node structural changes
EVENT_LAYER_ADDED = "layer_added"
EVENT_LAYER_REMOVED = "layer_removed"
EVENT_LAYER_RENAMED = "layer_renamed"
EVENT_GROUP_CREATED = "group_created"
EVENT_GROUP_DELETED = "group_deleted"
EVENT_GROUP_RENAMED = "group_renamed"
EVENT_ITEM_MOVED = "item_moved"

# Per-node state changes (no structural effect)
EVENT_VISIBILITY_CHANGED = "visibility_changed"
EVENT_EXPANDED_CHANGED = "expanded_changed"

# Coarse trailing signal — fired after any structural change so listeners
# that only care about the flattened order (Controller applying to QGIS)
# can do a single re-flatten instead of reacting to every individual event.
EVENT_ORDER_CHANGED = "order_changed"


# ====================================================================
# Model
# ====================================================================

class LayerOrderModel:
    """Single source of truth for the order tree.

    All mutations go through methods on this class. Listeners are notified
    after each mutation. Use `block_notifications()` to suppress emits
    during bulk operations.
    """

    def __init__(self) -> None:
        self._root: list[Node] = []
        self._listeners: list[Callable[[str, dict], None]] = []
        self._notification_blocked: bool = False
        # Pending events collected during a block (so we can coalesce
        # into a single model_loaded emit on exit, if a structural change
        # happened). None = no pending structural change.
        self._pending_order_change: bool = False
        # Per-project setting (QualityOverhaul 2.2): whether to auto-prune
        # groups that become empty after a layer is removed.
        self._remove_empty_groups: bool = True

    # ------------------------------------------------------------------
    # Listener registration
    # ------------------------------------------------------------------
    def add_listener(self, cb: Callable[[str, dict], None]) -> None:
        """Register a listener. Called as cb(event_type, payload) on every emit."""
        if cb not in self._listeners:
            self._listeners.append(cb)

    def remove_listener(self, cb: Callable[[str, dict], None]) -> None:
        try:
            self._listeners.remove(cb)
        except ValueError:
            pass  # already removed

    @contextmanager
    def block_notifications(self):
        """Suppress all emits while inside the with-block.

        If a structural change happens during the block, a single
        `order_changed` event is emitted on exit (but no per-node events).
        Listeners that need per-node granularity should not use bulk
        operations under a block.
        """
        prev = self._notification_blocked
        self._notification_blocked = True
        prev_pending = self._pending_order_change
        try:
            yield self
        finally:
            self._notification_blocked = prev
            if self._pending_order_change and not prev:
                # We accumulated a structural change — emit a coarse signal
                # so order-dependent listeners know to re-flatten.
                self._emit(EVENT_ORDER_CHANGED, {})
            self._pending_order_change = prev_pending

    def _emit(self, event_type: str, payload: dict) -> None:
        """Internal: send an event to all listeners (unless blocked)."""
        if self._notification_blocked:
            if event_type in (EVENT_LAYER_ADDED, EVENT_LAYER_REMOVED,
                              EVENT_GROUP_CREATED, EVENT_GROUP_DELETED,
                              EVENT_ITEM_MOVED, EVENT_MODEL_LOADED):
                self._pending_order_change = True
            return
        for cb in list(self._listeners):  # copy in case a listener removes itself
            try:
                cb(event_type, payload)
            except Exception:
                # Listener errors must not break the Model. Production code
                # logs via QgsMessageLog; here we swallow to keep the Model
                # Qt-free and side-effect-free.
                pass

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------
    def get_root(self) -> list[Node]:
        """Return the top-level node list (read-only — caller must not mutate)."""
        return self._root

    def find_item(self, item_id: str) -> Optional[Node]:
        """Find any node by id. Returns None if not found."""
        def walk(node: Node):
            if node.id == item_id:
                return node
            if isinstance(node, GroupNode):
                for ch in node.children:
                    found = walk(ch)
                    if found is not None:
                        return found
            return None
        for top in self._root:
            found = walk(top)
            if found is not None:
                return found
        return None

    def find_parent(self, item_id: str) -> Optional[GroupNode]:
        """Return the parent GroupNode of `item_id`, or None if top-level."""
        def walk(node: Node, parent: Optional[GroupNode]):
            if node.id == item_id:
                return parent
            if isinstance(node, GroupNode):
                for ch in node.children:
                    found = walk(ch, node)
                    if found is not None:
                        return found
            return None
        for top in self._root:
            found = walk(top, None)
            if found is not None:
                return found
        return None

    def get_index_in_parent(self, item_id: str) -> Optional[int]:
        """Return the index of `item_id` within its parent (or top-level)."""
        parent = self.find_parent(item_id)
        siblings = parent.children if parent is not None else self._root
        for i, sib in enumerate(siblings):
            if sib.id == item_id:
                return i
        return None

    def iter_layer_ids(self) -> Iterator[str]:
        """Yield every layer id in the tree, depth-first."""
        def walk(node: Node):
            if isinstance(node, LayerNode):
                yield node.id
            elif isinstance(node, GroupNode):
                for ch in node.children:
                    yield from walk(ch)
        for top in self._root:
            yield from walk(top)

    def get_flattened_layer_ids(self) -> list[str]:
        """Return the depth-first flattened layer id list — what gets written to customLayerOrder."""
        return list(self.iter_layer_ids())

    def get_group_names(self) -> set[str]:
        """Return the set of all group display names (recursive)."""
        names: set[str] = set()
        def walk(node: Node):
            if isinstance(node, GroupNode):
                names.add(node.name)
                for ch in node.children:
                    walk(ch)
        for top in self._root:
            walk(top)
        return names

    def get_remove_empty_groups(self) -> bool:
        return self._remove_empty_groups

    def set_remove_empty_groups(self, value: bool) -> None:
        self._remove_empty_groups = bool(value)
        # Setting change is not a structural event; no emit.

    # ------------------------------------------------------------------
    # Bulk load / clear
    # ------------------------------------------------------------------
    def load_from_json(self, raw_json: str) -> None:
        """Replace the entire tree from JSON.

        Emits EVENT_MODEL_LOADED (and a trailing EVENT_ORDER_CHANGED).
        Schema version handling: missing → v1 legacy; mismatched → still
        load as v1 (the format is forward-compatible so far).

        If JSON parsing fails, the tree is cleared and a model_loaded event
        is emitted (so listeners can rebuild an empty tree). This prevents
        a corrupted project entry from crashing the plugin on load.
        """
        new_root: list[Node] = []
        if raw_json:
            try:
                obj = json.loads(raw_json)
            except (json.JSONDecodeError, TypeError):
                # Corrupted JSON — clear and emit so listeners rebuild empty
                self._root = []
                self._emit(EVENT_MODEL_LOADED, {})
                self._emit(EVENT_ORDER_CHANGED, {})
                return
            for top_node in obj.get("children", []):
                built = self._build_node(top_node)
                if built is not None:
                    new_root.append(built)
        self._root = new_root
        self._emit(EVENT_MODEL_LOADED, {})
        self._emit(EVENT_ORDER_CHANGED, {})

    def _build_node(self, node: dict) -> Optional[Node]:
        t = node.get("type")
        if t == TYPE_GROUP:
            children: list[Node] = []
            for ch in node.get("children", []):
                built = self._build_node(ch)
                if built is not None:
                    children.append(built)
            return GroupNode(
                id=node.get("id", new_group_id()),
                name=node.get("name", "Group"),
                expanded=bool(node.get("expanded", True)),
                children=children,
            )
        if t == TYPE_LAYER:
            return LayerNode(
                id=node["id"],
                name=node.get("name", ""),
                visible=bool(node.get("visible", True)),
            )
        return None

    def clear(self) -> None:
        """Empty the tree. Emits MODEL_LOADED + ORDER_CHANGED."""
        _vlog(f"[M] load_from_json")
        self._root = []
        self._emit(EVENT_MODEL_LOADED, {})
        self._emit(EVENT_ORDER_CHANGED, {})

    # ------------------------------------------------------------------
    # Layer mutations
    # ------------------------------------------------------------------
    def add_layer(self, layer_id: str, name: str, visible: bool = True,
                  parent_id: Optional[str] = None,
                  index: Optional[int] = None) -> None:
        """Add a LayerNode under `parent_id` (or top-level if None) at `index`.

        `index` = None (default) → append to the end.
        Emits LAYER_ADDED + ORDER_CHANGED.
        """
        node = LayerNode(id=layer_id, name=name, visible=visible)
        parent = self.find_item(parent_id) if parent_id else None
        if parent_id is not None and not isinstance(parent, GroupNode):
            # parent doesn't exist or isn't a group → fall back to top-level
            parent = None
        siblings = parent.children if parent is not None else self._root
        if index is None:
            idx = len(siblings)
        else:
            idx = max(0, min(index, len(siblings)))
        siblings.insert(idx, node)
        self._emit(EVENT_LAYER_ADDED, {
            "layer_id": layer_id,
            "name": name,
            "visible": visible,
            "parent_id": parent.id if parent is not None else None,
            "index": idx,
        })
        self._emit(EVENT_ORDER_CHANGED, {})

    def remove_layer(self, layer_id: str) -> None:
        """Remove a LayerNode by id. Emits LAYER_REMOVED + ORDER_CHANGED.

        If get_remove_empty_groups() is True, any group that becomes empty
        as a result is also removed (and GROUP_DELETED events are emitted
        for each, in deepest-first order).
        """
        _vlog(f"[M] add_layer")
        parent = self.find_parent(layer_id)
        siblings = parent.children if parent is not None else self._root
        for i, sib in enumerate(siblings):
            if sib.id == layer_id and isinstance(sib, LayerNode):
                siblings.pop(i)
                self._emit(EVENT_LAYER_REMOVED, {"layer_id": layer_id})
                if self._remove_empty_groups:
                    self._prune_empty_groups_internal()
                self._emit(EVENT_ORDER_CHANGED, {})
                return
        # Layer not found — no-op

    def rename_layer(self, layer_id: str, new_name: str) -> None:
        """Update a layer's display name. Emits LAYER_RENAMED (no ORDER_CHANGED — display only)."""
        _vlog(f"[M] remove_layer")
        node = self.find_item(layer_id)
        if not isinstance(node, LayerNode):
            return
        old_name = node.name
        if old_name == new_name:
            return
        node.name = new_name
        self._emit(EVENT_LAYER_RENAMED, {
            "layer_id": layer_id,
            "old_name": old_name,
            "new_name": new_name,
        })

    def set_visibility(self, layer_id: str, visible: bool) -> None:
        """Toggle a layer's visibility flag. Emits VISIBILITY_CHANGED (no ORDER_CHANGED)."""
        _vlog(f"[M] rename_layer")
        node = self.find_item(layer_id)
        if not isinstance(node, LayerNode):
            return
        visible = bool(visible)
        if node.visible == visible:
            return
        node.visible = visible
        self._emit(EVENT_VISIBILITY_CHANGED, {
            "layer_id": layer_id,
            "visible": visible,
        })

    # ------------------------------------------------------------------
    # Group mutations
    # ------------------------------------------------------------------
    def create_group(self, name: str, parent_id: Optional[str] = None,
                     index: Optional[int] = None) -> str:
        """Create a GroupNode. Returns the new group_id.

        `parent_id` = None → top-level. `index` = None → append.
        Emits GROUP_CREATED + ORDER_CHANGED.
        """
        _vlog(f"[M] set_visibility")
        gid = new_group_id()
        node = GroupNode(id=gid, name=name)
        parent = self.find_item(parent_id) if parent_id else None
        if parent_id is not None and not isinstance(parent, GroupNode):
            parent = None
        siblings = parent.children if parent is not None else self._root
        if index is None:
            idx = len(siblings)
        else:
            idx = max(0, min(index, len(siblings)))
        siblings.insert(idx, node)
        self._emit(EVENT_GROUP_CREATED, {
            "group_id": gid,
            "name": name,
            "parent_id": parent.id if parent is not None else None,
            "index": idx,
        })
        self._emit(EVENT_ORDER_CHANGED, {})
        return gid

    def delete_group(self, group_id: str, unwrap_children: bool = True) -> None:
        """Delete a group. If unwrap_children, its children are promoted in place.

        Emits GROUP_DELETED + ORDER_CHANGED. If unwrap_children is True the
        payload's `unwrapped_children` lists the children that were promoted
        (with their new parent_id + index).
        """
        _vlog(f"[M] create_group")
        node = self.find_item(group_id)
        if not isinstance(node, GroupNode):
            return
        parent = self.find_parent(group_id)
        siblings = parent.children if parent is not None else self._root
        idx = self.get_index_in_parent(group_id)
        if idx is None:
            return
        promoted = []
        if unwrap_children:
            # Take children out, insert them at the group's slot in order
            children = list(node.children)
            for offset, ch in enumerate(children):
                siblings.insert(idx + offset, ch)
                promoted.append({
                    "item_id": ch.id,
                    "new_parent_id": parent.id if parent is not None else None,
                    "new_index": idx + offset,
                })
        siblings.pop(idx + (len(promoted) if unwrap_children else 0))
        self._emit(EVENT_GROUP_DELETED, {
            "group_id": group_id,
            "unwrapped_children": promoted,
        })
        self._emit(EVENT_ORDER_CHANGED, {})

    def rename_group(self, group_id: str, new_name: str) -> None:
        """Update a group's display name. Emits GROUP_RENAMED (no ORDER_CHANGED)."""
        _vlog(f"[M] delete_group")
        node = self.find_item(group_id)
        if not isinstance(node, GroupNode):
            return
        old_name = node.name
        if old_name == new_name:
            return
        node.name = new_name
        self._emit(EVENT_GROUP_RENAMED, {
            "group_id": group_id,
            "old_name": old_name,
            "new_name": new_name,
        })

    def set_expanded(self, group_id: str, expanded: bool) -> None:
        """Toggle a group's expanded flag. Emits EXPANDED_CHANGED (no ORDER_CHANGED)."""
        _vlog(f"[M] rename_group")
        node = self.find_item(group_id)
        if not isinstance(node, GroupNode):
            return
        expanded = bool(expanded)
        if node.expanded == expanded:
            return
        node.expanded = expanded
        self._emit(EVENT_EXPANDED_CHANGED, {
            "group_id": group_id,
            "expanded": expanded,
        })

    # ------------------------------------------------------------------
    # Move operations
    # ------------------------------------------------------------------
    def move_item(self, item_id: str, new_parent_id: Optional[str],
                  new_index: int) -> None:
        """Move a node to `new_parent_id` (None = top-level) at `new_index`.

        Emits ITEM_MOVED + ORDER_CHANGED. Prevents moving a group into its
        own descendant (no-op in that case). No-op if the position is unchanged.

        If remove_empty_groups is True and the old parent group becomes empty
        after the move, it is pruned automatically.
        """
        _vlog(f"[M] set_expanded")
        node = self.find_item(item_id)
        if node is None:
            return
        # Cycle prevention: don't move a group into itself or any descendant
        if isinstance(node, GroupNode) and new_parent_id is not None:
            target_parent = self.find_item(new_parent_id)
            if target_parent is node or self._is_descendant(target_parent, node):
                return
        old_parent = self.find_parent(item_id)
        old_index = self.get_index_in_parent(item_id)
        if old_index is None:
            return
        # Resolve new parent
        new_parent = self.find_item(new_parent_id) if new_parent_id else None
        if new_parent_id is not None and not isinstance(new_parent, GroupNode):
            new_parent = None
        # No-op detection: same parent + same effective index
        if old_parent is new_parent:
            # Adjust for the take that's about to happen
            effective_new_index = new_index
            if old_index < new_index:
                effective_new_index = max(0, new_index - 1)
            if effective_new_index == old_index:
                return
        # Take from old location
        old_siblings = old_parent.children if old_parent is not None else self._root
        old_siblings.pop(old_index)
        # Insert at new location
        new_siblings = new_parent.children if new_parent is not None else self._root
        # Adjust index if same parent and we removed from before the target slot
        if old_parent is new_parent and old_index < new_index:
            new_index = max(0, new_index - 1)
        new_index = max(0, min(new_index, len(new_siblings)))
        new_siblings.insert(new_index, node)
        self._emit(EVENT_ITEM_MOVED, {
            "item_id": item_id,
            "old_parent_id": old_parent.id if old_parent is not None else None,
            "old_index": old_index,
            "new_parent_id": new_parent.id if new_parent is not None else None,
            "new_index": new_index,
        })
        self._emit(EVENT_ORDER_CHANGED, {})
        # Prune empty groups if the setting is on and the old parent is now empty
        if self._remove_empty_groups and old_parent is not None and len(old_parent.children) == 0:
            self._prune_empty_groups_internal()

    def move_items_to_boundary(self, item_ids: list[str], to_top: bool) -> None:
        """Move each item to the top (or bottom) of its respective parent.

        Emits one ITEM_MOVED per item that actually moved, then a single
        ORDER_CHANGED.

        Sort order:
        - to_top=True: sort by index DESCENDING (take from back first →
          earliest selected ends up on top, preserving order)
        - to_top=False: sort by index ASCENDING (take from front first →
          earliest selected ends up at the front of the bottom block,
          preserving order)
        """
        _vlog(f"[M] move_item")
        # Snapshot current positions
        moves: list[tuple[str, Optional[GroupNode], int]] = []
        for item_id in item_ids:
            parent = self.find_parent(item_id)
            idx = self.get_index_in_parent(item_id)
            if idx is not None:
                moves.append((item_id, parent, idx))
        # Sort: descending for to_top, ascending for to_bottom
        if to_top:
            moves.sort(key=lambda m: m[2], reverse=True)
        else:
            moves.sort(key=lambda m: m[2], reverse=False)
        emitted_any = False
        for item_id, parent, _old_idx in moves:
            siblings = parent.children if parent is not None else self._root
            # Find current index (may have shifted from earlier moves in same parent)
            cur_idx = None
            for i, sib in enumerate(siblings):
                if sib.id == item_id:
                    cur_idx = i
                    break
            if cur_idx is None:
                continue
            target_idx = 0 if to_top else len(siblings) - 1
            if cur_idx == target_idx:
                continue  # already at boundary
            node = siblings.pop(cur_idx)
            if to_top:
                siblings.insert(0, node)
                new_idx = 0
            else:
                siblings.append(node)
                new_idx = len(siblings) - 1
            self._emit(EVENT_ITEM_MOVED, {
                "item_id": item_id,
                "old_parent_id": parent.id if parent is not None else None,
                "old_index": cur_idx,
                "new_parent_id": parent.id if parent is not None else None,
                "new_index": new_idx,
            })
            emitted_any = True
        if emitted_any:
            self._emit(EVENT_ORDER_CHANGED, {})

    # ------------------------------------------------------------------
    # Empty-group pruning
    # ------------------------------------------------------------------
    def prune_empty_groups(self) -> int:
        """Walk the tree and remove groups with no children. Returns count removed.

        Emits GROUP_DELETED for each removed group + a single ORDER_CHANGED
        if anything was removed.
        """
        _vlog(f"[M] move_items_to_boundary")
        return self._prune_empty_groups_internal()

    def _prune_empty_groups_internal(self) -> int:
        """Internal: prune without re-emitting ORDER_CHANGED (caller's job)."""
        removed = 0

        def walk(parent_children: list) -> None:
            nonlocal removed
            i = 0
            while i < len(parent_children):
                ch = parent_children[i]
                if isinstance(ch, GroupNode):
                    walk(ch.children)  # recurse first
                    if len(ch.children) == 0:
                        parent_children.pop(i)
                        removed += 1
                        self._emit(EVENT_GROUP_DELETED, {
                            "group_id": ch.id,
                            "unwrapped_children": [],  # was empty
                        })
                        continue
                i += 1

        walk(self._root)
        if removed:
            self._emit(EVENT_ORDER_CHANGED, {})
        return removed

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def serialize(self) -> str:
        """Serialize the tree to JSON with schema version."""
        _vlog(f"[M] prune_empty_groups")
        def ser_node(node: Node) -> dict:
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

        return json.dumps(
            {
                "version": TREE_JSON_SCHEMA_VERSION,
                "children": [ser_node(top) for top in self._root],
            },
            ensure_ascii=False,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _is_descendant(self, candidate: Optional[Node], ancestor: Optional[Node]) -> bool:
        """Return True if `candidate` is `ancestor` itself or any descendant of `ancestor`.

        Walks DOWN from `ancestor` looking for `candidate`.
        """
        if candidate is None or ancestor is None:
            return False
        if candidate is ancestor:
            return True
        if not isinstance(ancestor, GroupNode):
            return False
        for ch in ancestor.children:
            if self._is_descendant(candidate, ch):
                return True
        return False
