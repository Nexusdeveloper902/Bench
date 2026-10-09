# Simulation safety boundary spike: report

Date: 2026-10-04. Env: ngspice-47 (Arch 47-1, KLU, SPARSE 1.3),
libngspice.so.0.0.16, python 3.14.7 + numpy, Ryzen 5 5600G.
Prior work (`experiments/`, `exp-bexpr/`, `exp-numerical/`, `results/`,
`REPORT.md`) untouched; all new work isolated in `exp-safety/`.

Claim tags: **[E]** verified experimentally · **[D]** authoritative docs ·
**[I]** strong inference · **[U]** unresolved.

Repro: `REPRO.md` (≈9 min total). Raw results: `logs/*.json` (bench, events,
events2, events3, runtime_matrix, d4, advers).
Implementation: `validator.py` (~400 lines), `runtime.py`, `bsource.py`,
`helper_safety.py`. Regression: `tests/test_regression.py` (32 checks, ALL PASS).

## Answer

**A small, well-defined boundary exists and is implemented.** Static
validation rejects every malformed-topology class we could construct
(0 false negatives on 15 design cases + 21 adversarial probes + 300 fuzz
circuits, 0 false positives on 16 valids + 143 fuzz valids) **[E]**; the
runtime scan catches every loud ngspice failure and flags event-coverage and
cost pathologies **[E]**; the timestep policy `tmax ≤ min_width/10`
(`min_period/50` for sines) catches all tested B-source events including the
ones that defeat `/1` **[E]**. Two holes are fenced and documented rather
than closed: D4-class silence is invisible to every runtime signal and must
be refused statically; bare-`TIME` expressions with unquantifiable bandwidth
must demand an explicit user bound. No architectural change is required.

## 0. What was built (layer ownership)

| layer | file | job |
|---|---|---|
| component/package authoring | (user code) | physical intent: values, signs, series-vs-shunt topology; `NEG-R`, `SHORTED-PASSIVE`, `ZERO-R` point here |
| expression authoring | `thin.py` (prior spike) re-checked at netlist level | `B-SELFREF`, `B-IMPLICIT-NODE`, `B-DDT`, `B-DIVZERO`, `B-TIME-IN-OP`, `B-CYCLE` |
| static package validation | `validator.py` | `NO-DC-PATH`, `ISRC-OPEN`, `V-LOOP`, `NO-GROUND`, `DANGLING`, `MALFORMED`, `DUP-NAME`, `SHORTED-VSRC`, `OPAQUE-X`, `EXOTIC-DEVICE` — reject pre-sim |
| ngspice configuration | runtime-owned `.tran` 4th-arg | explicit `TMAX` from `bsource.py`; never rely on the `TSTEP` default |
| simulation runtime | `runtime.py` | log scan + health signals around every run |
| post-simulation analysis | `runtime.py` | `RAIL`, `EVENT-COVERAGE`, `COST-NPTS`, nonfinite |

Every validator finding carries its layer tag. Nothing lives in one giant
validator: model correctness (thin.py), topology correctness
(validator.py), numerical execution (ngspice + TMAX policy), result
validation (runtime.py) are separate, separately tested pieces.

## 1. Which topology errors can we reliably detect statically?

All of these, by graph reasoning over a DC-conductance model (R/L/V/E/Bv/H/D
conduct; C/I/G/Bi/F open; X optimistic + warn), with zero false positives
across 16 hand valids and 143 fuzz valids **[E]** (`logs/bench.json`,
`logs/advers.json`):

- floating nodes / missing DC path per node (`NO-DC-PATH`, BFS from ground)
- current-source-only nodes (`ISRC-OPEN`: D4, Bi-open, G-open — the silent
  class; M03/M04/M05/A06)
- current-source cutsets (M14: subcircuit tied only via Isrc/C — each
  pathless node flagged; dual of V-loop)
- voltage-source loops (`V-LOOP`, union-find over V/E/Bv/H/L/zero-R shorts:
  parallel, triple, via-L, via-E, via-Bv — M02/M13/A01–A04, incl.
  identical-value parallel V which is still singular, A12)
- shorted sources, no-ground decks, negative R/C/L, zero-R/C/L,
  duplicate names, malformed lines, dangling single-incidence nodes,
  B-expression implicit nodes (`V(typo)`), Bv self-reference, mutual B
  cycles (warn), opaque X / exotic kinds (warn).

Reliably detected = rejected pre-sim with the right code on every
constructed case (15/15 design + 21/21 adversarial probes at error-or-warn
level as designed) **[E]**.

## 2. Which failures still require actually running ngspice?

- **Convergence/multistability intent** (unseeded latch R-loud: validator
  warns `B-CYCLE` only; ngspice's `timestep too small`, rc=1 is the verdict)
  **[E]**. Static cannot decide whether a B-loop is a latch-awaiting-seed or
  a mistake — it flags for review, the run decides.
- **Component-value-dependent pathology** (F195/F197-style 1e18V quiet
  garbage on pathless-but-flagged circuits is moot pre-sim; for *valid*
  circuits, extreme-but-correct values like 10 kV / 1 T dividers only prove
  out by running) **[E]**.
- **Event capture itself** (no static rule can confirm a blip was sampled;
  only `dt_max ≤ required` post-sim proves coverage) **[E]**.
- **Exotic devices** (`EXOTIC-DEVICE` warn; ngspice rc≠0 was the verdict on
  the Z-probe A15) and **malformed-line semantics** (ngspice *ignores* bad
  lines, A16 — the validator's `MALFORMED` error is load-bearing because the
  sim would otherwise run a different circuit than intended) **[E]**.

## 3. Which runtime signals reliably indicate a bad simulation?

Qualified against 18 matrix cases (7 valid PASS, 9 invalid FAIL/
QUESTIONABLE, 0 HOLE) **[E]** (`logs/runtime_matrix.json`):

- **Reliable (fire on bad, never on valid):** rc≠0/timeout; fatal-log scan
  `singular|timestep too small|no convergence|stepping failed|DC solution
  failed|aborted` (every loud failure carries ≥1; zero valids carry any);
  `EVENT-COVERAGE` (dt_max > required×1.001 — the B2 miss; no false flag on
  the bounded sine A21 after the float-margin fix); `COST-NPTS` (>500 k —
  dense-output A2 only); `BLIND-SPOT` annotation (D4 pattern → explicit
  pass-with-warning, never mistaken for clean).
- **Reliable backstops, never needed on this corpus:** `NONFINITE`,
  `REJECT-RATE>0.5`, `ITER/PT>30` (benign worst observed: reject ratio
  ~0.02, iter/pt 2.0–2.2 — consistent with the prior spike; thresholds have
  >10× headroom, so they are tripwires, not tunables) **[E]**.
- **`RAIL`** (`peak > max(1e4, 1e6×max_source)`): fires on unbounded
  pos-feedback (1e13) alongside singular warnings; quiet on 10 kV, 1 T
  dividers, 18 V kickback, ring osc **[E]**. It is the only post-sim net for
  quiet-but-huge garbage (F195/F197 1e18-class, which the validator already
  refuses pre-sim).
- **Deliberately non-failing:** warnings without failure evidence
  (`LOUD-CONTEXT-ONLY`, e.g. R=0 substitution warning) → PASS with context;
  `B-DDT` → expression-layer warn only (peak=V/dt artifact documented, not a
  solver failure).

## 4. How should timestep bounds be derived from behavioral sources?

Scope: **B-sources containing TIME only**. PWL/SIN/PULSE independent sources
get solver breakpoints (control probe: 10 ns PWL blip caught at coarse tmax,
34 ms, 1039 pts) and need no bound **[E]** (`logs/events.json`).

- Parse each B expression: step/pulse widths from `u(TIME-a)` pairs,
  `pwl(TIME,…)` intervals, ternary thresholds; periods from
  `sin/cos(k·TIME)` coefficients (`bsource.py`). Take the min over all
  sources — the fast source governs (multi probe: slow-only 1 µs bound
  misses the 100 ns blip exactly like unbounded; 10 ns policy catches both)
  **[E]** (`logs/events2.json`).
- **Policy: `TMAX ≤ min_width/10` for events, `≤ min_period/50` for sines**
  (4th `.tran` arg — the `TSTEP` slot does NOT bound the solver; B2b-order
  trap re-verified) **[E]**:
  - blips 10 ns–10 µs: unbounded/`w/1` miss or skate (10/100 ns fully
    missed at 0.0/5 V, zero warnings); `w/10` catches 100%;
  - smooth zero-endpoint humps at `w/1` miss 4/6 sub-step offsets
    (peaks 0.6–3.9/5 V); `w/10` catches 6/6 (`logs/events3.json`). `/1` is
    placement luck (u-kinks sometimes trigger LTE refinement; smooth events
    don't) — this probe is the principled reason for `/10`, not margin
    theater;
  - sines 10 kHz–1 MHz: `/50` gives <0.3% amplitude error; `/20`–`/100`
    equivalent; `/10` shows ~1–5% sampling-level shortfall;
  - single edges need bounds for *timing*, not just capture: crossing error
    476 ns at TMAX=1 µs → 22 ns at 100 ns → 1 ns at 10 ns (≈TMAX-scale)
    **[E]** (`logs/events2.json`).
- **Unknown TIME dependence** (bare TIME arithmetic the parser cannot
  quantify): refuse a silent default — demand an explicit user TMAX (verdict
  `unknown`) **[I, enforced in code]**.
- Manual basis **[D]**: default TMAX = min(TSTEP, (TSTOP−TSTART)/50) — the
  "unbounded" runs were always TSTEP-bounded; the policy replaces an
  output-granularity accident with an event-bandwidth guarantee.

## 5. What is the computational cost of those safeguards?

- Static validation: **0.02 ms/run** (20-run mean, toy circuits) — free **[E]**.
- Runtime scan (regexes + stats + RAIL + EVENT): sub-ms on stored
  outputs — free **[E]**.
- The event bound is the only real cost, and it is output-step-driven
  (≈1 point per TMAX): 1 ms window ÷ 1 ns (10 ns event /10) = **1M pts /
  26 s**; 10 ms slow-charge window + 100 ns pulse = **1M pts / 27 s**;
  sine /50 ≈ 150–1500 pts / ≤50 ms **[E]**. Cost scales as
  `T_sim/w_min` — a fast event on a long window is 100–1000× a naive run.
- Mitigations (policy, not mechanism): split the sim (coarse window +
  refined event window), or restate the event as PWL/EXTERNAL with a
  breakpoint (prior-spike recipe stands) **[I]**. Do not "optimize" by
  loosening to /1: the hump probe proves /1 is luck.

## 6. What false positives/negatives remain?

- **False positives: 0** on 16 design valids + 143 fuzz valids + 7/7
  runtime-matrix valids **[E]**. Two were found and fixed during the spike
  (recorded as method, not hidden): Bi self-read flagged as algebraic loop
  (fixed: only Bv self-read is a loop; Bi self-read is a conductance —
  kickback circuit), and EVENT self-flag at float equality (fixed: 0.1%
  margin). Genuinely ambiguous: `DANGLING`/`ZERO-R`/`B-CYCLE`/`OPAQUE-X` are
  warns by design (unloaded Bv/E/H outputs are valid; R=0 simulates with a
  warning; seeded latches are valid) **[E]**.
- **False negatives: 0 dangerous.** 15/15 design + 21/21 adversarial probes
  flagged at the designed level **[E]**; 300-circuit fuzz with an independent
  numpy oracle: **0 SILENT-WRONG** **[E]**. Adjudicated non-issues: 6
  correct-rejects where numbers coincidentally agreed (sourceless 0 V;
  shared 1e18 regularization pathology — .op there is mathematically
  undefined, flagging is correct); F093's apparent mismatch was pure
  ground-reference offset (diffs identical to 4e-6, print precision).
- **Known static boundaries (documented, not pretended):** validator cannot
  decide B-loop intent (warn + runtime backstop); cannot see inside X
  (optimistic + warn, demands expansion/whitelist); cannot verify exotic
  kinds (warn, runtime load-bearing); RAIL cannot judge a circuit whose
  correct answer is legitimately huge (10 kV passes; a 1 MV design would
  need its scale declared — `max_source_scale` covers declared sources).

## 7. What exactly causes the D4 current-source/open-circuit behavior?

**A silent DC-solver artifact on a singular system — established, not
speculated** (probes P1–P11, each ≥3× deterministic, `logs/d4.json`):

- V=I×1 Ω over 6 decades (1 µA→1 µV … 10 A→10 V), sign-tracking, rc=0, zero
  warnings, batch **and** embedded identical (P11 re-verified in-process this
  spike: `EMB: 0.001`) — so NOT a measurement/parser/embedded artifact
  (Node-Voltage table + `print all` + device listing all agree) **[E]**.
- NOT gmin (1e-12…1e-3 unchanged), NOT gmin/source stepping (disabled:
  unchanged), NOT rshunt (default 0=disabled per manual **[D]**; any explicit
  rshunt=R *replaces* the phantom with I×R) **[E]**.
- The value is **solver-path-dependent**: `noopiter+gminsteps=0+srcsteps=0`
  gives 107 mV instead of 1 mV, still silent — a singular system has no
  unique solution, and the 1 mV is whatever the sparse pivot path produces
  (per-node 1 Ω to ground: two-node pair gives ±1 mV = I×2 Ω) **[E]**.
- **Silence is unique to current-source-only floating nodes** (I, Bi, G):
  V-source opens fail loudly (rc=1 singular); I+R-to-second-float, I+C,
  R-only, C-only, I‖R floats all fail loudly (1e9–1e18 V with gmin/stepping
  warnings) **[E]**. In `.tran UIC` the same net *ramps* (≈2 mV→1 V over
  1 ms) instead of sitting at 1 mV — the phantom is a DC-solver artifact,
  not a stamped device **[E]**.
- Exact C line (which pivot repair in SPARSE/KLU inserts the 1.0) **not
  traced to source** — marked **[U]** at line level; mechanism class
  (simulator singular-matrix silent repair) established beyond reasonable
  doubt by elimination + path-dependence. No user code can distinguish the
  phantom from 1 mV of real signal — hence static refusal is the *only*
  mitigation, and the runtime explicitly annotates its blindness.

## 8. Which safeguards are mandatory for v0.1?

1. `validator.py` error classes as pre-sim gates: `NO-DC-PATH`,
   `ISRC-OPEN`, `V-LOOP`, `NO-GROUND`, `B-SELFREF` (Bv),
   `B-IMPLICIT-NODE`, `NEG-R/CL`, `SHORTED-VSRC`, `MALFORMED`,
   `DUP-NAME` — reject, no override without review.
2. `bsource.py` bound: every TIME-bearing B-source gets explicit 4th-arg
   `TMAX ≤ min_width/10` (`/50` sines); unquantifiable TIME → require
   explicit user TMAX, never default.
3. `runtime.py` post-sim gate on every run: rc/timeout, fatal-log scan,
   nonfinite, `EVENT-COVERAGE`, `RAIL`, reject/iter tripwires, NPTS sanity.
4. `tests/test_regression.py` in CI (32 checks; exit≠0 blocks).

## 9. Which safeguards are useful but optional?

Warns (`DANGLING`, `ZERO-R`, `B-DDT`, `B-TIME-IN-OP`, `B-CYCLE`,
`OPAQUE-X`, `EXOTIC-DEVICE`, `SHORTED-PASSIVE`, `BAD-VALUE`): log + human
review, proceed. `COST-NPTS` flag: proceed, but fix output granularity
(coarse TSTEP + event windows) before scaling. `B-CYCLE` seeded-latch review
checklist (`.ic` + state caps + both-states-reachable test, cf. prior C3b).

## 10. What remains unresolved?

- D4 exact source line (pivot-repair site) **[U]** — irrelevant to
  mitigation (refuse statically regardless).
- Nonlinear-stiff tolerance sensitivity; multi-tone intermodulation
  accuracy; hidden-state continuation gating (inherited unknowns, prior
  reports §11) **[U]**.
- `TSTEP`-vs-`TMAX` interplay under `nostepsizelimit` (removes the TSTEP cap
  — strictly worse for B-events; forbid it wherever B-sources exist) **[I]**.
- RAIL scale declaration for legitimately huge-signal designs **[I]**.

## 11. Does anything here require an architectural change?

**No.** Batch findings transfer to embedded use (tanh bit-identical prior;
D4 silence identical then and now) **[E]**. The boundary is three small
files + a 4th-arg TMAX + a regression gate — no solver, controller, IR, or
tolerance changes (global tightening already disproven) **[E]**. The one
growth surface is the B-expression allowlist (prior spike), untouched here.

## Recommended next spike

**Continuation-aware event windowing**: the dominant cost found here is
`T_sim/w_min` (27 s for one 100 ns pulse on a 10 ms charge). Investigate
segmented simulation — coarse run → refined window around known B-event times
(`.ic`+`uic` continuation was validated only for memoryless-plus-C state;
B-source/L/OSDI hidden state is the gating unknown) — versus restating fast
B-events as PWL/EXTERNAL+breakpoint sources. Success criterion: 100 ns event
on 10 ms dynamics captured at ≤5% of today's 1M-point cost with analytic
agreement. Second priority (cheap): trace the D4 pivot-repair site in the
ngspice/SPARSE sources to convert the **[U]** into a **[D]**.
