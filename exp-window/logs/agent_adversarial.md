# Adversarial spike: breaking segmented simulation (.ic+uic continuation)

Date: 2026-10-04. Env: ngspice-47 batch subprocess only, timeouts everywhere.
Scope: repo `<repo>`, reuse `exp-safety/helper_safety.py` + `runtime.py` + `validator.py` (import-only).
Raw: `agent_adversarial.json` (16 records). Runner: `/tmp/opencode/adv_run.py` (tmp, `adv_` prefix).
Nothing outside `exp-window/logs/` modified. Total sim runtime ~60 s (< 8 min budget).

Tags: **[E]** measured · **[D]** docs · **[I]** inference · **[U]** unresolved · **[C]** caveat.
I do NOT fix failures. Verdicts below are "broken-or-not" per attack.

## 0. Baseline used by every attack

Reference = single full run, tight TMAX (w/10 or better). Segmented = seg1 `[0,t_split]`
+ seg2 (same netlist, `.ic` node voltages from seg1 end, `uic`, TIME terms shifted by
`-t_split` where stated, else deliberately NAIVE). Stitch = seg1 + seg2 offset by `t_split`.
`check_runtime` (runtime.py) applied to each restarted segment with `tmax_required=w/10`.

## 1. Hidden-state trap [E]

| probe | result |
|---|---|
| B `idt()`/`sdt()` | **Unconstructible**: ngspice-47 batch → `no such function 'idt'`, rc=1 **[E]**. `ddt()` exists. True B-integrator hidden state is therefore **[U]** in this toolchain — and DENY-by-default (cannot characterize what cannot build). |
| B `ddt()` split exactly AT a 0→1 step (A1b) | **BROKEN-DROPPED [E]**: impulse area ref = 1.000 (= dV) vs stitched = 0.000 + 0.000 = 0.000. The edge falls in the seam: seg1's endpoint lacks post-step slope, seg2's t=0 starts post-step. rc=0, zero warns, runtime **PASS clean**. Control (step 5 ns inside seg2) captures area — but reads **3.000 vs 1.000**: `.ic v(drv)=1` (seg1end, post-step) fights the B source (pre-step 0 at seg2 t=0), injecting a phantom impulse. Two rules: never split ON a discontinuity; never `.ic` a B-driven node. Overlap windows would double-count (2×) by symmetry [I]. ddt peak = 1e8 (= V/dt artifact, cf. B4) [E]. General-continuation KILL for ddt near seams: yes. |
| Diode TT=2us, split 7.5us BEFORE switch@8us, TIME-shifted seg2 (A1c) | **BROKEN-SILENT [E]**: post-switch tail differs by 38/39/43 mV at abs 8.5/9/10us (> 10 mV bar on 5 V scale). seg2 diode re-charges 0.5us vs 8us → different stored charge → different recovery. rc=0, zero warns, runtime **PASS clean**. |
| Diode TT=2us, split 8.15us IN recovery tail, const-0 drive (A6b) | Borderline **[E]**: final 0.6521 vs 0.6426 = **9 mV** (~1.4% of local 0.65 V signal; under the 10 mV absolute bar, over the 1% relative bar). Quasi-static forward equilibrium mostly restores. Verdict: marginal — still the silent class, not a clean kill. |
| Diode Tt=0 (Cjo only), same split (A1d) | **not-broken [E]**: interior maxerr 0.024 V, jump 0.024; raw maxerr 0.23 V sits entirely inside ±150 ns LTE edge-spike zones (placement noise, not state loss). Junction C is voltage-state → `.ic` restores it. |
| Inductor 10uH, split mid-transient, node-only `.ic` (A1e) | **BROKEN-CONTINUITY [E]**: \|iL\| seg1end 0.316 A → seg2start 0.000 A, JUMP = 0.316 A (63% of 0.5 A final signal). Stitched maxerr 0.316 A. Treacherous detail: **final values agree** (−0.499 vs −0.497, tau=1us decays the error) — endpoint comparison MASKS the break; only a stitch-continuity gate on branch current catches it. rc=0, zero warns, runtime **PASS clean**. |
| C-only control, same protocol (A1f) | **not-broken [E]**: stitched maxerr 89 µV, continuity jump 92 µV. `.ic`+`uic` SAFE for C. |
| OSDI diode | Skipped, no OpenVAF binary at /tmp/opencode/openvaf **[U]**. |

KILL summary for general continuation: yes — L, D-TT, ddt-at-seam each diverge silently with rc=0.

## 2. TIME-reset trap [E]

Fresh `.tran 0..5u` with `B=TIME` ends at TIME = 5e-6: **TIME resets to 0 every segment [E]**
(A2a). Naive seg2 reusing `5*(u(TIME-8e-6)-u(TIME-8.1e-6))` over its 0..5us window:
ref peak 5 V → seg2 peak **0 V; event time error = INFINITE** (never fires) (A2b).
rc=0, zero warns, per-seg dt_max fine → runtime **PASS clean**. All TIME/PWL/PULSE/SIN
terms require mechanical `-t_split` rewrite; PULSE delays that go negative are
unrepresentable (cf. A9). Verified TIME-shifted segs (A1c/A3/A4/A6a) work as constructed.

## 3. Boundary trap [E]

Smooth sin² hump (100 ns, peak 5 V @ 5.0us) straddling split@5.03us; seg1 uses realistic
coarse tmax=600 ns, seg2 fine + exact-shifted tail (A3): stitched peak **1.73 vs 5.00
(65% peak error)**, stitch maxerr 0.08. seg2 alone: runtime **PASS clean** — the break is
visible ONLY via stitch-vs-reference comparison. seg1 alone draws
`QUESTIONABLE(EVENT-COVERAGE: dt_max=600n >> required=10n)`: mis-capture is caught
**iff every segment incl. coarse ones is gated**, never if only refined windows are.
Double-capture: overlapping windows at a `ddt` discontinuity count the impulse twice [I] (A1b).

## 4. State-dependent-timing trap [E]

RC (tau=1us) threshold crossing: analytic t*=1.1931us; coarse run (TMAX=1us) estimates
1.2623us → err=**69 ns** (same TMAX-scale family as exp-safety events2's 476 ns @ 1us) (A4).
Ground-truth 20 ns blip at t*; window centered on coarse estimate: hw=10 ns → peak **0
(SILENT MISS)**; hw=err+50 ns → peak 5 (HIT). Narrow-win runtime **PASS clean**.
**Required margin: half-width ≥ timing_err + w/2 + tmax_fine** (= 69+10+2 ns here) [E].
Auto-windowing from a coarse scan without this margin is dead.

## 5. Silent-plausible trap [E] — CONFIRMED, the most dangerous class

Six of eight broken cases are runtime-**PASS-silent** (rc=0, zero warnings, no
EVENT-COVERAGE flag because per-segment dt_max looks fine): A2b (5→0 V),
A1c (43.5 mV), A1e (0.316 A jump, endpoints agree!), A9 (5 V arrival lost),
A1b (impulse 1→0), A4 (5→0 V); plus A6b borderline 9 mV. A3's seg2 passes; only
stitch comparison (or gating seg1) exposes it. `check_runtime` caught **0/6** silent
breaks — it is necessary, nowhere near sufficient, for continuation.

## 6. Multi-event interference

Two 100 ns blips 300 ns apart, tau=200 ns load, split@8.15us in the settle tail
(tail residue 1.50 V), exact shift (A6a): interior maxerr 0.056, 2nd-peak 2.348 vs
2.331 (0.7%), jump 1.5e-4 → **agree [E]**. Merged-vs-split and overlap are safe **for
C-state**; split-in-tail is safe for C and borderline-to-broken for D-TT (A6b vs A1c:
history before the split matters more than tail position). Overlap double-count at
discontinuities remains a stitching hazard [I].

## 7. Failure-behavior check + minimum detection rule [E]

`check_runtime` verdicts on restarted segments: A1b/A1c/A1e/A2b/A4/A6b/A9 all **PASS
clean**; A3-seg2 PASS clean / A3-seg1 QUESTIONABLE(EVENT-COVERAGE). Failures are
**SILENT** in 7/8 cases — never LOUD (no timestep/singular/nonfinite/RAIL trip).
Minimum production gate (all six, in order):
1. **Static state-class gate** (allowlist §8) — runtime caught 0/6.
2. **TIME-source audit**: rewrite every B-TIME/PWL/PULSE/SIN term by −t_split; self-test each segment with a TIME probe (final TIME must equal window length).
3. **Margin rule**: half-width ≥ coarse timing error + w/2 + tmax_fine.
4. **Stitch-continuity gate** on ALL node voltages AND reactive branch currents (|Δ| ≤ tol) — catches A1e/A1b/A6b-class instantly. Necessary, not sufficient (A9 passes it at nodes yet loses the arrival; A1e endpoints agree).
5. **Per-segment EVENT-COVERAGE (w/10)** on EVERY segment including coarse (catches A3-seg1).
6. **Seam hygiene**: never split within the settle/impulse zone of a discontinuity; never `.ic` a B-driven node (phantom impulse, A1b control 3.0 vs 1.0).
Residual risk even after 1–6: in-flight distributed state (T-line) and sub-tol silent drift (A6b 9 mV) — require reference spot-checks on a schedule, not just gates.

## 8. State-class gating table (one-line evidence each)

| class | gate | evidence |
|---|---|---|
| C (+ R, memoryless) | ALLOW | A1f 89 µV; A6a agrees [E] |
| L | DENY | A1e 0.316 A jump; node-`.ic` cannot set iL; endpoint masking [E] |
| K (mutual) | DENY | inherits L (coupled inductor state + core); no separate run [I] |
| D, Tt=0 (Cjo only) | ALLOW w/ care | A1d interior 24 mV, LTE edge noise only [E] |
| D, Tt>0 | DENY | A1c 43.5 mV silent; A6b 9 mV borderline [E] |
| B `ddt` | DENY near seams | A1b seam drop 1→0, overlap 2×, `.ic`-fight phantom 3× [E] |
| B `idt`/integrator | DENY (untestable) | no such function in ngspice-47; cannot characterize [U] |
| B memoryless + TIME | ALLOW iff TIME-audited | shifted segs agree wherever state is C-only [E] |
| T-line (T) | DENY | A9 in-flight 100 ns pulse lost, 5→0 V, PASS-silent [E] |
| PWL/PULSE/SIN indep. srcs | ALLOW iff shifted; DENY if shift unrepresentable | A1e/A1f DC-equivalent rewrite works; negative PULSE delay impossible (A9) [E] |
| OSDI/Verilog-A | DENY-unknown | skipped, no binary [U] |
