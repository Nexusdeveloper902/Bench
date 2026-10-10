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
device = "D"                        # SPICE letter: one of R C L D V I Q J M (see "Multi-terminal parts")
line = "{dev} {a} {k} d1n4148"      # {dev} is the instance name; {a} and {k} are pin ids
models = [".model d1n4148 D(Is=2.52n N=1.752 Rs=0.568)"]   # model names are global to the netlist (see rules below)
current = "@{dev_lc}[id]"           # the vector shown when show_current is on
```

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

## Parameters

Each `[[params]]` table maps to one editable field.

| key | meaning |
|---|---|
| `key` | stored name, unique, not a pin id, `dev` or `dev_lc` |
| `label` | field label |
| `unit` | one of `""`, `Ω`, `F`, `H`, `V`, `A`, `s`, `Hz` |
| `default` | text, exactly as a user would type it (`"1k"`, `"5"`), not a number |
| `kind` | `"number"` (default) or `"choice"` with a `choices` list |
| `min`, `min_exclusive`, `max` | numeric bounds |
| `when` | `{param = "choice"}`: the field applies only when those values match |
| `help` | one-line tooltip |

## `[sim.electrical]` rules

An electrical part is data only; `app/electrical.py` checks it (`DEF-SIM`) and renders it to one SPICE line.

| key | rule |
|---|---|
| `device` | one SPICE letter of `R C L D V I Q J M`. The device name is the part name, prefixed with the letter if it does not start with it (`SW1` becomes `RSW1`). |
| `line` | a string, or `{select = "<choice param>", <every choice> = "..."}`. It must start with `{dev} ` and be one line. |
| node fields | the part has the pin count its letter needs (two, except Q, J and M: see below), and the fields after `{dev}` are exactly those pin placeholders, once each. A literal node, a pin elsewhere in the line, or `;` / `$` comments are errors: they would attach the part to nets that are not drawn. |
| placeholders | plain identifiers only: `{dev}`, `{dev_lc}`, pin ids, and params that apply to that branch (a plain line: params without `when`). Numbers arrive as `repr(float)`, choices as text. |
| `models` | `.model <name> <TYPE>(...)` lines. Placeholders: `{dev}`, `{dev_lc}`, params without `when`. |
| `current` | `@{dev_lc}[id]` style or `i({dev_lc})`: the vector shown by `show_current` and `indicator`. |
| `ground = true` | instead of `device`/`line`: a reference node; exactly one pin, no other key. |

**Model names are global.** ngspice has one model namespace per deck, and a name matches case-insensitively. Two
definitions that use the same fixed name (for example `dled`) must carry the same body: otherwise the later definition
is rejected at catalog load (`DEF-SIM`), and a name whose body differs between instances stops the run
("model 'x' gets different parameters in different ... parts"). A model that depends on parameters, or that you
do not want to share, needs `{dev_lc}` in its name (`dmod_{dev_lc}`).

## Multi-terminal parts (Q, J, M)

Transistors have three or four terminals. The letters and pin counts the validator accepts (`DEF-SIM`, `app/electrical.py`):

| letter | pins | node fields, SPICE order | current |
|---|---|---|---|
| R C L D V I | 2 | `{a} {b}` | `@{dev_lc}[id]` style or `i({dev_lc})` |
| Q | 3 or 4 | `{c} {b} {e}` (+ `{s}` substrate) | exactly `@{dev_lc}[ic]` |
| J | 3 | `{d} {g} {s}` | exactly `@{dev_lc}[id]` |
| M | 4 | `{d} {g} {s} {b}` | exactly `@{dev_lc}[id]` |

The shipped 2N2222 (`app/library/electrical/q_2n2222/component.toml`), the NPN transistor, is the example:

```toml
[sim.electrical]
device = "Q"                        # 3 pins; a 4th pin would be the substrate
line = "{dev} {c} {b} {e} q2n2222"  # node fields are exactly the pins, once each, in SPICE order
models = [".model q2n2222 NPN(Is=14.34f Xti=3 ...)"]   # abbreviated; the shipped card is complete
current = "@{dev_lc}[ic]"           # the collector current, attributed to the first node field's pin (c)
```

Rules that apply only to these parts:

- The node fields fix the SPICE order. The validator checks that they are the pins, once each; it does not check the order, so get the order right in the template.
- The current is attributed to the pin in the first node field (collector for Q, drain for J and M). Q, J and M accept only the exact current forms above.
- Only two-terminal parts get `V(Part)`. A transistor shows its node voltages and its current, and no across voltage.
- The MOSFET gate (the second node field of M) has no DC path. A gate that is unwired, or driven only through a capacitor or a current source, is `NO-DC-PATH` and blocks the run. Drive it through a resistor or a voltage source. A BJT base or JFET gate is not checked this way: ngspice solves it through its junctions, and an unwired pin gets a `DANGLING` warning.

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
| `DEF-SIM` | error | an electrical section that the template check rejects, or a fixed model name already used with a different body by an earlier definition |
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
