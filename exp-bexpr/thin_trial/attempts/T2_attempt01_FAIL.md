# T2 attempt01 — LED Vf~2V@20mA + 5ohm — FAIL (bogus OP, silent)
## Spec
LED: Vf ~2V at 20mA, series 5ohm, Is/params choice. Test at 20mA forward.

## AST
```python
pins=['a','k']; internal=['mid']
params={'IS': 1.1205592937857232e-10, 'NVT': 0.1}  # IS computed as 0.02/(exp(1.9/0.1)-1) = 1.121e-10 so Vdiode=1.9V@20mA +0.1V across 5ohm = 2.0V total; NVT=0.1
expr=['mul', ['param', 'IS'], ['sub', ['call', 'exp', [['div', ['vdiff', 'mid', 'k'], ['param', 'NVT']]]], ['num', 1]]]
devices=[
  {'kind':'R','name':'s1','p':'a','n':'mid','value':'5','internal_nodes':['mid']},
  {'kind':'Bi','name':'d1','p':'mid','n':'k','tag':'shockley','expr':expr},
  {'kind':'C','name':'c1','p':'mid','n':'k','value':'10p'},
]
```
Target: Vdiode 1.9V at 20mA (remainder 0.1V = 20mA*5ohm), total 2.0V.

## Lowered text
```
.subckt led1 a k IS=1.1205592937857232e-10 NVT=0.1
Rs1 a mid 5
* [thin:shockley]
Bd1 mid k I=({IS}*(exp((V(mid,k)/{NVT}))-1.0))
Cc1 mid k 10p
.ends
```
single expr: `({IS}*(exp((V(mid,k)/{NVT}))-1.0))`

## Testbench
```
I1 0 top DC 20m; X1 top 0 led1; .op  (ideal current-source drive)
```

## Sim result
- v(top)=17848230.0 (expect ~2.0) errs=[]
- Bd1 reports i=1.12e89, v=1.78e7; Rs current 0.02 — KCL violated by 91 orders, yet ngspice printed NO error/singular/convergence strings (has_error=[]).
- Operating point is garbage (high-voltage branch of exponential).

## Diagnosis
FAIL — but not a thin-AST failure: ideal current-source drive of a bare-exponential Bi has two solutions; Newton found the high-voltage one from zero init. Fix: use Thevenin drive (voltage + resistor load line) so the low-voltage solution is selected. Also noted: neither thin nor ngspice flagged the bogus OP — silent wrong answer. Retry with Thevenin testbench, same AST.
