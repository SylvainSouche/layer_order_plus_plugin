# Layer Order Plus — User guide

**QGIS 4.x** · version in [`VERSION`](../VERSION) · developer documentation:
[`ARCHITECTURE.md`](ARCHITECTURE.md)

---

## 1. What it does

QGIS has two related panels:

| Panel | Purpose |
|---|---|
| **Layers** | The layer tree: groups, visibility, properties, legend |
| **Layer Order** | An optional *custom drawing order*, independent of the Layers panel |

**Layer Order Plus** is a Layer Order panel with **order groups**: folders
that exist only to organise the drawing order. Top of the list is drawn on
top of the map. The tree, flattened top to bottom, becomes QGIS's custom
layer order.

> Order groups are **not** Layers-panel groups. They don't change the
> legend, and the Layers panel doesn't change them.

---

## 2. Install

1. `make zip` → `dist/layer_order_plus_qgis4-<VERSION>.zip` (or use a release zip)
2. QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**
3. The **Layer Order Plus** dock appears on the left; toggle it from
   **Plugins → Layer Order Plus**.

---

## 3. The panel

### Control rendering order

The checkbox at the bottom is the same switch as the one in QGIS's own
Layer Order panel; they always agree.

* **On** — the Plus tree drives the map's drawing order; the tree and
  tools are active.
* **Off** — QGIS draws in Layers-panel order; the tree is greyed out.

### Toolbar and context menu

| Action | Effect |
|---|---|
| **Create group** | New group with a unique name, wrapping the selected items (it takes their place). Exception: with a single group selected, an empty subgroup is created at its top. Nothing selected: an empty group at the bottom. |
| **Rename group** | Exactly one group selected. |
| **Delete group** | One or more groups selected; their contents take their place. |
| **Expand / Collapse group** | Context menu. |
| **Move up / Move down** | Toolbar arrows, context menu, or **Ctrl+↑ / Ctrl+↓** (⌘ on macOS). Each selected item moves one step within its own group; adjacent selected items move together; an item already first/last in its group stays (use drag and drop to leave a group). One undo step. |
| **Move to top / bottom** | Context menu: each selected item goes to the top/bottom of its own group, keeping their order. |

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
* A group checkbox in Plus checks/unchecks every layer in it; it is
  partially checked when only some are visible.

### New and removed layers

A new layer appears next to its neighbour in the Layers panel. A removed
layer disappears; with **Remove empty groups on layer delete** checked,
groups left empty are removed too (the setting is saved with the project).

---

## 4. Working with QGIS's own Layer Order panel

Both panels show the same order. Moving a layer in QGIS's panel updates
the Plus groups:

* dropped **between two layers of a group** → it joins that group;
* dropped **on a group's edge** (just before its first or after its last
  layer) → it stays in its own group if that is the group, otherwise it
  becomes a sibling of the group;
* groups are never created or deleted from QGIS's panel, and no other
  layer changes group.

Group structure itself (creating, nesting) is only edited in Plus.

---

## 5. Undo / redo

* **Edit → Undo / Redo layer order**, or **Ctrl+Z**, **Ctrl+Y** /
  **Ctrl+Shift+Z** (when no layer is being edited — digitizing undo has
  priority).
* Undoable: create / rename / delete group, moves and drops.
* Not undoable (they belong to QGIS): adding/removing layers, visibility,
  changes made in QGIS's Layer Order panel. Undo never brings back a
  deleted layer nor removes one that exists.
* The history is cleared when a project is opened.

---

## 6. Saved with the project

| Project entry | Content |
|---|---|
| `BetterLayerOrder / tree_json` | The groups and the order: `{"version": 1, "children": [...]}` with group nodes (`id`, `name`, `expanded`, `children`) and layer nodes (`id`, `name`) |
| `BetterLayerOrder / removeEmptyGroups` | The remove-empty-groups setting |

The custom order itself and "Control rendering order" are QGIS's own
project settings. Visibility is never stored by Plus.

When a project opens, layers that no longer exist are dropped from the
tree and new ones are added next to their Layers-panel neighbour.

---

## 7. Troubleshooting

Check **Verbose logging** at the bottom of the panel, then open
**View → Panels → Log Messages**, tab **LayerOrderPlus**: drops, model
changes, reconciliations with QGIS's panel and applied orders are traced.
Errors are always logged there.

---

## 8. Credits and license

* **Original (v1.0.0):** Samuel Kultz — https://github.com/samkultz/layer_order_plus_plugin
* **QGIS 4 fork:** Sylvain Souche — https://github.com/SylvainSouche/layer_order_plus_plugin
* **License:** MIT — see [`LICENSE`](../LICENSE)
