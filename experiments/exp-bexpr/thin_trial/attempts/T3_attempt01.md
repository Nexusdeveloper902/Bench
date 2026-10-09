# T3 attempt01 — comparator thr 1V, out 0/5V — GREEN (iteration 1/1)
## Spec
Comparator, threshold 1V, output 0/5V. Tests: in=0.5 -> 0; in=2 -> 5.

## AST
```python
expr=['tern', ['gt', ['v', 'inp'], ['num', 1]], ['num', 5], ['num', 0]]
devices=[{'kind':'Bv','name':'o1','p':'outp','n':'gnd','tag':'comp','expr':expr}]
```

## Lowered text
```
.subckt comp1v inp outp gnd
* [thin:comp]
Bo1 outp gnd V=(((V(inp)>1.0) )?(5.0):(0.0))
.ends
```
single: `(((V(inp)>1.0) )?(5.0):(0.0))`

## Testbench
```
Vin inp 0 DC <vin>; Rin inp 0 1k; X1 inp outp 0 comp1v; Rload outp 0 10k; .op
```

## Sim result
- vin=0.5 -> v(outp)=0.000000 (expect 0) errs=[]
- vin=2.0 -> v(outp)=5.000000 (expect 5) errs=[]
- Sharp tern discontinuity solved fine at fixed-DC .op (no sweep through the corner in Newton).

## Diagnosis
PASS first try. tern maps 1:1 to ngspice ?: as documented. No smoothing needed for DC tests.
Iterations-to-green: 1.
