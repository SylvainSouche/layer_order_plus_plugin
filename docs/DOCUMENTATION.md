# Layer Order Plus — Documentation

**Plugin:** Layer Order Plus  
**Version:** 1.0.5 (QGIS 4 fork; next milestone 1.1.0)  
**Original author:** Samuel Kultz (Kultz Engenharia)  
**This fork:** QGIS 4 / PyQt6 fixes, undo→map order fix, layer/group icons, documentation  

---

## 1. What it does

QGIS has two related concepts:

| Panel | Purpose |
|-------|---------|
| **Layers** | Layer tree: groups, visibility, properties, legend |
| **Layer Order** | Optional **custom drawing order** (Z-order) independent of the Layers panel |

**Layer Order Plus** is an alternative to the stock **Layer Order** panel. It adds:

- **Order groups** (folders) used only for organising the draw-order list  
- Drag-and-drop reordering with nested groups  
- Undo/redo of order changes  
- Persistence of the tree inside the QGIS project  

The **flattened** top-to-bottom order of layers in this panel becomes the project’s `customLayerOrder` (top of the list draws on top of the map).

> **Important:** Groups in this panel are **not** the same as groups in the Layers panel. They exist only for ordering. Visibility and legend structure still live in the Layers panel.

---

## 2. Requirements

- **QGIS 4.x** (tested on 4.2.x, PyQt6)  
- This fork uses Qt6 scoped enums; it is **not** compatible with QGIS 3 without a compatibility shim.

---

## 3. Installation

1. Download the plugin ZIP.  
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**.  
3. Enable **Layer Order Plus**.  
4. The dock appears (default: left). Toggle via the plugin menu entry.

To uninstall: disable the plugin and remove the folder  
`…/profiles/<profile>/python/plugins/layer_order_plus_qgis4/`.

---

## 4. User guide

### 4.1 Panel overview

- **Create group** — creates an order-group (folder icon). Selected layers (if any) are moved into the new group.  
- **Delete group** — removes the selected group and **promotes** its children to the group’s former position.  
- Tree:
  - **Folder icon** = order group  
  - **Layer-type icon** = map layer (point / line / polygon / raster / … via QGIS theme icons)  
- Drag and drop to reorder layers and groups.  
- **Ctrl+Z** / **Ctrl+Y** (or **Ctrl+Shift+Z**) — undo / redo when the dock has focus.

### 4.2 How order is applied

1. The tree is flattened depth-first (group children in order, nested groups included).  
2. That list is written to:

   ```text
   QgsProject.instance().layerTreeRoot().setHasCustomLayerOrder(True)
   root.setCustomLayerOrder(list_of_layers)
   ```

3. The map canvas uses this list as Z-order (first = top).

### 4.3 Drop rules

| Drop indicator | Effect |
|----------------|--------|
| **On** a group | Move selection to the **top** of that group |
| **On** a layer | Move selection **above** that layer (same parent) |
| **Above** item | Insert above target (same parent) |
| **Below** item | Insert below target (same parent) |

Safeguards:

- Cannot drop an item onto itself.  
- Cannot drop a group onto one of its own descendants (no cycles).

### 4.4 New layers

When a layer is added to the project, it is inserted in the Plus tree relative to the **current selection (anchor)** when possible, instead of always at the bottom.

### 4.5 Persistence

The tree is stored in the project as:

```text
Project entry key:  BetterLayerOrder / tree_json
```

Saving the project keeps groups and order. Reopening the project restores the panel tree and reapplies custom layer order.

### 4.6 Relationship to stock Layer Order

Both panels drive the same underlying `customLayerOrder`. Changes in Layer Order Plus update that list; the stock panel should show the same flat sequence (without Plus groups).

---

## 5. Developer notes

### 5.1 File layout

```text
layer_order_plus_qgis4/
├── __init__.py          # classFactory
├── plugin.py            # lifecycle, project I/O, signals
├── dock.py              # UI, tree, undo, apply, icons
├── metadata.txt
├── icon.png
├── LICENSE
└── docs/
    ├── DOCUMENTATION.md
    ├── TEST_SCENARIO.md
    └── QUALITY_OVERHAUL.md
```

### 5.2 Key classes

| Class | Role |
|-------|------|
| `BetterLayerOrderPlugin` | Menu action, dock registration, project read/write, layer add/remove signals |
| `BetterLayerOrderDock` | Tree UI, groups, serialize, apply, undo stack |
| `BetterLayerTree` | Custom `dropEvent` with ordering rules |
| `TreeStateCommand` | `QUndoCommand` holding before/after JSON snapshots |

### 5.3 Apply path

```text
User edit
  → request_apply()          # debounced 50 ms (skipped if suspended / loading / in_undo)
  → _apply_now()
  → _apply_custom_order()    # setHasCustomLayerOrder(True) + setCustomLayerOrder(...)

Undo
  → TreeStateCommand.undo/redo
  → _apply_tree_state_from_undo(json)
  → load_from_project(json)  # rebuild tree
  → _apply_now_force()       # bypass guards, apply order immediately
```

### 5.4 Icons

```python
_icon_group()       # QgsApplication.getThemeIcon("/mIconFolder.svg")
_icon_for_layer(l)  # QgsIconUtils.iconForLayer(layer)
```

Set on every group/layer item at creation time.

### 5.5 Qt6 notes

This fork expects PyQt6-style APIs, for example:

- `Qt.ItemDataRole.UserRole`
- `Qt.ItemFlag.ItemIsEditable`
- `QAbstractItemView.DropIndicatorPosition.OnItem`
- `QKeySequence.StandardKey.Undo`
- `Qt.DockWidgetArea.LeftDockWidgetArea`
- `QUndoCommand` / `QUndoStack` from `QtGui`
- `QAction` from `QtGui`

### 5.6 Extension points

Useful places to extend:

- `_flatten_to_qgs_layers` — change flatten strategy  
- `_apply_custom_order` — extra side effects on apply  
- `on_layers_added` / `on_layers_removed` — placement policy  
- `_serialize_tree` / `load_from_project` — persistence format (add `"version"` when changing schema)

---

## 6. Known limitations

1. Order groups are **UI-only**; they do not create `QgsLayerTreeGroup` nodes.  
2. No per-layer visibility toggles in this panel (use Layers panel).  
3. Layer **rename** in the Layers panel is not yet reflected in the Plus tree until reload.  
4. Empty groups are kept after all children are removed.  
5. Undo shortcuts may require the dock to have focus.  
6. QGIS 3 is not supported by this fork without further shims.  
7. Possible double undo entries if both custom `dropEvent` and `rowsMoved` fire (see quality overhaul Phase 1.1).

See **QUALITY_OVERHAUL.md** for the planned fixes.

---

## 7. Changelog (this fork)

### 1.0.5

- Makefile + `VERSION` file as single source of truth; zip named `layer_order_plus_qgis4-<version>.zip`.

### 1.0.4

- Group folder icons + real layer-type icons; docs suite.

### 1.0.3

- Fix: undo restored the panel tree but not the map rendering order.  
- Icons: folder for groups; `QgsIconUtils.iconForLayer` for layers.  
- Docs: test scenario, quality overhaul, full documentation.

### 1.0.2

- Full Qt6 enum migration.

### 1.0.1

- `QUndoCommand` / `QUndoStack` import fix for PyQt6.  
- `qgisMaximumVersion=4.99.0`.

### 1.0.0 (upstream)

- Initial release: groups + sync with Layer Order.  
- Visibility not implemented.

---

## 8. License

MIT (see `LICENSE`). Original copyright Samuel Kultz; fork modifications as documented above.

---

## 9. Links

- Upstream: https://github.com/samkultz/layer_order_plus_plugin  
- Plugin directory (upstream): https://plugins.qgis.org/plugins/layer_order_plus/  
- QGIS custom layer order API: `QgsLayerTree.setCustomLayerOrder` / `setHasCustomLayerOrder`
