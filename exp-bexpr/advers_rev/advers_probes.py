"""Adversarial re-probes for exp-bexpr claims 1-10 + fresh breakage hunt.
Read-only wrt helper.py/thin.py/probes; writes only advers_rev/ artifacts.
Run: python3 advers_rev/advers_probes.py  (cwd = exp-bexpr/)
"""
import sys, json, re, time
sys.path.insert(0, '.')
sys.path.insert(0, 'advers_rev')
from helper import run_cir, has_error

R = []

def t(label, lines, vec='v(out)', tran=False, timeout=20):
    net = ['adv-' + re.sub(r'[^A-Za-z0-9_-]+', '_', label)[:48]] + lines
    if tran and not any(l.startswith('.tran') for l in net):
        net.append('.tran 1u 1m')
    if not any(l.startswith('.control') for l in net):
        net += ['.control', 'run', 'print ' + vec, '.endc', '.end']
    r = run_cir(net, timeout=timeout)
    out = r['out']
    errs = has_error(out)
    ran = 'no. of data rows' in out.lower()
    m = re.search(re.escape(vec) + r'\s*=\s*([-\deE+.NaNnUuinfINFloc]+)', out)
    val = m.group(1) if m else None
    # last tran value
    mall = re.findall(re.escape(vec) + r'\s+([-\deE+.]+)', out)
    entry = {'label': label, 'lines': lines, 'rc': r['rc'], 'ran': ran,
             'errs': errs, 'val': val,
             'last_tran': mall[-1] if mall else None,
             'npts': len(mall), 'wall_ms': r['wall_ms'],
             'log': out[:1800]}
    R.append(entry)
    print(f"{label:26s} rc={r['rc']:4d} ran={ran!s:5s} val={val} last={mall[-1] if mall else None} errs={errs}")
    return entry

# ---------- Claim 1: limit() ----------
t('c1-limit-mid',  ['V1 in 0 DC 0.5', 'R1 in 0 1k', 'B1 out 0 V=limit(V(in),0,1)', '.op'])
t('c1-limit-high', ['V1 in 0 DC 5', 'R1 in 0 1k', 'B1 out 0 V=limit(V(in),0,1)', '.op'])
t('c1-limit-low',  ['V1 in 0 DC -5', 'R1 in 0 1k', 'B1 out 0 V=limit(V(in),0,1)', '.op'])
t('c1-limit-const', ['B1 out 0 V=limit(5,0,1)', 'R1 out 0 1k', '.op'])
t('c1-limit-const-mid', ['B1 out 0 V=limit(0.5,0,1)', 'R1 out 0 1k', '.op'])
t('c1-limit-swapped', ['V1 in 0 DC 5', 'R1 in 0 1k', 'B1 out 0 V=limit(0,V(in),1)', '.op'])
t('c1-minmax-ctrl', ['V1 in 0 DC 5', 'R1 in 0 1k', 'B1 out 0 V=min(max(V(in),0),1)', '.op'])

# ---------- Claim 2: & | vs && || ! ----------
t('c2-single-and', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=(V(in)>1)&(V(in)<3)?5:0', '.op'])
t('c2-single-or',  ['V1 in 0 DC 0', 'R1 in 0 1k', 'B1 out 0 V=(V(in)>3)|(V(in)<1)?5:0', '.op'])
t('c2-dbl-and', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=(V(in)>1)&&(V(in)<3)?5:0', '.op'])
t('c2-dbl-or',  ['V1 in 0 DC 0', 'R1 in 0 1k', 'B1 out 0 V=(V(in)>3)||(V(in)<1)?5:0', '.op'])
t('c2-not',     ['V1 in 0 DC 0', 'R1 in 0 1k', 'B1 out 0 V=!(V(in)>1)?5:0', '.op'])
t('c2-bool-mult', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=(V(in)>1)*(V(in)<3)*5', '.op'])

# ---------- Claim 3: idt/sdt/ddt ----------
t('c3-idt', ['V1 in 0 DC 1', 'R1 in 0 1k', 'B1 out 0 V=idt(V(in))', 'R2 out 0 1k', '.tran 1u 1m'], tran=False)
t('c3-sdt', ['V1 in 0 DC 1', 'R1 in 0 1k', 'B1 out 0 V=sdt(V(in))', 'R2 out 0 1k', '.tran 1u 1m'], tran=False)
t('c3-ddt', ['V1 in 0 DC 0', 'R1 in 0 1k', 'B1 out 0 V=ddt(0.001*TIME)', 'R2 out 0 1k', '.tran 10u 2m UIC'], tran=False)

# ---------- Claim 4: gauss segfault ----------
t('c4-gauss-1arg', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=gauss(1)', '.op'])
t('c4-gauss-2arg', ['V1 in 0 DC 1', 'R1 in 0 1k', 'B1 out 0 V=gauss(0,1)', 'R2 out 0 1k', '.tran 1u 1m'], timeout=20)
t('c4-white-ctrl', ['V1 in 0 DC 1', 'R1 in 0 1k', 'B1 out 0 V=white(1)', 'R2 out 0 1k', '.tran 1u 1m'])

# ---------- Claim 5: TEMP vs temper ----------
t('c5-temp-bare', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=TEMP', '.op'])
t('c5-temper',    ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=temper', '.op'])
t('c5-temp-brace', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V={TEMP}', '.op'])
t('c5-tnom',      ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=tnom', '.op'])

# ---------- Claim 6: V(typo) ----------
t('c6-typo', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=V(inn)*2', '.op'])
t('c6-missing', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=V(nonexistent)*2', '.op'])

# ---------- Claim 7: 1/0, exp(100) ----------
t('c7-div0', ['V1 in 0 DC 0', 'R1 in 0 1k', 'B1 out 0 V=1/V(in)', 'R2 out 0 1k', '.op'])
t('c7-div0-lit', ['B1 out 0 V=1/0', 'R1 out 0 1k', '.op'])
t('c7-exp100', ['V1 in 0 DC 100', 'R1 in 0 1k', 'B1 out 0 V=exp(V(in))', '.op'])

# ---------- Claim 8: latch vs schmitt ----------
t('c8-latch-op', ['B1 q 0 V=(V(qb)>2.5)?0:5', 'B2 qb 0 V=(V(q)>2.5)?0:5',
                  'R1 q 0 1k', 'R2 qb 0 1k', '.op'])
t('c8-latch-cap', ['B1 q 0 V=(V(qb)>2.5)?0:5', 'B2 qb 0 V=(V(q)>2.5)?0:5',
                   'R1 q 0 1k', 'R2 qb 0 1k', 'C1 q 0 1p', 'C2 qb 0 1p',
                   '.tran 10u 2m UIC'])
t('c8-schmitt-res', ['V1 in 0 PWL(0 0 1m 0 1.01m 5 2m 5 2.01m 0 3m 0)',
                     'R1 in mid 10k', 'R2 out mid 10k',
                     'B1 out 0 V=(V(mid)>2.5)?5:0', 'R3 out 0 1k',
                     '.tran 10u 3m'])
t('c8-schmitt-selfread', ['V1 in 0 PWL(0 0 1m 0 1.01m 5 2m 5 2.01m 0 3m 0)',
                          'R1 in mid 1k',
                          'B2 out 0 V=(V(mid)>2.75)?5:((V(mid)<2.25)?0:V(out))',
                          'R2 out 0 1k', '.tran 10u 3m'])

# ---------- Claim 9/10 handled in VERDICT build (thin import + timing) ----------

# ---------- NEW breakage hunt ----------
t('n1-pwr-neg-even', ['V1 in 0 DC -2', 'R1 in 0 1k', 'B1 out 0 V=pwr(V(in),2)', '.op'])
t('n1-pwr-neg-odd',  ['V1 in 0 DC -2', 'R1 in 0 1k', 'B1 out 0 V=pwr(V(in),3)', '.op'])
t('n1-pow-neg-frac', ['V1 in 0 DC -8', 'R1 in 0 1k', 'B1 out 0 V=pow(V(in),0.333333)', '.op'])
t('n1-pwr-neg-frac', ['V1 in 0 DC -8', 'R1 in 0 1k', 'B1 out 0 V=pwr(V(in),0.333333)', '.op'])
t('n2-u-zero',  ['V1 in 0 DC 0', 'R1 in 0 1k', 'B1 out 0 V=u(V(in))', '.op'])
t('n2-u-neg',   ['V1 in 0 DC -1', 'R1 in 0 1k', 'B1 out 0 V=u(V(in))', '.op'])
t('n2-uramp-zero', ['V1 in 0 DC 0', 'R1 in 0 1k', 'B1 out 0 V=uramp(V(in))', '.op'])
t('n3-ln-zero',  ['V1 in 0 DC 0', 'R1 in 0 1k', 'B1 out 0 V=ln(V(in))', '.op'])
t('n3-sqrt-neg', ['V1 in 0 DC -4', 'R1 in 0 1k', 'B1 out 0 V=sqrt(V(in))', '.op'])
t('n3-exp-neg1000', ['B1 out 0 V=exp(-1000)', 'R1 out 0 1k', '.op'])
t('n5-selfsense', ['B1 out 0 I=I(B1)', 'R1 out 0 1k', '.op'])
t('n5-sense-bbranch', ['V1 in 0 DC 5', 'R1 in out 1k', 'B1 bout 0 V=I(B1)*1k', 'R2 bout 0 1k', '.op'])
t('n6-pwl-unsorted', ['V1 in 0 DC 1.5', 'R1 in 0 1k', 'B1 out 0 V=pwl(V(in),2,99,0,0,1,1)', 'R2 out 0 1k', '.op'])
t('n7-scale-M', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=V(in)*1M', '.op'])
t('n7-scale-meg', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=V(in)*1meg', 'R2 out 0 1G', '.op'])
t('n8-ddt-dc', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=ddt(V(in))', '.op'])
t('n9-time-dc', ['B1 out 0 V=TIME*1000', 'R1 out 0 1k', '.op'])
t('n10-hugegain', ['V1 in 0 DC 0.5', 'R1 in 0 1k', 'B1 out 0 V=1e6*(V(in)-V(out))', 'R2 out 0 1k', '.op'])
t('n11-paramexpr', ['.param GAIN=5*2', 'V1 in 0 DC 0.5', 'R1 in 0 1k', 'B1 out 0 V={GAIN}*V(in)', 'R2 out 0 1k', '.op'])
t('n12-case-node', ['V1 in 0 DC 2', 'R1 in 0 1k', 'B1 out 0 V=V(IN)*2', '.op'])
t('n13-ib-source', ['B1 out 0 I=V(1)/1k', 'R1 out 0 1k', '.op'])

with open('advers_rev/advers_raw.json', 'w') as f:
    json.dump(R, f, indent=1)
print(f"\nsaved {len(R)} probes -> advers_rev/advers_raw.json")
