"""Reconcile the Plus tree with a flat order coming from the stock Layer Order panel."""


from layer_order_plus_qgis4.model import GroupNode, LayerNode
from layer_order_plus_qgis4.reconcile import reconcile_tree


def _build(spec):
    out = []
    for item in spec:
        if isinstance(item, str):
            out.append(LayerNode(id=item, name=item))
        else:
            gid, children = item
            out.append(GroupNode(id=gid, name=gid, children=_build(children)))
    return out


def _shape(nodes):
    return [n.id if isinstance(n, LayerNode) else (n.id, _shape(n.children)) for n in nodes]


def _stock_move(tree_spec, order, hint=()):
    out = reconcile_tree(_build(tree_spec), list(order), hint)
    return _shape(out) if out is not None else None


ABCD = [("ABCD", [("AB", ["A", "B"]), ("CD", ["C", "D"])]), "E"]


def test_between_two_members_joins_group():
    assert _stock_move(ABCD, "ABCED") == [("ABCD", [("AB", ["A", "B"]), ("CD", ["C", "E", "D"])])]


def test_next_to_group_from_outside_stays_sibling():
    # E dropped right after D: adjacent to CD and ABCD, but E was top-level
    assert _stock_move([("CD", ["C", "D"]), "A", "E"], "CDEA") == [("CD", ["C", "D"]), "E", "A"]


def test_reorder_inside_group_keeps_membership_at_edges():
    # (C, (A, B)) → C, B, A  stays (C, (B, A)) whichever layer is seen as moved
    tree = ["C", ("g", ["A", "B"])]
    assert _stock_move(tree, "CBA") == ["C", ("g", ["B", "A"])]
    assert _stock_move(tree, "CBA", hint=["A"]) == ["C", ("g", ["B", "A"])]
    assert _stock_move(tree, "CBA", hint=["B"]) == ["C", ("g", ["B", "A"])]


def test_leaving_a_group_past_a_sibling():
    tree = [("CD", ["C", "E", "D"]), "X"]
    assert _stock_move(tree, "CDXE", hint=["E"]) == [("CD", ["C", "D"]), "X", "E"]


def test_between_members_of_other_group():
    tree = [("AB", ["A", "B"]), ("CD", ["C", "E", "D"])]
    assert _stock_move(tree, "AEBCD", hint=["E"]) == [("AB", ["A", "E", "B"]), ("CD", ["C", "D"])]


def test_nested_groups_survive():
    tree = [("ABCD", [("AB", ["A", "B"]), ("CD", ["C", "D"])]), "E"]
    assert _stock_move(tree, "EABCD", hint=["E"]) == ["E", ("ABCD", [("AB", ["A", "B"]), ("CD", ["C", "D"])])]


def test_empty_group_kept():
    tree = [("empty", []), "A", "B"]
    assert _stock_move(tree, "BA") == [("empty", []), "B", "A"]


def test_duplicate_or_mismatched_order_rejected():
    assert _stock_move(ABCD, "ABECED") is None   # stock panel's intermediate state
    assert _stock_move(ABCD, "ABCD") is None


def test_user_scenario_never_loses_groups():
    """Sequence from the 1.2.24 bug report, driven through stock-panel moves."""
    tree = _build(ABCD)
    steps = [
        ("ABCED", "E"),   # E between C and D
        ("ABCDE", "E"),   # after D
        ("ABECD", "E"),   # before C
        ("AEBCD", "E"),   # between A and B
        ("ABCDE", "E"),   # back to the end
    ]
    for order, moved in steps:
        tree = reconcile_tree(tree, list(order), [moved])
        assert tree is not None
        assert "".join(_flat(tree)) == order
        assert _group_ids(tree) == {"ABCD", "AB", "CD"}
    # E ended up inside AB in step 4; moving it to the very end takes it
    # past CD, so it leaves every group
    assert _shape(tree) == [("ABCD", [("AB", ["A", "B"]), ("CD", ["C", "D"])]), "E"]


# ---------- ambiguous moves without a drag hint ----------

def test_ambiguous_move_into_group_keeps_neighbour_in_group():
    """E above ABCD, dropped between A and B: 'E A B C D' → 'A E B C D'.
    Read as 'A moved up', A would leave AB (the 1.2.27 bug report)."""
    tree = ["E", ("ABCD", [("AB", ["A", "B"]), ("CD", ["C", "D"])])]
    assert _stock_move(tree, "AEBCD") == [("ABCD", [("AB", ["A", "E", "B"]), ("CD", ["C", "D"])])]


def test_ambiguous_swap_across_group_edge_keeps_group():
    # X (G: Y) → Y X : read as "X moved down", not "Y left its group"
    assert _stock_move(["X", ("G", ["Y"])], "YX") == [("G", ["Y"]), "X"]


def test_user_scenario_1_2_27_without_hints():
    tree = _build(ABCD)
    # E: between A and B, then to the very top, then back between A and B
    for order, expected in [
        ("AEBCD", [("ABCD", [("AB", ["A", "E", "B"]), ("CD", ["C", "D"])])]),
        ("EABCD", [("ABCD", [("AB", ["E", "A", "B"]), ("CD", ["C", "D"])])]),  # edge: stays in AB
        ("AEBCD", [("ABCD", [("AB", ["A", "E", "B"]), ("CD", ["C", "D"])])]),
    ]:
        tree = reconcile_tree(tree, list(order))
        assert _shape(tree) == expected, order


# ---------- invariant: only the dragged layer can change group ----------

def _parents(nodes, parent=None, out=None):
    out = {} if out is None else out
    for n in nodes:
        if isinstance(n, GroupNode):
            _parents(n.children, n.id, out)
        else:
            out[n.id] = parent
    return out


def _group_ids(nodes):
    return {n.id for n in nodes if isinstance(n, GroupNode)} | {
        g for n in nodes if isinstance(n, GroupNode) for g in _group_ids(n.children)}


def _flat(nodes):
    return [x for n in nodes for x in (_flat(n.children) if isinstance(n, GroupNode) else [n.id])]


def test_dragging_one_layer_never_regroups_another():
    """Every sequence of up to 4 stock-panel drags of E (with the drag hint
    the Controller always has): no other layer may change group."""
    checked = 0

    def explore(tree, depth):
        nonlocal checked
        if depth == 0:
            return
        rest = [x for x in _flat(tree) if x != "E"]
        for pos in range(len(rest) + 1):
            new = [*rest[:pos], "E", *rest[pos:]]
            if new == _flat(tree):
                continue
            result = reconcile_tree(tree, new, ["E"])
            before, after = _parents(tree), _parents(result)
            assert all(before[lid] == after[lid] for lid in "ABCD"), (new, _shape(tree), _shape(result))
            assert _flat(result) == new
            checked += 1
            explore(result, depth - 1)

    explore(_build(ABCD), 4)
    assert checked == 340
