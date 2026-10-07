"""Tree-walking helpers for Layer Order Plus.

These functions operate on QTreeWidget / QTreeWidgetItem instances but hold
no state themselves, so they're easy to unit-test (with a real QTreeWidget
under offscreen Qt) and reuse from both BetterLayerTree and BetterLayerOrderDock.
"""
import uuid

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QTreeWidget, QTreeWidgetItem


# Re-exported so callers can `from .tree_utils import ROLE_TYPE, ROLE_ID, ...`
# instead of reaching back into dock.py — keeps the dependency graph clean.
ROLE_TYPE = Qt.ItemDataRole.UserRole + 1   # "group" | "layer"
ROLE_ID   = Qt.ItemDataRole.UserRole + 2   # group_id | layer_id

TYPE_GROUP = "group"
TYPE_LAYER = "layer"


def new_group_id() -> str:
    """Generate a unique group id (stable format, easy to recognise in JSON)."""
    return "grp_" + uuid.uuid4().hex[:10]


def collect_group_names(tree: QTreeWidget) -> set:
    """Return the set of all group display names in the tree (recursive)."""
    names = set()

    def walk(item: QTreeWidgetItem):
        if item.data(0, ROLE_TYPE) == TYPE_GROUP:
            names.add(item.text(0))
        for i in range(item.childCount()):
            walk(item.child(i))

    for i in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(i))
    return names


def unique_group_name(tree: QTreeWidget, base: str = "New group") -> str:
    """Return base, or 'base 2', 'base 3', ... if base is already used."""
    existing = collect_group_names(tree)
    if base not in existing:
        return base
    n = 2
    while f"{base} {n}" in existing:
        n += 1
    return f"{base} {n}"


def expand_item_and_ancestors(item: QTreeWidgetItem) -> None:
    """Expand item and every parent so the new group is visible.

    Tolerates RuntimeError when the underlying C++ object has been deleted
    (can happen during async callbacks that touch the tree).
    """
    if item is None:
        return
    try:
        if item.treeWidget() is None:
            return
    except RuntimeError:
        return
    cur = item
    while cur is not None:
        try:
            cur.setExpanded(True)
            cur = cur.parent()
        except RuntimeError:
            break


def find_group_item(tree: QTreeWidget, group_id: str):
    """Find a group QTreeWidgetItem by ROLE_ID (safe after async callbacks)."""
    if not group_id:
        return None

    def walk(it):
        try:
            if it.data(0, ROLE_TYPE) == TYPE_GROUP and it.data(0, ROLE_ID) == group_id:
                return it
            for i in range(it.childCount()):
                found = walk(it.child(i))
                if found is not None:
                    return found
        except RuntimeError:
            return None
        return None

    for i in range(tree.topLevelItemCount()):
        found = walk(tree.topLevelItem(i))
        if found is not None:
            return found
    return None


def find_layer_item(tree: QTreeWidget, layer_id: str):
    """Find a layer QTreeWidgetItem by ROLE_ID. Returns None if not found."""
    if not layer_id:
        return None

    def walk(it):
        try:
            if it.data(0, ROLE_TYPE) == TYPE_LAYER and it.data(0, ROLE_ID) == layer_id:
                return it
            for i in range(it.childCount()):
                found = walk(it.child(i))
                if found is not None:
                    return found
        except RuntimeError:
            return None
        return None

    for i in range(tree.topLevelItemCount()):
        found = walk(tree.topLevelItem(i))
        if found is not None:
            return found
    return None


def iter_all_layer_ids(tree: QTreeWidget):
    """Yield every layer id in the tree, depth-first."""
    def walk(it):
        if it.data(0, ROLE_TYPE) == TYPE_LAYER:
            yield it.data(0, ROLE_ID)
        for i in range(it.childCount()):
            yield from walk(it.child(i))

    for i in range(tree.topLevelItemCount()):
        yield from walk(tree.topLevelItem(i))


def index_in_parent(tree: QTreeWidget, item: QTreeWidgetItem) -> int:
    """Return item's index within its parent (or top-level index if rootless)."""
    p = item.parent()
    if p is None:
        return tree.indexOfTopLevelItem(item)
    return p.indexOfChild(item)


def prune_empty_groups(tree: QTreeWidget) -> int:
    """Walk the tree and remove group nodes with childCount() == 0.

    Top-level groups and nested groups alike. Returns the count removed.
    Pure tree manipulation — does NOT touch undo/autosave (the caller's job).
    """
    removed = 0

    def walk(parent):
        nonlocal removed
        i = 0
        while i < parent.childCount():
            ch = parent.child(i)
            if ch.data(0, ROLE_TYPE) == TYPE_GROUP:
                walk(ch)  # recurse first
                if ch.childCount() == 0:
                    parent.takeChild(i)
                    removed += 1
                    continue
            i += 1

    # Top-level
    i = 0
    while i < tree.topLevelItemCount():
        top = tree.topLevelItem(i)
        if top.data(0, ROLE_TYPE) == TYPE_GROUP:
            walk(top)
            if top.childCount() == 0:
                tree.takeTopLevelItem(i)
                removed += 1
                continue
        i += 1
    return removed


def apply_name_filter(tree: QTreeWidget, needle: str) -> None:
    """Case-insensitive substring filter on layer/group names.

    Items are hidden (setHidden), not removed, so the tree state and undo
    stack are unaffected. A group is shown if it matches OR any descendant
    matches; groups that don't match themselves but have matching descendants
    are expanded so the match is visible.

    Empty needle clears the filter (unhide everything).

    Pure tree manipulation — does NOT touch signals or undo (the caller's job
    to wrap in blockSignals if needed).
    """
    needle = (needle or "").strip().lower()
    if not needle:
        def unhide(it):
            try:
                it.setHidden(False)
            except RuntimeError:
                return
            for i in range(it.childCount()):
                unhide(it.child(i))
        for i in range(tree.topLevelItemCount()):
            unhide(tree.topLevelItem(i))
        return

    def matches(it):
        try:
            return needle in (it.text(0) or "").lower()
        except RuntimeError:
            return False

    def filter_walk(it, ancestor_matches=False):
        """Return True if `it` or any descendant matches; set hidden accordingly.

        ancestor_matches: a parent (or ancestor) matched the filter — so this
        item should be shown regardless of its own match status, AND its
        descendants should be shown too (the whole subtree is visible).
        """
        try:
            self_match = matches(it)
        except RuntimeError:
            return False
        should_show = ancestor_matches or self_match
        any_descendant_match = False
        for i in range(it.childCount()):
            if filter_walk(it.child(i), ancestor_matches=should_show):
                any_descendant_match = True
        # Show if self matches, an ancestor matches, or any descendant matches
        final_show = should_show or any_descendant_match
        try:
            it.setHidden(not final_show)
            # Expand groups that have matching descendants so the match is visible
            if final_show and not self_match and any_descendant_match:
                it.setExpanded(True)
        except RuntimeError:
            pass
        return final_show

    for i in range(tree.topLevelItemCount()):
        filter_walk(tree.topLevelItem(i), ancestor_matches=False)
