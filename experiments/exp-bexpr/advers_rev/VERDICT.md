# Adversarial verdict — exp-bexpr claims 1–10 + fresh breakage hunt

Engine: `ngspice-47` (batch `ngspice -b file.cir`). Raw re-runs: `advers_rev/advers_raw.json`
(53 probes) plus inline follow-ups below. Prior logs in `logs/` re-checked, not re-trusted.

Verdict scale: **REPRODUCED** (claim holds as stated) / **CONTRADICTED** (fresh run
disproves it) / **QUALIFIED** (holds with a needed scope correction).

## Per-claim verdicts

### 1. `limit()` silently wrong — REPRODUCED, and understated [C1]
`B1 out 0 V=limit(V(in),0,1)` with `V(in)` = −5 / 0.5 / 5 → `v(out) = 0` all three
runs (rc=0, no error flags). `min(max())` control → `1.0`. So the clamp-shaped call
is dead-zero for every signal input. Worse than claimed:
- 3-arg constants pass through unclamped: `limit(5,0,1)→5`, `limit(10,0,1)→10`,
  `limit(-5,0,1)→−5` (rc=0, silent).
- 2-arg constants are **nondeterministic across identical runs** (same netlist,
  sequential runs): `limit(5,2)→{3,3,7}`, `limit(2,3)→{5,−1,−1}`,
  `limit(0.5,1)→{−0.5,1.5,−0.5}` — outputs are ±-combinations of the inputs, i.e.
  uninitialized-memory-class garbage, not a function. Signal-arg form was stable 0
  (10/10 runs), so the original "always 0" observation is the stable projection of
  a deeper bug. Thin's refusal of `limit()` is, if anything, under-justified.

### 2. Single `&`/`|` fail; `&&`/`||`/`!` work — REPRODUCED [C2]
```
B1 out 0 V=(V(in)>1)&(V(in)<3)?5:0   →  rc=1, "unknown parameter (&)", no rows
B1 out 0 V=(V(in)>3)|(V(in)<1)?5:0   →  rc=1, "unknown parameter (|)", no rows
B1 out 0 V=(V(in)>1)&&(V(in)<3)?5:0  →  rc=0, v(out)=5
B1 out 0 V=(V(in)>3)||(V(in)<1)?5:0  →  rc=0, v(out)=5
B1 out 0 V=!(V(in)>1)?5:0            →  rc=0, v(out)=5 (Vin=0)
```
Boolean-multiply workaround `(V(in)>1)*(V(in)<3)*5` also runs (rc=0, 5.0).

### 3. `idt()`/`sdt()` unavailable, `ddt()` works — REPRODUCED [C3]
`idt(V(in))` and `sdt(V(in))` in `.tran` → rc=1, `Error: no such function 'idt'/'sdt'`.
`B1 out 0 V=ddt(0.001*TIME)` in `.tran 10u 2m UIC` → rc=0, 200+ rows at `1.0e-03`.
(No qualification needed on availability; see N8 for a `ddt`-in-`.op` trap.)

### 4. `gauss(0,1)` segfaults (RC=139) — REPRODUCED, scoped [C4]
`B1 out 0 V=gauss(0,1)` + `.tran` → rc=**−11** (Python; = 128+11 = shell 139,
SIGSEGV), **empty stdout/stderr**, no `errs` flags (there is no output to flag).
Contrast: 1-arg `gauss(1)` fails *cleanly* (rc=1, `ERROR: Incomplete function gauss
... bogus gauss form`). So the segfault is specific to the 2-arg form; the claim
holds for `gauss(0,1)` exactly as stated.

### 5. Bare `TEMP` undefined; `temper` = 27 — REPRODUCED [C5]
`V=TEMP` → rc=1, `Undefined parameter [temp] / Cannot compute substitute`.
`V={TEMP}` → same rc=1 pair. `V=tnom` → same rc=1 pair.
`V=temper` → rc=0, `v(out)=2.7e+01`. Exact as claimed.

### 6. `V(typo)` → convergence wall, no name error — REPRODUCED [C6]
`B1 out 0 V=V(inn)*2` (top level) → rc=1, **no unknown-node line**; instead
`DC solution failed`, singular-matrix warnings, `Last Node Voltages` table that
*lists* `inn` at 0 V (the typo is silently admitted as a node). Same signature for
`V(nonexistent)`. A name error would name the offender; ngspice instead burns
solver iterations and reports a wall.

### 7. `1/0` → 1e32, `exp(100)` → 2.7e43, both silent — REPRODUCED [C7]
`B1 out 0 V=1/V(in)`, `Vin=0` → `v(out)=1.000000e+32`, rc=0, zero flags.
Literal `B1 out 0 V=1/0` → identical `1e32`, rc=0, silent (thin's div-zero guard
only fires on AST-level literal zero, which this confirms is the right layer —
ngspice will never complain).
`B1 out 0 V=exp(V(in))`, `Vin=100` → `v(out)=2.688117e+43`, rc=0, silent.

### 8. Latch fails even with caps; resistive Schmitt runs — QUALIFIED [C8]
- Latch `.op`: `B1 q 0 V=(V(qb)>2.5)?0:5` + cross-coupled `B2`, R loads → rc=1,
  `DC solution failed`. Holds.
- Latch `.tran UIC` + 1pF caps, **unseeded** → rc=1, `Timestep too small; initial
  timepoint: trouble with node "q"`. Holds (caps alone do not save it).
- Latch `.tran UIC` + caps + **asymmetric `.ic v(q)=5`** → rc=0, settles to
  `v(q)=5, v(qb)=0` (final row `2.000000e-03 5.000000e+00 0.000000e+00`).
  **This breaks the universal reading of "fails":** the latch holds state fine in
  tran once seeded; the disease is DC/OP-finding, not state-holding. Caps are
  insufficient, but caps + one-sided IC are sufficient.
- Resistive-feedback Schmitt (`R1 in mid 10k`, `R2 out mid 100k`,
  `B1 out 0 V=(V(mid)>2.5)?5:0`) → rc=0, 320 rows, output swings 0↔5
  (106 pts >4 V, 214 <1 V). Runs *and switches*. (First attempt with 10k/10k
  divider sat at 0 — a sizing error, not a sim failure; threshold never reached.)
- Note: the `run_l34.py` L3.5 self-read form (`V(out)` inside its own device's
  expr **plus** a `B1 mid 0 I=V(mid)/1k` shunt) errors with `singular matrix:
  check nodes out and b2#branch`. A bare self-read ternary in tran without that
  shunt ran rc=0 here, so L3.5's failure is the combination, not self-read alone.

### 9. Thin output bit-identical to hand-native — REPRODUCED [C9]
Fresh sims (`.op`, `print v(out)`), exact float equality:
gain `5.0==5.0`, affine `1.7==1.7`, clip `1.0==1.0`, diode `1.035518==1.035518`
(`exact_eq=True`, diff 0.0). E.g. thin lowers gain to
`Bg1 b gnd V=({G}*V(a))` under `.subckt gain10 a b gnd G=10` — textually
different netlist, numerically identical solution.

### 10. Validation ≈ µs vs ≈ 6 ms sim — REPRODUCED [C10]
`lower_expr(clamp…)` mean 2.76 µs/expr (timeit, n=2000); `.op` sim wall mean
5.98 ms (n=5). Ratio ≈ 1:2200. "Negligible" is fair.

## New breakages (fresh probes, all re-run above)

1. **`pwr()` sign-flips negative bases — silent.** `pwr(-2,2)→−4`, `pwr(-2,3)→−8`,
   `pwr(-8,0.333)→−2`, while `pow(-2,2)→+4` and `pow(-8,0.333)` → convergence
   wall (rc=1). So `pwr(x,y) = −|x|^y` for x<0: it *returns the negative of* the
   power. Thin allowlists `pwr` 1:1 with no warning — a user porting `pow`→`pwr`
   for "safety" silently negates every negative-base result. Strongest new
   counterexample.
2. **Thin guards `exp`/`1/0` but not `ln(0)`/`sqrt(neg)`.** `ln(0)→−1e99` rc=0
   silent; `sqrt(-4)` → `DC solution failed` wall (rc=1). Same hazard class as
   claim 7, unguarded — a hole in R5, fixable at the same layer (reject/flag
   constant-foldable `ln/log/log10` of ≤0, `sqrt` of negative literal; warn on
   signal form like `exp` does today — actually `exp` of a *signal* is not
   guarded either, only literals, so the gap is wider than one function).
3. **`u(0)=0.5`, silently.** `u(-1)→0`, `u(0)→0.5`, `uramp(0)→0`. A comparator
   built on `u()` has a mid-rail output exactly at threshold — smoothed step the
   docs don't advertise in the probe notes, and thin's `u` passthrough says
   nothing about it.
4. **`ddt()` in `.op` silently returns 0; `TIME` in `.op` silently 0.**
   `B1 out 0 V=ddt(V(in))` under `.op` → `0.000000e+00`, rc=0. Same for
   `V=TIME*1000` under `.op`. Thin allowlists `ddt`/`TIME` with no
   analysis-mode guard — DC use gives a plausible-looking zero. (Consts: how the
   L3-ddt tran value 1e-3 was obtained correctly only under `.tran UIC`.)
5. **`1M` is milli, not mega — silent 1e9× error.** `V(in)*1M` (Vin=2) →
   `2.000000e-03`; `V(in)*1meg` → `2.000000e+06`. Thin `_NUMVAL` accepts an `m`
   suffix (regex `...|m|...`, case-insensitive), so a user-written `1M` meaning
   mega sails through validation and simulates a billion times too small.
   Recommend rejecting bare `m`/`M` or forcing `meg`.
6. **`I(B1)` self-sensing "works" but poisons the matrix.** `B1 out 0 I=I(B1)` →
   rc=0, `v(out)=0`, **but** with `singular / failed / warning` flags set — ran
   with warnings, not clean. Sensing a *different* B branch (`I(B1)` from `B2`'s
   expr) is clean (5.0, no flags). Thin's `selfref_warn` heuristic (substring on
   the AST string) would miss `I(B1)`-style self-reference; worth a rule.
7. **`pwl` validates ordering loudly (good, no action).** Unsorted
   `pwl(x,2,99,0,0,1,1)` → rc=1 with error flags — counter-example to the
   "silent" pattern; ngspice *does* check this one.
8. **`.param` expressions evaluate** (`.param GAIN=5*2` → gain 10.0, rc=0) and
   node names are case-insensitive (`V(IN)` works, 4.0). Both benign; recorded
   so nobody "discovers" them as bugs later.

## Scorecard
REPRODUCED: C2, C3, C4, C5, C6, C7, C9, C10 (8). QUALIFIED: C8 (seeded tran
latches; failure is OP-finding, not state-holding). REPRODUCED-but-understated:
C1 (2-arg `limit` is nondeterministic garbage, not just wrong). CONTRADICTED: none.
Strongest new counterexample: **`pwr()` negates negative-base powers silently**
(#1), followed by the **`limit()` nondeterminism** extension and the
**`1M`-vs-`1meg`** validation hole (#5).
