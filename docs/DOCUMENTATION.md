# Advanced Layer Order — User guide

**QGIS 3.40, 3.44 and 4.x** · version in [`VERSION`](../VERSION) · developer documentation:
[`ARCHITECTURE.md`](ARCHITECTURE.md)

---

## 1. What it does

QGIS has two related panels:

| Panel | Purpose |
|---|---|
| **Layers** | The layer tree: groups, visibility, properties, legend |
| **Layer Order** | An optional *custom drawing order*, independent of the Layers panel |

**Advanced Layer Order** (ALO below) is a Layer Order panel with **order groups**: folders
that exist only to organise the drawing order. Top of the list is drawn on
top of the map. The tree, flattened top to bottom, becomes QGIS's custom
layer order.

> Order groups are **not** Layers-panel groups. They don't change the
> legend, and the Layers panel doesn't change them.

---

## 2. Install

1. `make zip` → `dist/advanced_layer_order-<VERSION>.zip` (or use a release zip)
2. QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**
3. The **Advanced Layer Order** dock appears on the left; toggle it from
   **Plugins → Advanced Layer Order**.

---

## 3. The panel

### Control rendering order

The checkbox at the bottom is the same switch as the one in QGIS's own
Layer Order panel; they always agree.

* **On** — the ALO tree drives the map's drawing order; the tree and
  tools are active.
* **Off** — QGIS draws in Layers-panel order; the tree is greyed out.

### Toolbar and context menu

| Action | Effect |
|---|---|
| **Add Group** | New group with a unique name, wrapping the selected items (it takes their place). Exception: with a single group selected, an empty subgroup is created at its top. Nothing selected: an empty group at the bottom. |
| **Rename Group** | Exactly one group selected. |
| **Remove Group** | One or more groups selected; their contents take their place. |
| **↶ Undo / ↷ Redo** | Toolbar. Undo / redo the last layer-order step (see section 5). |
| **Move up / Move down** | Toolbar arrows, context menu, or **Ctrl+↑ / Ctrl+↓** (⌘ on macOS). Each selected item moves one step within its own group; adjacent selected items move together; an item already first/last in its group stays (use drag and drop to leave a group). One undo step. |
| **Move to Top / Bottom** | Context menu: each selected item goes to the top/bottom of its own group, keeping their order. |

Double-click a group (or click its arrow, or ←/→) to expand/collapse it.

### Drag and drop

| Drop | Result |
|---|---|
| **Above / below** an item | Placed there, in that item's group |
| **On a group** | Moved to the end of that group |
| **On a layer** | New group at that place holding the target layer, then the dropped items |
| **In the empty area** below the list | Moved to the bottom of the list |

Several items can be dragged at once; they keep their on-screen order. A
group can't be dropped into itself.

### Visibility checkboxes

They show whether each layer is **really visible** (its own checkbox and
all its Layers-panel groups checked), and stay in sync with the Layers
panel both ways.

* Checking a layer also checks its Layers-panel groups, so it shows.
* A group checkbox in ALO checks/unchecks every layer in it; it is
  partially checked when only some are visible.

### New and removed layers

A new layer appears next to its neighbour in the Layers panel. A removed
layer disappears; its group stays, even if now empty, as in QGIS's Layers
panel. Remove a group with **Remove Group**.

---

## 4. Working with QGIS's own Layer Order panel

Both panels show the same order. Moving a layer in QGIS's panel updates
the ALO groups:

* dropped **between two layers of a group** → it joins that group;
* dropped **on a group's edge** (just before its first or after its last
  layer) → it stays in its own group if that is the group, otherwise it
  becomes a sibling of the group;
* groups are never created or deleted from QGIS's panel, and no other
  layer changes group.

Group structure itself (creating, nesting) is only edited in ALO.

---

## 5. Undo / redo

* Use the **↶ Undo** and **↷ Redo** buttons of the ALO toolbar. Their
  tooltip names the step ("Undo: Move up"); they are greyed out when there
  is nothing to undo / redo.
* ALO has no keyboard shortcut and no Edit-menu entry for undo: **Ctrl+Z**
  (⌘Z) and **Edit → Undo** stay QGIS's own undo, which only covers the
  feature edits of the layer being edited. A plugin can't add its steps
  to that history, so the layer order has its own.
* **One history for the layer order**: create / rename / delete group,
  moves and drops in ALO, **and** reorders made in QGIS's own Layer Order
  panel, in the order they happened. Undo never skips or loses one.
* Not undoable (they belong to QGIS): adding / removing layers,
  visibility. Undo never brings back a deleted layer nor removes one that
  exists. Feature edits keep QGIS's own undo.
* The history is cleared when a project is opened.

---

## 6. Saved with the project

| Project entry | Content |
|---|---|
| `AdvancedLayerOrder / tree_json` | The groups and the order: `{"version": 1, "children": [...]}` with group nodes (`id`, `name`, `expanded`, `children`) and layer nodes (`id`, `name`) |

Projects saved with *Layer Order Plus* or early builds of this plugin keep
their groups: their `BetterLayerOrder` entries are read when no
`AdvancedLayerOrder` entry exists.

The custom order itself and "Control rendering order" are QGIS's own
project settings. Visibility is never stored by ALO.

When a project opens, layers that no longer exist are dropped from the
tree and new ones are added next to their Layers-panel neighbour.

---

## 7. Languages

The panel uses labels QGIS already has (Add Group, Move to Top, Undo,
Control rendering order…), so they appear in QGIS's language without the
plugin shipping translations.

---

## 8. Troubleshooting

Errors are always logged in **View → Panels → Log Messages**, tab
**AdvancedLayerOrder**. For a detailed trace (drops, model changes,
reconciliations with QGIS's panel, applied orders), start QGIS with the
environment variable `QGIS_DEBUG=1` (**Settings → Options → System →
Environment**, then restart QGIS): a **Verbose logging** box appears at
the bottom of the panel.

---

## 9. Credits and license

* **Original plugin:** *Layer Order Plus* 1.0.0 (QGIS 3), Samuel Kultz —
  https://github.com/samkultz/layer_order_plus_plugin
* **Advanced Layer Order** (continuation for QGIS 3.40+ and 4): Sylvain Souche —
  https://github.com/SylvainSouche/qgis_advanced_layer_order
* **License:** MIT — see [`LICENSE`](../LICENSE)
