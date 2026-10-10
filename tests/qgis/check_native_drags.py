"""Drags in QGIS's own Layer Order panel, with the event loop running between
its two steps: only the dragged layer may ever change group."""
import random

from harness import Rig, check, finish

from advanced_layer_order.model import GroupNode

r = Rig()
r.fresh_abcde()
r.take_control()
lid = r.ids
ab = r.create_group("AB", [lid["A"], lid["B"]])
cd = r.create_group("CD", [lid["C"], lid["D"]])
r.create_group("ABCD", [ab, cd])


def groups_of(names):
    parent = {}

    def rec(nodes, g):
        for n in nodes:
            if isinstance(n, GroupNode):
                rec(n.children, n.name)
            else:
                parent[r.name(n.id)] = g
    rec(r.model.get_root(), None)
    return {n: parent[n] for n in names}


base = groups_of("ABCD")
# The 1.2.27 report: E to the very top, then between A and B
r.native_drag("E", "EABCD")
r.native_drag("E", "AEBCD")
check(groups_of("ABCD") == base, f"reported sequence: {groups_of('ABCD')}")

for seed in (1, 2, 3):
    rnd = random.Random(seed)
    for _ in range(60):
        rest = [c for c in r.qgis_order() if c != "E"]
        pos = rnd.randrange(len(rest) + 1)
        new = "".join([*rest[:pos], "E", *rest[pos:]])
        if new == r.qgis_order():
            continue
        r.native_drag("E", new, between_steps_ms=30)
        check(groups_of("ABCD") == base, f"seed {seed}: {new} regrouped {r.shape()}")
        check(r.qgis_order() == r.alo_order(), f"seed {seed}: orders differ")
finish()
