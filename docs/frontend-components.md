# Frontend components, parameters and save format

Contract for `app/components.py` (catalog, SI units, netlist builder), the parts of `app/model.py` that use it
(validation, persistence) and what `app/sim.py` returns. All three are Qt-free; only `app/sim.py` imports `simcore`.

```
Component definition (components.CATALOG[kind])
  -> Document part {kind, x, y, rot, params}            (app/model.py)
  -> components.build_netlist -> SPICE lines/.model/.save/vector names
  -> app/sim.py -> simcore.Simulation -> libngspice
  -> RunResult.series (+ readable RunResult.signals)
```

The core's Simulation API is netlist-in, so SPICE text has to be produced somewhere on the client side. It is
produced only in `components.py`. Moving that module behind the Simulation API later (for CLI/MCP clients) is a
file move, not a redesign. The Rust `components.rs` `Component` enum is unused by the core and is left alone.

## Catalog contract

`CATALOG: dict[str, Component]`. Consumers must read metadata from it and must not branch on the kind. The only
per-kind code is each entry's `emit` function.

| Field | Meaning |
|---|---|
| `id` | stable kind id, written to files (`R`, `C`, `L`, `V`, `BAT`, `I`, `D`, `LED`, `SW`, `GND`) |
| `label` | display name (`Resistor`, ...) |
| `prefix` | auto-name prefix: `R` gives `R1`, `R2`, ... `LED` uses `D` and `BAT` uses `V` (they share numbering with D/V) |
| `terminals` | terminal names in SPICE node order (`a,b` / `p,n` / `a,k` / `g`) |
| `pos` | terminal -> `(x, y)` in local unrotated coords relative to the part anchor `(x, y)` |
| `box` | `(x0, y0, w, h)` body rectangle in the same coords; rotation is about its centre |
| `symbol` | renderer key (`resistor`, `capacitor`, `inductor`, `vsource`, `battery`, `isource`, `diode`, `led`, `switch`, `ground`) |
| `params` | tuple of `Param` (below), display order |
| `letter` | SPICE device letter; `None` = not a device (Ground) |
| `emit(dev, nodes, values) -> (lines, models)` | device line(s) and `.model` lines from validated values |
| `observables` | part-specific `Observable(name, vector, quantity)`, e.g. `I({name})` -> `@{dev}[i]` |
| `value_param` | param shown as the canvas value label (`summary()` formats it) |
| `ground` | all terminals are the reference node `0` |

Helpers: `get(kind)` (ModelError on unknown kind), `Component.defaults()`, `.fields(params)` (applicable keys),
`.values(params, owner)` (parse all applicable), `.param(key)`, `.device(name)` (`SW1` -> `RSW1`),
`summary(kind, params)`, `rotate(pt, center, rot)`, `terminal_offset(kind, terminal, rot)`, `ROTATIONS = (0, 90, 180, 270)`
(clockwise, screen coords with y down).

### Parameter metadata (`Param`)

| Field | Meaning |
|---|---|
| `key` | key in `part["params"]` and in files |
| `label`, `unit` | `"Resistance"`, `"Ω"`; unit is one of `Ω F H V A s Hz` or empty |
| `default` | text, exactly as a user would type it |
| `kind` | `"number"` (SI text) or `"choice"` (`choices` tuple) |
| `min`, `min_exclusive`, `max` | bounds; message `R1 R: must be > 0` |
| `when` | `((other_key, value), ...)`: param applies only then (source waveform fields) |

`Param.parse(text, owner)` returns a float (or the choice string) or raises `ModelError` naming part and key.
Values are stored as typed text (`"4.7k"`), never normalised, so files keep what the user wrote.

### Components

| Kind | Params (default) | SPICE | Observables |
|---|---|---|---|
| GND | - | none (net `0`) | - |
| R | R Ω (1000) | `R1 a b <R>` | `I(R1)` = `@r1[i]` |
| C | C F (1e-6), ic V (0) | `C1 a b <C> IC=<ic>` | `I(C1)` = `@c1[i]` |
| L | L H (1e-3), ic A (0) | `L1 a b <L> IC=<ic>` | `I(L1)` = `@l1[i]` |
| V | mode DC/PULSE/SIN; dc; v1 v2 td tr tf pw per; vo va freq | `V1 p n DC <dc>` / `PULSE(...)` / `SIN(...)` | `I(V1)` = `i(v1)` |
| BAT | dc V (5) | `V1 p n DC <dc>` | `I(V1)` = `i(v1)` |
| I | dc A (1e-3) | `I1 p n DC <dc>` | - (its current is its parameter) |
| D | Is A (1e-14), N (1), Rs Ω (0) | `D1 a k dmod_d1` + `.model dmod_d1 D(Is N Rs)` | `I(D1)` = `@d1[id]` |
| LED | - (fixed red model) | `D1 a k dled` + `.model dled D(Is=1e-20 N=1.8 Rs=5)` | `I(D1)` = `@d1[id]` |
| SW | state open/closed (open), ron Ω (0.01), roff Ω (1e9) | `RSW1 a b <ron or roff>` | `I(SW1)` = `@rsw1[i]` |

Every two-terminal part also gets `V(Part)` = v(first terminal) - v(second terminal), computed in `sim._diff` by
subtracting two core node series (no solving). Node voltages are `V(n1)`, ... Current sign: SPICE convention,
positive flowing into the first terminal (`a`/`p`) and through the part (so a source delivering power has negative `I(V1)`).

The Switch is a real resistor on purpose: the core validator does not count `S`/`W` devices as DC paths and would
report a false NO-DC-PATH. Time-controlled switching is out of scope.

Adding a component = one `Component(...)` entry; model, sim, validation, persistence and the netlist pick it up.

## SI units

`parse_si(text, unit="", what="value") -> float`, `format_si(x, unit="", digits=4) -> str`.

- Prefixes (case-sensitive): `T G M k K m u µ μ n p f`, plus `meg`/`Meg`/`MEG`. **`M` is mega** (SI), unlike SPICE where
  it is milli. The core never sees suffixes: values go out as `repr(float)`, so there is no ambiguity downstream.
- Optional unit after the prefix must match the parameter: `10 µF`, `1kΩ`, `330 ohm`, `5 V`, `1kHz`. A bare unit wins
  over a prefix (`1F` = 1 farad, `1f` = 1 femto). A wrong unit fails: `unit 'V' does not fit; expected Ω`.
- Scaling is exact decimal (`10u` -> `1e-05`). NaN/inf -> `must be finite`; garbage -> `'abc' is not a number (e.g. 1k, ...)`.
- `format_si(1000, "Ω") == "1 kΩ"`; everything it prints parses back.

## Netlist builder

`build_netlist(parts, nets) -> dict` (also `Document.netlist()`), raises `ModelError` on the first invalid value:

```
lines        ["V1 n1 0 DC 5.0", "R1 n1 n2 1000.0", ...]   device lines in part order
models       [".model ...", ...]                           deduplicated
saves        [".save all @r1[i]", ...]                     for every '@dev[x]' vector
observables  ["v(n1)", ..., "i(v1)", ..., "@r1[i]", ...]   vectors read from the core
derived      {"v(R1)": ("n1", "n2"), ...}                  across-part voltages, by subtraction
signals      [{"name": "V(C1)", "vector": "v(C1)", "quantity": "voltage", "unit": "V",
               "kind": "node"|"across"|"current", "part": "C1"|None, "pins": ["C1.a", ...]}, ...]
```

## Pre-run validation

`Document.validate() -> [diagnostic]`, diagnostic = `{code, severity, message, part, pins, param}`; errors first.

| code | severity | when |
|---|---|---|
| BAD-CONFIG | error | stop time / time step invalid, tstep > tstop, too many points (> 100 000, or > 2e6 samples) |
| EMPTY | error | no modeled non-ground part ("Nothing to simulate" when only unsimulated parts exist) |
| NO-GROUND | error | no device terminal on net `0` (no Ground, or Ground not wired) |
| UNCONNECTED-PART | error | a device with no wired terminal |
| UNCONNECTED-PIN | warning | a single unwired terminal (the core still solves it and reports DANGLING) |
| BAD-PARAM | error | an applicable parameter fails `Param.parse` |
| DUP-NAME-CASE | error | two part names equal ignoring case (`r2`, `R2`): SPICE names are case-insensitive |
| NOT-SIMULATED | error / warning | part without `sim:electrical`: error if any pin is wired (Run refused), warning if unwired (excluded) |
| MISSING-DEFINITION | error / warning | placeholder, type not in the catalog (same wired/unwired split) |
| INCOMPATIBLE-DEFINITION | error / warning | placeholder, newer version or unknown param/pin (same split) |

Electrical checks (floating islands, voltage-source loops, capacitor-only nodes) stay in the core (`[NO-DC-PATH]`, ...).
`run_document` stops on the first app error and does not call the core: status `blocked`. Only errors raised by the
core (`simcore.SimError` subclasses) become status `failed`; an exception from app code propagates.

`RunResult`: `ok`, `status` (`success`/`failed`/`blocked`), `state` (`completed`/`failed`/`blocked`), `message` (one
line, `[CODE] text` for app diagnostics), `excluded` (unsimulated parts left out of an otherwise successful run; the
success message ends `; not simulated: U1, U2`), `series` (keyed by vector name), `signals`, `diagnostics`, `findings` (core),
`details` (developer text: error type, full message, diagnostics, findings, netlist sent), `get(name)` and
`stats(name)` (accept readable or vector names), `signal(name)`.

`simcore.Simulation.state` is now `idle` -> `running` -> `completed` | `failed` (it used to stay `running` after
a raising `run`). Runs are synchronous, so `paused` is never observed from Python.

## Save format

The writer emits format v2; the reader accepts v0, v1 and v2. The full description, compatibility rules and
placeholder behaviour are in `docs/persistence.md`. In short: v2 is v1 plus a per-component definition `version`
(`{"type": "R", "version": 1, "params": {...}}`). A component whose definition is missing or incompatible loads as a
placeholder and is saved back unchanged; it is never substituted. `version` > 2 is refused ("newer than this app
supports"). Within a version, changes are additive only (new types, new params with defaults, new optional keys);
unknown extra top-level, component or layout keys are ignored.

- Save: `Document.save(path=None)` writes to `path` or `doc.path` and then sets `doc.path` (Save As = `save(new_path)`).
  The text is serialised first, written to `<path>.tmp` and atomically renamed, so a failed save leaves the old file
  intact. OS errors become `ModelError("cannot save <path>: <reason>")`.

## Tests

- `app/tests/test_components.py` (Qt-free): SI parse/format and errors, catalog contract per entry, rotation maths,
  exact netlist text for every kind, validation codes, structured failures, v1 schema, round trip + Save As,
  v0 load, rejected files, atomic save.
- `app/tests/test_core_analytic.py` (real engine): voltage divider (3 cases, 1e-6 V), RC charge vs
  `Vf(1-e^{-t/RC})` at 0.1..5 tau (1 mV) and capacitor current, Switch closed/open vs Ohm's law (rel 1e-6), toggle
  rerun determinism. Tolerance rationale is in the module docstring.
