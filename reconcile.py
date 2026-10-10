"""Reconcile the ALO tree with a flat layer order coming from QGIS.

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
from collections.abc import Iterable

from .model import GroupNode


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


def _single_moves(old: list, new: list) -> list:
    """Every single layer whose move alone turns `old` into `new`, in `old`
    order. O(n): a move from p to q changes exactly the span p..q, so the
    layer is the span's first (moved down) or last (moved up) one."""
    n = len(old)
    i = 0
    while i < n and old[i] == new[i]:
        i += 1
    if i == n:
        return []
    j = n - 1
    while old[j] == new[j]:
        j -= 1
    out = []
    if new[j] == old[i] and old[i + 1:j + 1] == new[i:j]:
        out.append({old[i]})
    if new[i] == old[j] and old[i:j] == new[i + 1:j + 1]:
        out.append({old[j]})
    return out


def _moved_candidates(old: list, new: list, hint: Iterable[str]) -> list:
    """Possible sets of moved layers, most likely first.

    A valid drag hint is the answer. Otherwise a single-layer move is often
    ambiguous (``E A B`` → ``A E B``: E moved down, or A moved up), so every
    single layer that explains the change is a candidate; failing that, the
    complement of a longest common subsequence (O(n log n)).
    """
    hint = set(hint or ()) & set(old)
    if hint and _same_without(old, new, hint):
        return [hint]
    return _single_moves(old, new) or [set(old) - _lcs_ids(old, new)]


def _parents(nodes, parent=None, out=None) -> dict:
    """{layer id: parent group id or None}."""
    out = {} if out is None else out
    for n in nodes:
        if isinstance(n, GroupNode):
            _parents(n.children, n.id, out)
        else:
            out[n.id] = parent
    return out


def _edges(nodes, parent=None, out=None) -> dict:
    """{layer id: True if top level, or first or last in its group}."""
    out = {} if out is None else out
    last = len(nodes) - 1
    for i, n in enumerate(nodes):
        if isinstance(n, GroupNode):
            _edges(n.children, n, out)
        else:
            out[n.id] = parent is None or i in (0, last)
    return out


def _score(old_root: list, new_root: list, moved: set) -> tuple:
    """Lower is more plausible: fewest membership changes, then layers that
    landed strictly between two members of a group (unambiguous) first."""
    before, after = _parents(old_root), _parents(new_root)
    changed = sum(before[lid] != after[lid] for lid in before)
    edges = _edges(new_root)
    return changed, sum(edges[lid] for lid in moved)


def reconcile_tree(root: list, new_order: list, moved_hint: Iterable[str] = ()) -> list | None:
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
    """Detach `moved` layers and re-insert them at their new flat positions.

    O(n + k·depth): one walk indexes the tree, each moved layer is placed
    through parent links (as "before/after this node" or "at the end of
    this group"), and one final pass splices them all in.
    """
    root = copy.deepcopy(root)
    moved = set(moved)
    parent: dict = {}          # node id → parent GroupNode (None = top level)
    nodes: dict = {}           # moved layer id → its node

    def index(children, p):
        for ch in children:
            parent[ch.id] = p
            if ch.id in moved:
                nodes[ch.id] = ch
            if isinstance(ch, GroupNode):
                index(ch.children, ch)
    index(root, None)
    orig_parent = {lid: getattr(parent[lid], "id", None) for lid in moved}

    def detach(children):
        out = []
        for ch in children:
            if ch.id in moved:
                continue
            if isinstance(ch, GroupNode):
                ch.children = detach(ch.children)
            out.append(ch)
        return out
    root = detach(root)

    def chain(node_id) -> list:
        """Ancestor groups of node_id, outermost first ([] for top level)."""
        out = []
        p = parent[node_id]
        while p is not None:
            out.append(p)
            p = parent[p.id]
        return out[::-1]

    def child_containing(group, node_id):
        """Id of the child of `group` that is, or contains, node_id."""
        cur = node_id
        while parent[cur] is not group:
            if parent[cur] is None:
                return None
            cur = parent[cur].id
        return cur

    # Next anchor (non-moved layer) after each position
    next_anchor = [None] * len(new_order)
    anchor = None
    for pos in range(len(new_order) - 1, -1, -1):
        next_anchor[pos] = anchor
        if new_order[pos] not in moved:
            anchor = new_order[pos]

    before: dict = {}          # node id → moved layers just before it
    after: dict = {}           # node id → moved layers just after it
    end: dict = {}             # group id (None = top level) → appended layers
    for pos, lid in enumerate(new_order):
        if lid not in moved:
            continue
        prev_id = new_order[pos - 1] if pos > 0 else None
        next_id = next_anchor[pos]

        prev_chain = chain(prev_id) if prev_id else []
        next_chain = chain(next_id) if next_id else []
        common_depth = 0
        while (common_depth < min(len(prev_chain), len(next_chain))
               and prev_chain[common_depth] is next_chain[common_depth]):
            common_depth += 1
        common = prev_chain[common_depth - 1] if common_depth else None
        candidates = [common, *prev_chain[common_depth:], *next_chain[common_depth:]]

        target = common
        for c in candidates:
            if (c.id if c is not None else None) == orig_parent[lid]:
                target = c
                break

        x = child_containing(target, prev_id) if prev_id else None
        y = child_containing(target, next_id) if next_id and x is None else None
        if x is not None:
            after.setdefault(x, []).append(nodes[lid])
        elif y is not None:
            before.setdefault(y, []).append(nodes[lid])
        else:
            end.setdefault(getattr(target, "id", None), []).append(nodes[lid])
        parent[lid] = target   # now placed: an anchor for the moved layers after it

    def rebuild(children, group_id):
        out = []
        stack = [(True, ch) for ch in reversed([*children, *end.get(group_id, ())])]
        while stack:   # iterative: chains of "after" can be as long as k
            full, n = stack.pop()
            if not full:
                if isinstance(n, GroupNode):
                    n.children = rebuild(n.children, n.id)
                out.append(n)
                continue
            stack.extend((True, m) for m in reversed(after.get(n.id, ())))
            stack.append((False, n))
            stack.extend((True, m) for m in reversed(before.get(n.id, ())))
        return out

    return rebuild(root, None)
