# Advanced Layer Order — Performance

How time and memory grow with the number of layers *n*, measured for
n = 1, 2, 4, … 8192 on an Apple-silicon Mac (QGIS 4.2.3, Python 3.12).

```bash
make bench                                                        # core, no QGIS (≈ 1 min)
QGIS_APPS=/Applications/QGIS-final-4_2_3.app \
  bash scripts/run_qgis_checks.sh --script tests/perf/bench_qgis.py   # inside QGIS
```

`BENCH_MAX` / `--max` caps n, and `BENCH_JSON` / `--json` saves the results.
The QGIS run takes about 15 minutes to reach 8192 layers. Nearly all of
that is QGIS building its own test project (see below).

## Complexity per operation

Every operation is O(n) except the no-hint reconcile fallback, which is
O(n log n). Nothing is quadratic. The *exponent* column is the measured
log-log slope over the largest sizes (1 = linear, 2 = quadratic).

| Operation | When | Complexity | 1024 | 8192 | Exponent |
|---|---|---|---|---|---|
| Load the saved tree (`load_from_json`) | open project | O(n) | 0.7 ms | 6.0 ms | 1.00 |
| Serialize (save, one undo snapshot) | every edit | O(n) | 0.5 ms | 4.7 ms | 1.08 |
| Mirror names + visibility of all layers | open project | O(n) | 0.6 ms | 5.8 ms | 1.11 |
| One drag in the panel (Model + undo + render) | user edit | O(n) | 5.8 ms | 47 ms | 1.00 |
| Move up one layer | user edit | O(n) | 5.8 ms | 48 ms | 1.01 |
| Undo + redo | button | O(n) | 6.9 ms | 62 ms | 1.07 |
| Render the whole tree (VC → Qt) | after a structural change | O(n) | 1.3 ms | 12 ms | 1.06 |
| Reconcile a drag from QGIS's Layer Order panel | native drag | O(n) | 3.1 ms | 26 ms | 1.03 |
| Reconcile without a hint (script / other plugin) | external reorder | O(n), O(n log n) if many layers moved | 3.2 ms | 27 ms | 1.03 |
| Lookup by id (`find_item`) | everywhere | O(1), O(n) for the first lookup after a structural change | | | |

An edit costs O(n) as a whole because it serializes the tree once for undo
and renders it once. Everything inside the edit is O(1) per item:
lookups, a row's position, a group's checkbox state.

## Inside QGIS (8192 layers in one group)

| Operation | 1024 | 2048 | 4096 | 8192 | 8192 in 1.3.4 |
|---|---|---|---|---|---|
| Open project (plugin's share) | 4.2 ms | 10 ms | 18 ms | 104 ms | 5.5 s |
| Toggle the group holding every layer | 2.3 ms | 4.9 ms | 9.6 ms | 20 ms | 2.2 s |
| Drag in the panel, applied to QGIS | 8.5 ms | 18 ms | 35 ms | 77 ms | 114 ms |
| Drag in QGIS's Layer Order panel | 11 ms | 25 ms | 45 ms | 120 ms | 88 ms |
| Apply the order to QGIS | 1.2 ms | 2.5 ms | 5.4 ms | 11 ms | 14 ms |
| Add one layer | 0.7 ms | 2.1 ms | 3.2 ms | 8 ms | 8 ms |
| Undo + redo | 28 ms | 20 ms | 60 ms | 103 ms | 96 ms |
| Remove one layer | 33 ms | 131 ms | 504 ms | 2.0 s | 1.7 s |
| *Remove one layer, QGIS alone (plugin not loaded)* | 33 ms | 134 ms | 500 ms | 2.0 s | |
| *QGIS builds the test project* | 1.6 s | 12 s | 88 s | 704 s | |

Rows in italics are QGIS's own cost, measured for comparison:

- **Removing a layer** is quadratic inside QGIS's `removeMapLayer`. A
  profile at 2048 layers puts the plugin's share at 2 ms out of 109 ms.
- **Building a project of n layers** in QGIS grows about 8× per
  doubling. At 8192 layers it takes over ten minutes, while the plugin
  then opens the result in 0.1 s.

## Memory

| n | Model | Model + Qt view + VC | One undo step |
|---|---|---|---|
| 1024 | 268 KiB | 518 KiB | 207 KiB |
| 8192 | 2.1 MiB | 3.9 MiB | 1.7 MiB |

Everything is linear: about 270 bytes per layer in the Model, and about
500 bytes with the Qt view. These numbers are Python allocations kept
alive, measured with tracemalloc. The process RSS also changes inside
QGIS, but it mostly reflects QGIS's own work during the measured
operations, so it isn't a measure of the plugin.

An undo step stores two JSON snapshots of the tree. QUndoStack keeps
every step, so 100 edits on an 8192-layer project hold about 170 MiB.
Smaller projects are negligible: 100 edits on 500 layers is about 10 MiB.
Two remedies are possible but not implemented: an undo limit, or storing
only one snapshot per step.

## Leaks

`tests/qgis/check_no_leaks.py` runs with `make qgis-test` under every
installed QGIS (3.40 on Qt 5, 3.44, 4.2 on Qt 6). It covers three cases:

| Case | Checked | Result |
|---|---|---|
| Load + unload the plugin, 30 times | no plugin object, widget or QGIS signal connection left; Python memory flat | 0 objects left; 9–18 KiB over 20 cycles (PyQt's own bookkeeping) |
| Reopen the project 60 times, plugin loaded | one Model / View / VC / Controller; undo history emptied; memory flat | 0.7–1 KiB over 40 reopenings |
| 1000 edits with undo/redo, history dropped, twice | memory returns once the history is dropped | about 1 MiB while 800 steps are held (5 layers); 24–37 KiB kept once (warm-up), 0–2 KiB after a second session |

**Fixed: the panel leaked on every unload (1.3.4 and earlier).** Each
load/unload left the whole Model, View, ViewController and Qt tree in
memory, with the layer tree inside. A lambda or closure connected to a Qt
signal and capturing the panel is held by PyQt from C++, where Python's
garbage collector cannot see it, so the cycle was never freed. Signals
now go only to bound methods (which PyQt holds weakly) or to other
signals. The "report, don't toggle" checkboxes are now a small
`QCheckBox` subclass instead of a closure. Rule for future code: never
connect a lambda or closure that captures `self`.

Undo history is the one thing meant to grow while QGIS runs (see
Memory). It is dropped when a project is opened, closed or created.

## What changed since 1.3.4

In 1.3.4 the slow paths were quadratic, all for the same reason: a
lookup that walked the whole tree or a list, inside a loop over layers.

| Path | 1.3.4 | Now |
|---|---|---|
| `find_item` / `find_parent` / index in parent | walk the tree, O(n) | lazy id index, O(1). Rebuilt in one walk after a structural change; `test_scaling.py` checks it is never stale |
| Mirroring names/visibility, visibility toggles | n lookups × O(n), plus `QgsLayerTree.findLayer` (also a walk) per layer | O(1) lookups; `{id: node}` built once from `findLayers()` |
| Placing new layers / removing layers | per layer: scan of the panel order + an insert that drops the index | one sweep and one splice: `add_layers_beside`, `remove_layers` |
| Reconcile, "which single layer moved?" | try each layer, O(n) each | a move changes exactly one span: at most two candidates, O(n) |
| Reconcile, re-inserting moved layers | parent lookup by walking the tree, per layer | parent links plus one final splice, O(n + k·depth) |
| Qt item model: row of an item, group checkbox | `list.index`, and a subtree walk per paint | stored at render; visible/total counts updated along the ancestors |

The results are identical to 1.3.4. A differential run compared 60,000
random edit steps on the Model and 40,000 random reconciles, and both
matched the released code, events and their order included.
