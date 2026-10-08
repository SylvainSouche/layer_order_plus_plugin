> **Historical document** — the review and plan that led to 1.1.0. The
> current design is described in [`ARCHITECTURE.md`](ARCHITECTURE.md);
> later changes are in the `changelog` of `metadata.txt`.

## Status as of 1.1.0

All Phase 1, Phase 2, Phase 3, and Phase 4 items in scope of the 1.1.0 release are **DONE**.

**Shipped in 1.1.0:** dual undo guard (1.1/4.5), initial order seed (1.2), JSON schema version + expanded restore (2.4), layer rename sync (2.1), empty-group cleanup (2.2), control-rendering checkbox (1.3), QGIS 4 minimum (1.4), logging (2.3), context menu completion (3.2), drop-rule tooltips (3.5), View→Panels registration (3.4), shortcut focus (3.6), per-layer + per-group visibility checkboxes (3.1), name substring filter (3.3), dock.py split into modules (4.1), dedup of _add/_insert_layer_item (4.2), 54 unit tests + CI pytest (4.4).

**Deferred to 1.2.0+:** Phase 4.3 (complete type hint coverage — currently ~30% annotated).

---

# Layer Order Plus — Quality Overhaul Plan

Step-by-step plan to address every finding from the code review, plus fixes already applied (Qt6, undo→map order, icons).

Status legend: **DONE** · **PARTIAL** · **TODO**

---

## Phase 0 — Compatibility & crash fixes (DONE)

| # | Finding | Action | Status |
|---|---------|--------|--------|
| 0.1 | `QUndoCommand` / `QUndoStack` imported from `QtWidgets` | Import from `QtGui` | **DONE** |
| 0.2 | Scoped Qt6 enums (`UserRole`, `ItemIsEditable`, drop positions, …) | Use `Qt.ItemDataRole.*`, `Qt.ItemFlag.*`, `QAbstractItemView.DropIndicatorPosition.*`, etc. | **DONE** |
| 0.3 | `QAction` / `LeftDockWidgetArea` location | `QtGui.QAction`, `Qt.DockWidgetArea.LeftDockWidgetArea` | **DONE** |
| 0.4 | `metadata.txt` QGIS 4 range | `qgisMaximumVersion=4.99.0` | **DONE** |
| 0.5 | Undo restores tree but not map order | `_apply_now_force()` during undo; guards no longer skip order apply | **DONE** |
| 0.6 | Layer / group icons missing | Folder icon for groups; `QgsIconUtils.iconForLayer` for layers | **DONE** |

### How 0.5 was fixed

```text
_apply_tree_state_from_undo:
  _in_undo = True
  load_from_project(...)          # tree UI
  request_apply()                 # was NO-OP because _in_undo
  → replaced with _apply_now_force() → _apply_custom_order()
  _in_undo = False
```

### How 0.6 was fixed

- `_icon_group()` → `QgsApplication.getThemeIcon("/mIconFolder.svg")`
- `_icon_for_layer(layer)` → `QgsIconUtils.iconForLayer(layer)`
- Applied in `_add_layer_item`, `_insert_layer_item`, group create, `load_from_project`

---

## Phase 1 — Correctness (high priority) — TODO

### 1.1 Dual undo paths (dropEvent vs rowsMoved)

**Problem:** Custom `dropEvent` and `model().rowsMoved` can both push undo and call apply for one user gesture → duplicate history, possible snapshot skew.

**Steps:**

1. In `BetterLayerTree.dropEvent`, keep setting `_just_custom_dropped = True`.
2. In `BetterLayerOrderDock._on_rows_moved`:
   ```python
   if self.tree._just_custom_dropped:
       return
   ```
3. Optionally disconnect `rowsMoved` entirely if all moves go through custom `dropEvent`.
4. Test: single drag → exactly **one** undo step; Ctrl+Z once restores previous state.

**Status:** **TODO**

---

### 1.2 Initial layer order source

**Problem:** `_populate_all_layers_top_level` uses `proj.mapLayers().values()` (unordered).

**Steps:**

1. Prefer:
   ```python
   root = QgsProject.instance().layerTreeRoot()
   if root.hasCustomLayerOrder():
       layers = root.customLayerOrder()
   else:
       layers = root.layerOrder()  # or walk legend order
   ```
2. Fall back to `mapLayers().values()` only if empty.
3. Test: open project with known custom order → Plus panel matches it on first open.

**Status:** **TODO**

---

### 1.3 Sticky custom order flag

**Problem:** `setHasCustomLayerOrder(bool(layers))` turns custom order **off** when the list is empty.

**Steps:**

1. Always `setHasCustomLayerOrder(True)` once the user has used the panel (already partially done in `_apply_custom_order`).
2. Add optional UI checkbox “Control rendering order” (mirror stock Layer Order panel).
3. Persist checkbox state in project entry.

**Status:** **DONE** — checkbox shipped in 1.0.14 (`chk_control` at `dock.py:555`, `_on_control_toggled` at `dock.py:609`). State persists implicitly via QGIS's own `hasCustomLayerOrder` flag; `setDirty(True)` called on toggle.

---

### 1.4 QGIS 3 vs 4 support policy

**Problem:** Scoped enums break QGIS 3; metadata still says `qgisMinimumVersion=3.16`.

**Steps (pick one):**

- **A (recommended for this fork):** Set `qgisMinimumVersion=4.0.0`, document QGIS 4-only.
- **B:** Compatibility shim for every enum / import; test on 3.34 and 4.2.

**Status:** **DONE** — Option A chosen. `metadata.txt:3` `qgisMinimumVersion=4.0.0`, `qgisMaximumVersion=4.99.0`.

---

## Phase 2 — Robustness & signals — TODO

### 2.1 Layer rename

**Problem:** Tree keeps old display name when layer is renamed in Layers panel.

**Steps:**

1. Connect `QgsProject.instance().layerWasAdded` already handled; also:
   ```python
   layer.nameChanged.connect(...)
   ```
   for each layer, or project-level signal if available.
2. On rename: find item by layer id, `item.setText(0, new_name)`.
3. Do not push undo for external renames (or push a quiet update only).

**Status:** **TODO**

---

### 2.2 Empty groups after layer remove

**Problem:** Removing all layers from a group leaves empty groups forever.

**Steps:**

1. Optional setting: “Remove empty groups on layer delete”.
2. After prune in `on_layers_removed`, walk tree and drop group nodes with `childCount() == 0`.

**Status:** **TODO**

---

### 2.3 Logging instead of bare `except`

**Problem:** `except Exception: pass` hides failures on canvas refresh.

**Steps:**

1. `from qgis.core import QgsMessageLog, Qgis`
2. Log with `QgsMessageLog.logMessage(..., "LayerOrderPlus", Qgis.Warning)`.

**Status:** **DONE** — shipped in 1.0.15. Centralised `_log()` helper at `dock.py:42-51`; logged in `load_from_project`, `on_layers_added`, `on_layers_removed`, `_apply_custom_order`, dock `__init__`. Critical paths log `traceback.format_exc()` at `Qgis.Critical`. (Icon-loading fallbacks at lines 46/50/148/165/168/195/208/219/227/240/248/258/406/622 still bare-except — intentional, those are best-effort fallbacks.)

---

### 2.4 JSON schema version

**Problem:** No version field; future format changes will break load.

**Steps:**

1. Serialize: `{"version": 1, "children": [...]}`.
2. On load: if missing version, treat as v1; migrate later versions explicitly.
3. Stop writing unused `"expanded": True` or actually restore expanded state.

**Status:** **TODO**

---

## Phase 3 — UX — TODO

| # | Item | Steps |
|---|------|--------|
| 3.1 | Visibility checkboxes | Optional per-layer check state synced with layer `setItemVisibilityChecked` / renderer — or document “ordering only” |
| 3.2 | Context menu | **PARTIAL** — Create / Rename / Delete group shipped in 1.0.12. Missing: Expand / Collapse / Move to top / Move to bottom. |
| 3.3 | Search / filter | `QLineEdit` filtering tree items by name |
| 3.4 | Panel registration | **DONE** — auto-registered via `iface.addDockWidget` (QGIS lists every dock widget under View → Panels automatically). |
| 3.5 | Tooltips | Explain OnItem / Above / Below drop rules |
| 3.6 | Shortcut focus | **DONE** — both layers shipped in 1.0.8: app-level `QAction`s with `Qt.ShortcutContext.ApplicationShortcut` (`plugin.py:99-100,104`) + global event filter (`plugin.py:223-250`); dock-local fallback `QShortcut` with `WidgetWithChildrenShortcut` (`dock.py:562-570`). |

**Status:** **TODO**

---

## Phase 4 — Architecture / maintainability — DONE (4.3 type hints deferred to 1.2.0)

| # | Item | Steps |
|---|------|--------|
| 4.1 | Split `dock.py` | **DONE** — shipped in 1.0.26. dock.py split into icons.py (156 lines), tree_utils.py (231 lines), tree_widget.py (247 lines), undo.py (21 lines). dock.py itself went from 1782 → 1335 lines and now contains only the BetterLayerOrderDock class + orchestration. |
| 4.2 | Deduplicate | **DONE** — shipped in 1.0.26. `_add_layer_item` and `_insert_layer_item` now share a single `_make_layer_item` helper. `find_layer_item` / `find_group_item` / `_index_in_parent` / `iter_all_layer_ids` / `_prune_empty_groups` on the dock are now thin wrappers around the tree_utils functions. The dock's `_on_filter_changed` delegates to `tree_utils.apply_name_filter`. |
| 4.3 | Type hints | **PARTIAL** — ~30% of public methods annotated (e.g. `is_control_enabled() -> bool`, `_serialize_tree() -> str`). `lyr` params never typed. |
| 4.4 | Unit tests | **DONE** — shipped in 1.0.27. 54 tests across 4 files: `tests/test_tree_utils.py` (24), `tests/test_serialize.py` (9), `tests/test_filter.py` (11), `tests/test_icons.py` (10). `tests/conftest.py` installs qgis stubs so tests run without QGIS. CI runs `make test` between lint and zip build. |
| 4.5 | Dual event cleanup | See 1.1 |

---

## Phase 5 — Documentation & release — IN PROGRESS

| # | Item | Status |
|---|------|--------|
| 5.1 | Full user/developer documentation | **DONE** → `docs/DOCUMENTATION.md` |
| 5.2 | Test scenario | **DONE** → `docs/TEST_SCENARIO.md` |
| 5.3 | This overhaul plan | **DONE** → `docs/QUALITY_OVERHAUL.md` |
| 5.4 | Changelog discipline | Keep `metadata.txt` changelog in sync |
| 5.5 | Upstream PR | Offer Qt6 + undo + icons fixes to original repo |

---

## Suggested implementation order

1. Phase 1.1 (double undo) — prevents user-visible history bugs  
2. Phase 1.2 (initial order) — correct first impression  
3. Phase 1.4 (version policy) — honest metadata  
4. Phase 2.1 + 2.4 (rename + schema)  
5. Phase 3 UX as needed  
6. Phase 4 refactor when behaviour is stable  

---

## Already fixed in this fork (summary)

- QGIS 4 / PyQt6 imports and enums  
- Undo applies **map** custom layer order (`_apply_now_force`)  
- Folder icon for groups; real layer-type icons via `QgsIconUtils`  
- Sticky `hasCustomLayerOrder=True` on apply  
- Docs: test scenario, overhaul plan, full documentation  

Remaining work is listed above as **TODO** / **PARTIAL**.
