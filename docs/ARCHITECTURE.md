# Layer Order Plus — Architecture

One rule: **each piece does one thing, and data flows one way.**
The View shows the Model and reports what the user wants; it never acts on it.

```
                 intents                     transactions
   ┌──────────┐ ───────────► ┌────────────────┐ ──────────► ┌──────────────────┐
   │   View   │              │ ViewController │             │      Model       │
   │ view.py  │ ◄─────────── │ (+ undo stack) │ ◄────────── │  model.py (no Qt)│
   └──────────┘    render    └────────────────┘   events    └──────────────────┘
                                     │ requests for                ▲     │
                                     │ QGIS-owned state            │     │ events
                                     ▼                             │     ▼
                              ┌─────────────────────────────────────────────┐
                              │          Controller  (controller.py)        │
                              └─────────────────────────────────────────────┘
                                                  ▲ │
                                     QGIS signals │ │ setCustomLayerOrder, project entries,
                                                  │ ▼ layer-tree checkboxes
                                            ┌──────────┐
                                            │   QGIS   │
                                            └──────────┘
```

`plugin.py` creates the pieces and wires them. It is the only module that
knows all of them.

## Responsibilities

| Piece | Does | Never |
|---|---|---|
| **Model** `model.py` | Holds the document (groups, nesting, order, expanded, `remove_empty_groups`) and mirrors QGIS facts (layer names, effective visibility, `control_enabled`). All mutations, each followed by an event. | Import Qt or QGIS. |
| **View** `view.py`, `tree_widget.py` | Renders what it is told (`render*`). Emits one intent signal per user action, carrying all data needed (ids, target, flag). Keeps presentation state: selection, scroll, filter. | Change what it displays because of its own input. Drops, checkbox clicks, branch arrows and settings boxes are reported, not applied. Read the Model. |
| **ViewController** `view_controller.py` | Turns intents into Model transactions (one undo step each) or into requests for QGIS-owned state. Turns Model events into `render*` calls. | Touch QGIS. Read state back from the View. |
| **Controller** `controller.py` | Mirrors QGIS into the Model; applies the Model's order to QGIS; persists the document; reconciles with the stock Layer Order panel. | Touch the View or the ViewController. |
| **reconcile.py** | Pure function: infer the group tree for a new flat order. | Side effects. |
| **undo.py** | `TreeStateCommand`: before/after document snapshots. | Know the View. |

## Who owns which state

| State | Owner | Written by | Undoable | Persisted in `tree_json` |
|---|---|---|---|---|
| Groups, nesting, order | Model | ViewController (user), Controller (reconcile, layers added/removed) | yes (user edits only) | yes |
| Group expanded | Model | ViewController | no | yes |
| `remove_empty_groups` | Model | ViewController | no | own project entry |
| Layer set | QGIS | Controller | no | – |
| Layer names | QGIS | Controller | no | (informative copy) |
| Layer visibility | QGIS | Controller (on request) | no | no |
| `control_enabled` | QGIS (`hasCustomLayerOrder`) | Controller (on request) | no | by QGIS |
| Selection, scroll, filter | View | View | no | no |

Undo restores **structure only** (`Model.restore_structure`): layers QGIS
added since are kept, layers it removed are not brought back, names and
visibility keep their current values.

## Events

The Model emits `(event_type, payload)`:

* structural: `layer_added`, `layer_removed`, `group_created`,
  `group_deleted`, `item_moved` — always followed by `order_changed`;
* display: `layer_renamed`, `group_renamed`, `visibility_changed`,
  `expanded_changed`, `setting_changed`;
* `model_loaded` — "resync everything". Emitted by load/replace/restore and
  at the end of a `block_notifications()` batch; the `order_changed` that
  follows it carries `{"resync": True}`.

A user action runs inside `ViewController._user_edit`: one notification
batch, so the View renders once, the Controller applies once, and one undo
step is recorded.

## Sync with QGIS

* **Plus → QGIS**: `order_changed` → `setCustomLayerOrder` (50 ms debounce).
  While writing, `_applying` is set; QGIS echoes it synchronously and the
  Controller ignores its own echoes.
* **QGIS → Plus**: layer added (placed next to its Layers-panel neighbour,
  one loop turn later because QGIS creates the tree node after
  `layersAdded`), removed, renamed, visibility, `hasCustomLayerOrder`.
* **Stock Layer Order panel display**: that panel re-reads the order only on
  `customLayerOrderChanged`, which QGIS skips for an unchanged order and
  never sends when `hasCustomLayerOrder` flips. After a control toggle (either
  checkbox) or a project load, the Controller re-sets the same order through
  an empty one so the panel shows what the map really uses.
* **Stock Layer Order panel**: a drag there changes the order twice (layer
  briefly listed twice, then removed from its old row). The Controller
  reconciles once on the final order, using the duplicate as the hint of
  which layer moved. Rules (`reconcile.py`):
  * between two members of a group → joins that group;
  * on a group's edge → stays in its original group if possible, otherwise
    becomes a sibling;
  * groups are never created or deleted.

## Drag and drop

`BetterLayerTree.dropEvent` sets `IgnoreAction` (Qt must not move rows) and
emits `drop_intent(moving_ids, target_id, position)`:

| position | meaning | Model operation |
|---|---|---|
| `on` a layer | new group holding target + dropped items | `create_group` + `move_items` |
| `on` a group | append to the group | `move_items(ids, group, None)` |
| `above` / `below` | sibling of the target | `move_items(ids, parent, before_id)` |
| `end` | empty area | `move_items(ids, None, None)` |

`move_items` anchors on a sibling id, not an index, so multi-item drops
can't be thrown off by shifting indices.

## Tests

`make test` (PyQt6 + pytest, no QGIS needed): Model, reconcile, scenarios,
and `test_dnd.py`, which drives the real View + ViewController + Model and
asserts the View never acts on its own input.
