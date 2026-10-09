# Continuation safety-boundary validation, spike 3: systematic attack campaign

Date: 2026-10-04/05. Env: ngspice-47 batch (subprocess + timeout per call),
Ryzen 5 5600G. Prior suites untouched and green:
`exp-safety/tests/test_regression.py` ALL PASS,
`exp-window/tests/test_window.py` ALL PASS,
`exp-window/tests/test_hardening.py` ALL PASS (30 old + 23 new checks).
Changed files: `exp-window/window.py` (gates only),
`exp-safety/validator.py` (`+`-continuation join only),
`exp-window/tests/test_hardening.py` (new h18–h22 only).

Tags: **[E]** measured · **[D]** docs/manual/code-reading · **[I]** strong
inference · **[U]** unresolved · **[C]** contradicted.

Threads: A differential corpus (70 cases + convergence ladders + analytic RC),
B TIME-expression fuzz (286 python + 17 ngspice reps),
C metamorphic (34) + event discovery (23),
main-thread independent reproduction of every claimed hole before fixing.
Raw data + scripts: `/tmp/opencode/exp3/`
(`threadA_diff.py/results.json`, `threadB_time.py/results.json`,
`threadC_meta.py/_events.py/results.json`, `spike3_probes.py`).

## 0. Verdict: BOUNDARY BREACHED, then re-closed (outcome B)

The campaign found **eleven new silent PASS-but-wrong classes** (eight from
parallel threads, three from TIME fuzz), each minimized, each traced to a
specific gate, each fixed by the smallest fail-closed mechanism, each with a
regression check (h18–h22), full campaign re-run green. No supported model
class was expanded: every fix is a refusal or a refusal-preserving
narrowing. No new state-transfer abstraction was needed — the existing
G1–G6 vocabulary, plus two timestep invariants the experiments forced
(continuous-SIN whole-sim bound, non-resonant inductive bound), expresses
every required condition [I].

## 1. New holes found and fixed (all [E], all main-thread reproduced)

| # | class | silent effect | failed gate | fix |
|---|---|---|---|---|
| H1 | B `TIME - 1m * 2` (spaced `*`/`/` tail) | threshold 2m→−1m, **2.75 V** PASS | G2: `shift_b_expr` shifted only `1m`; residual strip saw parseable `TIME--0.0005` and passed | `shift_b_expr` raises on `*/` continuation of a shifted offset (fail-closed); unspaced form already REFUSEd |
| H2 | comparison `TIME > 1m * 2 ? …` | threshold 2m→1m, full-scale flip **5.0/2.75 V** | G2, same mechanism in `repl_cmp` | same fix in comparison branch |
| H3 | param SIN `SIN(0 5 {FREQ}…)` / `{PH}` | phase unadvanced 540°, **10 Vpp** PASS | G2: `shift_v_line` returned `raw` verbatim (no TIME token for residual) | REFUSE SIN with unparseable freq/phase when `t_off>0`; zero-freq (DC) still passes (shift-invariant) |
| H4 | non-bare `pwl(TIME-400u,…)`/`pwl(TIME*2,…)` control var | slope×t_off, **12–50 V** PASS (12.47 V end-to-end, state-node-only) | G2: shifter matched bare-only, caller matched prefix, residual stripped the `TIME` marker | residual marker strip narrowed to bare `pwl(TIME,`; any other TIME in `pwl(...)` REFUSEs (fixed pre-campaign, verified holding) |
| H5 | E/G `VALUE={…TIME…}` | **5→0 V** PASS | G1 (E/G TIME never denied) + G2 (E/G lines never scanned) | G1 DENY on TIME in any E/G/H/F line; residual scan on E/G/H/F lines (defense in depth) |
| H6 | `.func f(x){5*u(x+TIME…)}` called as `V=f(0)` | 5 V step fires, call-site ALLOW | G1 (param mediation only scanned TIME-bearing exprs) | G1 DENY when a B/E/G/H/F expr calls a TIME-bearing `.func` |
| H7 | `.include`/`.lib` file content | identical-via-include deck ALLOWs while inline DENYs | G1 (`parse_netlist` ignores file content; ngspice expands it) | G1 DENY on `.include`/`.lib` presence |
| H8 | `+` continuation lines | B expr truncated (`5*(u(TIME-200u)`), 2nd edge invisible to event set | `parse_netlist` never joined `+` (false event set + UNKNOWN-KIND noise) | join `+` continuations before parsing (single choke point: classifier, bandwidth, tau, G6 all corrected) |
| H9 | 100 kHz continuous SIN, coarse 1 µs | PASS-but-**185 mV** (control: continuous-coarse-vs-fine 211 mV = pure truncation) | G4: no whole-sim bound for continuous drive (B-event bound is window-local; tau bound 1 µs permits) | G4: `tmax <= SIN period/50` on coarse AND fine (PULSE/PWL need none: event-local + solver breakpoints) |
| H10 | non-resonant RL, coarse tau/2 (K2) / tau/10000 (small-L) | PASS-but-**2 mV / 4.75 mV** | G4: `tau_min` is C-only; `tau=None` circuits unbounded | G4: `tmax <= tau_L/100` with tau_L = L/max-series-R, LC-resonant windings skipped (h17 ring exact at 200 ns where L/R demands 100 ps [E REPORT2]) |
| H11 | G6 E-terminal map (`nodes[2:4]` = sense pair, not outputs) | `.ic` on true E output ALLOWED (benign here: PASS-exact, source enforces value) | G6 checked the wrong observable | map fixed to `nodes[:2]` + H voltage outputs added |

H1/H2 note: the bug is **spacing-dependent** — unspaced `TIME-1m*2` REFUSEd
while spaced `TIME - 1m * 2` went silently wrong. Same-precedence tails
(`TIME-1m+2m`) and parenthesized `(TIME-c)*k` verified SUPPORTED-exact [E].

## 2. Per-gate attack summary

- **G1**: attacked with E/G/H/F-VALUE-TIME, `.func`-hidden TIME, `.include`
  smuggling, lowercase `a1`/`x1`, reversed terminals, TT=0/CJO=0/`CJ0`
  spellings, caps-only Q, 60-case fuzz — 4 smuggles found (H5–H7 + H8 via
  parser), all closed; no over-refusals (plain-E, clean-`.func`, static
  `{param}` R, TT=0 still ALLOW) [E].
- **G2**: fuzzed over 286 expressions — 3 holes (H1/H2/H3) + 1 pre-fixed
  (H4) closed; 154 SUPPORTED-exact rows, 123 correctly-REFUSED rows [E].
- **G3**: wrong/narrow/edge-coincident/overlapping windows all REFUSE
  statically; every silent-miss case lives strictly in the
  unquantifiable/zero-TIME region where user windows are already mandatory.
  No G3 escape found [E].
- **G4**: 2 escapes found (H9/H10), both background-adequacy (grid too
  coarse), never handoff loss — seam innocent, grid guilty in both controls
  [E]. Closed with SIN + tau_L bounds; t3 RL (tau/100) and h17 ring still
  PASS (no over-refusal) [E].
- **G5**: complete for C (handoff⊆vecs enforced) and L/K currents (checked
  even when unobserved); completeness-before-continuity ordering proved
  load-bearing (forgotten-L at current-max: v-continuous, 100% energy lost,
  caught by completeness not continuity) [E]. Known availability limit
  (fail-closed direction): point-compare can REFUSE slewing observed nodes
  on mesh phase (~mV on 100s V/s slopes); interpolation would fix — not done
  (availability, not safety; no premature optimization) [E/I].
- **G6**: 1 wrong-observable defect (H11, benign, fixed); R-slave and
  E-downstream allowances verified sound [E].

## 3. Differential campaign (Thread A)

70 systematically varied cases (RC 1–3 caps, non-resonant RLC, DC-driven
diode clipper, K-ring; R/C/L decades; B-pulse timing/width/amplitude/
polarity/number/spacing; V-PWL/PULSE/SIN drives; ICs): <counts>,
zero silent-wrong after fixes; every tripwire adjudicated by 2×-finer
reference ladders to truncation/reference-artifact with seg≡ref at equal
tmax; analytic RC agreement nV-scale [E].

## 4. Event discovery / seam / reference / metamorphic

- Auto-discoverable: B u-pulse/difference, ternary TIME comparison,
  bare pwl(TIME), V-PWL/PULSE (breakpointed), smooth crossings.
  REQUIRE user windows: bare single edges, narrow state blips,
  hysteretic/latched zero-TIME edges (`dt ≈ TMAX`), unquantifiable TIME
  (additionally G2/G1-REFUSEd — user windows alone never suffice) [E].
- Seam: every naive-restart hazard constructed (0.29–7 V) REFUSEd by
  G1/G5/G6; kink-on-seam pre-empted by G3; LC energy exact when resolved [E].
- Reference methodology: every difficult case ladder-converged; seg beaten
  only by its own grid, never by handoff; edge-masked metrics throughout [E].
- Metamorphic: 34/34 META-PASS (rename, reorder, R-split+re-derive,
  t-shift, 2× scale, irrelevant structure); R-split re-derive confirmed
  load-bearing (fixed tc correctly REFUSEs G4) [E].

## 5. Production safety boundary (spike-3 revision)

- **Proven-safe**: R/C/plain-D (DC-driven, within tau bounds), memoryless
  B/E/G/H/F (TIME-free), L + complete IC handoff (non-resonant within
  tau_L/100; resonant exact-per-h17 riding handoff+G5 [U on bound]),
  K + every-winding IC, B u-pulse/ternary/bare-pwl(TIME) (precedence-clean),
  V-PWL/PULSE (shifted; PULSE seams outside active region), V-SIN (shifted,
  seams at 0 or ≥ td, within period/50, numeric freq/phase).
- **Conditionally safe**: unquantifiable-but-shiftable TIME + user windows;
  bare single edges + user windows; narrow/stateful triggers + user windows;
  all within timestep bounds.
- **Refused**: D-TT/CJO, Q/M/J, A, B-ddt/sin/bare-TIME/precedence-tail/
  non-bare-pwl, behav-R/C TIME, E/G/H/F TIME, TIME-bearing `.func` calls,
  `.include`/`.lib`, T, K-partial, OSDI/X/O/U/Y/N/S/W/Z, E/G-LAPLACE/FREQ,
  param SIN, damped/delayed SIN interiors, overlap/edge-coincident windows,
  events-in-coarse, any timestep beyond event/tau/tau_L/SIN bounds,
  incomplete handoff, `.ic` on driven nodes.
- **Unknown**: resonant-L coarse adequacy (no principled bound; exact in all
  tests); ddt relaxation; OSDI state class; G5 mesh-phase availability.

## 6. Answer

**Can we trust the mechanism to fail closed?** After this spike: every
*known* silent-wrong class fails closed, each backed by a minimized
reproducer and a regression check rather than by absence-of-counterexample.
The two residual unknowns (resonant-L bound, G5 availability) both err toward
REFUSE or exactness in all measurements, never toward silent wrongness — but
"no counterexample found" is not an invariant there, so they stay labeled
[U], not safe. The next spike that finds a new PASS-but-wrong reproducer
should follow this same recipe: minimize → attribute gate → smallest
fail-closed fix → regression → full re-run.
