# Task 2: architecture and implementation plan

Date: 2026-10-08. Inputs: `00-brief.md`, `01-audit.md`, and a re-read of `app/components.py`, `model.py`, `sim.py`, `history.py` and `window.py`. The audit's file:line references were spot-checked and hold.

Experiments (scratchpad, throwaway):
1. `tomllib` parses the example definitions below. A TOML trap showed up: a top-level key written after a `[table]` header lands inside that table. The validator reports it as an unknown key.
2. `str.format` templates reproduce the current netlist text exactly, for example `V1 n1 0 DC 5.0` and `i(v1)`.
3. An SVG with `viewBox="0 -10 60 40"` was rendered through `QGraphicsSvgItem` with a 90° part transform. It lands at the expected scene rect (115,85,40,60). Content outside the body box (the LED arrows) is drawn. Replacing `currentColor` with a colour recolours the symbol.
4. `QIcon('x.svg')` works. The installed environment is PySide6 6.11.2 with QtSvg and QtSvgWidgets, Python 3.14 (`tomllib` is in the stdlib), and the fonts Noto Sans, Roboto and DejaVu Sans.
5. LED example reference: I(D1) = 9.1772 mA and V(D1) = 1.97151 V (final values), 511 points.

No new dependencies are needed.

## 1. Decisions

| # | Decision | Rationale | Rejected alternative |
|---|---|---|---|
| D1 | One directory per component holding `component.toml` and optional SVG/doc files. Read with stdlib `tomllib`. | Allows comments, adds no dependency, and the app never writes definitions. | JSON (no comments). Python modules (executable code, mixes the definition with its implementation). |
| D2 | Built-in root is `app/library/`. Extra roots come from env `SIM_COMPONENT_PATH` (`os.pathsep`-separated). Discovery is `sorted(root.rglob("component.toml"))`, built-in root first. | Dropping a directory in makes the part appear, with no code change. | Package manager, user-config scanning, entry points. |
| D3 | The existing 10 ids (`R C L V BAT I D GND LED SW`) keep their ids, param keys and pin ids verbatim. | v1 files and the 262 tests keep their meaning. | Namespaced ids (needs a migration table for no gain). |
| D4 | Electrical behaviour is a declarative `[sim.electrical]` section with SPICE line templates, rendered by `app/electrical.py`. | A new electrical part needs no Python, and the generic catalog never parses SPICE. | A per-kind Python emit registry (every new part is a code change). Reviving the dead Rust `components.rs` (R9). |
| D5 | The domain registration point is `catalog.SIM_DOMAINS = {"electrical": electrical.check}`. | It is the one place a future domain plugs in its validator. Nonexistent domains get no internals. | Plugin entry points, or an abstract Domain class with one implementation. |
| D6 | Capabilities are an explicit list from a fixed vocabulary (§4), cross-checked against pins and `sim` sections. | The brief requires declarations, and the cross-check catches lies. | Inferring capabilities from category or name. |
| D7 | (audit Q4) An unsimulated part that is wired is an error and blocks Run. An unwired one is a warning: the part is excluded and named in the result (`RunResult.excluded` and the message). | Run never reports success over a circuit that contains an unmodeled part. A board placed only for documentation does not block everything. | Always blocking. Silently skipping (today's R2). |
| D8 | `build_netlist` raises `ModelError` for any part without an electrical binding. Ground is skipped only because its binding says `ground = true`. | Removes the silent `letter is None` skip (`components.py:273`). | Keeping the skip and hoping the pre-check catches everything. |
| D9 | (audit Q2) Persistence v2 is v1 plus a per-component `version`. No definition snapshot is stored. | The file stays small and has one source of truth. A missing definition becomes a placeholder, never a reinterpretation. | Embedding definition snapshots (stale copies, two truths). |
| D10 | A missing or incompatible definition becomes a placeholder part. Its pins are inferred from the connections. It is saved back verbatim. Ids are never resolved through aliases or fuzzy matching. | Nothing is lost and nothing is silently substituted. | Refusing the whole file (today's R1). |
| D11 | Symbols are SVG files in part-local units. Colour follows the part through `currentColor` substitution, rendered with `QSvgRenderer` and `QGraphicsSvgItem`. With no symbol, or an unusable one, the part draws as a generic labelled box. | A new part needs no painter code, and multi-pin boards get a usable body. | Keeping the `SYMBOLS` Python painters. A custom path DSL. |
| D12 | There is one process-wide `CATALOG`, refreshed in place by `catalog.reload(roots)`. | Every `from .catalog import CATALOG` stays valid, and tests reload it in a fixture. `# ponytail: global catalog; pass a Catalog explicitly if two are ever needed`. | Threading a catalog through Document, History and the window. |
| D13 | (R3) Split `window.py` into `canvas.py`, `library.py`, `inspector.py`, `results.py`, `symbols.py` and `theme.py`. `window.py` keeps the MainWindow wiring. | T8 and T9 can edit different files. | One 1200-line window. |
| D14 | Penpot design tokens go into `app/theme.py` (constants plus one QSS string). The app uses the Fusion style with an explicit palette. | Removes the OS-dependent chrome (audit Q8). | `.qss` or `.qrc` resource pipeline. |
| D15 | (audit Q5) The current source keeps no current observable. This is stated in its `limitations`. | The core has no `i()` for I sources. | Inventing a vector name. |
| D16 | (audit Q6) No Rust changes are planned, so no wheel rebuild is needed. R5 (case-only duplicate names) and R6 (over-broad `SimError` catch) are fixed in T6 at `Document.validate` and `sim.py`. | Fixes genuine gaps at the right boundary. | Leaving R5 to fail at Run with a core error. |
| D17 | (audit Q7) The review's mutations M04, M07, M12, M16, M17 and M18 become explicit numerical tests in T10. | A permanent guard instead of a one-off mutation run. | Re-running the mutation harness. |
| D18 | (audit Q1) The repo has no Penpot design. T3 first lists Penpot projects through the MCP and reuses a "Sim" file if one exists. Otherwise it creates "Sim Workspace". | Avoids duplicating an existing design. | n/a |
| D19 | Definition `version` is an integer. Removing or renaming a pin or param, or changing a param's meaning, bumps it. Adding a param with a default does not. | Simple and checkable. | Semver strings. |
| D20 | Search is case-insensitive AND over whitespace tokens, each a substring of the metadata. Ranking: exact name/id/alias match, then name prefix, then the rest. Ties sort by name. | Small, predictable, testable. | A fuzzy-search dependency. |

## 2. Definition format (`component.toml`, schema `format = 1`)

Rules: all top-level keys come before the first `[table]`. Unknown keys anywhere are an error. Paths are relative to the file's directory and must stay inside it.

| key | type | req | notes |
|---|---|---|---|
| `format` | int | yes | `1` |
| `id` | str | yes | `^[A-Za-z][A-Za-z0-9_]*$`, unique across all roots. Stored in files. |
| `version` | int ≥ 1 | yes | See D19. |
| `name` | str | yes | Display name. |
| `description` | str | no | One or two sentences, shown on the card and in details. |
| `designator` | str | no | `^[A-Z]+$`, default `"U"`. Auto-name prefix (R → R1). |
| `category` / `subcategory` | str | yes / no | Free text. Categories are listed in the TAXONOMY order (§3), then alphabetically. |
| `aliases`, `keywords`, `tags` | [str] | no | Search only. Never used to resolve ids. `tags` show as chips. |
| `domain` | str | yes | One of `electrical digital mcu audio rf optical mechanical`. |
| `capabilities` | [str] | yes | Vocabulary in §4 (may be `[]`). |
| `limitations` | [str] | no | Shown in the inspector and details. Required (≥1) when there is no `sim:*` capability. |
| `docs` | [str] | no | `http(s)://` URL or package-relative file. |
| `pins` | [table] | yes | `{id, name?, at=[x,y], description?}`. `id` is `^[A-Za-z0-9_]+$`, unique. `name` defaults to `id` and may repeat (three "GND"). Positions are distinct. |
| `params` | [table] | no | Fields map 1:1 to `components.Param`: `key label unit default kind choices min min_exclusive max when help`. `default` is a string. `unit` is in `"" Ω F H V A s Hz`. `when` is a table `{param = "choice"}`. |
| `[presentation]` | table | yes | `box = [x0,y0,w,h]` (required; rotation is about its centre). `symbol` (svg, or `{select="param", <choice>="file.svg"...}`), `icon` (svg), `value` (param key shown on the canvas), `indicator = "glow"`, `show_current` (bool), `animate_when = [["param","choice"], ...]`. |
| `[sim.<domain>]` | table | if `sim:<domain>` | Opaque to the catalog; checked by `SIM_DOMAINS[domain]`. |

Multi-pin geometry convention (not enforced): pins on multiples of 20 and `w`, `h` multiples of 40. That keeps pins on the 20 px grid in every rotation.

### Example A: implemented electrical part (`app/library/electrical/led/component.toml`)

```toml
format = 1
id = "LED"
version = 1
name = "LED"
description = "Red indicator LED. About 1.97 V forward drop at 9 mA."
designator = "D"                       # LED parts are D1, D2 ... (shares D numbering, as today)
category = "Electrical"
subcategory = "Semiconductors"
aliases = ["light emitting diode"]
keywords = ["indicator", "light", "diode"]
tags = ["optoelectronic"]
domain = "electrical"
capabilities = ["interface", "sim:electrical"]
limitations = ["Fixed red-LED model (Is=1e-20, N=1.8, Rs=5): colour is not a parameter.",
               "Brightness on the canvas is a display mapping of the simulated current (0.1 mA dark, 15 mA full)."]
docs = ["https://ngspice.sourceforge.io/docs.html"]

[presentation]
box = [0, 0, 60, 30]
symbol = "symbol.svg"                  # viewBox="0 -10 60 40": arrows overhang the body, which is fine
indicator = "glow"
show_current = true

[[pins]]
id = "a"
name = "Anode"
at = [0, 15]

[[pins]]
id = "k"
name = "Cathode"
at = [60, 15]

[sim.electrical]
device = "D"
line = "{dev} {a} {k} dled"
models = [".model dled D(Is=1e-20 N=1.8 Rs=5)"]
current = "@{dev_lc}[id]"
```

Diode (`D`) differs only in its params (`Is N Rs`, as `components.py:200-202`) and its binding: `line = "{dev} {a} {k} dmod_{dev_lc}"` and `models = [".model dmod_{dev_lc} D(Is={Is} N={N} Rs={Rs})"]`.

### Example B: unsupported future-domain part (`app/library/microcontrollers/esp32_devkitc/component.toml`)

```toml
format = 1
id = "ESP32_DEVKITC"
version = 1
name = "ESP32-DevKitC V4"
description = "Espressif development board with an ESP32-WROOM-32 module and two 19-pin headers."
designator = "U"
category = "Microcontrollers"
subcategory = "Development boards"
aliases = ["ESP32", "ESP-WROOM-32", "DevKitC"]
keywords = ["wifi", "bluetooth", "espressif", "mcu", "gpio"]
tags = ["3.3 V logic"]
domain = "mcu"
capabilities = ["interface"]           # no sim:* -> placeable, wireable, never simulated
limitations = ["No MCU emulation: firmware does not run and GPIO pins are not driven.",
               "Not part of the electrical simulation: Run is blocked while it is wired into the circuit."]
docs = ["https://docs.espressif.com/projects/esp-idf/en/stable/esp32/hw-reference/esp32/get-started-devkitc.html"]
# J2 (left, x=0) top to bottom, then J3 (right, x=160). T7 transcribes all 38 from the Espressif guide.
pins = [
  {id = "3V3", at = [0, 20]}, {id = "EN", at = [0, 40]}, {id = "GPIO36", name = "VP", at = [0, 60]},
  # ... remaining J2 pins every 20 px ...
  {id = "GND1", name = "GND", at = [0, 280]},
  {id = "GND2", name = "GND", at = [160, 20]}, {id = "GPIO23", name = "IO23", at = [160, 40]},
  # ... remaining J3 pins ...
  {id = "GPIO2", name = "IO2", at = [160, 300], description = "Strapping pin"},
]

[presentation]
box = [0, 0, 160, 400]                 # no symbol: generic labelled box (D11); icon optional
icon = "icon.svg"
```

## 3. Module layout and interfaces

The dependency direction is `components ← electrical ← catalog ← model ← sim`. Qt modules sit on top. There are no import cycles.

**`app/components.py`** (shrinks): `ModelError`, `parse_si`, `format_si`, `UNIT_ALIASES`, `Param` (adds `help: str = ""`), `ROTATIONS`, `rotate`. The `CATALOG` literal, the emit lambdas, `LED_MODEL`, `get`, `summary`, `terminal_offset` and `build_netlist` are deleted or moved.

**`app/electrical.py`** (new, pure; imports only `components`):
```python
DEVICE_LETTERS = "RCLDVI"           # letters the core validator models statically; extend after checking validate.rs
def check(section: dict, pins: tuple, params: tuple) -> list[dict]          # DEF-SIM diagnostics (field = "sim.electrical...")
def device_name(section, part_name) -> str        # name if it starts with letter else letter+name (SW1 -> RSW1)
def emit(section, part_name, nodes: dict, values: dict) -> tuple[list[str], list[str]]   # (lines, models)
def build_netlist(parts: dict, nets: dict, defs) -> dict     # same output dict as today; defs = Catalog
```
Rendering: `template.format(dev=..., dev_lc=dev.lower(), **nodes_by_pin_id, **values)`. Numbers use `repr(float(v))`, choices are inserted as text. A dict `line` picks `line[values[line["select"]]]`. The current vector is `current.format(dev_lc=...)`. Everything else in `build_netlist` keeps today's logic: derived `v(Part)` for 2-pin parts in pin order, `.save all` for `@` vectors, observable order, and signals.

**`app/catalog.py`** (new, pure):
```python
BUILTIN_ROOT = Path(__file__).parent / "library"
TAXONOMY = ("Electrical", "Microcontrollers", "Sensors", "Displays", "Communication", "Audio", "Power")
DOMAINS = ("electrical", "digital", "mcu", "audio", "rf", "optical", "mechanical")
CAPABILITIES = ("interface",)                    # plus "sim:<d>" for d in SIM_DOMAINS
SIM_DOMAINS = {"electrical": electrical.check}
@dataclass(frozen=True) class Pin: id; name; at: tuple; description = ""
@dataclass(frozen=True) class Definition:
    id; version; name; description; designator; category; subcategory; aliases; keywords; tags; domain
    capabilities: tuple; limitations: tuple; docs: tuple; pins: tuple; params: tuple
    box: tuple; symbol; icon; value_param; indicator; show_current: bool; animate_when: tuple
    sim: dict; path: Path                          # package directory
    terminals  (property) -> tuple of pin ids;  pos (property) -> {pin id: at}
    ground     (property) -> bool(sim.get("electrical", {}).get("ground"))
    simulated  (property) -> "sim:electrical" in capabilities
    readiness  (property) -> "simulated" | "interface" | "placeable"
    param(key) / defaults() / fields(params) / values(params, owner)   # bodies moved verbatim from Component
    symbol_for(params) -> Path | None               # resolves a {select=...} symbol
class Catalog:
    defs: dict[str, Definition]; problems: list[dict]; roots: list[Path]
    def get(self, id) -> Definition                 # ModelError(f"unknown component type {id}")
    def categories(self) -> list[str]               # only categories that have definitions
    def search(self, text="", category=None, simulated_only=False) -> list[Definition]
def load_definition(path: Path) -> tuple[Definition | None, list[dict]]
def default_roots() -> list[Path]
def reload(roots=None) -> Catalog                  # rebuilds CATALOG in place; returns it
CATALOG = Catalog(); reload()
def get(kind); def summary(kind, params); def terminal_offset(kind, terminal, rot=0)   # moved from components
def geometry(part: dict) -> tuple[tuple, dict]      # (box, pos) for a definition or a placeholder
def placeholder_geometry(pins) -> tuple[tuple, dict]   # box (0,0,80,h) with h = 40*ceil((n+1)/2); pin i at (0, 20*(i+1))
```
Search haystack: id, name, aliases, keywords, tags, description, category, subcategory.

**`app/model.py`**: imports `catalog`. A part dict is `{"kind","version","x","y","rot","params"}`, plus `"placeholder": {"reason": str, "pins": [ids]}` only for missing or incompatible parts. New methods:
- `definition(name) -> Definition | None` (None for placeholders).
- `pins(name)`, which is placeholder-aware.
- `unmodeled() -> list[name]`: parts that are placeholders or whose definition is not `simulated`.
- `wired(name) -> bool`.

`netlist()` passes only the modeled parts: `electrical.build_netlist({n: p ... if n not in unmodeled}, nets, CATALOG)`. The dead `KINDS` is deleted.

**`app/sim.py`**:
- `RunResult` gains `excluded: list`.
- On success the message reads `ok, N points; not simulated: U1, U2` whenever `excluded` is not empty.
- The `except` catches only `SIM_ERRORS = (simcore.InvalidCircuitError, InvalidConfigError, ConvergenceError, SolverError, BackendError, UnsupportedError, UnavailableError)`. `_diff` runs after the `try`, so app bugs propagate to `window.run`'s "Internal error" path (R6).

**Qt modules:**
- `app/symbols.py`: `renderer(path, color) -> QSvgRenderer | None` (lru_cache), `add_symbol(scene, part, defn, color) -> list[QGraphicsItem]`, `add_generic_body(scene, transform, box, pins, title, color)`, `icon_pixmap(defn, px=40) -> QPixmap`.
- `app/theme.py`: tokens and `QSS`.
- `app/library.py`: `LibraryPanel(QWidget)`, which emits `place_requested(id)`.
- `app/inspector.py`: `Inspector(QWidget)`.
- `app/canvas.py`: `Canvas`, `pin_pos`, `wire_pts`, `part_rect`, `part_transform`.
- `app/results.py`: `ResultsPanel` (signal picker, plot, slider, readout, findings, details).
- `window.py` re-exports `pin_pos`, `wire_pts` and `part_rect` for existing tests.

## 4. Capability model

| capability | meaning | consistency rule (DEF-CAPABILITY on violation) |
|---|---|---|
| (none) | Placeable: can be placed, moved, saved and documented. Every valid definition is placeable. | Must have ≥1 `limitations` entry if it has no `sim:*`. |
| `interface` | `pins` is the real, complete interface. The part can be wired. | Required if and only if `pins` is not empty. |
| `sim:electrical` | A working electrical model through the existing engine. | Requires a `[sim.electrical]` section that passes `electrical.check`. A section without the capability is also an error. |
| `sim:<other>` | Reserved. | Error unless `<other>` is in `SIM_DOMAINS`. The message says: "no '<other>' simulation domain exists; describe it under limitations". |

The UI readiness label is `Definition.readiness`: `simulated` → "Simulates", `interface` → "Not simulated", `placeable` → "Reference only". Run pre-check codes (from `Document.validate`):

| code | severity | when | example message |
|---|---|---|---|
| `NOT-SIMULATED` | error | part has no `sim:electrical` and ≥1 wired pin | `U1 (ESP32-DevKitC V4) has no electrical model and is wired to the circuit: disconnect it to run.` |
| `NOT-SIMULATED` | warning | same, but no wires | `U1 (ESP32-DevKitC V4) is not simulated and is excluded from the run.` |
| `MISSING-DEFINITION` | error/warning (same split) | placeholder, reason missing | `U2: component type 'FOO' (version 1) is not in the catalog; kept unchanged.` |
| `INCOMPATIBLE-DEFINITION` | error/warning | placeholder, reason incompatible | `U3: saved with RELAY version 3; this catalog has version 2.` |
| `DUP-NAME-CASE` | error | two names equal case-insensitively (R5) | `r2 and R2 differ only in case; SPICE names are case-insensitive.` |

Two existing checks change:
- `EMPTY` counts only modeled, non-ground parts. The message becomes `Nothing to simulate: place simulated components.` when only unmodeled parts exist.
- `UNCONNECTED-PART`, `UNCONNECTED-PIN` and `BAD-PARAM` skip unmodeled parts, so they are not reported twice.

## 5. Validation and diagnostics

A definition diagnostic is a dict: `{"code", "severity": "error"|"warning", "message", "file", "field", "id"}`. `field` is a dotted path such as `pins[3].at` or `sim.electrical.line.PULSE`. `id` is None when unknown. Message style: `"<what is wrong>; <how to fix>"`. Errors reject the definition: it is absent from `CATALOG.defs`, and every diagnostic is in `CATALOG.problems`. Warnings keep it, with a fallback.

| code | sev | rules |
|---|---|---|
| `DEF-PARSE` | error | Unreadable file or invalid TOML (message includes the `tomllib` line and column). |
| `DEF-FIELD` | error | Missing required key, wrong type, unknown key (hint: "top-level keys must come before the first [table]"), bad `id` regex, `format != 1`, `version < 1`, unknown `domain`, path escaping the package, `indicator`/`show_current` without `sim.electrical.current`, `value` or `animate_when` naming an unknown param or choice. |
| `DEF-DUP-ID` | error | Id already loaded from an earlier path. The first one wins and the message names both files. |
| `DEF-PIN` | error | Bad or duplicate pin id, `at` not two finite numbers, two pins at the same position. |
| `DEF-PARAM` | error | Duplicate key, key clashing with a pin id or `dev`/`dev_lc`, unknown unit, `default` not a string or not passing `Param.parse`, choice default not in `choices`, choices not matching `^[A-Za-z0-9_.+-]+$`, `when` naming a non-choice param or an unknown choice. |
| `DEF-CAPABILITY` | error | Rules in §4. |
| `DEF-SIM` | error | From `electrical.check` (below). |
| `DEF-ASSET` | warning | `symbol`, `icon` or a doc file is missing, is not well-formed XML (`xml.etree`), has a root other than `svg`, or has no 4-number `viewBox`. Fallback: generic body or generic icon. |

`electrical.check` rules:
- `device` is in `DEVICE_LETTERS`.
- The section has exactly one of `line` and `ground = true`.
- `ground` sections have no `device`, `line`, `models` or `current`, and the part has exactly one pin.
- `line` is a str, or a table `{select = <choice param>, <every choice> = str}` with no extra keys.
- Every template is a single line. A `line` starts with `{dev} `, and each model starts with `.model `.
- Placeholders are plain identifiers (no `.`, `[`, `!` or `:`; checked with `string.Formatter().parse`).
- Line placeholders must be in `{dev, dev_lc} ∪ pin ids ∪ applicable params`. In a plain line an applicable param is one without `when`. In branch X of `select = S`, it is one whose `when` ⊆ {S: X}.
- Every pin id appears in every line branch, otherwise a terminal would be left out of SPICE.
- `models` placeholders are limited to `dev`, `dev_lc` and params without `when`.
- `current` matches `^@\{dev_lc\}\[[a-z]+\]$` or `^i\(\{dev_lc\}\)$`.

## 6. Electrical domain binding

Today's per-kind code becomes data (the netlist text must stay byte-identical; the golden test is `test_components.py::test_netlist_emission_all_kinds`, lines 94-108):

| id | device | line | models | current | presentation |
|---|---|---|---|---|---|
| R | R | `{dev} {a} {b} {R}` | | `@{dev_lc}[i]` | value R |
| C | C | `{dev} {a} {b} {C} IC={ic}` | | `@{dev_lc}[i]` | value C |
| L | L | `{dev} {a} {b} {L} IC={ic}` | | `@{dev_lc}[i]` | value L |
| V | V | select mode: DC `{dev} {p} {n} DC {dc}`, PULSE `{dev} {p} {n} PULSE({v1} {v2} {td} {tr} {tf} {pw} {per})`, SIN `{dev} {p} {n} SIN({vo} {va} {freq})` | | `i({dev_lc})` | value dc, animate_when mode PULSE/SIN |
| BAT | V | `{dev} {p} {n} DC {dc}` | | `i({dev_lc})` | value dc, designator V |
| I | I | `{dev} {p} {n} DC {dc}` | | (none, D15) | value dc |
| D | D | `{dev} {a} {k} dmod_{dev_lc}` | `.model dmod_{dev_lc} D(Is={Is} N={N} Rs={Rs})` | `@{dev_lc}[id]` | show_current |
| GND | (none) | `ground = true` | | | box (-16,8,32,24), pin g (0,10) |
| LED | D | `{dev} {a} {k} dled` | `.model dled D(Is=1e-20 N=1.8 Rs=5)` | `@{dev_lc}[id]` | indicator glow, show_current, designator D |
| SW | R | select state: open `{dev} {a} {b} {roff}`, closed `{dev} {a} {b} {ron}` | | `@{dev_lc}[i]` | value state, symbol select state |

Boundary: `simcore` is reached only from `sim.py`, and the catalog never sees SPICE text. Removed per-kind UI code:
- `SHOW_CURRENT` and `GLOWS` become `show_current` and `indicator`.
- The `mode != "DC"` autoplay rule becomes `animate_when`.
- `_switch` reading `state` becomes a `symbol` select.
- `led_level`, `I_OFF` and `I_FULL` stay in `model.py` as the single "glow" mapping. `# ponytail: one glow mapping; move thresholds into presentation when a second glowing part needs others`.

## 7. Rendering and symbol strategy

- An SVG's user units are part-local coordinates, the same as pin `at` and `box`. `add_symbol` builds the item transform as `scale(vb.w/default.w, vb.h/default.h) · translate(vb.x, vb.y) · part_transform(part)`. This construction was verified in experiment 3.
- Colour: the SVG source has `currentColor` replaced with the state colour before it is loaded (`INK`, `SEL`, or theme tokens). The renderer is cached per (path, colour).
- Fallbacks, in order: `symbol_for(params)`, then the generic body. The generic body is a rounded rectangle on `box`, with a pin stub and the pin `name` at each pin, the designator plus name centred, and a dashed outline and a "?" badge for placeholders.
- Badges: a part whose readiness is not `simulated` gets the theme's "not simulated" badge (small icon at the box's top-right) with tooltip `limitations[0]`.
- Library icon: `icon`, else the rendered `symbol`, else a generic glyph (designator letters in a rounded square, coloured by category).
- T5 converts the 10 painters in `window.py:97-146` to `symbol.svg` files with identical coordinates. The `_lines` y-offset of +15 is applied: a path point (x, y) becomes (x, 15+y). SW gets two files, `switch-open.svg` and `switch-closed.svg`.
- SVG files use only `path`, `line`, `circle`, `rect` and `polyline` with `fill="none" stroke="currentColor" stroke-width="2"`.

## 8. Persistence v2, migration, missing definitions

The writer always emits v2. The reader accepts v0, v1 and v2. Format v2 is v1 plus `"version": 2` and a per-component `"version"` (the definition version):

```json
{"format": "sim-circuit", "version": 2,
 "components": {"V1": {"type": "BAT", "version": 1, "params": {"dc": "5"}},
                "R1": {"type": "R", "version": 1, "params": {"R": "330"}},
                "D1": {"type": "LED", "version": 1, "params": {}},
                "GND1": {"type": "GND", "version": 1, "params": {}},
                "U1": {"type": "ESP32_DEVKITC", "version": 1, "params": {}},
                "U2": {"type": "FUTURE_RELAY", "version": 3, "params": {"coil": "5"}}},
 "connections": [["V1.p", "R1.a"], ["R1.b", "D1.a"], ["D1.k", "GND1.g"], ["V1.n", "GND1.g"], ["U2.COM", "U2.NO"]],
 "simulation": {"analysis": "transient", "tstop": "5m", "tstep": "10u"},
 "layout": {"V1": {"x": 40, "y": 100, "rotation": 0}, "U1": {"x": 600, "y": 40, "rotation": 0},
            "U2": {"x": 600, "y": 500, "rotation": 90}}}
```

- **Migration v1 to v2:** each component gets `"version": 1`, because every v1 file only holds the 10 built-ins at version 1. The rest is unchanged. v0 loads through the existing path and is then treated as v1. A file version above 2 is refused, as today. The migration happens only in memory. A file is rewritten only when the user saves.
- **Load per component.** Let `d` be the catalog definition for `type`, if any:
  1. No `d`: placeholder with reason `missing`.
  2. `version > d.version`: placeholder with reason `incompatible`.
  3. A param key not in `d.params`, or a connection to a pin not in `d.terminals`: placeholder with reason `incompatible`.
  4. Otherwise a normal part. Missing params are filled from defaults. The stored version becomes `d.version` (the part now conforms to it).
- A **placeholder** keeps `type`, `version` and raw string `params` verbatim. Its pins are the sorted pin names that its connections reference. It can be moved, rotated, deleted and wired to its existing pins. `set_param` raises `ModelError("U2 has no usable definition; its parameters are read-only")`. `add_part` of an unknown type still raises (only loading creates placeholders). Saving writes the record back exactly as loaded.
- Schema errors stay whole-file errors, as today: bad JSON, wrong types, bad names, bad layout, a connection whose part does not exist, duplicates, a non-transient analysis. Unknown extra keys stay ignored (v1 promise, `docs/frontend-components.md:157-161`).
- After an open with placeholders, the window shows a status-bar warning `2 parts have no usable definition: U2 (FUTURE_RELAY v3), ...`, and Problems lists them. Undo and redo go through `to_dict`/`from_dict`, so placeholders survive history.
- T6 rewrites the "Save format" section in `docs/frontend-components.md` (v2, compatibility rules, placeholder behaviour).

## 9. UI structure Penpot must cover (T3/T4)

The main window is 1280×800, with a minimum of 1024×640. The deliverables are Penpot frames, plus `docs/milestone/03-design.md`. That document gives tokens as hex/px tables (colour, type scale in pt, spacing, radii, icon size), each frame's purpose, and the interaction specs that T8 and T9 copy into `theme.py`. Keep the existing identity:
- white canvas with a 20 px grid;
- wire blue `#1a5fb4`, selection orange `#e07000`, hover `#2a7fff`;
- pin states green `#2fa82f` (wired) and hollow red `#d02020` (open);
- run-state colours idle/running/completed/failed from `window.py:32`.

Use Noto Sans (installed, and available in Penpot). Chrome is light, with no dark-only design.

Screens and regions:
1. **Workspace layout.** A top bar with file actions, undo/redo and the simulation controls. A left library panel about 280 px wide. The central canvas. A right inspector about 320 px wide. A bottom or right results area for plot, cursor and findings. A Problems list. A status bar.
2. **Library panel.** Search field. Category navigation (an "All" option plus categories from the catalog with counts). A "Simulated only" toggle. Card list (icon, name, one-line description, readiness badge, tags). Hover, selected and placing (armed) states. A details view for a card: description, pins table, params with defaults and units, limitations, docs links, version and id.
3. **Library states:** empty search ("No components match 'xyz'", with a clear-search action); empty category; a catalog-problems banner ("2 definitions could not be loaded", which expands to file · code · message); a card with a missing icon (fallback glyph); a loading skeleton (designed for completeness, since loading is synchronous today).
4. **Canvas.**
   - Parts: idle, hover, selected, dragging.
   - Pins: open, wired, hover, pending wire with rubber band. Junction dot.
   - Wires: hover, selected.
   - Rotate and delete affordances.
   - Zoom controls (−, %, +, fit) and the pan cursor.
   - Empty-canvas hint (replaces the HINT text strip).
   - Unsupported badge on a placed ESP32. Placeholder for a missing definition (dashed, "?").
   - Core-finding ring on pins. LED glow and current label.
5. **Inspector.**
   - Nothing selected.
   - Part: header with icon, name, type, readiness badge and version. Param form from the schema: number with unit and SI hint; choice as a dropdown; `when`-dependent fields appearing; invalid field state and message.
   - Pins list (name, id, wired/open).
   - Limitations block. Docs links.
   - Wire selected (endpoints, "plot this node"). Placeholder selected (read-only params, reason, "kept unchanged").
6. **Simulation controls:** analysis label "Transient"; stop time and time step fields (valid and invalid); Run, Re-run and Reset; state badge (idle, running, completed, failed, stale "results out of date"); a run blocked by pre-check errors.
7. **Results:** signal picker grouped node/across/current; the plot with its cursor line; play/pause and the time slider; cursor readout; final/min/max stats; core findings; "Show details" developer text; the success message with "not simulated: U1"; a failure state (message and no plot).
8. **Dialogs:** unsaved changes; open failed (bad file); opened with placeholders (non-blocking banner).

T3 must not design anything the plan does not implement: no multi-select, no copy/paste, no breadboard view, no code editor, no MCU panels.

## 10. Per-task specs

All tests run with `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests`. Every task ends with the full suite green, and existing tests may only be edited where noted. Catalog tests use a fixture in `app/tests/conftest.py`: `extra_root(tmp_path)` calls `catalog.reload([BUILTIN_ROOT, tmp_path])` and, at teardown, `catalog.reload()`. Helper `write_def(root, id, **overrides)` writes a valid minimal TOML.

### T5: catalog core, validation, electrical binding, migration of the 10 kinds, SVG symbols
Files: new `app/catalog.py`, `app/electrical.py`, `app/symbols.py`, and `app/library/**` (10 packages: `component.toml`, `symbol.svg`). Edited: `app/components.py`, `app/model.py`, `app/window.py` (minimal), `app/tests/test_components.py`, `test_gui_workflow.py`, `test_analytic_diode.py`.

Steps:
1. Before touching code, add `test_template_netlist_matches_golden`. Paste in literal lines produced by the current code for V in PULSE and SIN mode, SW open, and D with non-default Is, N and Rs; these complement `test_netlist_emission_all_kinds`. Both tests are the oracle.
2. Write `electrical.py` and `catalog.py` per §3, §5 and §6.
3. Author the 10 TOML files from `components.py:142-214`, using exact labels, defaults, bounds, `when`, value params and designators. Add descriptions and keywords. Categories:
   - Electrical/Passives: R, C, L.
   - Electrical/Sources: V, I.
   - Power/Batteries: BAT.
   - Electrical/Semiconductors: D, LED.
   - Electrical/Switches: SW.
   - Electrical/Reference: GND.
4. Convert the painters to SVG (§7).
5. In `model.py`: use `catalog` and add `version` to the part dict. The window drawing uses `symbols.add_symbol`, plus `show_current`, `indicator` and `animate_when` from the definition. `LABELS` uses `name`. The parts toolbar stays (T8 replaces it).
6. Delete `SYMBOLS`, `KINDS` and `LED_MODEL`. Update the test imports. `test_analytic_diode.py:101` asserts the literal model string from the netlist.

Acceptance:
- `CATALOG.problems == []`.
- The golden netlist is unchanged.
- The LED example gives I(D1) = 9.1772 mA (rel 1e-4).
- Nothing in `app/` outside `library/` mentions a kind id, except the examples in `model.py`, `load_demo`, and tests. Check with grep for `"LED"\|"SW"\|"mode"` in `window.py` (expect 0).

Tests in new `app/tests/test_catalog.py`:
- `test_builtin_catalog_has_ten_kinds_and_no_problems`
- `test_definition_discovered_under_category`: a tmp def appears in `categories()`, `get()` and `search()`.
- `test_search_name_alias_keyword_and_ranking`: `search("light emitting")[0].id == "LED"`. `search("resistor")[0].id == "R"`. `search("zzz") == []`.
- `test_category_filter_and_simulated_only`
- `test_invalid_definitions` (parametrized). Each case asserts code, field and that the definition was rejected:
  - missing id → DEF-FIELD `id`
  - broken TOML → DEF-PARSE
  - unknown key `presentation.pins` → DEF-FIELD
  - duplicate pin → DEF-PIN
  - default `"abc"` → DEF-PARAM
  - `sim:electrical` without a section → DEF-CAPABILITY
  - `sim:mcu` → DEF-CAPABILITY
  - line omitting a pin → DEF-SIM
  - `{dev.__class__}` → DEF-SIM
  - device `"X"` → DEF-SIM
  - duplicate id across roots → DEF-DUP-ID, with the built-in kept
- `test_missing_symbol_is_warning_with_fallback`
- `test_bad_definition_does_not_block_good_one`
- `test_template_netlist_matches_golden`: all 10 kinds in all modes, through `electrical.build_netlist`.
- `test_build_netlist_refuses_unmodeled_part` (ModelError, no skip).
- Adapted `test_every_catalog_symbol_drawn_with_pins_at_its_ends`: the SVG item's scene bounds, adjusted by ±1, contain each pin in all 4 rotations.

### T6: persistence v2, capability pre-check, R5/R6
Files: `app/model.py`, `app/sim.py`, `app/history.py` (only if needed), `app/catalog.py` (`geometry`), `app/window.py` (geometry helpers via `catalog.geometry`, generic body for placeholders, warning after open), `docs/frontend-components.md`, new `app/tests/test_persistence_v2.py` and `test_capabilities.py`. Behaviour follows §4 and §8 exactly.

Tests (use a tmp interface-only def, `TESTBOARD` with 4 pins, through `extra_root`):
- `test_v1_file_migrates`: the audit §5 dict loads, and `to_dict()` has version 2, per-part version 1, and identical params, connections and layout.
- `test_roundtrip_led_plus_interface_only_part`: save, load and save give byte-identical JSON. The part's params and layout are intact.
- `test_missing_definition_kept_verbatim`: the U2 record from §8 survives a save with the same type, version, params and connection. Its pins are `["COM","NO"]`.
- `test_newer_version_and_unknown_param_become_incompatible`
- `test_alias_is_not_resolved`: `"type": "ESP32"`, an alias, stays missing.
- `test_placeholder_params_read_only_and_undo_redo_preserves_it`
- `test_wired_unsupported_blocks_run`: `ok False`, code `NOT-SIMULATED`, `"--- netlist" not in r.details`, so the core was never called.
- `test_unwired_unsupported_runs_rest`: `ok`, `excluded == ["U1"]`, `"not simulated: U1" in message`, I(D1) final = 9.1772e-3 (rel 1e-3).
- `test_only_unsupported_parts_is_empty_error`
- `test_case_only_duplicate_names_reported_before_run` (R5).
- `test_app_bug_propagates_not_labelled_sim_error` (R6): monkeypatch `sim._diff` to raise `ZeroDivisionError`, then `pytest.raises`.

Acceptance: the existing `test_persistence_roundtrip.py` passes unchanged except for the expected version value.

### T7: seed packages and authoring docs
Files: `app/library/**`, new `docs/adding-components.md`, new `app/tests/test_seed_packages.py`. No code changes. If one seems needed, report it instead of making it.

Seeds:
- **Interface-only, real pinouts taken from the cited source, never guessed:**
  - `ESP32_DEVKITC` (Microcontrollers, 38 pins per Espressif DevKitC V4 J2/J3).
  - `ARDUINO_UNO_R3` (Microcontrollers, header pins per the Arduino Uno Rev3 docs).
  - `DHT22` (Sensors: VDD, DATA, NC, GND).
  - `SSD1306_I2C` (Displays, 4-pin GND VCC SCL SDA module; limitation: "pin order varies by vendor").
  - `PIEZO_BUZZER` (Audio, 2 pins; limitation: no audio or electro-mechanical model).
- **Implemented from data only:** `D_1N4148` (Electrical/Semiconductors, `.model` with Is=2.52n N=1.752 Rs=0.568). This proves an electrical part needs no Python.
- Leave Communication empty rather than inventing an entry.

`adding-components.md` covers:
- directory layout;
- the full annotated TOML for both kinds;
- param and pin rules;
- SVG rules (§7);
- the capability table;
- version rules (D19);
- how to validate: `python3 -m app.catalog [root]` prints problems and exits 1 on errors. Add this 10-line `__main__` to `catalog.py`, the only code T7 may add;
- the error-code table (§5);
- `SIM_COMPONENT_PATH`.

Tests:
- `test_seeds_load_cleanly`
- `test_esp32_pins`: 38 unique ids, 3 pins named GND, GPIO2 present, readiness `interface`.
- `test_categories_present`: Electrical, Power, Microcontrollers, Sensors, Displays and Audio are present. Communication is absent.
- `test_no_seed_claims_unregistered_simulation`
- `test_1n4148_series_current`: 5 V, 1 kΩ, 1N4148 to ground. The simulated I matches a bisection solve of `I = Is·(exp((V−I·Rs)/(N·Vt))−1)` with `V = 5 − 1000·I` and `Vt = 0.025852` (300.15 K) to rel 2e-3.
- `test_doc_examples_validate`: extract each ```toml block from `adding-components.md`, write it, and `load_definition` it with no errors.

### T8: UI A, library panel, inspector, theme
Files: new `app/theme.py`, `app/library.py`, `app/inspector.py`. Edited: `app/symbols.py` (`icon_pixmap`), `app/window.py` (replace the parts toolbar and inspector form, apply the theme). New `app/tests/test_library_ui.py`. Follow the T3 design and record deviations in `docs/milestone/08-ui-notes.md`.

Behaviour:
- Cards come from `CATALOG.search(...)`.
- Clicking a card arms placement (`window.set_tool(id)`), and clicking the canvas places the part. A drag from a card with mime `application/x-sim-component` set to the id drops at the snapped point (T9 handles the drop on the canvas).
- Inspector fields come from `Param` metadata (number → `QLineEdit` with unit suffix and SI hint, choice → `QComboBox`, `help` → tooltip). Live validation reuses `doc.check_param`.
- The pins list shows wired/open state. Limitations, docs (`QDesktopServices.openUrl`), readiness, and the placeholder view are shown.

Tests:
- `test_library_shows_all_definitions_by_category`: card count equals `len(CATALOG.defs)`.
- `test_search_and_empty_state`: typing "light" shows LED. Typing "zzz" shows the empty-state label with the query.
- `test_category_filter`
- `test_card_click_then_canvas_click_places_part`
- `test_new_definition_appears_without_code`: with `extra_root`, a new MainWindow has the card (name, generic icon); it places and wires the part, and the inspector shows its unit field.
- `test_inspector_from_schema_mode_switch`: V1 in PULSE shows 7 fields. Typing "abc" sets the error style and message.
- `test_unsupported_part_inspector`: pins, limitations and the "Not simulated" badge are present.
- `test_catalog_problems_banner`: a bad def shows a banner with count 1, and the text contains the file name and the code.
- `test_missing_icon_fallback`

### T9: UI B, canvas and simulation UI
Files: new `app/canvas.py` (moved from `window.py`) and `app/results.py`. Edited: `app/window.py`, `app/symbols.py` (badges). New `app/tests/test_canvas_ui.py`.

Behaviour:
- **Zoom:** Ctrl+wheel and Ctrl+= / Ctrl+− / Ctrl+0 (fit), range 25-400 %. Zoom changes only the `QGraphicsView` transform.
- **Pan:** middle-drag or Space+drag, plus scrollbars. The scene rect grows to the items' bounds plus a 200 px margin.
- Drop from the library.
- Move, rotate (R), delete (Del), "Disconnect all" in the part context menu. Wire create and remove are kept.
- Unsupported badge and placeholder drawing (§7).
- Results panel extracted unchanged in behaviour. The state badge gains "stale".
- Pin hit radius scales with zoom (stays 10 screen px).

Tests:
- `test_zoom_keeps_hit_testing`: at 200 % a QTest click on two pins creates the wire.
- `test_fit_shows_all_parts`
- `test_pan_moves_view_not_parts`
- `test_place_move_rotate_delete_by_mouse_and_keys`
- `test_disconnect_all`
- `test_badge_on_interface_only_part`: a scene item carries a tooltip equal to the first limitation.
- `test_placeholder_drawn_and_movable`
- `test_run_blocked_by_wired_unsupported_shows_failure_without_plot`

The existing `test_led_e2e`, `test_led_pixels` and `test_gui_workflow` stay green (imports adjusted only).

### T10: integration and E2E (real catalog, persistence and engine; no mocks)
New file `app/tests/test_milestone_e2e.py`. Every test drives `MainWindow` through QTest or its public actions.

Scenarios:
1. Discovery: ESP32 is under Microcontrollers in the library.
2. Search plus filter through the UI.
3. Extensibility: a tmp electrical def (a copy of 1N4148 with id `TEST_DIODE`) is placed and wired via the UI into 5 V / 1 kΩ, then Run. The current matches the bisection solve (rel 2e-3).
4. ESP32: place, inspect 38 pins, save, open in a new window. Type, version, params and layout are equal, and the JSON record is checked.
5. Capability: wire the ESP32 to R1 and Run. The state is failed, the message names U1, and there are no signals. Remove the wire and Run again: success, the status contains "not simulated: U1", and I(D1) = 9.1772 mA (rel 1e-3).
6. Invalid definition root: the app starts, the banner lists it, and the other parts work.
7. Missing definition: open the §8 file. The placeholder is drawn, and a save keeps the U2 record byte-identical.
8. Regressions (D17):
   - M04: two Grounds are both node 0.
   - M07: C `ic=2` is saved, emitted and v(C) at t=0 is 2 V ± 1e-3.
   - M12: diode Rs=100 changes the current per the analytic solve.
   - M16: PULSE tr ≠ tf produce different edge durations in the waveform (±1 step).
   - M17: SIN amplitude 1 V, measured peak 1 V ± 2 %.
   - M18: L ic=1 mA gives i(L) at t=0 = 1 mA.
   - RC: v(C) at τ = 3.1606 V ± 0.5 %.

## 11. Risks

1. **Netlist drift during the T5 migration.** Golden-text test first. Byte-identical lines are required.
2. **TOML trap (keys after a table).** Unknown-key error with an explicit hint, plus a docs section.
3. **Global catalog leaking between tests.** The `extra_root` fixture always reloads at teardown. Never mutate `CATALOG.defs` directly.
4. **SVG conversion changes visuals or the pin test.** Coordinates are copied from the painters. The LED pixel tests depend on the glow overlay, which is unchanged.
5. **T8 and T9 both edit `window.py`.** Run them sequentially. T8 does not touch canvas drawing, and T9 does not touch the library or inspector.
6. **Wrong seed pinouts.** Cite the source in `docs` for each board. The T11 review checks the pin lists against the source.
7. **Templates as an injection path.** Identifier-only placeholders, a `{dev} ` prefix, `.model` only, a fixed letter set, numeric values via `repr(float)`, and choices matching a regex. The core still refuses control-flow escapes.
8. **Penpot details impractical in Qt** (blur, custom fonts, complex shadows). T8/T9 adapt and log each deviation with a reason. The design note should prefer flat styles.
9. **Partial-run semantics (D7) may surprise users.** The message and the Problems list always name excluded parts. Reviewers (T11/T12) should challenge this rule explicitly.

## 12. Amendments after design review (T4)

- A1: A Run refused by the pre-check (e.g. a wired unsupported part) uses run state **`blocked`** (badge "Blocked", no plot, message names the parts), distinct from `failed` (the engine ran and errored). T9/T10 tests that say "failed" for a pre-check refusal must assert `blocked` instead (e.g. `test_run_blocked_by_wired_unsupported_shows_failure_without_plot` → asserts blocked state).
- A2: Readiness badge text is "Interface only"; "Not simulated" is the tooltip/message wording. T8 tests assert both (see 03-design.md §9.4 R3).
- A3: Run/Re-run/Reset enablement follows 03-design.md §9.4 R2.
- A4: Library card highlight vs inspector selection follows 03-design.md §9.4 R1. The card details view opens on right-click or Shift+Enter (T8).
- A5: Category counts in the design are illustrative; real counts come from the catalog. Power is populated by BAT (§10 T5).

## 13. T5 notes (implementation choices where the plan was silent or needed a smaller step)

- **Persistence stays v1 in T5.** The part dict carries `"version"` (definition version) in memory, but `to_dict` still writes v1 without it (T6 owns v2), so `test_v1_schema` and the roundtrip tests are unchanged.
- **Message change:** `Catalog.get` raises `unknown component type X` (§3), so the file-load rejection text changed from `unknown part kind`. T6 turns that rejection into a placeholder.
- **Golden oracle:** `app/tests/fixtures/netlist_golden.json` was captured from the pre-catalog `components.build_netlist` (before any code change) by `app/tests/golden_netlists.py` (11 circuits covering all 10 kinds, V DC/PULSE/SIN, SW open/closed, D with custom Is/N/Rs, C/L `ic`, shared LED model). `test_catalog.py::test_template_netlist_matches_golden` compares full dicts (lines, models, saves, observables, derived, signals). Regenerate only on a deliberate netlist change: `python3 -m app.tests.golden_netlists --bless` (T17: without `--bless` it refuses).
- **Model API:** added `Document.definition(name)` and `Document.unmodeled()`; `netlist()` passes only modeled parts. Placeholder-aware `pins()`, `wired()`, `catalog.geometry/placeholder_geometry` are T6.
- **Extra `electrical.check` rules:** pin ids of an electrical part must be plain identifiers (`3V3` cannot be a `{placeholder}`) and not `dev`/`dev_lc`; unknown keys in `[sim.electrical]` are DEF-SIM (not DEF-FIELD). Catalog adds: unknown capability strings and a `[sim.<d>]` section without its capability are DEF-CAPABILITY; a non-existent root directory is a DEF-PARSE warning.
- **Symbols:** `part_transform` moved from `window.py` to `symbols.py` (window imports it). `symbols.py` has `renderer`, `add_symbol`, `add_generic_body` (minimal: rounded box, pin stubs and names, title; placeholder styling and badges are T9) but not `icon_pixmap` (T8). SVG strokes are a fixed 2 px, so a selected part is no longer drawn 2.5 px (colour and selection rectangle still mark it). Switch dots are hollow circles (fill none), previously filled-looking.
- **Removed from `window.py`:** `SYMBOLS`, `symbol()`, `SHOW_CURRENT`, `GLOWS`, `LABELS` (the toolbar and status text read `Definition.name` directly; LABELS would go stale on `catalog.reload`).
- **Tests:** `conftest.py` gained `extra_root`, `write_def(root, id, mutate=None, reload=True, **top)` and `minimal_def`; test modules import them with `from conftest import ...`. XML for SVG checks uses stdlib `xml.etree` as planned (no new dependency); the files are local package files, not network input.

## 14. T6 notes (implementation choices where the plan was silent)

- **`blocked` covers every pre-check refusal**, not only capability errors (A1: "a Run refused by the pre-check"). `run_document` returns status/state `blocked` for any `validate()` error (and for the `ModelError` safety net after it); `failed` is only for core (`simcore.SimError`) errors. The window prefixes the status `BLOCKED:`, the error box reads `Run blocked: ...`, the state colour is `#a05000`. Existing tests that asserted `failed` for pre-check refusals (`test_components`, `test_e2e`, `test_gui_review`, `test_gui_workflow`, `test_led`, `test_led_e2e`) were changed to `blocked`/`BLOCKED`; core-error cases (NO-DC-PATH) stay `failed`.
- **Exclusion is enforced at the netlist boundary.** `Document.netlist()` returns the `build_netlist` dict plus `"excluded"` (all unmodeled parts) and raises `ModelError` if any unmodeled part is wired; `sim.py` builds the success message and `RunResult.excluded` from that key only, so no success path can omit an excluded part. The golden comparison in `test_catalog.py` now expects the extra `"excluded": []` key from `Document.netlist()`.
- **Net numbering:** `Document.nets()` names modeled pins first, so an excluded part never renumbers `n1..` (a run with an unwired board is identical to one without it, asserted).
- **Placeholder record:** `{"placeholder": {"reason", "pins", "detail"}}` on the part dict; `detail` is the human text used in the diagnostic (`U2: component type 'FOO' (version 1) is not in the catalog; kept unchanged...`). Not persisted: it is re-derived on every `from_dict`, including undo/redo. Consequence: a placeholder's pin list is re-derived from the connections present, so if the user deletes all its wires and then undoes/redoes through a state without them, the pins that no wire references are gone (they were never stored). Placeholder `params` are deep-copied verbatim (not coerced to text); unknown extra keys in a record are dropped, as for every part (v1 promise).
- **Diagnostic wording** for MISSING/INCOMPATIBLE: wired errors end `It is wired to the circuit: disconnect it to run.`, unwired warnings end `; excluded from the run.`.
- **Pin ids in connections** must match `PIN_RE` (`[A-Za-z0-9_]+`), else whole-file `no such pin`; this keeps junk out of placeholders.
- **Edge cases:** `add_part` of an unknown type still raises (only loading creates placeholders). Version in a v2 record must be an int >= 1. `DUP-NAME-CASE` is reported by `validate()` only; `rename_part` still allows `r2` when `R2` exists (plan: caught at validate).
- **R6:** `sim.SIM_ERRORS` lists the 7 concrete `simcore` error classes (all subclass `SimError`); `_diff` runs after the `try`, so app bugs reach `window.run`'s "Internal error" path.
- **Window (minimal):** geometry via `catalog.geometry/pin_offset` (`terminal_offset` kept as a wrapper), placeholders draw with `symbols.add_placeholder` (generic body, no dashed style/badge: T9), inspector shows the reason and read-only note, status bar warns after opening a file with placeholders (`1 part has no usable definition: U2 (FUTURE_RELAY v3)`). No panels.
- **Docs:** new `docs/persistence.md`; the "Save format" section of `docs/frontend-components.md` now points to it and the validation table / `RunResult` text were updated.

## 15. T13a notes (fixes for the T11/T12 findings that cross modules)

Each fix sits at the boundary that owns the rule and has a test that failed on the previous code.

- **F1 (model-name collisions).** Two boundaries. Catalog load (`catalog.reload`): a definition whose placeholder-free
  model name is already used with a different body (names compared case-insensitively, bodies whitespace- and
  case-normalised, `electrical.model_key`) is rejected with `DEF-SIM` on the later definition (the earlier one wins,
  as for DEF-DUP-ID). Netlist build (`electrical.build_netlist`): the backstop for names the catalog cannot see (a fixed
  name with a per-instance body): `ModelError("model 'x' is defined differently by A and B")` or "gets different
  parameters in different A parts". Identical bodies are shared. `check` also rejects a `.model` with no name or type.
- **F2 (template grammar).** In `electrical.check`: a device letter means exactly two pins; `line` starts with `{dev} `;
  the two node fields are exactly the two pin placeholders, once each, and no pin placeholder appears after them; `;` and
  `$` are rejected in lines. Order of the two nodes stays the author's choice (polarity). `*` is not rejected: it cannot
  start a line (the prefix rule) and `{...}` expressions are not template syntax here.
- **F3 (older saved version).** `Document._resolve`: `saved version != definition version` is an `incompatible`
  placeholder (detail "saved with X version a; this catalog has version b"), per D19. There is no upgrade path and no
  `compatible_from` key (not needed until a real bump exists). v0/v1 files count as version 1. Supersedes plan section 8
  rule 4 ("stored version becomes d.version"). `docs/persistence.md` updated.
- **F6 (unknown keys).** Kept at the top level (`Document.extra`), per component record and per layout entry (the
  part's `extra = {"record", "layout"}`, present only when non-empty), deep-copied, appended after the known keys by
  `to_dict`. Unknown keys inside `simulation` and in v0 files are not kept (not asked). Undo/redo use `to_dict`, so
  they survive history too. Supersedes the T6 note "unknown extra keys in a record are dropped".
- **F7 (shadowing).** `catalog.reload` records `CATALOG.shadowed = {id: [kept path, rejected path, ...]}`.
  `Document.validate` emits a `SHADOWED-DEFINITION` warning per part of such a type (part, id, all paths), shown in
  Problems; Run is not blocked. Chosen over a load-time-only message because `validate` is re-run live (the warning
  disappears when the catalog is fixed) and Problems is the existing place for per-part diagnostics. Namespacing ids was
  not attempted.
- **F14 (placeholder pins).** The pin list is not written to the file (a placeholder record is saved back unchanged).
  Instead the history snapshot carries it (`history._state` adds `_pins`, `_restore` passes it to
  `Document.from_dict(d, pins)`, which unions it with the pins the connections use). First load still derives pins
  from the connections, as before.
- **D3/D5 + T9 (doc links).** `catalog.SCHEME_RE` and `WEB_URL`: a `docs` entry with any scheme other than `http://` or
  `https://` is `DEF-FIELD` at load and `widgets.doc_target` returns None for it. A relative file named like `a:b.pdf`
  is therefore refused too (accepted cost).
- **T2/R1, V3.** Tests added; the code was already right (`placeable` readiness, `interface` on a pin-less part is
  DEF-CAPABILITY). `conftest._toml` now writes `pins = []`.
- **T4/C4/F11 (categories).** Policy: free text is allowed (adding a category needs no UI change). A case-insensitive,
  whitespace-trimmed match of a TAXONOMY entry is normalised to it. An empty category is `DEF-FIELD`. Other categories
  sort alphabetically (case-insensitive, was case-sensitive) after the TAXONOMY ones. The plural-form warning from F11 was
  not added. `docs/adding-components.md` corrected.
- **T5 (pinouts).** `test_seed_packages.py::test_pinout_matches_cited_source` holds tables transcribed from the cited
  sources, not from the tomls: Espressif DevKitC V4 guide (J2/J3, 38 rows), Arduino A000066 full pinout PDF (32 pins),
  DHT22 datasheet (VDD, DATA, NC, GND), Shillehtek SSD1306 manual, piezo (two terminals). ESP32, Uno and DHT22 matched.
  **Seed correction: SSD1306_I2C** listed GND, VCC, SCL, SDA citing an Open-Electronics page that now returns HTTP 404;
  the live cited manual says VCC, GND, SCL, SDA. The toml now follows the manual and the dead link was dropped (the
  "pin order varies by vendor" limitation stays).
- **T1/S1/S4 (search).** `test_search_prefix_alias_tags_and_subcategory`; no code change needed.
- **T3/C1.** Nav counts under the toggle and query are pinned with hard-coded numbers (`test_nav_counts_follow_toggle_and_query`).
- **UI.** The "opened with placeholders" banner is a warning `banner` laid out above the canvas (not floating), text
  `N part(s) has/have no usable definition: ...`, links "Show in Problems" (selects the first placeholder) and
  "Dismiss"; the status bar keeps the message; any new document clears it. The failed/blocked run is no longer shown in
  the floating `notice` over the canvas (it covered the "Interface only" badge in t9-blocked.png); it lives in the
  results area only, as in results-running-blocked.png, and in the status bar. The floating notice remains for an
  invalid run field and for out-of-date results; both are short-lived and also shown elsewhere, but could still sit
  over a badge near the canvas top-left (left for 13b if wanted).
- **F12 (second domain).** No second domain was built. Decision: "simulated" currently means `sim:electrical`.
  `docs/adding-components.md` ("Capabilities") states that registering a validator in `catalog.SIM_DOMAINS` is the single
  registration point but only makes `sim:<domain>` a legal capability; a real domain must also extend
  `Definition.simulated`/`readiness` and `Document.unmodeled` (and provide a runner). T15 is pinned by
  `test_simulated_means_electrical_until_a_domain_extends_the_run`.
- **Docs (architecture review 1.2).** Added to `docs/adding-components.md`: the `[sim.electrical]` rules table, the
  global scope of model names, `pins = []` for pin-less parts, the unknown-key wording for `[sim.electrical]` (DEF-SIM),
  and "Simulated" instead of "Simulates".

## 16. T13b notes (low-severity fixes and test tightening)

Every code fix has a test that failed on the previous code (run first, then fixed). Test-only items were checked against
the mutant they target where noted.

- **F4 (root dedupe).** `catalog.reload` reads each resolved package file once, so a root listed twice or nested in
  another is not a duplicate. Test: `test_same_root_listed_twice_or_nested_is_not_a_duplicate` (also runs the CLI).
- **F5 (generic body labels).** `canvas._labels(named=...)`: a generic body still draws its value and current rows;
  only the name (which the body prints) is dropped. Test: `test_generic_body_still_draws_value_and_current_labels`.
- **F8 (device-name collisions).** `Document.validate` emits `DUP-DEVICE` (error) naming both parts when two modeled
  parts map to one SPICE device name (`electrical.device_name`, lower-cased). Test:
  `test_device_name_collision_reported_before_run`.
- **F9 (geometry bounds).** `catalog.MAX_GEOM = 1e4`: `box` and pin `at` beyond ±10000 are `DEF-FIELD` / `DEF-PIN`.
  Tests: two new `INVALID` cases (`_huge_box`, `_huge_pin`). `docs/adding-components.md` states the bound.
- **F10 (plain text).** `widgets.label`, `library.Elided` and `NavItem` set `Qt.PlainText`. The one intentional rich
  label (the Problems file head) is `RichText` with the path escaped. Test: `test_definition_text_is_shown_as_plain_text`.
  Status bar and notice labels still use auto text (they are not definition text; left as is).
- **F13 (symlinked packages).** Chosen: warn, do not follow. `reload` emits `DEF-LINK` (warning) for a symlinked
  directory in a root, so loops cannot hang. Documented in `docs/adding-components.md` (codes table and "Search and
  roots"). Test: `test_symlinked_package_directory_is_warned_not_silently_skipped` (includes a symlink cycle).
- **T7.** `test_placeholder_non_string_params_resave_identical`.
- **T10.** `test_wire_hit_tolerance_is_screen_pixels_at_25_and_400_percent`,
  `test_fit_never_zooms_past_100_percent_for_one_small_part`. Both fail on the mutants Z3 (no `/zoom`) and Z5 (no
  100 % cap) when applied temporarily.
- **T11.** `test_each_core_error_type_is_failed_with_its_name_and_keeps_excluded`: seven cases, one per `SIM_ERRORS`
  type, via an injected `Simulation` whose `run()` raises the typed error.
- **T12.** M16 now ±2 µs (interpolated crossings), M17 rel 2e-3. Measured on the engine: M16 error under 0.1 µs,
  M17 relative error about 3e-6. Both margins have large headroom, so the requested values were kept.
- **T13.** Chip text is asserted (the whole tag, or elided with the whole tag as the tooltip). The PULSE mode is set
  through `edits["mode"].setCurrentText("PULSE")`. The invalid-definition check now asserts the owning id.
- **T14.** Dock height is `theme.DOCK_H`, and the canvas layout is asserted relationally (taller than the dock, wider
  than the Problems column, above the dock).
- **T16. Deviation from the brief.** The brief asks for "plot bottom <= Play row top". The design image
  `docs/milestone/design/results-states.png` (and the list in `03-design.md`) shows the Play row above the plot, and
  the app matches it. The test asserts the design order (Play row bottom <= plot top). The layout was not changed.
  `docs/frontend.md` ("under the plot") is stale and still says the opposite.
- **T17.** `golden_netlists` `__main__` needs `--bless`. Its `snapshot()` also now uses `electrical.build_netlist`
  (as the golden test does). The old `Document.netlist()` added an `excluded` key, so a regenerated oracle would have
  failed the golden test. The regenerated bytes now equal the committed file. Test:
  `test_golden_oracle_regeneration_needs_explicit_bless`.
- **F-1.** `conftest.shown_window` (hide, close, deleteLater). The five window fixtures that never closed their window
  (`test_library_ui`, `test_canvas`, `test_led_e2e`, `test_led_glow`, `test_led_pixels`) use it.
- **F-2.** `test_autoplay_after_run_only_when_enabled` drives `tick(LOOP_SECONDS / 100)` 100 times. It no longer waits on
  the real timer.
- **F-3.** PySide6 has no `QTest.qWaitFor`, so `conftest.wait_for(cond, timeout)` (pumps events) replaces the fixed
  `qWait` barriers. The remaining `qWait` is inside `wait_for` itself.
- Count: 416 before, 434 after (18 new cases). The full suite passed on two consecutive runs.

