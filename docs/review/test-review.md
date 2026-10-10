# Independent test review: does the suite prove the simulator works?

Date: 2026-10-06. Scope: `app/tests` (188 tests, 7.7 s, all green). Method: I read every test, classified each one, then
ran mutation testing on a copy (`/tmp/claude-1000/-home-jperez-Documents-Sim/review-copy`; `app` imported from the
copy through `conftest.py`, `benchcore` from the installed wheel and left unmutated). Originals were not modified.

## Verdict

**Mostly yes. The suite proves physics, not just that the UI exists, but with specific holes.** About 60 % of the
tests check numbers against closed-form oracles through the real Document -> `app.sim` -> benchcore -> ngspice path,
with no mocks. Tolerances are documented and measured, and permanent discrimination tests show that a 2 % error in R,
L or C fails them. GUI workflows (mouse build -> inspector -> Run -> viewer data vs analytic; save -> new window ->
load -> rerun bit-identical; run -> reset -> rerun) are real.

Of 23 injected bugs, the suite caught 17 and missed 6. Three of the misses give **silently wrong numbers with
`ok=True`**: two Ground symbols, `ic` dropped on save, and diode `Rs` ignored. Those three are the priority.

## 1. Classification

| File | Class | Notes |
|---|---|---|
| test_core_analytic.py | (a) numerical | Divider, RC, switch. **Divider oracle is partly circular:** it takes R and V from `d.check_param`, which is the same SI parser under test, and a ratio does not change when every value is scaled the same way. M01 (`meg` x0.1) passed every divider case. RC uses a hard-coded tau, so it is independent. |
| test_analytic_rlc.py | (a) | Strong: whole waveform, peak, period, decay, settling, LC energy, plus discrimination tests. Independent oracles. |
| test_analytic_diode.py | (a) | Shockley solved by bisection. **The oracle reads Is/N/Rs back from the emitted `.model` card, so it is circular for card emission.** M12 (Rs forced to 0) passed all 4 forward cases. The LED tests pin the literal card, so they are not affected. |
| test_persistence_roundtrip.py | (b) workflow + (a) | Bit-identical after reload, still analytic. Uses only `ic`=default, so it cannot see M07. Its `I(D1)` check has rel 1e-3, which is too loose to see Rs=0.568 (about 6e-4). |
| test_determinism.py | (b) | Exact equality, includes an in-between failing run and core reset. Good. |
| test_e2e.py | (a)+(b)+error paths | RC/IC/divider analytic; validator failures; GUI run/edit/save-load. `test_floating_pin` and `test_dangling_wire_part` only check `not ok`, which is weak but fine. |
| test_gui_workflow.py | (b) real GUI | Viewer data == `RunResult` series == analytic (0.5 mV); reset/rerun; save/new-window/load/rerun; undo; click-wire probe. Good. |
| test_led.py, test_led_e2e.py | (a)-lite / (b) | KVL and Ohm on the core's own values plus range bounds. This is self-consistency, not an oracle; the real LED oracle is in test_analytic_diode. |
| test_led_glow.py, test_led_pixels.py | (c)+display mapping | Glow and playback. They do read real currents, but they prove the display, not physics. |
| test_canvas.py, test_history.py | (c) plumbing | Editing and undo. Needed, but no physics. |
| test_components.py | (c) plus a string oracle | SI parsing table, catalog contract, **exact netlist string emission**. This is the only thing that catches the current-source direction (M21): no current source is ever simulated. |

No vacuous asserts, zero-iteration loops or unreachable asserts were found. Loops over series are guarded by length
checks (`len(xs) >= 6`, `len(t) > 500`).

## 2. Mutation results

| ID | Bug injected | Result | Caught by (examples) |
|---|---|---|---|
| M01 | `meg` -> 1e5 | caught (unit test only) | test_parse_si[1meg]; **divider 1meg/2.2M passed** |
| M02 | `n` -> 1e-8 | caught | test_rc_charge[2.2k-470n], persistence, parse_si |
| M03 | `G` -> 1e6 | caught (unit test only) | test_format_si[1e9] |
| M04 | only the first GND symbol is node 0 | **MISSED** | Confirmed: divider with 2 GNDs returns V(R2)=0.0 instead of 5.0, with ok=True |
| M05 | node numbering starts at n2 | caught | test_netlist_emission_all_kinds, test_led_example_run_and_picker |
| M06 | rotation counter-clockwise | caught | test_rotation_math, test_undo_redo_rotate_and_param_edit |
| M07 | save drops `ic` | **MISSED** | Confirmed: C ic=5 discharge gives max 5.0 V before save and 0.0 V after reload |
| M08 | load ignores tstep | caught | test_save_reload_simulates_identically..., test_save_new_window_load_rerun_identical |
| M09 | viewer time tick labels x0.1 | **MISSED** | Only the axis label "time (ms)" is asserted, never the tick text |
| M10 | viewer plots the first series, not the picked one | caught | test_rc_built_with_mouse_matches_analytic, test_click_wire_plots_its_node (6 tests) |
| M11 | switch state inverted | caught | test_switch_closed_and_open, GUI switch test |
| M12 | diode card ignores user Rs | **MISSED** | Confirmed: Rs=20 changes I(D1) 1.502 mA -> 1.744 mA (+16 %); the oracle reads the same card |
| M13 | LED card Rs 5 -> 50 | caught | test_led_card_is_what_the_oracle_assumes, test_led_example_current... (12 tests) |
| M14 | Reset does not clear results | caught | test_run_reset_rerun_is_deterministic |
| M15 | undo does not restore params | caught | test_history::test_param, test_undo_redo_rotate_and_param_edit |
| M16 | PULSE tr/tf swapped | **MISSED** | Every test uses tr == tf |
| M17 | SIN offset/amplitude swapped | **MISSED** | SIN is never simulated or emitted in any test |
| M18 | inductor `ic` ignored | **MISSED** (see note) | No test has a non-zero L initial current |
| M19 | `value_at` sample-and-hold | caught | test_rl_current_rise, test_rc_tolerance_detects... |
| M20 | tmax = tstop (solver step not limited) | caught | 21 tests: RLC, RC, LC, determinism |
| M21 | current source direction reversed | caught (string only) | test_netlist_emission_all_kinds; no physics test exists |
| M22 | unit alias `s` broken | **MISSED** (minor) | "5 ms"/"5 s" typed with a unit is never tested |
| M23 | netlist values rounded to 3 significant digits | caught | test_led_lit_threshold, emission tests |

Totals: 17 caught, 6 missed in the main table (M04, M07, M09, M16, M17, M18). M22 (minor) also passed the whole suite,
which brings it to 7 missed out of 23.

## 3. Coverage vs the milestone spec

- GUI reset -> rerun: **covered** (test_run_reset_rerun_is_deterministic). Note that `testing-strategy.md` still
  lists it as a known gap, which is out of date.
- Save -> reload -> simulate: **covered** at both the document and window level, but only with default-valued
  parameters (M07).
- Observable selection: **covered**. The viewer data is compared with `RunResult` and with the analytic result
  (M10 is caught).
- Error paths: validator errors and the core NO-DC-PATH case are covered. A real solver/convergence failure
  ("timestep too small") is not covered (already documented).
- Not simulated at all: current source (I), SIN source, PULSE timing (the blink test only checks that it is on or
  off), inductor IC, multiple grounds.
- A behaviour seen while reading the code, not tested and not a mutation: `MainWindow._key()` ignores tstop and
  tstep, so editing Stop time after a run does not mark the results as stale.

## 4. Test additions, in priority order

1. **Two Ground symbols** (M04): V - R - R with `V1.n` on GND2 and `R2.b` on GND1. Assert divider = analytic and
   `nets()` puts both on "0".
2. **Non-default parameter round-trip** (M07, M18): save -> load -> simulate C with ic=5 discharging through R
   (`5e^{-t/RC}`) and L with ic=10m through R (`I0 e^{-tR/L}`). Also assert `d2.to_dict()["components"] ==` the
   *typed* dict that was built, not `d.to_dict()`.
3. **Make the diode oracle independent of the card** (M12): use the parameters the test typed (`float("2")` for Rs)
   instead of parsing `nl["models"]`. Add an Rs-sensitive case (Rs=20 Ω at 1 V/100 Ω, where Rs moves I by 16 %) and
   tighten the persistence `I(D1)` check to rel 1e-4.
4. **Make the divider oracle independent of the SI parser** (M01, M03): hard-code the floats (1e6, 2.2e6) instead of
   calling `check_param`, and add an asymmetric case such as "1meg" with "1k" so a scaling bug changes the ratio.
5. **Source waveforms vs analytic:** PULSE with tr != tf into R, checking the rise and fall slopes and the 50 % times
   (M16); SIN into R, checking offset, amplitude and frequency from zero crossings (M17); a DC current source into R,
   checking V = I·R with the sign (M21 physics).
6. **Viewer axis values** (M09): render, read `ax.xaxis.get_major_formatter()(tick)` for a known tick and assert it
   matches `t·1e3` in ms. Optionally assert the cursor readout at a non-grid time against analytic.
7. A real convergence-failure path, and a stale-results check after a tstop edit.
