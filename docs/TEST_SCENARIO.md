# Layer Order Plus — Full Test Scenario

**Target:** QGIS 4.2.x (PyQt6)  
**Plugin version under test:** 1.0.3+  
**Goal:** Verify panel grouping, custom draw order, undo, persistence, icons, and project lifecycle.

---

## 0. Prerequisites

1. Clean QGIS 4.2 profile (or remove any previous `layer_order_plus*` folders under  
   `…/python/plugins/`).
2. Install the plugin from ZIP (**Plugins → Manage and Install Plugins → Install from ZIP**).
3. Enable the plugin; dock **Layer Order Plus** should appear (left by default).
4. Prepare a test project with at least:
   - 2 polygon layers  
   - 2 line layers  
   - 2 point layers  
   - 1 raster layer  
   (e.g. QGIS sample data or any local files.)

Open a **new empty project**, then add the layers above in any order.

---

## 1. First load / initial population

| Step | Action | Expected |
|------|--------|----------|
| 1.1 | With layers already in the project, open/enable Layer Order Plus | Tree lists **all** map layers |
| 1.2 | Check icons | Groups (if any) show **folder** icon; each layer shows **type icon** (point / line / polygon / raster / …) |
| 1.3 | Open stock **Layer Order** panel (`Ctrl+9`) | After any reorder in Layer Order Plus, custom order should match the flattened order of the Plus panel |
| 1.4 | Look at map canvas | Top item in Plus panel draws **on top** (highest Z) |

**Pass criteria:** Every project layer appears once; icons are correct; draw order follows the tree top→bottom = front→back.

---

## 2. Create groups

| Step | Action | Expected |
|------|--------|----------|
| 2.1 | Select 2–3 layers → **Create group** → name `Roads` | New group with folder icon; selected layers moved **into** the group |
| 2.2 | Select nothing (or one layer) → **Create group** → name `Empty` | Empty group appears (folder icon) |
| 2.3 | Create nested group: select a group or layers inside `Roads` → Create group `Primary` | Nested structure allowed |
| 2.4 | Double-click group name → rename to `Transport` | Name updates; undo available |

**Pass criteria:** Groups only exist in this panel (Layers panel tree unchanged); children stay under parent; rename works.

---

## 3. Drag-and-drop reorder

| Step | Action | Expected |
|------|--------|----------|
| 3.1 | Drag a layer **Above** another top-level layer | Layer moves above target; map redraws |
| 3.2 | Drag a layer **Below** another | Layer moves below; map redraws |
| 3.3 | Drop a layer **On** a group | Layer becomes first child of that group |
| 3.4 | Drop a group on another group | Group moves; no cycle (dropping onto own descendant must be ignored) |
| 3.5 | Multi-select several layers and drag | All move together; relative order preserved when possible |
| 3.6 | Compare map | Flattened order (depth-first, top→bottom) = custom layer order |

**Pass criteria:** No crash; no cyclic nesting; canvas matches flattened tree order.

---

## 4. Delete group

| Step | Action | Expected |
|------|--------|----------|
| 4.1 | Select group `Transport` → **Delete group** | Group removed; **children promoted** to where the group was (order preserved) |
| 4.2 | Undo | Group and structure restored **and** map order restored |

**Pass criteria:** Children not deleted; only the group node removed.

---

## 5. Undo / redo (critical)

| Step | Action | Expected |
|------|--------|----------|
| 5.1 | Reorder two layers so layer A is clearly above B on the map | Map shows A over B |
| 5.2 | **Ctrl+Z** (focus dock if needed) | Panel order **and** map order both revert |
| 5.3 | **Ctrl+Y** or **Ctrl+Shift+Z** | Order restored again (panel + map) |
| 5.4 | Create group → Undo | Group gone; layers back; map order correct |
| 5.5 | Rename group → Undo | Old name restored |
| 5.6 | Delete group → Undo | Group back with children; map order correct |

**Pass criteria:** Undo must change **both** the panel tree **and** `customLayerOrder` / canvas.  
*(Regression: v1.0.0–1.0.2 only restored the tree UI.)*

---

## 6. Layer add / remove while panel is open

| Step | Action | Expected |
|------|--------|----------|
| 6.1 | Select a layer in the Plus panel (anchor) | — |
| 6.2 | Add a new layer via Data Source Manager | New layer appears in the Plus tree near the **anchor** (not only at bottom) |
| 6.3 | Icon of new layer | Correct type icon |
| 6.4 | Remove a layer from the project | Layer disappears from Plus tree; map order updates |
| 6.5 | Undo after remove (if undo entry was pushed) | Behaviour depends on whether layer still exists in project; tree should stay consistent |

**Pass criteria:** No duplicate layer IDs in the tree; no orphan IDs after remove.

---

## 7. Persistence (project save/reload)

| Step | Action | Expected |
|------|--------|----------|
| 7.1 | Build a nested structure with 2 groups and a custom order | — |
| 7.2 | **Save project** | Project dirty flag cleared after save |
| 7.3 | Close project / open another / reopen the same `.qgz` | Tree structure (groups + order) restored |
| 7.4 | Map canvas after reload | Same draw order as before save |
| 7.5 | Stock Layer Order panel | Reflects the same flattened order |

**Pass criteria:** `BetterLayerOrder/tree_json` in project stores the tree; reload does not lose groups.

---

## 8. Project lifecycle edge cases

| Step | Action | Expected |
|------|--------|----------|
| 8.1 | **Project → New** | Tree clears; no crash |
| 8.2 | Open a project that never used the plugin | All layers listed (flat); no error |
| 8.3 | Disable plugin → enable again | Dock returns; data still in project entry |
| 8.4 | Unload plugin while project dirty | Last tree state written to project |

---

## 9. Icons checklist

| Item | Expected icon |
|------|----------------|
| Order group | Folder (`mIconFolder.svg`) |
| Point layer | Point layer theme icon |
| Line layer | Line layer theme icon |
| Polygon layer | Polygon layer theme icon |
| Raster | Raster theme icon |
| Mesh / point cloud / other | Matching `QgsIconUtils.iconForLayer` |

---

## 10. Negative / stress tests

| Step | Action | Expected |
|------|--------|----------|
| 10.1 | Drop a group onto one of its own descendants | Drop ignored |
| 10.2 | Delete group when selection is a layer | No-op (button only deletes groups) |
| 10.3 | Empty project, no layers | Empty tree; no crash on Create group |
| 10.4 | Rapid drag-drop spam | Debounced apply; no freeze; final order consistent |
| 10.5 | 100+ layers | Tree usable; apply still works (may be slower) |

---

## Sign-off template

| Area | Pass / Fail | Notes |
|------|-------------|-------|
| Initial population | | |
| Groups create/delete/rename | | |
| Drag-drop | | |
| Undo restores **map** order | | |
| Add/remove layers | | |
| Save / reload | | |
| Icons | | |
| No crash / no traceback | | |

**Tester:** _______________  
**Date:** _______________  
**QGIS version:** _______________  
**Plugin version:** _______________
