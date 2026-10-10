"""No leaks: plugin load/unload cycles, project reopen cycles and a long
editing session leave nothing behind — no plugin objects, no widgets, no
connections to QGIS signals, no Python memory growth."""
import gc
import importlib
import tracemalloc

from harness import FakeIface, check, finish, open_test_project, pump
from qgis.core import QgsProject
from qgis.PyQt.QtCore import QCoreApplication, QEvent
from qgis.PyQt.QtWidgets import QApplication

from advanced_layer_order.controller import LayerOrderController
from advanced_layer_order.model import LayerOrderModel
from advanced_layer_order.tree_model import LayerOrderItemModel
from advanced_layer_order.tree_view import LayerOrderTree
from advanced_layer_order.view import LayerOrderView
from advanced_layer_order.view_controller import ViewController

CLASSES = (LayerOrderModel, ViewController, LayerOrderController, LayerOrderView,
           LayerOrderItemModel, LayerOrderTree)
CYCLES = 30


def settle():
    """Run the event loop, the deferred deletes (deleteLater) and the GC."""
    for _ in range(3):   # deleting an object can post more deletes (PyQt slot proxies)
        pump(0)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        gc.collect()


def live():
    counts = dict.fromkeys((c.__name__ for c in CLASSES), 0)
    for o in gc.get_objects():
        for c in CLASSES:
            if isinstance(o, c):
                counts[c.__name__] += 1
    return counts


def receivers():
    """Connections on the QGIS signals the Controller listens to."""
    proj = QgsProject.instance()
    root = proj.layerTreeRoot()
    sigs = [proj.layersAdded, proj.layersWillBeRemoved, proj.aboutToBeCleared, proj.cleared,
            iface.projectRead, iface.newProjectCreated, root.nameChanged, root.visibilityChanged,
            root.hasCustomLayerOrderChanged, root.customLayerOrderChanged]
    return [o.receivers(s) for o, s in zip([proj] * 4 + [iface] * 2 + [root] * 4, sigs)]


def reopen_project():
    QgsProject.instance().clear()
    open_test_project()
    iface.projectRead.emit()
    pump(0)


def kib(n):
    return f"{n / 1024:.1f} KiB"


package = importlib.import_module("advanced_layer_order")
iface = FakeIface()
open_test_project()
settle()
tracemalloc.start()

# ---- 1. load / unload ---------------------------------------------------
base_live, base_recv = live(), receivers()
base_widgets = len(QApplication.allWidgets())
mem = []
for i in range(CYCLES):
    plugin = package.classFactory(iface)
    plugin.initGui()
    pump(0)
    if i == 0:
        check(receivers() != base_recv, "plugin connects to QGIS (sanity)")
    plugin.unload()
    del plugin
    settle()
    mem.append(tracemalloc.get_traced_memory()[0])
growth = mem[-1] - mem[CYCLES // 3]
print(f"load/unload x{CYCLES}: live {live()}, widgets {len(QApplication.allWidgets())}"
      f" (was {base_widgets}), growth over the last {CYCLES - CYCLES // 3} cycles {kib(growth)}")
check(live() == base_live, f"plugin objects survive unload: {live()}")
check(receivers() == base_recv, f"QGIS signal connections left: {receivers()} vs {base_recv}")
check(len(QApplication.allWidgets()) == base_widgets, "widgets left after unload")
# PyQt keeps a few hundred bytes per load/unload (its own bookkeeping, no
# plugin object alive; 9–18 KiB over 20 cycles). The 1.3.4 leak — lambdas
# and closures capturing the panel — kept 150 KiB here, the closures alone 48.
check(growth < 32 * 1024, f"memory grows with load/unload: {kib(growth)}")

# ---- 2. project reopen with the plugin loaded ---------------------------
plugin = package.classFactory(iface)
plugin.initGui()
pump(0)
settle()
one_each = live()
check(all(v == 1 for v in one_each.values()), f"one of each while loaded: {one_each}")
widgets = len(QApplication.allWidgets())
mem = []
for _ in range(CYCLES):
    reopen_project()
    plugin.view_controller.handle_drop(plugin.model.get_flattened_layer_ids()[:1], "", "end")
    reopen_project()                                        # history dropped again
    settle()
    mem.append(tracemalloc.get_traced_memory()[0])
growth = mem[-1] - mem[CYCLES // 3]
print(f"project reopen x{2 * CYCLES}: live {live()}, widgets {len(QApplication.allWidgets())}"
      f" (was {widgets}), growth over the last {CYCLES - CYCLES // 3} cycles {kib(growth)}")
check(live() == one_each, f"plugin objects pile up across projects: {live()}")
check(len(QApplication.allWidgets()) == widgets, "widgets pile up across projects")
check(plugin.view_controller.undo_stack.count() == 0, "undo history survives a new project")
check(growth < 16 * 1024, f"memory grows with project reopen: {kib(growth)}")

# ---- 3. long editing session -----------------------------------------------
# Undo history is the one thing meant to grow; once dropped, memory returns.
vc, model = plugin.view_controller, plugin.model


def editing_session():
    """1000 drags (every third undone and redone); returns
    (memory during, undo steps, edits that changed something)."""
    start = tracemalloc.get_traced_memory()[0]
    changes = 0
    for i in range(1000):
        ids = model.get_flattened_layer_ids()
        vc.handle_drop([ids[i % len(ids)]], "", "end")
        changes += model.get_flattened_layer_ids() != ids
        if i % 3 == 0:
            vc.undo_stack.undo()
            vc.undo_stack.redo()
    settle()
    return tracemalloc.get_traced_memory()[0] - start, vc.undo_stack.count(), changes


settle()
before = tracemalloc.get_traced_memory()[0]
during, steps, changes = editing_session()
vc.reset_history()
settle()
first = tracemalloc.get_traced_memory()[0]
editing_session()
vc.reset_history()
settle()
second = tracemalloc.get_traced_memory()[0]
print(f"1000 edits: +{kib(during)} with {steps} undo steps; kept once the history is dropped:"
      f" {kib(first - before)} after the first session, {kib(second - first)} more after a second")
check(steps == changes, f"one undo step per edit that changed something: {steps} vs {changes}")
check(first - before < 64 * 1024, f"memory kept after dropping the history: {kib(first - before)}")
check(second - first < 8 * 1024, f"memory grows session after session: {kib(second - first)}")

plugin.unload()
finish()
