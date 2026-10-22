# ngspice engine and component library: final report

Branch `feat/multi-terminal-transistors`, from 7a8606f to 73cb002 plus the task 10 fixes. Sources:
- brief: `SPEC.md`;
- per-task log: `HANDOFF.md`;
- design and probed ngspice facts: `DESIGN.md`;
- live runs: `LIVE.md`;
- gaps: `GAPS.md` (G1 to G45).

All numbers come from the catalog or test runs on this machine (ngspice 47).

## 1. Limitations confirmed at the start (DESIGN.md section 2)
- **L1:** the app accepted only R C L D V I Q J M.
- **L2:** packages could not carry a `.subckt` or definition files, and there were no X parts.
- **L3:** any `@dev[x]` current was accepted; a wrong one (`@z1[id]`) segfaults ngspice.
- **L4:** `.subckt` bodies were skipped, and a nested one broke the skip (false NO-DC-PATH).
- **L5:** X instances had no undefined-subckt check and no instance-param check, and every instance got OPAQUE-X.
- **L6:** an undefined subckt "succeeded" with 0 points.
- **L7:** one undefined `{param}` or a LAPLACE source left libngspice unusable for the rest of the process.
- **L8:** XSPICE A devices were rejected.
- **L9:** VDMOS was rejected.
- **L10:** E/G VALUE/TABLE/POLY were rejected, and S W T O U Y Z were read as 2-node elements.
- **L11:** Mextram was accepted though this build lacks it.
- **L12:** errors showed only as `circ rc=1`.
- **L13:** `.spiceinit` in the working directory changed behaviour.
- **L14:** the B-source TMAX policy ignores subckt bodies. This is still true, and harmless while tmax = tstep.

## 2. What changed

**Engine** (`ngspice.rs`, `kernel.rs`, `live.rs`):
- The exit callback now unloads and reloads the library, so an aborted deck fails only its own run.
- Any stderr `Error` line or rc-0 parse message fails the run; an empty grid is a solver failure, and messages carry ngspice's reason.
- The compatibility mode is pinned off before each circuit load.
- There is a device table with one row per element letter, with levels probed one per process, and a parity test against the Python table.
- New refusals: unsaved device observables (G32 core dump) and K misuse (segfaults).
- After a successful run, a runaway internal node gives an IMPLAUSIBLE-NODE warning (G15).
- Gear integration is available (G30).
- Live runs (`live.rs`):
  - native stop/resume at chunk boundaries, with verified `alter` of DC sources and resistors;
  - pacing, pause and cancel;
  - a change log and a deterministic replay;
  - indefinite runs using `.save none` and SendData streaming, with ngspice command memory released every 100 chunks and the library unloaded after each live run.

**Validator** (`validate.rs`, `benchcore.validate()`):
- It parses the definition tree, flattens instances, and analyses each reached definition once. Findings carry a scope and a class (syntax, dependency, connectivity, unsupported, limited, value).
- Scoping follows ngspice: models and subckts are lexical, params and `.func`s are dynamic.
- New checks:
  - UNDEFINED-SUBCKT, -PARAM and -ELEMENT;
  - UNKNOWN-INSTANCE-PARAM;
  - DUP-SUBCKT and DUP-MODEL;
  - SUBCKT-RECURSION and SUBCKT-UNBALANCED;
  - LIMITED-ANALYSIS;
  - expression names per context;
  - K misuse, MODEL-SIGN and MODEL-IBV;
  - XSPICE port shapes;
  - the lossy-LTRA step budget.
- DC topology runs on the flattened circuit; capacitors count as connections under `uic`.
- Part status:
  - **full:** no error and nothing left unanalysed;
  - **limited:** no error, but some constructs (A, B, E/G VALUE, T/O/U/Y/P) are left to ngspice, and each is named in a generated limitation line;
  - **failed:** the package is rejected.
  - Neither full nor limited proves the circuit is electrically right.

**Package format and netlist** (`electrical.py`, `catalog.py`):
- New keys: `device = "X"`, `defs` (package files or `models:` paths under `_models/`), inline `body`, `model_kind`, `verification` and `provenance`.
- Only `.subckt`, `.ends`, `.model`, and `.param`/`.func` inside a body are allowed.
- Each definition unit is emitted once per deck, keyed by sha256, dependencies first. Conflicting names are errors.
- Each `_models/` file needs a MANIFEST record with origin, source, license and a matching sha256. Since task 10, a path listed twice is refused.
- New param kind `points` (PWL); params also take `scale`, `min` and `max`.

**Catalog and UI:**
- 158 new packages.
- `docs/library.md` is generated, with a drift test.
- Search matches at word starts.
- Parts with no value show a type label.
- Simulation > Gear integration.

**Live mode:**
- Live (F6) with sliders for:
  - 14 sensor stimuli;
  - pots, switches, DC sources and batteries;
  - internal R/V/I values the derived rule finds (G35).
- A streaming plot with change ticks, pause (F7), Stop, a pace picker, and "Live for" (the stop time, a typed time, or Indefinite with a scrolling window and bounded memory).
- Replay as batch run.
- An edit stops the run.
- During a live run, cyclic gc runs only on the GUI thread (the 9.7 segfault fix).

## 3. Device families added
- **Task 3 (3):** OPAMP, OPAMP_DUAL, NTC_THERMISTOR.
- **Batch A (37):**
  - passives: pots, rheostat, resistor networks, toleranced R, L with DCR, coupled L, transformers (1:N and centre tap), 4 capacitor types, lossless line;
  - protection: fuse, PTC, MOV;
  - switches: SPDT, DPST, DPDT, pushbuttons, relays, reed, V/I-controlled switches;
  - sources: EXP/SFFM and pulse/sine current sources, E/F/G/H sources, clipping amplifier;
  - batteries: 3 cells.
- **Batch B (35):**
  - diodes: generic and named diodes, Zener, TVS, varactor, LEDs;
  - transistors: 9 named BJTs plus generics (incl. TIP120), 6 named VDMOS plus generic MOSFETs, 3 JFETs;
  - thyristors: SCR, TRIAC, DIAC, IGBT.
- **Batch C (25):**
  - op-amps: macro, LM358, TL072, MCP6002, LM324, LM741, plus an in-amp;
  - comparators: generic comparator, LM393, LM311;
  - timers: NE555/556;
  - references: TL431, LM4040;
  - regulators: 78xx, 79xx, LM317, LM337, AMS1117;
  - switches and drivers: CD4066, CD4051/52/53, LM386, ULN2003.
- **Batch D (31):**
  - 74HC/74LS/CD4000 gates, Schmitt triggers and buffers;
  - flip-flops and latches;
  - counters and shift registers;
  - muxes;
  - 8-bit ADC and R-2R DAC.
- **Batch E (25):**
  - sensors: light, temperature, Hall, force/strain/position, pressure, humidity;
  - acoustic: microphone, piezo;
  - current sensing: shunt, CT;
  - other: optocoupler, DC motor, solenoid, speaker, crystals, ceramic resonator.
- **Task 9.6 (2):** VSRC_PWL, ISRC_PWL.

## 4. Part counts (catalog)
- **Total:** 175 definitions (17 at 7a8606f, 158 new).
- **Simulated:** 170. The 5 interface-only parts are ESP32, Arduino Uno, SSD1306, DHT22 and the buzzer.
- **By category:** Electrical 147, Sensors 20, Power 4, Microcontrollers 2, Audio 1, Displays 1.
- **Top-level element:** X 121, D 10, Q 10, M 8, R 4, V 4, I 3, J 3, and C, L, T, G, E, S one each.
- **model_kind (new parts):** behavioral 62, equivalent 36, model 28, project-subckt 23, primitive 9.
- **Verification (new parts):** analytic 96, reference 58, smoke 4.
- **Status of X parts:** 80 full, 41 limited (30 digital/XSPICE, 9 B-driven sensors, MOV, SAT_AMP). T_LINE is also limited.
- **Definition text:** 119 distinct units (19 shared `_models/` files and 99 inline bodies), holding 142 top-level `.subckt`s and 7 top-level `.model`s.

## 5. Catalog entry vs device model vs subcircuit
- **Catalog entry:** one `component.toml` (id, pins, symbol, params, limitations), placed by the user. Several entries can share one definition: 4 capacitors share `bench_cap_esr`, and 7 op-amps share `bench_opamp_macro`.
- **Device model:** a `.model` card for a built-in element (D, Q, J, M, Z, S, ...). The 28 "model" parts are one element plus a card.
- **Subcircuit:** a `.subckt` body from a shared file, a package file or an inline body.
  - The part's element is X: its node fields map to ports in order, and its params are checked against the `.subckt` header at load.
  - The body is validated statically and emitted once per deck.

## 6. Model sources and licensing
- **No vendor models were copied.** No redistribution terms could be verified (G19), and vendor PSpice models need `ngbehavior=ps` per included file (G10).
- **Everything is project-authored and MIT.**
  - All 19 `_models/` files are `origin = "project"`.
  - Task 10 verified every record's license and sha256, and that each path has exactly one record.
- **Named parts are behavioural fits to datasheet typicals.**
  - Each provenance records the datasheet URL and revision.
  - Each part's first limitation says it is not a vendor model.
- **Two sources were not re-fetched:** the IEC 60751 RTD coefficients were written from memory (the tests check the standard table values), and LM35 datasheet SNIS159 was not fetched.
- KiCad's `Simulation_SPICE.sp` (CC-BY-SA-4.0) was not copied.

## 7. Tests run (task 10, final tree; real exit codes)
| Command | Result | rc |
|---|---|---|
| `(cd benchcore && cargo test; echo rc=$?)` | 80 passed (13 lib, 3 adder, 51 correctness, 13 live) | 0 |
| `(cd benchcore && python3 -m pytest -q tests/python; echo rc=$?)` | 23 passed | 0 |
| `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests; echo rc=$?`, run 1 | 1394 passed (545.75 s) | 0 |
| same, run 2 | 1394 passed (537.91 s) | 0 |
| `python3 -m app.catalog; echo rc=$?` | 175 definitions, 0 errors, 0 warnings | 0 |

- Branch-start baselines: app 480, cargo 35, py 5.
- Task 10 start: app 1390, rc 0.
- Task 10 added 4 app tests.
- No Rust changed in task 10, so the wheel was not rebuilt.

**G20 evidence:**
- Scan design: 600 runs (5 parameter sets × gains 1, 2, 5, 10 × steps 0.1 to 10 µs × 3 input steps × 2 supplies), each compared with a 5 ns reference.
- Old macro: 133 runs above 0.05 V error, up to 14.9 V. Under gear: 80.
- New macro: none above 0.05 V at steps up to 1 µs; 0.09 V at 2 µs, 0.2 V at 5 µs, 0.4 V at 10 µs.
- Slew rate within 1%.

## 8. Remaining limitations and open gaps
**Task 10 fixes:** G20, G43, G44, G45.

**GAPS totals:** 20 fixed, 7 wontfix, 15 deferred, 3 open.

**Open:**
- **G40:** a source that leaves a time parameter to ngspice's tstop default would follow an indefinite run's huge tstop. No shipped source does this.
- **G41:** the "Live for" field clips its text below a 1150 px window width.
- **G42:** a long live run keeps only its last window of points.

**Deferred:**
- **G17:** no AC analysis.
- **G10/G19:** no vendor models.
- **G21:** no switching regulators.
- **G24:** XSPICE flip-flops see one clock edge at power-up.
- **G25:** converters are fixed at 8 bits.
- **G27/G28:** humidity and solenoid position cannot be stimulus nodes.
- **G29:** B-driven sensors stay limited.
- **G9/G22:** no `nc` pins.
- **G34/G38:** a live chunk cannot be interrupted.
- **G35:** live params come from a derived rule.
- **G39:** live slider values are not written back.

**Unsupported (refused with a reason):**
- OSDI (N);
- M level 60 and other missing levels, Q levels 3/5/6/7/10+, D levels 2/4/5;
- LAPLACE, `if()`, `limit()`, `table()`;
- lossy LTRA past 2e4 steps;
- O/U/OSDI in live runs.

**Other limits:**
- Op-amp edges still overshoot by up to 0.4 V at 5 to 10 µs steps.
- The validator does not evaluate expression values, validates with default params plus each `select` choice, and does not prove well-posedness.
- Live runs can change only DC sources and resistors, one run at a time.

**Next steps:**
1. `.ac` analysis (G17).
2. A PSpice compatibility path plus a licence-checked vendor library (G10/G19).
3. A power-on gate for XSPICE sequential parts (G24).
4. `nc` pins (G9/G22).
5. An adaptive live chunk (G34).

## Addendum: gap closure, waves 1 to 3

Plan and per-workstream notes: `docs/plans/gap-closure/` (`PLAN.md`, `W1.md` to `W6.md`). Range 35e0a24..HEAD on
`feat/multi-terminal-transistors`. Wave 1 ran W1 to W4 in parallel, wave 2 ran W5 and W6, and wave 3 is this
integration review.

### What changed, per gap
- **G17 (W1), AC analysis.**
  - `.ac dec|oct|lin` runs end to end: the engine reads complex vectors, the document saves `analysis` and the AC
    sweep, and the results show a Bode view (dB and unwrapped phase on a log axis, with a log cursor).
  - Live runs and replays are refused in AC.
  - Device `@` currents are left out of AC results, because ngspice gives their operating-point value there.
- **G13 (W1), AC stimulus.** `ac`/`acphase` on V and I only. They are emitted in AC decks only, so transient decks
  and the golden netlists are byte-identical.
- **G34 and G38 (W2), live chunks.**
  - Chunks adapt to about 50 ms of wall time each.
  - Boundaries stay on an exact integer grid.
  - Cancel, New, Open and close wait at most one such chunk.
- **G35 (W2), live params.** The package key `live = [...]` overrides the derived live rule.
- **G39 (W2), Apply live values.** It writes the final slider values into the circuit as one undo step.
- **G40 (W2), tstop defaults.** Live decks and replays get the batch stop time's PULSE/SIN/SFFM defaults written in.
- **G41 (W2, then wave 3), top bar.**
  - Fixed in W2, then broken by the merge.
  - The fit is now measured per analysis, with the widest run-state text.
- **G42 (W2), run history.** A bounded min/max overview of the whole live run is kept, and put in front of the window
  when the run ends.
- **G27 and G28 (W3), sensor stimulus nodes.**
  - HUMIDITY_CAP is a charge-form capacitor (`q='C(stim)*V(a,b)'`).
  - SOLENOID uses a flux construction.
  - Both take their stimulus on a node, and both conserve charge or flux on a stimulus step.
- **G29 (W3), interval checks.**
  - The validator evaluates B, E/G VALUE and behavioural R/C/L expressions over each declared `[min, max]`.
  - It also runs a zero-first-iterate pass.
  - The 9 B-driven sensors, MOV and SAT_AMP moved from `limited` to `full`.
- **G9 and G22 (W4), `nc` pins.**
  - `nc` pins are drawn and wireable, but not netlisted.
  - LM741 gains null1/null2/nc8, and LM311 gains BALANCE/STROBE.
- **G24 (W4), power-on clock gate.** A `bench_dclk` gate is on every rising-edge sequential body (the falling-edge
  bodies were verified immune).
- **G25 (W4), converter widths.** ADC and DAC come at 4, 10 and 12 bits beside the 8-bit pair.
- **G10 (W5), PSpice-compat path.** With `compat = "ps"`, the package's files are `.include`d, and a per-deck marker
  selects `ngbehavior=ps`.
- **G19 (W5), vendor models: partial.** The licence policy and record format are written. No vendor file is shipped.
- **G21 (W6), averaged converters: partial.**
  - Averaged CCM buck and boost come with and without a duty input.
  - They are checked against the analytic DC ratio, efficiency, load step and AC transfer functions (including the
    boost RHP zero).

### Counts
- **Catalog:** 185 definitions (175 + 6 converter widths + 4 averaged converters), 180 simulated, 0 errors and 0
  warnings.
- **By category:** Electrical 157, Sensors 20, Power 4, Microcontrollers 2, Audio 1, Displays 1.
- **X parts:** 131. 98 are `full`. 33 are `limited`: the XSPICE digital and ADC parts, plus `T_LINE` outside X.
- **GAPS totals:** 36 fixed, 2 partially fixed, 7 wontfix, 0 deferred, 0 open. Before: 20 fixed, 7 wontfix,
  15 deferred, 3 open.
  - The 36 "fixed" include rows whose fix has a stated scope: G13 is V and I only, G25 is one package per width,
    and G29 covers the shipped parts.

### Remaining work
- **G19:** no vendor model file is shipped. The Analog Devices model licence is the open candidate. It needs a live
  read of the terms and an ADI-authored file shipped unmodified.
- **G21:** there is no named switcher IC, switching ripple, DCM or switching-loss model.
- **Scoped residuals:**
  - only V and I carry an AC stimulus;
  - AC does not show device currents;
  - noise, pole-zero and `.dc` sweeps are not available;
  - a power-on clock edge within about 5 ns of t = 0 is ignored;
  - one-sided params are interval-checked at their default only;
  - a `{expr}` time slot that evaluates to 0, and sources inside a ps `.include`d file, escape the G40 pin;
  - a param-driven converter width is wontfix.
- **"Next steps" in section 8:** items 1 to 5 are done, apart from the vendor library in item 2.

### Notable findings (ngspice 47, probed)
- **ps mode is not scoped to `.include`d files.** The manual and HANDOFF say it is, but `set ngbehavior=ps` also
  translates the main deck (`limit()` in a main-deck B source clamps under ps).
  - Twelve native expression forms, `.param` and a diode gave the same values in both modes. Opting a deck in is
    therefore safe for everything probed, but that guarantee is empirical, not structural.
  - Without ps, an included `LIMIT()` is silently 0: a wrong success, not an error.
- **Behavioural capacitance and inductance are not conservative.**
  - `C='expr'`, `L='expr'` and `flux=` act as C(t)·dv/dt and L(t)·di/dt, so they lose charge or flux on a stimulus
    step (0.5 V error in the probe). The validator now warns (B-CL-FORM).
  - `C ... q='C(stim)*V(a,b)'` conserves charge. So does the E/unit-C/H flux construction, to 3e-6 V at a 1 ns step.
- **First iterate.** Every analysis starts from an all-zero first iterate, so `sqrt(V(in)-4)` on a 5 V node is fatal.
- **Expression edge cases.** `ln(0)` is -1e99, `1/0` is 1e32 and `exp` saturates at 1e99, all silently.
- **Power-on clock edge.** The cause is `.tran ... uic` plus the input bridge delay, not a first-event rule. A gate
  that arms only after the clock has been seen at its idle level removes it, with no power-up event of its own.
- **AC.**
  - In an AC plot, device `@` vectors hold the operating-point value at every frequency.
  - `.ac lin 2` returns one point.
  - `fstart = 0` silently makes no AC plot.
  - The engine refuses all three cases.
- **`gnd` as a `.subckt` port name** is ngspice's global ground alias, so a port named `gnd` cannot be used (W6).

### Wave 3 review
- **Defect found (confirmed by measurement).** After the merges, the top bar overflowed and overlapped between 1024
  and 1566 px in both analyses, by up to about 170 px in AC at 1024 px.
  - Cause: W2's G41 thresholds were measured with the analysis chip hidden below 1520 px, and the merge keeps W1's
    analysis picker visible at every width.
  - W2's own fit test had become vacuous. It measured the children of `live_for.parentWidget()`, which after the
    merge is W1's small live box, not the bar.
  - Fix:
    - thresholds per analysis, measured with the widest state text;
    - the run-state badge shows its dot only below 1120 px (1080 in AC), with the state in its tooltip, and the
      file label hides there (the window title keeps it);
    - in AC, Live is hidden along with its duration field;
    - the AC fields share the compact width;
    - the sweep combo is narrower.
  - The test now checks the bar, and the run-settings and live boxes, at 25 widths in both analyses.
- **Checked, no defect:**
  - live in AC: refused in `LiveSession`, `replay_document`, the Live button and F6;
  - AC × W3: the charge and flux forms give the exact RC and RL corners;
  - AC × W5: a ps package runs in AC, and live (through the shared `do_circ`);
  - interval checks read `DC x AC m p` source lines correctly;
  - AC × W4: the LM741 with open `nc` pins gives a gain of -10, and digital parts run in AC with zero small-signal
    output;
  - W2's `live` lists against the W3, W4 and W6 packages;
  - save/load and undo of `analysis` and the AC sweep;
  - the G40 pin against `.include` and marker lines.
- **Documentation fixed:**
  - `docs/persistence.md` still called a non-transient analysis a bad file;
  - the `ngspice.rs` comment repeated the per-file ps claim.
- **Tests (final tree, real exit codes, wheel rebuilt from this tree):**

| Command | Result | rc |
|---|---|---|
| `(cd benchcore && cargo test; echo rc=$?)` | 100 passed (26 lib, 6 ac, 3 adder, 51 correctness, 14 live) | 0 |
| `(cd benchcore && python3 -m pytest -q tests/python; echo rc=$?)` | 28 passed | 0 |
| `python3 -m app.catalog; echo rc=$?` | 185 definitions, 0 errors, 0 warnings | 0 |
| `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests; echo rc=$?`, run 1 | 1644 passed (723.68 s) | 0 |
| same, run 2 | 1644 passed (720.77 s) | 0 |

- **Test count changes:**
  - Wave 3 baseline (830c4f5 plus ddf734f): 1601 app tests passed, rc 0. The task 10 count was 1394.
  - Wave 3 replaced the 10-width top-bar test with 25 widths × 2 analyses, and added 3 seam tests.
