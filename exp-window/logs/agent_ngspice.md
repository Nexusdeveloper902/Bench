# NGSPICE-BEHAVIOR: what `.ic`+`uic` restarts preserve/lose in ngspice-47 batch

Method: ngspice-47 batch (`ngspice -b`, subprocess, timeout per call), RC=`R10k/C100n`
step 5 V, RL=`R100/L10mH` step 5 V, windows ≤1 ms (2 ms one probe). Each probe =
concrete netlist + measured numbers. Raw: `agent_ngspice.json` (28 probes).
Labels: **[E]** measured · **[D]** doc/manual-string · **[I]** inference · **[U]**
unresolved · **[C]** contradicted (claim false as stated).

## 1. `.ic` semantics [E]

| form | result |
|---|---|
| `.ic v(cap)=X`, `.ic V(cap)=X` | honored identically (tail max err 7.7e-8 V btw. cases; `V`≡`v`). Wrong-value control `v(cap)=mid+1.0` restarts at 2.96737 vs true 1.96735 → honored, not ignored |
| `.ic i(l1)=X`, `.ic I(L1)=X` | **fatal `Error: .ic syntax error`, rc=1, no sim**. Branch-current `.ic` does not exist in batch |
| `.ic i(VL)=X` (V-source branch) | same fatal syntax error |
| `.ic v(out)=X` on inductor node | accepted but **useless for L state**: restart `i(L1)` starts at 5 µA instead of 49.66 mA; tail max err 0.0492 A (98% rel) |
| `L1 … IC=X` (instance line) + `uic` | **exact**: restart starts at 0.04966315 A, tail max err 7.6e-10 A |

RC split 500 µs/1 ms, correct `.ic`: overlay max err 7.7e-8 V, final err 4e-8 V
(3.16060294 vs 3.16060290) — continuation-exact for C state. RL continuation
needs **netlist rewriting** (`IC=` on the L line), not `.ic`. [E]

## 2. UIC vs OP restart [E, one expectation X]

- RC node voltage: `.ic v(cap)=mid` **without** `uic` starts at 1.96735 = `.ic`
  (log: `Initial Transient Solution … cap 1.96735`), tail vs uic-restart max
  err 3.9e-7 V. Wrong `.ic` without `uic` also starts at the wrong value
  (2.96735). I.e. node-voltage `.ic` defines the transient start **with and
  without** UIC on this build — "OP wipes `.ic`" is **[C]** for C state.
- Inductor instance `IC=` is where UIC bites [E]: `L1 … IC=0.0496631` **with**
  `uic` restarts at 0.04966315 A (exact); **without** `uic` restarts at
  0.05000000 A = DC-OP value (L≡short), IC silently discarded (Δ=3.4e-4 A).
  So: UIC's load-bearing effect is preserving **instance** ICs (L), not node
  `.ic` (C). Rule: always pass `uic` on continuation segments; it is necessary
  for L, harmless for C. [E+I]

## 3. Hidden-state inventory [E] (split while hidden charge/flux/history ≠ 0)

| # | state | probe | result |
|---|---|---|---|
| a | diode TT=50n stored charge | PULSE 5 V/300 ns into `R100+D(IS=1e-14,TT=50n)`; split 350 ns: source already 0 V (since 320 ns) but `v(d)`=0.729 V = recovery in progress | **FAIL**: `v(d)` max err 1.15 V, R-current max err 0.050 A vs 0.0073 A signal (6.8×); residual 0.054 V / 0.54 mA at 800 ns |
| b | B-source `ddt()` | differentiator on RC charge, split 500 µs (true dv/dt 3037 V/s) | **FAIL at boundary**: segment opens at 0.0 V/s (err = full scale 3037 V/s, history empty); reconverges (final err 4e-5, tail max 3.0 V/s rel 1e-3). Any threshold inside ~50 µs of the seam is unsafe |
| c | `idt()` | `B1 … V=idt(v(in),0)` and `sdt(v(in),0)` | **do not parse**: `Error: no such function 'idt'/'sdt'`, fatal. ngspice-47 B has `ddt` only. Safe pattern instead: C+Bi integrator (`Bi 0 mint I=v(cap)/10k`, `Cint mint 0 100n`) — state is a cap voltage → restart via `.ic v(mint)` **PASSES** (max err 7.9e-8 V) [E] |
| d | coupled L1/L2 K=1 | Vstep, split 100 µs (i1=34.8 mA, i2=−15.2 mA); `.ic`-only restart impossible (no syntax); zero-IC restart | **FAIL** (max err 9.8 mA, 20–65% rel, final 0.11 mA). Rewriting **both** `IC=` values → **PASS** (max err 9.8e-9 A) [E] |
| e | T-line `T1 Z0=50 TD=200n` | 5 V/300 ns pulse, split 300 ns (pulse inside line, `v(t2)`=0.0625 V rising) | **FAIL**: `.ic v(t2)` restart misses the in-flight pulse entirely — tail max err 2.5 V = full pulse height (100% rel) |

Gate: continuation-safe = node voltages + L-line-`IC` rewrites + C+Bi
integrators. Unsafe: diode TT charge, `ddt` history, K-coupled flux (unless all
winding ICs rewritten), T-line traveling energy (unrepresentable). [E]

## 4. TIME re-evaluation [E]

`B1 out 0 V=5*(u(TIME-500u)-u(TIME-500.1u))`, `.tran 10n 600u 0 10n uic`
(4-arg TMAX per safety bound 100 ns/10). Reference: peak 5.0, rise 500.0046 µs,
fall 500.1046 µs, 60011 rows, 1495 ms. Restart [400 µs,600 µs] as fresh run:
**TIME resets to 0** — output flat 0.0 (seg max 0.0, no crossing); the 500 µs
event never fires in-segment. `.tran` TSTART does not offset TIME (TIME is
per-run). Rewriting thresholds −400 µs (`TIME-100u/-100.1u`) refires at local
100.0046 µs = global 500.0046 µs (same 4.6 ns solver offset — bit-consistent).
Windowing needs TIME-offset rewriting of every B-source expression. [E]

## 5. PWL alternative [E]

Same 100 ns blip as `V1 in 0 PWL(0 0 499.9u 0 500u 5 500.1u 5 500.2u 0 1m 0)`,
**coarse 2-arg** `.tran 10u 1m uic` (no explicit TMAX): peak 5.0 at 500.0 µs,
rise-2.5 V at 499.95 µs, fall at 500.15 µs (exact to PWL corners), **134 rows,
9 ms** vs B-source+10 ns-TMAX **60011 rows, 1495 ms** (≈448× points, 161× wall
for identical event fidelity). Breakpoints are solver-automatic for PWL.
Two-event + slow RC (`R10k/C100n`, 2 ms, blips at 500 µs/1 ms): 248 rows,
12 ms, both peaks 5.0, cap ends 3.16088 V vs analytic 5(1−e^{−1})=3.16060 V
(err 2.8e-4, ramp-edge filtering) — multi-event cost stays flat. [E]

## 6. savestate/loadstate [C + E]

`strings /usr/bin/ngspice` → zero hits for savestate/loadstate. Batch probes:
`savestate …` → `savestate: no such command available in ngspice` (rc 0, no
file written); `loadstate …` → `loadstate: no such command available in
ngspice`. **No full-state save/restore exists in ngspice-47 batch.** [C]
(`write`/`wrdata` export signals only, no state.) [E]

## Verdicts for windowing

- `.ic`(+`uic`) carries node voltages exactly; inductor/coupled-flux state only
  via `IC=` line rewrites; diode-TT/`ddt`/T-line state is uncarriable — these
  classes must be re-simulated whole or bounded, never windowed. [E]
- Every continuation segment must use `uic` (cheap insurance; load-bearing for
  L) and must rewrite TIME-relative B-source expressions by −t_split. [E]
- Prefer restating known events as PWL (free solver breakpoints, ~100× cheaper
  than TMAX-bounding B-source TIME events). [E]
