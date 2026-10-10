# Testing strategy (app layer)

Goal: the tests must prove the **simulator computes correct physics**, not that the UI/API exists. Every analytic
test runs the real path: `Document` (catalog, SI text) -> `app.sim.run_document` -> `benchcore.Simulation` ->
libngspice -> series, and compares against an oracle computed independently in the test. No mocks anywhere.

Run: `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests` (core: `cd benchcore && cargo test`).

## What is covered where

| File | Proves |
|---|---|
| `test_core_analytic.py` | divider (DC, every point), RC charge vs `5(1-e^{-t/RC})` + capacitor current, switch on/off, toggle determinism |
| `test_analytic_rlc.py` | series RLC step, underdamped: whole waveform, loop current, first peak time/value, damped half-period (zero crossings), decay ratio `e^{-alpha pi/wd}`, 2 % settling time, final value, `i(0+) = V t/L`; overdamped: waveform, monotonic/no overshoot, current peak time/value; RL `V/R(1-e^{-tR/L})` and `V_L`; lossless LC energy conservation, amplitude `V sqrt(C/L)`, phase |
| `test_analytic_diode.py` | diode forward current/drop vs Shockley+Rs+GMIN solved by bisection from the **emitted** `.model` card (4 cards); reverse leakage `-Is - GMIN V` and blocking; LED example `I = (Vs-Vf)/R` vs the LED card; LED lit threshold at +/-3 % of the analytic supply voltage |
| `test_persistence_roundtrip.py` | build -> save -> load into a fresh Document -> simulate: bit-identical series + still analytic; edit after reload; old v0 file simulates identically to a v1 build; corrupt/truncated/binary/empty files -> `ModelError("cannot open ...")`; bad value in file -> run fails with `[BAD-PARAM] C1 C ...` |
| `test_determinism.py` | rerun same doc, independent docs, other (and failing) runs in between, edit-and-revert, core `Simulation` run -> reset -> run: all bit-identical |
| `test_e2e.py`, `test_led*.py`, `test_canvas.py`, ... | GUI workflows (real mouse events, inspector, save/load in the window) on top of the same engine |

| `test_review_physics.py` | two Grounds = one node (divider 5 V); hard-coded divider values incl. 1meg/1k; capacitor IC 5 V and inductor IC 10 mA save -> load -> simulate vs `5e^{-t/RC}`, `I0 e^{-tR/L}`; diode vs Shockley using the values *typed* (incl. Rs = 20 ohm); PULSE with tr != tf, SIN offset vs amplitude, DC current source into R |
| `test_robustness.py` | 26 malformed files -> one `ModelError`; deep nesting, duplicate keys; safe part names on load and rename; run-size limit (points and samples) |
| `test_gui_review.py` | pending inspector edit committed by F5; Stop time/Time step edit marks result stale; Open failed dialog; SI run fields; inspector field sizes at 1300x850; time-axis tick text and cursor readout; a real ngspice "Timestep too small" failure -> state failed + details |
| `test_bjt_2n2222.py` | Q_2N2222 through Document -> netlist -> ngspice: golden netlist (pin order, model, save), common-emitter operating point vs KVL and the emitted card, collector-current attribution, saturation and cutoff, persistence and placeholder load, unwired base and wrong wiring diagnosed, GUI place-wire-run |
| `test_multiterminal_generalization.py` | test-local PNP, 4-pin MOSFET and JFET definitions (nothing shipped): PNP high-side KVL, 4-pin saturation, JFET default, unwired MOSFET gate -> NO-DC-PATH, one model name with different parameters -> error |

GUI Reset -> rerun is covered by `test_gui_workflow.py::test_run_reset_rerun_is_deterministic`.

## Oracles

- Closed forms: RC, RL, series RLC (both damping regimes), LC.
- Diode: ngspice's DC diode is `I = Is(exp(Vj/(N Vt)) - 1) + GMIN Vj`, `Vt = kT/q` at 300.15 K, `GMIN = 1e-12 S`,
  with series `Rs`. The test parses `Is/N/Rs` out of the `.model` line the app actually sends and solves KVL by
  bisection. GMIN matters only in reverse bias: with `Is = 1e-14` the reported -5 pA is GMIN, not leakage; a
  second case with `Is = 1e-9` shows `-Is` directly.

## Tolerances (measured first, then set with margin)

ngspice defaults: trapezoidal, reltol 1e-3, vntol 1 uV, abstol 1 pA; the app sets `tmax = tstep`.

| Check | Measured error | Tolerance | Why this size |
|---|---|---|---|
| RLC underdamped Vc (tstep = T_d/200) | 0.96 mV | 3 mV | trapezoidal LTE at 1 us steps; 2 % R/L/C error = 38-119 mV |
| RLC loop current | 31 uA (0.16 A peak) | 0.3 mA | same |
| Peak time / value | 0.13 us / 0.22 mV | 1 us / 2 mV | peak sampled on the solver grid (step <= 1 us) |
| Half period (5 averaged) | 7.5e-5 rel | 1e-3 rel | linear interpolation of crossings |
| Decay ratio per half period | 2.0e-4 rel | 2e-3 rel | sampled extrema |
| 2 % settling time | 1 step | 2 steps | grid quantisation |
| RLC overdamped Vc | 0.11 mV | 0.5 mV | 2 % C or R error = 37 mV |
| RL current (tau/100) | 0.15 uA of 50 mA | 2 uA | 2 % L error = 364 uA |
| LC energy | 4.3e-6 rel | 1e-4 rel | trapezoidal is energy-preserving |
| LC waveform phase | 12.4 mV | 25 mV | trapezoidal frequency warping `w0^3 h^2 t/12` predicts 13 mV |
| Diode / LED current | <= 1.3e-6 rel | 1e-4 rel | DC Newton-converged point; 2 % N or 2x Is = > 0.3 % |
| Persistence, determinism | 0 | exact | same text -> same deck -> deterministic ngspice |

## Discrimination (mutation sanity)

Tests that pass whatever the engine does are worthless, so:

- In-suite, permanent: `test_rlc_tolerance_detects_2_percent_error` (R, L, C +2 % in both regimes),
  `test_rc_tolerance_detects_2_percent_error` (R +/-2 %), `test_rl_tolerance_detects_2_percent_error`,
  `test_diode_tolerance_detects_model_change` (N +2 %, Is x2): the document is perturbed, the oracle is not, and the
  error must exceed the tolerance by 5-100x. These pass; they fail if a tolerance becomes meaningless.
- One-off (2026-10-05, not kept): copies of the tests with C +3 % / L +3 % in the simulated document failed
  `test_rlc_underdamped`, `test_rlc_overdamped` and `test_rl_current_rise`; a copy with the oracle's `Vt` +1 %
  failed all 4 diode-forward cases, both LED tests and the diode discrimination test.

## Not covered (known gaps)

- The solver-failure test injects a B-source latch deck through `Document.netlist` because the app's own parts cannot
  make ngspice fail to converge; the engine failure itself is real.
- No rename UI, so rename validation is tested at `Document.rename_part`.
- Diode breakdown, junction capacitance and temperature are not modelled by the app's cards (BV = inf, CJO = 0,
  TNOM only), so they are not tested.

## Mutation check of the review fixes (2026-10-06)

Each bug was injected into a copy of the source, the new tests run, then the source restored:

| Mutation | Failed tests |
|---|---|
| tr/tf swapped in the PULSE card | `test_pulse_unequal_rise_fall` |
| SIN offset/amplitude swapped | `test_sin_offset_vs_amplitude` |
| diode card ignores Rs (`Rs=0`) | `test_diode_uses_typed_values` (the two Rs = 20 cases) |
| `ic` dropped on save | `test_ic_roundtrip_capacitor_and_inductor` |
| time tick labels x10 | `test_time_axis_ticks_and_cursor_readout` (3 cases) |
| only the first Ground is node 0 | `test_two_grounds_are_one_node` (plus GUI errors from the crude mutation) |

Finding: in the current source the two-Ground and `ic`-on-save bugs reported as "SOURCE BUG" do **not** exist
(`nets()` already unions all Ground pins into node 0 and `to_dict` saves every param; the review's confirmations were
mutations in its copy). The tests now pin the correct behaviour.
