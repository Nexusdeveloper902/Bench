# Adding components

A component is a directory holding a `component.toml` file and optional SVG or documentation files. The catalog
(`app/catalog.py`) finds every `component.toml` under the built-in root `app/library/` and under each directory in the
`SIM_COMPONENT_PATH` environment variable (`os.pathsep` separated). A new package appears in the library with no code
change. The format rules and their rationale are in `docs/milestone/02-plan.md` section 2 and section 5.

```
app/library/electrical/d_1n4148/component.toml   definition (required)
app/library/electrical/d_1n4148/symbol.svg       symbol (optional; generic labelled body if absent)
app/library/electrical/d_1n4148/icon.svg         library icon (optional)
```

Validate after every edit:

```bash
python3 -m app.catalog                  # built-in root: prints each problem, exits 1 on any error
python3 -m app.catalog ~/my-parts       # plus an extra root
```

Warnings (`DEF-ASSET`) keep the definition with a fallback. Errors reject it: the part is absent from the catalog and
the message says why.

## Example 1: electrical part with a fixed model (1N4148)

This part is simulated. Its behaviour is declared as data only (`[sim.electrical]`), and no Python code is involved.

```toml
format = 1                          # always 1
id = "D_1N4148"                     # unique across all roots; stored in circuit files, so do not rename it
version = 1                         # see "Versions" below
name = "1N4148 diode"
description = "General-purpose silicon signal diode with a fixed SPICE model."
designator = "D"                    # auto-name prefix: D1, D2 ...
label = "1N4148"                    # optional; short part type printed on the canvas when the part has no value label
category = "Electrical"             # free text; a TAXONOMY name is listed first (see "Search and roots")
subcategory = "Semiconductors"
aliases = ["1N4148", "signal diode"]        # search only, never used to resolve ids
keywords = ["diode", "switching", "signal"]
tags = ["semiconductor"]
domain = "electrical"
capabilities = ["interface", "sim:electrical"]
limitations = ["Fixed model (Is=2.52n, N=1.752, Rs=0.568): commonly published values, not a fit to one datasheet.",
               "Reverse breakdown and temperature dependence are not modelled."]
docs = ["https://ngspice.sourceforge.io/docs.html"]

[[pins]]                            # pins are a list of tables; ids unique, positions distinct
id = "a"
name = "Anode"
at = [0, 15]

[[pins]]
id = "k"
name = "Cathode"
at = [60, 15]

[presentation]
box = [0, 0, 60, 30]                # [x, y, w, h] in part-local units; required
show_current = true                 # needs sim.electrical.current below

[sim.electrical]
device = "D"                        # SPICE letter from the device table (see "Device letters")
line = "{dev} {a} {k} d1n4148"      # {dev} is the instance name; {a} and {k} are pin ids
models = [".model d1n4148 D(Is=2.52n N=1.752 Rs=0.568)"]   # model names are global to the netlist (see rules below)
current = "@{dev_lc}[id]"           # the vector shown when show_current is on
```

`label` (optional, 12 characters or fewer) is the short part type drawn under the part name for parts that have no value label (ICs, transistors, sensors). If you omit it, the first word of `name` that contains a digit is used (`2N3904 NPN transistor` gives `2N3904`); with no such word nothing is drawn. Set it when that default is poor, e.g. `label = "OP-AMP"`. Parts with a generic box body already print their name inside it, so they never show the label.

## Example 2: interface-only part (DHT22)

This part has real pins, so it can be placed and wired, but it has no `sim:*` capability and no `[sim.*]` section.
Run is blocked while it is wired into a circuit, and it is excluded from a run when it is unwired.

```toml
format = 1
id = "DHT22"
version = 1
name = "DHT22 (AM2302)"
description = "Digital temperature and relative humidity sensor on a single-wire bus. Four pins, pin 3 is not used."
designator = "U"                    # U is the default designator for parts that are not electrical
category = "Sensors"
subcategory = "Temperature and humidity"
aliases = ["AM2302"]
keywords = ["humidity", "temperature", "single-wire"]
tags = ["3.3-6 V"]
domain = "digital"
capabilities = ["interface"]        # required whenever pins are present
limitations = ["Not simulated: the single-wire protocol and the sensor's readings are not modelled."]   # required without sim:*
docs = ["https://cdn.sparkfun.com/assets/f/7/d/9/c/DHT22.pdf"]

pins = [                            # top-level keys must come before the first [table] header
  {id = "VDD", at = [0, 20], description = "Power supply, 3.3-6 V DC"},
  {id = "DATA", at = [0, 40], description = "Single-bus data"},
  {id = "NC", at = [0, 60], description = "Not used (NULL in the datasheet)"},
  {id = "GND", at = [0, 80], description = "Ground"},
]

[presentation]
box = [0, 0, 80, 120]               # no symbol: a generic labelled box is drawn
```

## Top-level keys and the TOML trap

Every top-level key must come before the first `[table]` header. A key written after `[presentation]` belongs to that
table, so `pins = [...]` placed there is rejected as `unknown key 'pins' in [presentation]`. Put `pins`, `params` and
`sim` before `[presentation]`, or use `[[pins]]` and `[[params]]` headers, which are always top-level.

Unknown keys are an error (`DEF-FIELD`), which catches typos. Inside `[sim.electrical]` they are `DEF-SIM`.

A part without pins (a documentation-only "Reference only" part) must still write `pins = []`: the key is required and
`[[pins]]` tables cannot express an empty list. It then has no `interface` capability and needs a `limitations` entry.

## Pins

- `id`: letters, digits and `_` (`^[A-Za-z0-9_]+$`), unique within the part. Circuit files store pin ids, so do not rename one casually.
- `name`: the label shown on the part. Defaults to the id and may repeat (the three ground pins of an ESP32 are all named `GND`). Names, descriptions and limitations are shown as plain text: markup such as `<b>` or `<img>` is not rendered.
- `at = [x, y]`: two numbers in part-local units, each within ±10000 (`DEF-PIN` beyond that). Positions must be distinct.
- Convention, not enforced: pins on multiples of 20, and `box` width and height multiples of 40, keep pins on the 20 px grid in every rotation.
- Electrical parts: pin ids must be plain identifiers that do not start with a digit and are not `dev` or `dev_lc`, because they are template placeholders.

### `nc` pins (drawn, no model port)

A real package often has pins the model has no port for: the LM741 offset-null and NC pins, the LM311 BALANCE and STROBE
pins. List their ids in the top-level key `nc` (before the first `[table]` header, like `pins`):

```
nc = ["null1", "null2", "nc8"]      # ids of pins listed in [[pins]]; the part keeps its real pin count and pin numbers
```

An `nc` pin is an ordinary pin for the user: it is drawn, can be wired, shows in the pin lists, and keeps its position, so
saved documents stay valid. For the simulation it does not exist:

- it is not a node field of `line` (the node-field rule in `check()` counts only the other pins, so the line has one
  placeholder per model port) and a template that mentions it is a `DEF-SIM` unknown placeholder;
- it is not netlisted: wiring it to a net changes nothing in the deck, and a net that only it touches never reaches ngspice;
- it gets no `UNCONNECTED-PIN` warning when left open (a part whose other pins are all open is still `UNCONNECTED-PART`).

An id that is not a pin, or a repeated id, is `DEF-PIN`. State in `limitations` that the pin is not modelled, so a user
who wires a trimmer to it knows it does nothing. `Definition.terminals` is every pin; `Definition.model_pins` is the
terminals without the `nc` ones.

## Parameters

Each `[[params]]` table maps to one editable field.

| key | meaning |
|---|---|
| `key` | stored name, unique, not a pin id, `dev` or `dev_lc` |
| `label` | field label |
| `unit` | one of `""`, `Ω`, `F`, `H`, `V`, `A`, `s`, `Hz` |
| `default` | text, exactly as a user would type it (`"1k"`, `"5"`), not a number |
| `kind` | `"number"` (default), `"choice"` with a `choices` list, or `"points"`: time-value pairs typed as `0 0, 1m 5, 2m 0` (times in s, strictly increasing from 0; `unit`, `min` and `max` apply to the values), emitted as SPICE text `0.0 0.0 0.001 5.0 ...` for a `PWL({key})` line |
| `min`, `min_exclusive`, `max` | numeric bounds |
| `when` | `{param = "choice"}`: the field applies only when those values match |
| `help` | one-line tooltip |
| `scale` | live-run slider scale: `"log"` or `"linear"`; default log for `Ω`, linear otherwise. `"log"` needs `min >= 0` |

**Live-run sliders.** During a live run the inspector shows a slider for each live param of the selected part
(a dropdown for a choice). Its range is `min`..`max`. A log slider whose `min` is missing or 0 (exclusive) starts six
decades below `max` (LDR lux: 0.1 to 100000). A missing end is taken around the starting value: x/÷100 on a log
slider (a resistor at 1k: 10 to 100k), ±2·|value| (or ±1) on a linear one. So give a stimulus param (`VSTIM ... DC
{P}`, LIVE.md) both `min` and `max`, at the physical range the model is valid for, and `scale = "log"` when it spans
decades (illuminance, force). `max` is also a validation bound, so a typed value above it is refused (G31).

## `[sim.electrical]` rules

An electrical part is data only; `app/electrical.py` checks it (`DEF-SIM`) and renders it to one SPICE line.

| key | rule |
|---|---|
| `device` | one SPICE letter from the device table below. The device name is the part name, prefixed with the letter if it does not start with it (`SW1` becomes `RSW1`). |
| `line` | a string, or `{select = "<choice param>", <every choice> = "..."}`. It must start with `{dev} ` and be one line. |
| node fields | the part has the pin count its letter needs (see the table below; `nc` pins do not count), and the fields after `{dev}` are exactly those pin placeholders, once each. A literal node, a pin elsewhere in the line, or `;` / `$` comments are errors: they would attach the part to nets that are not drawn. |
| placeholders | plain identifiers only: `{dev}`, `{dev_lc}`, pin ids, and params that apply to that branch (a plain line: params without `when`). Numbers arrive as `repr(float)`, choices as text. |
| `models` | `.model <name> <TYPE>(...)` lines. Placeholders: `{dev}`, `{dev_lc}`, params without `when`. For a letter with model types in the table, the type and `level` of the card the line names must be in the table (write `level` as a number). |
| `current` | exactly the vector the table lists for the letter (a wrong `.save @dev[x]` can crash ngspice); the vector shown by `show_current` and `indicator`. |
| `ground = true` | instead of `device`/`line`: a reference node; exactly one pin, no other key. |
| `defs`, `body` | definition text (`.subckt` / `.model`): see "Subcircuit parts". Required for `X`; a model-backed letter may take its card from them instead of `models`. |
| `model_kind` | what backs the part: `primitive`, `model`, `third-party-model`, `project-subckt`, `third-party-subckt`, `behavioral`, `equivalent`. Required for `X` (one of the last four). |
| `verification` | how its behaviour was checked: `analytic`, `reference`, `smoke` or `none` (default). |
| `provenance` | table of strings: `source`, `url`, `version`, `license`, `attribution`, `retrieved`, `changes`, `notes`. Required, with `license`, when `model_kind` is `third-party-*` and any text comes from the package itself (`body`, a package-local `defs` file or `models`). |

**Model names are global.** ngspice has one model namespace per deck, and a name matches case-insensitively. Two
definitions that use the same fixed name (for example `dled`) must carry the same body: otherwise the later definition
is rejected at catalog load (`DEF-SIM`), and a name whose body differs between instances stops the run
("model 'x' gets different parameters in different ... parts"). A model that depends on parameters, or that you
do not want to share, needs `{dev_lc}` in its name (`dmod_{dev_lc}`).

## Device letters

The letters a part may use, their pin counts and the one `current` each allows (`DEF-SIM`, `DEVICES` in
`app/electrical.py`; the core validator has the same rows for every letter, `DEVICES` in `benchcore/src/validate.rs`).
Model types and levels are the ones ngspice 47 on this build actually has (probed one level at a time).

| letter | kind | pins | node fields, SPICE order | after the nodes | model types (levels) | current |
|---|---|---|---|---|---|---|
| R | primitive | 2 | `{a} {b}` | value | - | `@{dev_lc}[i]` |
| C, L | primitive | 2 | `{a} {b}` | value | - | `@{dev_lc}[i]` |
| V | primitive | 2 | `{p} {n}` | source spec (`DC`, `PULSE`, `SIN`, `PWL`, ...) | - | `i({dev_lc})` |
| I | primitive | 2 | `{p} {n}` | source spec | - | `@{dev_lc}[c]` |
| B | primitive | 2 | `{p} {n}` | `V=` or `I=` expression (no pins in it) | - | `@{dev_lc}[i]` |
| E, G | primitive | 4 | `{o+} {o-} {c+} {c-}` | gain (E: V/V, G: A/V) | - | `@{dev_lc}[i]` |
| T | primitive | 4 | `{p1+} {p1-} {p2+} {p2-}` | `Z0=` `TD=` | - | none |
| D | model-backed | 2 | `{a} {k}` | own model | `D` (1, 3) | `@{dev_lc}[id]` |
| S | model-backed | 4 | `{n+} {n-} {nc+} {nc-}` | own model | `SW` | `@{dev_lc}[i]` |
| Q | model-backed | 3 or 4 | `{c} {b} {e}` (+ `{s}` substrate) | own model | `NPN`, `PNP` (1, 2, 4, 8, 9) | `@{dev_lc}[ic]` |
| J | model-backed | 3 | `{d} {g} {s}` | own model | `NJF`, `PJF` (1, 2) | `@{dev_lc}[id]` |
| M | model-backed | 4 | `{d} {g} {s} {b}` | own model | `NMOS`, `PMOS` (1-6, 8, 9, 10, 14, 49, 54-58, 68, 73) | `@{dev_lc}[id]` |
| M (VDMOS) | model-backed | 3 | `{d} {g} {s}` | own model | `VDMOS` | `@{dev_lc}[id]` |
| Z | model-backed | 3 | `{d} {g} {s}` | own model | `NMF`, `PMF` (1-6) | `@{dev_lc}[cd]` |
| X | subcircuit-backed | the `.subckt`'s port count | the ports, in `.subckt` order | subckt name, then `NAME=value` params | - | none |

Not available here, and refused by the validator with `UNSUPPORTED`: D level 2; Q levels 3, 5, 6 (Mextram), 7, 10+;
J level 3+; M levels 7, 60 (SOI3: aborts the process) and the others missing above; Z level 7+; PSpice `VSWITCH`,
`ISWITCH`, `LPNP`; E/G `LAPLACE`; `if()`, `limit()`, `table()` in expressions; N (OSDI). K, F, H, W, O, U, Y, P and
A reference other elements by name or need bracketed ports, so they belong in subcircuit bodies (see "Subcircuit
parts"), not top-level parts. Bench pins ngspice's compatibility mode off (`unset ngbehavior` before every circuit), so PSpice and
LTspice syntax is not translated, whatever a `.spiceinit` says.

The shipped 2N2222 (`app/library/electrical/q_2n2222/component.toml`), the NPN transistor, is the example (an excerpt, so the doc-example test, which loads every `toml` block as a full part, skips it):

```
[sim.electrical]
device = "Q"                        # 3 pins; a 4th pin would be the substrate
line = "{dev} {c} {b} {e} q2n2222"  # node fields are exactly the pins, once each, in SPICE order
models = [".model q2n2222 NPN(Is=14.34f Xti=3 ...)"]   # abbreviated; the shipped card is complete
current = "@{dev_lc}[ic]"           # the collector current, attributed to the first node field's pin (c)
```

Rules for parts with an own model (D, S, Q, J, M, Z):

- The field right after the node fields must be a model named in this part's `models`. ngspice reads extra tokens there as optional nodes (Q substrate, M SOI nodes), so a literal such as `0` or `n1` would connect to a net that is not drawn.
- The node fields fix the SPICE order. The validator checks that they are the pins, once each; it does not check the order, so get the order right in the template.
- The current is attributed to the pin in the first node field (collector for Q, drain for J, M and Z). Every letter accepts only the exact current form in the table.
- A 3-pin M needs a `VDMOS` card, and a `VDMOS` card needs 3 pins (a 4th node silently changes the result in ngspice).
- Only two-terminal parts get `V(Part)`. A transistor or a 4-pin source shows its node voltages and its current, and no across voltage.
- The MOSFET gate (the second node field of M) has no DC path. A gate that is unwired, or driven only through a capacitor or a current source, is `NO-DC-PATH` and blocks the run. Drive it through a resistor or a voltage source. A BJT base or JFET gate is not checked this way: ngspice solves it through its junctions, and an unwired pin gets a `DANGLING` warning.

## Subcircuit parts

A part with `device = "X"` instantiates a `.subckt`. Its definition is ngspice text that the package carries, in
`body` (inline) or in files listed in `defs`. The shipped references are `OPAMP` (shared file),
`OPAMP_DUAL` (inline body nesting the shared file) and `NTC_THERMISTOR` (package-local file).

### Example 3: subcircuit part with an inline body

```toml
format = 1
id = "TAP_DIVIDER"
version = 1
name = "Tapped divider"
description = "Two-resistor divider with a ratio parameter, as a subcircuit."
designator = "U"
category = "Electrical"
subcategory = "Passives"
domain = "electrical"
capabilities = ["interface", "sim:electrical"]

[[pins]]
id = "top"
at = [0, 0]

[[pins]]
id = "tap"
at = [40, 20]

[[params]]
key = "rtot"
label = "Total resistance"
unit = "Ω"
default = "10k"
min = 0
min_exclusive = true

[[params]]
key = "ratio"
label = "Tap ratio"
default = "0.5"
min = 0
min_exclusive = true
max = 0.999

[presentation]
box = [0, 0, 40, 40]

[sim.electrical]
device = "X"
line = "{dev} {top} {tap} tapdiv RTOT={rtot} K={ratio}"   # node field i -> port i: top -> hi, tap -> mid
body = """
* the lower resistor goes to ground (node 0 is global inside a body)
.subckt tapdiv hi mid params: RTOT=10k K=0.5
R1 hi mid {RTOT*(1-K)}
R2 mid 0 {RTOT*K}
.ends tapdiv
"""
model_kind = "project-subckt"
verification = "analytic"
```

### Example 4: definition files (excerpts of the shipped OPAMP and NTC_THERMISTOR)

```
[sim.electrical]                    # app/library/electrical/opamp/component.toml
device = "X"
line = "{dev} {inp} {inn} {out} bench_opamp A={aol} GBW={gbw}"   # ROUT is not passed: the .subckt default (1) applies
defs = ["models:generic/opamp.lib"] # app/library/_models/generic/opamp.lib + its MANIFEST.toml record
model_kind = "project-subckt"
verification = "analytic"

[sim.electrical]                    # app/library/sensors/ntc_thermistor/component.toml
device = "X"
line = "{dev} {a} {b} bench_ntc R25={r25} B={beta} T={temp}"
defs = ["ntc.lib"]                  # package-local: app/library/sensors/ntc_thermistor/ntc.lib
model_kind = "project-subckt"

[sim.electrical.provenance]
source = "Bench project; the beta equation of an NTC thermistor"
license = "MIT"
```

### Rules

- **Pins to ports.** The node fields of `line` are the `.subckt` ports in order: the first node field connects to the
  first port, and so on. Their count must equal the port count (`DEF-SIM`). Every pin appears once (the general node
  rule). The catalog does not compare pin ids with port names, so get the order right; a pin that the model does not
  use still needs a port (tie it inside the body, for example with a 1 GΩ resistor to `0`).
- **Subckt name.** The field after the node fields must name a top-level `.subckt` of this part's `defs` or `body`,
  literally (no placeholder).
- **Params.** After the name come `NAME=value` tokens without spaces and without `params:`; values are usually
  `{param}` placeholders (numbers arrive as `repr(float)`). Every `NAME` must be declared on that `.subckt` line
  (`params: NAME=default` or `NAME=default`), case-insensitively. ngspice silently ignores an unknown instance
  parameter, so the catalog rejects it. A declared parameter left out of `line` takes the `.subckt` default.
- **Files.** `defs` is a list. An entry is either package-relative (`"ntc.lib"`, must stay inside the package
  directory) or `"models:<path>"`, resolved under `<library root>/_models/` of the root the package was found in
  (`app/library/_models/` for built-in parts; `<root>/_models/` for a `SIM_COMPONENT_PATH` root). Paths never depend
  on the working directory. Files are UTF-8, read once per catalog load and identified by their sha256.
- **Shared files need provenance.** Each file under `_models/` has one `[[file]]` record in `_models/MANIFEST.toml`:
  `path` (relative to `_models/`), `origin` (`project` or `third-party`), `source`, `license` (SPDX id, or
  `LicenseRef-<name>` with the text in `_models/LICENSES/<name>.txt`), `sha256` of the file as shipped, and for
  third-party files `url`; optional `version`, `attribution`, `retrieved`, `changes`. A missing or incomplete record,
  or a stale `sha256`, rejects every package that uses the file. Editing a shared file means updating its record.
- **Allowed text.** Comment lines (`*`), inline comments (`;`, ` $ `, ` // `), `+` continuations, `.subckt` ...
  `.ends` blocks (nested blocks are local to their parent) and `.model` cards. `.param` and `.func` only inside a
  body. Any other directive (`.include`, `.lib`, `.control`, `.option`, analyses, `.end`, `.global`, `.save`) and
  element lines outside a body are `DEF-SIM`: packages are untrusted input, and the deck must stay self-contained.
- **Dependency order.** Text is never formatted (its `{...}` are ngspice expressions). An X line inside a body must
  name a `.subckt` defined in the same text or in a `defs` entry listed before it, so list dependencies first; the
  inline `body` comes after every `defs` file. A unit that uses a name it does not define is `DEF-SIM`, and so is an
  X line in a body whose node count differs from the ports of a `.subckt` the catalog knows.
- **One definition per deck.** Each unit (file or body) is emitted once per netlist, after the devices, framed by
  `* bench-def <root-relative path> <sha256 prefix>`, however many parts use it, in first-use order (so a unit's
  dependencies always come before it). Ten op-amps share one `.subckt bench_opamp`; parameters differ per instance.
- **Global names.** `.subckt` names and top-level `.model` names are global to the netlist and case-insensitive, and
  ngspice keeps the first definition of a name (a different second one is ignored, for `.model` silently). Within a
  part, one name defined twice is `DEF-SIM`; across the catalog a name may be defined only by one unit (the same file
  or identical text), otherwise the later package is rejected. Prefix project names (`bench_`).
- **Body validation.** At load the catalog runs the core's static validator (`benchcore.validate`, the one every run
  deck goes through) on a probe deck: the part's instance with default params, each pin on its own net with a 1 GΩ
  resistor to `0`, its `models`, then its definition text. Every body the instance reaches, nested ones included, is
  checked: element syntax and pin counts, `.model` and `.subckt` references (a definition nested in a body is local
  to it), port counts, instance params, every `{param}` and function name, duplicates, recursion, and connectivity
  with internal nodes named `xu1.n2` (each internal node needs a DC path to a port or to `0`; a body needs no ground
  of its own). Errors reject the package (`DEF-SIM`, message `CODE: ...`); other warnings (for example `DANGLING` on an
  unused port) are `DEF-SIM` warnings. The part gets a status:
  - `full`: everything was analysed. Nothing is added to its limitations.
  - `limited`: no error, but some construct is left to ngspice; its limitations get one line naming each one, as
    "Static validation is limited; Bench's validator left these to ngspice: .subckt s: B1: behavioural B not analysed
    ...". Constructs: B sources and E/G `VALUE=`/`vol=`/`cur=`/`TABLE` expressions, XSPICE `A` devices, T/O/U/Y/P
    lines, a `.model` whose `level` is not a literal number, a directive other than `.model`/`.param`/`.func` in a
    body.
  - `failed`: errors; the package is rejected.

  To reach `full`, build from R, C, L, linear E/F/G/H, `POLY` sources, K, S/W switches, and D/Q/J/M/Z with literal
  levels, and declare every param the body reads. To keep a `limited` part honest, say in `limitations` what the
  unanalysed construct models and how it was verified (`verification`), since only ngspice checks it. Neither status
  proves the model is electrically right: that is what `verification` and the tests are for.
- **No current.** An X part has no `current`, and only a 2-pin X part gets `V(Part)`. Internal nodes are named by
  ngspice as `<instance>.<node>` (`xu1.n2`, nested `xu1.xa.n2`).
- **Dialect.** ngspice syntax only: Bench pins the compatibility mode off, so PSpice `IF()`/`LIMIT()`/`VSWITCH` and
  LTspice-only syntax fail. Vendor PSpice macromodels need a translation first.

## Presentation

`[presentation]` is required. `box = [x0, y0, w, h]` sets the body (rotation is about its centre), each number within ±10000 (`DEF-FIELD` beyond that). The optional keys are:
`symbol` (an SVG file, or `{select = "param", <choice> = "file.svg"}`), `icon` (SVG for the library list), `value`
(the parameter shown on the canvas), `indicator = "glow"` and `show_current` (both need `sim.electrical.current`),
and `animate_when = [["param", "choice"], ...]`.

## SVG rules

- Coordinates are part-local, the same as pin `at` and `box`. The `viewBox` must have four numbers.
- Use only `path`, `line`, `circle`, `rect` and `polyline`, with `fill="none" stroke="currentColor" stroke-width="2"`.
  `currentColor` is replaced with the part's state colour when the file is drawn.
- A missing or unusable file is a `DEF-ASSET` warning. The part then draws as a generic labelled box, so multi-pin
  boards may omit `symbol`.

## Capabilities

| capability | meaning | rule (violation is `DEF-CAPABILITY`) |
|---|---|---|
| (none) | placeable: can be placed, moved, saved and documented | needs at least one `limitations` entry |
| `interface` | `pins` is the real, complete interface; the part can be wired | required if and only if `pins` is not empty |
| `sim:electrical` | a working electrical model through the engine | needs a `[sim.electrical]` section that passes validation |
| `sim:<other>` | reserved | error unless `<other>` is a registered simulation domain; describe it under `limitations` |

`sim:electrical` is the only simulation domain today. "Simulated" (readiness, the Simulated-only filter, the run)
means exactly `sim:electrical`: `Definition.simulated`, `Definition.readiness` and `Document.unmodeled` test for it.
A second domain is registered in one place, `catalog.SIM_DOMAINS` (its validator), but that only makes `sim:<domain>`
a legal capability: the part stays "Interface only" and is excluded from a run until the domain's runner exists and
`simulated`, `readiness` and `unmodeled` are extended with it (`app/tests/test_seed_packages.py` pins this). Nothing
else in the UI names a domain.

Readiness shown in the UI follows from the capabilities: `simulated` (Simulated), `interface` (Interface only) or
`placeable` (Reference only). An interface-only part states in `limitations` that it is not simulated, and it has no
`[sim.*]` section and no fake behaviour. A wired part with no simulation blocks Run; an unwired one is excluded from
the run and named in the result.

## Versions

`version` is an integer, starting at 1 (D19). Bump it when you remove or rename a pin or a parameter, or change what a
parameter means. Adding a parameter with a default does not need a bump. A circuit file stores the version each part was
saved with. A file whose version differs from the catalog's (higher or lower) becomes a placeholder part, which is kept
unchanged: there is no automatic upgrade, so bump only for a breaking change.

## Diagnostics

`python3 -m app.catalog` prints one line per problem: severity, code, file, field and message. Errors reject the
definition. Warnings keep it with a fallback.

| code | severity | cause |
|---|---|---|
| `DEF-PARSE` | error | unreadable file or invalid TOML (the message gives the line) |
| `DEF-FIELD` | error | missing or wrong-typed key, unknown key, bad `id`, `format` not 1, unknown `domain`, empty `category`, a `docs` entry with a URL scheme other than http(s) (`file:`, `javascript:`, `ftp:` ...), path leaving the package |
| `DEF-DUP-ID` | error | id already defined by another root; the first one wins |
| `DEF-PIN` | error | bad or duplicate pin id, bad `at`, two pins at one position |
| `DEF-PARAM` | error | bad unit, choice or bounds; default that does not parse; clashing key |
| `DEF-CAPABILITY` | error | capabilities inconsistent with pins, `[sim.*]` sections or `limitations` |
| `DEF-SIM` | error | an electrical section that the template check rejects; definition text that is unreadable, outside its directory, without a provenance record, malformed at header level or with an unresolved dependency; a body error found by the static validator (`CODE: message`, see "Body validation"); a fixed model name or a `.subckt` name already defined differently by an earlier definition |
| `DEF-SIM` | warning | a static-validator warning on the part's definition text (for example `DANGLING` on a port the body never uses) |
| `DEF-ASSET` | warning | symbol, icon or doc file missing or not a usable SVG |
| `DEF-LINK` | warning | a symbolic link to a directory inside a root: it is not followed, so the package behind it is not loaded |

## Search and roots

Search matches id, name, aliases, keywords, tags, description, category and subcategory. It never resolves ids, so an
alias cannot replace a missing id.

Categories are free text: a package may name a new category and it appears in the library with no code change. A
name that matches a TAXONOMY entry case-insensitively is normalised to it (`sensors` becomes `Sensors`); an empty one
is `DEF-FIELD`. The list shows the TAXONOMY ones first, in the order Electrical, Microcontrollers, Sensors, Displays,
Communication, Audio, Power (only those that have parts: Communication has none yet), then any other category
alphabetically.

Extra roots come from the `SIM_COMPONENT_PATH` environment variable, a list separated by `os.pathsep`. Use it to keep
personal parts outside the repository. Built-in parts load first, so a duplicate id in an extra root is rejected.
A root listed twice, or nested in another root, is read once. A package directory that is a symbolic link is not
followed (no loops are possible): it gets a `DEF-LINK` warning, so copy the directory into the root instead.
