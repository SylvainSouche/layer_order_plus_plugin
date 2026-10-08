# Layer Order Plus — Manual acceptance test

What the automated checks can't do: real mouse drags, QGIS's real Layer
Order panel, and real projects. Everything else is covered by `make test`
and `make qgis-test` (see [`ARCHITECTURE.md`](ARCHITECTURE.md#tests)) —
run those first.

**Setup:** QGIS 4.x, a project with five point layers **A B C D E**, both
**Layer Order Plus** and QGIS's **Layer Order** panel visible, *Verbose
logging* on (Log Messages → LayerOrderPlus). Note the build from
`VERSION`. Mark each row Pass / Fail.

---

## 1. Load and control

| # | Action | Expected |
|---|---|---|
| 1.1 | Install the zip, enable the plugin | Dock appears, no error in the log |
| 1.2 | Open the project, check **Control rendering order** in Plus | Both panels show the same order; map follows it |
| 1.3 | Uncheck it, then check it in **QGIS's** panel instead | Plus checkbox follows; both panels show the same order |

## 2. Groups

| # | Action | Expected |
|---|---|---|
| 2.1 | Select A, B → **Create group** "AB"; same for C, D → "CD" | Groups take the place of their layers |
| 2.2 | Select AB and CD → **Create group** "ABCD" | ABCD holds AB then CD; E stays below |
| 2.3 | Rename, then delete a group (toolbar and context menu) | Contents take the group's place |
| 2.4 | Collapse ABCD with the arrow, double-click, ←/→ | Expands/collapses each time; stays so after other edits |

## 3. Drag and drop in Plus (mouse)

| # | Action | Expected |
|---|---|---|
| 3.1 | Drag E above / below various layers, inside and outside groups | Lands exactly at the indicator; QGIS's panel and map follow |
| 3.2 | Drag E **onto** group CD | E becomes CD's last layer |
| 3.3 | Drag E **onto** layer C | New group holding C then E, at C's place |
| 3.4 | Drag CD into the empty area below the list | CD at the bottom |
| 3.5 | Multi-select (Ctrl+click in reverse order) two layers, drag | Both move, in on-screen order |
| 3.6 | Try to drag ABCD onto AB | Refused (no-drop cursor), nothing changes |
| 3.7 | After each drop | Scroll position and other groups' expansion unchanged; moved items selected |

## 3b. Move up / down

| # | Action | Expected |
|---|---|---|
| 3b.1 | Select B → toolbar ▲ | B above A inside AB; map follows |
| 3b.2 | ▲ again | Nothing moves (B is first in AB) |
| 3b.3 | Select C and D (all of CD) → **Ctrl+↓** / **⌘↓** | Nothing moves: the block already fills CD |
| 3b.4 | Select group AB → ▼ | AB moves below CD, inside ABCD |
| 3b.5 | Ctrl+Z | Undoes the last step only |

## 4. QGIS's Layer Order panel

| # | Action | Expected |
|---|---|---|
| 4.1 | Drag E between A and B | E joins AB |
| 4.2 | Drag E to the very top | E stays in AB (first); no other layer changes group |
| 4.3 | Drag E back between A and B, then after D, then between C and D | Only E's group changes; A–D never move out of their groups |
| 4.4 | Swap A and B | Still both in AB |

## 5. Visibility

| # | Action | Expected |
|---|---|---|
| 5.1 | Uncheck a layer in Plus / in the Layers panel | The other panel follows; map updates |
| 5.2 | Uncheck group AB in Plus | A and B unchecked everywhere |
| 5.3 | Put D in a Layers-panel group and uncheck that group | D shows unchecked in Plus |
| 5.4 | Check D in Plus | The Layers-panel group is checked again; D shows |

## 6. Layers added / removed

| # | Action | Expected |
|---|---|---|
| 6.1 | Add a layer with C selected in the Layers panel | Appears next to C in Plus |
| 6.2 | Remove the last layer of a group (remove-empty on / off) | Group removed / kept |

## 7. Undo and persistence

| # | Action | Expected |
|---|---|---|
| 7.1 | Ctrl+Z / Ctrl+Y with focus on the map and on the Plus tree | One step each, order and map follow |
| 7.2 | Start editing a layer, Ctrl+Z | Digitizing undo, not layer order |
| 7.3 | Add a layer, then Ctrl+Z | The layer stays |
| 7.4 | Save, close, reopen the project | Same groups, order, expansion; history empty |
