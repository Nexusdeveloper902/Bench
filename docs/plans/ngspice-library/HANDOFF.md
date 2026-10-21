# Handoff log (sequential agents)

Brief: `SPEC.md`. Design and evidence: `DESIGN.md`. Probe harness: `probes/ng_probe.py`.
Each task appends its own section below. Do not rewrite earlier sections.

## Task 1: recon + design (done, no source changes)

### Summary
- Traced the pipeline end to end (DESIGN.md section 1, file:line). SPICE text is produced only by
  `app/electrical.py` (one line per part, `.model` lines only), sent through `benchcore.Simulation`
  (`sim.py:83-86`; deck = title + devices + saves + models + `.tran ... uic` + `.end`), gated by
  `validate.rs`, run by `kernel.rs` / `ngspice.rs`.
- Confirmed 14 limitations L1-L14 (DESIGN.md section 2). The ones that change the plan:
  - **L7:** a single undefined `{param}` or E/G `LAPLACE` leaves libngspice unusable for the whole process.
    Every later `benchcore` run, valid ones included, fails with `circ rc=1`. Subckt bodies are full of `{param}`,
    so this must be fixed before task 3. Fix: handle the exit callback by reloading the library; dlclose and reload
    was verified to recover.
  - **L6:** an undefined subckt returns `ok` with 0 points (a silent success).
  - **L3:** a wrong `.save @dev[param]` can segfault the process (`@z1[id]` plus a second save). Currents need an
    exact per-letter allowlist.
  - **L4:** a nested `.subckt` breaks the body skip in `validate.rs:317-329`, which gives false `NO-DC-PATH` errors.
  - **L8 to L11:** XSPICE `A` is rejected even though the code models load. VDMOS (3-node `M`) is rejected. E/G
    `VALUE`/`TABLE`/`POLY` are rejected. Mextram (Q level 6) is treated as valid but does not exist in this build.
- Design (DESIGN.md section 4):
  - extend `[sim.electrical]` with `device = "X"`, `defs`/`body`, `nc`, `model_kind`, `verification` and
    `provenance`;
  - one explicit device table per layer (Python for top-level part letters, Rust for every letter), with a parity
    test between them;
  - inline every definition into the deck once, in first-use order, never through `.include`;
  - a single Rust SPICE parser, reused at catalog ingestion through a synthetic deck;
  - a `class` field (syntax, dependency, connectivity, unsupported, limited, value) and a `scope` field on findings.
- Shared model files go in `app/library/_models/`, with a `MANIFEST.toml` provenance record per file (DESIGN.md 7.1
  and 7.2).

### Baseline tests (branch `feat/multi-terminal-transistors` @ 7a8606f, before any change)
| Suite | Result |
|---|---|
| `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests` | **480 passed** (52.9 s) |
| `cd benchcore && cargo test` | **35 passed** (adder.rs 3, correctness.rs 32; lib and doc tests 0) |
| `cd benchcore && python3 -m pytest -q tests/python` | **5 passed** |

### Key ngspice facts (ngspice-47, `/usr/lib/libngspice.so.0.0.16`; details in DESIGN.md section 3)
- **Element support:**
  - These element letters work: R C L K D Q J M Z V I E G F H B S W T O U Y P A X.
  - N (OSDI) does not work: no `.osdi` files are installed and spinit runs `unset osdi_enabled`.
  - Unsupported levels: D level 2, Q levels 3/5/6/7 and 10+, M levels 7/44/45/61/62/64/70/77.
  - E/G `LAPLACE` is not supported.
  - VDMOS takes 3 nodes, and LTspice VDMOS cards load unchanged.
- **XSPICE:** spinit loads 81 code models, digital and bridges included, through Bench's own init. This was
  verified with `benchcore` (an A device inside a subckt). Any A device makes ngspice lower `trtol` for the whole
  deck.
- **Subcircuit naming:** `r.x1.r1`, nodes `x1.mid`, local models `x1:dloc` (duplicated per instance). Node `0` and
  `.global` nodes are global.
- **Subcircuit params and names:** `params:` with defaults and instance overrides work, with or without the keyword.
  Unknown instance params are ignored silently. `.param`/`.func` in a body are local. Subckt and model names are
  separate namespaces, case-insensitive. A duplicate `.subckt` keeps the first (with a warning). A duplicate
  `.model` keeps the first, silently.
- **Subcircuit errors:**
  - These print errors but return rc 0, which Bench misses: unknown subckt, port mismatch, missing model in a body,
    too few nodes.
  - These fire the exit callback: undefined param, `if()`/`table()`, LAPLACE, missing `.ends`, missing include.
    Undefined param and LAPLACE leave the library permanently unusable.
- **Compatibility mode:**
  - The default is none.
  - `ngSpice_Command("set ngbehavior=ps")` before circ works. `ps`/`lt` apply only to `.include`d files;
    `psa`/`ltpsa` apply to the whole deck.
  - Without a compatibility mode, `limit()` silently returns 0, and `IF()`, `VSWITCH` and `TC=a,b` fail.
  - `./.spiceinit` in the cwd is executed at init.
- **Floating nodes under `uic`:** a capacitor-only node solves. An isolated transformer winding is a singular
  matrix, which a 1 GΩ leak resistor or `.option rshunt` fixes. So `NO-DC-PATH` matches ngspice for windings.
- **Model sources:** the only SPICE library on the machine is KiCad's `Simulation_SPICE.sp` (CC-BY-SA-4.0 file).
  Its generic op-amp is marked public domain inside the file. Re-author it rather than copy it.

### Changes to the downstream plan
- Task 2 must start with engine robustness (DESIGN.md 4.5): exit-callback reload, failure on `stderr Error` lines
  and empty grids, a pinned compat mode, and the current allowlist. Without these, a bad package can kill or
  silently corrupt every later run.
- XSPICE is available, so batch 8 (digital) is feasible as subckt-wrapped A devices. A devices are allowed in
  bodies only, not as top-level parts.
- Vendor PSpice macromodels are deferred: they need `.include` + `ngbehavior=ps` and a license check per file.
  Batch 7 should ship project-authored behavioural ICs.
- Batches must keep `app/tests/test_catalog.py:18` (`ALL_CATEGORIES`) in mind and must not edit the 17 shipped
  packages.

## Task 2: engine robustness + generalized device support (done, not committed)

### What changed (file:line)
- **Engine recovery (L7)**, `benchcore/src/ngspice.rs`:
  - `cb_exit` (:128) sets `EXITED` (:82).
  - `after_call` (:243) runs after every `do_circ` (:256) and `do_command` (:282). When the flag is set, it drops
    the `Lib` (dlclose), marks it un-inited and returns `Err("ngspice aborted and was reloaded: <ngspice error
    lines>")`. The next call reloads the library and runs `ngSpice_Init` again (spinit loads the XSPICE code models
    again). A reload costs about 1 ms.
- **Compat-mode pin (L13)**: `do_circ` sends `unset ngbehavior` right before every `ngSpice_Circ` (:273).
- **Logs**: `teardown` (:416) clears the log, so teardown chatter never reaches the next run's failure scan.
  `error_lines(max)` (:230) returns ngspice's stderr lines with the prefix stripped.
- **Fail loudly (L6, L12)**, `benchcore/src/kernel.rs`:
  - `scan_loud_failure` (:162) treats any `stderr Error...` line as a failure, plus these rc-0 parse messages:
    `unknown subckt`, `parameters for subcircuit type`, `instance line, ignored` (ngspice silently drops `R2 a`),
    and `unknown model type`. It strips the `stderr`/`stdout` prefix from the reported line.
  - A transient deck with an empty time grid is a `SolverFailure` (:~305).
  - `circ rc=N` / `run rc=N` messages carry ngspice's first error lines (`with_reason`, :213).
- **Rust device table**, `benchcore/src/validate.rs`:
  - The table section (:115-405) holds `Tail`, `Dc`, `DeviceSpec`, `DEVICES` (one row per letter: R C L K V I B E
    G F H D Q J M Z S W T O U Y P A X), `device_spec()`, `LEVELS` (probed per level, one process each),
    `FOREIGN_MODELS` (VSWITCH, ISWITCH, LPNP) and `node_cap`.
  - Helpers in the same section: `pspice_only_fn` (`if(`, `limit(`, `table(`), `xspice_nodes` (`[..]`, `~`,
    `%vd(..)`, NULL), `poly` (`POLY(n)`), `check_model` (defined, type, level, VDMOS 3/5+`thermal`, 3-node non-VDMOS
    M, node cap) and `dc_edges`.
  - `parse_netlist` (:496) dispatches on `spec.tail` (:623-799). `subckt_depth` (:561) replaces the `in_subckt`
    bool, which fixes L4.
  - DC adjacency comes from `dc_edges` (:1028). `dc_conductance` and the old Q/M/J/X special case are deleted.
  - Unit tests are at :1436.
- **Python device table**, `app/electrical.py`:
  - `Device(pins, models, current)` and `DEVICES` (:10-38) replace `DEVICE_LETTERS`, `PINS`, `CUR_PIN` and
    `CURRENT`. Letters: R C L V I B E G T (primitive), D S Q J M Z (model-backed).
  - `check()` resolves `spec` (:86) and parses the section's own cards (`cards`, :109). For model-backed letters,
    the field after the nodes must name one of them, of an allowed type and level (`_model_problems`, :164). An M
    part has 3 pins if and only if its card is VDMOS.
  - `current` must equal `spec.current` exactly (:146): T has none, and Z is `@{dev_lc}[cd]` (`[id]` can segfault).
  - New helper `model_type(text)` returns `(type, level|None|"?")`.
- **Docs**:
  - `docs/adding-components.md` has a "Device letters" table in place of "Multi-terminal parts".
  - `benchcore/docs/components.md` gains "Device table" and "Engine robustness" sections.
- **Test fixture**: `app/tests/test_catalog.py` `_multi` gave every letter an `NPN` card. It now uses a type the
  letter accepts (`MODEL_TYPE`); no assertion changed.

### Contracts for tasks 3 and 4
- **New finding code `UNSUPPORTED`** (error, layer `authoring`, the message carries the reason). It covers missing
  levels, PSpice model types, LAPLACE, `if/limit/table()` and N.
- **EXOTIC-DEVICE** (warn) now fires only for rows with `covered=false`: T O U Y P A. S, W and Z are fully covered.
  Task 4's `LIMITED-ANALYSIS` should replace it per row.
- **DC edges for task 4 flattening**: use the table, not letter matches. `Dc::Pair`: R L V D H S W U, E outputs,
  and B with `V=`. `PortPairs`: T O Y. `AllPairs`: Q J Z A P X. `NoGate`: M.
- **Engine behaviour**:
  - A bricking deck no longer poisons the process. It still fails the run, so task 4 must reject undefined `{param}`
    statically for good messages.
  - Any `stderr Error` line fails the run. If an XSPICE or library construct prints a benign `Error` line, scope
    the rule; do not delete it.
- **Compat mode is pinned off.** PSpice files need an explicit per-deck mechanism, which does not exist yet.
- **Task 3 (Python)**:
  - Add `"X"` to `DEVICES`, or handle it before the table lookup. Today `device = "X"` is still a `DEF-SIM` error
    (`test_catalog._dev_x`).
  - `model_type()` and `_model_problems()` can check def-file `.model` headers.
  - The current allowlist means X has no `current`.

### Tests (all green, wheel rebuilt and reinstalled per CLAUDE.md)
| Suite | Before | After |
|---|---|---|
| `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests` | 480 | **548** (+68, `app/tests/test_device_table.py`) |
| `cd benchcore && cargo test` | 35 | **48** (+5 lib unit tests in validate.rs, +8 in correctness.rs t60-t67) |
| `cd benchcore && python3 -m pytest -q tests/python` | 5 | **8** |

Real-ngspice coverage:
- t60/t61 and `test_poisoned_engine_recovers`: an undefined `{param}` and a LAPLACE deck, then valid runs, XSPICE
  included.
- `test_package_that_aborts_ngspice_does_not_break_later_runs`: the same through `Document`.
- t62/t63 and `test_unknown_subckt_is_an_error...`: unknown subckt, port mismatch and a short R are errors on both
  paths.
- `test_spiceinit_cannot_change_the_compat_mode`: a subprocess with `./.spiceinit` set to psa. The unpinned probe
  runs that deck (v = 1), and Bench refuses it.
- t64: 34 decks, at least one per letter, original letters included. Each passes the validator and runs to a
  hand value.
- t65: 36 malformed or unsupported forms.
- App: parity per letter with the Rust validator; per-letter load, current, pin-count, type and level checks; and
  end-to-end runs for S (on and off), E, G, B, T, Z (Statz) and VDMOS (square law at two Vgs).
- Golden netlists are unchanged (`test_template_netlist_matches_golden`).

### Deviations from DESIGN and new facts
- **M level 60 (SOI3) is refused.** With 4 or 5 nodes it aborts the whole process (`spbuild.c:267 spGetElement`
  assertion). With 6 nodes it does not crash, but the run fails. Level 4 (BSIM1) exists; it only fails to
  converge with default parameters.
- **D levels 4 and 5** run in ngspice but are not documented. They are refused; only 1 and 3 are accepted.
- **VDMOS with 4 nodes** is accepted silently by ngspice and gives a different answer, so it is MALFORMED.
  5 nodes require `thermal`, as ngspice says ("We need exactly 5 nodes").
- **`limit(5,0,1)` returns 5**, not 0 as DESIGN 3.1 said. Either way it is wrong, so it is UNSUPPORTED.
- **The exit callback always triggers a reload**, also for the classes DESIGN says recover on their own. This is
  simpler and costs about 1 ms.
- **O (LTRA)** hangs ngspice with lossy parameters (`R=0.1 L=1n C=1p len=1`, `.tran 1u`). Lossless LTRA runs. O
  stays body-only; batch authors should test their LTRA cards.
- **No new PyO3 API.** The parity test uses `Simulation.validate()`.
- **E/G `FREQ` forms are not modelled.** A `FREQ` line is read as linear, so it may be misread.

## Task 3: subcircuit-backed component packages (done, not committed)

### What changed (file:line)
- **`app/electrical.py`**:
  - `DEVICES["X"]` (:42): any pin count, no model types, no current.
  - New keys `KEYS` (:45); vocabularies `MODEL_KINDS`, `X_KINDS`, `VERIFICATION`, `PROVENANCE_KEYS`, `MANIFEST_KEYS`
    (:46-51); `MODELS_DIR = "_models"`; `STATUS_NOTES` (:53).
  - `Unit` (:57) and `parse_def` (:73): header-level parse of definition text (comments, `+` joins, depth-correct
    `.subckt`/`.ends`, directive allowlist, top-level `.subckt` ports and param defaults, top-level `.model` names,
    X references inside bodies).
  - `validate_bodies` (:130): **the task 4 hook** (see below).
  - `_read`, `manifest`, `manifest_problems` (:137-170): files and `MANIFEST.toml`, cached per catalog load.
  - `_units` (:173): resolves `defs` then `body`, checks paths, provenance records, duplicate names within a part
    and nested dependencies (an X in a body must name a `.subckt` in the same unit or an earlier `defs` entry; node
    count vs known ports).
  - `check(section, pins, params, where)` (:258): new 4th argument `where = {"dir", "root", "cache"}`. It checks X
    lines through `_instance_problems` (:389: subckt name, port count, `NAME=value` params declared on the `.subckt`).
    Model-backed letters may take their card from definition text. It validates `model_kind`, `verification` and
    `provenance`. When the section is valid, it stores `section["_units"]` (tuple of `Unit`) and `section["_status"]`.
  - `fixed_models` (:443): now returns every deck-global name: model names (card body, or `("unit", sha)`) and
    `("subckt", name)`.
  - `build_netlist` (:479): emits each unit once per deck (keyed by sha256), framed by
    `* bench-def <root-relative path> <sha8>`, after the part's `models`. Order is first use, and each part lists
    its deps first, so dependencies always come before their users. A name defined differently by two units, or by a
    unit and a rendered `models` line, raises `ModelError`.
- **`app/catalog.py`**:
  - `load_definition(path, root=None, cache=None)`; the domain validator gets `where` (:337).
  - The status note is appended to `limitations` (:340).
  - `reload` passes the root and one cache per load (:395, :409). The global-name conflict message covers `.subckt`.
- **Shared models**: `app/library/_models/MANIFEST.toml` and `app/library/_models/generic/opamp.lib`.
- **Reference packages** (all `project-subckt`, `verification = "analytic"`):
  - `electrical/opamp` (`OPAMP`): a shared `models:` file; `ROUT` is left to the subckt default.
  - `electrical/opamp_dual` (`OPAMP_DUAL`): an inline `body` that nests `bench_opamp` from the shared file twice.
  - `sensors/ntc_thermistor` (`NTC_THERMISTOR`): a package-local `ntc.lib`; R25, B and T params; a `provenance`
    table.
- **Rust**: no source change was needed. The validator already accepts top-level X (DEVICES row, port-count check,
  AllPairs DC). `correctness.rs` t68 pins the app-shaped deck: bench-def frames, a default vs an override, a nested X
  using a top-level card, internal nodes per instance, run to analytic values. The wheel was not rebuilt (no Rust
  source change).
- **Docs**: `docs/adding-components.md` gains the `[sim.electrical]` keys table rows, an X row, a "Subcircuit parts"
  section (Example 3 is an inline body, validated by `test_doc_examples_validate`; Example 4 shows file excerpts) and
  the DEF-SIM row. The README part list now has 20 parts.

### Package schema (`[sim.electrical]`, format 1; all new keys optional for non-X letters)
| key | type | required | rule |
|---|---|---|---|
| `device` | str | yes | `"X"` for subcircuit parts |
| `line` | str or `{select, <choice> = str}` | yes | `{dev} {pin}... <subckt> NAME={param} ...`; node field i = port i; count = port count; subckt name literal and defined at top level of this part's text; every `NAME` declared on that `.subckt` (case-insensitive); no spaces, no `params:` |
| `defs` | list[str] | X: `defs` or `body` | `"rel/path.lib"` (inside the package dir) or `"models:<path>"` (inside `<root>/_models/`); UTF-8; listed dependencies first |
| `body` | str (TOML multiline) | X: `defs` or `body` | literal ngspice text, emitted after all `defs` |
| `models` | list[str] | no | unchanged (`.model` lines, str.format templates) |
| `model_kind` | str | **X: yes** (one of `project-subckt`, `third-party-subckt`, `behavioral`, `equivalent`) | any letter: `primitive`, `model`, `third-party-model`, + the four |
| `verification` | str | no (default `none`) | `analytic`, `reference`, `smoke`, `none` |
| `provenance` | table of str | when `model_kind` is `third-party-*` and the text comes from the package (`body`, a local `defs` file or `models`) | keys `source`, `url`, `version`, `license` (required then), `attribution`, `retrieved`, `changes`, `notes` |
| `current` | - | not allowed for X | |
Allowed definition text: `*` comments, `;` / ` $ ` / ` // ` inline comments, `+` continuations, `.subckt`/`.ends`
(nested ones are local), `.model`; `.param`/`.func` only inside a body. Everything else is `DEF-SIM`. Names: top-level
`.subckt` and `.model` names are catalog-global (case-insensitive). The same unit (identical sha256) may be shared by
any number of packages; a different unit with the same name rejects the later package.

### Shared-model convention and provenance record (batches 5-9 must follow)
- Shared files live in `app/library/_models/<family>/<file>.lib` (for example `diodes/1n400x.lib`,
  `opamps/...`). Packages reference them as `models:<family>/<file>.lib`. An extra root has its own
  `<root>/_models/`; a package resolves `models:` only in the root it was found in. There is no `component.toml`
  there, so discovery ignores it.
- One record per file in `app/library/_models/MANIFEST.toml`:
  ```toml
  [[file]]
  path = "diodes/1n400x.lib"      # relative to _models/
  origin = "third-party"          # project | third-party
  source = "Vendor X application library, file 1n400x.lib"
  url = "https://..."             # required for third-party
  version = "rev 3"               # optional
  license = "LicenseRef-VendorX"  # SPDX id, or LicenseRef-<name> + _models/LICENSES/<name>.txt
  attribution = ""                # optional; text the license requires
  retrieved = "2026-10-10"        # optional
  sha256 = "<sha256 of the file as shipped>"
  changes = "renamed models with a bench_ prefix"   # optional
  ```
  The catalog rejects any package using a file whose record is missing or incomplete, or whose sha256 is stale.
  `test_every_shared_model_file_has_one_matching_provenance_record` fails on a file without a record or a
  `LicenseRef-` without its text.
- Fidelity: say what is not modelled in top-level `limitations`; record `model_kind` and `verification` honestly
  (`analytic` only with a closed-form test).

### Hook for task 4
`electrical.validate_bodies(units, section, pins, params) -> (status, [(message, field)])` (`app/electrical.py:130`).
- It is called from `check()` once the header-level checks pass, and only for parts with definition text.
- Returned messages become DEF-SIM errors, which reject the package.
- `status` is stored in `section["_status"]`. If `STATUS_NOTES` has an entry for it, the note is appended to the
  part's `limitations` (`catalog.py:340`).
- Today it returns `("not-body-validated", [])`, so every subckt part shows "Subcircuit body not yet checked by
  Bench's static validator".
- Task 4 should:
  - build the synthetic deck (DESIGN 4.6) from `units[*].lines` and the rendered instance line;
  - return `"full"` or `"limited"` and add notes for them;
  - drop `OPAQUE-X` for X instances whose definition is in the deck;
  - add Rust `UNDEFINED-SUBCKT`. Not added here: it changes `test_unknown_subckt_is_an_error_not_an_empty_success`
    (`tests/python/test_api.py`), which expects the runtime `SolverError`.

### Tests (all green)
| Suite | Before | After |
|---|---|---|
| `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests` | 548 | **595** (+44 `app/tests/test_subckt_packages.py`, +3 contract cases for the new packages) |
| `cd benchcore && cargo test` | 48 | **49** (t68) |
| `cd benchcore && python3 -m pytest -q tests/python` | 8 | **8** |

`test_subckt_packages.py` covers these, all with real ngspice runs where numbers matter:
- port order: an inverting amplifier, and a divider wired in both orders;
- three OPAMP instances: one `.subckt` in the deck, gains 2, 11 and 4 at `G/(1+G/A)`, and `v(xuN.n2)` and
  `@r.xuN.rout[i]` per instance;
- nesting: the dual followed by a single, with the shared file emitted first and `v(xu1.xa.n2)` ≠ `v(xu1.xb.n2)`;
- three-level nesting across two files and a shared `.model`, checked against Shockley;
- params: the subckt default vs a package override vs an instance override (exact currents); three NTC
  temperatures against the beta equation; an unknown instance param is rejected;
- 21 malformed or unresolved cases, manifest problems, conflicts across packages and at deck level, a model-backed D
  taking its card from a shared file, a cwd change, a save/load round trip, and Rust parity (only `OPAQUE-X`).

### Deviations
- **`nc` (pins not connected to the model) was not added** (YAGNI). A pin the model ignores gets a port tied
  inside the body. Add `nc` when a batch needs symbol pins with no port; `used` nets in `build_netlist` must then
  skip them.
- Tests that pinned the whole catalog were updated:
  - `test_catalog` search and simulated-set assertions are now filtered to their own fixtures or extended;
  - `test_library_ui` counts are now 20/13/2/2 and 15/13/0/1, and its empty-category case uses Microcontrollers,
    since Sensors now has a simulated part;
  - the `test_milestone_e2e` "temperature" search now finds DHT22 and NTC_THERMISTOR;
  - `test_components` maps nets for any pin count;
  - `test_device_table.test_examples_cover_the_table` excludes X, which is covered in the new file.
  - `ALL_CATEGORIES` is unchanged: the new parts use Electrical and Sensors.
- `_dev_x` in `test_catalog.INVALID` now expects field `sim.electrical.line` ("an X part needs ... defs or body").
- Missing `.model` references inside bodies are not checked at load. The engine fails the run (tested), and task 4
  owns the static check.

## Task 4: subcircuit-aware validator (done, not committed)

### The interrupted attempt
- It left nothing behind. `git diff` matched the task 2 and task 3 descriptions exactly. No source file was newer
  than this handoff's task 3 section (22:37). No grep hits for task 4 codes. No stash.
- The installed wheel was older than `validate.rs` (22:17 vs 22:18). Rebuilding it changed no test result.
- Baseline before any change: app 595, cargo 49, py 8, all green. There was nothing to repair or remove.

### Validator API and statuses
- **`benchcore/src/validate.rs`**:
  - `Finding` has a new `scope` field: `""` (top level), `".subckt <name>"` (a definition, reported once whatever
    the instance count; nested definitions are `outer/inner`), or an instance path such as `"x1.xa"`
    (connectivity and parameters, per instance).
  - `class_of(code)` (:54) returns one of six classes: `syntax`, `dependency`, `connectivity`, `unsupported`,
    `limited`, or `value` (the old numeric and expression checks).
  - PyO3 `validate()` (`lib.rs`) adds the dict keys `class` and `scope`.
  - New public Python `benchcore.validate(deck)` (`__init__.py:45`): the static checks without a simulation
    session. The catalog uses it because tests monkeypatch `benchcore.Simulation`.
- **Pipeline** (`validate()` :1858):
  1. `parse_deck` (:807) builds the definition tree. `logical_lines` (:557) drops comments before joining `+`
     continuations.
  2. `Flat::expand` (:1400) walks the instances from the top. For every definition it reaches, `analyse_def`
     (:945) runs once and `element()` (:1028) handles each line; params are resolved per instance.
  3. `topology()` (:1487) runs on the flattened elements. `b_checks` (:1689) and `values` (:1284) run per
     definition, on local names.
  - `b_exprs()` (TMAX policy) stays top-level only (L14 unchanged).
- **App hook** (`app/electrical.py`):
  - `probe_decks(section, pins, params, units)` (:128) builds one deck per `line` branch: `XU1` with default
    params, each pin on `pin_<id>` with `Rbench_leakN <net> 0 1g`, the rendered `models`, then every unit's lines.
  - `validate_bodies(units, section, pins, params) -> (status, errors, warnings, limits)` (:144).
    - Statuses: `full` (no error and no limited-class finding), `limited` (no error, limited findings),
      `failed` (errors).
    - Errors become `DEF-SIM` errors with message `CODE: message` and field `sim.electrical.line`
      (scope `""`), otherwise `body` or `defs`.
    - Other warnings (DANGLING ...) become `DEF-SIM` warnings, which keep the package.
  - `check()` stores `_status` and `_limits`.
  - `status_note(section)` (:162) replaces `STATUS_NOTES`. Only `limited` adds a line to `limitations`
    ("Static validation is limited; Bench's validator left these to ngspice: <each finding>"). `full` adds
    nothing.
  - Generated-deck validation needed no app change: every run deck already goes through `validate()` (the kernel
    gate and `sim.py` findings), and it now flattens the bodies.

### Diagnostic codes (new ones marked *)
| class | codes |
|---|---|
| syntax | MALFORMED (+ port named `0`/`gnd`, unnamed `.subckt`), DUP-NAME (+ per body, port listed twice, header param declared twice), SUBCKT-UNBALANCED* |
| dependency | UNDEFINED-MODEL (scope chain), UNDEFINED-SUBCKT*, UNDEFINED-PARAM* (also unknown functions), UNDEFINED-ELEMENT* (K/F/H/W target), UNKNOWN-INSTANCE-PARAM*, DUP-SUBCKT*, DUP-MODEL*, SUBCKT-RECURSION* |
| connectivity | NO-GROUND, NO-DC-PATH, ISRC-OPEN, V-LOOP, SHORTED-VSRC (errors); DANGLING, SHORTED-PASSIVE (warn) |
| unsupported | UNSUPPORTED (+ `if()`/`table()` in `.param`/`.func`), UNKNOWN-KIND |
| limited | LIMITED-ANALYSIS* (warn; replaces EXOTIC-DEVICE, which is gone), OPAQUE-X (only when the `.subckt` is absent and an `.include`/`.lib` may supply it) |
| value | BAD-VALUE (now skipped for `{}`/`''` values), NEG-R, NEG-CL, ZERO-R, ZERO-CL, B-* |

### ngspice 47 facts probed for this task (`probes/ng_probe.py`, one subprocess per deck)
- **Ground.** `gnd` (any case) is node 0, at top level and in bodies. A port named `0` or `gnd` silently leaves
  the outside net unconnected.
- **Scoping.**
  - Models and subckts scope lexically: body, then enclosing bodies, then top level, and a local name shadows
    the outer one. A card or `.subckt` of the instantiating body is not visible to a top-level definition it
    instantiates.
  - Params and `.func`s scope **dynamically**: a top-level definition reads its instantiator's params. Global
    `.param`s are visible everywhere, including header defaults and instance values.
- **Unreached bodies.** ngspice ignores uninstantiated bodies entirely (malformed lines, missing models,
  undefined params), so the validator analyses only reached definitions.
- **Silent misreadings.**
  - A port listed twice: the second is dropped.
  - A header param declared twice: the last wins.
  - A `.subckt` defined twice: the first wins, with a warning.
  - A `.model` defined twice: the first wins, silently.
  - An unknown instance param is ignored.
- **Fatal.**
  - K naming a missing inductor; F/H naming a missing source, or an R, L or I (V, E, H, B work).
  - Recursion: "unknown subckt" after about 22 levels.
  - An undefined identifier in `{}` or in a bare B expression, or an unknown function: aborts the circuit.
  - **K naming a resistor segfaults the process** (exit -11). K naming a V source is silently accepted.
- **Builtins.**
  - In `{}`: sqrt sqr sin cos tan asin acos atan arctan sinh cosh tanh asinh acosh atanh exp ln log log10 abs
    int nint floor ceil sgn agauss gauss aunif unif pow pwr max min ternary_fcn limit temper time hertz.
  - In B only: u u2 uramp ddt pwl pi e.
  - Not functions: fabs sign round db pwrs idt integ sdt tablef.
  - `FUNCS`/`CONSTS` take the union of both contexts, so a name valid in only one context passes statically and
    fails in the engine.
- **Floating nodes.**
  - Floating R islands and floating capacitor islands (also inside a body): singular matrix.
  - A capacitor-only node between driven nodes solves under `uic`; `NO-DC-PATH` still flags it (existing policy,
    stricter than ngspice).
  - An isolated winding inside a body: singular. With a 1 GΩ leak, or with its ports grounded outside: runs.
- **Includes.** A missing `.include` file aborts and reloads, and the validator lets it through. That is now the
  Python recovery test's trigger.

### Analysed vs opaque
- **Analysed** in every reached definition:
  - element arity per `DEVICES`, model type, level and node cap through the scope chain;
  - X: definition, port count, instance params;
  - every identifier in `{}`, `''`, B and E/G `VALUE=` expressions (skipping `v()`/`i()` arguments and `name=`
    keywords), per instance;
  - `.param`/`.func`/header defaults; duplicates; recursion;
  - K/F/H/W targets;
  - DC topology of the flattened circuit, where a B `I=` or a G that reads its own output voltage is a
    conductance.
- **Limited** (LIMITED-ANALYSIS, one per element and definition, naming it):
  - XSPICE A; T O U Y P;
  - B expressions; E/G `VALUE=`/`vol=`/`cur=`/`TABLE`;
  - a `.model` level that is not a literal number;
  - K/F/H/W references containing `.`;
  - any directive other than `.model`/`.param`/`.func`/`.subckt`/`.ends` inside a body;
  - flattening past 100 000 elements.
- **Not claimed:** the values of expressions (NEG-R etc. only on literal values), or electrical well-posedness.

### For library authors (tasks 5-9)
- **Reaching `full`.**
  - Bodies built from R C L, linear E F G H, `POLY`, K, S W, and D Q J M Z with literal levels.
  - Every param a body reads declared on its `.subckt`, or as `.param`/`.func` in that body or an enclosing one.
  - No `.model` level as an expression.
  - Every pin wired inside the body: an unused port gives a DANGLING `DEF-SIM` warning. Tie it with 1 GΩ to `0`.
- **Justifying `limited`.** Behavioural parts (B sources, E VALUE), XSPICE digital parts (batch 8) and
  transmission lines are `limited` by construction. That is acceptable; the note is generated automatically. Add
  a `limitations` line saying what the construct models, and set `verification` to `analytic` or `reference` with
  a test, since only ngspice checks those elements.
- **Reading statuses.** `CATALOG.get(id).sim["electrical"]["_status"]` and `["_limits"]`.
  `test_every_shipped_part_passes_the_static_validator` pins every shipped part at `full` and the catalog size at
  20. Batches that add parts must extend it, for example with a `limited` set.

### Tests
| Suite | Before | After |
|---|---|---|
| `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests` | 595 | **617** |
| `cd benchcore && cargo test` | 49 | **59** (lib 13 = 5 + 8 new, adder 3, correctness 43 = 41 + t69 + t70) |
| `cd benchcore && python3 -m pytest -q tests/python` | 8 | **9** |
- **Rust unit tests** (validate.rs): one per class (`syntax_class_in_bodies`, `dependency_class_follows_ngspice_scoping`,
  `connectivity_class_on_the_flattened_circuit`, `unsupported_class_in_bodies`,
  `limited_class_names_what_is_not_analysed`, `class_of_keeps_the_five_classes_apart`), plus
  `false_positive_guards` (16 decks: 0 and gnd in bodies, `.global`, a floating subckt, a differential input with
  no internal ground, a transformer through ports, comments and continuations, shadowing, sibling and local defs,
  dynamic params, B/G conductances, unreached junk, spaced params) and `expression_and_token_helpers`.
- **Real ngspice.**
  - t69: 8 accepted nested, multi-instance and guard decks simulate to hand values, including the transformer.
  - t70: 17 rejected decks, run raw. 11 fail in ngspice (named error line); 6 run and silently compute something
    other than the text says (unknown instance param, port `0`, duplicate port, redefined `.subckt`/`.model`, a
    param declared twice).
- **App** (`test_subckt_packages.py`, +22):
  - all shipped parts are `full` (the 14 simulated: 3 reference + 11 originals; the 5 unmodelled and GND have
    nothing to check);
  - a B-based part is `limited` with its note and still simulates exactly;
  - 5 legitimate bodies load as `full`;
  - 13 body defects reject the package;
  - a winding with a leak loads;
  - a transformer part whose secondary the user leaves floating is refused at run time with NO-DC-PATH on the
    user's nets, and runs once that secondary is grounded.
- Golden netlists are byte-identical. The wheel was rebuilt and reinstalled after the final Rust change.

### Deliberate test changes and deviations
- **`test_api.py`.**
  - `test_unknown_subckt_is_an_error_not_an_empty_success` now expects `InvalidCircuitError [UNDEFINED-SUBCKT]`
    (it expected the runtime `SolverError`). The same deck with an `.include` still reaches ngspice and fails
    with SolverError.
  - `test_poisoned_engine_recovers` now aborts the engine with a missing `.include`, because `{nope}` is refused
    statically.
- **Recovery tests.** t60 and t61 poison through the raw adapter (`poison_raw`).
  `test_package_that_aborts_ngspice...` keeps its assertions; the docstring now says the static gate catches it
  first.
- **t66.** It expects LIMITED-ANALYSIS for T. The L4 deck's `Cfloat zz yy` is now reported as internal
  `x1.zz`/`x1.yy`, which the old validator passed and ngspice fails (singular, probed).
- **Task 3 tests.** `test_unresolved_nested_dependency` (missing card) is now a load error, and the emitted
  op-amp deck has zero findings.
- **Generated decks are stricter.** They now get V-LOOP, SHORTED-VSRC and NO-DC-PATH through subckt ports. A V
  source driving a body inductor directly is V-LOOP, the same rule as at top level.
- **Not done.**
  - BAD-DIRECTIVE (DESIGN 4.6): odd directives in bodies are LIMITED, and top-level directives are the deck's
    business.
  - Expression values are not evaluated.
  - A finding inside a mixed `defs` + `body` part is attributed to the `body` field.
  - Top-level B and T parts now carry a LIMITED-ANALYSIS warning in every run's core findings.

## Task 5: library batch A (done, not committed)

### Packages added (37; catalog 20 -> 57 definitions; all `sim:electrical`, all with MIT provenance)
Kinds: project-subckt 15, equivalent 10, primitive 7, behavioral 4, model 1. Status: full 27, limited 2. The 8
non-X parts (7 primitives and VSW) have no stored status, since only X parts store one; T_LINE is reported limited by
the validator.

| family | package (id) | kind | status |
|---|---|---|---|
| passives | POT (3 pins, wiper `pos` 0-1), RHEOSTAT (2 pins), R_TOL (tolerance fraction) | project-subckt | full |
| passives | RNET4_ISO (8 pins, 4 isolated), RNET4_BUS (5 pins, common pin) | project-subckt | full |
| passives | IND_DCR (L + DCR), IND_COUPLED (L1, L2, K), XFMR_1N (LM, N, K), XFMR_CT (centre tap) | project-subckt | full |
| passives | CAP_CERAMIC, CAP_ELECTROLYTIC, CAP_TANTALUM, CAP_FILM (C, ESR, ESL, leak) | equivalent | full |
| passives | T_LINE (lossless T element, Z0, TD) | primitive | limited (ngspice T) |
| protection | FUSE (one-shot, time-delay), PTC (hold current, tripped resistance) | behavioral | full |
| protection | MOV (power-law clamp) | behavioral | limited (B) |
| switches | SPDT, DPST, DPDT (R contacts, state select) | project-subckt | full |
| switches | PUSH_NO, PUSH_NC (R element, pressed/released) | primitive | - |
| switches | RELAY_SPDT, RELAY_DPDT (coil R+L, pull-in/release current), REED (coil-driven contact) | equivalent | full |
| switches | VSW (S element, VT/VH), CSW (W element, IT/IH, 0 V sense) | model / project-subckt | - / full |
| sources | VSRC_EXP_SFFM (EXP, SFFM), ISRC_WAVE (PULSE, SIN) | primitive | - |
| sources | VCVS (E), VCCS (G) | primitive | - |
| sources | CCCS (F, 0 V sense), CCVS (H, 0 V sense) | project-subckt | full |
| sources | SAT_AMP (clamp of gain*V, B source) | behavioral | limited (B) |
| batteries | BAT_COIN, BAT_AA, BAT_LITHIUM (EMF + internal R) | equivalent | full |

- Shared model files (3, each with a MANIFEST record, `origin = project`, MIT): `_models/passives/cap_esr.lib`,
  `_models/protection/trip.lib`, `_models/power/cell_thevenin.lib`.
- 37 `symbol.svg` files (generic line art). The existing contract test requires a symbol for every simulated part.
- No new category: Electrical (subcategories Passives, Switches, Sources, Protection, Transmission lines) and Power
  (Batteries). `ALL_CATEGORIES` is unchanged.
- Four capacitor packages share one `.subckt` and differ in defaults; the three batteries share one too. Merge them
  later if they look like duplicates.

### Existing tests whose pinned counts changed (no assertion weakened)
- `test_catalog.py`: Power = BAT + 3 batteries; Electrical simulated set + the 34 new Electrical parts.
- `test_library_ui.py`: totals 57 / Electrical 47 / Microcontrollers 2 / Sensors 2; simulated-only 52 / 47 / 0 / 1.
- `test_subckt_packages.py`: 51 simulated, 57 total. MOV and SAT_AMP are `limited` (B source). T_LINE validates as
  limited (ngspice T). `_status` is stored for X parts only.
- `test_components.py` gains one contract row per new simulated part (no edit).

### Tests
- New: `app/tests/test_library_batch_a.py`: **106 passed**.
  - 37 parametrized load/validate/kind/provenance checks, plus the catalog count and a shared-unit-emitted-once check.
  - Real-ngspice families, each checked against a closed form:
    - pot wiper divider (4 positions) and two pots on one rail (two instances);
    - rheostat, isolated and bussed networks, tolerance corners;
    - DCR RL step (value and tau); coupled inductors, 1:N and centre-tap transformers (sample by sample, open secondary);
    - ESR/leak RC time constant for all four capacitors (internal node `xc1.n2`);
    - matched lossless line (0, then 1 V after TD);
    - fuse trip time `tau*ln(I/(I-rating))` (+-5 us) and latched; fuse below rating never opens; PTC trip delay and
      hold at `V*R/(Rtrip+R)`;
    - MOV power law (30 V, 36 V, -36 V);
    - switch states (SPDT, DPST, DPDT, both pushbuttons); relay pull-in at 3 V (30 mA x 100 ohm), SPDT and DPDT;
      reed at 2 V; VSW and CSW thresholds;
    - EXP and SFFM waveforms, pulse and sine current, VCVS, VCCS, CCCS, CCVS gains, clipping amplifier;
    - cell terminal voltage under load (three cells), and two AA cells in series (two instances).
- Full suite (`QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests`): **760 passed** (task 4 baseline 617 =
  617 + 106 new + 37 contract rows). Build validation: `python3 -m app.catalog` 57 definitions, 0 errors, 0 warnings.
- No Rust change, so no wheel rebuild and no `cargo test` run.

### Engine facts measured (for later batches)
- S and CSW switch when the control passes VT+VH (ON) and VT-VH (OFF). Measured edges: 1.4 and 0.6 for VT=1, VH=0.4.
- A one-shot latch needs hold 0 and a -1 mV initial charge on the proxy capacitor. At 0 V the S needs strictly more
  than VT+VH to start closed, so without the charge the fuse starts open. The proxy only decays towards zero, so it
  never closes again.
- A source directly across a body inductor is `V-LOOP` (correctly refused). Windings need a series resistance, and
  the test measures the primary terminal, not the source.
- Transient samples are not exactly on the requested grid: at a steep point (1 V/ms) an EXP source read up to about
  2 us late at tstep 1 us. Check flat points.

### Infrastructure gap (not fixed; task says not to change infrastructure)
- An unwired negative pin on a voltage source is accepted and runs. Example: V1 to R to GND with V1.n unconnected
  gives `ok` (`nets: V1.n = n2`, floating). The validator's NO-DC-PATH check does not flag a net touched only by a
  source. Suggest a follow-up: treat a source-only net as a floating net at the top level.

### Skipped (and why)
- SPST: the existing `SW` part is SPST, so no duplicate.
- PWL source: needs a list-valued parameter, and params support only number and choice.
- AC source: no effect in a `.tran` run; needs `.ac`.
- Exponential and SFFM current sources: only PULSE and SIN were added to `ISRC_WAVE`. The syntax is the same as
  `VSRC_EXP_SFFM`; add when a circuit needs it.
- Current sink: an `I` with its pins reversed; no new package. A compliance-limited source needs a conditional B
  source, which was not built.
- Lossy transmission line (O element): ngspice hangs on lossy LTRA cards in this build.
- Resistor power rating: ngspice does not limit dissipation, so a rating parameter would have no effect.
- Fuse and PTC: thermal behaviour is a current-and-delay proxy, not self-heating; the fuse is one-shot.
- Batteries: no state of charge, discharge curve, temperature or ageing (labelled approximate).
- Dielectric absorption (film), electrolytic polarity and reverse bias, MOV energy and ageing, and reed bounce or
  magnet fields: not modelled, and the limitations say so.
- Maintained (latching) pushbutton: not in batch A.

## Task 6: library batch B, semiconductors (done, not committed)

### Packages added (35; catalog 57 -> 92 definitions; all `sim:electrical`, all MIT)
Kinds: model 27, project-subckt 2, equivalent 6. Verification: reference 22, analytic 9, smoke 4. Every X part validates
as `full` (no limited findings); the 27 `model` parts are single D/Q/J/M elements. Category Electrical, new subcategories
Diodes, Bipolar transistors, MOSFETs, JFETs, Thyristors and IGBT (the existing "Semiconductors" search is unchanged).
`ALL_CATEGORIES` is unchanged.

| family | packages (id) | element | kind / verification |
|---|---|---|---|
| diodes | D_GENERIC (Is, N, Rs, Cjo, Vj, M, Tt, BV, IBV) | D | model / analytic |
| diodes | D_1N400X (BV param), D_UF4007, D_1N5819, D_BAT54 | D | model / reference |
| diodes | ZENER (VZ, IZT, ZZ) | X | project-subckt / reference |
| diodes | TVS_UNI, TVS_BI (VRWM, VC, IPP) | X | equivalent / analytic |
| diodes | VARACTOR (Cjo, Vj, M, Rs, BV), LED_COLOR (ir, red, yellow, green, blue, white) | D | model / analytic |
| BJT | Q_NPN, Q_PNP (generic) | Q | model / analytic |
| BJT | Q_2N3904, Q_2N3906, Q_BC547B, Q_BC557B, Q_TIP31C, Q_TIP32C, Q_2N3055 | Q | model / reference |
| BJT | Q_TIP120 (Darlington with the internal 8 k / 120 ohm resistors and damper diode) | X | project-subckt / reference |
| MOSFET | NMOS, PMOS (generic level 1, 4 pins, W/L params) | M | model / analytic |
| MOSFET | M_2N7000, M_BS170, M_BSS138, M_IRF540N, M_IRF9540N, M_IRLZ44N (VDMOS, 3 nodes) | M | model / reference |
| JFET | J_J201, J_2N5457, J_2N5460 | J | model / reference |
| thyristor | SCR, TRIAC, DIAC (VBO), IGBT (VGETH, ISAT, RON) | X | equivalent / smoke |

- Shared model files (2, `origin = project`, MIT, sha256 in MANIFEST): `_models/semiconductors/led.lib` (one D model per colour)
  and `_models/semiconductors/scr.lib` (`bench_scr`, shared by SCR, TRIAC and DIAC; TRIAC and DIAC add their own `body`).
- 35 `symbol.svg` files. The named cards are inline `models` in each `component.toml`.
- Not added, with reasons: 1N914 (same datasheet as the 1N4148 that exists), maintained-switch style duplicates, level-3 generic
  MOSFETs (many short-channel parameters nobody could verify), phototransistor and photodiode (batch 9, G18).

### Model data and licensing (read this before trusting a named part)
No vendor SPICE card was copied. I could not verify redistribution terms for any manufacturer, LTspice or OrCad card
(onsemi blocked downloads; Fairchild, Diodes, Nexperia, Vishay and Infineon model terms were not found or not permissive), so every
named part is a **project-authored parameter set fitted to the datasheet values** listed in its `provenance.notes`, with the exact
datasheet URL and revision in `provenance.url` / `provenance.version` and "not a vendor SPICE model" in its limitations.
Assumptions that are not in the datasheet (Early voltage 100 V, JFET lambda 0.01, Ikf, Tf where fT is only a minimum) are listed in
each package's limitations. The ngspice examples (Modified BSD) have no 2N/BC/TIP/1N cards; the VDMOS examples there (IRFP240, IRF7233)
are not the requested parts and were not used.

| part(s) | datasheet read (URL in provenance) | license of shipped card |
|---|---|---|
| D_1N400X | Vishay 88503 rev 29-Apr-2020 | MIT (project fit) |
| D_1N5819 | Vishay 88525 rev 01-Jul-2020 | MIT |
| D_UF4007 | Vishay 88755 rev 06-Apr-2020 | MIT |
| D_BAT54 | STMicroelectronics BAT54 (copy at CERN) | MIT |
| ZENER | Vishay 85816 rev 2.7 (1N4728A-1N4761A table) | MIT |
| Q_2N3904 / Q_2N3906 | onsemi 2N3904 rev 9 (UCSB copy) / ST 2N3906 | MIT |
| Q_BC547B / Q_BC557B | Philips BC546-548 (2004) / Fairchild BC556-560 | MIT |
| Q_TIP31C / Q_TIP32C / Q_TIP120 | Fairchild TIP31, TIP32 (rev A 2000), TIP120 (rev A1 2001) | MIT |
| Q_2N3055 | Multicomp 2N3055 (2012) | MIT |
| M_2N7000 / M_BS170 / M_BSS138 | Zetex issue 2 / onsemi BS170 rev 5 / Diodes DS30144 rev 25-2 | MIT |
| M_IRF540N / M_IRF9540N / M_IRLZ44N | Infineon datasheets, rev 2.1 (2026) | MIT |
| J_J201 / J_2N5457 / J_2N5460 | Central J201 / Fairchild 2N5457-9 rev G / Central 2N5460-62 | MIT |
| TVS_*, SCR, TRIAC, DIAC, IGBT, VARACTOR, LED_COLOR, generics | none (textbook structures; ngspice docs URL only) | MIT |
The TVS default (SMAJ12A class: 12 V, 19.9 V at 20.1 A) and the DIAC default (DB3 class, 32 V) come from the general part class, not from a
datasheet I read; the limitations say so. The LED colours are typical Vf ranges per technology, not a part.

### Design notes and engine facts found while fitting
- **Fits were done against datasheet numbers, then tested** (`verification = reference`): hFE at the datasheet currents, VCE(sat), VBE,
  IDSS, Vth (250 uA or 1 mA), Rds(on), gate charge (simulated: IRF540N about 60 nC vs 71 nC max, IRF9540N 82 vs 97, IRLZ44N 41 vs 48), forward
  voltage, UF4007 trr in the JEDEC test (about 55 ns vs 75 ns max). A pass means "inside the window the fit targeted", not that the part is reproduced.
- **VDMOS**: 3 nodes only, `ksubthres` default 0.1 is on. `Cgdmax` was calibrated by simulated gate charge. `bv`/`ibv` follow V(BR)DSS at the
  datasheet test current. The gate has no DC path (a current source alone into the gate is `NO-DC-PATH`).
- **D breakdown (G14)**: ngspice raises `IBV` to `Is*BV/Vt` if the card asks for less, so rectifier/Schottky cards cannot state leakage at the rating.
- **PJF sign (G16)**: ngspice wants a negative `Vto` for PJF as well (and negative for PMOS); `Vto=+2.5` makes a dead device.
- **Latching subcircuits (G15)**: `Tf=Tr=1u` plus floating internal nodes ran away to 1000 V after turn-off; `Tf=Tr=0` fails to converge. `bench_scr` uses
  `Tf=Tr=10n`, a 100 Meg leak `RLK` across the anode and a series anode diode (reverse blocking). Hold current IH sets `RXA = 0.65/IH` (measured
  5.4 mA for IH = 5 mA); trigger current is about 0.6 V/RGK + 0.1 mA (0.3 mA default). On-state about 1.5 V at 0.1 A, 2.2 V at 5 A.
- **TRIAC** triggers in quadrants I and III only (positive gate current fires MT2+, negative gate current is copied by an F source into the second SCR).
  **DIAC** is two SCRs with a series diode + zener from the anode to the gate (breakover = VBO, then about 2 V on-state). **IGBT** is a VDMOS driving a PNP
  (`ISAT` sets the VDMOS Kp; on-state 0.9 V at 1 A and 2.6 V at 20 A for the defaults).
- The validator reports `full` for all of these (the F-source gate network and the VDMOS in a body are analysed).

### Tests
- New `app/tests/test_library_batch_b.py`: 130 tests, real ngspice through `Document` -> `run_document`:
  - load/kind/provenance/status for all 35, catalog size, shared files emitted once;
  - forward voltage windows (1N400X, UF4007, 1N5819, BAT54 at the datasheet currents) and Shockley for the generic diode, breakdown at BV, leakage;
  - UF4007 trr in the Vishay test circuit; Zener VZ and ZZ for four rows of the Vishay table, regulation, forward drop, three Zeners in series (one shared `.subckt`);
  - TVS breakdown at 1.11 x VRWM and clamp at VC/IPP both polarities, surge clamp; varactor C(V) from a ramp; six LED colours at 20 mA;
  - BJT hFE windows (18 points), VCE(sat) / VBE(sat) (11 points), generic beta and Early voltage, TIP120 gain and saturation, two TIP120 instances on one rail;
  - generic MOSFET square law (N and P), threshold and Rds(on) windows for the six VDMOS parts (9 points), IRF540N gate charge;
  - JFET IDSS window, pinch-off and half-pinch-off square law (3 parts);
  - SCR latch, hold (2 mA drops out, 12 mA holds, IH = 2 m and 20 m), weak gate, reverse blocking; TRIAC both half cycles (and no firing without a gate);
    DIAC breakover 32 V and 40 V in both polarities and no conduction below; IGBT on-state, threshold and off-state.
- Existing tests whose pinned counts changed (no assertion weakened): see below.
  - `test_catalog.py`: the Electrical simulated set now includes batch B (imported from `test_library_batch_b.NEW`).
  - `test_library_batch_a.py`: "only additions" is now `>=` (batch B adds more); batch B pins the exact size (92).
  - `test_library_ui.py`: nav counts 92 / 82 / 2 / 2, simulated-only 87 / 82 / 0 / 1.
  - `test_milestone_e2e.py`: the "diode" search returns the originals plus the batch B diodes, all Electrical.
  - `test_subckt_packages.py`: 86 simulated parts, 92 definitions; every new part must be `full`.
  - `test_components.py` gained one contract row per new simulated part (no edit).
- Full suite: `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests` is **925 passed** (760 + 130 new + 35 contract rows). Catalog build:
  `python3 -m app.catalog` reports 92 definitions, 0 errors, 0 warnings. No Rust change (no wheel rebuild, no cargo run).

### Skipped (and why)
- Vendor SPICE cards (all): redistribution terms could not be verified (G19). No part is attributed to a manufacturer's model.
- 1N914 (its datasheet equals the 1N4148 already shipped), 1N5817/1N5818 (differ from the 1N5819 only by rating and Vf; the description says so),
  Zener series parts at fixed voltages (one parameterised ZENER covers them; the tests use four rows of the Vishay table).
- Generic level-3 MOSFET, generic JFET, bidirectional-gate-quadrant II/IV TRIAC triggering, IGBT with co-packaged diode, SCR forward breakover and dV/dt.
- Phototransistor and photodiode: batch 9 (G18).
- Reverse recovery of the power MOSFET body diodes and the BAT54 (below the model's resolution or not in the card).
- AC analysis of anything (G17).
- Temperature dependence, noise and thermal limits everywhere.

## Task 7: library batch C, analog ICs (done, not committed)

### Packages added (25; catalog 92 -> 117 definitions; all `sim:electrical`, all X parts, all MIT, all status `full`)
Kinds: behavioral 23, project-subckt 2 (INAMP_3OA, ULN2003). Verification: analytic 5 (OPAMP_MACRO, INAMP_3OA, COMPARATOR,
NE555, NE556), reference 20. Every body is built from R, C, V, I, D, Q, S and linear E/F/G/H/POLY sources, so the validator
analyses all of it (no B sources, no `limited` part). Category Electrical (as OPAMP/OPAMP_DUAL); new subcategories Comparators,
Timers, Voltage references, Regulators, Analog switches, Audio amplifiers, Drivers (op-amps stay in "Analog ICs").
`ALL_CATEGORIES` is unchanged.

| family | packages (id) | structure |
|---|---|---|
| op-amps | OPAMP_MACRO (generic: AOL, GBW, SR, VOS, IB, ROUT, IQ, VDROPH/L) | `bench_opamp_macro`: clamped G stage, integrator (SR exact), rail clamps, buffer |
| op-amps | LM358, TL072, MCP6002 (duals), LM324 (quad, nests the macro 4x), LM741 | named fits of `bench_opamp_macro` |
| in-amp | INAMP_3OA (gain 1 + 50k/RG) | three generic macros + matched resistors (no commercial fit) |
| comparators | COMPARATOR (generic, open collector), LM393 (dual), LM311 (separate emitter pin, 15 ohm, 140 ns) | `bench_comparator`: clamped G stage, RC delay, S output switch |
| timers | NE555, NE556 | `bench_ne555`: 3x5k divider, S-switch comparators and latch, fitted output/discharge |
| references | TL431, LM4040 (2.048-10 V choice) | `bench_shunt`: ideal Vref + SLOPE*VKA + ZK*IK, error amp, NPN sink |
| regulators | REG_78XX (5/8/10/12/15/24 V choice), REG_79XX (-5/-8/-12/-15), LM317, LM337, AMS1117 (1.5-5.0 V choice) | `bench_linreg_pos/neg`: Darlington pass, Qcl current limit, single-pole error amp |
| switches | CD4066 (quad), CD4051 (8:1), CD4052 (dual 4:1), CD4053 (triple 2:1) | `bench_asw_hi/lo`: S switch + constant RON, (VDD+VSS)/2 threshold; muxes are switch trees (RON split per level) |
| audio / driver | LM386 (gain 20/200 via pins 1-8), ULN2003 (7 Darlingtons, 2.7k/7.2k/3k, clamp diodes) | LM386: datasheet 150/1.35k/15k network; ULN2003: transistor level, inline nested subckt |

- Shared files (6, `origin = project`, MIT, MANIFEST records with sha256): `_models/analog/opamp_macro.lib`, `comparator.lib`,
  `ne555.lib`, `shunt_ref.lib`, `linreg.lib`, `aswitch.lib`. One parameterized 78xx and one 79xx package instead of per-voltage
  packages (dropout, bias and current limit do not differ meaningfully across the voltage options in the datasheets).
- 25 `symbol.svg` files (op-amp triangle for singles/comparators, zener-style for TL431/LM4040, pinned boxes for the rest). Pin
  descriptions carry the DIP/TO-220 pin numbers.

### Datasheets (all fetched and read; URL + literature number/revision in each `provenance`)
| part | datasheet |
|---|---|
| LM358 / LM324 / LM741 / TL072 | TI SLOS068AB (Oct 2024) / SLOS066AE (Sep 2025) / SNOSC25D (Oct 2015) / SLOS080W (Jul 2025) |
| MCP6002 | Microchip DS20001733L |
| LM393 / LM311 | TI SLCS005AH (Apr 2025) / SLCS007K (Mar 2017) |
| NE555 / NE556 | TI SLFS022K (Mar 2026) / SLFS023I (Mar 2025) |
| TL431 / LM4040 | TI SLVS543S (May 2024) / SNOS633N (Aug 2025) |
| uA78xx / uA79xx / LM317 / LM337 | TI SLVS056P (Jan 2015) / SLVS058H (Nov 2006) / SLVS044Z (Apr 2025) / SLVS047L (Jan 2015) |
| AMS1117 | Advanced Monolithic Systems ds1117.pdf (no revision printed) |
| CD4066B / CD4051B-53B / LM386 / ULN2003A | TI SCHS051J (Aug 2024) / SCHS047O (May 2026) / SNAS545D (Aug 2023) / SLRS027T (Mar 2025) |
Every named part's first limitation reads "Behavioral fit to datasheet typicals, not a vendor macromodel." (ULN2003: "not a
vendor SPICE model"). No vendor macromodel was used (G10: needs ngbehavior=ps; G19: no redistribution terms verified).
Values that are assumptions (not in the datasheet) are listed in each package's limitations/notes: LM358/LM324 ROUT 50 ohm,
LM741 GBW from the 0.3 us rise time, MCP6002 VOS 0, LM311 large-overdrive delay, LM317/LM337 dropout, LM337 ADJ-current direction.

### Modelling notes and engine facts found
- **Op-amp**: SR = G2 x 10 V / 1 nF exactly (stage 1 clamped at +-10 V); GBW from G1*R1*G2/(2 pi C2). Rail clamp sources are widened
  by the clamp diode's drop at the full integrator current (`{VDROPx + N*Vt*ln(SR*1e-9/Is)}`), so a saturated output sits exactly
  VDROP inside the rail. Output current comes from node 0, not the supply pins (limitation). **G20**: without the extra stage-1
  pole (100 x GBW) the model rang numerically at tstep near 1/GBW and returned a wrong "success".
- **Offset sign**: VOS is added to the + input (`Vos ip inp`), so the output offset is +VOS x noise gain.
- **Regulators**: the error amplifier and base buffer are referenced to node 0. Referencing them to `ret` (= OUT for LM317) made the
  Darlington a transconductor in the loop and oscillated with an output cap. A supply step overshoots up to about 0.6 V into large
  output caps (limitation). Dropout fit: VHD 0.65 (78xx, 2 V at 1 A), -0.4 (79xx, 1.1 V), -0.5 (AMS1117, about 1.3 V floor at 0.8 A,
  datasheet max; 1.1 typ not reachable with a Darlington pass).
- **Shunt reference**: a slow internal pole (0.8 nF) left 2 mV of unsettled error at 100-200 us; 10 pF settles and stays stable
  with 0-100 uF cathode caps.
- **555**: latch priorities come from path resistances (RESET 1 ohm > set 10 > clear 100 > hold 1k). A subckt port named `gnd` is
  node 0 (validator MALFORMED), so ports use `gn`. RESET has only a 1 G leak: a floating RESET holds the timer reset (limitation).
- The S-switch comparators have 1 mV hysteresis; astable timing matches 1.44/((RA+2RB)C) within 3 % only when RA >> discharge
  RON (19 ohm, fitted to 0.15 V at 8 mA): with RA = 1k the real-part effect lengthens the period about 2 %.

### Tests
- New `app/tests/test_library_batch_c.py`: **91 tests**, real ngspice through `Document` -> `run_document`:
  load/kind/provenance/`full` status for all 25, catalog size, shared file emitted once; closed-loop gain G/(1+G/A) (6 gains); VOS and
  IB closed forms; slew-limited steps (generic x2, TL072 20 V/us, LM741, LM358 0.3, MCP6002 0.6); unity-buffer 10-90 % rise =
  ln9/(2 pi GBW) (2 GBWs); rail saturation; LM358/TL072/LM741/MCP6002 swing rows; LM324 quad with four gains (multi-instance) and
  supply current; in-amp gain 1+50k/RG (3 RG); comparator threshold at VOS, Schmitt hysteresis with feedback (closed form),
  LM393 1.3 us / 0.3 us response and VOL, LM311 at 50 mA and 140 ns; 555 astable (3 cases) and monostable 1.1RC (2), output levels
  (5 datasheet rows), CONT override and RESET, two 555s cascaded (astable triggering monostable), 556 two astables; TL431 Vref,
  ZKA, divider formula, off state; LM4040 (3 voltages); 78xx regulation/bias/dropout/current limit (5 V and 12 V), 79xx, LM317
  (3 divider settings) and minimum load, LM337 (2), AMS1117; CD4066 on/off resistance and RON at 15 V, CD4051 all 8 addresses and
  INH, CD4052/CD4053 selection; LM386 gain 20/200/resistor-set and 325 mW into 8 ohm; ULN2003 sinking loads, input current, clamp
  diode catching an inductive kick.
- Existing tests whose pinned counts changed (no assertion weakened):
  - `test_catalog.py`: Electrical simulated set now includes `BATCH_C` (imported from the new file).
  - `test_library_batch_b.py`: exact size 92 -> `>=` (batch C pins the exact 117).
  - `test_library_ui.py`: nav counts 117 / 107 / 2 / 2, simulated-only 112 / 107 / 0 / 1.
  - `test_subckt_packages.py`: 111 simulated parts, 117 definitions; all new parts `full`.
  - `test_components.py` gained one contract row per new simulated part (no edit).
  - `test_milestone_e2e.py` unchanged: the LM393/LM311 descriptions were reworded ("switching delay") because "esp" matched
    "response" (G23).
- Full suite `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests`: **1041 passed** (925 + 91 new + 25 contract rows).
  `python3 -m app.catalog`: 117 definitions, 0 errors, 0 warnings. No Rust change (no wheel rebuild, no cargo run).

### Skipped (and why)
- Switching regulators: no verifiable averaged/switch-level model (G21). Vendor PSpice macromodels (G10, G19).
- Separate per-voltage 78xx/79xx/AMS1117 packages (one parameterized package each), LM2904/LM358B/TL074 variants (same fits or
  different designs; aliases only where the datasheet is shared), AMS1117-ADJ (LM317 covers the adjustable structure).
- Supply-dependent CD4066/405x RON (constant RON param, datasheet values in the help text), output current limits of op-amps and
  comparators, foldback and thermal limits of regulators, LM311 strobe/balance and LM741 offset null pins (G22), AC behaviour (G17).

## Task 8: library batch D, digital and mixed-signal ICs (done, not committed)

### Packages added (31; catalog 117 -> 148 definitions; all `sim:electrical`, all X parts, all MIT)
Kinds: behavioral 30, project-subckt 1 (DAC_8BIT). Verification: reference 16 (gates, Schmitt, buffers: datasheet levels, drive and
delay windows plus exact truth tables), analytic 15 (flip-flops, counters, shift registers, muxes, converters: exact function against
state sequences and closed forms). Status: 30 `limited` (XSPICE `A` devices and B sources, named in each part's generated note), the
R-2R DAC `full`. Category Electrical, new subcategories Logic gates, Flip-flops and latches, Counters and shift registers,
Multiplexers, Data converters. `ALL_CATEGORIES` is unchanged. Package ids start with a letter, so TI 74xx parts are `SN74HCxx`
(aliases `74HCxx`, `HCxx`).

| family | packages (id) |
|---|---|
| gates | SN74HC00, 02, 04, 08, 32, 86; SN74LS00 (TTL levels differ, so a separate package); CD4001, CD4011, CD4069 (UB), CD4070, CD4081 |
| Schmitt | SN74HC14, CD40106 (hysteresis from a switch on the supply-normalised input) |
| buffers | SN74HC125 (quad), SN74HC244 (octal, two enables), tri-state outputs |
| flip-flops, latches | SN74HC74 (dual D), CD4013 (dual D), SN74HC107 (dual JK, falling edge), CD4027 (dual JK), SN74HC573 (octal latch, tri-state) |
| counters, shift registers | SN74HC161 (sync 4-bit), CD4017 (decade Johnson), CD4040 (12-stage ripple), SN74HC393 (dual 4-bit ripple), SN74HC595, CD4094 |
| muxes | SN74HC151 (8:1), SN74HC157 (quad 2:1) |
| converters | ADC_8BIT (generic, REF pin, sampling clock), DAC_8BIT (generic R-2R ladder) |

Shared files (5, `origin = project`, MIT, MANIFEST records with sha256): `_models/digital/io.lib` (stages), `gates.lib`, `seq.lib`,
`mux.lib`, `conv.lib`. 31 `symbol.svg`. Pin descriptions carry DIP pin numbers from the datasheets (all pinouts read from TI/Nexperia
PDFs; the 74HC107 from Nexperia Rev. 7, since TI publishes no file under that name). Revisions are in each `provenance.version`.

### Approach chosen, and why (probed with `probes/ng_probe.py` before building)
- **(a) for everything sequential, complex or multi-input**: XSPICE `d_nand/d_nor/d_and/d_or/d_xor/d_inverter/d_buffer/d_dff/d_jkff/
  d_dlatch` inside subckts, between analog bridges. Exact logic with a few elements per function, and the counters, shift registers
  and muxes would be hundreds of transistors in (b). The probes confirmed that all of these load through Bench's init and run inside
  subckts.
- **(b) was not used for any logic part**: it could reach `full`, but transistor-level gates have no meaningful way to reproduce the
  74HC/74LS/CD4000 levels with the engine's tmax = tstep and the datasheet drive figures would still need an extra buffer stage.
  The one place (b) fits is the DAC: resistors and switches, so DAC_8BIT is `full` and `analytic`.
- **Supply pins are real.** Bridges cannot take thresholds from pins, but a B source can: `bench_din` normalises the pin to
  `(V(a)-V(gn)) / (RK*(V(vc)-V(gn)) + AK)` before an `adc_bridge`, so thresholds are fractions of the supply (CMOS, `RK=1`) or volts
  above GND (TTL, `RK=0`). The output stage is a `dac_bridge` (0 or 1) scaled by a POLY source to `gn + (VOLF + (VOHF-VOLF)*d)*(vc-gn)` with
  `ROUT` in series. Outputs therefore swing to a 3.3 V, 5 V, 10 V or 15 V supply (tested). Supply current and output current from VCC
  are NOT modelled (ideal sources). HC and CD gates switch at 0.5 x supply with a 0.45-0.55 unknown band (the guaranteed 0.3/0.7 sit
  inside it, and the tests check them); LS switches at 1.2-1.4 V above GND; 74LS outputs 0.25/3.4 V with 15 ohm; CD4000 outputs 400 ohm
  default.
- **Hi-Z**: `dac_bridge` has no high-impedance state (`d_tristate` through it holds the last level), so tri-state outputs use a
  separate enable dac and a switch (1 GOhm leak to GND when off).
- **Schmitt**: adc_bridge cannot do hysteresis (it outputs unknown between its thresholds), so the stage is a 1 ohm / 1 kohm `S`
  switch with `VT +- VH` on the normalised input.
- **Defaults for unconnected pins** (documented in every limitation): data and clock low; clear, preset, set, reset and load inactive;
  enable and OE inputs enabled.

### ngspice/XSPICE facts found (for later work)
- `d_dff`, `d_jkff`, `d_dlatch`, `d_tristate`, `d_srlatch` take scalar nodes (`A1 d clk set reset q nq model`; NULL is allowed for set,
  reset and unused outputs); `d_nand` etc. and the bridges take `[ ]` vectors; `d_buffer` and `d_inverter` are scalar in this build.
- `in_low = in_high` is accepted by `adc_bridge`; between two different thresholds the output is unknown (it does not hold).
- Model parameters accept `{param}` expressions inside subckts, including POLY coefficients and SW `VT`/`VH`.
- **Power-up edge (G24)**: digital nodes start low; a clock path that is high at t = 0 clocks every edge-triggered model once. Analog-domain
  inversion of a clock counted once at start in the first CD4040 draft; the fix is digital-domain inversion (`d_inverter`).
- `d_dff` ripple chains (`D = nQ`, clock = nQ of the previous stage) give a true ripple delay of one TPD per stage.
- CD4017 reproduces the datasheet quirk that releasing CLOCK INHIBIT while CLOCK is high advances the count (the test pins it).
- A B source reading `V(vc)` does not count as a use of the supply pin for DANGLING, but the POLY output source does.

### Tests
- New `app/tests/test_library_batch_d.py`: **112 tests**, real ngspice through `Document` -> `run_document`:
  - load/kind/provenance/status for all 31 (limited note names the A devices), catalog size, shared files emitted once;
  - full truth table for every gate type across all gates of each package at once (HC00/02/04/08/32/86, LS00, CD4001/4011/4069/4070/4081);
  - HC levels at 3.3/5/6 V, switching point at half the supply (3.3 and 5 V), LS TTL thresholds at 4.75/5/5.25 V, CD4000 ratiometric at
    5/10/15 V, output impedance against the datasheet 4 mA windows, TPD parameter and tpd windows (HC, LS, CD, both edges);
  - Schmitt thresholds from a triangle (HC14 at 5 and 3.3 V, CD40106 at 5/10/15 V), holds between the thresholds where an HC04 does not,
    ignores in-band noise and follows a full swing, hex independence;
  - tri-state buffers (125 and 244 truth tables including Hi-Z), unwired OE;
  - D flip-flops (HC74, CD4013): capture at the edge only, async set/reset, divide-by-2, two independent flip-flops;
  - JK (HC107 falling edge, CD4027 rising edge): set/reset/hold/toggle sequence, active edge only, async reset/set;
  - octal latch transparency and hold, OE Hi-Z; HC161 sequence 0-15 with RCO, async clear, sync load, ENP/ENT hold, two chained HC161
    to 40 (multi-instance); CD4017 one-hot, carry, reset, inhibit; CD4040 binary count, reset, ripple delay stage by stage; HC393 two
    independent counters;
  - HC595 byte shift and RCLK latch, SRCLR clears only the shift register, OE, QH', two cascaded (16-bit); CD4094 strobe, hold,
    transparency, OE, QS/Q'S;
  - HC151 all eight channels (two data patterns) and enable, HC157 all channels, select and enable;
  - ADC code = floor(256 vin / ref) within 1 LSB (exact away from boundaries), clamp, ratiometric REF, registered output; DAC 256 codes,
    output resistance R, VTH parameter; ADC -> DAC round trip within 1 LSB;
  - mixed: NE555 astable -> two HC161 -> DAC, +1 LSB per clock edge across the nibble carry.
- Existing tests whose pinned counts changed (no assertion weakened): `test_catalog.py` (Electrical simulated set includes `BATCH_D`),
  `test_library_ui.py` (148 / 138 / 2 / 2, simulated-only 143 / 138 / 0 / 1), `test_library_batch_c.py` (exact 117 -> `>=`),
  `test_subckt_packages.py` (142 simulated, 148 definitions, the 30 batch D X parts must be `limited`, the DAC `full`),
  `test_components.py` gains 31 contract rows (no edit). `docs/library.md` regenerated.
- Full suite `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests`: **1185 passed** (1042 + 112 + 31). `python3 -m app.catalog`:
  148 definitions, 0 errors, 0 warnings. No Rust change (no wheel rebuild, no cargo run).

### Skipped (and why)
- 74HC163 (HC161 with a synchronous clear), 74HC138/139 decoders, 74HC164/165 shift registers, 74HC75, 74HC112: not requested or the
  same cores (add them from `seq.lib`). A named ADC/DAC (ADC0804, DAC0808, MCP49xx): they need a handshake or SPI frame, which is a
  state machine per part with no datasheet fit to check, so only the generic converters were built.
- Generic N-bit converters: pin counts cannot depend on a param (G25), so 8 bits.
- Propagation delay accuracy beyond one nominal value, fan-out, supply current and power dissipation, setup/hold checks, output current
  limit, open-drain outputs, input clamps and capacitance, metastability, temperature: not modelled, and each limitation says so.
- Static validation of A devices (G26) and the power-up clock edge (G24) are logged as gaps; the CD4017 anti-lock gating is not needed
  because the Johnson counter always starts in a valid state.
- Digital devices of `docs/adding-components.md` were not documented there; the section above is the reference.

## Task 9: library batch E, sensors and other electrical devices (done, not committed)

### Packages added (25; catalog 148 -> 173 definitions; all `sim:electrical`, all X parts, all MIT, all `analytic`)
Kinds: equivalent 20, behavioral 5. Status: full 16, limited 9 (the B-driven or expression-gain sensors below; named in each part's generated note). Category Sensors (18 parts: Light, Temperature, Humidity, Force and position, Magnetic, Acoustic and ultrasonic, Current sensing, Pressure) and Electrical (7 parts: Isolation, Electromechanical, Transducers, Timing). `ALL_CATEGORIES` is unchanged.

| family | package (id) | kind | status | stimulus (instance param -> node `stim`) |
|---|---|---|---|---|
| light | LDR (power law, lux), PHOTODIODE (irr, SENS per mW/cm²), PHOTOTRANSISTOR (irr, base photocurrent) | equivalent | LDR limited, PD and PT full | LDR: `stim` = lux, B resistance; PD: `stim` = irradiance, G current; PT: `stim` = irradiance, G base current |
| temperature | PTC_THERMISTOR (logistic step in log R), RTD_PT100, RTD_PT1000 (IEC 60751 A, B), LM35 (10 mV/°C) | equivalent / behavioral | PTC, RTDs limited; LM35 full | PTC/RTD: `stim` = °C, B resistance; LM35: `stim` = °C, E gain 0.01 |
| magnetic | HALL_LINEAR (Vcc/2 + S·B, ratiometric) | behavioral | full | `stim` = field in mT, POLY(2) control |
| force / position | FSR (power law in force), STRAIN_GAUGE (R0(1+GF·ε)), STRAIN_BRIDGE (quarter bridge), POSITION_SENSOR (3-wire ratiometric) | equivalent / behavioral | all four limited | FSR: `stim` = N; strain: `stim` = µε (B gauge arm); POSITION: `stim` = stroke fraction, E VALUE |
| pressure / humidity | PRESSURE_BRIDGE (S·P·Vex), HUMIDITY_CAP (C0 + CS·RH) | behavioral / equivalent | pressure limited; humidity full | PRESSURE: `stim` = bar, B source; HUMIDITY: none, see G27 |
| acoustic | MIC_ELECTRET (drain current source), PIEZO_ULTRASONIC (BVD, 40 kHz) | behavioral / equivalent | mic full, piezo full | MIC: `stim` = SIN sound-pressure waveform in Pa, G current; piezo: no stimulus (passive) |
| current sensing | CURRENT_SHUNT (Kelvin sense), CURRENT_TRANSFORMER (ideal coupling with burden) | equivalent | full | none: the current is the caller's |
| other: isolation | OPTOCOUPLER (LED + NPN, F-source base drive, CTR generic) | equivalent | full | none |
| other: electromechanical | DC_MOTOR (R, L, back-EMF; simplified), SOLENOID (R + plunger-dependent L) | equivalent | full | DC_MOTOR: `stim` = speed in rpm, E gain; SOLENOID: none, see G28 |
| other: transducers | SPEAKER (Thiele-Small electrical analogy) | equivalent | full | none |
| other: timing | CRYSTAL_32K, CRYSTAL_16M, CERAMIC_RES_16M (BVD) | equivalent | full | none |

**Stimulus design (orchestrator constraint).** Every sensor's physical stimulus is an instance parameter with a default. It drives its own node `stim` through a source in the body (`VSTIM stim 0 DC {param}`, or `SIN(...)` for the microphone), and the response reads V(stim). A 1 GΩ load on `stim` keeps the validator quiet (a B reading V(stim) alone is DANGLING). The source's default uses the parameter, so a later task can replace the source with a time-varying waveform without touching the response.

**Status changes caused by the stimulus rewrite (accepted, per the orchestrator).** Before the rewrite all 25 parts were `full`. Nine dropped to `limited` because their response became a B source or an E VALUE: LDR, PTC_THERMISTOR, RTD_PT100, RTD_PT1000, FSR, STRAIN_GAUGE, STRAIN_BRIDGE, POSITION_SENSOR and PRESSURE_BRIDGE. The other sixteen stayed `full` because the response is linear in V(stim) (PD and PT via G, Hall via POLY(2), LM35 via E gain, mic via G, motor via E gain) or has no stimulus (HUMIDITY_CAP, SOLENOID, and the passive parts).

**Exceptions to the stimulus rule (logged as gaps).**
- HUMIDITY_CAP keeps RH as a fixed value folded into C. A B-source capacitor needs ddt(), which the validator rejects as a timestep artifact (G27).
- SOLENOID keeps the plunger position as a parameter in L. ngspice has no voltage-controlled inductor (G28).
- Piezo, shunt, CT, opto, speaker and the crystals have no physical stimulus; their drive is the caller's source.

### Shared models and provenance
- `_models/sensors/rtd.lib` (`bench_rtd`, project-authored, MIT, sha256 in MANIFEST). Callendar-Van Dusen with IEC 60751 A = 3.9083e-3 and B = -5.775e-7, valid 0 to 850 °C. The C term below 0 °C is not modelled. Used by RTD_PT100 (R0 = 100) and RTD_PT1000 (R0 = 1000).
- `_models/resonators/bvd.lib` (`bench_bvd`, project-authored, MIT). Used by the two crystals, the ceramic resonator and the piezo.
- Inline bodies for everything else (provenance `source` says "Bench project" with the law; values are generic unless noted).
- **Sources.** IEC 60751:2008 for the RTD constants: the coefficients were written from the standard as I know it, and I did not fetch or check a copy. LM35: TI datasheet SNIS159 (10 mV/°C, 0 V at 0 °C, −55 to 150 °C), URL https://www.ti.com/product/LM35. I did not fetch that URL in this task, so it needs checking. Every other value is generic or typical and is labelled as such in its limitations and provenance (no datasheet was cited for any other part, and none was claimed).

### Tests
- New `app/tests/test_library_batch_e.py`: **99 tests**, real ngspice through `Document` -> `run_document` (via `Circuit` from batch C):
  - load/kind/status/provenance for all 25; catalog size; the stimulus source appears in each stimulus part's netlist; shared RTD and BVD files emitted once per deck.
  - LDR `R10·(lux/10)^-γ` at 10, 100 and 1000 lx.
  - RTD PT100 at 0, 100 and 850 °C, and PT1000 at 100 °C, against the CVD closed form and the table values (100.00, 138.51, 390.48, 1385.1). Two PT100s in series (multi-instance, one definition).
  - PTC step: closed form at six temperatures, about 1000× across the switching point, midpoint √1000 · R25.
  - Photodiode V = SENS·E·RL at 1, 2, 4 mW/cm² and dark. Phototransistor Ic/Iph = BF+1 (1 % tolerance). Optocoupler Ic/IF = CTR at 1 and 0.5, and saturation with a 1 kΩ load.
  - Hall Vout = Vcc/2 + S·B at 5 V and 3.3 V; strain bridge closed form at 0, +1000 and −500 µε; strain gauge R; FSR R at 1 and 4 N; position ratiometric at 0, 0.25, 1; LM35 at 0, 25, 100 °C; pressure bridge; humidity RC time constant (2 µs).
  - Microphone tone amplitude at 94 and 74 dB SPL, plus its bias drop. Shunt sense = I·RS. CT burden voltage = Ip·RB/N (1 %).
  - DC motor: stall current V/RA, running current (V−KE·ω)/RA at 1000 rpm, and the L/R rise (τ = 0.5 ms). Solenoid L/R rise at both plunger ends. Speaker: DC resistance Re, the early Le/Re rise, and the motional ring at the Thiele-Small damped frequency (within 1 %).
  - Resonator rings (`test_resonator_rings_at_its_series_resonance`, four cases, 0.5 % tolerance): a 1 V step through 1 Ω, source current, detrended zero crossings. Measured against fs = 1/(2π√(Lm·Cm)): 32.768 kHz ≈ 0.1 %, 16 MHz ≈ 0.01 %, ceramic ≈ 0.05 %, piezo 40 kHz ≈ 0.06 %.
- Existing tests whose pinned counts moved (no assertion weakened; `>=` where the count can grow, exact only in the new file):
  - `test_catalog.py`: Electrical simulated set adds the seven Electrical batch E parts (`BATCH_E` import).
  - `test_library_batch_d.py`: `len(CATALOG.defs) == 117 + 31` -> `>=`.
  - `test_library_ui.py`: nav counts 173 / 145 / 2 / 20 and simulated-only 168 / 145 / 0 / 19 as `>=`.
  - `test_subckt_packages.py`: `>=` on 142 + 25 and 148 + 25; the limited set includes batch E's nine.
  - `test_milestone_e2e.py`: the "diode" search now includes PHOTODIODE (a Sensors part, correctly), so the assertions exclude it from the Electrical set. The empty-state probe uses "motor" (no sensor matches). The "temperature" assertion checks a subset including RTD_PT100 and LM35.
  - `test_components.py`: contract rows for each new simulated part are generated automatically (no edit).
- Word choice: the milestone "esp" search matched "response" in three descriptions (LDR, Hall, LM35). They say "electrical behaviour" now (G23's pattern, from batch C). `DC_MOTOR`'s description no longer says "source" (the "dc source" search).
- `docs/library.md` regenerated: `python3 -m app.catalog --markdown > docs/library.md`. `python3 -m app.catalog`: 173 definitions, 0 errors, 0 warnings.
- Full suite `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests`: **1309 passed** (1185 + 99 new + 25 contract rows). No Rust change, so no wheel rebuild and no cargo run.

### Skipped (and why)
- **Gas sensors.** A MOS-type sensor's R–ppm power law depends on the sensor and the gas, and no published constant could be cited, so there is no credible generic law to package.
- **Humidity RH and solenoid plunger as stimulus nodes.** Both need a voltage-controlled capacitor or inductor, which ngspice lacks. Logged as G27 and G28.
- **Connectors, terminals, headers and test points.** A pass-through would be a wire or a 1 mΩ resistor, so it adds nothing. An interface-only part would block a run when wired (the DHT22 rule). The result window already plots any node or source current, so test points and probes add nothing.
- **Voltmeter and ammeter elements.** A 0 V source and a 1 GΩ resistor already exist and already show up in results, so there is nothing to add.
- **Linear position as a second pot.** POT already exists, so POSITION_SENSOR is the 3-wire ratiometric sensor instead.
- **Piezo stimulus.** The BVD is passive, so there is no physical stimulus to expose.
- **4N35 as a separate pinout.** OPTOCOUPLER is one generic part with the PC817-style pinout and a generic CTR.
- **Not modelled, all stated in the limitations:** crystal load-capacitance pulling, drive level, overtones, temperature curves and ageing; the Hall output saturation at the rails; sensor noise, drift and hysteresis; the speaker's Le(x) and cone breakup; motor torque, inertia and brush losses; the opto's isolation rating and switching speed; the phototransistor's dark current beyond the transistor card.

### Gaps (appended to GAPS.md)
- **G27** (humidity RH stimulus: no controlled capacitor, B-ddt warned), **G28** (solenoid plunger: no controlled inductor), **G29** (B-driven sensors are `limited`; the validator does not check B values against a stimulus range), **G30** (sharp source edges make ngspice's trapezoidal integration put an alternating-sample artefact on a high-Q node current; the resonator tests use 10 µs to 50 ns edges and 0.2 to 0.5 µs output steps).
- Note: the pressure and strain bridges have a differential output with no reference. Without one ground on an output, the validator reports NO-DC-PATH (correct). The tests reference one side through 1 GΩ or ground it.

## Task 9.5a: live interaction, engine and Python API (done, not committed)

Design, probes and the full contract: `LIVE.md`. Probe script: `probes/live_probe.py`.

### The interrupted attempt
It had left `kernel.rs` and `ngspice.rs` edited and referring to a `crate::live` module that did not exist, so it did not
compile. I saved the diff, restored HEAD for the baselines, then reapplied all of it because it is sound and small:
- `gates()` extracted from `run_simulation_inner`, so batch and live share the static gates;
- `pub(crate)` on `apply_tmax_policy`, `scan_loud_failure`, `with_reason`, `nonfinite_or_short` and `check_observables`;
- `refuse_if_live()` in `run_simulation`;
- `read_vec_from` / `read_series_from` (read only points `from..`) and `drain_logs()` in `ngspice.rs`.
Nothing was reverted. There was no LIVE.md.

### Mechanism (chosen after probing; LIVE.md "Decision")
Native stop/resume with `alter` at each pause. The run pauses at every chunk boundary `k·chunk`, and each pause is
verified exact. Queued changes are applied there with `alter <dev> = v` and read back with `print @dev[param]`. Then
`delete all`, the next `stop when time = ...`, and `resume`.
- EXTERNAL sources were rejected: a user change is not a pure function of time.
- bg_halt was rejected: it halts at an arbitrary time, and the step after it gives 2.5e-3 V of artefact vs 1.6e-5 V here.

### Files
- New `benchcore/src/live.rs`:
  - `LiveSim`: the kernel layer: start, `advance(apply, until)`, finish.
  - `replay`.
  - `LiveRun`: controls, pause, pacing, sequence numbers.
  - `refuse_if_live`.
- `benchcore/src/lib.rs`: pyclass `LiveRun` and `replay()`.
- `benchcore/python/benchcore/__init__.py`: exports, plus `Simulation.live()` and `Simulation.replay()`.
- `app/electrical.py`: `live_devices` (pure, package data).
- `app/sim.py`:
  - `run_document` was split into `_prepare` / `_simulation` / `_failed` / `_derive`; its behaviour is identical.
  - New: `live_params`, `LiveSession`, `replay_document`.
  - `RunResult.state` gains "running" and "cancelled".
- Docs: `benchcore/docs/api.md` ("Live runs"), LIVE.md.

### Python API for 9.5b (use `app.sim`; never benchcore directly from UI code)
```python
from app.sim import live_params, LiveSession, replay_document, run_document

live_params(doc) -> [{"part": "RL1", "key": "lux", "label": "Illuminance (lux)", "unit": "", "stimulus": True}, ...]
    # what can change live, at the parts' current values, in part order. stimulus=True: a sensor's physical input.

s = LiveSession(doc, chunk=None, realtime=None)
    # chunk: sim seconds per chunk (default tstop/100; with realtime r: min(tstop/100, 0.05*r)). Also the change latency.
    # realtime: sim seconds per wall second (1.0 = real time); None = as fast as possible.
    # Never raises for circuit/core problems: s.result.status is "blocked" (pre-check) or "failed" (core refused,
    # e.g. another live run is open), and iterating yields nothing.

for chunk in s: ...          # ON ONE WORKER THREAD (QThread). Each step computes a chunk with the GIL released.
    # chunk = {"t": float sim time reached, "time": [t...] new points, "series": {vector: [(t, v)...]} new points only
    #          (keys as RunResult.series: node vectors 'v(n2)', currents, derived 'v(Part)'; readable name ->
    #          s.result.signal("V(n2)")["vector"]), "changes": [(t, part, key, value)] applied at this chunk's start,
    #          "done": bool, "wall": active wall seconds (paused time excluded), "rtf": sim s per wall s achieved so far}
    # Iteration ends (StopIteration) when done, cancelled or failed. Core errors never raise here: they end iteration
    # and land in s.result (status "failed", message "SolverError: ...", details). Points before an error are kept.

s.set_stimulus(part, value, key=None)   # any thread (the Qt thread): queues only, never waits for the engine.
    # key None = the part's one stimulus param. value: number or text as typed ("1k"). Applied at the next chunk
    # boundary. Raises app.components.ModelError (user message): run over, unknown part, param not live
    # ("... cannot change during a live run"), or a value the param refuses ("RL1 lux: must be > 0").
s.pause(); s.resume()        # any thread; a pause takes effect at the next chunk boundary; paused time is not paced.
s.cancel()                   # any thread; the worker's iteration ends at the next boundary (at once if paused).
s.close()                    # ends now and releases the engine; WAITS for a chunk in progress (call it off the Qt
                             # thread, or after the worker has finished). Idempotent.
s.state                      # "running" | "paused" | "completed" | "cancelled" | "failed" | "blocked"
s.result                     # RunResult growing on the worker thread: status "running" -> "success" | "cancelled" |
                             # "failed"; series accumulate (same keys and pairs as run_document's)
s.changes                    # [(t, part, key, value)] applied so far, in order
s.replay()                   # -> RunResult: this run's change log on the circuit it started from (blocking; refused
                             # while any live run is open; a session that never started returns its own result)
replay_document(doc, changes) -> RunResult   # the same for a saved log; "blocked" if a change is not live in doc
```
**Threading rules.**
1. Exactly one worker thread iterates a session.
2. Every session must end: iterate it to the end, `cancel()` and let the worker finish, or `close()`. Until it ends,
   `run_document` returns status "failed" ("BackendError: a live run is active: ...") and a second `LiveSession` is
   "failed" too.
3. Never leave the worker blocked in a paused session at shutdown. Call `cancel()` and then join the worker.
4. `s.result` lists are appended on the worker thread. Read them in the Qt thread after a chunk signal, or use the
   chunk payloads.

Engine level (for reference): `benchcore.LiveRun(deck, observables, devices=[], chunk=None, realtime=None)`.
- It is iterable, with `set({device: value}) -> seq`, `pause`, `resume`, `cancel`, `close`, `state` and `changes`.
- Its chunk dict also has `seq`.
- `benchcore.replay(deck, observables, [(t, device, value)]) -> dict` like `run_simulation`.
- Errors are the existing typed ones. `SimError::Lifecycle` maps to `BackendError`.

### Live-adjustable parts (LIVE.md "What is live")
- **Stimuli** (`stimulus: True`): LDR lux; PTC, RTD_PT100, RTD_PT1000 and LM35 temp; PHOTODIODE and PHOTOTRANSISTOR
  irr; HALL_LINEAR b; FSR force; STRAIN_GAUGE and STRAIN_BRIDGE eps; POSITION_SENSOR pos; PRESSURE_BRIDGE pres;
  DC_MOTOR speed.
- **Primitives:**
  - R `R`; V, battery and I `dc` (DC mode only);
  - SW, PUSH_NO and PUSH_NC `state` and the active on/off value;
  - SPDT, DPST and DPDT `state`;
  - POT and RHEOSTAT R, pos and wiper resistance;
  - BAT_AA, BAT_COIN and BAT_LITHIUM voc and rint.
- **Internal R/V/I values** the rule also finds (G35): capacitor ESR and leakage, op-amp macro vos/ib/iq/rout, SCR,
  IGBT, crystals and others. That is 49 parts in all.
- **Not live:** B/E/G expressions, models, waveforms (PULSE/SIN/PWL, so the microphone SPL), C/L values, a source's
  mode, HUMIDITY_CAP RH (G27), SOLENOID plunger (G28).

### Limits
- Latency is one chunk, and a chunk cannot be interrupted (G34).
- A change queued during the last chunk is dropped with the run's end.
- Params have no upper bound for sliders, and an accepted extreme value can fail the solve (G31).
- XSPICE edge timing moves within a time step under chunking (G33).
- O/U/OSDI decks are refused (`UnavailableError` -> status "failed").
- An `@` observable needs a `.save` (the app always saves its currents).
- One live run at a time.

### Tests (all against real ngspice)
- `benchcore/tests/live.rs`: 12 tests.
  - LDR step at t1 matches the law before and after.
  - RC source change matches the analytic response and a batch PWL step.
  - Live vs replay with changes from another thread, and the replay is deterministic.
  - Pause/resume continuity.
  - Cancel, then batch.
  - Batch, replay and a second live run refused while live.
  - Solver error mid-run, then recovery.
  - Poisoned engine mid-run, then reload.
  - Bad devices refused (unknown, C, PULSE, injection, unsaved `@`).
  - Pacing.
  - Two live devices.
  - XSPICE edges.
- `benchcore/tests/python/test_live.py`: 12 tests. The same scenarios through `Simulation.live` and a worker thread,
  plus GIL release (the main thread keeps running while a chunk computes) and the package exports.
- `app/tests/test_live.py`: 30 tests.
  - Every stimulus is live and flagged; static parts have none; primitives are live and waveforms are not.
  - LDR step through `LiveSession`.
  - `replay_document` and `session.replay()` vs live.
  - Switch and pot together, against the closed form and replay.
  - Pause/resume continuity.
  - Cancel, then batch.
  - Engine error mid-run, then recovery.
  - Batch and a second live run refused; a blocked circuit never starts.
  - User errors.
  - Pacing.

| Suite | Baseline (HEAD d3a5839, wheel rebuilt) | After |
|---|---|---|
| `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests` | 1310 | 1340 (+30 `app/tests/test_live.py`) |
| `cd benchcore && cargo test` | 59 (13 lib + 3 adder + 43 correctness) | 71 (+12 `tests/live.rs`) |
| `cd benchcore && python3 -m pytest -q tests/python` | 9 | 21 (+12 `test_live.py`) |

The wheel was rebuilt and reinstalled per CLAUDE.md. Golden netlists are unchanged (`build_netlist` untouched).

### Gaps (appended to GAPS.md)
- **G31:** no slider range on stimulus params.
- **G32:** the batch pause path dumps core with an unsaved `@dev[param]` observable. This bug predates this task, and
  the repro is in the entry.
- **G33:** XSPICE edge shift under chunking.
- **G34:** a chunk cannot be interrupted.
- **G35:** the derived live rule exposes internal values.

## Task 9.5b: live interaction, UI (done, not committed)

Built on 9.5a's `app.sim` API only (`LiveSession`, `live_params`, `session.replay()`); no engine code. Engine
contract: `LIVE.md`.

### Behaviour
- **Live button** (top bar, after Reset; F6; Simulation menu) starts a live run of the circuit and turns into
  **Stop**. Batch Run / Re-run / Reset (buttons, F5, Ctrl+F5, menu), the run-setting fields and the pace picker are
  disabled until the worker has finished; `run()` and `reset()` also refuse while a session exists. The state badge
  shows **Live** (hollow, running colour), **Paused**, then Completed / **Stopped** (cancelled) / Failed.
- **Pace** picker in the results header: `10 s run` (default: tstop spread over 10 s of wall time), 1×, 0.1×, 0.01×,
  0.001×, 0.0001× (sim seconds per wall second) and `max` (as fast as possible). The chunk is 9.5a's default.
- **Live strip** (replaces the result banner while live): Pause/Resume (F7), Stop, and `t = 11.2 ms / 20 ms
  0.0100× real time` (sim time reached, stop time, achieved factor from the chunk's `rtf`; "paused" while paused).
- **Inspector**: while live, a selected part with live params gets a "Live" section on top: a slider per number
  param (label from the package, value readout with its unit, range ends underneath, `(log)` on a log slider,
  tooltip with the range) and a dropdown per choice param (switch state). Stimulus params are listed first. Moving
  a control emits `live_edited`; the window coalesces changes (last value per param wins) and hands them to
  `set_stimulus` at most every 50 ms, so a drag does not flood the chunk queue. A refused value (over `max`, or the
  run just ended) goes to the status bar in red; the run continues. No on-canvas affordance was added.
- **Streaming plot**: the window keeps its own `RunResult` copy and extends it on the Qt thread from each chunk
  payload (rule 4). A 50 ms single-shot timer coalesces redraws. The time axis is fixed at 0..tstop while live, so
  the line visibly grows. The cursor follows the newest point unless the user moved it back. The readout, stats and
  LED glow and current labels work during the run, because the copy has `ok=True`. Each applied change is a short
  orange tick at the bottom of the plot at its time; hovering over it shows `t = 6.2 ms: RL1 lux = 805`.
  `RunResult.changes` (new field) holds the log; replay results carry it too.
- **After the run**: the result stays as a normal result: scrub, Play, probe picker, Show details, Re-run. A
  cancelled run keeps its points (`ok=True`, state "cancelled"). The banner reads `ok, N points` plus a **Replay
  as batch run** link (shown when the live run applied changes). It calls `session.replay()` (blocking, like Run)
  and shows that result with the same ticks.
- **Errors mid-run** (e.g. a `SolverError` in a chunk) are shown exactly like a batch failure: Failed badge, "Run
  failed" page with the message, `FAILED: ...` status, Show details with the developer text. The engine is free
  again for the next run. An exception in the worker (an app bug) becomes a failed result with the traceback in
  details.
- **Editing during a live run stops it** (chosen over refusing). Any change in the run key (parts, params, wires,
  settings; the same signature that makes batch results stale) cancels the session in `_check_stale`. The streamed
  result is dropped as out of date, as after a batch run, and the status reads "Live run stopped: the circuit
  changed. results out of date - press Re-run". Moving or selecting parts does not stop it. Slider values are never
  written into the document (G39).

### Threading and cleanup
- `LiveWorker(QThread)` iterates the session; each chunk is emitted as a queued `chunk` signal; `finished` triggers
  `_live_done`. That slot joins the thread (`wait()`; finished is emitted in the thread's last moments), calls
  `session.close()` (idempotent, never waits since the iteration has ended) and publishes the outcome.
- The Qt thread only calls queueing methods (`set_stimulus`, `pause`, `resume`, `cancel`), so it never waits on
  the engine. The exceptions are `end_live()` on window close and on New/Open/examples (`_reset`). There it cancels
  (immediate if paused) and waits for the chunk in progress. This is threading rule 3: never leave the worker
  blocked in a paused run (G38).
- A chunk that arrives after its result was dropped is ignored (`result is not live_result`).

### Package data (G31, fixed)
- New optional param key `scale` (`"log"` | `"linear"`; default log for Ω, else linear; `"log"` needs `min >= 0`;
  `DEF-PARAM` otherwise). `Param.slider(value) -> (lo, hi, log)`: [min, max]; a log slider without a positive min
  starts six decades below max; a missing end is taken around the value (x/÷100 log, ±2|v| linear).
- `max` (and a `min` where there was none) on every stimulus that lacked one: LDR lux 100000 + log, FSR force
  100 N + log, PHOTODIODE/PHOTOTRANSISTOR irr 100 mW/cm², HALL_LINEAR b ±200 mT, STRAIN_GAUGE/STRAIN_BRIDGE eps
  ±10000 µε, PRESSURE_BRIDGE pres 10 bar, DC_MOTOR speed 20000 rpm. LM35, PTC, RTD and POSITION_SENSOR already had
  both ends. Documented in `docs/adding-components.md` (Parameters) and `docs/frontend-components.md`.
  `docs/library.md` is unchanged (bounds are not in the catalog output; regenerated and compared).
- `app/tests/test_live.py::test_engine_error_mid_run...` used LDR lux = 1e300, now refused by `max`; it uses 1e-300
  (still inside `min > 0`, still breaks the law: G37).

### Layout
- Top bar: the Live button needs about 104 px, so the compact top bar (no Transient chip, narrower fields and
  buttons) now starts below 1360 px instead of 1240, and below 1150 px the "Stop time"/"Time step" captions hide
  (the fields keep their tooltips). Minimum bar width is now 1136 px compact and 1001 px narrow, so it fits at
  1280 and at the 1024 minimum (before this task it overflowed by 32 px at 1024).
- Screenshots were rendered offscreen at 1280×800, light and dark: live and paused with the LDR slider and a
  half-grown plot with its change tick, and finished with two ticks and the replay link. Nothing overlaps; the
  plot keeps its tick labels clear because the live strip replaces the banner instead of adding a row.

### Files
- `app/window.py`: `LiveWorker`, Live/Pause actions and button, `live`, `pause_live`, `stop_live`, `end_live`,
  `live_set` + `_live_flush`, `_live_chunk`, `_live_redraw`, `_live_label`, `_live_done`, `replay_live`. Also
  `_sync_controls`, `_show_result`, `_check_stale`, `closeEvent`, `_reset`, `resizeEvent`, `refresh` (passes live
  values to the inspector).
- `app/results.py`: `PACES`, pace combo, live strip, `grow()`, `_points()`, fixed `xlim`, change ticks + hover
  tooltip, empty-series guards, replay link in the ok banner.
- `app/inspector.py`: `live_edited` signal, `slider_map`, `live_fmt`, `_live_controls`; `show_selection(..., live)`.
- `app/components.py` (`Param.scale`, `Param.slider`), `app/catalog.py` (`scale` key), `app/sim.py`
  (`RunResult.changes`; replay results carry the log), `app/theme.py` (badge states live, paused, cancelled),
  `app/widgets.py` (stop icon), 9 sensor/motor `component.toml` files.
- Docs: adding-components.md, frontend-components.md, frontend-architecture.md, LIVE.md (limits), GAPS.md.

### Tests: `app/tests/test_live_ui.py` (27, real MainWindow, real ngspice, offscreen)
- LDR divider at 0.01×. The lux slider is moved mid-run. The plotted output (`ax.lines[0]` = `view_data()`) matches
  the LDR law for 10 lux before the change and for the new lux after it, within 1e-4 V. The change tick is drawn.
  Scrubbing works after the run.
- The plot grows (fixed 0..tstop axis, more points over time).
- Pause holds sim time over 400 ms; resume completes to tstop.
- Stop keeps the points so far (state "Stopped"), then Re-run batch succeeds.
- Batch is disabled during live: buttons, actions, fields, pace; direct `run()`/`reset()` are refused.
- Window close mid-run while paused: the worker finishes, the session is cancelled, `run_document` works, and a
  new window's batch run works.
- Engine error mid-run (lux 1e-300): shown as Run failed with SolverError, then batch recovers.
- A refused value (over max) is reported in red and the run goes on.
- Editing a param stops the run and makes the result stale.
- Switch dropdown plus resistor slider range (fallback x/÷100): the switch current steps at the change.
- Replay matches the live run within 1e-4 V and carries the same change log.
- Slider ranges come from package data for all 14 stimuli. Log scale and mapping. A bad `scale` is a definition error.
- Ran 3 more times in a row: 27/27 each time (about 23 s).

| Suite | Before | After |
|---|---|---|
| `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests` | 1340 | 1367 (+27 `test_live_ui.py`) |

### Gaps (appended to GAPS.md)
- **G31:** fixed.
- **G36:** sensor limitation text says the stimulus is fixed for the whole run.
- **G37:** typed values below a log slider's floor can still break the solve.
- **G38:** close/New/Open wait for the chunk in progress on the Qt thread.
- **G39:** slider values are not written back, and there is no save-session.

## Task 9.6: gap fixes (done, not committed)

Every GAPS.md row has a final status:
- **16 fixed**: G1 G2 G7 G8 G12 G13 G14 G15 G16 G23 G26 G30 G32 G36, plus G18 and G31, which earlier tasks fixed.
  G13 covers the current EXP/SFFM modes; its AC source is deferred with G17.
- **7 wontfix**, each with its reason: G3 G4 G5 G6 G11 G33 G37. A test pins G11.
- **16 deferred**, each saying what it would take: G9 G10 G17 G19 G20 G21 G22 G24 G25 G27 G28 G29 G34 G35 G38 G39.

### Notable fixes (each reproduced first, against real ngspice, and kept as a test)
- **G32 crash.** `pause_at` plus an unsaved `@v1[dc]` killed the process (rc -11). One shared guard,
  `kernel::refuse_unsaved_device_observables`, now runs on the batch path and the live path.
- **G7 hang.** A lossy LTRA limits the step to its delay, and its convolution is quadratic in the step count (1e4 steps:
  2.2 s; 2e4: 9 s; 5e4: 55 s; 3e5: never). A watchdog is not possible in-process: libngspice exports only the API, Python
  owns SIGINT, and bg_halt was rejected. The validator now refuses a run needing more than 2e4 steps
  (`tstop / min(tmax, len*sqrt(LC))`). Package probe decks use `.tran 1u 10u`, so a body line under 0.5 ns is refused at load.
- **G15 silent runaway.** Kernel runs scan the plot's node vectors after success. A subcircuit-internal node whose peak
  exceeds max(10x, +100 V) of every circuit node becomes `SimResult.warnings` (PyO3 `warnings`). The app adds it to
  findings as IMPLAUSIBLE-NODE and to the message. Live chunks and replays do not scan.
  - The scan found that the **shipped bench_scr** (SCR, TRIAC, DIAC) still ran its internal nodes to 833 V when it dropped
    out below IH.
  - Fix: `CAK ax k 10p` (MANIFEST sha updated). Junction capacitances were tried first, but they let the UIC start step
    fire the DIAC.
- **G1.** Under `.tran ... uic` (every Bench run, and the package probe decks), C counts as a connection for NO-DC-PATH.
  Probed: a C divider, a C-C-C chain and an I charging a C solve, while a capacitor island is still singular and still an
  error. Three app tests used a floating capacitor as their "core error" trigger. They now use a source across an
  inductor (V-LOOP), with the same assertions. `test_floating_pin`'s second case now runs with warnings, and a C island case
  was added.
- **Validator additions** (Rust, all probed):
  - G8: K misuse forms. K to a C or D also dumps core.
  - G16: MODEL-SIGN warning.
  - G14: MODEL-IBV warning. The floor is exactly IS*BV/Vt, whatever N. D_1N5819 and D_BAT54 carry it: changing their
    IBV changed their tested leakage, so the cards stayed as they were and their limitations explain it.
    `test_every_shipped_part_passes_the_static_validator` pins exactly those two warnings.
  - G26: XSPICE port shapes. A trailing output may be left off a flip-flop, which ngspice accepts.
  - G2: names per expression context. `{}` reads no constant, R/C/L values read time/temper/hertz, and B has no
    sqr/arctan/int.
- **G30.** `Simulation.configure(method="gear")` adds `.options method=gear`. The setting is exposed as
  `Document.method` and Simulation > Gear integration. It is saved as `simulation.method` only when it is not the
  default, it is part of the stale key, and docs/persistence.md describes it. Gear removes the trap artefact on a
  32 kHz crystal; it also damps the real ring by about 1.5 % over 2 ms.
- **G20 is worse than logged, and deferred.** The shipped LM324 macro spikes 1.5 V at 2 us steps on a gain-2 falling
  step. A 5 MHz macro needs steps of about 1/(40*GBW) at edges. Neither gear nor a larger internal capacitance fixes it.
  The 7 op-amp packages now state this in their limitations.
- **Library and data.** G12: param kind `points`, plus VSRC_PWL and ISRC_PWL (catalog 175). G13: ISRC_WAVE gains EXP
  and SFFM. G23: search matches at word starts. G36: 12 stimulus limitations reworded. Crystal limitations follow G1.
  docs/library.md is regenerated, and adding-components.md and LIVE.md are updated.

### Contracts kept
- Golden netlists are byte-identical. `build_netlist` is unchanged, and `config()` and `sim_config()` add `method` only
  for gear.
- Task-2 guarantees and batch/live behaviour are unchanged, except for the new refusals and warnings above.
- The wheel was rebuilt and reinstalled after the last Rust change.

### Tests
| Suite | Before | After |
|---|---|---|
| `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests` | 1367 | 1378 (+8 `test_gaps_96.py`, +1 GUI gear test, +2 contract rows for the PWL sources) |
| `cd benchcore && cargo test` | 71 | 79 (correctness t71-t78) |
| `cd benchcore && python3 -m pytest -q tests/python` | 21 | 23 |
Count-pin changes, none of them weakened:
- batch E's catalog size is now `>=`; the exact 175 is in test_gaps_96;
- the Electrical simulated set in test_catalog adds the two PWL sources;
- the contract test's kind list adds `points`.

The first baseline app run printed a Python fatal-error footer (exit code hidden by `tail`); 5 later full runs were clean.
Watch for a shutdown crash.

## Task 9.7: live-run duration and indefinite live runs (done, not committed)

User request: control how long the real-time (live) simulation runs, and let it run indefinitely. Design, probes and
limits: LIVE.md "Indefinite runs and the live duration (task 9.7)". Probe script: `probes/indefinite_probe.py`.

### Behaviour
- **Live for** field (top bar, just before Live; editable combo, `objectName` `topcombo`): type a time (`2`, `500m`),
  or pick **Stop time** (the default, unset: live runs last the batch stop time, as before) or **Indefinite** (until
  Stop). Invalid text marks the field red and disables Live only (batch Run is unaffected); F6 then says why in the
  status bar. Disabled while a run is in progress. Saved with the circuit (`simulation.live_tstop`, only when set);
  older files load unchanged. Not part of the stale key (it does not change the circuit or batch results).
- **Scrolling plot.** The plot window is the batch stop time. A run longer than that (or indefinite) scrolls:
  the axis shows (t - stop time, t), only that window is kept (and at most `LiveSession.cap` points per series), and
  after Stop the retained window can be scrubbed (cursor, slider, readout, stats). The live strip reads
  `t = 72.6 ms / ∞` for an indefinite run.
- **Replay as batch run**: a finite run replays its live duration; an indefinite run replays up to the time it
  reached. Past the batch limits it is refused in the status bar (`REPLAY BLOCKED: Replay refused: the live run
  lasted 1.1 s; ... limit 100,000 points ...`) and the live result stays.
- Pace "10 s run" spreads the live duration over 10 s (the stop time when indefinite).

### Engine (benchcore)
- `live.rs`: decks get `.save none` (inserted after the title); chunk points come from SendData
  (`ngspice::capture_start/take/stop`), bitwise the stored vectors (probed). `ngSpice_Command(NULL)` every 100
  chunks (`ngspice::release_commands`) frees ngspice's per-command memory. The `tstop/chunk <= 100000` limit is gone;
  the "chunk must end after the last" guard uses 1e-12 relative. Every live run, finite or not, takes this path.
- `.save none` is sticky in libngspice (static `savenone`, never reset), so `LiveSim::shutdown` unloads the library
  (`ngspice::unload`); the next run reloads it (~1 ms). Batch runs are otherwise untouched.
- No API change. An indefinite run is `tstop = tstep × 1e12` plus an explicit chunk, chosen in `app/sim.py`.

### App
- `app/model.py`: `Document.live_tstop`, `live_duration()`, `RUN_PARAMS["live"]`, `LIVE_STOP_TIME`/`LIVE_INDEFINITE`,
  `size_error()` (extracted from `config()`, same message).
- `app/sim.py`: `LiveSession.tstop/window/indefinite/cap`, chunk = window/100 (paced: min with 0.05 × pace),
  `trim()`, `INDEFINITE_STEPS`, replay length and limit.
- `app/window.py`: Live-for field, `live_for_changed`, `_live_for_text`, scrolling `xlim`, trim of the window copy,
  replay refusal. Top-bar thresholds: full layout from 1520 px (was 1360); the Live-for caption shows only there; below
  1150 px the dark-mode button hides (View > Dark mode stays) and the field and file label shrink. Bar minimum: 1505
  full, 1232 compact (fits 1280), 1018 narrow (fits 1024).
- `app/results.py`: `LIVE_FOR` presets, change ticks older than the retained data are not drawn. `app/theme.py`:
  `QComboBox#topcombo`.
- Docs: LIVE.md, persistence.md, frontend-architecture.md, benchcore/docs/api.md.

### Screenshots (offscreen, 1280×800, light and dark)
Indefinite run at pace 1× with the LDR selected: field reads "Indefinite" (disabled while live), the axis scrolled to
about 52-72 ms with the change tick, live strip `t = 72.6 ms / ∞`. Top bar idle at 1600 (caption), 1280 and 1024
("Indefin", G41). Nothing overlaps.

### Tests
- `benchcore/tests/live.rs` +1: `a_long_live_run_keeps_ngspice_memory_flat_and_changes_exact` (30000 chunks at
  max pace on a 1e7 s tstop, RSS growth < 4 MB, every pause at its boundary, a change at 2.5 s lands exactly, a batch
  run afterwards keeps all its points). Fails with 22 MB growth if the command release is removed.
- `app/tests/test_live_duration.py` (10): the duration sets the live tstop (default, shorter, longer, invalid);
  an indefinite run goes 25× past the stop time until cancelled; 8000 fast chunks keep retained samples under the cap
  and RSS growth under 15 MB; `trim`; a change 15× past the stop time lands exactly and replays to the reached time;
  replay past the batch limit is refused; save/load round trip; an old file without the key; the UI field (validate,
  presets, unset); an indefinite UI run scrolls and its window is scrubbed after Stop.

| Suite | Before | After |
|---|---|---|
| `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests` | 1378 | 1388 (+10 `test_live_duration.py`) |
| `cd benchcore && cargo test` | 79 | 80 |
| `cd benchcore && python3 -m pytest -q tests/python` | 23 | 23 |

The wheel was rebuilt and reinstalled after the last Rust change.

### Gaps (appended to GAPS.md)
- **G40:** tstop-defaulted source parameters would follow the indefinite tstop (none ship).
- **G41:** the Live-for field clips its text below 1150 px; its caption shows only from 1520 px.
- **G42:** only the last window of a long live run is kept.

### Follow-up: intermittent segfault in the live UI tests (fixed, not committed)
- **Symptom.** In the full app suite: `Fatal Python error: Segmentation fault`, GUI thread in
  `QApplicationPrivate::notify_helper` (posted events from `QTest.qWait`), LiveWorker thread in `LiveSession.__next__`,
  interpreter state "Garbage-collecting".
- **Root cause.** Python's cyclic gc ran on the LiveWorker thread (its allocations triggered it) and finalized Qt
  wrappers there. PySide defers deleting widgets to the GUI thread, but it deletes a Python-owned non-widget QObject
  on the collecting thread (probed: QObject, QTimer, QAction are deleted on the collecting thread; QWidget, QLabel on
  the GUI thread). The canvas's `QGraphicsScene()` had no parent, so a dropped window's scene was deleted on the worker
  (3 of 3 times when gc runs on another thread), racing the GUI thread that was delivering the window's DeferredDelete
  and the scene's events.
- **Fix.** (1) `canvas.py`: the scene is created with the view as its parent, so it dies with the view on the GUI
  thread. (2) `window.py`: while a live worker runs, automatic gc is off and a GUI-thread timer collects instead
  (generations 0-1 every 200 ms, a full collection every 10 s, ~25 ms); `_live_done` turns gc back on. No Qt
  wrapper is finalized off the GUI thread during a live run.
- **Tests** (`test_live_ui.py`, +2; both fail deterministically without the fix):
  - `test_gc_never_runs_on_the_live_worker_thread`: gc threshold 50 during a UI live run. Without the fix, the
    worker thread collects (`Dummy-1`).
  - `test_a_dropped_windows_canvas_scene_is_deleted_on_the_gui_thread`: without the fix, the scene is destroyed on the
    other thread.
- The crash itself would not reproduce here: 0 in 9 full runs (3 in parallel, 2 under gdb), 12 loops of
  test_live_ui.py, and 6 low-threshold stress runs. The two tests above reproduce its cause on every run.
- After the fix: test_live_ui.py passed 32 consecutive runs (29 tests each). Totals: app 1390, cargo 80, py 23.

## Task 10: adversarial review, G20, final regression (done, not committed)

### Review (branch diff 7a8606f..73cb002)
Each finding was confirmed by reproduction or by reading the code before any change. Each one is a GAPS row.
- **G43, provenance (confirmed with a sha256 script).**
  - The problem:
    - `_models/MANIFEST.toml` had 23 records for 19 files: 4 stale duplicates (`digital/mux.lib` ×3,
      `digital/gates.lib` ×1, old sha256s).
    - `manifest()` keyed records by path, so the last record silently won.
  - The fix:
    - The duplicates are removed.
    - `manifest()` now refuses a manifest that lists a path twice, which rejects every `models:` package in that
      root.
    - Test: `test_a_manifest_listing_a_file_twice_is_refused`.
  - Every remaining record has a license (MIT, project) and a matching sha256.
- **G44, docs drift.** The README library summary stopped at analog ICs, and live runs were not listed as a
  feature. Both sentences are updated.
- **G45, dead code.** Three unused test helpers were removed: `load_pair`, `pins_of` and `SENSORS`.
- **Checked, no defect found:**
  - live-run threading and lifetime: `set` takes only the control lock; `close` and `__next__` release the GIL;
    every `LiveSim` error path unloads ngspice; gc is restored in `_live_done`;
  - 7 validator edge decks against ngspice: case-insensitive nodes, chained param defaults, undefined defaults,
    `gnd` in a body, a body model used at top level, subckt params shadowing a global `.param`;
  - every `analytic` and `reference` part is exercised by named tests;
  - catalog counts in docs.
- **Rust warnings.** The two warnings (`Symbol` import, `num_end`) predate the branch and were left.

### G20 (fixed)
- **Cause.** The slew ramp is linear, so its truncation error is zero and ngspice grows the step to tmax. The hard
  N=0.01 stage-1 clamp corner where slewing ends then overshoots towards a rail. Gear does not fix it.
- **Fix.** In `bench_opamp_macro`, D1 and D2 use `bench_dsoft` (N=1). Vcp and Vcn are
  `10 - Vt·ln(1e-5/1e-15)`, so the clamp sits at 10 V at 10 µA. The rail clamps are unchanged. MANIFEST sha256
  updated.
- **Scan.** 600 runs: 5 shipped parameter sets × gains 1, 2, 5, 10 × steps 0.1 to 10 µs × 3 input steps × 2
  supplies, each compared with a 5 ns reference.
  - Before: 133 runs bad (> 0.05 V), up to 14.9 V. Under gear: 80 bad.
  - After: no run above 0.05 V at steps up to 1 µs. Worst 0.09 V at 2 µs, 0.2 V at 5 µs, 0.4 V at 10 µs.
  - Slew rate within 1 % of SR.
- **Test.** `test_coarse_time_steps_do_not_spike_the_output_at_an_edge` covers LM324 gain 2 at 2 µs, TL072 gain 10
  at 1 µs, and MCP6002. All 3 fail on the old macro, by 3 to 5 V.
- **Limitations.** The 7 op-amp packages' limitation now states the measured residual. `docs/library.md` was
  regenerated and is unchanged, because limitations are not part of it.

### Tests (real exit codes, `cmd; echo rc=$?`)
| Suite | Result | rc |
|---|---|---|
| `cd benchcore && cargo test` | 80 passed (13 + 3 + 51 + 13) | 0 |
| `cd benchcore && python3 -m pytest -q tests/python` | 23 passed | 0 |
| `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests`, run 1 | 1394 passed (1390 + 4 new) in 545.75 s | 0 |
| same, run 2 | 1394 passed in 537.91 s | 0 |
| `python3 -m app.catalog` | 175 definitions, 0 errors, 0 warnings | 0 |

- Baseline at 73cb002, before any edit: app 1390 passed, rc 0.
- No Rust changed, so the wheel was not rebuilt.
- REPORT.md (SPEC §9): the subagent's file-write tool refused to create it ("subagents should return findings as
  text"). Its full text went to the orchestrator in the handback, to be saved as
  `docs/plans/ngspice-library/REPORT.md`.

## Gap closure, wave 3: integration review (corrections to earlier sections)

The gap-closure plan (`docs/plans/gap-closure/`) closed or scoped every deferred and open row in GAPS.md. The
summary and counts are in REPORT.md's addendum. Earlier sections of this log are kept as written. These statements
in them are no longer true:
- **Task 1, "Compatibility mode":** "`ps`/`lt` apply only to `.include`d files" is false for ngspice 47. `ps` also
  translates the main deck (probed in W5).
  - Bench now uses ps only through `compat = "ps"` packages, whose files are `.include`d. This is the one exception
    to "inline every definition, never through `.include`".
- **Vendor macromodels "deferred" (task 1, task 7):** the ps path exists (G10). No vendor file is shipped yet (G19).
- **Task 8:**
  - "8 bits" (G25): ADC/DAC also come at 4, 10 and 12 bits.
  - The power-on clock edge (G24) is gated in every rising-edge body.
- **Task 9:** the 9 B-driven sensors are `full`, not `limited` (G29). Humidity and solenoid position are stimulus
  nodes (G27, G28).
- **Task 9.6:**
  - The "16 deferred" list is closed: every row is now fixed, partially fixed (G19, G21) or wontfix.
  - "the AC source is deferred" (G13): V and I have an AC stimulus.
- **Task 9.5b/9.7, live runs:**
  - Chunks adapt (G34).
  - A package can list its live params (G35).
  - Final values can be applied (G39).
  - A long run keeps a coarse history (G42).
  - The top-bar widths are per analysis (`MainWindow.FIT`, G41).
- **Rebuilding the wheel on this machine:** `benchcore/target/wheels/` also holds an old manylinux wheel, so
  `pip install ... target/wheels/benchcore-*.whl` fails with ResolutionImpossible. Install the
  `benchcore-0.1.0-cp314-cp314-linux_x86_64.whl` file by name.
