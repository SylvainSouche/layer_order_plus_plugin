# Layer Order Plus — Documentation

**Plugin:** Layer Order Plus  
**Version:** 1.0.14 (QGIS 4 fork; next milestone **1.1.0**)  
**Original author:** Samuel Kultz (Kultz Engenharia) — upstream **v1.0.0**  
**This fork:** https://github.com/SylvainSouche/layer_order_plus_plugin  

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
- Toolbar + context menu for create / rename / delete group  
- Undo/redo of order changes (panel + **map** custom order), integrated with the Edit menu when not digitizing  
- Persistence of the tree inside the QGIS project  

The **flattened** top-to-bottom order of layers in this panel becomes the project’s `customLayerOrder` (top of the list draws on top of the map).

> **Important:** Groups in this panel are **not** the same as groups in the Layers panel. They exist only for ordering. Visibility and legend structure still live in the Layers panel.

---

## 2. Requirements

- **QGIS 4.x** (tested on 4.2.x, PyQt6)  
- This fork uses Qt6 scoped enums and `QtGui` undo classes; it is **not** a drop-in for QGIS 3 without a compatibility shim.

---

## 3. Installation

1. Build (optional): `make zip` → `dist/layer_order_plus_qgis4-<VERSION>.zip`  
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**  
3. Enable **Layer Order Plus** (experimental plugins may need to be allowed)  
4. The dock appears (default: left). Toggle via the plugin menu entry.

To uninstall: disable the plugin and remove  
`…/profiles/<profile>/python/plugins/layer_order_plus_qgis4/`.

---

## 4. User guide

### 4.0 Control rendering order

Checkbox at the bottom of the panel (same idea as the stock **Layer Order** panel).

- **Checked** — panel drives `customLayerOrder`; tree and tools enabled.
- **Unchecked** — custom order off; tree and group tools greyed out and non-interactive.

### 4.1 Toolbar

Icon-only buttons (Layers-panel style):

| Button | Action |
|--------|--------|
| **Create group** | New order-group. Selected items (if any) are moved into it. Default name is unique (`New group`, `New group 2`, …). |
| **Rename group** | Enabled when **exactly one** group is selected. Opens a name dialog. |
| **Delete group** | Enabled when **one or more** groups are selected. Deletes **all** selected groups; children are promoted in place. |

### 4.2 Tree and icons

| Node | Icon | Interaction |
|------|------|-------------|
| Order group | Folder (bundled SVG) | Double-click → **expand/collapse** only (no inline rename) |
| Layer | Type icon (QGIS / bundled SVG by geometry) | Double-click → no-op |

### 4.3 Context menu (right-click)

- **Create group**  
- **Rename group** — enabled only if exactly one group is selected  
- **Delete group** — enabled if at least one group is selected  

### 4.4 Drag and drop

| Drop | Result |
|------|--------|
| **On a group** | Move item(s) to the **top** of that group |
| **On a layer** | Create a **new group** (unique default name), put the **target layer** and the **dropped item(s)** inside it, expand the group and ancestors |
| **Above / below** | Reorder among siblings |

Safeguards: cannot drop onto a descendant; cannot drop “on” an item that is itself being moved.

### 4.5 Undo / redo

- History lives on the panel’s `QUndoStack`.  
- **Edit → Undo layer order / Redo layer order** when the panel is visible and **no** vector layer is in edit mode.  
- Shortcuts: **Ctrl+Z**, **Ctrl+Y**, **Ctrl+Shift+Z** under the same rules; also work with focus in the dock.  
- While a layer is being digitized, QGIS feature undo keeps priority.  
- Undo restores **both** the panel tree **and** the map `customLayerOrder`.

### 4.6 How order is applied

1. The tree is flattened depth-first (group children in order, nested groups included).  
2. That list is written with:

   ```text
   root.setHasCustomLayerOrder(True)
   root.setCustomLayerOrder(list_of_layers)
   ```

3. Apply is debounced; undo uses a forced immediate apply.

### 4.7 Persistence

Stored in the project under:

```text
BetterLayerOrder / tree_json
```

JSON shape (simplified):

```json
{
  "children": [
    { "type": "group", "id": "grp_…", "name": "New group", "expanded": true, "children": [
        { "type": "layer", "id": "<qgis-layer-id>" }
    ]},
    { "type": "layer", "id": "…" }
  ]
}
```

Schema version field is not yet written (planned for 1.1.0).

---

## 5. Version history (fork)

| Version | Notes |
|---------|--------|
| **1.0.0** | Upstream original (Samuel Kultz), QGIS 3-oriented |
| **1.0.1–1.0.2** | PyQt6 imports and scoped enums |
| **1.0.3** | Undo restores map order |
| **1.0.4–1.0.7** | Icons (theme then bundled SVG), toolbar button style |
| **1.0.8–1.0.10** | Edit-menu undo integration; `QUndoGroup` from QtGui |
| **1.0.9** | Drop on layer → new group with both items |
| **1.0.11** | Unique group names; expand group + ancestors after drop |
| **1.0.14** | Rename via toolbar/context; multi-delete; double-click expand only |
| **1.1.0** | *(planned)* after test validation |

Single source of truth: root file `VERSION` (synced into `metadata.txt` by `make sync`).

---

## 6. Build and CI

```bash
make show-version
make check          # scripts/check_plugin.py
make zip            # dist/layer_order_plus_qgis4-<VERSION>.zip
```

GitHub Actions on push/PR: metadata/VERSION/syntax/Qt6 pattern checks + zip artifact.

---

## 7. Developer notes

| Module | Role |
|--------|------|
| `plugin.py` | Dock lifecycle, project load/save, Edit-menu undo integration |
| `dock.py` | Tree UI, drop rules, groups, apply custom order, local undo stack |
| `icons/` | Bundled SVGs (always available without theme) |
| `scripts/check_plugin.py` | CI validation without QGIS runtime |

Known gaps (see `QUALITY_OVERHAUL.md`): dual undo on some drags, initial order seed, layer rename sync from Layers panel, JSON schema version, visibility toggles.

---

## 8. Credits and license

- **Original (v1.0.0):** Samuel Kultz — https://github.com/samkultz/layer_order_plus_plugin  
- **This fork:** Sylvain Souche — https://github.com/SylvainSouche/layer_order_plus_plugin  
- **License:** MIT — see `LICENSE`
