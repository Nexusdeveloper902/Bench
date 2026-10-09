"""Native B-source capability matrix probe (ngspice-47)."""
import sys, json, time, re
sys.path.insert(0, '.')
from helper import run_cir, has_error

def test_expr(label, bline, extra=None, analysis=None, expect_pass=True):
    """Build small circuit with B device, run, classify."""
    net = ['probe-' + label.replace(' ', '_')[:40],
           'V1 in 0 DC 2',
           'R1 in 0 1k',
           bline,
           ]
    if extra:
        net.extend(extra)
    if analysis:
        net.extend(analysis)
    else:
        net.extend(['.op', '.control', 'run', 'print v(out)', '.endc', '.end'])
    r = run_cir(net)
    out = r['out']
    errs = has_error(out)
    has_err = len(errs) > 0
    # check sim actually produced output rows
    ran = ('no. of data rows' in out.lower())
    ok = ran and not has_err
    return {'label': label, 'bline': bline, 'rc': r['rc'], 'ran': ran, 'has_err': has_err,
            'ok': ok, 'wall_ms': r['wall_ms'],
            'log_excerpt': out[:1500]}

probes = [
    # operators
    ("op-plus",  "B1 out 0 V=V(in)+1"),
    ("op-minus", "B1 out 0 V=V(in)-1"),
    ("op-mul",   "B1 out 0 V=V(in)*2"),
    ("op-div",   "B1 out 0 V=V(in)/2"),
    ("op-pow-caret", "B1 out 0 V=V(in)^2"),
    ("op-pow-starstar", "B1 out 0 V=V(in)**2"),
    ("op-unary", "B1 out 0 V=-V(in)"),
    ("op-mod",   "B1 out 0 V=V(in)%2"),
    ("cmp-gt",   "B1 out 0 V=V(in)>1?5:0"),
    ("cmp-lt",   "B1 out 0 V=V(in)<1?5:0"),
    ("logic-and", "B1 out 0 V=(V(in)>1)&(V(in)<3)?5:0"),
    ("logic-or",  "B1 out 0 V=(V(in)>3)|(V(in)<1)?5:0"),
    ("logic-not", "B1 out 0 V=!(V(in)>1)?5:0"),
    ("ternary",  "B1 out 0 V=V(in)>1?10:0"),
    ("nested-ternary", "B1 out 0 V=V(in)>2?10:(V(in)>1?5:0)"),
    ("if-func",  "B1 out 0 V=IF(V(in)>1,10,0)"),
    # functions
    ("fn-exp", "B1 out 0 V=exp(V(in))"),
    ("fn-ln", "B1 out 0 V=ln(V(in))"),
    ("fn-log", "B1 out 0 V=log(V(in))"),
    ("fn-log10", "B1 out 0 V=log10(V(in))"),
    ("fn-sqrt", "B1 out 0 V=sqrt(abs(V(in)))"),
    ("fn-pow", "B1 out 0 V=pow(V(in),2)"),
    ("fn-pwr", "B1 out 0 V=pwr(V(in),2)"),
    ("fn-sin", "B1 out 0 V=sin(V(in))"),
    ("fn-cos", "B1 out 0 V=cos(V(in))"),
    ("fn-tan", "B1 out 0 V=tan(0.1)"),
    ("fn-asin", "B1 out 0 V=asin(0.5)"),
    ("fn-acos", "B1 out 0 V=acos(0.5)"),
    ("fn-atan", "B1 out 0 V=atan(1)"),
    ("fn-atan2", "B1 out 0 V=atan2(1,1)"),
    ("fn-sinh", "B1 out 0 V=sinh(0.5)"),
    ("fn-cosh", "B1 out 0 V=cosh(0.5)"),
    ("fn-tanh", "B1 out 0 V=tanh(V(in))"),
    ("fn-abs", "B1 out 0 V=abs(V(in))"),
    ("fn-min", "B1 out 0 V=min(V(in),1)"),
    ("fn-max", "B1 out 0 V=max(V(in),1)"),
    ("fn-floor", "B1 out 0 V=floor(V(in))"),
    ("fn-ceil", "B1 out 0 V=ceil(V(in))"),
    ("fn-int", "B1 out 0 V=int(V(in))"),
    ("fn-sgn", "B1 out 0 V=sgn(V(in))"),
    ("fn-limit", "B1 out 0 V=limit(V(in),0,1)"),
    ("fn-u", "B1 out 0 V=u(V(in))"),
    ("fn-uramp", "B1 out 0 V=uramp(V(in))"),
    ("fn-pwl", "B1 out 0 V=pwl(V(in),0,0,1,1,2,0)"),
    ("fn-table", "B1 out 0 V=table(V(in),0,0,1,1,2,0)"),
    ("fn-poly", "B1 out 0 V=poly(1,V(in),0,1)"),
    ("fn-stp", "B1 out 0 V=stp(V(in))"),
    ("fn-ddt", "B1 out 0 V=ddt(V(in))",),
    ("fn-idt", "B1 out 0 V=idt(V(in))"),
    ("fn-white", "B1 out 0 V=white(1)"),
    ("fn-gauss", "B1 out 0 V=gauss(1)"),
    ("fn-foo-unknown", "B1 out 0 V=foo(V(in))"),
    # refs
    ("ref-vdiff", "B1 out 0 V=V(in,0)"),
    ("ref-time", "B1 out 0 V=TIME*1000"),
    ("ref-temp", "B1 out 0 V=TEMP"),
    ("ref-param", None),  # special below
    ("i-form", "B1 out 0 I=V(in)/1k"),
    ("e-behav", "E1 out 0 value={V(in)*2}"),
    ("g-behav", "G1 out 0 value={V(in)/1k}"),
]

results = []
for label, bline in probes:
    if label == "ref-param":
        net = ['probe-param', '.param GAIN=5', 'V1 in 0 DC 2', 'R1 in 0 1k',
               'B1 out 0 V={GAIN}*V(in)', '.op', '.control', 'run', 'print v(out)', '.endc', '.end']
        r = run_cir(net)
        errs = has_error(r['out'])
        has_err = len(errs) > 0
        ran = 'no. of data rows' in r['out'].lower()
        results.append({'label': label, 'bline': '.param GAIN=5 + B1 V={GAIN}*V(in)', 'rc': r['rc'],
                        'ran': ran, 'has_err': has_err, 'ok': ran and not has_err,
                        'wall_ms': r['wall_ms'], 'log_excerpt': r['out'][:1500]})
        continue
    if label in ("fn-ddt", "fn-idt", "ref-time", "fn-white", "fn-gauss"):
        analysis = ['.tran 1u 1m', '.control', 'run', 'print v(out)', '.endc', '.end']
    else:
        analysis = None
    t = test_expr(label, bline, analysis=analysis)
    results.append(t)
    print(f"{label:16s} ok={t['ok']} ran={t['ran']} err={t['has_err']} rc={t['rc']}")

with open('logs/native_matrix.json', 'w') as f:
    json.dump({'ngspice': 'ngspice-47 Arch 47-1', 'results': results}, f, indent=1)
print(f"\nTotal: {sum(1 for r in results if r['ok'])}/{len(results)} ok")
