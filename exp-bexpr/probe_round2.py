"""Round 2: variant spellings + fixed current-source circuits."""
import sys, json
sys.path.insert(0, '.')
from helper import run_cir, has_error

def t(label, lines, tran=False):
    net = ['probe2-' + label] + lines
    if tran and not any(l.startswith('.tran') for l in net):
        net.append('.tran 1u 1m')
    if not any(l.startswith('.control') for l in net):
        net += ['.control', 'run', 'print v(out)', '.endc', '.end']
    else:
        net.append('.end')
    # ensure .end present once
    if net.count('.end') > 1:
        pass
    r = run_cir(net)
    errs = has_error(r['out'])
    ran = 'no. of data rows' in r['out'].lower()
    # extract printed value
    import re
    m = re.search(r'v\(out\)\s*=\s*([-\deE+.]+)', r['out'])
    val = m.group(1) if m else None
    ok = ran and not errs
    print(f"{label:22s} ok={ok} val={val} errs={errs} rc={r['rc']}")
    if not ok:
        # show error lines
        for line in r['out'].splitlines():
            if re.search(r'error|unknown|undefined|invalid|no such|bogus|failed', line, re.I):
                print('    |', line.strip()[:160])
    return {'label': label, 'lines': lines, 'ok': ok, 'val': val, 'errs': errs,
            'log': r['out'][:1200]}

R = []
R.append(t('and-double-amp', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=(V(in)>1)&&(V(in)<3)?5:0', '.op']))
R.append(t('if-lower', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=if(V(in)>1,10,0)', '.op']))
R.append(t('if-bool-mult', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=(V(in)>1)*(V(in)<3)*5', '.op']))
R.append(t('arctan-func', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=arctan(1)', '.op']))
R.append(t('floor-ok', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=floor(V(in))', '.op']))
R.append(t('idt-ic', ['V1 in 0 DC 1', 'R1 in 0 1k', 'B1 out 0 V=idt(V(in),0)', '.tran 1u 1m']))
R.append(t('idt-alone-tran', ['V1 in 0 DC 1', 'R1 in 0 1k', 'B1 out 0 V=idt(V(in))', '.tran 1u 1m']))
R.append(t('temper', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=temper', '.op']))
R.append(t('temp-braces', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V={TEMP}', '.op']))
R.append(t('tnom', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=tnom', '.op']))
R.append(t('i-form-loaded', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 I=V(in)/1k', 'R2 out 0 1k', '.op']))
R.append(t('g-cur-form', ['V1 in 0 DC 2', 'R1 in 0 1k', "G1 out 0 cur='V(in)/1k'", 'R2 out 0 1k', '.op']))
R.append(t('g-poly', ['V1 in 0 DC 2', 'R1 in 0 1k', 'G1 out 0 POLY(1) in 0 0 1m', 'R2 out 0 1k', '.op']))
R.append(t('b-time-tran', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=TIME*1000', 'R2 out 0 1k', '.tran 1u 1m']))
R.append(t('pwl-time', ['V1 in 0 DC 0', 'R1 in 0 1k', 'B1 out 0 V=pwl(TIME,0,0,1m,5)', 'R2 out 0 1k', '.tran 1u 2m']))
R.append(t('white-2arg', ['V1 in 0 DC 1', 'R1 in 0 1k', 'B1 out 0 V=white(1,0.1)', 'R2 out 0 1k', '.tran 1u 1m']))
R.append(t('gauss-2arg', ['V1 in 0 DC 1', 'R1 in 0 1k', 'B1 out 0 V=gauss(0,1)', 'R2 out 0 1k', '.tran 1u 1m']))
R.append(t('stp-form', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=stp(V(in)-1)', '.op']))
R.append(t('step-form', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=step(V(in)-1)', '.op']))
R.append(t('pow-neg-base', ['V1 in 0 DC -2', 'R1 in 0 1k', 'B1 out 0 V=pow(V(in),2)', '.op']))
R.append(t('pwr-neg-base', ['V1 in 0 DC -2', 'R1 in 0 1k', 'B1 out 0 V=pwr(V(in),2)', '.op']))
R.append(t('caret-neg', ['V1 in 0 DC -2', 'R1 in 0 1k', 'B1 out 0 V=V(in)^2', '.op']))
R.append(t('current-sense', ['V1 in 0 DC 5', 'R1 in mid 1k', 'Vsense mid out DC 0', 'B1 bout 0 V=I(Vsense)*1k', 'R2 bout 0 1k', '.op']))
R.append(t('v-branch-sense', ['V1 in 0 DC 5', 'R1 in out 1k', 'B1 bout 0 V=I(V1)*1k', 'R2 bout 0 1k', '.op']))
R.append(t('param-subckt', ['.param GAIN=10', 'V1 in 0 DC 0.5', 'X1 in out amp', '.subckt amp a b', '.param G=5', 'B1 b 0 V={G}*V(a)', '.ends amp', '.op']))

with open('logs/native_round2.json', 'w') as f:
    json.dump(R, f, indent=1)
print(f"\n{sum(1 for r in R if r['ok'])}/{len(R)} ok")
