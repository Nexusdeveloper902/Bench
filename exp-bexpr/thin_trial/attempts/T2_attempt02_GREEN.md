# T2 attempt02 — LED — GREEN (iteration 2/2)
## AST (unchanged from attempt01)
```python
params={'IS': 1.1205592937857232e-10, 'NVT': 0.1}
expr=['mul', ['param', 'IS'], ['sub', ['call', 'exp', [['div', ['vdiff', 'mid', 'k'], ['param', 'NVT']]]], ['num', 1]]]
```
Same lowered subckt:
```
.subckt led1 a k IS=1.1205592937857232e-10 NVT=0.1
Rs1 a mid 5
* [thin:shockley]
Bd1 mid k I=({IS}*(exp((V(mid,k)/{NVT}))-1.0))
Cc1 mid k 10p
.ends
```

## Testbench (changed only)
```
Vin top 0 DC 5; Rext top anode 150; X1 anode 0 led1; .op
Load-line drive: (5-Vf)/150 = 20mA at Vf=2.0.
```

## Sim result
- v(anode)=2.000000 (target ~2.0) errs=[]
- I=(5-v)/150=0.020000 A (target 0.020)
- x1.mid=1.9V (diode intrinsic), 0.1V across internal 5ohm — as designed. Converged, no errors.

## Diagnosis
PASS. Exponential + internal Rs + 10p shunt cap converges under Thevenin drive. Lesson: ideal current-source drive is a testbench trap for exponential Bi (silent high-voltage branch); Thevenin drive selects the physical branch.
Iterations-to-green: 2 (1 fail + 1 pass). AST revisions: 0 (testbench fix only).
