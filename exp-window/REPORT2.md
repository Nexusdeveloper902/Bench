# Continuation-aware event windowing, spike 2: boundary expansion + hardening

Date: 2026-10-04. Env: ngspice-47 batch (subprocess + timeout per call),
Ryzen 5 5600G. Prior work untouched except `exp-window/window.py`
(hardened) + `exp-window/tests/test_hardening.py` (new, 30 checks ALL PASS);
`exp-window/tests/test_window.py` still ALL PASS (20/20, unmodified);
`exp-safety/tests/test_regression.py` still ALL PASS (unmodified).
`bsource.py`/`validator.py`/`runtime.py` untouched.

Tags: **[E]** measured · **[D]** docs/manual/code-reading · **[I]** strong
inference · **[U]** unresolved · **[C]** contradicted.

Five parallel threads + main-thread verification. Scratch in
`/tmp/opencode/exp2/thread_{numerical,hidden,margin,multi,advers}/`
(scripts + JSON logs). This report synthesizes; thread reports live in
that scratch.

## 0. Verdict

**The validated boundary both expanded and contracted.** Thirteen
silent-PASS-but-wrong holes [E] were found in the spike-1 mechanism; all
are now either REFUSEd by enforced gates or exactly supported by
implemented shifting. The failure was **enforcement, not physics**: G1–G6
were sound but optionally-invoked (run_segmented never called
classify_state; _event_active was dead code; shifts were partial while
docstrings promised total). No new abstraction (no formal
state-transfer contract) proved necessary: completing the enforcement
of the existing gate vocabulary closed every hole [I].

Cost/accuracy of the validated class is unchanged or better: the
10 ms / 100 ns target numbers from spike 1 stand; auto multi-event now
derives every window; V-PWL/PULSE/SIN events segment exactly.

## 1. Nonlinear/stiff backgrounds (Priority 1)

Campaign (`thread_numerical/cases.py`): diode-clipper, tanh-limiter,
B-feedback (loop gain 2/3), dual-tau (10 us + 10 ms), stiff Rs+diode+C,
sustained-step operating-point shift — each vs 1M-pt full-fine ref, coarse
swept tau/10 → tau/1000.

- [E] All five completed classes are PASS-exact at tc <= tau/1000
  (in-window <=0.8 uV, final <=0.4 uV, peaks exact). Errors at coarser tc
  are pure background truncation (H=0 probes match event-run pre-errors),
  margin-independent, amplitude-linear — i.e. mis-resolution, never
  handoff loss. G1 ALLOWs all (plain IS/N diodes) correctly.
- [E] Revised coarse rule: **tmax_coarse <= tau_min/1000** with
  tau_min = min over C of (C * min incident R). Enforced in code (G4);
  coarser runs now REFUSE instead of passing with mV errors. The old
  RC-derived "coarse resolves background" rule did NOT generalize on its
  own — diode-clipper at tc=tau/10 passed with 3 mV error.
- [E] Direct-drive events (50 V slammed into the state node through 100 R)
  need **tmax_fine <= min(w/10, tau_min/1000)**: 0.2 V in-window error at
  w/10 collapses to 0.2 mV at w/100. Same divisor, enforced in code (G4).
- Case f (sustained 5 V step into 1 V diode clipper = large OP shift) [E]:
  tc=1 us (tau/1000) PASS, masked in-window 23 uV, final 0.0, 9.4% cost;
  tc=10 us/100 us/1 ms REFUSEd by the tau gate. An un-gated run at tc=1 ms
  shows 912 uV (background truncation, consistent with the a–e scaling).
  OP-shift supported within the same bounds.

## 2. Hidden-state classification (Priority 2)

Split-vs-continuous, no event at split (pure state-transfer loss):

| category | verdict | key numbers [E] |
|---|---|---|
| D plain (IS/N, 3 decades bias) | SAFE | maxerr <=10 nV, silent |
| D with TT>0 | REFUSED (stays) | 722 uV at turn-off, tmax-INDEPENDENT = structural; scales w/ TT |
| D with CJO/CJP>0 | REFUSED (new deny) | seam-local ~0.2 mV at coarse tmax, tail exact; numerical restart artifact, but G5-blind → fail-closed |
| B idt()/sdt() | N/A (loud) | `no such function`, rc=1; no gate needed |
| B ddt() | REFUSED (stays, no relaxation) | seam dropout = full local \|d/dt\| for ~2 steps then tracking; refill length solver-controlled, not user-quantifiable |
| K, all windings IC+UIC | SAFE-WITH-EXPLICIT-HANDOFF (relaxed) | 103 uV@k=0.9/6 uV@k=1.0, seam-local, →7 uV at finer tmax; no-IC control 3.2 V catastrophe |
| T-line | REFUSED always | in-flight 2.5 V loss; TD=1n still 2.5 V; endpoint T-IC rewrite still 2.5 V. Quiescent splits exact but quiescence not gate-provable |
| V PULSE/SIN/PWL | SAFE-WITH-EXPLICIT-HANDOFF (now implemented) | naive restart 5–7 V silent loss; shifted: PULSE 1e-10, PWL exact, SIN 22 uV mesh |
| B ternary TIME-x | SAFE (new: comparison shifting) | peak exact |
| B pwl(TIME,..) | SAFE (new: keep-all shifting) | peak 4.98/4.98; B-pwl EXTRAPOLATES outside table (V-PWL holds) [E] |
| B sin(k*TIME), bare TIME, TIME-(a+b) | REFUSED (fail-closed residual) | unshiftable phase/expression |
| Q/M/J | REFUSED (new deny) | BJT TF 4.4 V tail; caps-only 163 mV; TF-only 40 mV; bare NPN 386 uV; MOS 1.56 mV mid-transition (intrinsic channel charge), 0.0 quiescent (not gate-provable) |
| A XSPICE code models | REFUSED (new deny) | int_ic state opaque [D] |
| E/G/H/F plain | SAFE (memoryless algebraic [D]) | no change needed |
| E/G LAPLACE/FREQ | REFUSED (new deny) | transfer state; direct forms fail loudly today, denied anyway |
| S/W, O/U/Y/N/Z, X, OSDI | REFUSED (unchanged) | switch hysteresis is 1-bit state [D]; rest opaque |
| R/C with TIME | REFUSED (new deny) | 30% silent final error |

G1 false-ALLOWs fixed: Q/M/J/A/CJO/behavioral-R-C/.param-TIME (all [E]).
G1 relaxed on evidence: K with complete winding handoff (was deny-by-default).

## 3. Margin robustness (Priority 3)

~170 segmented runs, tau/w 10..1e6, widths 10 ns..10 us, H 0.05..50 V.

- [E] 10x stands as kink-clearance default; m=0 REFUSEd 16/16 (G5 full-amplitude jump). No regime needs >10x; 30x costs ~2x points for zero gain; 3x equals 10x whenever background is resolved (per-class choice [I]).
- [E] Errors are pure f(dt_coarse/tau), margin-independent; bit-identical across 1000x width span; peak err 0 in all 64 PASS runs.
- [E] `margin=max(10w, tmax_coarse)` DELETED: buys nothing, kills savings (window swallows sim), background controlled via the tau bound instead.
- [E] Reference-artifact warning: a "315 uV final error" invariant over all parameters turned out to be an UNCONVERGED REFERENCE (analytic solution: seg 15 uV, ref-10ns −300 uV; 10→2→1 ns ladder converges). Segmentation with fresh history beat the continuous run at the same tmax 20x. Converge refs (tmax<=min(w,tau)/1000, slew-aware) + edge-masked metrics before blaming segmentation.

Revised rule (replaces margin>=10w + max() + "1 coarse step"):
margin = 10*min-edge-interval; tmax_coarse <= tau_min/1000;
tmax_fine <= min(w/10, tau_min/1000) enforced in code; converged refs.

## 4. Multiple/overlapping events (Priority 4)

- [E] SEVERE: derive_windows kept only the FIRST edge pair (`break`) — 2nd+ pulses silently left to coarse (PASS +29–324 uV) or luck-REFUSE. Fixed: per-edge windows + clustering; 2-pulse auto now covers both.
- [E] bsource inter-event gaps recorded as widths (pollution). Left as-is: min edge-gap is the correct fastest-feature quantity for t_req; span construction no longer consumes widths.
- [E] run_segmented accepted overlapping windows → backward-time plan + 2.5 mV double-count PASS. Fixed: overlap/unsorted → REFUSE.
- [E] G5 catches seam-on-kink but not coarse-swallowed events (PASS, peak 0.0). Fixed: static containment — every quantified event strictly inside a fine window (S7-class → REFUSE without sim).
- Per-event vs merged accuracy holds for correct windows (all <=0.16 uV) [E]; the old "merged ~2x cost" is ~1.2–1.5x in re-measurement (minor [C] on cost only).

## 5. State-dependent events (Priority 5)

- [E] Smooth RC crossing prescan: 6.0/1.4/0.02 ns err at 10/5/1 us coarse → SAFE-auto (prescan + margin).
- [E/C] Step/blip prescan: COMPLETE MISS at all coarse tmax (P(detect)~w/TMAX); prior "err ~TMAX-scale" contradicted — error is UNBOUNDED, margin only helps after detection. Narrow-step triggers REQUIRE user trigger times.
- [E] Schmitt (zero TIME tokens): auto claimed "no windows needed" + ALLOW while edge err = 0.96*TMAX. Demonstrated UNSAFE-auto → message fixed (no-TIME verdict now carries the sharp-feature caveat) + classification REQUIRES-USER-WINDOW.
- Minimal trigger interface (sketch, NOT built — experiments do not force it yet [I]): stateful ⇒ user `window` mandatory; `t_est` ⇒ window = t_est ± margin rule; static containment converts silent-PASS to REFUSE.

## 6. Adversarial silent-failure testing (Priority 6)

13 SEVERE PASS-but-wrong holes [E] (rc=0, zero warnings, full-waveform maxerr):
S1 .param-TIME 5→0; S2 ternary 5→0 (now SUPPORTED via comparison shift);
S3 pwl(TIME) (now SUPPORTED via keep-all shift); S4 sine-phase inverted (REFUSE);
S5 NPN-TF 4.4 V tail (G1 deny); S6 behavioral-R 30% (G1 deny);
S7 far-window coarse-miss (containment REFUSE); S8/S9 ghost caps 0.41 V / 82.8 V
(completeness REFUSE); S10 V-PWL 5→0 (now shifted, exact); S11 omit-L 200%
(completeness REFUSE); S12 C-IC strip breaks seg-0 (strip-only-with-.ic fix);
S13 ddt-in-coarse smear (G1 deny + fine-bound); A2 terminal-swap G6 evasion
(both-terminals fix; R-slave/E-downstream allowance verified sound [C]).
Plus (main-thread review, verified [E]): seg-0 base-.ic preservation fix;
comma/space PWL arg splitting; SIN delay rules — pre-delay output is
Voff+Vamp*sin(phase) so seams strictly inside (0, td) are unrepresentable
(REFUSE), phase advances by oscillation-elapsed (t0−td) (an initial
180°-wrong version PASSED all gates with onset-continuous stitch: proof
that phase-class errors can be G5-blind and full-waveform spot-checks
remain necessary); damped post-delay SIN REFUSEd.

Correctly-refusing/sound controls held [E/C]: forward-B G6, D-TT G1, A5-auto
exact, with-L exact, reversed-I allowance, .temp/.options preservation.

## 7. Production change (minimal)

`window.py` only: G1 enforced INSIDE run_segmented with
4 new deny classes + K-handoff allowance; G2 shift coverage (comparisons,
pwl(TIME) keep-all, V-PWL prefix-drop, V-PULSE modular mid-train, V-SIN
phase) + fail-closed residual-TIME REFUSE; G3 per-edge auto-windows,
overlap REFUSE, static event containment; G4 enforced timestep bounds
(tmax_fine <= event bound, both <= tau_min/1000); G5 handoff completeness
+ observables + `>=`; G6 both-terminal coverage + E-output fix; seg-0 .ic
preservation; C-IC strip-only-with-handoff. `tests/test_hardening.py`: 30
checks covering every SEVERE repro — ALL PASS. All prior suites unmodified
and green.

## 8. Final classification (every tested category)

- SAFE: R, C, memoryless B/E/G/H/F, plain-D (IS/N, incl. large-OP-shift
  diode backgrounds within tau bounds), L+IC, B u-pulse/ternary/pwl(TIME)
  (shiftable), V-PWL/PULSE/SIN (shifted, SIN seams at 0 or >= td),
  smooth-threshold prescan.
- SAFE WITH EXPLICIT HANDOFF: L (device IC=), K (every winding IC= + UIC).
- REQUIRES USER WINDOW/TRIGGER: unquantifiable TIME (shiftable forms + user tmax_fine... unshiftable still refused), bare single edges, narrow-step triggers (user trigger times), hysteretic/stateful (user window mandatory).
- REFUSED: D-TT, D-CJO/CJP, Q/M/J, A, B-ddt, B-sin/bare-TIME/TIME-expr, behav-R/C, .param-TIME, T always, K-without-full-handoff, OSDI/X/O/U/Y/N/S/W/Z, E/G-LAPLACE/FREQ, overlapping windows, events-in-coarse, timesteps beyond bounds, incomplete handoff.
- UNRESOLVED: inductive coarse adequacy (L/K ride on handoff+G5; C-only
  tau scope); ddt relaxation (no principled refill bound); OSDI state
  class (no binary); D4 SPARSE pivot exact line (mitigation unaffected);
  V-PWL source entirely before segment start (fail-closed for now).

## 9. D4 secondary [U, unchanged]

No new tracing (no ngspice sources in env); pivtol/pivrel/KLU already shown inert; ISRC-OPEN static refusal remains the mitigation. Unaffected by this spike.

## 10. State-transfer contract question

No new abstraction introduced. The "contract" turned out to be three
enforced parameter checks (complete handoff sets, handoff⊆observables,
UIC always) + G5 — the existing vocabulary, now actually invoked.
Introducing a formal contract object is NOT justified by the experiments [I].
