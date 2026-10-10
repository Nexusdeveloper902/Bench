# Task 1 audit: current state of Bench

Date: 2026-10-08. Read-only audit of the repo at `<repo>` (HEAD `1b2c335`, plus untracked `docs/milestone/`). No source files were modified. Paths are repo-relative; `file:line` refers to the current working tree.

## 1. Test results

| Command | Result |
|---|---|
| `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests` | **262 passed**, 0 failed, 9.19 s (141 `def test_` functions, parametrized to 262) |
| `cd benchcore && cargo test` | `tests/adder.rs` 3 passed; `tests/correctness.rs` 23 passed; unit and doctests 0. **26 passed, 0 failed** |
| `cd benchcore && python3 -m pytest -q tests/python` | **3 passed** |
| LED workflow subset: `app/tests/test_led.py test_led_e2e.py test_led_glow.py test_led_pixels.py test_e2e.py` | **52 passed** |

- Failures: none. Blockers: none. ngspice 47 is present (`/usr/lib/libngspice.so.0.0.16`).
- Caveat: `benchcore/tests/python` imports the **installed** wheel at `~/.local/lib/python3.14/site-packages/benchcore/`, not the repo source. Its `__init__.py` is identical to `benchcore/python/benchcore/__init__.py`. The `_benchcore` `.so` is dated Oct 5 20:48, later than the last Rust edit (`benchcore/src/kernel.rs`, Oct 5 19:37). I did not rebuild, so "in sync" is inferred from timestamps only.

## 2. Architecture map

```
app/__main__.py -> app/main.py:5-12   entry; autoplay=True; --smoke builds demo, runs, prints status
app/window.py    (855 lines, Qt only)  Canvas 164-271; MainWindow 274-855
app/history.py   (55)                  undo/redo = Document.to_dict() snapshots (minus "simulation")
app/model.py     (330)                 Document: parts, wires, nets, validate, save/load (pure Python)
app/components.py(304)                 CATALOG, Param/Observable/Component, SI units, build_netlist (pure)
app/sim.py       (93)                  only importer of benchcore: run_document(doc) -> RunResult
benchcore (PyO3 module _benchcore) -> Rust kernel (kernel.rs) -> validate.rs -> libngspice
```

`docs/frontend-architecture.md:6-15` describes the same layering, and the code matches it.

Data flow, GUI to simulator to results:

1. **Edit.** Handlers call `Document` methods inside `with self.hist.change(doc)`: place `window.py:394-397`, connect `window.py:207-209`, delete `419-424`, rotate `426-429`, param edit `535-538`.
2. **Stale check.** `MainWindow._key()` (`window.py:525-528`) is compared with `ran_key` in `_check_stale` (`530-533`). Any circuit edit clears the result.
3. **Run.** `window.py:559-573` calls `run_document(self.doc)`, synchronously on the GUI thread (`window.py:563-565`).
4. **Pre-run gate.** `sim.py:63-68`: `doc.validate()` (`model.py:174-203`). Any error returns a failed `RunResult` without calling the core.
5. **Netlist.** `sim.py:69-72`: `doc.config()` (`model.py:154-164`) and `doc.netlist()` (`model.py:170-172`), which calls `components.build_netlist` (`components.py:260-304`) over `Document.nets()` (`model.py:133-151`).
6. **Core call.** `sim.py:76-83`: `benchcore.Simulation("app")`, `build_circuit`, `add_model`, `add` (for `.save` lines), `configure`, `observe`, `validate`, `run`.
7. **Results.** `sim.py:84-86`: `series[name] = list(s.observable(n))`. Derived `v(Part)` is computed by subtraction in `_diff` (`sim.py:51-57`). `sim.py:88-93` builds the `RunResult`.
8. **Display.** `window.py:582-606` (`_show_result`) fills the signal picker; `plot` at `613-637`; cursor readout and LED glow use `model.value_at` (`model.py:33`) and `window.part_current` (`706-710`).
9. **Exceptions.** `sim.py:87` catches `benchcore.SimError`, which is an alias for `Exception` (`benchcore/python/benchcore/__init__.py:40`). See section 9, R6.

## 3. Component representation today

Structures:

- `Param` (`components.py:70-97`): `key, label, unit, default` (text, as typed), `kind` (`"number"` or `"choice"`), `choices`, `min`, `min_exclusive`, `max`, `when` (applicability tuple). `parse()` returns a float or choice string, or raises `ModelError`.
- `Observable` (`components.py:101-104`): `name` template (`I({name})`), `vector` template (`@{dev}[i]` or `i({dev})`), `quantity`.
- `Component` (`components.py:108-139`), a frozen dataclass: `id, label, prefix, terminals` (SPICE node order), `pos` (terminal to local xy), `box` (x0, y0, w, h), `symbol` (GUI painter key), `params`, `letter` (SPICE device letter, `None` = not a device), `emit(dev, nodes, values) -> (lines, models)`, `observables`, `value_param`, `ground`.
- Document part (`model.py:55`, `76`): `{"kind", "x", "y", "rot", "params"}`. Params are stored as text (`model.py:75`).
- Wires: tuples of `"Part.terminal"` strings (`model.py:122-126`).

Catalog today (`components.py:182-214`, 10 entries, hardcoded Python literal):

| id | label | prefix | terminals | symbol | params (default) | letter / emit | observables |
|---|---|---|---|---|---|---|---|
| R | Resistor | R | a,b | resistor | R Ω (1000), min>0 | R, emits `R<n> a b <val>` | I_THROUGH |
| C | Capacitor | C | a,b | capacitor | C F (1e-6), ic V (0) | C, emits `IC=` | I_THROUGH |
| L | Inductor | L | a,b | inductor | L H (1e-3), ic A (0) | L, emits `IC=` | I_THROUGH |
| V | Voltage source | V | p,n | vsource | mode DC/PULSE/SIN (DC); dc 5 V; PULSE: v1 v2 td tr tf pw per; SIN: vo va freq | V via `_vsrc` (`components.py:158-160`) | I_SOURCE |
| BAT | Battery | V (shares V numbering) | p,n | battery | dc V (5) | V, `DC` line | I_SOURCE |
| I | Current source | I | p,n | isource | dc A (1e-3) | I, `DC` line | **none** (`components.py:199`) |
| D | Diode | D | a,k | diode | Is A (1e-14), N (1), Rs Ω (0) | D via `_diode` (`163-165`), per-part `.model` | I_DIODE |
| GND | Ground | GND | g | ground | none | letter None, `ground=True` | none |
| LED | LED | D (shares D numbering) | a,k | led | none; fixed `.model dled` (`components.py:154`) | D, `dled` | I_DIODE |
| SW | Switch | SW | a,b | switch | state open/closed (open), ron Ω (0.01), roff Ω (1e9) | emitted as **R**, value chosen by `state` (`209-213`) | I_THROUGH |

- Geometry: all bodies are 60x30 (`_pins` helper, `components.py:142-143`), except GND `(-16, 8, 32, 24)`.
- Symbols are drawn in `window.py:97-146` (`SYMBOLS` dict keyed by the catalog `symbol` key).
- Not represented anywhere (brief section 4 asks for them): version, aliases, keywords or tags, category or subcategory, description, icon, domain, documentation references, declared capabilities or limitations, and a registration or discovery API. "Simulation implemented" is only implicit, through `letter` and `emit`.

## 4. Simulation API contract

**Python wrapper** (`benchcore/python/benchcore/__init__.py`):

- `Simulation` (`43-168`): `add`, `build_circuit`, `add_model`, `modify`, `configure(tstep, tstop, tstart, tmax, uic)`, `observe`, `pause_at`, `clear_stops`, `validate`, `run`, `reset`, `destroy`, `observable(name)`, and properties `state`, `time`, `used_pauses`, `fallback_reason`.
- Generated deck (`_deck`, `110-117`): title `"app"`, devices, models, `.tran tstep tstop tstart [tmax] [uic]`, `.end`. `uic` defaults to True.

**PyO3 exports** (`benchcore/src/lib.rs`):

- `_benchcore.run_simulation(netlist, observables, stops)` (`48-79`) returns a dict: `series {vector: [(t, v)]}`, `time`, `npts`, `wall_ms`, `used_pauses`, `fallback_reason`.
- `_benchcore.validate(netlist)` (`82-96`) returns a list of `{code, severity, message, nodes, devices, layer}`.
- `_benchcore.required_tmax(expr)` (`99-109`): not referenced by `app/` (grep).
- Typed exceptions (`lib.rs:22-28`, mapped in `map_err` at `30-43`): `InvalidCircuitError` (message `"[CODE] msg"`), `InvalidConfigError`, `ConvergenceError`, `SolverError`, `BackendError`, `UnsupportedError`, `UnavailableError`.
- Kernel gates run before the backend (`kernel.rs:309-318`): any validator error becomes `InvalidCircuitError`. An empty netlist gives `MALFORMED`. Control-flow escapes are refused.

**Circuit representation accepted by the core:** SPICE netlist strings only. `validate.rs` classifies devices by first letter. Unknown letters give `UNKNOWN-KIND` (`validate.rs:457`). Exotic letters `O U Y N T S W Z` give `EXOTIC-DEVICE` with severity `"warn"` (`validate.rs:438-439`). Duplicate names give `DUP-NAME` (`validate.rs:273`), compared case-insensitively. Node `0` is ground.

**Analyses:** the app uses only transient. `_deck` emits `.tran`, and `sim_config` is always `"transient"` (`model.py:166-168`). The loader rejects other analyses (`model.py:291-292`). The validator recognises `.op .dc .ac .noise .pz` (`validate.rs:257`), but no Python method runs them.

**What the app sends** (`sim.py:76-83`):

- `build_circuit(nl["lines"])`, for example `"V1 n1 0 DC 5.0"`, `"R1 n1 n2 330.0"`, `"D1 n2 0 dled"`.
- `add_model(".model dled D(Is=1e-20 N=1.8 Rs=5)")`, one per model (`components.py:154`, `205-206`).
- `add(".save all @r1[i]")`, one per `@` vector (`components.py:300`).
- `configure(tstep, tstop, tmax=tstep)` (`model.py:164`).
- `observe(*nl["observables"])`, for example `v(n1)`, `i(v1)`, `@d1[id]`. Nodes come first, then source currents, then device currents (`components.py:302`).
- `validate()`, then `run()`. `pause_at` is never called, so `used_pauses` is always False in the app.

**What comes back:** a `series` dict keyed by the exact vector names observed. Unknown observables raise `InvalidConfigError` (checked by `benchcore/tests/python/test_api.py`). `sim.py:88-90` shows `"{ErrType}: {msg}"` as the message, or the bare message for `InvalidCircuitError`.

**Limits:** `MAX_POINTS = 100_000` and `MAX_SAMPLES = 2_000_000` (`model.py:14-15`, enforced in `config()` at `160-163`).

**Severity vocabularies differ:** core uses `"error"` and `"warn"` (`validate.rs:439`), while app diagnostics use `"error"` and `"warning"` (`model.py:181-195`). Core findings are displayed verbatim (`window.py:586-587`).

**Sign convention** (`docs/frontend-components.md:74-75`): current is positive into the first terminal. A supplying source therefore reads negative.

## 5. Persistence format

Format v1: `FORMAT = "sim-circuit"`, `VERSION = 1` (`model.py:8`). Writer `Document.to_dict` (`228-233`). Reader `from_dict` (`235-244`) and `_from_dict` (`247-297`). Atomic save (`save`, `299-313`: write `.tmp`, then `os.replace`). `load` (`315-330`) also rejects duplicate keys.

Example, produced by running `Document.led_example().to_dict()` (layout trimmed):

```json
{"format": "sim-circuit", "version": 1,
 "components": {"V1": {"type": "BAT", "params": {"dc": "5"}},
                "R1": {"type": "R", "params": {"R": "330"}},
                "D1": {"type": "LED", "params": {}},
                "GND1": {"type": "GND", "params": {}}},
 "connections": [["V1.p", "R1.a"], ["R1.b", "D1.a"], ["D1.k", "GND1.g"], ["V1.n", "GND1.g"]],
 "simulation": {"analysis": "transient", "tstop": "5m", "tstep": "10u"},
 "layout": {"V1": {"x": 40, "y": 100, "rotation": 0}, "R1": {"x": 180, "y": 60, "rotation": 0},
            "D1": {"x": 320, "y": 100, "rotation": 0}, "GND1": {"x": 440, "y": 220, "rotation": 0}}}
```

Load behaviour:

- Missing params are filled from catalog defaults (`model.py:271`). Unknown params are refused (`273`).
- **Unknown component type is refused for the whole file.** `get()` raises `ModelError("unknown part kind …")` (`components.py:217-221`), called at `model.py:270`. I confirmed with a probe: `"bad simulation file: unknown part kind ESP32"`.
- Unknown terminals are refused (`model.py:117-120`, `285`). Bad layout is refused (`277-280`).
- v0 files (no `version`, with `parts`) are migrated (`252-257`). `version > 1` is refused (`260-261`).
- `tstop` and `tstep` are stored as text (`290`).
- Versioning is file-level only. No per-component definition version is saved. Nothing marks an unsupported component. The stability promise is in `docs/frontend-components.md:157-161`.

Undo/redo (`history.py`): snapshots are `to_dict()` minus `"simulation"` (`history.py:10-11`). Restore goes through `from_dict` (`14-17`). The stack is capped at `LIMIT = 200` (`history.py:7`). Run settings are not undoable (by design).

## 6. Hardcoded type branches (what the new catalog must remove)

| file:line | what it does | verdict |
|---|---|---|
| `components.py:158-160` | `_vsrc`: branches on V mode `DC`/`PULSE`/`SIN` to emit lines | per-kind emit, acceptable inside a definition |
| `components.py:163-165` | `_diode`: per-part `.model` card | per-kind emit |
| `components.py:183-213` | eight `emit` lambdas, one per kind | the intended per-kind code |
| `components.py:154`, `205-206` | LED's fixed `.model dled` card in code | per-kind data |
| `components.py:209-213` | Switch emit: `state == 'closed'` picks Ron or Roff | per-kind emit |
| `components.py:230-231` | `summary()`: falls back to the first choice param when `value_param` does not apply (V mode, SW state) | generic rule, implicit |
| `components.py:273-274` | `if c.letter is None: continue` (skips Ground) | **danger**: any future non-simulated kind with `letter=None` silently vanishes from the netlist |
| `components.py:281-284` | `len(nodes) == 2` adds derived `V(part)` | structural rule (terminal count), not kind-based |
| `components.py:300-302` | `startswith("@")` splits `.save` vectors | vector-naming convention |
| `model.py:7` | `KINDS` dict "kept for callers that predate the catalog" | dead code, no references |
| `model.py:25-30` | `led_level`, `I_OFF`, `I_FULL`: LED-only brightness mapping in the pure model | LED-specific code in a Qt-free module |
| `model.py:209-210` | `led_example`: literal kinds `BAT`, `R`, `LED`, `GND` and names `V1`, `R1`, `D1`, `GND1` | example data, acceptable |
| `model.py:220-221` | `blink_example`: literal `V` with `PULSE` params | example data |
| `model.py:253-256` | v0 loader: literal `parts` layout | migration only |
| `window.py:35` | `SHOW_CURRENT = {"diode", "led"}`, keyed by symbol | display set, symbol-based |
| `window.py:36`, `752` | `GLOWS = {"led"}`, checked in `draw` | display set, symbol-based |
| `window.py:778-789` | `_glow`: LED-only radial gradient and colours `LED_OFF`/`LED_ON` (`25`) | LED-specific drawing |
| `window.py:122-125` | `_switch` reads `params["state"]` | per-symbol painter |
| `window.py:134-145` | `SYMBOLS`: painter per symbol key | per-symbol code; a new symbol needs an edit in `window.py` |
| `window.py:572` | autoplay checks `params.get("mode", "DC") != "DC"` | hardcoded `mode` and `DC` (only V has them) |
| `window.py:719-724` | `load_demo`: literal `V`, `R`, `C`, `GND` and wiring | demo data |
| `window.py:327-330`, `26`, `747`, `755`, `828-847` | toolbar buttons, labels, inspector, value labels, all loop over `CATALOG` | generic, good |

`sim.py`, `history.py` and `main.py` contain no kind branches. `sim.py` is generic: it works on pins, nets and vector names.

## 7. Visual identity (what exists now)

**Penpot:** no Penpot files, exports, tokens or references in the repo. `grep -rni penpot` only hits `docs/milestone/00-brief.md`. I did not query the Penpot MCP, which is outside the repo scope (see open questions).

**Colours** (all hardcoded, no theme file, no QSS file, no palette):

- Canvas and parts: `INK #222`, `SEL #e07000`, `HOVER #2a7fff`, `WIRE #1a5fb4` (`window.py:30`); `OK_PIN #2fa82f`, `OPEN_PIN #d02020`, `BAD #b00020` (`31`). Wired pin ring `#1b6e1b` (`768`). Hover/pending/junction use `HOVER` and `WIRE`. Selection box fill `rgba(255,230,190,140)` (`750`). Canvas background `Qt.white` (`177`). Amber finding ring `#e0a000` (`744`).
- LED: `LED_OFF (90,90,90)`, `LED_ON (255,70,30)` (`25`); glow gradient `(255,140,60)` to `(255,60,20)` (`784-785`).
- State badges: `idle #666`, `running #1a5fb4`, `completed #1b6e1b`, `failed #b00020` (`32`).
- Text and messages: `#a05000` (findings and warnings, `345`, `797`), `#b00020` (errors, `359`, `546`, `717`, `849`), `#555` (param message, `550`), `#888` (plot placeholder, `634`), `#666` (`381`).
- Inputs: QSS on QLineEdit only, `border: 1px solid #666; border-radius: 3px; padding: 2px 4px` (`window.py:381`). Invalid field: `border 2px #d02020; background #fff0f0; color #222` (`854`).
- Plot: line in `WIRE` (`621`), cursor line `#d02020` (`627`).

**Fonts:** none set. No `setFont` or font family. Canvas labels use `QFont()` at 9 pt (`window.py:745`). Chrome uses the system Qt font. The plot uses matplotlib defaults.

**Palette and icons:** no `setPalette`, no `Fusion` style, no `setWindowIcon`, no icon or resource files (no `.svg`, `.qrc`, `.ttf`). The screenshot `docs/review/attack/shots/led_example.png` shows dark window chrome with a white canvas. Because the app sets no palette, the dark chrome is the capture machine's Qt style, not an app choice. Unverified on this machine.

**Layout** (`window.py`):

- Window `1280x800` (`382`). Menu bar File, Edit, Simulation (`302-306`).
- Toolbars: row 1 is File plus Simulation (Analysis label, Stop time and Time step line edits 80 px wide, Run, Re-run, Reset, state badge). Break. Row 2 is Parts: one checkable text button per catalog kind, then Rotate and Delete (`308-331`).
- Hint label at the top of the left pane (`27-29`, `375`), then the canvas. Canvas scene rect `1000x700` (`175`), grid snap `GRID = 20` (`22`), pin hit radius 10 px (`184`).
- Right pane (`372-373`): vertical splitter, minimum width 420, sizes 250/600. Top: inspector in a scroll area with minimum height 140 (`370-371`), a Problems list capped at 90 px (`335`). Bottom: signal picker, plot (min 300x160, `355`), Play and time slider, readout, stats, error, findings, and a hidden details box.
- Status bar: one label, "Ready." (`379-380`).
- No zoom or pan on the canvas (no `wheelEvent` or scale code in `app/`). Parts are text buttons with no icons.

## 8. Docs vs code discrepancies

1. **`docs/frontend-architecture.md:22`** says "No per-component branching". Mostly true for logic, but the display exceptions in section 6 (`window.py:35-36`, `752`, `778-789`, `572`, `122-125`) are not mentioned.
2. **`benchcore/docs/components.md:3-20`** presents `components.rs` as the device representation, with PWL, K, T, subcircuits and the thin B-source layer "supported in v1". In fact the Rust `Component` enum and `build_deck` (`benchcore/src/components.rs:37-80`, `84-113`) have no references in `benchcore/src` or `benchcore/tests`. `docs/frontend-components.md:15-16` says the same. The Python API accepts raw SPICE lines, so "supported" means the kernel and validator accept such lines. No app code uses them.
3. **`benchcore/docs/api.md:20`** shows `r = s.run()` with fields read as attributes. The real return is a dict (`benchcore/src/lib.rs:50-79`, `benchcore/python/benchcore/__init__.py:123-134`). `docs/frontend-plan.md:43` uses dict access correctly.
4. **`docs/frontend-plan.md`** (dated 2026-10-05) is stale. It mentions a `plot.py` (plots are in `window.py`), `tests/test_model.py` (tests are in `app/tests/`), a "palette", and a components table without BAT, LED or SW.
5. **`docs/review/test-review.md:3`** says 188 tests. The suite now has 262 passing cases (141 functions). Its item M21 ("no current source ever simulated") looks addressed: `app/tests/test_review_physics.py:114` builds an `I` source circuit. I did not re-run the mutation tests.
6. **Severity vocabulary:** core `"warn"` (`validate.rs:439`) vs app `"warning"` (`model.py:195`). Docs only list app codes (`docs/frontend-components.md:111-117`). Not wrong, but there are two vocabularies.
7. **LED numbers:** `components.py:154` comment says "Vf ~1.98 V at 10 mA"; `docs/frontend.md:21` says "Vf ~1.97 V at 9 mA". Minor.
8. **`circuits/`** exists but is empty. No sample project exists. The format is documented only (`docs/frontend-components.md:131-161`).
9. **`docs/frontend-components.md:157-161`** correctly says unknown types are refused, not dropped. That is the behaviour the brief has to change (see R1).

## 9. Reusable assets, gaps and risks

**Reuse (keep):**

- Catalog nucleus: `Param` and `Component` dataclasses, each with an `emit` closure (`components.py:70-139`). `parse_si`/`format_si` (`26-65`) and `Param.parse` (`85-97`), with tests in `app/tests/test_components.py`.
- `Document` (`model.py`): union-find nets, `validate` (`174-203`), schema-checked load, atomic save. `History` snapshots (`history.py`).
- `run_document` and `RunResult` (`sim.py`) as the only simulator seam. Add capability checks here.
- `SYMBOLS` painter table and `_box`/`terminal_offset` geometry (`window.py:97-146`, `40-70`).
- GUI test harness: `test_gui_workflow.py`, `test_led_e2e.py` (QTest mouse and key events, offscreen).
- Simulation API and error taxonomy (`benchcore/python/benchcore/__init__.py`, `lib.rs`). LED example and blink example as regression cases (`model.py:205-225`).

**Gaps against the brief:**

- **Catalog is a code literal** (`components.py:182-214`). No discovery, registration API, package loading or definition validation. A malformed entry fails only at import or at run time. For example, a non-ground entry with `letter` set but no `emit` raises `TypeError` in `build_netlist` (`278`).
- **Metadata missing** (see section 3): version, aliases, keywords, category, icon, domain, docs, capabilities.
- **Definition and simulation are not separated.** `letter`, `emit`, `observables` sit in the same dataclass as pins and params. Placeable, interface-defined and simulation-implemented cannot be distinguished.
- **Unsupported components cannot be placed, wired, saved or loaded.** `add_part` (`model.py:64`), `_check_pin` (`117-120`) and `_from_dict` (`270`) all require a catalog entry.
- **UI:** no search, no category filter, no icons, no library panel, no details panel, no zoom or pan. The parts palette is ten text buttons (`window.py:325-331`). `window.py` is one 855-line class.

**Risks:**

- **R1 (high):** unknown kinds are refused for the whole file (`model.py:270`, verified). Brief section 9 needs preservation, so the file format must change (for example, store the definition reference, version, params, pins and placement, then show a placeholder).
- **R2 (high): silent false success.** `components.py:273-274` drops any kind with `letter is None`. Only Ground uses that today. Any future kind that is placeable but not simulated would vanish from the netlist. `Document.validate` would not warn, and the run would report success. Brief section 5 forbids this. A capability check must come before `build_netlist`.
- **R3 (high):** the UI cannot show categories, search or capability state, and adding one requires editing `window.py`. The redesign will touch most of `window.py`. Consider splitting it first.
- **R4 (medium): electrical test gaps.** The 2026-10-06 review (`docs/review/test-review.md`) reported these injected bugs as missed: a second Ground not being node 0 (M04), `ic` dropped on save (M07), diode `Rs` ignored (M12), PULSE `tr` vs `tf` swap (M16), SIN never simulated (M17), inductor `ic` ignored (M18). Some tests have since been added, so I did not confirm which are still open. Re-run those mutations before trusting the electrical regression.
- **R5 (medium): case-only name collision not caught before Run.** `NAME_RE` (`model.py:16`) allows `r2` and `R2`. SPICE device names are case-insensitive. I probed this: the core reports `[DUP-NAME] duplicate device name 'R2'` at Run, and the Problems panel is empty beforehand. Fix in `Document.validate`.
- **R6 (medium):** `sim.py:87` catches `benchcore.SimError`, which is `Exception` (`__init__.py:40`). It also covers app code (`_diff`, `sim.py:85-86`), so an app bug shows up as a typed simulator failure. `window.py:565` has a second catch-all.
- **R7 (medium): stale installed wheel.** Tests run against `~/.local/.../benchcore`. After any Rust change, run `maturin build` and reinstall, as CLAUDE.md says.
- **R8 (low): geometry does not scale.** Bodies are fixed 60x30 (`components.py:142-143`, `183-213`). Pin hit radius is 10 px (`window.py:184`). A part with many pins (an ESP32 has dozens) needs a larger box and label placement that the model does not support.
- **R9 (low): dead Rust code.** `benchcore/src/components.rs` and `build_deck` are unused (section 8, item 2). Removing them or documenting them as reserved would avoid confusion.
- **R10 (low): `model.py:7` `KINDS`** is dead. The I (current) source has no observable (`components.py:199`), so the inspector cannot show its current.

## 10. Open questions (my uncertainty stated)

1. **Penpot.** Is there an existing Penpot file or design-system link outside the repo? I did not query Penpot, and the repo has no references. Uncertain whether a design already exists.
2. **Unsupported-component persistence.** Should the file embed a snapshot of the definition (self-contained, but can go stale), or store only kind id and version and require the definition at load (smaller, but needs a placeholder path)? I lean toward embedding the minimal definition (pins, params, version, placement) so a file never silently changes meaning. Not verified against the brief's intent.
3. **Catalog package format.** Keep Python modules with `emit` closures, or move metadata to a declarative manifest (TOML or JSON) with an optional Python `emit` module? My guess is the second, because it keeps definitions separate from simulation. This is a judgement, not tested.
4. **Wired non-simulated parts.** Should they block Run as an error, or should the rest of the circuit still run with a warning? The brief implies an error, since it forbids partial success. Not decided.
5. **Current source observable.** `components.py:199` gives `I` no observable. Intentional (the docs say "its current is its parameter") or a gap?
6. **Installed `_benchcore`.** I trusted timestamps and did not rebuild. A rebuild would confirm it.
7. **Mutation results.** The review's mutations were run on a copy on 2026-10-06. I did not re-run them.
8. **Dark chrome in the screenshot.** The app sets no palette, so the look depends on the OS Qt style. The machine I audited may differ from the one that captured the screenshot.
