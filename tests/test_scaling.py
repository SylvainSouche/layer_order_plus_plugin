"""Invariants behind the O(n) / O(n log n) bounds (docs/PERFORMANCE.md).

- The Model's id index never goes stale, whatever the sequence of edits.
- Batch add/remove give the same tree as the one-by-one calls.
- reconcile: the result always has the requested flat order, and the
  single-move candidates match the brute-force definition.
- The Qt item model's cached rows and group check counts stay exact.
"""
import random

from qgis.PyQt.QtCore import Qt

from advanced_layer_order.model import GroupNode, LayerNode, LayerOrderModel
from advanced_layer_order.reconcile import _flatten, _same_without, _single_moves, reconcile_tree
from advanced_layer_order.tree_model import LayerOrderItemModel


def _assert_index_fresh(m: LayerOrderModel):
    truth = {}
    m._lookup("?")                               # (re)build if dropped

    def rec(nodes, parent):
        for i, n in enumerate(nodes):
            truth[n.id] = (n, parent, i)
            if isinstance(n, GroupNode):
                rec(n.children, n)
    rec(m.get_root(), None)
    for item_id, (node, parent, i) in truth.items():
        got = m._lookup(item_id)
        assert got[0] is node and got[1] is parent and got[2] == i, item_id
    assert m._index.keys() == truth.keys()


def _random_tree(rng, ids, depth=0):
    out = []
    while ids and rng.random() < 0.85:
        if depth < 3 and rng.random() < 0.3:
            out.append(GroupNode(id=f"g{len(ids)}_{depth}_{rng.random()}", name="g",
                                 children=_random_tree(rng, ids, depth + 1)))
        else:
            out.append(LayerNode(id=ids.pop(), name="l"))
    return out


def test_index_never_stale_under_random_edits():
    rng = random.Random(3)
    for _run in range(150):
        m = LayerOrderModel()
        snapshots = [m.serialize()]
        for step in range(40):
            nodes = [n.id for n, _ in m.walk()]
            groups = [n.id for n, _ in m.walk() if isinstance(n, GroupNode)]
            layers = m.get_flattened_layer_ids()
            m.find_item("?")                     # index built: a missed invalidation shows
            op = rng.randrange(10)
            if op == 0:
                m.add_layer(f"L{_run}_{step}", "n", True, rng.choice([*groups, None]),
                            rng.choice([None, 0, 2]))
            elif op == 1:
                m.add_layers_beside([(f"L{_run}_{step}_{k}", "n", True, rng.choice([*nodes, None]),
                                      rng.random() < .5) for k in range(3)])
            elif op == 2 and layers:
                m.remove_layers(rng.sample(layers, min(3, len(layers))))
            elif op == 3:
                m.create_group("G", rng.choice([*groups, None]))
            elif op == 4 and groups:
                m.delete_group(rng.choice(groups), rng.random() < .7)
            elif op == 5 and nodes:
                m.move_items(rng.sample(nodes, min(2, len(nodes))), rng.choice([*groups, None]),
                             rng.choice([*nodes, None]))
            elif op == 6 and nodes:
                m.move_items_by_one(rng.sample(nodes, min(2, len(nodes))), rng.random() < .5)
            elif op == 7 and nodes:
                m.move_items_to_boundary(rng.sample(nodes, min(2, len(nodes))), rng.random() < .5)
            elif op == 8:
                m.prune_empty_groups()
            else:
                m.restore_structure(rng.choice(snapshots))
            snapshots.append(m.serialize())
            _assert_index_fresh(m)


def test_add_layers_beside_matches_one_by_one():
    batch, single = LayerOrderModel(), LayerOrderModel()
    for m in (batch, single):
        gid = m.create_group("G")
        m.add_layer("a", "a", parent_id=gid)
        m.add_layer("b", "b")
    entries = [("n1", "N1", True, "a", False), ("n2", "N2", False, "a", False),
               ("n3", "N3", True, "a", True), ("n4", "N4", True, "b", True),
               ("n5", "N5", True, None, False), ("n6", "N6", True, "unknown", True)]
    batch.add_layers_beside(entries)
    # one by one, the same result needs each later entry anchored on the previous one
    single.add_layer_beside("n1", "N1", True, "a", after=False)
    single.add_layer_beside("n2", "N2", False, "a", after=False)
    single.add_layer_beside("n3", "N3", True, "a", after=True)
    single.add_layer_beside("n4", "N4", True, "b", after=True)
    single.add_layer_beside("n6", "N6", True, None, after=True)
    single.add_layer_beside("n5", "N5", True, None, after=False)
    assert _shape(batch.get_root()) == _shape(single.get_root())
    assert batch.get_flattened_layer_ids() == ["n5", "n6", "n1", "n2", "a", "n3", "b", "n4"]
    assert batch.find_item("n2").visible is False


def test_add_layers_beside_events():
    m = LayerOrderModel()
    m.add_layer("a", "a")
    events = []
    m.add_listener(lambda t, p: events.append((t, p)))
    m.add_layers_beside([("x", "X", True, "a", True), ("y", "Y", True, "a", True)])
    assert [(t, p.get("layer_id"), p.get("index")) for t, p in events] == [
        ("layer_added", "x", 1), ("layer_added", "y", 2), ("order_changed", None, None)]


def test_remove_layers_prunes_only_groups_it_emptied():
    m = LayerOrderModel()
    outer = m.create_group("outer")
    inner = m.create_group("inner", outer)
    m.create_group("kept empty")
    m.add_layer("a", "a", parent_id=inner)
    m.add_layer("b", "b", parent_id=inner)
    m.add_layer("c", "c")
    events = []
    m.add_listener(lambda t, p: events.append(t))
    m.remove_layers(["a", "b", "nope"])
    assert [n.name for n, _ in m.walk()] == ["kept empty", "c"]
    assert events.count("layer_removed") == 2 and events.count("group_deleted") == 2
    assert events[-1] == "order_changed"


def test_single_moves_match_brute_force():
    rng = random.Random(5)
    for _ in range(3000):
        old = [f"L{i}" for i in range(rng.randint(1, 9))]
        new = list(old)
        x = new.pop(rng.randrange(len(new)))
        new.insert(rng.randint(0, len(new)), x)
        if rng.random() < .3:
            rng.shuffle(new)
        brute = [{lid} for lid in old if old != new and _same_without(old, new, {lid})]
        assert _single_moves(old, new) == brute


def test_reconcile_always_gives_the_requested_order():
    rng = random.Random(11)
    for _ in range(3000):
        ids = [f"L{i}" for i in range(rng.randint(1, 16))]
        pool = list(ids)
        root = _random_tree(rng, pool) + [LayerNode(id=x, name="l") for x in pool]
        new = _flatten(root)
        rng.shuffle(new)
        for hint in ([], new[:1]):
            result = reconcile_tree(root, new, hint)
            assert _flatten(result) == new
            assert {n.id for n in _walk(result) if isinstance(n, GroupNode)} == \
                   {n.id for n in _walk(root) if isinstance(n, GroupNode)}   # groups kept


def test_reconcile_long_chain_of_moved_layers():
    """Thousands of adjacent moved layers: no recursion limit on the splice."""
    n = 5000
    root = [LayerNode(id=f"L{i}", name="l") for i in range(n)]
    new = [f"L{i}" for i in range(n)][::-1]
    assert _flatten(reconcile_tree(root, new)) == new


def _shape(nodes):
    return [(n.name, _shape(n.children)) if isinstance(n, GroupNode) else n.id for n in nodes]


def _walk(nodes):
    for n in nodes:
        yield n
        if isinstance(n, GroupNode):
            yield from _walk(n.children)


def test_item_model_rows_and_group_counts():
    m = LayerOrderItemModel()
    m.render([{"type": "group", "id": "g", "name": "G", "children": [
        {"type": "layer", "id": "a", "name": "A", "visible": True},
        {"type": "group", "id": "h", "name": "H", "children": [
            {"type": "layer", "id": "b", "name": "B", "visible": False}]},
        {"type": "layer", "id": "c", "name": "C", "visible": True}]},
        {"type": "group", "id": "e", "name": "E", "children": []}])

    def state(item_id):
        return m.index_of(item_id).data(Qt.ItemDataRole.CheckStateRole)

    assert [m.index_of(i).row() for i in "gahbce"] == [0, 0, 1, 0, 2, 1]
    assert m.parent(m.index_of("b")).data() == "H"
    assert state("g") == Qt.CheckState.PartiallyChecked
    assert state("h") == Qt.CheckState.Unchecked
    assert state("e") == Qt.CheckState.Checked             # empty group: as before
    m.set_visible("b", True)
    m.set_visible("b", True)                                # no double count
    assert state("h") == Qt.CheckState.Checked and state("g") == Qt.CheckState.Checked
    m.set_visible("a", False)
    m.set_visible("c", False)
    m.set_visible("b", False)
    assert state("g") == Qt.CheckState.Unchecked
