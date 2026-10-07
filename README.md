# Layer Order Plus (QGIS 4 fork)

**Kinda like the Layer Order panel, but with grouping.**

This repository is a maintained fork of the original **Layer Order Plus** plugin, updated for **QGIS 4 / PyQt6**, with bug fixes and documentation.

| | |
|---|---|
| **Current version** | See [`VERSION`](VERSION) (source of truth) |
| **Validated target** | **1.1.0** (after test pass) |
| **QGIS** | 4.x (this fork). Original upstream targeted 3.16–3.99. |
| **License** | MIT |

---

## Origin (v1.0.0)

The first public release is the upstream plugin by **Samuel Kultz (Kultz Engenharia)**:

| | |
|---|---|
| **Upstream repo** | https://github.com/samkultz/layer_order_plus_plugin |
| **Plugin directory** | https://plugins.qgis.org/plugins/layer_order_plus/ |
| **Baseline version** | **1.0.0** — initial release (groups in a Layer Order–style panel; experimental) |

**v1.0.0** introduced:

- A dock similar to the stock **Layer Order** panel  
- Create / delete **order groups** (for organising draw order only)  
- Sync of flattened order with QGIS `customLayerOrder`  
- Project persistence of the tree  

Known limits at 1.0.0 (from upstream changelog): visibility toggles not implemented; **not compatible with QGIS 4** as published (PyQt5-style imports).

This fork keeps that design and extends it for QGIS 4.

---

## What this fork changes

### Compatibility (required for QGIS 4)

- `QUndoCommand` / `QUndoStack` imported from `QtGui` (not `QtWidgets`)
- Qt6 scoped enums (`Qt.ItemDataRole.UserRole`, drop indicator positions, selection/drag modes, `StandardKey`, `DockWidgetArea`, …)
- `QAction` from `QtGui`

Without these, the plugin fails to load on QGIS 4 with errors such as missing `QUndoCommand` or `Qt.UserRole`.

### Behaviour fixes

| Issue | Fix |
|-------|-----|
| **Undo only restored the panel tree**, not the map Z-order | Undo calls `_apply_now_force()` so `setCustomLayerOrder` runs even while `_in_undo` is set |
| Empty / unclear apply when custom order list is empty | Apply keeps `hasCustomLayerOrder=True` once the panel drives order |

### UX

- **Folder icon** on order groups  
- **Real layer-type icons** via `QgsIconUtils.iconForLayer` (point / line / polygon / raster / …)

### Packaging & docs

- [`VERSION`](VERSION) — single source of truth for the release number  
- [`Makefile`](Makefile) — `make zip` → `layer_order_plus_qgis4-<VERSION>.zip`  
- [`docs/DOCUMENTATION.md`](docs/DOCUMENTATION.md) — user & developer guide  
- [`docs/TEST_SCENARIO.md`](docs/TEST_SCENARIO.md) — full manual test plan  
- [`docs/QUALITY_OVERHAUL.md`](docs/QUALITY_OVERHAUL.md) — review findings and remaining work toward **1.1.0**

---

## Version history

| Version | Notes |
|---------|--------|
| **1.0.0** | Upstream original (Samuel Kultz). QGIS 3-oriented; groups + order panel. |
| **1.0.1** | Import fix for `QUndoCommand` / `QUndoStack` on PyQt6; `qgisMaximumVersion` for QGIS 4. |
| **1.0.2** | Full Qt6 enum migration. |
| **1.0.3** | Undo restores **map** custom layer order, not only the tree UI. |
| **1.0.4** | Group folder + layer type icons; documentation suite. |
| **1.0.5** | `VERSION` + `Makefile`; versioned zip name. **Current.** |
| **1.1.0** | *(planned)* After validation of the test scenario; cleanup items in the quality overhaul plan. |

Always read the number in [`VERSION`](VERSION) and the `version=` field in `metadata.txt` (kept in sync by `make sync`).

---

## Install

1. Build (optional): from this directory, `make zip`  
2. In QGIS 4: **Plugins → Manage and Install Plugins → Install from ZIP**  
3. Choose `layer_order_plus_qgis4-<version>.zip`  
4. Enable **Layer Order Plus**

Or clone this repo into your profile’s `python/plugins/` folder (folder name must match what QGIS expects for the plugin package).

---

## Usage (short)

1. Open the **Layer Order Plus** dock.  
2. **Create group** / **Delete group** to organise the order list (folder icons = groups).  
3. Drag layers and groups to set draw order (top of the tree = drawn on top).  
4. **Ctrl+Z** / **Ctrl+Y** undo/redo (dock focused): restores panel **and** map order.  
5. Save the project to persist groups and order (`BetterLayerOrder` / `tree_json`).

Order groups in this panel are **not** Layers-panel groups; they only structure the custom draw-order list.

---

## Build

```bash
# Show version (from ./VERSION)
make show-version

# Write VERSION into metadata.txt and build:
#   ../layer_order_plus_qgis4-<VERSION>.zip
make zip

make clean   # remove versioned zips in parent directory
```

To bump a release:

```bash
echo 1.0.6 > VERSION   # or 1.1.0 when validated
make zip
```

---

## Credits

- **Original plugin (v1.0.0):** Samuel Kultz (Kultz Engenharia) — https://github.com/samkultz/layer_order_plus_plugin  
- **This fork:** QGIS 4 port, fixes, packaging, docs — https://github.com/SylvainSouche/layer_order_plus_plugin  

---

## License

MIT — see [LICENSE](LICENSE).
