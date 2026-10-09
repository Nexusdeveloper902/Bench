# T4 attempt01 — 5V->3.3V soft-limit regulator — FAIL (sense-name trap)
## Spec
5V-to-3.3V regulator, soft current limit ~0.5A. Tests: vin=5/load=6.6 -> ~3.3; short 0.1ohm -> limited.

## AST (naive sense spelling)
```python
tgt_expr=['tern', ['lt', ['v', 'vin'], ['num', 3.6]], ['sub', ['v', 'vin'], ['num', 0.3]], ['num', 3.3]]
out_expr=['sub', ['sub', ['smax', ['v', 'tgt'], ['num', 0]], ['mul', ['num', 0.05], ['i', 'sens']]], ['mul', ['num', 50], ['smax', ['sub', ['i', 'sens'], ['num', 0.5]], ['num', 0]]]]   # uses ['i','sens'] (Vsense suffix, matches senses-set naming)
devices=[
  {'kind':'Bv','name':'tgt','p':'tgt','n':'gnd','tag':'target','expr':tgt_expr,'internal_nodes':['tgt','oint']},
  {'kind':'Bv','name':'out','p':'oint','n':'gnd','tag':'softlimit','expr':out_expr},
  {'kind':'Vsense','name':'sens','p':'oint','n':'out'},
]
```
Design mirrors proven native reg33 (min(3.3,vin-0.3) target + 50mΩ Rout + 50Ω soft-foldback via smax): term1=smax(v(tgt),0), term2=0.05*i, term3=50*smax(i-0.5,0).

## Lowered text (thin accepted without complaint)
```
.subckt reg33 vin out gnd
* [thin:target]
Btgt tgt gnd V=(((V(vin)<3.6) )?((V(vin)-0.3)):(3.3))
* [thin:softlimit]
Bout oint gnd V=(((0.5*((V(tgt))+(0.0)+sqrt(((V(tgt))-(0.0))*((V(tgt))-(0.0))+1e-12)))-(0.05*I(sens)))-(50.0*(0.5*(((I(sens)-0.5))+(0.0)+sqrt((((I(sens)-0.5))-(0.0))*(((I(sens)-0.5))-(0.0))+1e-12)))))
Vsens oint out DC 0
.ends
```
tgt: `(((V(vin)<3.6) )?((V(vin)-0.3)):(3.3))`
out: `(((0.5*((V(tgt))+(0.0)+sqrt(((V(tgt))-(0.0))*((V(tgt))-(0.0))+1e-12)))-(0.05*I(sens)))-(50.0*(0.5*(((I(sens)-0.5))+(0.0)+sqrt((((I(sens)-0.5))-(0.0))*(((I(sens)-0.5))-(0.0))+1e-12)))))`
Note I(sens) — thin's lower_expr validated IDENT only, did NOT check against Vsense devices.

## Testbench
```
Vin vin 0 DC 5; X1 vin out 0 reg33; Rload out 0 6.6; .op
```

## Sim result
- v(out)=None (expect ~3.3) errs=['\\berror\\b', '\\bwarning\\b', 'no such (parameter|device|vector)']
- ngspice fatals:
```
Fatal error: b.x1.bout: unknown controlling source v.x1.v_sens
Fatal error: b.x1.bout: unknown controlling source v.x1.v_sens
```
`I(sens)` does not match device `Vsens`; ngspice wants `I(Vsens)` (full V-prefixed name).

## Diagnosis
FAIL at sim, not at layer: thin accepted a bad sense reference that only ngspice catches, with a cryptic message. Layer friction F1 (I-name not validated; correct spelling ['i','Vsens'] is undiscoverable from docstring). Fix: use full device name 'Vsens' in ['i',...].
