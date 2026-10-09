# Production continuation capability, spike 5: plan/execute split + refusal taxonomy

Date: 2026-10-05. Env: ngspice-47 batch (subprocess + timeout), Ryzen 5 5600G,
python 3.14.7. Changed files: `exp-window/continuation.py` (new, ~470 lines),
`exp-window/tests/test_continuation.py` (new, 82 checks ALL PASS).
`exp-window/window.py` untouched; all prior suites green unmodified:
`exp-window/tests/test_window.py` ALL PASS, `exp-window/tests/test_hardening.py`
ALL PASS, `exp-safety/tests/test_regression.py` ALL PASS.

Tags: **[E]** measured · **[D]** docs/code-reading · **[I]** strong inference ·
**[U]** unresolved.

## 0. Verdict

The validated mechanism productionizes as a thin plan/execute split plus a
first-class refusal taxonomy, with no solver changes, no model-class
expansion, and no generic state-transfer framework. Every previously
discovered silent-wrong reproducer remains supported-correct or refused
before execution through the new API, and all seven tested API-level
bypasses refuse instead of simulating wrongly [E].

## 1. Production abstraction (derived, not designed)

`ContinuationRequest` (`continuation.py`) carries exactly what the
invariants consume and nothing else: netlist, `t_stop`, explicit windows
(or `None` = auto-derive), `tmax_coarse`/`tmax_fine`, `handoff_nodes`,
`handoff_l`, `observables`, `resonant_period`. Callers receive either a
validated opaque `SegmentPlan` (segment boundaries + enforced-bounds and
capability snapshots) or `PlanRefused` (stable codes + human message +
agent-actionable hint + originating gate). Callers need no G1–G6 knowledge
[E: plan/execute round-trip on RC + ternary, §4].

Not generalized beyond evidence: interval stays `[0, t_stop]` (the only
shape `run_segmented` validates); timeout is deliberately excluded from the
plan fingerprint (safety-irrelevant); no new state-transfer abstraction was
introduced (cf. REPORT2 §10 — still not justified) [I].

## 2. Planning vs execution (emergent, not forced)

The split falls out of `window.py`'s existing structure: everything before
the first `tran_run` (`window.py:756-856`) is static analysis. `plan()`
mirrors that prefix line-for-line over the same helpers, then adds a
dry-run TIME-shift / seam-hygiene pass (`_rewrite_segment_lines` at every
segment start with dummy handoff values — value-independent, since G6 keys
on node names and the PWL prefix-drop keys on value strings, not handoff
magnitudes), so G2/G6 refuse before any simulation runs. `execute()`:
seal-check → static revalidation (defense in depth) → `STALE_PLAN` on any
post-planning mutation → delegation to the validated `run_segmented`
(single execution truth; an invalid plan is refused, never reinterpreted).
Guard-test proves `plan()` invokes zero simulations [E].

## 3. Refusal taxonomy (from failure modes, not speculative)

16 stable `RefusalCode`s; `classify_reason()` maps every `window.py` REFUSE
string to exactly one code. Each code is hit by at least one live spike-1–4
reproducer through the public API and asserted by code, not text [E]:
hidden state (Q, D-TT, D-CJO, ddt, T-line, X, A-device); unshiftable TIME
(.param-mediated, B sine-phase, E-VALUE, TIME-bearing .func, `*/`
precedence, param SIN); incomplete handoff (ghost cap, omit-L, K partial
winding); event-outside-window; overlapping windows; invalid window;
coarse/fine bound excess (event, tau, tau_L, SIN); resonant-period
unavailable; observable mapping; driven-node init; include/lib content;
unknown timing (bare TIME, single edge); seam discontinuity and
event-coverage (dynamic paths, unit-mapped). One behavioral-R/C TIME case
classifies as unshiftable-TIME (time-varying resistance is a transform
problem, not stored charge) — documented judgment call [I]. A fail-closed
`UNCLASSIFIED_REFUSAL` catch-all exists; no test hits it [E].

## 4. Fail-closed evidence [E]

* Supported-correct: RC segmented via plan+execute — peak exact vs
  continuous ref, final err 0.16 µV, executor boundaries identical to plan
  boundaries; ternary shift yields 5 V (naive restart yields 0 V).
* Bypass resistance (each refuses, none simulates): hand-built plan with
  forged seal → `INVALID_PLAN`; hand-built plan with *correct* seal on a
  Q-device → `UNSUPPORTED_HIDDEN_STATE` (classification not bypassable);
  dropped handoff / bad windows / R-value mutation / fine-step change /
  source retiming / appended `.include` / dropped resonant period after
  planning → `STALE_PLAN`, and fresh re-plans name the true underlying
  cause (incomplete handoff, event-outside-window, coarse/fine excess,
  included content). Two initial test-expectation failures on re-plan
  (in-bounds R change, auto re-derive after retiming) were correct
  fail-safe behavior, not holes — corrected to assert the safe outcome.
* No silent path added: `execute()` delegates all dynamics (EVENT
  coverage, G5 continuity) to the unchanged validated runner.

## 5. Residuals (unchanged, availability-only)

Sub-threshold bounded tolerance (explicit G5 REL=1e-3/ABS=1e-6 tradeoff)
and G5 mesh-phase false-refusals persist as documented in REPORT4 §6;
neither admits large wrong hidden state (§2 evidence stands, re-asserted
through the new API's completeness/observability ordering). `window.py`
remains directly importable as the experimental mechanism; production
callers must use `continuation.plan`/`execute` (convention, not enforced
in code) [U/I].
