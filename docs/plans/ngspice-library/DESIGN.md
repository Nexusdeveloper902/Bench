# ngspice library: recon findings and design (task 1)

Scope: the SPEC.md phase one, plus ngspice 47 facts that tasks 2-9 depend on. Every ngspice claim below was run on
this machine (ngspice-47, Arch package 47-1, `/usr/lib/libngspice.so.0.0.16`) on 2026-10-10, through a harness that
initialises the shared library exactly as `benchcore/src/ngspice.rs` does (`probes/ng_probe.py`), or through
`benchcore` itself. The CLI (`ngspice -b`) was used only for structural questions (flattened listings).

## 1. Pipeline as it is (file:line)

| Stage | Where | What happens |
|---|---|---|
| Package discovery | `app/catalog.py:386-423` | `rglob("component.toml")` under `app/library/` + `SIM_COMPONENT_PATH`; first id wins (`DEF-DUP-ID`) |
| Package parse | `app/catalog.py:140-348` | TOML -> `Definition`; unknown top-level keys are errors (`TOP`, :31); assets must stay inside the package (`asset()`, :270-280) |
| Domain hook | `app/catalog.py:21`, `:333-337` | `SIM_DOMAINS["electrical"] = electrical.check(section, pins, params)`; no package path is passed |
| Device letters | `app/electrical.py:9-11` | `DEVICE_LETTERS = "RCLDVIQJM"`, `PINS = {Q:(3,4), J:(3,), M:(4,)}`, `CUR_PIN` |
| Terminal count | `app/electrical.py:81-85` | pin count must be in `PINS.get(letter, (2,))` |
| Template choice | `app/electrical.py:66-80`, `:146-148` | `line` is one string or `{select = <choice param>, <choice> = line}` |
| Pin->node mapping | `app/electrical.py:91-99` | tokens after `{dev}` must be exactly the pin placeholders, once each; order = SPICE order |
| Model token | `app/electrical.py:100-104` | for Q/J/M the token after the nodes must be a model named in `models` |
| `.model` text | `app/electrical.py:105-111` | `models` = list of `.model ...` lines only (`.subckt` impossible) |
| Global model names | `app/electrical.py:127-133` + `app/catalog.py:413-420` | fixed model names: one body per name across the catalog (`DEF-SIM`) |
| Nets | `app/model.py:160-179` | union-find over wires; ground nets `0`, others `n1..` |
| Netlist | `app/model.py:198-207` -> `app/electrical.py:158-214` | one line per part (`emit`, :151-155), models deduped by name/body (:183-191), `V(part)` only for 2 pins (:192), `.save all @x[y]` (:210) |
| Device name | `app/electrical.py:136-139` | letter prefixed unless present (`SW1` -> `RSW1`); collisions `DUP-DEVICE` (`app/model.py:225-230`) |
| Run | `app/sim.py:65-101` | `build_circuit(lines)`, `add_model(m)`, `add(save)`, `configure`, `observe`, `validate()`, `run()` |
| Deck text | `benchcore/python/benchcore/__init__.py:110-117` | title + devices (+ saves) + models + `.tran tstep tstop 0 tmax uic` + `.end` |
| Static gate | `benchcore/src/kernel.rs:307-317` | `validate::validate`; any `error` finding -> `InvalidCircuitError` |
| Validator | `benchcore/src/validate.rs:208-601` | joins `+` lines (:223), pre-pass collects top-level `.model` type/level and `.subckt` port counts (:233-271), per-letter arms (:348-593), topology checks (:669-1163) |
| ngspice | `benchcore/src/ngspice.rs:142-206`, `kernel.rs:250-297` | dlopen once, `ngSpice_Init`, `ngSpice_Circ`, `run`, read vectors from the current plot |
| Failure scan | `benchcore/src/kernel.rs:162-197` | substring patterns over the log -> `SolverError` / `ConvergenceError` |
| Results | `app/sim.py:91-101` | series per observable; derived `v(part)` by subtraction |

Not on the app path: `benchcore/src/components.rs` (`Component` enum, unused; engine-design.md section 4) and
`benchcore/src/thin.rs` (B-expression allowlist, no PyO3 binding; app B lines would bypass it).

## 2. Confirmed limitations (evidence)

| # | Limitation | Evidence |
|---|---|---|
| L1 | App accepts only R C L D V I Q J M | `app/electrical.py:9`, `:60-61` |
| L2 | `models` cannot carry `.subckt`; no file dependencies; no X parts | `app/electrical.py:105-111`; `check()` gets no package path (`catalog.py:336`) |
| L3 | Any `@{dev_lc}[x]` current is accepted for 2-pin letters | `app/electrical.py:13`. ngspice 47 **segfaults the process** with `.save all @z1[id] @m1[id]` (invalid MESFET param `id`; the valid one is `cd`) |
| L4 | `.subckt` bodies are skipped wholesale; nested definitions break the skip | `validate.rs:317-329`: `in_subckt` is a bool, so the inner `.ends` re-exposes the rest of the outer body as top level. Verified: outer body `Cfloat zz yy 1n` -> false `NO-DC-PATH` errors |
| L5 | X: no undefined-subckt check, instance params unchecked, all-pairs DC optimism, `OPAQUE-X` on every instance | `validate.rs:520-558`, `:1043-1058` |
| L6 | Undefined subckt runs "successfully" with **0 points** | `benchcore` deck `X1 a b nosuch` -> `ok`, `npts 0`. ngspice prints `Error: unknown subckt` but `ngSpice_Circ` and `run` return 0; `scan_loud_failure` (`kernel.rs:163-180`) has no matching pattern; `check_observables` skips an empty grid (`kernel.rs:233-236`) |
| L7 | One bad expression **bricks the engine for the whole process** | `benchcore`: `R1 a b {nope}` -> `circ rc=1`, then every later run (valid decks too) -> `BackendError circ rc=1`. ngspice calls the exit callback and prints "ngspice.dll cannot recover and awaits to be reset or detached"; `cb_exit` ignores it (`ngspice.rs:120-128`) and the library is never reloaded (`:142-206`). Same for E/G `LAPLACE`. App params render as numbers, so today only package text can trigger it; subckt bodies (`{param}` everywhere) make it likely |
| L8 | `A` (XSPICE) is an error even though code models load | `validate.rs:583-592` (`UNKNOWN-KIND`) |
| L9 | VDMOS (3-node `M`) rejected | `validate.rs:456-474`: M needs 4 nodes -> `MALFORMED "model name 'vd' in a node position"` |
| L10 | E/G behavioural forms rejected; S/W/T/O/U/Y/Z read as 2 nodes | `validate.rs:416-438` (`E1 b 0 VALUE={..}` -> `MALFORMED`), `:559-582` |
| L11 | Q level 6 (Mextram) treated as valid 5-node model, but this build has no Mextram | `validate.rs:480`; ngspice: `Unable to find definition of given model` |
| L12 | Errors surface as `circ rc=1` without the ngspice message | `kernel.rs:254-259`; the reason is only in the log |
| L13 | Behaviour depends on the working directory | the shared library reads `./.spiceinit` at init (verified: a `.spiceinit` echo appears); relative `.include` resolves against the process cwd |
| L14 | B-source TMAX policy ignores B lines inside subckts | `validate::b_exprs` reuses `parse_netlist`, which skips bodies (`kernel.rs:77-79`). Harmless today: the app sets `tmax = tstep` (`model.py:192`) |

## 3. ngspice 47 facts (verified)

### 3.1 Element letters (shared library, `.tran ... uic`)
| Letter | Works | Nodes (order) | Model / form | Current vector (verified) |
|---|---|---|---|---|
| R | yes | n+ n- | value, `{expr}`, `R='expr'` (behavioural), `rmod L= W=` (`.model R`), `tc1=` | `@r[i]` |
| C | yes | n+ n- | value, `IC=`, `Q='expr'` | `@c[i]` |
| L | yes | n+ n- | value, `IC=` | `@l[i]` |
| K | yes | (none) | `K1 L1 L2 k` | - |
| D | yes | a k | `D` levels 1, 3; **level 2 fatal** ("not supported") | `@d[id]` |
| Q | yes | c b e [s] [tj] | NPN/PNP levels 1, 2, 4 (VBIC), 8 (HICUM), 9; **3, 5, 6 (Mextram), 7, 10+ absent**; `LPNP` fails | `@q[ic]` |
| J | yes | d g s | NJF/PJF levels 1, 2; 3 unknown | `@j[id]` |
| M | yes | d g s b (+ SOI nodes) | NMOS/PMOS levels 1-6, 8, 9, 14, 49, 54, 68, 73, SOI 10, 55-58, 60; **7, 44, 45, 61, 62, 64, 70, 77 fail** | `@m[id]` |
| M VDMOS | yes | **d g s** [tj tc + `thermal`] | `VDMOS` (n default, `pchan`); LTspice cards with `mfg= Vds= Ron= Qg=` accepted | `@m[id]` |
| Z | yes | d g s | NMF/PMF levels 1-6 (MES, MESA, HFET) | `@z[cd]` (**`@z[id]` can segfault**) |
| V, I | yes | n+ n- | DC, PULSE, SIN, PWL, EXP, SFFM, AM, TRNOISE | `i(v)`, `@i[c]` |
| E | yes | o+ o- [c+ c-] | linear gain, `GAIN=`, `VALUE={}`, `vol='..'`, `TABLE {..} = (x,y)..`, `POLY(n)`; **`LAPLACE` fails and bricks the library** | `@e[i]`, `i(e)` |
| G | yes | o+ o- [c+ c-] | linear, `cur='..'`, VALUE/TABLE/POLY | `@g[i]` |
| F, H | yes | o+ o- | `Vsense gain` (references another element by name) | - |
| B | yes | n+ n- | `V=` / `I=`; **`if()`, `table()` unknown (brick)**; `limit()` silently returns 0 without compat mode | `@b[i]` (`i(b)` absent) |
| S | yes | n+ n- nc+ nc- | `SW` (defaults ok, `ON/OFF`, negative `Vh`); PSpice `VSWITCH` needs `ngbehavior=ps(a)` | `@s[i]` |
| W | yes | n+ n- | `Vsense CSW` | - |
| T | yes | p1+ p1- p2+ p2- | `Z0= TD=` | `@t[i1]` |
| O | yes | 4 | `LTRA` | - |
| U | yes | n1 n2 ncap | `URC` + `L=` | - |
| Y | yes | 4 | `txl` | - |
| P | yes | 2N+2 | `CPL` | - |
| A | **yes** | per code model, `[..]` vectors | 81 code models loaded (analog, digital, bridges, `potentiometer`, `memristor`, `zener`, `sidiode`, `pswitch`, `table2d/3d`, `d_cosim`...) | per model |
| N (OSDI) | **no** | - | no `.osdi` files installed and `spinit` has `unset osdi_enabled` | - |
| X | yes | ports | subckt + params | internal: `v(x1.n)`, `i(v.x1.vs)`, `@d.x1.d1[id]` (with `.save`) |

XSPICE: `/usr/share/ngspice/scripts/spinit` runs `codemodel /usr/lib/ngspice/*.cm` when `xspice_enabled`;
`ngSpice_Init` reads it, so Bench's init path has the code models. Verified through `benchcore` itself
(adc_bridge -> d_inverter -> dac_bridge inside a subckt: correct inverted 0/3.3 V output). Side effects: ngspice logs
"Reducing trtol to 1 for xspice 'A' devices" (a global accuracy change for the whole deck); digital nets have no
`v()` vectors (observe through a `dac_bridge`); bridge levels are fixed model params, not supply-tracking.

### 3.2 Subcircuits
- Flattening: element `R1` in instance `X1` becomes `r.x1.r1`; node `mid` becomes `x1.mid`; nested: `r.x3.xa.r1`,
  `x3.xa.mid`. Node `0` inside a body is global ground; `.global vdd` nodes are global.
- `params:` with defaults on `.subckt` (also without the keyword: `.subckt s a b threshold=1k`); instance overrides
  `X1 a b s ra=3k`, `X1 a b s params: ra=2k`, expressions `rb={gain*1k}` all work.
- **Unknown instance params are silently ignored** (`X1 b 0 s q=3`: no message).
- `.param` and `.func` inside a body are local to it; top-level `.param` is visible in every body.
- `.model` inside a body is local and **duplicated per instance** (`x3.xc:dloc`, `x4.xc:dloc`); a local model shadows
  a global one of the same name. A top-level `.model` used by bodies is shared, not duplicated.
- Nested `.subckt` definitions inside a body work and are local.
- One definition shared by many X instances works; definition order does not matter (definition after use is fine).
- Subckt and model names live in separate namespaces (`dd` as both works); names are case-insensitive.
- Duplicate `.subckt` name: **first wins**, "Warning: redefinition of .subckt s, ignored" (even with a different body).
  Duplicate `.model` name with a different body: **first wins silently**.
- Errors (all with `ngSpice_Circ` rc 0 unless noted): unknown subckt ("Error: unknown subckt"), port-count mismatch
  ("Too many parameters for subcircuit type"), undefined model in a body, too few nodes.
- Exit callback (`ngSpice_Circ` rc 1, "cannot recover and awaits to be reset or detached"): undefined `{param}`,
  `LAPLACE`, unknown function in an expression (`if`, `table`, a body-local `.func` used outside), missing `.ends`,
  missing `.include` file. The numparam class ("Cannot compute substitute": undefined param, `LAPLACE`) leaves the
  library **permanently unusable** (every later circ rc 1); the others recover on the next circ.
- Recovery: `dlclose` + `dlopen` + `ngSpice_Init` restores a working library (verified with ctypes).
- `.include`/`.lib file section` work; relative paths resolve against the process cwd.
- Inline comments `;`, `$ `, `//` and `+` continuations inside bodies are accepted.
- Floating nodes: a capacitor-only node solves under `uic`; an isolated transformer secondary (`K` coupled, no DC
  path to 0) is a **singular matrix even with `uic`**; a 1 GΩ leak to 0 inside the subckt or `.option rshunt=1e12`
  fixes it. An unwired MOSFET gate is singular. So the existing `NO-DC-PATH` rule matches ngspice for windings.

### 3.3 Compatibility mode
- Default (this `spinit`, no `.spiceinit`): "No compatibility mode selected!".
- `ngSpice_Command("set ngbehavior=<mode>")` before `ngSpice_Circ` works; `unset ngbehavior` resets it.
- `ps`/`lt` (without `a`) apply only to `.include`d files (verified: PSpice `IF()` inside an included subckt works
  with `ps`, fails inline). `psa`/`ltpsa` transform the whole deck: `VSWITCH`, `IF()`, `LIMIT()`, `TC=a,b` then work.
  `TABLE()` in B and LTspice `Rser=` on C fail in every mode.
- A `./.spiceinit` in the cwd is executed by the shared library at init (L13). `ngSpice_nospiceinit()` segfaults
  (`experiments/coupling-proto/REPORT.md:58`).

## 4. Design

Extend the existing `[sim.electrical]` section and the existing validator. No new component system, no new domain.

### 4.1 Three backing kinds (derived from the section, not declared twice)
| Kind | Section shape | Example |
|---|---|---|
| primitive | one element line, no model token | R, C, L, V, I, B, E/G (linear), T |
| model-backed | one element line + `.model` card(s) (`models`, or a top-level card in a `defs` file) | D, Q, J, M (incl. VDMOS), Z, S |
| subckt-backed | `device = "X"`, line instantiates a `.subckt` from `defs` | op-amps, 555, gates, relays, sensors |

Letters that reference another element by name (K, F, H, W), use bracketed ports (A), or have no static model (O,
U, Y, P) are allowed **only inside subckt bodies**. Top-level parts never reference other parts by name (part names
are user-editable).

### 4.2 Device definition table
One table per layer, kept in sync by a parity test (no new PyO3 API): for every Python entry, an example line must
pass `benchcore.validate` without `MALFORMED`/`UNSUPPORTED`.

Python, `app/electrical.py` (top-level part letters; replaces `DEVICE_LETTERS`, `PINS`, `CUR_PIN`):

| Letter | Pins | Node fields (SPICE order) | Token after nodes | Model types | `current` allowed |
|---|---|---|---|---|---|
| R | 2 | n+ n- | value or own model | R | `@{dev_lc}[i]` |
| C | 2 | n+ n- | value | C | `@{dev_lc}[i]` |
| L | 2 | n+ n- | value | L | `@{dev_lc}[i]` |
| D | 2 | a k | own model | D (levels 1, 3) | `@{dev_lc}[id]` |
| V | 2 | n+ n- | source spec | - | `i({dev_lc})` |
| I | 2 | n+ n- | source spec | - | `@{dev_lc}[c]` |
| B | 2 | n+ n- | `V=`/`I=` expr, no pin placeholders in the expr | - | `@{dev_lc}[i]` |
| E, G | 4 | o+ o- c+ c- | gain number | - | `@{dev_lc}[i]` |
| S | 4 | n+ n- nc+ nc- | own model | SW | `@{dev_lc}[i]` |
| T | 4 | p1+ p1- p2+ p2- | `Z0= TD=` | - | none |
| Q | 3, 4 | c b e [s] | own model | NPN, PNP (levels 1, 2, 4, 8, 9) | `@{dev_lc}[ic]` |
| J | 3 | d g s | own model | NJF, PJF (1, 2) | `@{dev_lc}[id]` |
| M | 4; 3 iff VDMOS | d g s [b] | own model | NMOS, PMOS (verified levels), VDMOS | `@{dev_lc}[id]` |
| Z | 3 | d g s | own model | NMF, PMF (1-6) | `@{dev_lc}[cd]` |
| X | port count of the subckt | subckt port order | subckt name from `defs` + `NAME={param}` | - | none |

`current` becomes an exact per-letter allowlist (L3). Model type and `level` of the named card are checked against
the table at catalog load (`DEF-SIM`), from the same parse `fixed_models` already does.

Rust, `benchcore/src/validate.rs` (every letter, top level and bodies): a `match` on the letter becomes one table
row each: min/max nodes, how the end of the node list is found (model token / value / named reference / `=` form /
bracket ports), model types and supported levels (section 3.1), which node pairs are DC edges. DC edges: R, L, V, E/H
outputs, B with `V=`, D, Q, J, Z, S and W switch pairs, T/O/U/Y port pairs, M except the gate (VDMOS gate too);
none for C, I, G/F outputs, B with `I=`, K, control inputs. Rows marked `unsupported` produce an error with a
reason (Q level 6, D level 2, M levels from 3.1, E/G `LAPLACE`, N, PSpice-only functions `if`/`table`, `limit`).

### 4.3 Package format for subckt-backed parts (new optional keys in `[sim.electrical]`)
```toml
[sim.electrical]
device = "X"
line = "{dev} {inp} {inn} {vcc} {vee} {out} bench_opamp GAIN={gain} GBW={gbw}"
defs = ["opamp.lib", "models:generic/clamp.lib"]   # files holding .subckt/.model definitions
nc = ["NC"]                       # pins deliberately not connected to the model (optional)
model_kind = "project-subckt"     # primitive | model | third-party-model | project-subckt | third-party-subckt | behavioral | equivalent
verification = "analytic"         # analytic | reference | smoke | none
[sim.electrical.provenance]       # required when model_kind is third-party-*; optional otherwise
source = "..."; url = "..."; license = "SPDX id or LicenseRef-..."; notes = "..."
```
Optional inline form for small project-authored bodies: `body = """.subckt ... .ends"""` (same rules as a file).
Rules:
- Pins <-> ports: the node fields of `line`, in order, are the subckt ports in order; every pin except those in
  `nc` appears once (existing rule). `count(node fields) == port count of the named .subckt` (`DEF-SIM`).
- Params: `[[params]]` defaults are rendered into the instance line (`NAME={key}`); every `NAME=` must be a declared
  param of that subckt (ngspice silently ignores unknown ones). Subckt-side defaults remain the ngspice fallback.
- Def text is literal: never passed through `str.format` (its `{...}` are ngspice expressions), never templated per
  instance, so one definition serves every instance.
- `defs` paths: package-relative, or `models:<path>` resolved under `<root>/_models/` of the root the package came
  from; must stay inside that directory (`DEF-FIELD`, same rule as `asset()`); read once at catalog load, cached
  with a sha256.
- Allowed in def text: comments, blank lines, `+` continuations, `.subckt`/`.ends` blocks, `.model`; `.param` and
  `.func` only inside a body. Everything else (`.include`, `.lib`, `.control`, `.option(s)`, analyses, `.end`,
  `.global`, `.save`, `.osdi`, element lines outside a body) is `DEF-SIM` (security: packages are untrusted input).
- Names are global and case-insensitive: a subckt or top-level model name with two different bodies across the
  catalog is a `DEF-SIM` load error (extends `fixed_models`, `catalog.py:413-420`), because ngspice keeps the first.
- `current`: none for X. `V(part)` stays 2-pin only.
- Dialect: ngspice syntax only. PSpice files would need `.include` + `set ngbehavior=ps` (3.3); deferred.

### 4.4 Deck assembly
`build_netlist` keeps its outputs; `models` grows to "definitions". Order: parts in creation order; per part its
inline `models` (existing dedupe), then its `defs` units in listed order, each unit emitted once per deck (key:
resolved path + sha256), framed by `* bench-def <root-relative path> <sha8>` comments. ngspice accepts any order, so
first-use order gives determinism without a dependency sort. Decks without `defs` are byte-identical to today
(`app/tests/fixtures/netlist_golden.json`). Content is inlined, never `.include`d: cwd-independent (L13), a missing
file cannot brick the engine (3.2), the validator sees every line, and the deck in `RunResult.details` is
self-contained.

### 4.5 Engine robustness (prerequisite, task 2)
1. Exit callback: `cb_exit` sets a flag; after `ngSpice_Circ`/`ngSpice_Command`, if set, drop the `Lib`
   (dlclose) and re-init on the next call; return `BackendError` with the log's error lines (L7).
2. Failure detection: any `stderr Error` log line is a failure; a transient deck that yields an empty time grid is
   a `SolverError`, never `ok` with 0 points (L6, L12).
3. `ngSpice_Command("unset ngbehavior")` before every circ (pins the compat mode against a stray `.spiceinit`).
4. Current allowlist (4.2) so the app never emits an unverified `.save @dev[param]`.

### 4.6 Validator for subckt bodies (task 4)
One SPICE parser, in Rust. The catalog reuses it at ingestion through the existing `benchcore.validate`: a synthetic
deck = title + the part's instance line (default params, each pin on its own net with a 1 GΩ resistor to 0) + its
models + defs. Findings map to `DEF-SIM` diagnostics (errors reject the package; limited-analysis warnings keep it
and add a line to its displayed limitations). The same validator runs on the whole deck at run time.
- Parse into a scope tree: top level, each `.subckt` (with ports, params+defaults, local `.param`/`.func`, local
  `.model`, nested `.subckt`). Depth-correct `.ends` (fixes L4). Strip `;`, `$ `, `//` comments, join continuations.
- Resolve per scope chain: model names, subckt names (+ port count), `{}` identifiers (params, `.param`, `.func`,
  builtins), instance `NAME=` params.
- Flatten X instances for connectivity (ports -> actual nets, `0`/`.global` stay global, internals prefixed with the
  instance path), with a recursion guard; run the existing checks on the flattened graph. Findings carry `scope`
  (instance path, `""` at top level) so internal problems are reported apart from external wiring.
- Elements the table marks as not statically modelled (A, T/O/U/Y/P, nonlinear E/G) are treated as conducting
  between their analog nodes (no false `NO-DC-PATH`) and produce one `LIMITED-ANALYSIS` warning per instance naming
  them. `OPAQUE-X` remains only for an X whose definition is not in the deck (`.include`).
- Taxonomy: a new `class` field on every finding (PyO3 dict key), old codes mapped:

| class | codes |
|---|---|
| syntax | MALFORMED, DUP-NAME, SUBCKT-UNBALANCED (new), BAD-DIRECTIVE (new) |
| dependency | UNDEFINED-MODEL (now per scope), UNDEFINED-SUBCKT (new, error), UNDEFINED-PARAM (new, error: bricks), UNKNOWN-INSTANCE-PARAM (new, error), DUP-SUBCKT / DUP-MODEL with different body (new, error), SUBCKT-RECURSION (new, error) |
| connectivity | NO-GROUND, NO-DC-PATH, ISRC-OPEN, V-LOOP, DANGLING, SHORTED-VSRC, SHORTED-PASSIVE |
| unsupported | UNKNOWN-KIND, UNSUPPORTED (new, error, with reason), EXOTIC-DEVICE (warn) |
| limited | LIMITED-ANALYSIS (new, warn), OPAQUE-X (warn) |
| value | BAD-VALUE, NEG-R, NEG-CL, ZERO-R, ZERO-CL, B-* |

Not claimed: a clean result proves syntax, references and DC topology only, never electrical well-posedness.

## 5. Backwards compatibility
- The 17 shipped packages keep ids, pins, params and `version`; no edits needed. New keys are optional (`format = 1`).
- Saved circuits store type, version, params and wired pins (`model.py:326-338`); nothing new is persisted.
- Golden decks (`test_template_netlist_matches_golden`, `test_determinism`) must stay byte-identical.
- New validator errors must not fire on existing decks: 480 app, 35 cargo and 5 Python benchcore tests stay green.
  `t20_xspice_pause_fenced` (`correctness.rs:605`) relies on `A1 ... mycode` + `.model mycode xspice()` failing:
  once A is accepted it must fail through 4.5(2) ("Unknown model type"), not pass.
- `app/tests/test_catalog.py:18` pins `ALL_CATEGORIES`: library batches use existing categories (new subcategories
  are free) or update that constant deliberately.
- `components.rs` stays unused (no enum variants for new letters).

## 6. Task scopes

### Task 2: generalize device support (engine + app)
- `benchcore/src/ngspice.rs`: 4.5(1) exit-flag + reload; 4.5(3).
- `benchcore/src/kernel.rs`: 4.5(2); surface the first ngspice error line in `BackendError`/`SolverError` messages.
- `benchcore/src/validate.rs`: table-driven arity for every letter (3.1); Z; VDMOS 3 nodes (+`tj tc thermal`);
  E/G forms (linear, `GAIN=`, `VALUE`, `vol=`/`cur=`, `TABLE`, `POLY(n)`); S/W/T/O/U/Y/P arity and DC edges; A
  accepted (bracket ports parsed, `LIMITED`-style warn until task 4); N, LAPLACE, Q level 6/3/5/7, D level 2,
  unsupported M levels -> `UNSUPPORTED`; nested `.ends` depth fix (L4).
- `app/electrical.py`: `DEVICES` table (4.2) replacing `DEVICE_LETTERS`/`PINS`/`CUR_PIN`; model type/level check;
  current allowlist; VDMOS pin rule.
- Rebuild/reinstall the wheel (CLAUDE.md).
- Tests: `correctness.rs` per letter valid + malformed + unsupported; kernel: bad-param deck then good deck in one
  process succeeds; unknown subckt is `Err`; `test_api.py` recovery through Python; `test_catalog.py` parametrized
  per new letter (valid, wrong pin count, wrong model type, bad current); parity test (4.2); end-to-end runs through
  `Document` for S (switch on/off by control voltage), E (gain), G, Z, VDMOS (Id vs Vgs), each against hand values.
- Docs: `docs/adding-components.md` letter table; `benchcore/docs/components.md` codes.

### Task 3: subckt-backed packages
- `app/electrical.py`: X letter; keys `defs`, `body`, `nc`, `model_kind`, `verification`, `provenance`; header
  parse of defs (`.subckt` name/ports/params, top-level `.model` names) for the port-count and param checks; directive
  allowlist; `build_netlist` emits defs once (4.4).
- `app/catalog.py`: pass the package dir and root to the domain check; cache def text + sha256; reload-time global
  conflict map for subckt and def-file model names.
- `app/library/_models/` + `MANIFEST.toml` (section 7); one seed part proving the format end to end (a
  project-authored generic op-amp; see 7.3 for why not the KiCad block verbatim).
- Tests: valid X; port mismatch; unknown subckt name; `defs` path escaping the root; conflicting bodies across
  packages; three instances -> one definition in the deck; `nc` pins; instance param rendered and propagated
  (non-inverting gains 2 and 11 from two instances of one def, analytic `1 + Rf/Rg`); unique internal nodes
  (`v(xu1.int)` != `v(xu2.int)`); nested dependency across two files; save/load round trip; golden decks unchanged.
- Docs: `docs/adding-components.md` "Subcircuit parts" with the example; README part list if it lists parts.

### Task 4: subckt-aware validator
- `benchcore/src/validate.rs`: scope tree, resolution, flattening, taxonomy (4.6); `lib.rs`: `class`, `scope` keys.
- `app/electrical.py`/`catalog.py`: ingestion via synthetic deck; map findings to `DEF-SIM` errors/warnings;
  record validation status (full / limited + reasons) on the definition; inspector shows it under limitations.
- Tests (`correctness.rs` + app): one per code in the table; nested subckt; recursion; unknown instance param;
  undefined param inside a body is rejected statically (the engine is never bricked); floating internal node reported
  with its instance path; isolated winding -> `NO-DC-PATH`, same with leak R -> clean; differential input pair
  without a ground reference inside the body -> clean; `0` inside a body is global; `.global`; continuations and
  `;`/`$` comments inside bodies; duplicate element names in one body vs. same name in two bodies; local model
  shadowing; A inside a body -> `LIMITED-ANALYSIS`, not an error.

## 7. Library batches (5-9): feasibility on ngspice 47

| Batch | Feasible now | Defer / caveat |
|---|---|---|
| 5 passives, sources, switches | R/C/L variants; ESR/ESL as subckts (LTspice `Rser=` is rejected); potentiometer/rheostat (R pair subckt with a position param, or XSPICE `potentiometer` in a body); resistor arrays; coupled inductors and transformers (K in a body + 1 GΩ leak per isolated winding); T line (top level), LTRA in a body; V/I DC, PULSE, SIN, EXP, SFFM, AM, TRNOISE; battery with internal R; E/G linear (top level), F/H/W in bodies; SPST/SPDT/DPDT as R-choice parts (like `SW`) or S elements; relay (coil R+L driving S with hysteresis); reed switch | PWL with user tables (params are numbers/choices only); fuses/PTC that latch need state (behavioural approximation only, say so); AC/DC/noise analyses do not exist in the core (transient only) |
| 6 semiconductors | D models (1N400x, 1N4148 exists, 1N581x Schottky, 1N47xx Zener via `BV`, TVS via `BV`, varactor via `CJO/M`); LEDs per colour (fit Is/N/Rs to a Vf point, mark approximate); BJTs NPN/PNP small-signal/power; MOSFETs as VDMOS (LTspice VDMOS cards load unchanged) or level 1/3; JFETs; photodiode/phototransistor as subckts with an irradiance param | IGBT, SCR, TRIAC, DIAC: no native model, only project-authored equivalents (mark `equivalent`, list missing effects); Mextram BJTs; anything needing OSDI |
| 7 analog ICs | project-authored behavioural op-amps (gain, GBW pole, rails, output R; the generic single-pole topology is verified to run 2 instances under `uic`); comparators (open-collector via S/B); 555 (comparators + `d_srlatch` or B logic + discharge S); TL431-style references; 78xx/LM317-style regulators (behavioural, dropout); CD4066/CD4051 switches (S with thresholds); LM386-style audio amp | vendor macromodels: most are PSpice syntax (needs 3.3 dialect support) and carry redistribution terms that must be checked file by file; all ICs start from 0 V (`uic`), so references and regulators show a start-up transient |
| 8 digital / mixed | XSPICE works: gates (`d_and/or/nand/nor/xor/inverter`), `d_dff/jkff/tff/srlatch`, counters/shift registers as body chains, each part a subckt with `adc_bridge` inputs and `dac_bridge` outputs (+ output R); ADC/DAC as bridge-based or B quantizer idealisations | Schmitt hysteresis needs a body (analog `hyst` model or B+S); supply pins are not supply-tracking (fixed bridge levels; say so); one A device lowers `trtol` deck-wide; d_cosim, d_ram, HDL out of scope |
| 9 sensors / other | LDR, NTC/PTC, RTD (resistance from a stimulus param via subckt `params:` expressions); photodiode; Hall (B output from a field param); FSR, strain gauge bridge, position pot; electret mic, ultrasonic BVD, crystal BVD (RLC + C0); speaker (Thiele-Small RLC); DC motor and solenoid R-L + back-EMF equivalents; optocoupler (D + CCCS/Q in a body); shunt current sense; probes as 0 V sources | humidity/pressure/gas only as simplified electrical equivalents with a static stimulus param; time-varying stimuli (no PWL param); connectors/terminals with no element (a part needs a line; a 0-Ω part trips `ZERO-R`) |

### 7.1 Directory layout
```
app/library/
  _models/                       shared definitions; no component.toml, so discovery ignores it
    MANIFEST.toml                one [[file]] record per file below
    LICENSES/<LicenseRef>.txt    full license texts where a license requires them
    generic/opamp.lib
    diodes/1n400x.lib
  electrical/<id>/component.toml (+ symbol.svg, optional package-local *.lib)
<extra root>/_models/...         same layout; packages resolve models: only within their own root
```

### 7.2 Provenance record (`_models/MANIFEST.toml`)
```toml
[[file]]
path = "generic/opamp.lib"
origin = "project"                     # project | third-party
source = "Bench project; single-pole topology after H. Vogt's public-domain KiCad generic op-amp"
url = ""
version = ""                           # upstream version/tag when third-party
license = "MIT"                        # SPDX id or LicenseRef-<name> with LICENSES/<name>.txt
attribution = ""                       # text the license requires to be shown
retrieved = "2026-10-10"
sha256 = "<hex of the file as shipped>"
changes = "none"                       # what was changed from upstream (renames, syntax fixes)
```
A test checks: every file under `_models` has exactly one record, the sha256 matches, `license` is set, and every
package whose `model_kind` is `third-party-*` has a `provenance` table or uses only third-party files with records.

### 7.3 Model sources seen on this machine
- `/usr/share/kicad/symbols/Simulation_SPICE.sp` (kicad-library 10.0.7, package license CC-BY-SA-4.0): generic op-amp
  (single/dual/quad), varistor, `pot_dual`. The op-amp block says "Author Holger Vogt, Public Domain" but ships in a
  CC-BY-SA file: re-author the topology as a project model (it is textbook) rather than copy it.
- No other SPICE libraries are installed. Vendor models must be fetched per part with their terms recorded.
