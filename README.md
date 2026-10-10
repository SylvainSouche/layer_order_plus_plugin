# Advanced Layer Order

**QGIS's Layer Order panel, with groups.**

Organise the map's drawing order in folders — *order groups* that exist
only for the drawing order, independent of the Layers panel. Top of the
list draws on top of the map.

| | |
|---|---|
| **Version** | [`VERSION`](VERSION) (synced into `metadata.txt`) |
| **QGIS** | 3.40, 3.44 (Qt 5) and 4.x (Qt 6), from one package |
| **License** | MIT |

## Features

* Order groups, nested, created from a selection or by dropping one layer
  onto another; rename, delete, expand/collapse.
* Drag and drop of any selection, keeping its on-screen order.
* Two-way sync with QGIS: Layers-panel visibility, layers added / removed /
  renamed, the "Control rendering order" switch, and moves made in QGIS's
  own Layer Order panel (groups are kept consistent).
* Undo / redo buttons in the panel, one history for the layer order
  (including reorders made in QGIS's own Layer Order panel), separate from QGIS's undo
  (Ctrl+Z stays QGIS's); layers QGIS owns are never undone.
* Saved with the project.

User guide: [`docs/DOCUMENTATION.md`](docs/DOCUMENTATION.md).

## Install

1. `make zip` → `dist/advanced_layer_order-<VERSION>.zip`
2. QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**

## Develop

```bash
make test        # unit tests, no QGIS needed (PyQt6; QT_API=pyqt5 make test for PyQt5)
make qgis-test   # end-to-end checks under every QGIS installed (official app and MacPorts; or $QGIS_APPS)
make lint        # ruff
make check       # metadata / packaging checks
make zip         # versioned plugin zip in dist/
```

The code is a strict model / view / controller split — the View only
renders and reports intents, the Controller is the only piece that talks to
QGIS. Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) before changing it.
Manual acceptance: [`docs/TEST_SCENARIO.md`](docs/TEST_SCENARIO.md).

Releasing: [`docs/RELEASING.md`](docs/RELEASING.md). Version history:
[`VERSION.md`](VERSION.md).

## History

Advanced Layer Order is the continuation of **Layer Order Plus**
by **Samuel Kultz (Kultz Engenharia)**, published as a new plugin and
maintained by **Sylvain Souche** with his agreement.

> Huge thanks to Sam for the original plugin that provided me the feature
> when I needed it. I just had to port it over to QGIS 4 for stability
> reasons. — Sylvain

* **1.0.0** — *Layer Order Plus*, the original plugin by Samuel Kultz:
  https://github.com/samkultz/layer_order_plus_plugin ·
  https://plugins.qgis.org/plugins/layer_order_plus/ (QGIS 3).
* **1.0.x – 1.1.0** — QGIS 4 / PyQt6 port, undo restoring the
  map order, icons, visibility, persistence schema.
* **1.2.x** — rewritten as a model / view / controller design; sync with
  QGIS's Layer Order panel.
* **1.3.x** — move up / down, packaging for plugins.qgis.org, manual test
  plan with verifiable data; published as *Advanced Layer Order*; runs on
  QGIS 3.40 / 3.44 again as well as QGIS 4. Details
  in [`VERSION.md`](VERSION.md).

Repository: https://github.com/SylvainSouche/qgis_advanced_layer_order ·
MIT — see [`LICENSE`](LICENSE).
