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
