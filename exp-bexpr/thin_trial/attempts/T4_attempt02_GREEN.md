# T4 attempt02 — regulator — GREEN (iteration 2/2)
## AST (fixed sense spelling only)
```python
tgt_expr=['tern', ['lt', ['v', 'vin'], ['num', 3.6]], ['sub', ['v', 'vin'], ['num', 0.3]], ['num', 3.3]]
out_expr=['sub', ['sub', ['smax', ['v', 'tgt'], ['num', 0]], ['mul', ['num', 0.05], ['i', 'Vsens']]], ['mul', ['num', 50], ['smax', ['sub', ['i', 'Vsens'], ['num', 0.5]], ['num', 0]]]]   # ['i','Vsens'] full device name
```
Changed vs attempt01: 'sens' -> 'Vsens' in both ['i',...] leaves. All else identical.

## Lowered text
```
.subckt reg33 vin out gnd
* [thin:target]
Btgt tgt gnd V=(((V(vin)<3.6) )?((V(vin)-0.3)):(3.3))
* [thin:softlimit]
Bout oint gnd V=(((0.5*((V(tgt))+(0.0)+sqrt(((V(tgt))-(0.0))*((V(tgt))-(0.0))+1e-12)))-(0.05*I(Vsens)))-(50.0*(0.5*(((I(Vsens)-0.5))+(0.0)+sqrt((((I(Vsens)-0.5))-(0.0))*(((I(Vsens)-0.5))-(0.0))+1e-12)))))
Vsens oint out DC 0
.ends
```
out: `(((0.5*((V(tgt))+(0.0)+sqrt(((V(tgt))-(0.0))*((V(tgt))-(0.0))+1e-12)))-(0.05*I(Vsens)))-(50.0*(0.5*(((I(Vsens)-0.5))+(0.0)+sqrt((((I(Vsens)-0.5))-(0.0))*(((I(Vsens)-0.5))-(0.0))+1e-12)))))`
Note I(Vsens) now matches `Vsens oint out DC 0`.

## Testbench
```
Vin vin 0 DC <vin>; X1 vin out 0 reg33; Rload out 0 <R>; .op
```

## Sim result
- vin=5 R=6.6 -> v(out)=3.275188 (expect ~3.3) I=0.496241A errs=[]
- vin=5 R=0.1 -> v(out)=0.056431 (expect small, limited) I=0.564307A errs=[]
- Nominal 3.275V (25mV Rout drop at 0.496A, just under 0.5A knee); short holds ~0.56A (soft knee by design). Matches native reg33 reference (3.272V / 0.0564V). Converged, no errors. smax eps 1e-12 (thin fixed) proved C1-smooth enough — no extra cap needed.

## Diagnosis
PASS. Layer's smax helper was sufficient despite fixed eps (native used 1e-6); no convergence aid needed.
Iterations-to-green: 2 (1 sense-name fail + 1 pass).
