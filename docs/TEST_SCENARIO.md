# Layer Order Plus — Full test scenario

**Target version:** 1.0.14  
**Environment:** QGIS 4.2.x (or current 4.x), fresh or throwaway project  
**Goal:** Manual acceptance before **1.1.0**

Mark each row **Pass / Fail** and note build (`VERSION` file / About).

---

## 0. Install and load

| Step | Action | Expected |
|------|--------|----------|
| 0.1 | Install from ZIP `layer_order_plus_qgis4-1.0.14.zip` | No “Error reading metadata” |
| 0.2 | Enable plugin | Dock **Layer Order Plus** appears; no traceback in Log Messages |
| 0.3 | Toolbar shows three icons | Create group, Rename group, Delete group |

**Pass criteria:** Plugin loads cleanly on QGIS 4.

---

## 1. Layers and icons

| Step | Action | Expected |
|------|--------|----------|
| 1.1 | Add several vector (point/line/polygon) and one raster | Each appears once in the Plus tree |
| 1.2 | Check icons | Groups → folder; layers → type-specific or generic layer icon |
| 1.3 | Compare with stock Layer Order / map | After reorders, canvas Z-order matches flattened Plus tree (top = on top) |

**Pass criteria:** Icons visible; every project layer listed once; draw order follows tree.

---

## 2. Create group

| Step | Action | Expected |
|------|--------|----------|
| 2.1 | Select 2–3 layers → Create group → accept default name | Unique name (`New group` or `New group N`); layers inside; folder icon |
| 2.2 | Create again with same default | Name is unique (not a duplicate label) |
| 2.3 | Create with empty selection | Empty group allowed |
| 2.4 | Nested: create group while selection is inside another group | Nested structure OK |

**Pass criteria:** Groups only in this panel; Layers panel tree unchanged.

---

## 3. Rename (no double-click edit)

| Step | Action | Expected |
|------|--------|----------|
| 3.1 | Select one group → **Rename** toolbar | Dialog opens; new name applied |
| 3.2 | Select one group → right-click → **Rename group** | Same dialog behaviour |
| 3.3 | Double-click group name | **Does not** open inline editor; toggles expand/collapse only |
| 3.4 | Rename with zero or multiple groups selected | Rename button disabled; context Rename disabled if not exactly one group |

**Pass criteria:** Rename only via toolbar/context dialog.

---

## 4. Double-click behaviour

| Step | Action | Expected |
|------|--------|----------|
| 4.1 | Double-click collapsed group | Expands |
| 4.2 | Double-click expanded group | Collapses |
| 4.3 | Double-click layer | No rename, no other action |

---

## 5. Drag and drop

| Step | Action | Expected |
|------|--------|----------|
| 5.1 | Drag layer **above** / **below** sibling | Reorder only; map updates |
| 5.2 | Drop layer **on** a group | Moves to top of that group |
| 5.3 | Drop layer or group **on** a layer | New group created; **target + dropped** inside; unique name; group and **ancestors expanded** |
| 5.4 | Drop onto own descendant | Ignored (no cycle) |
| 5.5 | Multi-select drag above/below | All move; relative order preserved when possible |

**Pass criteria:** No node disappearance; no crash; canvas matches flattened order.

---

## 6. Delete group

| Step | Action | Expected |
|------|--------|----------|
| 6.1 | Select **no** group (only layers) | Delete button **greyed out** |
| 6.2 | Select one group → Delete | Group removed; children promoted in place |
| 6.3 | Select **several** groups → Delete | **All** selected groups removed (not only last) |
| 6.4 | Context menu Delete with no group selected | Action disabled |

**Pass criteria:** Multi-delete works; children not destroyed.

---

## 7. Context menu

| Step | Action | Expected |
|------|--------|----------|
| 7.1 | Right-click empty / layer | Create enabled; Rename/Delete per selection rules |
| 7.2 | Right-click one group | Create, Rename, Delete all usable |
| 7.3 | Right-click with two groups selected | Rename disabled; Delete enabled |

---

## 8. Undo / redo (critical)

| Step | Action | Expected |
|------|--------|----------|
| 8.1 | Reorder or group change → **Ctrl+Z** | Tree **and** map order restored |
| 8.2 | **Ctrl+Y** / **Ctrl+Shift+Z** | Redo |
| 8.3 | **Edit → Undo layer order** | Same as Ctrl+Z when no layer is being edited |
| 8.4 | Start editing a vector layer, change features | Layer digitizing undo still works; order undo does not steal while editing |
| 8.5 | Stop editing, change order, Edit menu undo | Order undo available again |

**Pass criteria:** Undo never leaves map order out of sync with the panel.

---

## 9. Project save / load

| Step | Action | Expected |
|------|--------|----------|
| 9.1 | Build groups + order → Save project → Reload | Tree structure and names restored |
| 9.2 | Map draw order after reload | Matches saved tree |

---

## 10. Regression / smoke

| Step | Action | Expected |
|------|--------|----------|
| 10.1 | Add layer after groups exist | Layer appears in tree (typically top-level) |
| 10.2 | Remove layer from project | Removed from tree; no crash |
| 10.3 | Disable/enable plugin | Clean unload/reload |

---

## Sign-off

| Field | Value |
|-------|--------|
| Tester | |
| QGIS version | |
| Plugin VERSION | 1.0.14 |
| Date | |
| Result | Pass / Fail |
| Notes | |

When this scenario passes, bump to **1.1.0**.
