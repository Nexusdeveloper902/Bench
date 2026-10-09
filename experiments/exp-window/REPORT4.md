# Continuation safety-boundary closure, spike 4: resonant bound + state correctness

Date: 2026-10-05. Env: ngspice-47 batch (subprocess + timeout), Ryzen 5 5600G.
Changed files: `exp-window/window.py` (G4 resonant gate only),
`exp-window/tests/test_hardening.py` (h17 period supply + new h23 only).
All prior suites green after the change:
`exp-safety/tests/test_regression.py` ALL PASS,
`exp-window/tests/test_window.py` ALL PASS,
`exp-window/tests/test_hardening.py` ALL PASS (incl. new h23).
Raw data: `logs/spike4_{resonant,pulse,damped_sup,seam,k,ddt}.json`
(scripts that produced them: `/tmp/opencode/part{1,2,3,4}_*.py`).

Tags: **[E]** measured · **[D]** docs/interface · **[I]** strong inference ·
**[U]** unresolved.

Three parallel lanes + main-thread verification (every load-bearing number
below was re-read from the lane JSONs by the main thread; Part 1 phase table
and Part 2 corrupt-split numbers reproduced exactly from raw data).

## 0. Verdict

One genuinely new silent PASS-but-wrong class was found and closed (H12
resonant long-phase, G4, fix = fail-closed refusal without a user period).
Everything else the lanes probed confirmed the existing boundary: no other
gate change was justified, and none was made. The safety model is now stated
as per-class invariants (§6), not as a test corpus.

## 1. Resonant coarse-step adequacy (Part 1) — CLOSED as USER-SUPPLIED BOUND

Family: parallel-RLC tanks (1mH/1uF f0≈5.03kHz T0≈198.7us; 10uH/100nF;
1mH/10nF), Q ∈ {∞,100,50,20,5,2,1,0.7,0.4}, two-tone f+3f / f+20f, SIN@f0,
B-pulse injection (w=T0/100,T0/10 × 10%,100% energy), 5/20/100-period runs.
Refs: 4-level ladder T0/200→T0/2000 (ideal deltas 5.0e-6→3.3e-7→6.8e-8,
clean 2nd-order); analytic decay E0·e^(−T/RpC) matched to 0.003–0.05%.
Segmented runs used quiet mid-seams with coarse=fine to isolate seam error.

| N=T0/tmax | phase/period | amplitude (late) | energy drift | 100-per accumulation |
|---|---|---|---|---|
| 10 | 13.2° | −0.13% | − | — |
| 20 | 3.42° | −0.30% | − | — |
| 50 | 0.56° | −0.06% | −0.007% | **46.7° at amp −0.14%** |
| 100 | 0.14° | −0.006% | −0.002% | 11.3° |
| 200 | 0.035° | −0.001% | −0.0004% | 2.5° |

Findings [E]:
- N≈50 is the Q-independent, L/C-independent knee (~0.5°/period, <0.1%
  local amplitude, 2nd-order 4×/doubling) for Q≳2. Damping does NOT change
  the grid requirement; Q≤1 needs only N≈10–20 on the early envelope.
- **Accumulation is decisive**: per-period error is length-independent, so
  total phase = per-period × periods. 100 periods at N=50: 46.7° with
  amplitude "passing" (−0.14%). Local-amplitude vs accumulated-phase
  dissociation is the central result — no fixed T0/N bounds total phase.
- Seam innocent everywhere: seg-vs-continuous 10–100× below grid error.
- Cheap syntactic bounds are 2.5–300× over-tight (tau_min/1000 down to
  T0/15708; L/R/100 demands 200ps-class on the 159kHz tank vs needed T0/50);
  T0 is not auto-extractable (coupled/multi-rate need min-T0); T0/50 coarse
  silently steps over a T0/100 pulse (+99% post-peak; B-form refused via G6,
  correctly — firewall, not failure).
- Hence **automatic coarse continuation for resonant L is REFUSED**; with a
  user-supplied min period the /50 bound (tightenable to /100, /200 from the
  table) is enforced. Code: `detect_resonant()` (L sharing non-ground node
  with C — same incidence as the old tau_L skip) + `resonant_period`
  parameter, `RES_DIV=50.0`. Missing/non-positive period → G4 REFUSE;
  coarse/fine beyond period/50 → G4 REFUSE.

## 2. Observable/state correctness (Part 2) — ANSWERED, no gate change

Definitions: **completeness** = every state var handed off (G5-1st-half +
G1-K); **observability** = every handoff var in vecs (G5-2nd-half; L currents
auto-added to sim vecs); **correctness** = values right (G5 jumps,
REL=1e-3/ABS=1e-6).

Evidence [E]: series caps — omission REFUSEs (S8 ghost rule), handoff∉vecs
REFUSEs, +1V hidden-split corruption REFUSEs on the hidden var while the
terminal jumps only 3.4µV (terminal-only checking would go silent);
parallel-L same-total split swap (±50mA, terminal divergence 1e-12 exactly)
REFUSEs on currents; K-L2 corruption REFUSEs on current; zero-cross
current sign-flip (energy invisible in voltage) REFUSEs on current;
weak-node same pattern; slewing/fast seams produce fail-closed false
REFUSEs (availability, e.g. slope×dt_first≈1mV boundary — interpolation
would fix, not done).

**Explicit answer**: for every supported class (R/C/plain-D/memoryless/L/K)
there is **no class where a large wrong internal state passes all checks**.
Omission always REFUSEs (completeness/G1-K); large corruption always REFUSEs
(jump on the hidden var or its current). The residual gap is **sub-threshold
correctness**: errors below max(1µV, 0.1%·max(1,|prev|)) PASS-but-bounded
(demo: +2mV persists 2mV to final; +0.5mA likewise) — an explicit tolerance
tradeoff, error bounded by the threshold, not silent-catastrophic.
Tightening it further trades directly against mesh-phase availability, so no
change was made (cf. "do NOT add arbitrary state comparison").

## 3. K-coupled transfer (Part 3) — SAFE WITH EXPLICIT HANDOFF + 2 conditions

Matrix [E]: 2-winding k=0.01…1.0, k=−0.9, 3-winding single-line K, reversed
winding, unequal L (1mH/10mH), unequal R, floating unloaded, floating via
10Meg, one-winding vs distributed energy, resonant LC per winding — both-IC
PASS in all; missing-1-IC REFUSEs (G1, stronger than G5); 200mA corruption
REFUSEs / 0.5mA bounded-PASS (same G5 tolerance as §2).

"Every winding IC + UIC" is sufficient in all tested topologies. Two
additional conditions (both already handled, documented here):
- Asymmetric loading: timestep must satisfy the FASTEST winding's tau_L/100
  (estimator already takes min — no code change).
- Floating winding via large shunt R: estimator pessimism (max-R → ps bound)
  availability-REFUSEs converged grids; fail-closed direction, model unloaded
  windings as no-R. No support expansion.

## 4. B ddt() (Part 4) — REFUSED, no relaxation

Perfect-handoff probes [E]: dropout = 1.0000× local |d/dt| for exactly 2
solver micro-steps then tracking (tmax-, METHOD-, reltol-independent → solver
micro-state, not settable: `show` exposes branch current only, no history);
`idt()` reconstructive primitive does not exist; K≥5 refill re-simulation is
exact but is re-simulation, not handoff, and does not generalize (B→R→C:
2-step dropout deposits 0.38µC → +3.7V permanent offset, G5-blind downstream);
`ddt(ddt())` migrates/amplifies error (delayed full-peak spike at step 3).
**Missing invariant**: past state-vector values + integrator step/order
memory — no `.ic`/`.save` interface, model- and mesh-dependent. Keep G1 DENY.

## 5. OSDI (Part 5) — REFUSED (interface-level)

No OpenVAF binary in env (stale ENV path), no OSDI `.so`; this ngspice-47
build does not implement `.osdi` (`unimplemented dot command`, rc=1), so
continuation is moot here. Even where supported, compiled-model hidden state
(integration states, ddt/idt memory, event/hysteresis registers) has no
enumerate/restore API and is model-specific opaque — a tested memoryless
diode would prove nothing. **Missing invariant**: compiled-model internal
state vector. Permanently REFUSED unless the interface exposes save/restore.

## 6. Safety model (Part 6) — per-class invariants

SAFE classes (each: complete state / transfer / TIME transform / step bound /
discovery assumption / seam checks / why hidden-wrong cannot survive):
- **R/C/plain-D/memoryless B/E/G/H/F + shifted V-PWL/PULSE/SIN + B
  u-pulse/ternary/bare-pwl(TIME)**: state = C node voltages only; transfer =
  `.ic`+UIC (C-IC stripped iff `.ic` present; seg-0 preserves base `.ic`);
  TIME shifted per segment (−t_start; comparisons, pwl-coords, PWL pairs,
  PULSE modular-mid-train, SIN phase), residual TIME REFUSEs; bounds =
  event w/10 + tau_min/1000 + SIN period/50; discovery = quantified B events
  statically contained in fine windows, everything else needs user windows;
  seam = G5 all-vecs continuity + G6 no-`.ic`-on-driven; hidden-wrong cannot
  survive: no hidden state exists in this class (plain-D verified storageless;
  every C node is handed off AND observed, §2 demos).
- **Non-resonant L**: + state adds each winding current; transfer adds device
  `IC=`+UIC; bound adds tau_L/100; omission/corruption REFUSE via
  completeness/current-jump (§2b/d).
- **K, all windings**: + every-winding IC handoff enforced at G1 (before G5);
  same bounds + fastest-winding condition (§3).
- **Resonant L/LC — USER-SUPPLIED BOUND REQUIRED**: as above + user min
  period enforced as tmax≤period/50 (tighten per table for long runs); pulses
  additionally need w/10; without period → REFUSE (H12).

REFUSED classes with exact missing invariant: D-TT/CJO (junction/transit
charge, no restore path), Q/M/J (base/channel charge), A (int_ic opaque),
B-ddt/sin/bare-TIME/precedence-tail/non-bare-pwl (unshiftable history/phase),
behav-R/C TIME, E/G/H/F TIME, TIME-bearing `.func`, `.include`/`.lib`
(invisible content), T (in-flight state), K-partial, OSDI/X/O/U/Y/N/S/W/Z,
E/G-LAPLACE/FREQ, param SIN, damped/delayed-SIN interiors, overlap/edge
windows, events-in-coarse, any bound excess, incomplete handoff, `.ic` on
driven nodes.

Residual **[U]**: G5 mesh-phase availability (false REFUSEs on fast slewing/
zero-cross seams — availability only, interpolation fix deferred, never
silent); sub-threshold bounded tolerance (explicit, §2).

## 7. Regression/adversarial closure (Part 7)

- H12 (new, from this spike's Part 1 lane): ideal-LC 100-period N=50
  PASS-with-46.7°-drift — minimized (single LC, quiet seam, coarse=fine),
  reproduced from lane JSON, attributed to G4 (no resonant bound),
  fixed by smallest fail-closed correction (resonant REFUSE without user
  period; /50 enforcement with it), regression h23 added, full suites rerun
  green. No support broadened (resonant narrowed from auto-PASS).
- No other new PASS-but-wrong: seam/K lane produced only REFUSEs +
  bounded-sub-threshold (documented tolerance, not a hole); ddt lane
  confirmed REFUSE; OSDI interface-level REFUSE. "No hole found" in those
  lanes is corpus evidence, not proof — recorded as such; the invariants in
  §6 are what the boundary rests on.

## Final classification

- Resonant systems: **USER-SUPPLIED BOUND REQUIRED** (auto-coarse REFUSED;
  with min period enforced as tmax≤period/50, tightenable per table).
- K-coupled: **SAFE WITH EXPLICIT HANDOFF** (+ fastest-winding tau_L/100;
  floating-shunt estimator pessimism is availability-only).
- B ddt(): **REFUSED**.
- OSDI: **REFUSED**.
- Observable/state correctness: existing gates guarantee **completeness
  exactly and correctness up to the explicit G5 tolerance** for every
  supported class; **no class admits a large wrong hidden state through all
  checks** (§2 evidence). Sub-threshold bounded error + availability
  false-REFUSEs are the documented residuals.
