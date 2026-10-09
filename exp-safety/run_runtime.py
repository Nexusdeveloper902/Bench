"""Runtime signal qualification matrix: which signals fire on which cases.

For each case: validator findings + batch sim + runtime.check_runtime verdict.
Signals under test: RC, FATAL-LOG, NONFINITE, RAIL, REJECT-RATE, ITER/PT,
NPTS, EVENT-COVERAGE. Also records validator-vs-ngspice agreement class:
  validator-earlier (validator errors AND ngspice loud),
  validator-only   (validator errors, ngspice silent -> load-bearing),
  ngspice-only     (validator passes, ngspice loud -> runtime load-bearing),
  both-accept      (both pass; correct iff circuit truly valid),
  silent-accept    (both pass but circuit invalid -> boundary hole).
Writes logs/runtime_matrix.json.
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(__file__))
import validator as V
import runtime as RT
import bsource as BS
from helper_safety import run_cir, tran_run, op_float

CASES = [
    # (id, net_or_fn, kind, vecs, truly_valid)
    ('R-valid-rc-tran', ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'C1 out 0 1u IC=0',
                         '.tran 10u 5m UIC'], 'tran', ['v(out)'], True),
    ('R-valid-divider', ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'R2 out 0 1k',
                         '.op'], 'op', ['v(out)'], True),
    ('R-valid-10kV', ['t', 'V1 in 0 DC 10k', 'R1 in out 1k', 'R2 out 0 1k',
                      '.op'], 'op', ['v(out)'], True),
    ('R-valid-1T', ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'R2 out 0 1T',
                    '.op'], 'op', ['v(out)'], True),
    ('R-valid-eamp', ['t', 'V1 inp 0 DC 2.5', 'E1 out 0 inp fb 100000',
                     'R1 out fb 1k', 'R2 fb 0 1k', '.op'], 'op', ['v(out)'],
     True),
    ('R-valid-kickback', ['t', 'I1 0 n1 DC 10m', 'L1 n1 0 10mH IC=0',
                          'C1 n1 0 1n IC=0',
                          'B1 n1 0 I=V(n1)/(1+(1MEG-1)*u(TIME-5m))',
                          '.tran 1u 10m UIC'], 'tran', ['v(n1)'], True),
    ('R-valid-ring', ['t', 'B1 o1 0 V=5*(1-u(V(o3)-2.5))', 'R1 o1 n1 1k',
                      'C1 n1 0 1n IC=0',
                      'B2 o2 0 V=5*(1-u(V(n1)-2.5))', 'R2 o2 n2 1k',
                      'C2 n2 0 1n IC=2',
                      'B3 o3 0 V=5*(1-u(V(n2)-2.5))', 'R3 o3 0 1k',
                      '.tran 10n 10u UIC'], 'tran', ['v(o1)'], True),
    ('R-loud-unseeded-latch', ['t', 'B1 o1 0 V=(V(o2)>2.5)?0:5',
                               'R1 o1 0 1k', 'B2 o2 0 V=(V(o1)>2.5)?0:5',
                               'R2 o2 0 1k', '.op'], 'op', ['v(o1)'], False),
    ('R-loud-selfref', ['t', 'V1 in 0 DC 5', 'R1 in out 1k',
                        'B1 out 0 V=V(out)+1', 'R2 out 0 1k', '.op'], 'op',
     ['v(out)'], False),
    ('R-loud-posfb', ['t', 'V1 inp 0 DC 2.51', 'V2 inm 0 DC 2.5',
                      'B1 out inm V=1e4*(V(inp)-V(inm))+V(out)',
                      'R1 out 0 1k', '.op'], 'op', ['v(out)'], False),
    ('R-loud-float', ['t', 'V1 in 0 DC 5', 'R1 in mid 1k', 'Rfloat n1 n2 1k',
                      '.op'], 'op', ['v(mid)'], False),
    ('R-loud-vloop', ['t', 'V1 out 0 DC 5', 'V2 out 0 DC 4', '.op'], 'op',
     ['v(out)'], False),
    ('R-loud-caploop', ['t', 'V1 in 0 DC 5', 'C1 in mid 1u', 'C2 mid out 1u',
                        'R1 out 0 1k', '.op'], 'op', ['v(mid)'], False),
    ('R-silent-isrc-open', ['t', 'I1 0 out DC 1m', '.op'], 'op', ['v(out)'],
     False),
    ('R-silent-bi-open', ['t', 'B1 out 0 I=1m', '.op'], 'op', ['v(out)'],
     False),
    ('R-silent-blip-miss', ['t', 'B1 in 0 V=5*(u(TIME-500u)-u(TIME-500.01u))',
                            'R1 in 0 1k', 'R2 in out 1k', 'C1 out 0 1n IC=0',
                            '.tran 1u 1m UIC'], 'tran', ['v(in)'], False),
    ('R-quest-ddt', ['t', 'B1 out 0 V=ddt(u(TIME-1m)*5)', 'R1 out 0 1k',
                     '.tran 10u 2m UIC'], 'tran', ['v(out)'], True),
    ('R-cost-dense', ['t', 'V1 in 0 DC 5', 'R1 in f 1k', 'C1 f 0 1n IC=0',
                      'R2 in s 1k', 'C2 s 0 10u IC=0', '.tran 100n 50m UIC'],
     'tran', ['v(f)'], True),
]


def run_case(cid, net, kind, vecs):
    f = V.validate(net)
    verrs = V.codes(f, 'error')
    req, _ = BS.required_tmax_for_netlist(net)
    req = None if req == 'unknown' else req
    if kind == 'op':
        ctl = ([l for l in net if l.strip().lower() != '.end']
               + ['.control', 'run', f"print {' '.join(vecs)}",
                  'rusage all', '.endc', '.end'])
        r = run_cir(ctl, timeout=30, tag=cid)
        series = None
        val = op_float(r['out'], vecs[0])
    else:
        r, series = tran_run(net, vecs, timeout=120, tag=cid)
        val = None
    chk = RT.check_runtime(r, series, net, tmax_required=req)
    return {'id': cid, 'validator_errors': verrs,
            'validator_reject': bool(verrs), 'ngspice_loud': chk['loud'],
            'rc': r['rc'], 'npts': r.get('npts'),
            'rejected': r.get('rejected'), 'accepted': r.get('accepted'),
            'iter_per_pt': r.get('iter_per_pt'),
            'dt_max': r.get('dt_max'), 'tmax_required': req,
            'verdict': chk['verdict'], 'reasons': chk['reasons'],
            'warns_n': len(r.get('warns', [])),
            'fatals': r.get('fatals', [])[:2],
            'op_val': val, 'blind_spot': chk['blind_spot']}


def main():
    rec = []
    for cid, net, kind, vecs, tv in CASES:
        d = run_case(cid, net, kind, vecs)
        d['truly_valid'] = tv
        vr, loud = d['validator_reject'], d['ngspice_loud']
        if vr and loud:
            d['agree'] = 'validator-earlier'
        elif vr and not loud:
            d['agree'] = 'validator-only(load-bearing)'
        elif not vr and loud:
            d['agree'] = 'ngspice-only(runtime-backstop)'
        elif not vr and not loud and not tv and d['verdict'] in ('FAIL', 'QUESTIONABLE'):
            d['agree'] = 'runtime-only(post-sim-flag)'
        elif tv:
            d['agree'] = 'both-accept(correct)'
        else:
            d['agree'] = 'silent-accept(HOLE)'
        rec.append(d)
        print(f"{cid:24s} valrej={vr} loud={loud} verdict={d['verdict']:12s} "
              f"reasons={d['reasons']} | {d['agree']}")
    os.makedirs('logs', exist_ok=True)
    with open('logs/runtime_matrix.json', 'w') as fh:
        json.dump(rec, fh, indent=1, default=str)
    print('-> logs/runtime_matrix.json')


if __name__ == '__main__':
    main()
