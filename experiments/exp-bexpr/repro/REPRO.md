# Reproduction report — exp-bexpr native-matrix / L1-L2 / thin claims

Repro agent re-ran all five scripts from `<repo>/experiments/exp-bexpr`
(`probe_native.py`, `probe_round2.py`, `probe_round3.py`, `run_l12.py`,
`test_thin.py`) plus the standalone gauss segfault check, and compared
measured values against the `logs/*.json` files **as read before any script
was executed**.

> Methodology hazard (please read first): every probe script overwrites its
> `logs/*.json` output file in place on each run (`probe_native.py:116`,
> `run_l12.py:97`, `test_thin.py` "saved logs/thin_results.json", round2/round3
> likewise). The "logged" values below are from pre-run reads; post-run files
> contain repro measurements. Back up `logs/` before re-running.

## Environment

- `ngspice --version` head:
  `ngspice-47 : Circuit level simulation program / Compiled with KLU Direct
  Linear Solver / U. C. Berkeley CAD Group ... / Copyright 2001-2026, The
  ngspice team.`
- Log header string: `ngspice-47 Arch 47-1` — consistent with this binary.
- Date of repro: Sat Oct 3 2026, ~20:00 -0500. All scripts ran clean, rc=0
  (no script crashed; ngspice subprocess segfaults captured as rc=-11/139).

## Per-figure results

| # | Figure (claimed) | Logged | Measured (repro) | Verdict |
|---|---|---|---|---|
| 1 | Round-1 native matrix 43/59 ok | 59 entries, 43 ok | `Total: 43/59 ok` (stdout); re-read JSON 59 entries / 43 ok | MATCH |
| 2 | Round-1 per-label pass/fail pattern | 16 fails: op-mod, logic-and, logic-or, if-func, fn-atan2, fn-int, fn-table, fn-poly, fn-stp, fn-idt, fn-white, fn-gauss, fn-foo-unknown, ref-temp, i-form, g-behav | identical 16 fails, same labels; other 43 (incl. silent ref-param) ok | MATCH |
| 3 | Round-2 15/25 ok | 25 entries, 15 ok | `15/25 ok`, all 25 labels match (ok flag, value, rc) | MATCH |
| 4 | Round-2 spot values (temper=27, pwr-neg-base=-4.0, param-subckt=2.5) | 2.700000e+01 / -4.00000e+00 / 2.500000e+00 | identical | MATCH |
| 5 | Round-3 12/18 (incl. div-zero=1e32, ln-zero=-1e99, exp-huge=2.688117e43, u(0)=0.5) | 18 entries, 12 ok, values as claimed | `12/18 ok`, all 18 labels match incl. values | MATCH |
| 6 | B-diode Vf = 1.035518 (ngspice value) | 1.035518 (L2-Bdiode, -nocap, -param all three) | 1.035518e+00 all three | MATCH |
| 7 | Analytic (bisection) Vd = 1.035518; B-diode "exact to 6dp" | 1.035518, note "bisection" | script prints `analytic_diode_Vd 1.0910727285836133` | **MISMATCH** (see note A) |
| 8 | D-model +0.59 mV vs analytic | 1.036110 (= +0.592 mV over 1.035518) | ngspice value 1.036110e+00 identical; delta vs script's analytic is −54.96 mV | ngspice MATCH / derived claim MISMATCH (see note A) |
| 9 | L1 values (4.999995, 1.666667, 5.0, 5.0, 1.7, 1.0) | as claimed | identical to 6+ dp | MATCH |
| 10 | L2 tanh 9.866143 vs expect 9.86614298; log ln(2.71828)=0.9999993; rev50 −50.0 / ref −5.41542 | as claimed | identical | MATCH |
| 11 | Thin equivalence 4/4 identical (gain, affine, clip, diode) | 4× identical=true | 4× identical=True (hand==thin: 5.0, 1.7, 1.0, 1.035518) | MATCH |
| 12 | Translation ~0.002 ms/expr | 0.00183 ms/expr | 0.0020 ms/expr (stdout 200×: 0.5 ms total); re-run JSON 0.00228 | MATCH (same order, timer noise) |
| 13 | All 9 rejection tests rejected (no-if, no-limit, no-gauss, no-idt, no-unknown-fn, no-div-zero, typo-node, unknown-param, exp-overflow) | 9/9 rejected=true | 9/9 rejected + 4/4 injection guards rejected | MATCH |
| 14 | gauss(0,1) in B-source segfaults ngspice-47 (`/tmp/g1.cir` + `.tran`, `ngspice -b`) | (thin guard message asserts it; round2 log shows empty log) | `Segmentation fault (core dumped)`, **rc=139**; round2 `gauss-2arg rc=-11` (SIGSEGV in subprocess) | MATCH |

### Note A — the one genuine mismatch (analytic reference)

`run_l12.py:25-34` `shockley_vd()` is a Newton solver started at vd=0.7 with
100 iterations. From vd0=0.7 the first step jumps deep into the exponential
region (~4.9 V) and each subsequent Newton step retreats only ~nVt (0.0388 V),
so 100 iterations stall at **1.091073**, not the root. Independent bisection
(200 iters, verified during repro) gives **Vd=1.035518**, residual −1.7e−08 —
confirming the ngspice B-diode value is exact to 6 dp and the D-model offset
is truly +0.59 mV. So the *physics* claims hold, but the *checked-in script
does not reproduce the logged analytic figure*: the log's `analytic_note`
says "bisection" while the script implements (under-converged) Newton. Fix:
replace `shockley_vd` body with bisection or raise iterations + better vd0.

### Minor observations (not verdict-affecting)

- `probe_native.py:97-107`: `ref-param` result is appended to JSON but skipped
  by `continue` before the `print`, so stdout shows 58 lines while JSON/total
  cover 59 probes. Benign but confusing when eyeballing stdout.
- `run_l12.py` executes the full suite on `import` (no `__main__` guard); the
  repro accidentally ran it twice via `from run_l12 import shockley_vd`.
  Deterministic, no harm.
- `logs/l12_results.json` on disk now carries `analytic_diode_Vd=1.09107...`
  from the repro run (overwrite hazard above); pre-run value was 1.035518.

## Overall verdict: PARTIAL

13/14 figures reproduce exactly (including every ngspice-measured number, the
segfault, and all thin-layer guards). The single failure is the analytic
bisection reference: the logged 1.035518 is correct (independently verified)
but `run_l12.py` as checked in computes 1.091073, invalidating the two
figures derived from it ("B-diode exact vs bisection", "D-model +0.59 mV")
until the script's solver is fixed.
