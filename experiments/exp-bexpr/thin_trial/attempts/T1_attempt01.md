# T1 attempt01 — 10x amplifier, clip +-12V — GREEN (iteration 1/1)
## Spec
10x amplifier, output clips at +-12V rails. Tests: in=0.5 -> 5; in=2 -> 12.

## AST (thin only, no raw B strings)
```python
pins=['inp', 'outp', 'gnd']
params={'G': 10}
expr=['clamp', ['mul', ['param', 'G'], ['v', 'inp']], ['num', -12], ['num', 12]]
devices=[{'kind':'Bv','name':'o1','p':'outp','n':'gnd','tag':'amp-clip','expr':expr}]
```

## Lowered text (build_subckt output)
```
.subckt amp10x inp outp gnd G=10
* [thin:amp-clip]
Bo1 outp gnd V=min(max(({G}*V(inp)),-12.0),12.0)
.ends
```
lower_expr single: `min(max(({G}*V(inp)),-12.0),12.0)`

## Testbench (top-level only, model itself is thin-generated)
```
Vin inp 0 DC <vin>; Rin inp 0 1k; X1 inp outp 0 amp10x; Rload outp 0 1k; .op
```

## Sim result (`ngspice -b`, .op)
- vin=0.5 -> v(outp)=5.000000 (expect 5.0) errs=[]
- vin=2.0 -> v(outp)=12.000000 (expect 12.0 clipped) errs=[]
- No Error/singular/non-convergence strings; Newton converged first try.

## Diagnosis
PASS first try. clamp() lowered to min(max()) as documented (R1). No layer friction encountered.
Iterations-to-green: 1.
