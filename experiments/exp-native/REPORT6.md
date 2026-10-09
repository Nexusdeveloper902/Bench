# Spike 6 — Native Resume vs. Reconstructed Continuation: engineering report

Date: 2026-10-05. Env: ngspice-47 (Arch 47-1, KLU, libngspice.so.0.0.16),
python 3.14.7, Ryzen 5 5600G. Changed files: `exp-native/native_resume.py`
(new, ~230 lines), `exp-native/tests/test_native.py` (new, 10 checks ALL PASS).
Untouched and green: `exp-window/tests/test_window.py`,
`test_hardening.py`, `test_continuation.py`, `exp-safety/tests/test_regression.py`
(ALL PASS each, re-run 2026-10-05).

Tags: **[E]** measured · **[D]** manual documentation · **[S]** source-code
reading (ngspice master, sparse checkout 2026-10-05) · **[I]** inference.

## 0. Verdict

**SUPPORTED WITH LIMITATIONS.**

In-process libngspice natively pauses a transient run at an exact,
caller-chosen time boundary and resumes the *same* analysis with the
*actual* simulator state intact -- no reconstruction. Pause-vs-unpaused
comparisons on identical breakpoint grids are bit-identical (max|dt|
<= 8.5e-22, state-variable rel err <= 2e-14) across 8 circuit classes
deliberately including every hard case reconstruction had to refuse
(diode TT/CJO, B `ddt`, resonant LC, coupled L, T-line, BJT charge,
event-on-seam, absolute-TIME B-sources).

Limitation (load-bearing, §5): the step policy **cannot** be changed
across the boundary. Issuing `tran` while paused restarts the analysis
from zero [E]; `option`/`set` do not touch the TRAN job's max step [E].
So native resume preserves state but provides **no coarse/fine step
savings**. Its production value is synchronization (pause for
MCU/kernel decisions), not speed. Step economy for known events already
comes from PWL/breakpoints in a single run (236 pts vs 200 007 fixed-fine,
§6) -- segmentation is unnecessary for that.

Recommendation (option **1**): **adopt native resume, retire most
reconstructed continuation machinery.** Keep the PWL-restatement helper
as a single-run optimization; keep `exp-window/` as experimental
reference, not production path. No further continuation research is
justified (no unresolved correctness problem found).

## 1. Primary question: answer

| sub-question | answer | basis |
|---|---|---|
| run transient | yes | `run` / `bg_run` [D] |
| stop/pause at controlled boundary | yes, exact via `stop when time = T` | [D] manual 13.5.92 + [E] pause at 5e-6 == 5e-6 double (diff 8.5e-22) |
| change legitimately changeable controls | `alter`/`altermod`, new `stop`s: yes; TMAX/step policy: **no** | [E] alter rc=0 + resume completes; new stop honored at 7us [E]; `tran` restarts [E] |
| resume same simulation | yes, `resume` re-enters `dctran.c:resume:` with `ci_inprogress` held | [S] `spiceif.c` (`resume` -> `doAnalyses(...,0,...)`), `runcoms2.c:com_resume` |
| preserve internal + device state | yes, full CKTcircuit incl. CKTstates history | [S] + [E] §2 |

`resume` after a second `run`/`tran` is a *new* analysis, not a resume --
the module therefore issues only `resume` while in progress (static rule,
§11). A command that merely starts another transient run was never
counted as resume in any comparison: every native-vs-reference comparison
below holds the breakpoint grid constant, so grid effects and pause
effects are separated (cf. §4).

## 2. Actual state boundary

What lives in a running transient (from `cktdefs.h` + `dctran.c` [S]):
`CKTstates[8]` (per-device state history: capacitor charge, inductor
flux, diode/BJT junction + transit charge, B-source `ddt` history,
integration memory), `CKTrhs*` vectors, `CKTdelta`/`CKTdeltaOld[7]`
(adaptive-step history), `CKTorder`/`CKTag` (integration order/coeffs),
`CKTbreaks` (breakpoint table), `CKTmode`,`CKTstat` (accepted/rejected,
iterations). All dynamic device states are allocated inside `CKTnumStates`
entries of `CKTstates` -- there is no second device-state store.

What survives `stop`/`resume` and `bg_halt`/`bg_resume`: everything above
-- the CKTcircuit is never freed; `resume` jumps back into the transient
loop (`goto resume`, [S]). Pause is checked **after** `CKTaccept`
(`IFpauseTest()` at `dctran.c:505`, [S]), so interruption never splits a
Newton iteration. What is *additionally* proven to survive: diode
transit/junction charge, `ddt` history, LC/K integration history and
order, T-line delay-line state, BJT charge, XSPICE-event breakpoint
tables -- each via bit-identical pause-vs-bkpt tests [E, §4].

Documented [D]: `snsave` writes the CKTcircuit struct + `CKTstates` +
RHS + breakpoints + TRAN job + stats to file; `snload` + `resume`
restores them even in a new instance (verified: 4771-byte snapshot,
"Snapshot saved", resume completes [E]). Explicitly **not** preserved by
`snsave`: XSPICE A-device state ("snsave not implemented for XSPICE A
devices", command ignored [D] + reproduced with E-POLY [E]); event-node
data (`FIXME ... not compatible with XSPICE code models` [S]).
In-memory `stop`/`resume` has no such file-format exclusion, but XSPICE
pause behavior was not exercised: **unresolved, fail-closed to full sim**
[I]. OSDI in-memory preservation is inferred (states live in CKTstates)
but untested (no OpenVAF binary in env): **unresolved** [I].

## 3. Minimal native-resume proof

```python
ng.circ([... '.tran 10n 10e-06 0 10n' ...])   # any stateful circuit
ng.command("stop when time = 5e-06")           # exact breakpoint form
ng.command("run")                              # pauses at 5us (E_PAUSE)
ng.command("resume")                           # same analysis continues
```

Reference design (grid-effect isolation): `bkpt-cont` = same circuit +
`ngSpice_SetBkpt(5e-6)` + uninterrupted `run` (breakpoint hit, no pause).
`stop-resume` must equal `bkpt-cont` exactly; both differ from plain
`cont` only by the 3 breakpoint-forced points (1027 vs 1024 on RC).

Result [E]: `bkpt-cont` vs `stop-resume` bit-identical for RC, diode
TT/CJO, B-`ddt`, resonant LC, coupled K, T-line, BJT, event-on-seam,
absolute-TIME B-source (§4 tables). Determinism baseline: `cont` vs
`cont` re-run is bit-identical (0.0), so any nonzero diff would be a
real pause artifact -- none found.

## 4. Correctness comparison (numbers [E])

RC pulse (10us, TMAX=10n, stop 5us):

| pair | n | max\|dt\| | max\|dv\| |
|---|---|---|---|
| cont vs cont2 (determinism) | 1024=1024 | 0.0 | 0.0 |
| cont vs bkpt-cont (grid effect) | 1024 vs 1027 | first diff at 5us only | breakpoint-forced hit |
| **bkpt-cont vs stop-resume (pause effect)** | 1027=1027 | **8.5e-22** | **5.3e-15 (rel 1e-15)** |

Hard-state pause effect (bkpt-cont vs stop-resume, same grid):

| circuit (prev. reconstruction verdict) | max\|dt\| | max state err (rel) |
|---|---|---|
| diode TT=20n CJO=2p (REFUSE) | 8.5e-22 | 5.8e-25 (5e-25) |
| B `ddt(V)` (REFUSE) | 8.5e-22 | 2.2e-07 abs on 5e6 peak (4e-14) |
| resonant LC (period-bound REFUSE w/o user T0) | 0.0 | 0.0 |
| coupled L K=0.9 (REFUSE w/o full handoff) | 8.5e-22 | 6.4e-14 (2e-14) |
| T-line TD=1us (REFUSE) | 8.5e-22 | 0.0 |
| BJT CJC/TF (REFUSE: Q charge) | 8.5e-22 | 8.9e-16 (2e-16) |
| PULSE edge exactly at seam | 0.0 | 0.0 |
| absolute-TIME B-source (no shift) | 8.5e-22 | 0.0 |

Sustained ideal LC (1mH/1uF, T0=198.7us, 10 periods, TMAX=200n):
bkpt-vs-stop zero-crossings identical to 0.0, amplitude rel diff 0.0;
cont-vs-bkpt (breakpoint perturbation alone) 1.3ps on 198us (2.5 micro-deg).
No seam discontinuity, no phase accumulation from pausing. Post-seam
windows of 5-10 periods were compared in every case; long-term divergence
is zero to floating noise.

What was *not* counted as success: return-code-only, visual similarity,
or endpoint agreement. Endpoints alone can mask breaks (spike-1 S11
showed 0.316 A jumps with agreeing finals); all claims above are full-grid
comparisons.

## 5. Native-resume semantics (exact)

* **How stopped**: `stop when time = T` issued before `run`; run returns
  E_PAUSE ("doAnalyses: pause requested", "run simulation interrupted")
  [D+E]. `bg_halt` suspends a `bg_run` thread instead, but is racy (§11).
* **At what time**: exactly T. Measured pause 4.9999999999999996e-06 =
  5e-06 double (diff 8.5e-22) [E]. The `=` form installs an internal
  breakpoint (manual 13.5.92 [D]; delta-cut-to-breakpoint in `dctran.c`
  [S]); post-breakpoint order is cut to 1 with ramp-up [S].
* **Exact or approximate**: `=` exact; `time > T` approximate (overshoot
  to 5.001us observed [E]). Kernel must use `=` only; the module does,
  and verifies every pause (fail-closed).
* **Arbitrary times**: yes, any T in (0, TSTOP); verified 3/5/7us,
  1ms, 100us grids [E].
* **Breakpoint/event forcing**: yes -- `=` *is* a breakpoint;
  `ngSpice_SetBkpt(T)` (must be set while bg thread idle [D]) also
  forces exact hits [E].
* **TMAX change after stop**: **no**. `tran ...` while in progress
  starts a NEW analysis from 0 (measured restart + re-pause [E]);
  `option`/`set maxstep` had no observable effect [E]. Per-segment step
  policy is impossible on this path.
* **New breakpoints**: yes -- second `stop` added while paused is
  honored on the next `resume` (paused at 7us after 5us [E]); chained
  multi-pause verified (3 stops, exact, test `multi-pause`).
* **Different controls per segment**: `alter R1=...` between pause and
  resume is accepted (rc=0, resume completes [E]) -- allowed by ngspice,
  but the kernel must treat any mutation as a fallback trigger (§11),
  since resumed physics would no longer match the request.
* **Same or new analysis**: same (`resume` -> `doAnalyses(ckt,0,...)`
  re-enters at `resume:` [S]; `ci_inprogress` held TRUE [S]).
* **Callbacks sufficient**: yes -- and none needed. The main-thread
  path is fully synchronous (`stop`/`run`/`resume`/`delete` via
  `ngSpice_Command`); SendData/GetSyncData/bg threads are unnecessary
  for kernel pausing and are excluded from v1 (§11).
* **Restart internally**: no re-init on resume (grids bit-identical [E]).
* **State surviving / documented / inferred**: §2 table.
* **Residual value without TMAX control**: synchronization pauses
  (MCU-driven breakpoints) + `alter`-class param updates; step economy
  comes from PWL/breakpoints set *before* the run (§6), not from
  per-segment TMAX.

## 6. Performance comparison (2ms RC + 100ns B-blip at 1ms [E])

| approach | pts | wall | peak | verdict |
|---|---|---|---|---|
| fixed-fine TMAX=10n (old headline baseline) | 200 007 | 585 ms | 5.0 | worst-case demo only |
| ordinary adaptive (default TMAX) | 211 | 7 ms | **0.0 (blip missed)** | silent-wrong; not a valid baseline for eventful circuits |
| coarse B TMAX=10us | 211 | 7 ms | 0.0 (missed) | same miss |
| **PWL restatement + coarse 10us (single run)** | **236** | **6 ms** | **5.0 exact** | **850x cheaper than fixed-fine, no machinery** |
| reconstructed segmented (10ms target, REPORT) | 1 241 (0.12%) | 57 ms | exact | real win vs fixed-fine, needless vs PWL (1 047 pts, 34 ms) |
| native pause/resume, same TMAX=10n | 200 013 | ~600 ms | 5.0 exact | +3 pts/breakpoint, **no step savings** |

Accepted/rejected/iter evidence: fixed-fine 200 008 acc / 0 rej /
2.0 iter-pt; adaptive 212 acc / 0 rej -- rusage cannot see event-miss
breaks (consistent with spike-1 finding that runtime checks catch 0/6
silent breaks). No claim is made on Newton/memory beyond this: the
architectural point does not need it -- segmentation's headline win
evaporates once the baseline is PWL/adaptive-with-breakpoints instead of
fixed-fine. Do not use fixed-fine as the production benchmark.

## 7. Refusal-boundary revision

Previous taxonomy (`continuation.py` RefusalCode) vs native path:

| previous refusal | native verdict |
|---|---|
| hidden state: Q/D-TT/D-CJO/ddt/T-line/X/A-device | **eliminated** (bit-identical [E] for D-TT/CJO, ddt, T, Q/BJT) |
| coupled-L partial handoff | **eliminated** (K exact, no handoff needed [E]) |
| resonant-period unavailable | **eliminated** as a refusal (no coarse-adequacy proof needed; breakpoint perturbation 1.3ps [E]); user T0 still useful as step advice, not a gate |
| OSDI / exotic | **unresolved** (no binary; inferred likely-OK in-memory [I]) -> fallback, not refusal-of-simulation |
| unshiftable TIME / .param-TIME / E-VALUE / func-TIME / `*/` precedence | **eliminated** (no shifting; TIME stays absolute [E]) |
| unknown event timing / event-outside-window / overlapping / invalid window | **irrelevant** (no windows; stops are the event list, validated as strictly-increasing inside (0,TSTOP)) |
| coarse/fine bound exceeded | **irrelevant** (single step policy; kernel sets one global bound before the run) |
| incomplete handoff / observable mapping / driven-node init | **eliminated** (no handoff, no stitching, single plot) |
| included content (.include/.lib) | **eliminated** (ngspice expands normally; nothing is statically classified) |
| seam discontinuity / event coverage | **eliminated** (no seams; pause times verified exactly, else fallback) |
| STALE/INVALID_PLAN | **irrelevant** (no plan objects in v1) |

Remaining operational fallbacks (availability only, §8): unknown_tstop,
stop_outside_interval, stops_not_strictly_increasing/duplicate,
stop_rejected, inexact_pause_*, incomplete_after_resume. None refuses
simulation -- each runs the full path.

## 8. Safety principle + fallback demonstration

`run_simulation(request)`: stops validated upfront; every pause verified
(`_pause_exact`, tol max(1e-12, 1e-9·|T|)); completion verified at
t_stop. Any deviation -> ordinary full `circ`+`run` on a fresh handle.
Tests prove it: `fallback outside-interval == full sim` (bit-identical
series to the no-stop run), `fallback unsorted stops` (named reason, full
series returned). The full path is the correctness baseline in every
case; there is no reconstructed state anywhere in the module and no
chained native->reconstructed fallback (per §12: simplest architecture).

## 9. Plan/execute reevaluation

Native resume needs **neither** `ContinuationRequest` **nor**
plan/execute. The v1 request is `(netlist, observables, stops)` and the
runtime decides internally (pauses iff all stops exactly achievable,
else full run). No continuation planning layer for v1: "planning" is
just the MCU event schedule (stop times), which the caller already owns.
The 16-code refusal taxonomy is not carried over (see §7); 7 operational
fallback strings replace it, all availability-graded. No generic
simulation-plan framework was created -- the evidence requires none.

## 10. Kernel integration boundary

```text
run_simulation(request) -> (series, npts, wall_ms, used_pauses, fallback_reason)
```

`exp-native/native_resume.py` is that boundary (ngspice-specific details
stay inside; GUI/CLI/MCP callers see only request/result/optimization
flag/structured fallback). AI/MCP callers never handle breakpoints,
TMAX, `.ic`, or plots: they optionally supply stop times (e.g. MCU event
schedule) and read `used_pauses`/`fallback_reason` for observability.
Sequential-handle discipline found during testing and encoded: reads are
current-plot qualified (`CurPlot`-qualified vectors; the naive
tran1-first lookup returns stale data after handle reuse [E]).

## 11. Attacks on the native path (adversarial results)

* `>`-form stop overshoots (5.001us) [E] -> module uses `=` only and
  verifies; `>` never issued.
* Breakpoint skipped / extra pause / short final: covered by
  count+time verification at every boundary; any anomaly -> full-sim
  fallback (tested for outside/unsorted; inexact paths return full
  series by construction).
* Event exactly at seam (PULSE edge == stop): exact, 0.0 diff [E] --
  no ordering hazard between device breakpoints and stop breakpoints.
* TMAX-change attempt via `tran` restarts from 0 [E] -> module never
  issues `tran`/`run` while in progress (only `resume`); code-reviewed,
  no test needed for a non-existent call.
* Circuit mutation via `alter` accepted by ngspice [E] -> module has
  no mutation API; documented rule: any future between-pause mutation
  must force fallback, never silent resume.
* bg-thread path: startup race (vectors unavailable at first poll),
  `bg_resume` progress invisible to `ngSpice_running` polling,
  teardown core-dump on active-thread exit (observed [E], consistent
  with REPORT e11 join discipline) -> **rejected for v1**; main-thread
  path has none of these.
* Newton-interruption: impossible on this path (pause checked after
  accept [S]); bg_halt mid-iteration untested -> another reason to
  avoid bg.
* Timeout-during-pause: N/A (synchronous; no watchdog needed).
* Post-breakpoint order cut (order=1 + 0.1x ramp [S]): measured
  harmless (1.3ps phase on 198us; 2e-6 amplitude [E]).
* Stale-plot reads on handle reuse (1027-pt stale grid returned for a
  508-pt run [E]) -> fixed with CurPlot-qualified reads; regression
  test `sequential reuse isolated` guards it.
Stopped here: boundary established (exact-pause + fallback + no-bg +
no-mutation + qualified-reads). No exhaustive ngspice-behavior sweep.

## 12. Failure handling

Native continuation fails closed *as an optimization*: any unavailable
pause -> normal full simulation (demonstrated, §8). No automatic
reconstruction exists in the module. No combined native+reconstructed
fallback chain. The only two paths are `used_pauses=True` (all
boundaries exact) and full simulation.

## 13. Deliverables map

* A. Verdict: SUPPORTED WITH LIMITATIONS (§0).
* B. State boundary: §2 (preserved / documented / inferred split).
* C. Minimal reproduction: §3 (`stop when time = T` + `run` + `resume`;
  `exp-native/tests/test_native.py:t_easy`).
* D. Correctness: §4 (continuous / native / reconstruction-applicable;
  reconstruction need not run where it refuses -- that is the point).
* E. Performance: §6 (adaptive/PWL/segmented/native with miss analysis).
* F. Refusal revision: §7 table.
* G. Production API: §9-10 (`run_simulation`, `exp-native/native_resume.py`).
* H. Fallback: §8 + fallback tests.
* I. Code/tests: this section. Changed: `exp-native/native_resume.py`
  (new), `exp-native/tests/test_native.py` (new, 10/10 ALL PASS).
  Regressions: window×3 + safety suites ALL PASS unmodified.
  Limitations: no per-segment TMAX; XSPICE/OSDI pause untested
  (fallback); single-handle plots need qualified reads; bg path
  rejected for v1.
* J. Architecture: **1. Adopt native resume and retire most
  reconstructed continuation machinery** (plan/execute + G1-G6 +
  segmented runner leave the production path; PWL restatement stays as
  a single-run optimization; `exp-window/` retained as reference).

## 14. Definition-of-done answers

1. Can libngspice pause/resume state? **Yes** -- exact stop + same-analysis
   resume, bit-identical state (§1, §4).
2. What survives? Full CKTcircuit incl. all device/integration history
   (§2); XSPICE-file-snapshot and OSDI untested.
3. Can step policy change across boundary? **No** -- limitation (§5).
4. Numerically equivalent for stateful circuits? **Yes** -- 8 classes,
   0.0-to-2e-14 (§4), incl. 10-period resonant phase.
5. Outperform adaptive? **No as an optimizer** -- PWL/breakpoints already
   give 850x; adaptive-alone misses events (§6). Native wins on
   state coverage, not speed.
6. Which refusals disappear? All model-class/window/handoff/seam
   refusals (§7); only operational fallbacks remain.
7. Smallest abstraction? `run_simulation(request)` (§10).
8. Refusal falls back safely? Yes, demonstrated bit-identical (§8).
9. Retain/reduce/retire reconstruction? **Retire** from production path
   (keep as reference + PWL helper) (§13J).
10. Another research spike? **No** -- no unresolved correctness problem
    blocks the chosen architecture.

## 15. Constraints compliance

No solver modifications; no undocumented-guarantee reliance (every
semantic above is [D]/[S]/[E]-tagged, OSDI/XSPICE marked unresolved);
no visual-similarity claims (full-grid diffs); fixed-fine used only as
worst-case foil with PWL as the real baseline; reconstruction untouched
(`exp-window/` green, unmodified); no state-transfer framework built;
no taxonomy preserved for compatibility; continuation is optimization-only
(fallback-tested); no GUI/MCP-specific code (one kernel function).
