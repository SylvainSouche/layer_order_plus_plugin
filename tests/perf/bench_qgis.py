"""Time and memory of the plugin inside real QGIS, n = 1, 2, 4, … 8192 layers.

    bash scripts/run_qgis_checks.sh --script tests/perf/bench_qgis.py
    # env: BENCH_MAX (default 8192), BENCH_BUDGET seconds (default 30),
    #      BENCH_JSON=path to save the results

Each size starts from a fresh project of n memory layers (all inside one
Layers-panel group, so a whole-project visibility toggle can be measured).
Measured: what the plugin adds on top of QGIS's own work, plus the
process's resident memory (RSS).
"""
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "qgis"))   # harness
from harness import FakeIface, pump  # noqa: E402
from qgis.core import QgsLayerTreeLayer, QgsProject, QgsVectorLayer  # noqa: E402

from advanced_layer_order.controller import LayerOrderController  # noqa: E402
from advanced_layer_order.model import LayerOrderModel  # noqa: E402
from advanced_layer_order.tree_model import DROP_END  # noqa: E402
from advanced_layer_order.view import LayerOrderView  # noqa: E402
from advanced_layer_order.view_controller import ViewController  # noqa: E402

MAX = int(os.environ.get("BENCH_MAX", "8192"))
BUDGET = float(os.environ.get("BENCH_BUDGET", "30"))


def rss_mib():
    """Current resident memory of this process, MiB (macOS/Linux `ps`)."""
    with os.popen(f"ps -o rss= -p {os.getpid()}") as p:
        return int(p.read().strip() or 0) / 1024


def timed(fn):
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


def project_with(n):
    proj = QgsProject.instance()
    proj.clear()
    root = proj.layerTreeRoot()
    group = root.addGroup("all")
    layers = [QgsVectorLayer("Point?crs=EPSG:4326", f"L{i}", "memory") for i in range(n)]
    proj.addMapLayers(layers, False)
    # One insert: group.addLayer() per layer costs QGIS O(n^2) and more
    # (8192 layers: 10 min instead of seconds)
    group.insertChildNodes(0, [QgsLayerTreeLayer(lyr) for lyr in layers])
    return proj, root, group, layers


def native_drag(root, proj, layer_id):
    """QGIS's Layer Order panel: insert at the new row, then remove the old."""
    cur = [lyr.id() for lyr in root.customLayerOrder()]
    target = [x for x in cur if x != layer_id]
    target.insert(len(target) // 2, layer_id)
    dup = list(cur)
    dup.insert(target.index(layer_id) + (1 if cur.index(layer_id) < target.index(layer_id) else 0), layer_id)
    root.setCustomLayerOrder([proj.mapLayer(i) for i in dup])
    pump(0)
    root.setCustomLayerOrder([proj.mapLayer(i) for i in target])


def run_size(n, dead):
    """One size: fresh project of n layers, the plugin on it, every timing."""
    rss0 = rss_mib()
    t_build = time.perf_counter()
    proj, root, group, layers = project_with(n)
    t_build = time.perf_counter() - t_build
    rss_project = rss_mib()
    # QGIS's own cost of removing a layer, before the plugin listens
    probe = QgsVectorLayer("Point?crs=EPSG:4326", "probe", "memory")
    proj.addMapLayer(probe, False)
    group.addLayer(probe)
    t_qgis_remove = timed(lambda: proj.removeMapLayer(probe.id()))

    model = LayerOrderModel()
    view = LayerOrderView()
    vc = ViewController(model, view)
    ctl = LayerOrderController(model, FakeIface())
    vc.visibility_requested.connect(ctl.set_layers_visible)
    vc.control_requested.connect(ctl.set_control_enabled)
    ctl.external_edit.connect(vc.record_step)
    row = {"n": n, "qgis project build": t_build, "remove 1 layer (QGIS alone)": t_qgis_remove}

    def measure(name, fn):
        if name in dead:
            return
        t = timed(fn)
        row[name] = t
        if t > BUDGET:
            dead.add(name)

    measure("load project (plugin)", lambda: (ctl.load_project(), pump(0)))
    root.setHasCustomLayerOrder(True)
    model.set_control_enabled(True)
    measure("apply order to QGIS", ctl._apply_custom_order)
    first = model.get_flattened_layer_ids()[0]
    measure("ALO drag (model+undo+render+apply)",
            lambda: (vc.handle_drop([first], "", DROP_END), pump(0), ctl._apply_custom_order()))
    measure("native panel drag (reconcile+render)", lambda: native_drag(root, proj, layers[-1].id()))
    extra = QgsVectorLayer("Point?crs=EPSG:4326", "extra", "memory")
    measure("add 1 layer (placement)", lambda: (proj.addMapLayer(extra), pump(0)))
    measure("remove 1 layer", lambda: proj.removeMapLayer(extra.id()))
    measure("toggle group of all layers", lambda: (group.setItemVisibilityChecked(False), pump(0)))
    measure("undo + redo", lambda: (vc.undo_stack.undo(), vc.undo_stack.redo()))
    row["RSS project MiB"] = rss_project - rss0
    row["RSS + plugin MiB"] = rss_mib() - rss_project

    ctl.teardown()
    vc.deleteLater()
    view.deleteLater()
    pump(0)
    return row


results = {"rows": []}
dead = set()
for size in [2 ** k for k in range(int(math.log2(MAX)) + 1)]:
    r = run_size(size, dead)
    results["rows"].append(r)
    print("  ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}"
                    for k, v in r.items()), flush=True)

if os.environ.get("BENCH_JSON"):
    with open(os.environ["BENCH_JSON"], "w") as f:
        json.dump(results, f, indent=1)
print("OK", flush=True)
os._exit(0)
