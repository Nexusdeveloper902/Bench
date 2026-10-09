# Numerical robustness & convergence spike: report

Date: 2026-10-04. Env: ngspice-47 (Arch 47-1, KLU, SPARSE 1.3),
libngspice.so.0.0.16, python 3.14.7 + numpy/scipy, Ryzen 5 5600G.
Prior work (`experiments/`, `exp-bexpr/`, `REPORT.md`) untouched; all new work
isolated in `exp-numerical/`.

Claim tags: **[E]** verified experimentally · **[D]** authoritative docs ·
**[I]** strong inference · **[U]** unresolved.

Repro: `cd exp-numerical && python3 run_all.py` (~75 s, 39 records) then
`python3 advers_verify.py` (~30 s). See `REPRO.md`.
Raw results: `logs/num_results.json`, `logs/advers_verify.json`.

## Answer

**The current approach (ngspice, batch or embedded) is reliable over a wide
envelope — stiff, dynamic, nonlinear, oscillatory, long, extreme-valued —
and every hard failure we could induce is loud *except two silent ones*.**
No architectural change is justified. What is justified: (a) topology checks
in the package/component layer, (b) a step-bound policy + warning scan in the
simulation runtime. Details below.

## 1. What was tested (39 records, `run_all.py`)

| group | cases | reference |
|---|---|---|
| A linear/stiff | nominal RC, stiff pair 1e4 (dense + coarse output), two-tone fast-on-slow, LC tank, 1 MHz sine, 100-tau run | closed form / \|H\| / f0 |
| B sharp/nonlinear | 1 ps edge, 10 ns B-blip (±tmax), comparator ramp, ddt(step), diode sweep, tanh | analytic / bisection / math |
| C feedback | E-amp gain 2, network Schmitt, latch unseeded/seeded, self-ref B, unbounded pos-feedback | topology-correct expectation |
| D singular/extreme | floating node, weak tie, V-loop, Isrc-open, cap loop (±gmin), dividers 1k/1T both ways, 10 kV, 10 H / 1 F | analytic / loudness |
| E tolerances | stiff trio reltol 1e-2/1e-3/1e-6, R-sweep ×2, transient ×3 | analytic / determinism |
| F dynamics | ring oscillator, interrupted-inductor kickback, 5-stage RC ladder | oscillation criteria / tight-ref |

Instrumentation per tran case: wall time, npts, dt_min/max/ratio, accepted/
rejected steps, iterations/point (via `.options acct` + `rusage all`), full
series via `wrdata`, warning/fatal regex scan. Every sim is a subprocess with
timeout — one pathological case cannot kill the campaign **[E]**.

## 2. Results by class (spec taxonomy + orthogonal `loud` flag)

- **Reliable: 29.** Includes all of group A (stiff err ≤ 1.3e-3 abs on 5 V;
  LC ferr 8.3e-5, decay 3.9e-6; 1 MHz amplitude exact to 4 digits; 100-tau
  drift 0), diode/tanh vs independent refs, E-amp loop, Schmitt, seeded-latch
  bistability (5,0)/(0,5) both stable, all D-extreme values (5 nV–5 kV,
  10 H/1 F), R-sweep (maxerr 4.6e-7, repeat spread 0), transient repeats
  bit-identical (spread 0.0) **[E]**, ring osc (66 switches, period stable),
  kickback bounded (18.7 V peak, finite), ladder self-convergence 1e-4 **[E]**.
- **Questionable: 1 (B4).** `ddt()` of a step converges and conserves impulse
  area, but peak = 5 V/dt exactly (5e5/5e6/5e7 at tmax 10u/1u/100n **[E]**) —
  a pure timestep artifact around a discontinuity. Must never be read as a
  physical voltage.
- **Failed: 7 — all LOUD (warnings/fatals and/or rc≠0) [E].** Unseeded latch
  (`timestep too small`, rc=1), self-ref B (−5e14 **with** `singular matrix`
  warnings), unbounded pos-feedback (1e13 with singular warnings), floating
  node, V-loop (rc=1), cap-loop .op (arbitrary 4.95 V **with** singular
  warnings; gmin 1e-9 gives the identical value — determinism ≠ correctness).
- **Silent failure: 2 [E, each reproduced ≥3× deterministic].**
  1. **B2 — 10 ns B-source blip stepped over entirely** (peak 0.0000/5 V,
     dtmax 1 µs, zero warnings). TIME-dependent B expressions get no
     breakpoints. Bound `tmax=5n` (correct `.tran` arg position) catches it
     fully (peak 5.0, B2b) at 200 kpts cost **[E]**.
  2. **D4 — current source into open returns V = I × 1 Ω** (1 mA→1 mV,
     1 A→1 V, 1 µA→1 µV), rc=0, **zero warnings** in batch *and* embedded
     libngspice **[E]**. Plausible-looking millivolts for an undefined
     circuit — the most dangerous finding in this spike. Mechanism **[U]**.

## 3. Adversarial record (results I first got wrong, then fixed)

rev1 mislabeled 4 cases; the sim was right every time: C1 topology didn't
close the loop (replaced with E-amp, exact); D6a/D8L references were
algebraically wrong (fixed, both reliable); C2 read `V(out)` directly
(algebraic loop — replaced with network-closed Schmitt, works); B2b put
`tmax` in the wrong `.tran` arg slot (fixed, mitigation verified). A2's 500 k
points were output-density (TSTEP grid), not solver need: A2b with coarse
output proves LTE grows steps 196 ns→1 ms (ratio 5102, 68 pts, 1 rejected,
9 ms) **[E]**. Stiffness costs nothing unless you force dense output.

Independent verification (`advers_verify.py`): scipy BDF/Radau reproduce
ngspice to 1.5e-5/1.3e-3 **[E]**; damped Newton confirms the 0.59 mV D-model
offset is device physics (high-level injection region), not solver error
**[E]**; tanh embed==batch to print precision (embed keeps full double)
**[E]**; all 5 failure cases deterministic 3/3 **[E]**.

## 4. Sensitivity findings

- **Tolerances:** reltol 1e-2/1e-3/1e-6 barely moves smooth-circuit results
  (fast-node err 1.26e-3 → 0.85e-3) **[E]**. No global tightening justified.
  (Nonlinear-stiff sensitivity untested **[U]**.)
- **Initial conditions:** decide latch outcomes deterministically (.ic seeds →
  both states reachable and stable) **[E]** — multistability, not fragility.
- **Component values:** 1e12-ratio dividers, 10 H, 1 F, 10 kV all exact
  **[E]**; DC resolves ~abstol·R (≈5 nV here) **[I]**.
- **Timestep:** LTE spans 3+ decades automatically; ~2 iter/pt everywhere
  benign (2.0–2.2; Schmitt 2.02 with 7 rejected) **[E]**. Rejected-rate and
  iter/pt never approached alarming levels in any converging case.
- **Auto-detection works:** a prototype classifier using only rc + fatal
  patterns + err-vs-tol + reject-rate + iter/pt + point-count reproduces all
  manual *correctness* classes 39/39 (B4 conservative: auto says reliable;
  6 dense-output runs additionally cost-flagged) **[E]**.

## 5. Verdicts per failure (reality check vs intended workload)

| failure | workload relevance | handled by |
|---|---|---|
| B2 blip miss | **relevant** (MCU edges, sensor spikes via B-sources) | **runtime**: bound tmax by fastest B-source bandwidth; embedded breakpoints where available (prior spike recipe stands) |
| D4 Isrc-open silence | **relevant** (authoring slip: unconnected current port) | **validator**: static check — every current-source terminal needs a DC path; plus runtime warn-scan can never catch this one, so the validator is load-bearing |
| C4 self-ref B | relevant (behavioral authoring) | already refused by `thin.py` pre-sim **[E, prior spike]**; runtime warn-scan as backstop |
| C3/C5/D1/D3/D5 loud failures | relevant as authoring slips | **validator** (connectivity, V-loops, DC-path-per-node) + **runtime** warn/fatal scan (they are reliably loud — scan `singular|timestep|no convergence|stepping failed`) |
| B4 ddt artifact | relevant (derivative models) | **component-layer** rule: never differentiate a discontinuity; document impulse-area behavior |
| A2-style point explosion | relevant at scale | **runtime**: default coarse TSTEP + tighten only around events of interest (cost is output-driven, not solver-driven) |

## 6. What is justified / not justified

- **Justified:** (1) package-validator topology checks (float nodes, V-loops,
  Isrc cutsets, DC-path-per-node — all statically detectable) **[I]**;
  (2) runtime post-sim scan: nonfinite check + `singular|timestep|no
  convergence|stepping failed` + rail heuristic (|v| > 1e6 × max source)
  **[E]**; (3) B-source bandwidth → tmax policy in the runtime **[E]**;
  (4) regression tests for B2, D4, C4, D5 (cases already in `run_all.py`).
- **Not justified:** custom solver, timestep controller, numerical IR,
  global tolerance tightening, OSDI/B-source changes — ngspice-47 handles
  everything tested that is well-posed **[E]**.
- **Architecture:** nothing must change. Batch findings reproduce exactly in
  embedded libngspice (tanh bit-identical, D4 silence identical), so batch
  benchmarking transfers to the intended embedded use **[E]**.

## 7. Next spike

Nonlinear-stiff tolerance sensitivity (diode/B-source inside stiff loops);
multi-tone + feedback intermodulation accuracy; MCU-in-the-loop timing
pressure (1 MHz lesson from prior spike × step-bound cost here); D4-mechanism
root cause from ngspice sources **[U]**; validator prototype implementing §5
row 1–3 checks against these regression cases.

## Files

`helper_num.py` (contained runner + `wrdata`/`rusage` instrumentation +
classifier helpers) · `run_all.py` (39 cases + rev2 fix log in header) ·
`advers_verify.py` (scipy/Newton/determinism/xcheck/classifier) ·
`logs/num_results.json` · `logs/advers_verify.json` · `REPRO.md` ·
`REPORT.md` (this file).
