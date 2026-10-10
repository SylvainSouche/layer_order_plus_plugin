"""Advanced Layer Order — Model: the single source of truth (plain Python, no Qt, no QGIS).

What the Model owns
-------------------
* The ALO document: order groups, their nesting and expanded state, and
  the draw order of the layers inside them.
* The document setting ``remove_empty_groups``.

What the Model mirrors (written only by the Controller, from QGIS)
------------------------------------------------------------------
* Each layer's display name and effective visibility.
* ``control_enabled`` — QGIS's ``hasCustomLayerOrder``.

Nobody but the Model mutates the tree. Every mutation notifies listeners
with ``(event_type, payload)``. Inside ``block_notifications()`` the
granular events are suppressed and a single resync (``model_loaded``, plus
``order_changed`` if the structure changed) is emitted on exit, so
listeners never miss a change.

Persistence (``serialize`` / ``load_from_json``) and undo snapshots
(``restore_structure``) carry the document only — never the mirrored QGIS
state.
"""
from __future__ import annotations

import copy
import json
import logging
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Union

log = logging.getLogger("AdvancedLayerOrder.model")


# ====================================================================
# Schema
# ====================================================================

TREE_JSON_SCHEMA_VERSION = 1

TYPE_GROUP = "group"
TYPE_LAYER = "layer"


# ====================================================================
# Nodes
# ====================================================================

@dataclass
class LayerNode:
    """A leaf referencing a QGIS layer by id."""
    id: str                    # QGIS layer id
    name: str
    visible: bool = True       # mirror of QGIS effective visibility

    @property
    def is_group(self) -> bool:
        return False


@dataclass
class GroupNode:
    """An order group. Children are LayerNode | GroupNode in draw order (top first)."""
    id: str                    # "grp_" + 10 hex chars
    name: str
    expanded: bool = True
    children: list = field(default_factory=list)

    @property
    def is_group(self) -> bool:
        return True


Node = Union[GroupNode, LayerNode]   # runtime alias: no `|` (Python 3.9)


def new_group_id() -> str:
    """Generate a unique group id (stable format, easy to recognise in JSON)."""
    return "grp_" + uuid.uuid4().hex[:10]


# ====================================================================
# Events
# ====================================================================

# Resync everything (load, undo, reconcile, end of a notification block)
EVENT_MODEL_LOADED = "model_loaded"

# Structural changes
EVENT_LAYER_ADDED = "layer_added"
EVENT_LAYER_REMOVED = "layer_removed"
EVENT_GROUP_CREATED = "group_created"
EVENT_GROUP_DELETED = "group_deleted"
EVENT_ITEM_MOVED = "item_moved"

# Display-only changes
EVENT_LAYER_RENAMED = "layer_renamed"
EVENT_GROUP_RENAMED = "group_renamed"
EVENT_VISIBILITY_CHANGED = "visibility_changed"
EVENT_EXPANDED_CHANGED = "expanded_changed"
EVENT_SETTING_CHANGED = "setting_changed"     # payload: {"key", "value"}

# Trailing signal after any structural change: the flattened order may differ.
# Payload {"resync": True} when it follows a model_loaded (already a full resync).
EVENT_ORDER_CHANGED = "order_changed"

SETTING_CONTROL_ENABLED = "control_enabled"
SETTING_REMOVE_EMPTY_GROUPS = "remove_empty_groups"

_STRUCTURAL_EVENTS = frozenset({
    EVENT_LAYER_ADDED, EVENT_LAYER_REMOVED, EVENT_GROUP_CREATED,
    EVENT_GROUP_DELETED, EVENT_ITEM_MOVED, EVENT_MODEL_LOADED,
})

Listener = Callable[[str, dict], None]


# ====================================================================
# Model
# ====================================================================

class LayerOrderModel:
    """Single source of truth for the order tree. See module docstring."""

    def __init__(self) -> None:
        self._root: list[Node] = []
        self._listeners: list[Listener] = []
        self._block_depth = 0
        self._suppressed = False          # something was emitted while blocked
        self._suppressed_structural = False
        self._settings = {
            SETTING_CONTROL_ENABLED: False,
            SETTING_REMOVE_EMPTY_GROUPS: True,
        }

    # ------------------------------------------------------------------
    # Listeners
    # ------------------------------------------------------------------
    def add_listener(self, cb: Listener) -> None:
        """Register `cb(event_type, payload)`."""
        if cb not in self._listeners:
            self._listeners.append(cb)

    def remove_listener(self, cb: Listener) -> None:
        if cb in self._listeners:
            self._listeners.remove(cb)

    @contextmanager
    def block_notifications(self):
        """Batch mutations: no granular events inside, one resync on exit.

        On exit (outermost block only), if anything changed, listeners get
        ``model_loaded`` and, if the structure changed, ``order_changed``.
        """
        self._block_depth += 1
        try:
            yield self
        finally:
            self._block_depth -= 1
            if self._block_depth == 0 and self._suppressed:
                structural = self._suppressed_structural
                self._suppressed = self._suppressed_structural = False
                self._emit(EVENT_MODEL_LOADED, {})
                if structural:
                    self._emit(EVENT_ORDER_CHANGED, {"resync": True})

    def _emit(self, event_type: str, payload: dict) -> None:
        if self._block_depth:
            self._suppressed = True
            if event_type in _STRUCTURAL_EVENTS:
                self._suppressed_structural = True
            return
        for cb in list(self._listeners):  # a listener may remove itself
            try:
                cb(event_type, payload)
            except Exception:
                # One failing listener must not break the others, but it
                # must not vanish either.
                log.exception("listener failed on %s", event_type)

    def _structure_changed(self, event_type: str, payload: dict) -> None:
        self._emit(event_type, payload)
        self._emit(EVENT_ORDER_CHANGED, {"resync": True} if event_type == EVENT_MODEL_LOADED else {})

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def get_root(self) -> list[Node]:
        """Top-level nodes. Read-only: never mutate what this returns."""
        return self._root

    def walk(self) -> Iterator[tuple[Node, GroupNode | None]]:
        """Yield (node, parent) for every node, pre-order (display order)."""
        def rec(nodes, parent):
            for n in nodes:
                yield n, parent
                if isinstance(n, GroupNode):
                    yield from rec(n.children, n)
        yield from rec(self._root, None)

    def find_item(self, item_id: str) -> Node | None:
        return next((n for n, _ in self.walk() if n.id == item_id), None)

    def find_parent(self, item_id: str) -> GroupNode | None:
        """Parent group of `item_id`, or None if top-level (or unknown)."""
        return next((p for n, p in self.walk() if n.id == item_id), None)

    def _siblings(self, parent: GroupNode | None) -> list[Node]:
        return parent.children if parent is not None else self._root

    def get_index_in_parent(self, item_id: str) -> int | None:
        siblings = self._siblings(self.find_parent(item_id))
        return next((i for i, s in enumerate(siblings) if s.id == item_id), None)

    def get_depth(self, item_id: str) -> int:
        """1 for top-level, 2 for one level deep, ...; 0 if unknown."""
        depth = 0
        node = self.find_item(item_id)
        while node is not None:
            depth += 1
            node = self.find_parent(node.id)
        return depth

    def iter_layer_ids(self) -> Iterator[str]:
        return (n.id for n, _ in self.walk() if isinstance(n, LayerNode))

    def get_flattened_layer_ids(self) -> list[str]:
        """Depth-first layer ids — exactly what is written to customLayerOrder."""
        return list(self.iter_layer_ids())

    def get_group_names(self) -> set[str]:
        return {n.name for n, _ in self.walk() if isinstance(n, GroupNode)}

    def unique_group_name(self, base: str = "New group") -> str:
        """`base`, or `base 2`, `base 3`, ... — the first unused group name."""
        existing = self.get_group_names()
        if base not in existing:
            return base
        n = 2
        while f"{base} {n}" in existing:
            n += 1
        return f"{base} {n}"

    def tree_order_key(self) -> dict[str, int]:
        """{node_id: pre-order position} for sorting ids in display order."""
        return {n.id: i for i, (n, _) in enumerate(self.walk())}

    def descendant_layer_ids(self, item_id: str) -> list[str]:
        node = self.find_item(item_id)
        if isinstance(node, LayerNode):
            return [node.id]
        if not isinstance(node, GroupNode):
            return []
        out: list[str] = []

        def rec(n):
            for ch in n.children:
                if isinstance(ch, LayerNode):
                    out.append(ch.id)
                else:
                    rec(ch)
        rec(node)
        return out

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def get_setting(self, key: str):
        return self._settings[key]

    def set_setting(self, key: str, value) -> None:
        if key not in self._settings:
            raise KeyError(key)
        if self._settings[key] == value:
            return
        self._settings[key] = value
        self._emit(EVENT_SETTING_CHANGED, {"key": key, "value": value})

    # Convenience accessors kept for readability at call sites / tests
    def get_remove_empty_groups(self) -> bool:
        return self._settings[SETTING_REMOVE_EMPTY_GROUPS]

    def set_remove_empty_groups(self, value: bool) -> None:
        self.set_setting(SETTING_REMOVE_EMPTY_GROUPS, bool(value))

    def get_control_enabled(self) -> bool:
        return self._settings[SETTING_CONTROL_ENABLED]

    def set_control_enabled(self, value: bool) -> None:
        self.set_setting(SETTING_CONTROL_ENABLED, bool(value))

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------
    def load_from_json(self, raw_json: str) -> None:
        """Replace the tree with a persisted document.

        Malformed input is tolerated: bad JSON gives an empty tree; nodes
        without id, of unknown type, or whose id was already seen are skipped.
        """
        log.debug("load_from_json")
        new_root: list[Node] = []
        seen: set[str] = set()
        if raw_json:
            try:
                obj = json.loads(raw_json)
            except (json.JSONDecodeError, TypeError):
                obj = {}
            for raw in obj.get("children", []) if isinstance(obj, dict) else []:
                node = self._build_node(raw, seen)
                if node is not None:
                    new_root.append(node)
        self._root = new_root
        self._structure_changed(EVENT_MODEL_LOADED, {})

    def _build_node(self, raw, seen: set[str]) -> Node | None:
        if not isinstance(raw, dict):
            return None
        t = raw.get("type")
        node_id = raw.get("id") or (new_group_id() if t == TYPE_GROUP else None)
        if node_id is None or node_id in seen:
            return None
        if t == TYPE_GROUP:
            seen.add(node_id)
            children = [c for c in (self._build_node(ch, seen) for ch in raw.get("children", [])) if c]
            return GroupNode(
                id=node_id,
                name=raw.get("name", "Group"),
                expanded=bool(raw.get("expanded", True)),
                children=children,
            )
        if t == TYPE_LAYER:
            seen.add(node_id)
            return LayerNode(id=node_id, name=raw.get("name", ""))
        return None

    def replace_root(self, nodes: list[Node]) -> None:
        """Swap in a tree computed elsewhere (e.g. reconcile with QGIS)."""
        log.debug("replace_root")
        self._root = list(nodes)
        self._structure_changed(EVENT_MODEL_LOADED, {})

    def restore_structure(self, raw_json: str) -> None:
        """Restore a document snapshot (undo/redo) without touching QGIS facts.

        The snapshot gives groups, nesting and order. The current layer set
        wins: layers removed since the snapshot are dropped, layers added
        since are kept next to their current flat neighbour. Layer names,
        visibility and the expanded state of existing groups keep their
        current values (they are not part of undo).
        """
        log.debug("restore_structure")
        current = {n.id: n for n, _ in self.walk() if isinstance(n, LayerNode)}
        expanded = {n.id: n.expanded for n, _ in self.walk() if isinstance(n, GroupNode)}
        current_order = self.get_flattened_layer_ids()

        snapshot = LayerOrderModel()
        snapshot.load_from_json(raw_json)

        def adopt(nodes):
            out = []
            for n in nodes:
                if isinstance(n, GroupNode):
                    n.children = adopt(n.children)
                    n.expanded = expanded.get(n.id, n.expanded)
                    out.append(n)
                elif n.id in current:
                    out.append(copy.copy(current[n.id]))
            return out
        snapshot._root = adopt(snapshot._root)

        # Layers that did not exist when the snapshot was taken
        present = set(snapshot.iter_layer_ids())
        for i, lid in enumerate(current_order):
            if lid in present:
                continue
            node = copy.copy(current[lid])
            prev = next((x for x in reversed(current_order[:i]) if x in present), None)
            if prev is None:
                snapshot._root.insert(0, node)
            else:
                parent = snapshot.find_parent(prev)
                siblings = snapshot._siblings(parent)
                siblings.insert(snapshot.get_index_in_parent(prev) + 1, node)
            present.add(lid)

        self._root = snapshot._root
        self._structure_changed(EVENT_MODEL_LOADED, {})

    def clear(self) -> None:
        log.debug("clear")
        self._root = []
        self._structure_changed(EVENT_MODEL_LOADED, {})

    # ------------------------------------------------------------------
    # Layers
    # ------------------------------------------------------------------
    def add_layer(self, layer_id: str, name: str, visible: bool = True,
                  parent_id: str | None = None,
                  index: int | None = None) -> None:
        """Add a layer under `parent_id` (None = top level) at `index` (None = end)."""
        log.debug("add_layer %s", layer_id)
        parent = self.find_item(parent_id) if parent_id else None
        if not isinstance(parent, GroupNode):
            parent = None
        siblings = self._siblings(parent)
        idx = len(siblings) if index is None else max(0, min(index, len(siblings)))
        siblings.insert(idx, LayerNode(id=layer_id, name=name, visible=visible))
        self._structure_changed(EVENT_LAYER_ADDED, {
            "layer_id": layer_id, "name": name, "visible": visible,
            "parent_id": parent.id if parent else None, "index": idx,
        })

    def add_layer_beside(self, layer_id: str, name: str, visible: bool,
                         anchor_id: str | None, after: bool) -> None:
        """Add a layer just before/after `anchor_id`, in the anchor's group.

        Unknown or missing anchor → top of the top level.
        """
        parent = self.find_parent(anchor_id) if anchor_id else None
        idx = self.get_index_in_parent(anchor_id) if anchor_id else None
        if idx is None:
            parent, idx = None, 0
        elif after:
            idx += 1
        self.add_layer(layer_id, name, visible, parent.id if parent else None, idx)

    def remove_layer(self, layer_id: str) -> None:
        """Remove a layer.

        With the remove-empty-groups setting, the groups this removal left
        empty (its group, then that group's parents while they become empty)
        are removed too. Other empty groups are left alone: an empty group
        the user created on purpose survives unrelated deletions.
        """
        log.debug("remove_layer %s", layer_id)
        node = self.find_item(layer_id)
        if not isinstance(node, LayerNode):
            return
        parent = self.find_parent(layer_id)
        self._siblings(parent).remove(node)
        self._emit(EVENT_LAYER_REMOVED, {"layer_id": layer_id})
        while self.get_remove_empty_groups() and parent is not None and not parent.children:
            grandparent = self.find_parent(parent.id)
            self._siblings(grandparent).remove(parent)
            self._emit(EVENT_GROUP_DELETED, {"group_id": parent.id, "unwrapped_children": []})
            parent = grandparent
        self._emit(EVENT_ORDER_CHANGED, {})

    def rename_layer(self, layer_id: str, new_name: str) -> None:
        node = self.find_item(layer_id)
        if not isinstance(node, LayerNode) or node.name == new_name:
            return
        old, node.name = node.name, new_name
        self._emit(EVENT_LAYER_RENAMED, {"layer_id": layer_id, "old_name": old, "new_name": new_name})

    def set_visibility(self, layer_id: str, visible: bool) -> None:
        node = self.find_item(layer_id)
        visible = bool(visible)
        if not isinstance(node, LayerNode) or node.visible == visible:
            return
        node.visible = visible
        self._emit(EVENT_VISIBILITY_CHANGED, {"layer_id": layer_id, "visible": visible})

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------
    def create_group(self, name: str, parent_id: str | None = None,
                     index: int | None = None) -> str:
        """Create an empty group; returns its id."""
        log.debug("create_group %r", name)
        parent = self.find_item(parent_id) if parent_id else None
        if not isinstance(parent, GroupNode):
            parent = None
        siblings = self._siblings(parent)
        idx = len(siblings) if index is None else max(0, min(index, len(siblings)))
        gid = new_group_id()
        siblings.insert(idx, GroupNode(id=gid, name=name))
        self._structure_changed(EVENT_GROUP_CREATED, {
            "group_id": gid, "name": name,
            "parent_id": parent.id if parent else None, "index": idx,
        })
        return gid

    def delete_group(self, group_id: str, unwrap_children: bool = True) -> None:
        """Delete a group; with `unwrap_children` its children take its place."""
        log.debug("delete_group %s", group_id)
        node = self.find_item(group_id)
        if not isinstance(node, GroupNode):
            return
        parent = self.find_parent(group_id)
        siblings = self._siblings(parent)
        idx = self.get_index_in_parent(group_id)
        siblings.pop(idx)
        promoted = list(node.children) if unwrap_children else []
        siblings[idx:idx] = promoted
        self._structure_changed(EVENT_GROUP_DELETED, {
            "group_id": group_id,
            "unwrapped_children": [{
                "item_id": ch.id,
                "new_parent_id": parent.id if parent else None,
                "new_index": idx + k,
            } for k, ch in enumerate(promoted)],
        })

    def rename_group(self, group_id: str, new_name: str) -> None:
        node = self.find_item(group_id)
        if not isinstance(node, GroupNode) or node.name == new_name:
            return
        old, node.name = node.name, new_name
        self._emit(EVENT_GROUP_RENAMED, {"group_id": group_id, "old_name": old, "new_name": new_name})

    def set_expanded(self, group_id: str, expanded: bool) -> None:
        node = self.find_item(group_id)
        expanded = bool(expanded)
        if not isinstance(node, GroupNode) or node.expanded == expanded:
            return
        node.expanded = expanded
        self._emit(EVENT_EXPANDED_CHANGED, {"group_id": group_id, "expanded": expanded})

    # ------------------------------------------------------------------
    # Moves
    # ------------------------------------------------------------------
    def move_item(self, item_id: str, new_parent_id: str | None, new_index: int) -> bool:
        """Index-based form of move_items() for scripting and tests.

        `new_index` is a position in the target parent *before* the item is
        taken out (i.e. "insert before the node currently at new_index").
        """
        parent = self.find_item(new_parent_id) if new_parent_id else None
        siblings = self._siblings(parent if isinstance(parent, GroupNode) else None)
        before = siblings[new_index].id if 0 <= new_index < len(siblings) else None
        if before == item_id:
            return False
        return self.move_items([item_id], new_parent_id, before)

    def move_items(self, item_ids: list[str], new_parent_id: str | None,
                   before_id: str | None = None) -> bool:
        """Atomically move nodes into `new_parent_id` (None = top level).

        The nodes are inserted as one contiguous block, in the given order,
        just before the sibling `before_id` (None → at the end). The anchor
        is resolved after the movers are taken out, so it can't be thrown
        off by shifting indices.

        Ids nested inside another mover travel with it and are ignored.
        Returns False — and emits nothing — if the move is invalid (unknown
        parent, anchor not a child of the parent or itself a mover, cycle)
        or changes nothing.
        """
        log.debug("move_items ids=%s parent=%s before=%s", item_ids, new_parent_id, before_id)
        nodes = [n for n in (self.find_item(i) for i in item_ids) if n is not None]
        movers: list[Node] = []
        for n in nodes:
            if any(n is m for m in movers):
                continue
            if any(m is not n and self._is_descendant(n, m) for m in nodes):
                continue
            movers.append(n)
        if not movers:
            return False

        new_parent = self.find_item(new_parent_id) if new_parent_id else None
        if new_parent_id is not None and not isinstance(new_parent, GroupNode):
            return False
        if new_parent is not None and any(self._is_descendant(new_parent, m) for m in movers):
            return False
        new_siblings = self._siblings(new_parent)
        if before_id is not None and (any(m.id == before_id for m in movers)
                                      or not any(s.id == before_id for s in new_siblings)):
            return False

        before = [(self.find_parent(m.id), self.get_index_in_parent(m.id)) for m in movers]
        for m in movers:
            siblings = self._siblings(self.find_parent(m.id))
            siblings.pop(next(i for i, s in enumerate(siblings) if s is m))

        idx = (len(new_siblings) if before_id is None
               else next(i for i, s in enumerate(new_siblings) if s.id == before_id))
        new_siblings[idx:idx] = movers

        if all(p is new_parent and i == idx + k for k, (p, i) in enumerate(before)):
            return False
        self._structure_changed(EVENT_ITEM_MOVED, {
            "item_ids": [m.id for m in movers],
            "new_parent_id": new_parent.id if new_parent else None,
            "new_index": idx,
        })
        return True

    def move_items_by_one(self, item_ids: list[str], up: bool) -> bool:
        """Move each item one step up (or down) within its own parent.

        Every selected item swaps with the nearest unselected sibling above
        (below) it, so adjacent selected items move as one block and keep
        their order. An item already first (last) in its parent stays: it
        never leaves its group. Items nested inside another selected item
        travel with it. Returns True if anything moved.
        """
        log.debug("move_items_by_one %s up=%s", item_ids, up)
        selected = [n for n in (self.find_item(i) for i in item_ids) if n is not None]
        ids = {n.id for n in selected
               if not any(m is not n and self._is_descendant(n, m) for m in selected)}
        moved = False
        parents = {id(self.find_parent(i)): self.find_parent(i) for i in ids}
        for parent in parents.values():
            siblings = self._siblings(parent)
            steps = range(1, len(siblings)) if up else range(len(siblings) - 2, -1, -1)
            for i in steps:
                j = i - 1 if up else i + 1          # the neighbour it would swap with
                if siblings[i].id in ids and siblings[j].id not in ids:
                    siblings[i], siblings[j] = siblings[j], siblings[i]
                    moved = True
        if moved:
            self._structure_changed(EVENT_ITEM_MOVED, {"item_ids": sorted(ids)})
        return moved

    def move_items_to_boundary(self, item_ids: list[str], to_top: bool) -> None:
        """Move each item to the top (or bottom) of its own parent, keeping their order."""
        log.debug("move_items_to_boundary")
        by_parent: dict = {}
        positioned = [i for i in item_ids if self.get_index_in_parent(i) is not None]
        for item_id in sorted(positioned, key=self.get_index_in_parent):
            parent = self.find_parent(item_id)
            by_parent.setdefault(id(parent), (parent, []))[1].append(item_id)
        moved = False
        for parent, ids in by_parent.values():
            siblings = self._siblings(parent)
            before = [s.id for s in siblings]
            picked = [s for s in siblings if s.id in ids]
            rest = [s for s in siblings if s.id not in ids]
            siblings[:] = picked + rest if to_top else rest + picked
            moved |= [s.id for s in siblings] != before
        if moved:
            self._structure_changed(EVENT_ITEM_MOVED, {"item_ids": list(item_ids)})

    # ------------------------------------------------------------------
    # Empty groups
    # ------------------------------------------------------------------
    def prune_empty_groups(self) -> int:
        """Remove every group without children (cascading). Returns the count."""
        removed = self._prune_empty_groups_internal()
        if removed:
            self._emit(EVENT_ORDER_CHANGED, {})
        return removed

    def _prune_empty_groups_internal(self) -> int:
        removed = 0

        def rec(children: list) -> None:
            nonlocal removed
            i = 0
            while i < len(children):
                ch = children[i]
                if isinstance(ch, GroupNode):
                    rec(ch.children)
                    if not ch.children:
                        children.pop(i)
                        removed += 1
                        self._emit(EVENT_GROUP_DELETED, {"group_id": ch.id, "unwrapped_children": []})
                        continue
                i += 1

        rec(self._root)
        return removed

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def serialize(self) -> str:
        """The document as versioned JSON (no mirrored QGIS state)."""
        def ser(node: Node) -> dict:
            if isinstance(node, GroupNode):
                return {"type": TYPE_GROUP, "id": node.id, "name": node.name,
                        "expanded": node.expanded, "children": [ser(c) for c in node.children]}
            return {"type": TYPE_LAYER, "id": node.id, "name": node.name}
        return json.dumps({"version": TREE_JSON_SCHEMA_VERSION,
                           "children": [ser(n) for n in self._root]}, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _is_descendant(self, candidate: Node | None, ancestor: Node | None) -> bool:
        """True if `candidate` is `ancestor` or lies anywhere below it."""
        if candidate is None or ancestor is None:
            return False
        if candidate is ancestor:
            return True
        return isinstance(ancestor, GroupNode) and any(
            self._is_descendant(candidate, ch) for ch in ancestor.children)
