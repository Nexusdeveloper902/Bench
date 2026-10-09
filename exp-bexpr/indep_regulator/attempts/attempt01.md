# Attempt 01 (only iteration needed) — reg33 LDO behavioral model

## Netlist under test (`reg33_final.inc`)
```
* reg33: 5V->3.3V LDO behavioral model (native ngspice, B-source only)
* pins: vin out gnd | nominal 3.3V, dropout 0.3V, soft limit ~0.5A
.subckt reg33 vin out gnd
Btgt tgt gnd V = (v(vin) < 3.6) ? (v(vin) - 0.3) : 3.3
Bout oint gnd V = 0.5*(v(tgt)+sqrt(v(tgt)*v(tgt)+1e-6)) - 0.05*i(vsens) - 50*0.5*((i(vsens)-0.5)+sqrt((i(vsens)-0.5)*(i(vsens)-0.5)+1e-6))
Vsens oint out DC 0
.ends reg33
```
Design: `Btgt` = min(3.3, vin-0.3) via single (non-nested) ternary;
`Bout` = smoothed clamp of target + 50mΩ Rout + soft current-limit foldback
(smooth-max around 0.5A, slope 50Ω, eps 1e-6 for C1 continuity);
`Vsens` (0V) senses load current via `i(vsens)`. All devices native (B/V/.subckt).

Testbench per case:
```
Vin vin 0 DC <vin> / X1 vin out 0 reg33 / Rload out 0 <R> / .op
```

## Results (`ngspice -b`, .op, all converged, no Error/singular/non-convergence)
| vin | Rload | v(out) | Iload=V/R | status |
|-----|-------|--------|-----------|--------|
| 5.0 | 6.6   | 3.2723 V | 0.496 A | nominal (just under limit; 25mV Rout drop) |
| 5.0 | 3.3   | 1.7505 V | 0.530 A | current-limited (soft limit engaged) |
| 3.4 | 6.6   | 3.0763 V | 0.466 A | dropout (3.4-0.3=3.1 minus Rout drop) |
| 5.0 | 0.1   | 0.0564 V | 0.564 A | short-circuit, current held ~0.5A |

Robustness extras: vin=0 -> out ≈ -24µV (≈0, clamp works); vin=3.6 (corner) -> 3.272V
(continuous); vin=2.0 -> 1.687V (tracks vin-0.3). Only benign `checkvalid` print-vector
warning (top-level `out` inside X1 scope; values read from .op table instead).

## Diagnosis
No failure occurred: syntax (ternary, `i(vsens)`, `sqrt`) accepted; topology solved by
Newton on first try due to smoothed (C1) limit corner; no convergence aids needed.
Accepted tolerances: 3.27V vs 3.30V nominal (realistic Rout), limit 0.53–0.56A vs 0.5A
target (soft knee by design). No revision required — iterations: 1.
