"""Reconcile the Plus tree with a flat layer order coming from QGIS.

When the user reorders layers in the stock Layer Order panel, QGIS only
gives us a new flat list. The group structure has to be inferred. This
module is pure Python (no Qt, no QGIS) so it can be unit-tested.

Algorithm:

1. Find which layers moved. The stock panel's intermediate signal (the
   dragged layer is briefly present twice) gives an exact hint. Without
   it a move can be ambiguous (``E A B`` → ``A E B``: E moved down, or A
   moved up), so each candidate is tried and the most plausible result
   kept: fewest group-membership changes, then a layer landing strictly
   between two members of a group rather than on a group's edge.
2. Detach the moved layers. Every other layer ("anchor") kept its relative
   order, so every group is still contiguous and keeps its members.
3. Re-insert each moved layer between its new flat neighbours. The places
   it can go without breaking group contiguity are the deepest group
   containing both neighbours, plus any group that ends at the previous
   neighbour or starts at the next one (the boundary cases).
     - If one of those is the layer's original group, it stays there
       (reordering inside a group never changes membership, even when
       the layer lands first/last).
     - Otherwise it goes to the outermost choice: dropping next to a
       group makes it a sibling, dropping strictly between two members
       of a group makes it a member (the only choice in that case).

Groups (including empty ones) are never created or deleted.
"""
from __future__ import annotations

import copy
from bisect import bisect_left
from typing import Iterable, Optional

from .model import GroupNode, LayerNode


def _flatten(nodes) -> list:
    out = []
    for n in nodes:
        if isinstance(n, GroupNode):
            out.extend(_flatten(n.children))
        else:
            out.append(n.id)
    return out


def _lcs_ids(old: list, new: list) -> set:
    """Ids of a longest common subsequence of two permutations (via LIS)."""
    pos = {lid: i for i, lid in enumerate(old)}
    seq = [pos[lid] for lid in new]
    tails, tails_idx, prev = [], [], [-1] * len(seq)
    for i, v in enumerate(seq):
        k = bisect_left(tails, v)
        if k == len(tails):
            tails.append(v)
            tails_idx.append(i)
        else:
            tails[k] = v
            tails_idx[k] = i
        prev[i] = tails_idx[k - 1] if k > 0 else -1
    keep = set()
    i = tails_idx[-1] if tails_idx else -1
    while i != -1:
        keep.add(new[i])
        i = prev[i]
    return keep


def _same_without(old: list, new: list, ids: set) -> bool:
    return [x for x in old if x not in ids] == [x for x in new if x not in ids]


def _moved_candidates(old: list, new: list, hint: Iterable[str]) -> list:
    """Possible sets of moved layers, most likely first.

    A valid drag hint is the answer. Otherwise a single-layer move is often
    ambiguous (``E A B`` → ``A E B``: E moved down, or A moved up), so every
    single layer that explains the change is a candidate; failing that, the
    complement of a longest common subsequence.
    """
    hint = set(hint or ()) & set(old)
    if hint and _same_without(old, new, hint):
        return [hint]
    singles = [{x} for x in old if _same_without(old, new, {x})]
    return singles or [set(old) - _lcs_ids(old, new)]


def _parents(nodes, parent=None, out=None) -> dict:
    """{layer id: parent group id or None}."""
    out = {} if out is None else out
    for n in nodes:
        if isinstance(n, GroupNode):
            _parents(n.children, n.id, out)
        else:
            out[n.id] = parent
    return out


def _score(old_root: list, new_root: list, moved: set) -> tuple:
    """Lower is more plausible: fewest membership changes, then layers that
    landed strictly between two members of a group (unambiguous) first."""
    before, after = _parents(old_root), _parents(new_root)
    changed = sum(before[lid] != after[lid] for lid in before)
    tree = _Tree(new_root)
    on_edge = 0
    for lid in moved:
        parent = tree.parent(lid)
        siblings = tree.children_of(parent)
        i = next(k for k, ch in enumerate(siblings) if ch.id == lid)
        on_edge += parent is None or i == 0 or i == len(siblings) - 1
    return changed, on_edge


class _Tree:
    """Small helper giving parent lookups over a node list being edited."""

    def __init__(self, root: list):
        self.root = root

    def parent(self, node_id) -> Optional[GroupNode]:
        def walk(children, parent):
            for ch in children:
                if ch.id == node_id:
                    return True, parent
                if isinstance(ch, GroupNode):
                    found, p = walk(ch.children, ch)
                    if found:
                        return True, p
            return False, None
        return walk(self.root, None)[1]

    def chain(self, node_id) -> list:
        """Ancestor groups of node_id, outermost first ([] for top level)."""
        out = []
        p = self.parent(node_id)
        while p is not None:
            out.append(p)
            p = self.parent(p.id)
        return list(reversed(out))

    def children_of(self, group: Optional[GroupNode]) -> list:
        return group.children if group is not None else self.root

    def child_containing(self, group: Optional[GroupNode], node_id) -> Optional[int]:
        """Index in `group` of the child that is, or contains, node_id."""
        chain = self.chain(node_id)
        if group is None:
            top = chain[0].id if chain else node_id
        else:
            ids = [g.id for g in chain]
            if group.id not in ids:
                return None
            k = ids.index(group.id)
            top = chain[k + 1].id if k + 1 < len(chain) else node_id
        for i, ch in enumerate(self.children_of(group)):
            if ch.id == top:
                return i
        return None


def reconcile_tree(root: list, new_order: list, moved_hint: Iterable[str] = ()) -> Optional[list]:
    """Return a new tree whose flattened order is `new_order`.

    `root` is not modified. Returns None if `new_order` is not a
    permutation of the tree's layers (duplicates, additions, removals).
    """
    old_order = _flatten(root)
    if len(new_order) != len(set(new_order)) or set(new_order) != set(old_order):
        return None
    if new_order == old_order:
        return copy.deepcopy(root)

    candidates = _moved_candidates(old_order, new_order, moved_hint)
    results = [(_place_moved(root, new_order, moved), moved) for moved in candidates]
    return min(results, key=lambda r: _score(root, r[0], r[1]))[0]


def _place_moved(root: list, new_order: list, moved: set) -> list:
    """Detach `moved` layers and re-insert them at their new flat positions."""
    tree = _Tree(copy.deepcopy(root))
    moved = set(moved)

    # Detach moved layers, remembering their original parent
    orig_parent = {}
    nodes = {}
    for lid in moved:
        p = tree.parent(lid)
        siblings = tree.children_of(p)
        idx = next(i for i, ch in enumerate(siblings) if ch.id == lid)
        nodes[lid] = siblings.pop(idx)
        orig_parent[lid] = p.id if p is not None else None

    for pos, lid in enumerate(new_order):
        if lid not in moved:
            continue
        prev_id = new_order[pos - 1] if pos > 0 else None
        # Next neighbour that is already placed (an anchor); later moved
        # layers are placed after this one, so they don't count.
        next_id = next((x for x in new_order[pos + 1:] if x not in moved), None)

        prev_chain = tree.chain(prev_id) if prev_id else []
        next_chain = tree.chain(next_id) if next_id else []
        common_depth = 0
        while (common_depth < min(len(prev_chain), len(next_chain))
               and prev_chain[common_depth] is next_chain[common_depth]):
            common_depth += 1
        common = prev_chain[common_depth - 1] if common_depth else None
        candidates = [common] + prev_chain[common_depth:] + next_chain[common_depth:]

        target = common
        for c in candidates:
            if (c.id if c is not None else None) == orig_parent[lid]:
                target = c
                break

        children = tree.children_of(target)
        i = tree.child_containing(target, prev_id) if prev_id else None
        if i is not None:
            insert_at = i + 1
        else:
            j = tree.child_containing(target, next_id) if next_id else None
            insert_at = j if j is not None else len(children)
        children.insert(insert_at, nodes[lid])
        moved = moved - {lid}  # now placed: acts as an anchor for the rest

    return tree.root
