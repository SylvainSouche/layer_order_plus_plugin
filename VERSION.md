# Version history

Detailed history of Layer Order Plus. `metadata.txt` only lists the macro
versions; the current version number is in [`VERSION`](VERSION). Commit
messages hold the full reasoning behind each fix.

## 1.3 — Move up / move down; review completed: strict MVC, Qt model/view tree, sync with QGIS's Layer Order panel, packaging for plugins.qgis.org

*1.3.0 – 1.3.2*

* **1.3.2** — Opening a project no longer inherits the previous project's groups: the old project's layer removals were mirrored and the leftover tree saved into the project being opened (now ignored from `QgsProject.aboutToBeCleared`). Manual test plan rewritten with test data that shows the drawing order on the map (`make test-data`), expected results for Plus, QGIS's Layer Order panel and the map, and a headless replay that verifies them. Release metadata: maintained by Sylvain Souche with Samuel Kultz's agreement (author, email, LICENSE, README), published as experimental.
* **1.3.1** — The panel no longer closes itself when a project is closed or when it is tabbed behind another panel (the plugin-menu toggle reacted to "hidden" as if it meant "closed"; it is now the dock's own toggle action). The dock has an object name, so QGIS restores its place and visibility between sessions.

**1.3.0**

* Move up / Move down (toolbar, context menu, Ctrl+↑ / Ctrl+↓): each selected item swaps with the nearest unselected sibling, blocks move together, items never leave their group; one undo step.
* Packaging for plugins.qgis.org: the zip contains only runtime files under an explicit folder name, without executable bits or hidden/dev files; invalid `category` removed; `qgisMinimumVersion=4.0`, `qgisMaximumVersion=4.99`.
* Changelog split: detailed history in `VERSION.md`, macro versions in `metadata.txt`.
* Review pass over code and documentation (see the 1.3.0 commit messages).

## 1.2 — Rewritten as model / view / controller; two-way sync with the Layers panel and QGIS's own Layer Order panel; undo that never fights QGIS

*1.2.0 – 1.2.30*

* **1.2.30** — The tree is now a Qt model/view (QTreeView + item model) instead of a QTreeWidget rebuilt on every change: selection, expansion and scroll survive edits natively; drops and checkbox clicks reach Plus as intents through the item model and never change the tree themselves. In-QGIS end-to-end checks (make qgis-test)
* **1.2.29** — Cleanup — name filter removed; one undo keyboard path (Ctrl+Z/Ctrl+Y/Ctrl+Shift+Z via the main window, menu actions without competing shortcuts); standard Python logging; simpler icons; ruff lint; CI on every branch
* **1.2.28** — Fix layers thrown out of their group after drags in the stock Layer Order panel — its drag runs its own event loop, so the deferred reconcile fired mid-drag and lost which layer was dragged; changes from that panel are now handled synchronously in arrival order with the exact dragged layer. No apply is scheduled when QGIS already has Plus's order. Ambiguous moves without a hint prefer the reading that regroups the fewest layers
* **1.2.27** — Fix native Layer Order panel showing a different order after activating control (from either checkbox) or loading a project — QGIS's panel only refreshes on customLayerOrderChanged, which is not emitted when the order is unchanged or when control is toggled; the Controller now forces that refresh
* **1.2.26** — Strict MVC boundaries — View only renders and reports intents (drops, checkbox clicks, expand arrows, settings boxes are no longer applied by Qt itself); ViewController turns intents into Model transactions; Controller is QGIS↔Model only (no View access); project load/persistence moved to the Controller. Fixes: remove-empty setting from the project now applies; undo never removes layers QGIS has, resurrects deleted ones, or changes visibility; checking a layer in a hidden QGIS group now shows it; new layers are placed next to their Layers-panel neighbour. docs/ARCHITECTURE.md
* **1.2.25** — Fix sync with the stock Layer Order panel — its two-step drag (layer briefly listed twice) was reconciled mid-way, duplicating layers and destroying groups; reconcile is now coalesced to the final state and rewritten (reconcile.py) to only re-place the dragged layer, keeping every group; Plus edits no longer reloaded twice by QUndoStack.push
* **1.2.24** — Fix drag-drop View/Model desync — atomic sibling-anchored Model.move_items (multi-item drops no longer scramble), drops in empty space no longer let Qt move items behind the Model, one View rebuild + one undo step per user action, expand/collapse synced to Model, tri-state group echo no longer re-checks layers, apply guard no longer leaks (QGIS skips the signal for an unchanged order)
* **1.2.23** — Logging only: fix the off-by-one method tags in model.py traces
* **1.2.22** — Force-apply the Plus order to QGIS on project load (QGIS kept a stale custom order from a previous session)
* **1.2.21** — Fix reconciliation firing after Plus drag-drop — counter-based guard replaces 200ms timer; deduplicate layers in customLayerOrder
* **1.2.20** — Fix move_item pruning — empty groups survive moves (not pruned); only layer deletion prunes
* **1.2.19** — Layer-tagged logging — [M] Model, [V] View, [VC] ViewController, [C] Controller, [P] Plugin, [TW] TreeWidget
* **1.2.18** — Fix reconciliation firing after Plus drag-drop — _in_apply guard stays active 200ms to catch queued customLayerOrderChanged signals
* **1.2.17** — Fix drag-drop — DROP_ON group inserts at end (not top); DROP_ABOVE uses reverse order for multi-item; add drop logging
* **1.2.16** — Fix recursive reconciliation — compute group spans from ALL direct children (layers + subgroups) recursively
* **1.2.15** — Redesign reconciliation — group membership by contiguity, no group deletion, no warning popup
* **1.2.14** — Fix BUG-1 (move-to-bottom reverses order) + BUG-4 (move_item does not prune empty groups) + 23 new scenario tests
* **1.2.13** — Fix undo intercepting Ctrl+Z (handle KeyPress too) + suppress reconcile during layer add/remove/apply
* **1.2.12** — Fix create-group-from-multiple-groups — new group was created INSIDE the last selected group instead of as a sibling
* **1.2.11** — Add reconciliation logging — verify (C,(A,B))→(A,C,B) removes only inner group; (C,(A,B))→(C,(B,A)) reorders within group
* **1.2.10** — Fix within-group reorder from native panel + smarter group deletion (only non-contiguous groups)
* **1.2.9** — Minimal group reconciliation (only remove conflicting groups) + push Plus order on control activate
* **1.2.8** — Fix delete-button grayed + initial order mismatch + reconciliation warning popup + undo/redo panel update
* **1.2.7** — CRITICAL fix — undo.py NameError broke every drag-drop/layer-add; fix reconcile feedback loop; reduce eventFilter log noise
* **1.2.6** — Verbose logging checkbox + fix View self-mutation during DnD + sync with stock Layer Order panel
* **1.2.5** — Fix drag-drop corruption (defer to next event loop) + effective visibility (consider parent group)
* **1.2.4** — Fix DeepSeek review findings — autosave, insertion order, Ctrl+Y, filter after rebuild, Edit menu, group checkboxes, selection after drop, dead code cleanup
* **1.2.3** — Fix wrong layer count/missing names/broken icons — removed redundant JSON round-trip on project load; added null-icon guard
* **1.2.2** — Fix pyqtSignal AttributeError on load — undo/redo shortcut signals must be class-level, not instance-level
* **1.2.1** — Full MVC refactor — view.py + view_controller.py + controller.py + plugin.py orchestrator; dock.py is now a thin compat shim
* **1.2.0** — MVC refactor step 1 — extract LayerOrderModel (plain Python, Qt-free, 76 unit tests)

## 1.1 — Quality overhaul: visibility checkboxes, rename sync, persistence schema, empty-group cleanup, module split, unit tests

*1.1.0*

* **1.1.0** — All QualityOverhaul Phase 1+2+3+4 items shipped — refactor + 54 unit tests included

## 1.0 — QGIS 4 / PyQt6 port of the original plugin (1.0.0, Samuel Kultz); undo restores the map order; icons; group tools

*1.0.0 – 1.0.27*

* **1.0.27** — Unit tests (54 tests across tree_utils / serialize / filter / icons) + CI runs pytest + Makefile test target (closes QualityOverhaul 4.4)
* **1.0.26** — Refactor dock.py into icons.py / tree_utils.py / tree_widget.py / undo.py; dedup _add/_insert_layer_item (closes QualityOverhaul 4.1, 4.2)
* **1.0.25** — Name substring filter field (closes QualityOverhaul 3.3)
* **1.0.24** — Per-layer + per-group visibility checkboxes, bi-directional sync with Layers panel (closes QualityOverhaul 3.1)
* **1.0.23** — Drop-rule tooltip on tree (closes QualityOverhaul 3.5)
* **1.0.22** — Context menu Expand/Collapse + Move to top/bottom (closes QualityOverhaul 3.2)
* **1.0.21** — Empty-group cleanup on layer remove (closes QualityOverhaul 2.2)
* **1.0.20** — Layer rename sync via nameChanged (closes QualityOverhaul 2.1)
* **1.0.19** — JSON schema version field + restore group expanded state (closes QualityOverhaul 2.4)
* **1.0.18** — Initial layer order seed from customLayerOrder/layerOrder (closes QualityOverhaul 1.2)
* **1.0.17** — Dual-undo guard in _on_rows_moved (closes QualityOverhaul 1.1/4.5)
* **1.0.16** — (docs) Reconcile QUALITY_OVERHAUL with actual code state
* **1.0.15** — Diagnostic logging in layer add/remove/apply paths (helps diagnose stale-install + silent-failure cases)
* **1.0.14** — Control rendering order checkbox; safe expand after drop
* **1.0.13** — Remove leftover rename-timer hook (load crash)
* **1.0.12** — Rename via toolbar/context (no double-click edit); multi-delete; expand on double-click group
* **1.0.11** — Unique group names; expand new group and ancestors after drop
* **1.0.10** — Import QUndoGroup from QtGui (PyQt6)
* **1.0.9** — Drop on layer creates a new group containing both items
* **1.0.8** — Integrate undo/redo with Edit menu and app shortcuts
* **1.0.7** — Bundled SVG icons for tree and toolbar buttons (add/delete group)
* **1.0.6** — Reliable folder/layer icons (Qt style fallbacks + tree icon size)
* **1.0.5** — Makefile and VERSION single source of truth; versioned zip name
* **1.0.4** — Group folder icons and layer-type icons; documentation suite
* **1.0.3** — Undo restores map custom layer order, not only panel tree
* **1.0.2** — Full Qt6 enum migration
* **1.0.1** — QUndoCommand/QUndoStack import fix for PyQt6
* **1.0.0** — Initial upstream release (Samuel Kultz)
