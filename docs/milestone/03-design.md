# Task 3: Penpot interface design

Date: 2026-10-08. Inputs: `00-brief.md`, `02-plan.md` (D11, D14, D18, §4, §8, §9), `01-audit.md` §7, `app/window.py` (current look).
Output of this task: the Penpot file, 28 PNG exports in `docs/milestone/design/`, and this document. T8 (library, inspector, `theme.py`) and T9 (canvas, results) implement from these.

## 1. Penpot file

| Item | Value |
|---|---|
| File | "New File 3" (open file of the connected plugin). **Not renamed**: `penpot.currentFile.name` is read-only in the plugin API (`TypeError: setting getter-only property "name"`). Rename it to "Sim Workspace" by hand in the Penpot UI. |
| D18 (reuse a "Sim" file) | The Penpot MCP exposes only the one connected file, no project listing. There was nothing to reuse, so the design was built in this file. |
| Pages | `Foundations` (3 frames), `Components` (6 section boards holding the 79 main components), `Screens` (2 frames), `States` (18 frames; `results-running-blocked` added in T4). |
| Library assets | 55 colours (`app-bg`, `wire`, ...), 10 typographies (`text/heading` ... `text/code`), token sets `sim/core` (55 colour tokens, `color.*`) and `sim/layout` (spacing, radius, sizing, font size and weight tokens: 26). |
| Components | 79 Penpot components with path names (`Button / Primary / Default`). Screens use **instances** of them (top bar, buttons, search, nav items, toggle, cards, badges, field rows, pin rows, banners, state badges, zoom control, problem rows, dialogs, status bar). One Penpot variant group would have been tidier, but separate components per state were chosen because they instantiate and nest reliably through the plugin API. |
| Fonts | Noto Sans (Google font in Penpot, installed locally per plan) and Noto Sans Mono for ids and code. |

Editing tip for anyone changing the file by script: a page must be the active page and stay open for about a second before text layout (and exports) are correct. Texts on inactive pages render with width 1.

## 2. Tokens for `app/theme.py` (D14)

Everything is flat, light chrome, no shadows, no gradients, no blur (plan risk 8). Colours marked **kept** come from `window.py` and must not change.

### 2.1 Colours

| Constant (suggested) | Penpot token | Hex | Role |
|---|---|---|---|
| `CANVAS_BG` | `color.canvas` | `#FFFFFF` | Canvas background (kept: white) |
| `GRID` | `color.grid` | `#E6E9EE` | 20 px grid lines, 1 px |
| `INK` | `color.ink` | `#222222` | Part strokes and canvas labels (kept) |
| `WIRE` | `color.wire` | `#1A5FB4` | Wire (kept) |
| `SEL` | `color.selection` | `#E07000` | Selection outline, selected wire, armed card (kept) |
| `SEL_FILL` | `color.selection-fill` | `#FFE6BE` at 55 % | Selection box fill (kept: `rgba(255,230,190,140)`) |
| `HOVER` | `color.hover` | `#2A7FFF` | Hover part, pin halo, wire hover, hover card border (kept) |
| `OK_PIN` | `color.pin-wired` | `#2FA82F` | Wired pin fill (kept) |
| `OK_PIN_RING` | `color.pin-wired-ring` | `#1B6E1B` | Wired pin ring (kept, `window.py:768`) |
| `OPEN_PIN` | `color.pin-open` | `#D02020` | Open pin: hollow ring (kept) |
| `FINDING` | `color.finding` | `#E0A000` | Core-finding ring on pins (kept, `window.py:744`) |
| `BAD` | `color.bad` | `#B00020` | Errors (kept) |
| `LED_OFF` / `LED_ON` / `LED_GLOW` | `color.led-off` / `led-on` / `led-glow` | `#5A5A5A` / `#FF4620` / `#FF8C3C` | LED glow (kept: `(90,90,90)`, `(255,70,30)`, `(255,140,60)`) |
| `APP_BG` | `color.app-bg` | `#F4F6F8` | Window background behind panels |
| `PANEL` | `color.panel` | `#FFFFFF` | Panels, cards, top bar, inputs |
| `PANEL_ALT` | `color.panel-alt` | `#F8F9FB` | Table header, disabled button, zebra rows |
| `BORDER` | `color.border` | `#D9DEE5` | Panel and card borders |
| `BORDER_STRONG` | `color.border-strong` | `#B8C0CC` | Input and secondary-button borders, toggle track off |
| `DIVIDER` | `color.divider` | `#E8EBEF` | Separators and table row lines |
| `TEXT` | `color.text` | `#1F2933` | Primary text |
| `TEXT_2` | `color.text-secondary` | `#52606D` | Secondary text, labels, icons |
| `TEXT_MUTED` | `color.text-muted` | `#8A94A0` | Hints, placeholders, units |
| `TEXT_DISABLED` | `color.text-disabled` | `#A8B0BA` | Disabled text and icons |
| `DISABLED_BG` | `color.disabled-bg` | `#EEF1F4` | Disabled fields and buttons |
| `PRIMARY` | `color.primary` | `#1A5FB4` | Primary button, selected nav, links, focus border (equals `WIRE`) |
| `PRIMARY_HOVER` | `color.primary-hover` | `#174F96` | Primary button hover |
| `PRIMARY_SOFT` | `color.primary-soft` | `#E7F0FB` | Selected nav item and selected card fill, secondary-button hover |
| `ON_PRIMARY` | `color.on-primary` | `#FFFFFF` | Text on primary |
| `SCRIM` | `color.overlay` | `#1F2933` at 45 % | Behind modal dialogs |
| `STATE_IDLE` | `color.state-idle` | `#666666` | Run state idle (kept) |
| `STATE_RUNNING` | `color.state-running` | `#1A5FB4` | Run state running (kept) |
| `STATE_COMPLETED` | `color.state-completed` | `#1B6E1B` | Run state completed (kept) |
| `STATE_FAILED` | `color.state-failed` | `#B00020` | Run state failed and blocked (kept) |
| `STATE_STALE` | `color.state-stale` | `#A05000` | Run state stale (new; the existing warning-text colour) |
| `RDY_SIM_FG` / `_BG` | `color.rdy-sim-fg` / `-bg` | `#1B6E1B` / `#E4F4E4` | Readiness "Simulated" |
| `RDY_IFACE_FG` / `_BG` | `color.rdy-iface-fg` / `-bg` | `#0B5CAD` / `#E3EEFA` | Readiness "Interface only" |
| `RDY_REF_FG` / `_BG` | `color.rdy-ref-fg` / `-bg` | `#52606D` / `#ECEFF3` | Readiness "Reference only" |
| `RDY_PH_FG` / `_BG` | `color.rdy-ph-fg` / `-bg` | `#6B3FA0` / `#EFE8F8` | Readiness "Placeholder" (missing or incompatible definition) |
| `RDY_BAD_FG` / `_BG` | `color.rdy-bad-fg` / `-bg` | `#B00020` / `#FBE4E8` | Readiness "Invalid" |
| `INFO_FG` / `_BG` | `color.info-fg` / `-bg` | `#0B5CAD` / `#E3EEFA` | Info banner |
| `WARN_FG` / `_BG` / `_BORDER` | `color.warn-fg` / `-bg` / `-border` | `#8A4B00` / `#FFF1DC` / `#E0A000` | Warning banner, success-with-exclusion message, findings text |
| `ERR_FG` / `_BG` / `_BORDER` | `color.err-fg` / `-bg` / `-border` | `#B00020` / `#FBE4E8` / `#D02020` | Error banner, failure message, invalid field border |
| `INVALID_BG` | `color.invalid-bg` | `#FFF0F0` | Invalid field background (kept, `window.py:854`) |

Readiness label text (use exactly these): `simulated` → "Simulated", `interface` → "Interface only", `placeable` → "Reference only", placeholder → "Placeholder", invalid definition → "Invalid" (never shown on a card: invalid definitions are not loaded; the badge exists for diagnostics views).
State badge text: idle "Idle", running "Running", completed "Completed", failed "Failed", stale "Out of date", blocked "Blocked".

### 2.2 Type scale (Noto Sans; Qt: `setPixelSize(px)`, or pt = px x 0.75 rounded)

| Style | px | pt | Weight (Qt) | Line height | Use |
|---|---|---|---|---|---|
| `heading` | 20 | 15 | 600 (DemiBold) | 1.3 | Document-level titles (none in the workspace; docs and empty states) |
| `title` | 16 | 12 | 600 | 1.3 | Panel titles (Components, inspector header name), dialog titles |
| `body` | 13 | 10 | 400 | 1.4 | Fields, text, descriptions. App default font |
| `body-medium` | 13 | 10 | 500 (Medium) | 1.4 | Buttons, nav items |
| `body-strong` | 13 | 10 | 600 | 1.4 | Card names, selected nav, empty-state titles |
| `small` | 12 | 9 | 400 | 1.4 | Hints, tags, canvas labels (today's 9 pt), messages |
| `small-strong` | 12 | 9 | 600 | 1.4 | State badges, results title |
| `caption` | 11 | 8 | 400 | 1.4 | Units, status bar, field messages, readiness badge text at 600 |
| `overline` | 11 | 8 | 600, uppercase, +0.5 px tracking | 1.4 | Section labels (Parameters, Pins) |
| `code` | 12 | 9 | 400, Noto Sans Mono | 1.4 | Ids, codes, readouts, developer text |

### 2.3 Spacing, radii, sizes

| Token | px | Use |
|---|---|---|
| `space.xs` / `sm` / `md` / `lg` / `xl` / `xxl` | 4 / 8 / 12 / 16 / 24 / 32 | Library panel padding 12, inspector padding 16, card padding 10 |
| `radius.sm` | 4 | Fields, badges, tags |
| `radius.md` | 6 | Buttons, nav items |
| `radius.lg` | 8 | Cards, icon tiles, banners, zoom control |
| `radius.xl` | 12 | Dialogs |
| `radius.pill` | 999 | State badge, toggle, tag chips (10 on a 20 px chip) |
| `icon.inline` / `toolbar` / `card` | 16 / 20 / 40 (tile 44) | Icon stroke 1.5, round caps, current text colour |

| Control | Size |
|---|---|
| Window | 1280 x 800 default, 1024 x 640 minimum |
| Top bar / status bar | 48 / 24 px high |
| Library panel / inspector | 280 / 320 px wide, full height between bars |
| Results dock | 248 px high under the canvas, 430 px results + problems column |
| Button, search and inspector field | 32 px high. Top-bar fields 28 px (label 62 px, input 84 px) |
| Nav item | 256 x 28 |
| Component card | 256 x 84 |
| Canvas | 20 px grid, snap 20 px |
| Pin | dot 9 px diameter, 1.5 px stroke; hover halo 18 px; finding ring 22 px, 2 px; hit radius 10 screen px |
| Wire / part stroke | 2 px; hover and selected wire 3 px; selected part 3 px |
| Selection box | part rect + 6 px, dashed 1.5 px orange, fill `SEL_FILL` |
| Zoom control | 168 x 32 (− / % / + / fit) bottom-left of the canvas, 12 px inset |
| Part toolbar (rotate, delete) | 76 x 32, 16 px above the selection box |

## 3. Frames

All PNGs are in `docs/milestone/design/`. Frame names equal file names.

### Foundations (page `Foundations`)

| Frame | Purpose | PNG |
|---|---|---|
| `foundations-color` | Every colour token with hex, grouped canvas / chrome / action / run state / readiness / feedback | `design/foundations-color.png` |
| `foundations-type` | Type scale with px, pt, weight | `design/foundations-type.png` |
| `foundations-spacing-radii-icons` | Spacing, radii, icon sizes, control and layout sizes | `design/foundations-spacing-radii-icons.png` |

### Components (page `Components`, sections of main components)

| Section | Contents | PNG |
|---|---|---|
| Buttons | Primary / Secondary x Default, Hover, Disabled; Icon button Default, Hover, Disabled; icon glyph buttons (new, open, save, redo, rotate, delete, search, fit) | `design/components-buttons.png` |
| Fields, navigation, tables | Search (Empty, Focused, Typed), inline top-bar field (Valid, Invalid, Disabled), Toggle (Off, On), Nav item (Default, Hover, Selected), FieldRow (Number, Choice, Invalid, ReadOnly), PinRow (Header, Wired, Open) | `design/components-fields-nav-tables.png` |
| Badges, banners, status | Readiness badges (5), state badges (6), Tag chip, Count pill, Banner Info / Warning / Error in 256 and 416 widths, ProblemRow Error / Warning | `design/components-badges-banners-status.png` |
| Component library cards | Card Default, Hover, Selected, Armed, MissingIcon, Skeleton | `design/components-library-cards.png` |
| Canvas controls, top bar, status | Zoom control, Part toolbar, Status bar, TopBar base, SimControls x 7 states | `design/components-canvas-topbar-status.png` |
| Dialogs, headers | Plain primary button, Inspector header, Dialog UnsavedChanges, Dialog OpenFailed | `design/components-dialogs-headers.png` |

### Screens (page `Screens`)

| Frame | Purpose | PNG |
|---|---|---|
| `screen-workspace-led` | 1280 x 800 workspace: top bar with file / undo / redo and simulation controls (Completed state), library left (search, categories with illustrative counts, Simulated-only toggle, four cards, none hovered, armed or selected; the Arduino card is the interface-only sample), canvas centre (LED circuit: battery, resistor R1 selected with part toolbar, LED D1 glowing with 9.18 mA, ground, wires, junction; ESP32 DevKitC box with 38 pins and "Interface only" badge; zoom control), results dock (signal picker, play + slider, plot with cursor, readout, stats, "ok, 511 points; not simulated: U1"), Problems list (one NOT-SIMULATED warning), inspector right (R1: header, resistance field, pins, docs), status bar | `design/screen-workspace-led.png` |
| `interaction-spec` | Input / result / feedback table for every interaction | `design/interaction-spec.png` |

### States (page `States`)

| Frame | Plan §9 item | PNG |
|---|---|---|
| `library-empty-states` | Empty search ("No components match 'xyz'", Clear search), empty category (filter hides all, Show all), loading skeleton | `design/library-empty-states.png` |
| `library-card-states` | Card default, hover, selected, armed (placing), missing icon (designator-letter fallback); readiness Simulated / Interface only / Reference only; skeleton; anatomy note | `design/library-card-states.png` |
| `library-catalog-problems` | Banner "2 definitions could not be loaded", collapsed and expanded (file, code, message) | `design/library-catalog-problems.png` |
| `library-details` | Details view for a simulated part (Diode: pins, parameter table with defaults and units, limitations, docs, id and version) and an interface-only part (ESP32: 38 pins truncated, no parameters, limitations) | `design/library-details.png` |
| `canvas-part-states` | Part idle, hover, selected (part toolbar with rotate and delete), dragging (ghost, snap guides) | `design/canvas-part-states.png` |
| `canvas-pin-states` | Pin open, wired, hover (halo + name tooltip), pending wire (rubber band to cursor), junction, core-finding ring + tooltip, hit area, ground | `design/canvas-pin-states.png` |
| `canvas-wire-states` | Wire idle, hover, selected (end handles, click hint) | `design/canvas-wire-states.png` |
| `canvas-empty-hint` | Empty-canvas hint card replacing the `HINT` strip, plus zoom control | `design/canvas-empty-hint.png` |
| `canvas-placing-armed` | Armed card + ghost part on the canvas, tooltip "Click to place Resistor, Esc to cancel" | `design/canvas-placing-armed.png` |
| `canvas-led-glow` | LED glow at 0, 0.8, 4, 9.2, 15 mA with current label | `design/canvas-led-glow.png` |
| `canvas-unsupported-placeholder` | ESP32 with "Interface only" badge and limitation tooltip; placeholder parts (dashed box, "?", "Placeholder" badge) for missing and incompatible definitions | `design/canvas-unsupported-placeholder.png` |
| `canvas-navigation` | Zoom control at 200 %, zoom-in tooltip, scrollbars, pan hand cursor, zoom and pan rules | `design/canvas-navigation.png` |
| `inspector-part-states` | Voltage source in PULSE mode (choice + 7 `when`-dependent number fields), Resistor with an invalid field and message, LED with limitations block and docs | `design/inspector-part-states.png` |
| `inspector-other-states` | Nothing selected (circuit and simulation summary), wire selected (endpoints, node, Plot node, Delete), placeholder selected (read-only parameters, reason banner, pins from connections), unsupported ESP32 selected (pins, limitations, docs, "Interface only") | `design/inspector-other-states.png` |
| `sim-controls-states` | Top bar with simulation controls: idle (valid), invalid field, running, completed, failed, stale, blocked by pre-check (with the messages) | `design/sim-controls-states.png` |
| `results-states` | Completed with excluded part, failure (message, no plot), not run yet, signal picker open (grouped), "Show details" expanded (core findings + developer text), stale (dimmed + banner), pause specimen | `design/results-states.png` |
| `results-running-blocked` (T4) | Results while Running (controls locked, previous results hidden, progress bar) and Blocked by pre-check (run did not start, error banner, no plot) | `design/results-running-blocked.png` |
| `dialogs` | Unsaved changes, open failed, opened-with-placeholders banner (non-blocking) with status-bar message | `design/dialogs.png` |

## 4. Component inventory (Penpot main components)

| Group | Components | Count |
|---|---|---|
| Buttons | `Button/Primary/{Default,Hover,Disabled}` (with play icon), `Button/PrimaryPlain/{Default,Hover,Disabled}`, `Button/Secondary/{Default,Hover,Disabled}`, `Button/Icon/{Default,Hover,Disabled}`, `Button/Icon/Glyph/{new,open,save,redo,rotate,trash,search,fit}` | 20 |
| Fields | `Field/Search/{Empty,Focused,Typed}`, `Field/Inline/{Valid,Invalid,Disabled}`, `Toggle/{Off,On}`, `Nav/Item/{Default,Hover,Selected}`, `FieldRow/{Number,Choice,Invalid,ReadOnly}`, `PinRow/{Header,Wired,Open}` | 18 |
| Badges and messages | `Badge/Readiness/{Simulated,InterfaceOnly,ReferenceOnly,Placeholder,Invalid}`, `Badge/State/{Idle,Running,Completed,Failed,Stale,Blocked}`, `Tag/Chip`, `Count/Pill`, `Banner/{Info,Warning,Error}`, `Banner/Wide/{Info,Warning,Error}`, `ProblemRow/{Error,Warning}` | 21 |
| Library | `Card/{Default,Hover,Selected,Armed,MissingIcon,Skeleton}` | 6 |
| Canvas and bars | `Zoom/Control`, `Canvas/PartToolbar`, `StatusBar`, `TopBar/Base`, `SimControls/{Idle,Invalid,Running,Completed,Failed,Stale,Blocked}` | 11 |
| Inspector and dialogs | `Inspector/Header`, `Dialog/UnsavedChanges`, `Dialog/OpenFailed` | 3 |
| Total | | 79 |

Notes for T8/T9:
- `Card` has no per-part glyph in the component: the tile carries a placeholder symbol that screens hide and replace. In the app the tile shows `icon_pixmap(defn)`: `icon`, else the rendered `symbol`, else the letter fallback (`MissingIcon`).
- The part symbols and icons drawn in Penpot are illustrative vector shapes (paths). T5 owns the real `symbol.svg` files from the existing painters. The Penpot symbols use the same 60 x 30 box and pin positions.
- Tooltips, the signal-picker list, the placeholder part and the finding ring are drawn shapes in the state frames, not components. They are fully specified by the token table.

## 5. Interaction spec

Full table: `design/interaction-spec.png`. Summary (all of it exists in the plan; there is no multi-select, copy/paste, breadboard, code editor or MCU panel):

| Input | Result |
|---|---|
| Click a library card | Arms placement (card orange with "Placing", crosshair cursor, status "Placing X: click the canvas. Esc cancels."). Click again disarms. |
| Click the canvas while armed | Places one part at the click point snapped to 20 px, selects it, disarms. This matches the current app (`set_tool(None)` after placing). |
| Drag a card onto the canvas | Same result, at the drop point (mime `application/x-sim-component` = id). |
| Click part / wire / empty canvas | Select part (inspector part view) / select wire (wire view, plot its node) / clear (inspector "Nothing selected"). |
| Drag a selected part | Move, snapped; wires follow; one undo step. |
| R | Rotate 90 degrees clockwise about the body centre. The part toolbar has the same Rotate and Delete as buttons. |
| Del / Backspace | Delete the selected part (and its wires) or wire. Ctrl+Z restores. |
| Drag from a pin to a pin, or click pin then pin | Create a wire. Pending wire is a dashed rubber band, target pin gets the hover halo. |
| Esc | Cancels in this order: armed placement, pending wire, open dropdown or dialog, then clears selection. In the search field it clears the text. |
| Ctrl+wheel, Ctrl+= / Ctrl+- / Ctrl+0 | Zoom 25-400 % anchored at the cursor / zoom / fit all parts. Plain wheel scrolls. |
| Middle-drag, or Space + drag | Pan. Cursor: open hand. |
| Right-click a part | Rotate, Delete, Disconnect all. |
| F5, Ctrl+F5, Reset, Ctrl+Z/Y, Ctrl+S/O/N | As today (Run, Re-run = reset + run, Reset clears results). |

## 6. Review and corrections made during the design pass

The brief asks for a review before implementation. These defects were found in the first draft of the frames and fixed:
- Results had no place for core findings, developer details, stale results or pause. Added `results-states` variants.
- The library details view reused the inspector pin row, which shows wired/open. That is meaningless for an unplaced part. The state column is hidden there.
- Inspector header: a long name plus the readiness badge overlapped. The header is now 76 px high with the badge on its own row.
- Top bar overflowed with the long "results out of date" label. The state badge says "Out of date"; the full sentence is the banner and tooltip text.
- Placement behaviour was ambiguous (stay armed or not). Aligned to the current app: one placement per arming.
- Sample file names use `.json`, the app's real extension (`QFileDialog` filter `*.json`).
- Armed card originally printed a hint over the badge row. Replaced by a "Placing" pill.

## 7. Plan §9 items not fully designed

| Item | Status |
|---|---|
| Penpot prototype interactions (clickable flows) | Not created. The interaction spec table and the state frames carry the behaviour. |
| Dragging state of a part | Designed as a ghost + snap guides, a static depiction. |
| Real icon SVGs | Not produced (T5 converts the painters; library glyphs in Penpot are illustrative). |
| ESP32 pin labels | The 38 labels on the Penpot board were typed from the commonly published ESP32-DevKitC V4 pinout, not transcribed from the Espressif guide. T7 must transcribe from the cited source; do not copy from the picture. |
| Sample texts | Diode limitations, descriptions of cards, the netlist in "developer details", "NO-DC-PATH" wording, and the 2 broken packages in the problems banner are samples for layout. Real text comes from `component.toml` and the core. |

Everything else in plan §9 points 1-8 has a frame.

## 8. Deliberate deviations from the plan text

1. **Readiness label "Interface only"** (brief wording) instead of the plan §4 UI label "Not simulated"; "Simulated" instead of "Simulates". Badge wording and tooltips are fixed by rule R3 in §9.4 (T4).
2. **Extra readiness values** "Placeholder" and "Invalid" (brief) next to Simulated / Interface only / Reference only.
3. **Results layout.** Results (430 px) and Problems (250 px) sit side by side in a 248 px dock below the canvas. Plan §9 allowed bottom or right.
4. **Run / Re-run / Reset.** Decided in T4, rule R2 in §9.4. Run is disabled once results exist, Re-run is primary, and Reset is disabled while running.
5. **Esc in the search field clears the text** and the empty-state offers "Clear search". The plan only specifies the action.
6. **LED glow** is drawn in Penpot as three concentric translucent circles (64, 44, 26 px). T9 keeps the existing radial gradient (`(255,140,60)` to `(255,60,20)`); the mapping (0.1 mA dark, 15 mA full) is unchanged.
7. **Category counts follow the current search** (all 0 on an empty search). The plan says only "with counts". The sample counts on the screen are illustrative (rule R4 in §9.4).
8. **Pin and canvas label sizes** stay at today's 9 pt (12 px) for canvas labels. The rest of the chrome moves to the 10 pt body size.
9. The Penpot file could not be renamed from the plugin (section 1).

## 9. Review (T4)

Date 2026-10-08. Scope: plan §9 items 1-8, §4 capability model, all 28 PNGs viewed. Changed or added: 16 PNGs (15 re-exported, 1 new: `results-running-blocked.png`), 29 PNGs in total. No component was added (79 main components, unchanged). Code is not touched.

### 9.1 Checklist against plan §9

| Plan §9 item | Required content | Frame(s) | Status |
|---|---|---|---|
| 1 Workspace layout | Top bar, library 280, canvas, inspector 320, results, Problems, status bar | `screen-workspace-led` | present |
| 2 Library panel | Search, All + categories with counts, Simulated only, card list (icon, name, description, badge, tags), hover / selected / armed, details view (description, pins, params with units, limitations, docs, version, id) | `screen-workspace-led`, `library-card-states`, `library-details` | present. Details trigger was missing from the interaction spec; added (9.4 R1) |
| 3 Library states | Empty search, empty category, loading skeleton, catalog-problems banner (collapsed and expanded), missing-icon card | `library-empty-states`, `library-catalog-problems`, `library-card-states` | present |
| 4 Canvas | Part idle / hover / selected / dragging; pin open / wired / hover / pending wire / junction; wire hover / selected; rotate and delete; zoom controls and pan cursor; empty hint; unsupported badge; placeholder; core-finding ring; LED glow and label | `canvas-part-states`, `canvas-pin-states`, `canvas-wire-states`, `canvas-navigation`, `canvas-empty-hint`, `canvas-unsupported-placeholder`, `canvas-led-glow`, `canvas-placing-armed` | present |
| 5 Inspector | Nothing selected; part header (icon, name, type, badge, version); param form (number + unit + SI hint, choice, `when` fields, invalid); pins list; limitations; docs; wire selected; placeholder selected; unsupported part selected | `inspector-other-states`, `inspector-part-states` | present |
| 6 Simulation controls | Analysis label; stop and step fields valid / invalid; Run, Re-run, Reset; state badges idle, running, completed, failed, stale, blocked; blocked by pre-check | `sim-controls-states`, `components-canvas-topbar-status`, `results-running-blocked` (blocked result) | present. Blocked result panel added |
| 7 Results | Signal picker grouped; plot with cursor; play / pause and slider; readout; final / min / max; core findings; developer text; success message "not simulated: U1"; failure without plot | `results-states`, `results-running-blocked` | present. Running state added |
| 8 Dialogs | Unsaved changes; open failed; opened with placeholders (non-blocking banner) | `dialogs` | present |

Missing items found and filled: (a) results while Running (no frame); (b) results for a pre-check block (only the badge existed); (c) the details-view trigger (no input in the interaction spec). Not drawn, specified in text only: the inspector's "Not simulated" tooltip (rule R3).

### 9.2 Consistency fixes made in Penpot

- **Library highlight.** The Arduino card on `screen-workspace-led` was `Card / Hover` (blue border) while R1 was selected in the canvas. It now has the default border (instance override, renamed "Card / Default (Interface only)"). No card on the screen is hovered, armed or selected.
- **Piezo sample.** `library-card-states` showed PIEZO_BUZZER as "Reference only" with two pins. T7 gives it two pins, so it is Interface only and cannot be Reference only. The slot now shows an illustrative "Reference part (example)" with no pins.
- **Tag chips.** "wifi" is a keyword (search-only, plan §2), not a tag. The five "wifi" chips in `Component library cards` now read "passive". The ESP32 chip reads "3.3 V logic" (its T7 tag).
- **Off-token colours.** #F8DCDC (invalid field unit cell) changed to #FFF0F0 (`INVALID_BG`); #FFF8EC (armed card tile) to #FFFFFF; #FBF8FE (placeholder body, dialog sample) to #EFE8F8 at 50 % (`RDY_PH_BG`).
- **Type scale.** Canvas and pin labels were 9 px (the pt value used as px). Now 12 px (`small`, §2.2). The ESP32 module label is 11 px (caption) so it does not wrap to three lines. The "FUTURE_RELAY v3" label is 11 px auto-width. Medium 12 px labels in app frames are 13 px (`body-medium`). Results overlines were 10 px and are 12 px / 600 (`small-strong`). Foundations sheet titles (18-36 px) are left as presentation headings.
- **Run state.** The Running row and the Components main `SimControls / Running` now show Reset disabled (rule R2). Its caption was updated to match.
- **Interaction spec.** The "Click a library card" row now names the details path.

### 9.3 Feasibility in Qt (checked in the file)

- The file has 0 drop shadows, 0 blurs and 0 gradient fills across 3,915 shapes. Nothing needs simplifying for QWidgets or QGraphicsView.
- Dialog scrim (45 % flat overlay), tooltips and cards are flat rectangles with 1 px borders. Fusion style and QPainter can draw them directly.
- LED glow is three translucent circles in Penpot. T9 uses the existing radial gradient (§8 item 6), which Qt supports; the flat-ring fallback is also acceptable.

### 9.4 Rules decided in T4

- **R1 Library highlight and inspector.** Four independent states in the library list:
  - Hover is a transient mouse state (blue border), never persisted.
  - Armed (placing) is the orange card with the "Placing" pill. Exactly one card, until the part is placed, Esc is pressed or the card is clicked again.
  - Selected (details open) is the soft-blue card whose details view is open. It stays on that card when the list returns, until another card is opened or armed.
  - The inspector shows canvas selection only. Canvas selection never highlights a library card, and library state never changes the inspector.
  - Details open via right-click on a card, "Details" (keyboard: focus the card and press Shift+Enter). Back returns to the list (the "All components" link). This trigger is not in plan §5 and T8 must implement it.
- **R2 Run / Re-run / Reset.**

  | State | Run | Re-run | Reset | Results panel |
  |---|---|---|---|---|
  | Idle, valid | primary | disabled | disabled | "No results yet" |
  | Invalid field | disabled | disabled | disabled | unchanged, message under the field |
  | Running | disabled | disabled | disabled (no cancel in v1) | "Running", previous results hidden |
  | Completed | disabled | primary | enabled | plot, readout, stats |
  | Failed (engine error) | disabled | primary | enabled | "Run failed", message, no plot |
  | Stale | disabled | primary | enabled | results dimmed, banner |
  | Blocked by pre-check | disabled | disabled | disabled | "Run blocked", first error, no plot |

  F5 runs the enabled primary action. Re-run = Reset plus Run. Reset clears results and returns to Idle. This differs from the current app (Run stays enabled after results); T9 follows this table.
- **R3 Badge and readiness wording.**
  - Badge text is "Interface only" on every surface (library card, inspector, canvas, details). The placeholder badge is "Placeholder", and "Simulated" / "Reference only" are as in §2.1.
  - Badge tooltip on library and inspector: "Not simulated". On the canvas, the item tooltip is `limitations[0]` as plan T9 says.
  - Messages (problem rows, run messages, banners) use "not simulated" wording, e.g. "U1 ... is not simulated and is excluded from the run".
  - T8 test `test_unsupported_part_inspector` asserts the badge text "Interface only" and the tooltip "Not simulated". The canvas test asserts `limitations[0]`.
- **R4 Category counts.** The counts on the screen (Electrical 10, Microcontrollers 2, Sensors 1, Displays 1, Audio 1, Power 1; total 16) are illustrative. They follow the T7 seed list where it names parts (ESP32 and Arduino in Microcontrollers, DHT22 in Sensors, SSD1306 in Displays, PIEZO in Audio). The real counts come from the catalog. An empty category uses `library-empty-states`. Communication is not shown (T7).
- **R5 LED current label.** The canvas label shows 3 significant figures (9.18 mA). The results readout shows 4 (9.177 mA). Both come from the same value.

### 9.5 Open items (not resolved in T4)

1. **Pre-check block versus plan text.** Plan T9 (`test_run_blocked_by_wired_unsupported_shows_failure_without_plot`) and T10 scenario 5 say "failed". The design (R2) uses "Blocked", because the run never started. Needs approval; if rejected, the Blocked badge becomes "Failed" and the state table row changes.
2. **Power category.** Plan T7 requires `test_categories_present` to include Power, but no seed part is named for it. T7 must add one, or the category shows the empty state.
3. **Electrical count.** Illustrative only (10). T7 decides it.
4. **Results and details are drawn.** The plan inventory has no Results or details components, so these panels are drawn shapes (the new frame clones the `results-states` panels). T9 builds them from the token table.
5. **Penpot file name** is still "New File 3" (section 1).

## 10. T8 deviations (library panel, inspector, theme)

Screenshot: `docs/milestone/impl/t8-workspace.png` (LED example, R1 selected, offscreen, 1280 x 800). Library and inspector follow the frames; the top bar, canvas and results are untouched (T9). Differences, with reasons:

1. **Top bar and results dock are not restyled.** The toolbars keep their text actions, the results panel its old controls. T8 only re-arranged them: the old right column (inspector + results) became [library 280 | canvas over a results + Problems dock | inspector 320], because the plot cannot live in a 320 px inspector. Results (T9) will be rebuilt to the 248 px dock design. Rotate and Delete moved from the removed Parts toolbar to the File toolbar.
2. **Cards are 246 px wide, not 256.** Qt's vertical scrollbar (10 px, always reserved when the list scrolls) takes the difference inside the 280 px panel. The card list scrolls; the category list does not (7 rows fit).
3. **Elided text.** The card description and tag chips are elided with an ellipsis (full text in the tooltip) when the card is narrow; the design assumes short texts. Chips shrink to a 28 px minimum.
4. **Icons.** Card tiles show the part's `symbol.svg` (T5 files, stroke 2) fitted to 40 px, not the 1.5 px Penpot glyphs. Parts without an `icon` or `symbol` show their designator letters on a dashed tile (`MissingIcon`).
5. **Search icon** is a painted magnifier (QAction icon in the field). The "x" clear button is Qt's own `setClearButtonEnabled`.
6. **Details view** is a stacked page of the panel, not a separate frame. "Place" arms placement and returns to the list; pin lists fold behind "Show all N pins" after 5 rows (library details and inspector).
7. **Catalog banner** counts distinct files with errors ("N definitions could not be loaded"); warnings only (DEF-ASSET) switch it to an amber "N definition warnings". It lists every problem (file relative to its root, code, message and field), plus "Copy all diagnostics".
8. **Unit cell, field focus.** The unit suffix is a label inside a bordered box with a tinted cell; the focus ring is the box border (a `focus` property), because Qt has no `:focus-within`. Invalid fields use the same box (`invalid` property, 2 px #D02020, #FFF0F0) and the ModelError text from `components.py`, so the wording differs from the sample ("... is not a number (e.g. 1k, 10u, ...)").
9. **Valid-value echo.** While typing a valid value the field shows "= 4.7 kΩ" under it (the old inspector did this; kept as the SI feedback). Committing an invalid value on Enter still stores the text as typed and flags it in Problems (existing behaviour, tested since T6); nothing is stored while typing.
10. **Card focus** uses Tab focus only (a blue border), so clicking does not leave a stuck border. Shift+Enter on a focused card opens details; Enter or Space arms; right-click opens details directly (no context menu).
11. **Drag from a card** carries `application/x-sim-component` = id; the canvas drop is T9.
12. **Docs links** show the host (URLs) or file name, because `docs` holds only strings, not titles. Security: only http(s) URLs and files of type .pdf .html .htm .md .txt .png .svg that resolve (symlinks followed) inside the package directory get a link; `catalog.py` rejects other local docs entries (DEF-FIELD).
13. **Shared widgets** live in `app/widgets.py` (not in the plan's module list): badge, toggle, tile, banner, table, doc links.
14. **Fonts.** The app font is Noto Sans at 13 px, set on the Fusion style with an explicit palette; Noto Sans Mono for ids. Not embedded: a machine without them falls back to Qt's default.
15. **Wire tile glyph** is an em dash on the generic tile (Penpot draws a wire shape).
16. **Loading skeleton** is not implemented: the catalog loads synchronously.

## 11. T9 deviations (canvas, simulation controls, results, status bar)

Screenshots (offscreen, 1280 x 800): `docs/milestone/impl/t9-workspace.png` (LED example run, ESP32 placed unwired, R1 selected; the example's parts are moved 60 px down so the part toolbar fits above R1, and the ESP32 sits at 480, 40), `t9-blocked.png` (ESP32 wired to R1: blocked), `t9-empty.png` (empty-canvas hint). Code: `app/canvas.py`, `app/results.py`, `app/window.py` (top bar, status bar). Differences, with reasons:

1. **No menu bar.** The logo button opens a menu (File, Edit, Simulation, View) holding every action, so "Load LED example", "Save As", Rotate and Delete stay reachable. All shortcuts are kept (actions are added to the window). Rotate and Delete are also on the part toolbar and the part context menu.
2. **Stale drops the results** (existing behaviour, covered by tests) instead of dimming them. The state stays `idle`; the badge reads "Out of date", Run is disabled, Re-run is primary and Reset is enabled (R2 stale row), and Results shows an amber "Results out of date" banner on an empty page. F5, Ctrl+F5 and the menu entries stay enabled in every state except Running (F5 runs the primary action); only the buttons follow the R2 table.
3. **Notice under the top bar** (invalid field, failed, blocked, stale) is a wrapped floating label at the top-left of the canvas (max 520 px), not a strip centred under the fields. For failed and blocked it repeats the Results banner, as in the design frames.
4. **Results.** The "Show details" link stays in the header in every state (the design puts it under the failure banner). The closed signal picker shows the signal name (`I(D1)`), not "Current across D1 · I(D1)"; group headings are upper case. The plot keeps numeric tick labels and the axis labels (tests assert them) but the axis labels are hidden and the unit is drawn inside the plot corners; the plot is about 70-90 px high; vertical grid lines are shown. The cursor readout is `t = 2.5 ms  V(n1) = 5 V` (5 significant figures); the slider readout is `2.50 / 5.00 ms`. Failure and blocked pages show the banner (title + message) and "No plot: ...", findings only inside "Show details" (hidden when there are none). Running shows an indeterminate bar (the core runs synchronously, so it is not animated).
5. **Problems** rows are painted by a delegate (icon, mono code, wrapped message); the count pill is always red; the item text keeps the old "Severity: message [CODE]" form for scripts.
6. **Stroke widths.** Symbols are SVG files with a fixed 2 px stroke (T5), so a selected part is 2 px, not 3. The core-finding ring stays 20 px with a 3 px stroke (a test measures it), not 22 / 2. Hover on a part colours the symbol blue.
7. **Generic board body** (interface-only parts): rounded box with pin names inside, name and wrapped title centred; no inner module or USB shapes (illustrative in Penpot) and no pin stubs (pins sit on the box edge). The readiness badge sits above-left (below the box for placeholders) and its tooltip is `limitations[0]` (placeholders: the reason).
8. **Placeholder** is a dashed purple box with "?", its type and version at the bottom, and the pins from its connections; orange when selected.
9. **Dragging a part** moves it live (snapped) and draws a faint blue box plus dashed guides through its left and top edges; there is no separate ghost.
10. **Placement ghost** is a 55 % copy with an orange dashed box and pin dots; the "Click to place X / Esc to cancel" and pin-name tips are one dark overlay label following the cursor. The core-finding ring has no tooltip.
11. **Zoom.** Buttons and Ctrl+= / Ctrl+- step by 1.25, the wheel by 1.2 per notch, clamped to 25-400 %; near 100 % snaps to 100. Fit is capped at 100 % (no parts: 100 % at the origin). Below 40 % zoom (grid step < 8 px) grid lines are 100 units apart.
12. **Scene rect** is the parts' bounds plus 200 px, and at least the visible area from the origin (the old fixed 1000 x 700 forced scrollbars on an empty canvas). Scrollbars are Qt's, restyled to 10 px.
13. **Part toolbar** sits 30 px above the selection box and flips below the part when there is no room. It is a widget over the viewport, so it does not scale with the zoom.
14. **Canvas space.** At 1280 x 800 the canvas is 680 x 479 and the dock 248 px high (fixed); results and Problems share it (Problems is 250 px, narrowing to 150 px). Below 1240 px the top bar compacts (no "Transient" chip, narrower fields and buttons); minimum window size hint is 993 x 464, so 1024 x 640 works, but the signal picker and "Show details" are tight there.
15. **Not done:** the Unsaved / Open failed dialogs are still plain `QMessageBox`es and the "opened with placeholders" banner is not drawn (status bar and Problems list carry the message); no Penpot-style tooltip on the finding ring; the Penpot file was not opened (it was unavailable), all measurements come from the token table and the PNGs.

Test adjustments (accessors / wording only): `test_capabilities` (blocked badge text compared case-insensitively, `err_title`), `test_gui_workflow` (badge text lower-cased, `tstop` invalid state via its `invalid` property, Problems empty via `problems.count()`), `test_gui_review` (`err_title == "Run failed"`, readout starts with `t = `, plot/Play row non-overlap checked after a run because the plot page only exists with results).
