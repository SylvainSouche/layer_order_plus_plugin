"""Time and memory of the core (Model, reconcile, ViewController + Qt tree)
against the number of layers n = 1, 2, 4, … 8192. No QGIS needed.

    QT_QPA_PLATFORM=offscreen python tests/perf/bench_core.py [--max 8192] [--json out.json]

Tree shape: layers in groups of 8 (top-level groups), the shape of a real
project. For each operation and n: best wall time over a few untraced repeats
and the peak Python allocation of one more, traced run (tracemalloc). An
operation is dropped for larger n once one run exceeds --budget seconds.
The growth exponent is the log-log slope over the largest sizes measured.
"""
import argparse
import json
import math
import os
import sys
import time
import tracemalloc

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # tests/ → conftest stubs + package alias
import conftest  # noqa: E402,F401  (installs qgis stubs, registers advanced_layer_order)
from qgis.PyQt.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from advanced_layer_order.model import GroupNode, LayerNode, LayerOrderModel  # noqa: E402
from advanced_layer_order.reconcile import reconcile_tree  # noqa: E402
from advanced_layer_order.tree_model import DROP_END  # noqa: E402
from advanced_layer_order.view import LayerOrderView  # noqa: E402
from advanced_layer_order.view_controller import ViewController  # noqa: E402

GROUP = 8


def layer_id(i):
    # realistic QGIS-like id length
    return f"layer_{i:05d}_c6d8568b_1475_425a_bdba_e98784bdf690"


def build_nodes(n):
    if n < GROUP:
        return [LayerNode(id=layer_id(i), name=f"L{i}") for i in range(n)]
    return [GroupNode(id=f"grp_{g:06d}", name=f"G{g}",
                      children=[LayerNode(id=layer_id(i), name=f"L{i}")
                                for i in range(g * GROUP, min(n, (g + 1) * GROUP))])
            for g in range(math.ceil(n / GROUP))]


def fresh_model(n):
    m = LayerOrderModel()
    m.replace_root(build_nodes(n))
    return m


def measure(fn, setup, repeats):
    """Best untraced time over repeats; peak allocation from one extra,
    traced run (tracemalloc slows Python ~10x, so it never times a run)."""
    best = math.inf
    for _ in range(repeats):
        state = setup()
        t0 = time.perf_counter()
        fn(state)
        best = min(best, time.perf_counter() - t0)
    state = setup()
    tracemalloc.start()
    fn(state)
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return best, peak


def retained(build):
    """Memory kept alive by build() (tracemalloc, after the call returns)."""
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    obj = build()
    after = tracemalloc.take_snapshot()
    tracemalloc.stop()
    size = sum(s.size_diff for s in after.compare_to(before, "filename"))
    return obj, size


# ----------------------------------------------------------------------
# Operations: (name, setup(n) -> state, run(state), what it models)
# ----------------------------------------------------------------------
def ops():
    def with_mvc(n):
        m = fresh_model(n)
        v = LayerOrderView()
        vc = ViewController(m, v)
        return m, v, vc

    def last_layer(m):
        return m.get_flattened_layer_ids()[-1]

    def first_layer(m):
        return m.get_flattened_layer_ids()[0]

    return [
        ("load_from_json", lambda n: (fresh_model(n).serialize(), LayerOrderModel()),
         lambda s: s[1].load_from_json(s[0]), "open a saved project (document part)"),
        ("serialize", lambda n: fresh_model(n),
         lambda m: m.serialize(), "save / one undo snapshot"),
        ("find_item (last layer)", lambda n: (fresh_model(n), None),
         lambda s: s[0].find_item(last_layer(s[0])), "any lookup by id"),
        ("mirror names+visibility (all layers)", lambda n: fresh_model(n),
         lambda m: [(m.rename_layer(lid, "x"), m.set_visibility(lid, False))
                    for lid in m.get_flattened_layer_ids()],
         "project load: QGIS names/visibility → Model"),
        ("drag 1 layer (VC: model+undo+render)", lambda n: with_mvc(n),
         lambda s: s[2].handle_drop([first_layer(s[0])], "", DROP_END),
         "one user drag, end to end without QGIS"),
        ("move up 1 layer (VC)", lambda n: with_mvc(n),
         lambda s: s[2]._on_move_by_one([last_layer(s[0])], True), "toolbar ▲"),
        ("undo + redo (VC)", lambda n: _after_drag(with_mvc(n)),
         lambda s: (s[2].undo_stack.undo(), s[2].undo_stack.redo()), "Undo / Redo buttons"),
        ("render whole tree (VC → Qt)", lambda n: with_mvc(n),
         lambda s: s[2]._render_all(), "full View refresh"),
        ("reconcile, drag hint", lambda n: _native_drag_input(n),
         lambda s: reconcile_tree(s[0], s[1], s[2]), "drag in QGIS's Layer Order panel"),
        ("reconcile, no hint", lambda n: _native_drag_input(n, hint=False),
         lambda s: reconcile_tree(s[0], s[1], s[2]), "reorder by another plugin / script"),
    ]


def _mvc(m):
    v = LayerOrderView()
    return m, v, ViewController(m, v)   # renders the whole tree into the View


def _after_drag(state):
    m, _v, vc = state
    vc.handle_drop([m.get_flattened_layer_ids()[0]], "", DROP_END)
    return state


def _native_drag_input(n, hint=True):
    m = fresh_model(n)
    order = m.get_flattened_layer_ids()
    if len(order) < 2:
        return m.get_root(), order, []
    moved = order[-1]                          # last layer dragged to the middle
    new = [x for x in order if x != moved]
    new.insert(len(new) // 2, moved)
    return m.get_root(), new, [moved] if hint else []


def slope(points):
    """log-log slope over the last 3 sizes (growth exponent)."""
    pts = [(math.log(n), math.log(t)) for n, t in points if t > 0][-3:]
    if len(pts) < 2:
        return None
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    return (y1 - y0) / (x1 - x0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=8192)
    ap.add_argument("--budget", type=float, default=20.0)
    ap.add_argument("--json")
    args = ap.parse_args()
    sizes = [2 ** k for k in range(int(math.log2(args.max)) + 1)]
    results = {"sizes": sizes, "ops": {}, "memory": {}}

    for name, setup, run, what in ops():
        rows = []
        for n in sizes:
            repeats = 5 if n <= 256 else 3
            t, peak = measure(run, lambda n=n, setup=setup: setup(n), repeats)
            rows.append((n, t, peak))
            print(f"{name:40} n={n:5}  {t * 1000:10.2f} ms  peak {peak / 1024:9.0f} KiB", flush=True)
            if t > args.budget:
                print(f"{name:40} stopped: over the {args.budget:.0f} s budget", flush=True)
                break
        results["ops"][name] = {"what": what, "rows": rows,
                                "exponent": slope([(n, t) for n, t, _ in rows])}

    # Memory kept alive: Model, Model+View+VC, one undo step
    for n in sizes:
        m, model_bytes = retained(lambda n=n: fresh_model(n))
        _state, mvc_bytes = retained(lambda n=n: _mvc(fresh_model(n)))
        snapshot = len(m.serialize().encode())
        results["memory"][n] = {"model": model_bytes, "model+view": mvc_bytes,
                                "undo_step": 2 * snapshot}
        print(f"memory n={n:5}  model {model_bytes / 1024:9.0f} KiB   "
              f"model+view {mvc_bytes / 1024:9.0f} KiB   "
              f"undo step {2 * snapshot / 1024:8.0f} KiB", flush=True)

    print("\nGrowth exponents (log-log slope on the largest sizes; 1 = linear, 2 = quadratic):")
    for name, r in results["ops"].items():
        e = r["exponent"]
        shown = f"{e:.2f}" if e is not None else "–"
        print(f"  {name:40} {shown:>5}   {r['what']}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()
