# Bexpr spike: native ngspice behavioral expressions vs a thin validated layer

Date: 2026-10-03. Env: ngspice-47 (Arch 47-1, KLU, SPARSE 1.3), libngspice.so.0.0.16,
python 3.14.7, Ryzen 5 5600G. Prior spike (`experiments/`, `results/`, `REPORT.md`) untouched
(mtimes ≤ 12:48, this work 19:5x–20:0x, isolated in `exp-bexpr/`).

Claim tags: **[E]** verified experimentally · **[D]** authoritative docs · **[I]** strong
inference · **[U]** unresolved · **[X]** contradicted. Repro: `repro/REPRO.md` (13/14 figures
MATCH; 1 MISMATCH found and fixed — see §9). Adversarial review: `advers_rev/VERDICT.md`
(8/10 held, 1 qualified, 1 understated-against-native, plus new holes we then closed).

## Recommendation

**v0.1 should author component models in a thin validated expression AST (`thin.py`,
~250 lines) that lowers deterministically to native B-source text — not raw ngspice strings.**

This is not a language or IR with new semantics. It is an allowlist + typo guard +
lowering with exactly the rules the experiments below forced. Lowered output simulates
**bit-identical** to hand-written native (4/4 models, float equality **[E]**), costs
~3 µs/expr vs ~6 ms sim **[E]**, and converts ngspice's worst failure modes (silent
wrong answers, segfault, convergence walls) into pre-sim errors with fixes attached.
Every rule traces to a numbered finding; nothing aesthetic.

If you disagree, the fast path to overturn this: show an agent authoring loop where raw
B-strings beat thin.py's iteration counts in `thin_trial/` + §7, or show a needed model
that thin.py cannot lower (extend `CALLS_1TO1` — the allowlist is the only growth point).

## 1. What native ngspice-47 B-expressions actually do (§evidence: probe_*.py, logs/*.json)

Supported **[E]**: `+ - * / ^ **` (both), unary, `> < >= <= =`, `?:` (incl. nesting),
`&& || !`; `exp ln log log10 sqrt pow sin cos tan asin acos atan sinh cosh tanh abs
min max floor ceil sgn u uramp pwl ddt`; `V(a) V(a,b) I(Vsense) TIME temper .param {}
E-value G-cur G-poly` subckt params; scale suffixes (`1meg` etc). Bare `& |` do NOT work
("unknown parameter (&)" **[E]**) — C-style doublings required. `u(0)=0.5` **[E]**.

Missing / broken **[E]**: no `IF() int table poly stp step idt sdt atan2 arctan white
TEMP tnom` ("no such function", fatal pre-run — loud, fine). `gauss(0,1)` in a B-source
**segfaults ngspice (rc=139/−11, empty output) [E, reproduced ×3, subprocess-contained]**.
`limit()` is silently wrong: signal args → 0 regardless of bounds; 3-arg consts pass
through unclamped; adversarial found 2-arg const form **varies run to run** (3,3,7)
**[E]**. `pwr(-2,2)=-4` while `pow(-2,2)=+4` (sign-preserving trap) **[E-adv]**.
`1/0` → 1e32 silently; `exp(100)` → 2.7e43 silently; `ln(0)` → −1e99 silently **[E]**.
`V(typo)` invents a floating node → gmin/stepping convergence wall, no name error at
top level (in-subckt case at least warns `check node x1.aa`) **[E]**. `TIME`/`ddt` are
silently 0 in `.op` **[E-adv]**. Bare `m`/`M` suffix = milli (1e9 off mega) **[E-adv]**.

## 2. Levels 1–2: trivial + nonlinear (run_l12.py, logs/l12_results.json)

L1 (R/V/I/gain/affine): all exact vs analytic, ~6 ms batch wall **[E]**. L2: B-diode
Vf=1.035518 = bisection Shockley to 6 dp (0.000 mV err); D-model +0.592 mV (reproduces
prior e07's 0.6 mV) **[E]**. tanh/clip/pwl/log/param all exact **[E]**. C1 helper cap
unnecessary here (nocap identical) **[E]**. rev −50 V: B gives −50 (no breakdown) vs
D-model −5.42 (BV=5) — native B omits physics you don't write **[E]**. (My first Newton
reference stalled at 1.091073 — fixed to bisection; cautionary tale recorded.)

## 3. Level 3: dynamic (run_l34.py, logs/l34_results.json)

Works **[E]**: sine-via-TIME (±4.9992), u()-step, ddt (exact 1e-3 steady state), C +
B-current-source integrator (2.000000 V @2 ms, exact vs I·t/C), comparator, PWL(TIME).
Boundary **[E]**: B-expr reading its **own output node fails** (singular/timestep);
cross-coupled B latch fails even with caps unless seeded with asymmetric `.ic`
(adv: OP-finding failure, not state-holding). Working patterns: RC state nodes,
resistive-feedback Schmitt (runs, 323 pts, switches when sized). No `idt()` — state
must be C + source, never an operator. Hysteresis/memory is a topology recipe, not
an expression feature.

## 4. Level 4: realistic (same runner + independent agent)

LED (1.365 V consistent w/ hand calc), sensor 0.75 exact, nonlinear load −15 mA exact,
comparator 5/0, regulator subckt: 3.30 nom / 2.68 overload / 3.10 dropout / 0.21 short
**[E]**. Cost: regulator took **3 topological iterations** (series-vs-shunt confusion —
an expression layer cannot fix this; recorded as out-of-scope **[I]**). Independent
subagent built a better regulator (smoothed corners, short holds 0.56 A) in **1 iter**,
confirming native is generable and smoothing helps convergence **[E-indep]**.

## 5. Agent-generation test (this session's iteration logs + thin_trial/ + indep_regulator/)

Native authoring failures observed (all real): `&`→`&&` (1 wasted run), `limit()` trap,
`I=` into open node (singular), B current-sign error (integrator −2.0, caught by numeric
check), regulator topology ×3, latch abandoned → RC workaround. Dominant cost is
**topology + silent-wrong-answers**, not syntax. Thin-layer trial (independent agent,
T1–T4 via AST only): 1/2/1/2 iters; T4's only fatal (`['i','sens']` bad sense name) was a
**layer validation hole (since fixed: F1)**, not user error; frictions F1–F6 rated, all
fixed except documented `.op`-context limits (§8). Verdict from trial: helped more than
hindered, ~0.003 ms/expr **[E-indep]**.

## 6. The thin layer (thin.py, test_thin.py, logs/thin_results.json)

Rules and their forcing evidence: `clamp→min(max())` (§1 limit); `and/or/not→&&/||/!`
(§1); allowlist rejecting if/int/table/poly/stp/idt/sdt/atan2/TEMP/white/gauss(REFUSED:
segfault)/limit/pwr/ln≤0/sqrt<0/exp>700/div-by-0 (all §1); node allowlist incl. p/n
(§1 typo wall); sense-name allowlist (trial F1); self-ref raise w/ RC recipe (§3);
`m`-suffix refuse (§1-adv); R/C `['param',..]` refs (trial F3); identifier + numeric
audit + output sandbox scan (all injection strings rejected **[E]**). Lowered text is
plain B-sources with `* [thin:tag]` traceability comments. Equivalence 4/4 identical,
12/12 + 5/5 + 4/4 rejections green, translate 0.003 ms, libngspice alter+run identical
(27.3 vs 27.5 ms) **[E]**.

## 7. Debuggability / security / perf (summary)

Parse errors (`no such function 'foo' at line N`) are decent **[E]**; convergence walls
from typos/float-nodes are hostile **[E]** — thin.py moves 6 fault classes pre-sim with
construct names. Sandbox: pure expr AST cannot emit control/shell/include/osdi by
construction + audit **[E]**; batch containment held the gauss segfault; in-process
libngspice callers must still sandbox (crash in-process = dead worker) **[I]**.
Perf: representation cost is noise (µs vs ms sim); iteration latency is sim-dominated —
thin.py's win is fewer wasted sims, not faster sims **[I]**.

## 8. Concrete limitations (thin.py does NOT fix these)

Topology errors (series/shunt, signs, drive Thevenin — T2 attempt01's silent 1.78e7 OP);
`.op`-vs-`.tran` semantic traps (TIME/ddt-0, u(0)=0.5) — documented only; expert latch
(`allow_selfref` bypass); breakdown/thermal/second-order physics (must be written, §2);
smax eps fixed 1e-12; allowlist will need curation as models grow (that is the intended
and only growth surface).

## 9. Reproducibility

`cd exp-bexpr && python3 probe_native.py probe_round2.py probe_round3.py run_l12.py
run_l34.py test_thin.py` regenerates all logs (~3 min). Repro agent: 13/14 MATCH incl.
gauss rc=139; the 1 MISMATCH (Newton-vs-bisection analytic) was fixed in `run_l12.py`
and re-verified (§2 tail). Adversarial: `advers_rev/VERDICT.md` + 53 fresh probes
(`advers_raw.json`); its two strongest hits (limit nondeterminism, pwr/1M/ln(0)/sqrt/u(0)
traps) are now enforced or documented above. Open unknowns **[U]**: stiff/convergence-
hostile circuits; multi-tone/transient tolerance sensitivity; >2-instance behavior
(inherited from prior spike).

## Files

`helper.py probe_native.py probe_round2.py probe_round3.py run_l12.py run_l34.py
thin.py test_thin.py | logs/*.json (6) | advers_rev/ repro/ indep_regulator/
thin_trial/ (6 attempts + FRICTIONS.md) | REPORT.md (this file)`
