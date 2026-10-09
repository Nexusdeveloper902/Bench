# Task 3: Penpot interface design

Date: 2026-10-08. Inputs: `00-brief.md`, `02-plan.md` (D11, D14, D18, §4, §8, §9), `01-audit.md` §7, `app/window.py` (current look).
Output of this task: the Penpot file, 28 PNG exports in `docs/milestone/design/`, and this document. T8 (library, inspector, `theme.py`) and T9 (canvas, results) implement from these.

## 1. Penpot file

| Item | Value |
|---|---|
| File | "New File 3" (open file of the connected plugin). **Not renamed**: `penpot.currentFile.name` is read-only in the plugin API (`TypeError: setting getter-only property "name"`). Rename it to "Sim Workspace" by hand in the Penpot UI. |
| D18 (reuse a "Sim" file) | The Penpot MCP exposes only the one connected file, no project listing. There was nothing to reuse, so the design was built in this file. |
| Pages | `Foundations` (3 frames), `Components` (6 section boards holding the 79 main components), `Screens` (2 frames), `States` (17 frames). |
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
| `screen-workspace-led` | 1280 x 800 workspace: top bar with file / undo / redo and simulation controls (Completed state), library left (search, categories with counts, Simulated-only toggle, four cards, one hovered), canvas centre (LED circuit: battery, resistor R1 selected with part toolbar, LED D1 glowing with 9.18 mA, ground, wires, junction; ESP32 DevKitC box with 38 pins and "Interface only" badge; zoom control), results dock (signal picker, play + slider, plot with cursor, readout, stats, "ok, 511 points; not simulated: U1"), Problems list (one NOT-SIMULATED warning), inspector right (R1: header, resistance field, pins, docs), status bar | `design/screen-workspace-led.png` |
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

1. **Readiness label "Interface only"** (brief wording) instead of the plan §4 UI label "Not simulated". Plan T8 test `test_unsupported_part_inspector` asserts a "Not simulated" badge. Resolution for T8: the badge text is "Interface only". Keep "Not simulated" as the badge tooltip and as the wording in the problems and results messages. Adjust that test to look for the badge by role, or for either string.
2. **Extra readiness values** "Placeholder" and "Invalid" (brief) next to Simulated / Interface only / Reference only.
3. **Results layout.** Results (430 px) and Problems (250 px) sit side by side in a 248 px dock below the canvas. Plan §9 allowed bottom or right.
4. **Run / Re-run emphasis.** Run is primary before the first run. Once results exist (completed, failed, stale) Re-run is primary and Run is disabled, because Re-run does the same. The current app keeps Run enabled; either is acceptable, T9 may keep Run enabled if simpler. Blocked by pre-check disables Run, Re-run and Reset and shows the first error.
5. **Esc in the search field clears the text** and the empty-state offers "Clear search". The plan only specifies the action.
6. **LED glow** is drawn in Penpot as three concentric translucent circles (64, 44, 26 px). T9 keeps the existing radial gradient (`(255,140,60)` to `(255,60,20)`); the mapping (0.1 mA dark, 15 mA full) is unchanged.
7. **Category counts follow the current search** (all 0 on an empty search). The plan says only "with counts".
8. **Pin and canvas label sizes** stay at today's 9 pt (12 px) for canvas labels. The rest of the chrome moves to the 10 pt body size.
9. The Penpot file could not be renamed from the plugin (section 1).
