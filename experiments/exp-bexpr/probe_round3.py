"""Round 3: logic-or, sdt, limit, scale factors, subckt params, sampling of error quality."""
import sys, json, re
sys.path.insert(0, '.')
from helper import run_cir, has_error

def t(label, lines):
    net = ['probe3-' + label] + lines
    if not any(l.startswith('.control') for l in net):
        net += ['.control', 'run', 'print v(out)', '.endc', '.end']
    r = run_cir(net)
    errs = has_error(r['out'])
    ran = 'no. of data rows' in r['out'].lower()
    m = re.search(r'v\(out\)\s*=\s*([-\deE+.]+)', r['out'])
    val = m.group(1) if m else None
    ok = ran and not errs
    print(f"{label:24s} ok={ok} val={val} errs={errs} rc={r['rc']}")
    if not ok:
        for line in r['out'].splitlines():
            if re.search(r'error|unknown|undefined|invalid|no such|bogus|failed|cannot', line, re.I):
                print('    |', line.strip()[:160])
    return {'label': label, 'lines': lines, 'ok': ok, 'val': val, 'errs': errs, 'rc': r['rc'], 'log': r['out'][:1200]}

R = []
R.append(t('or-double-pipe', ['V1 in 0 DC 0', 'R1 in 0 1k', 'B1 out 0 V=(V(in)>3)||(V(in)<1)?5:0', '.op']))
R.append(t('not-combo', ['V1 in 0 DC 0', 'R1 in 0 1k', 'B1 out 0 V=!(V(in)>1)?5:0', '.op']))
R.append(t('sdt-func', ['V1 in 0 DC 1', 'R1 in 0 1k', 'B1 out 0 V=sdt(V(in))', 'R2 out 0 1k', '.tran 1u 1m']))
R.append(t('limit-order', ['V1 in 0 DC 5', 'R1 in 0 1k', 'B1 out 0 V=limit(V(in),0,1)', '.op']))
R.append(t('scale-meg', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=V(in)*1meg', 'R2 out 0 1G', '.op']))
R.append(t('scale-u', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=V(in)*1u*1meg', '.op']))
R.append(t('subckt-params', ['V1 in 0 DC 0.5', 'X1 in out amp GAIN=10 OFF=0.7', '.subckt amp a b GAIN=1 OFF=0', 'B1 b 0 V={GAIN}*V(a)+{OFF}', '.ends amp', '.op']))
R.append(t('b-missing-node', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=V(nonexistent)*2', '.op']))
R.append(t('b-wrong-node-typo', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=V(inn)*2', '.op']))
R.append(t('b-unbalanced', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=(V(in)*2', '.op']))
R.append(t('b-empty-expr', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=', '.op']))
R.append(t('div-zero', ['V1 in 0 DC 0', 'R1 in 0 1k', 'B1 out 0 V=1/V(in)', 'R2 out 0 1k', '.op']))
R.append(t('sqrt-neg', ['V1 in 0 DC -4', 'R1 in 0 1k', 'B1 out 0 V=sqrt(V(in))', '.op']))
R.append(t('ln-zero', ['V1 in 0 DC 0', 'R1 in 0 1k', 'B1 out 0 V=ln(V(in))', '.op']))
R.append(t('exp-huge', ['V1 in 0 DC 100', 'R1 in 0 1k', 'B1 out 0 V=exp(V(in))', '.op']))
R.append(t('alg-loop', ['B1 out 0 V=V(out)*2', 'R1 out 0 1k', '.op']))
R.append(t('u-at-zero', ['V1 in 0 DC 0', 'R1 in 0 1k', 'B1 out 0 V=u(V(in))', '.op']))
R.append(t('v-sense-cur-direct', ['V1 in 0 DC 5', 'R1 in out 1k', 'B1 bout 0 I=I(V1)/10', 'R2 bout 0 1k', '.op']))

with open('logs/native_round3.json', 'w') as f:
    json.dump(R, f, indent=1)
print(f"\n{sum(1 for r in R if r['ok'])}/{len(R)} ok")
