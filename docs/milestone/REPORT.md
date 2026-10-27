# Milestone report: component ecosystem and native workspace UI

> **Historical.** This report describes the state at commit 9574f5d. Since then the catalog grew to 185 definitions and multi-terminal devices (BJT, MOSFET, JFET, MESFET, subcircuits) are supported; see README.md for the current state.

Branch `milestone/component-ecosystem`, 14 commits on top of `1b2c335`. Changes under `app/`: 64 files, +7.8k/−1.0k lines. No Rust changes.
The work ran as 14 sequential tasks; the per-task inputs and outputs are in this directory (00–12, the design folder, the impl folder).

## Verification (run on the final commit, `9574f5d`)

| Check | Result |
|---|---|
| `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests` | **434 passed** (262 at the start of the milestone). Two consecutive green runs; no flakes in repeated, reverse-order or parallel runs (T12). |
| `cargo test` (benchcore) | 26 passed |
| `pytest tests/python` (benchcore) | 3 passed. These tests import the installed wheel; the wheel was not rebuilt because no Rust changed. |
| `python3 -m app.catalog` | 16 definitions, 0 errors, 0 warnings |
| LED regression | I(D1) = 9.1772 mA through the real engine, unchanged from before the milestone |
| Mutation testing (T12, before the T13 fixes) | 53 of 69 mutants killed. T13 added tests targeting 13 of the 16 survivors. X2 (the DHT22 pinout) is covered by the new pinout table; P2 and I2 are covered by the T7 and F3 tests. The suite has not been re-scored since. |

## Penpot deliverables

- **Penpot file:** "New File 3"; the plugin API cannot rename files, so rename it to "Bench Workspace" by hand. It has four pages:
  - **Foundations:** 3 frames.
  - **Components:** 79 reusable components, plus 55 library colours, 10 typographies and 81 tokens.
  - **Screens:** the 1280×800 workspace with the LED circuit and an unwired ESP32.
  - **States:** 18 frames covering the library, canvas, inspector, simulation controls, results and dialogs. That includes empty search, the catalog-problems banner, the missing icon, Blocked, Failed, Out of date and placeholders.
- **Specification:** `03-design.md` holds the token tables, the frame list, the interaction spec, the review rules R1–R5 (T4), and the T8/T9 implementation deviations.
- **Exports:** 29 PNGs in `design/`. Implementation screenshots for comparison are in `impl/`.

## UI changes (`app/`)

`window.py` was split into these modules; it now only wires them together:
- **`theme.py`:** Penpot tokens, one QSS string, and the Fusion style with an explicit palette.
- **`library.py`:** search, category navigation with live counts, the "Simulated only" toggle, cards with readiness badges and tags, empty and problem states, and a details view (right-click or Shift+Enter).
- **`inspector.py`:** the param form is generated from the definition schema. It also shows pins (wired/open), limitations, doc links, and the wire and placeholder views.
- **`canvas.py`:**
  - zoom from 25 to 400 % with a zoom control widget;
  - pan by middle-drag or Space+drag;
  - drop from the library, plus move, rotate and delete with a floating toolbar;
  - "Disconnect all", and an empty-canvas hint;
  - "Interface only" badges and dashed "?" placeholders.
- **`results.py`:** the plot and cursor readout, the status banners (success with "not simulated: U1", Failed, Blocked) and the Problems list.
- **Top bar:** file actions, undo/redo, the analysis fields, Run/Re-run/Reset following rule R2, and a run-state badge (idle, running, completed, failed, blocked, out of date).

Deliberate deviations from the design are listed in `03-design.md` §10–11. Examples: there is no menu bar (the logo opens the menu), and the unsaved-changes and open-failed dialogs are native message boxes.

## Component architecture

- **Definition, a data package:** `app/library/<category>/<id>/component.toml`, plus optional SVG symbol and icon files. The package holds:
  - identity and version, name, description, aliases, keywords and tags;
  - category and subcategory, and domain;
  - pins, and params (type, default, unit, constraints, `when`);
  - presentation and docs;
  - capabilities and limitations.
- **Catalog** (`catalog.py`):
  - discovers packages under `app/library/` and the roots in `SIM_COMPONENT_PATH`;
  - validates each one into diagnostics (code, field, message, file);
  - provides search (AND over tokens; ranked exact, then prefix, then rest), category listing (TAXONOMY first, then free categories) and readiness (`simulated` / `interface` / `placeable`).
  - It knows nothing about SPICE.
- **Domain implementation:** separate from the definition. The electrical domain lives in an optional `[sim.electrical]` section, which `electrical.py` validates and renders: a declarative line template, `.model` lines and an observable for current. Adding an electrical part needs no Python; the 1N4148 package demonstrates this. `catalog.SIM_DOMAINS` is the single registration point for domains.
- **Future-domain parts:** an ESP32, for example, declares only pins, docs and limitations. It is placeable, persistable and inspectable, and never gets fake behaviour.
- **Run pre-check:**
  - a wired unsimulated part makes the run **Blocked**, and the engine is never called;
  - an unwired one is excluded and named in the result ("not simulated: U1");
  - `build_netlist` refuses any unmodeled part, so no code path skips one silently.
- **Persistence v2** (`docs/persistence.md`):
  - each part stores its definition id and version, params, position and rotation; v0 and v1 files still load;
  - a part whose definition is missing, or saved at a different version, loads as a placeholder; it is kept verbatim and never substituted;
  - unknown keys survive a re-save;
  - a part whose id is shadowed by another definition gets a warning.
- **Authoring guide:** `docs/adding-components.md`. It covers the layout, annotated TOML for both kinds of part, pin, param and SVG rules, capabilities, versioning, the validator CLI and the error-code table. A test checks that its examples load.

## Supported workflows (each covered by end-to-end tests through the real window, catalog, files and engine)

1. Search by name or alias, and filter by category or "Simulated only". The library shows real catalog entries.
2. Drop a new `component.toml` into an extra root and it appears as a card, with an inspector and simulation, without any UI code.
3. Place, wire, rotate, move, delete, edit params, undo/redo, zoom and pan.
4. Place an ESP32, inspect its 38 pins, save, and reopen it identical.
5. A wired ESP32 makes Run Blocked. Unwired, the run succeeds and names it as not simulated.
6. A broken definition is listed in the problems banner, and everything else keeps working.
7. A file with a missing definition opens with a placeholder and a banner, and re-saves unchanged.
8. Electrical regressions are checked against analytic references:
   - the LED, the 1N4148 and the diode series resistance (Shockley bisection);
   - the RC time constant;
   - capacitor and inductor initial conditions;
   - PULSE edges and SIN amplitude;
   - two grounds.

## Seed catalog (16 definitions)

- **Electrical:** 10 entries.
  - The 9 original electrical parts: R, C, L, V, I, D, LED, SW, GND.
  - The 1N4148, a data-only part. Its model values are commonly published figures, not a datasheet fit.
- **Power:** Battery.
- **Interface only:**
  - ESP32-DevKitC V4, Arduino Uno R3 (Microcontrollers);
  - DHT22 (Sensors);
  - SSD1306 I2C (Displays);
  - piezo buzzer (Audio).
- **Pinouts:** each was transcribed from the cited official sources and is pinned by a test table.
- **Communication:** intentionally empty.

## Known limitations

- **Two-terminal electrical devices only:** R, C, L, D, V and I. The core validator (`validate.rs`) models only these, so transistors, op-amps and subcircuits need engine work.
- **"Simulated" means `sim:electrical`:** a second domain must extend readiness and the run path. This is documented, and a test pins it.
- **Run Blocked by any pre-check error:** this covers more than unsupported parts, for example a missing ground. Engine errors remain Failed.
- **Out of date:** a stale run drops its results instead of dimming them.
- **Synchronous loading:** the catalog loads at startup, so there is no loading skeleton.
- **Invalid params:** pressing Enter on an invalid param stores the typed text, and the pre-check catches it.
- **Symlinked packages:** these produce a DEF-LINK warning instead of being followed.
- **Rich-text labels:** status-bar and notice labels still use Qt's automatic rich-text detection. They never show definition text.
- **Floating notices:** the "invalid field" and "out of date" notices float over the canvas top-left and can cover a badge there.
- **Penpot:** the design has no prototype links; behaviour is specified in the interaction spec frame.

## Recommended next milestone

**MCU domain, first slice: a digital-I/O ESP32/Uno co-simulation.**
- **Runtime:** wire the `experiments/coupling-proto` work into a real `sim:mcu` domain in `SIM_DOMAINS`. The first slice is GPIO in and out to the electrical net through threshold and drive models, with firmware loaded from a file.
- **Part definitions:** the existing interface-only ESP32 and Uno definitions gain a `[sim.mcu]` section. Their identity, pins and persistence stay unchanged.
- **Prerequisite:** extend the core validator and templates to multi-terminal devices (BJT, MOSFET, subcircuit) so that drivers and level shifters can be modelled.
