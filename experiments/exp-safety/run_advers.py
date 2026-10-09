"""Adversarial verification: hand-crafted defeat probes + randomized linear
fuzzer with an independent numpy nodal-analysis oracle.

Part A (defeat probes): circuits designed AFTER the validator was frozen to
try to break each rule (V-loop via L/0R/E/Bv, G/F/H opens, diode floats,
Bv-into-cap, parallel-V variants, zero-R, dup names, unknown kinds,
malformed lines, opaque X, B-cycle seeded/unseeded, well-bounded EVENT pass).

Part B (fuzzer): N random linear R/V/I(+C-as-open) circuits, 2-3 non-ground
nodes. Independent numpy MNA reference -> classifies:
  agree-ok     validator-pass + ngspice-quiet + matches numpy
  ngspice-loud (validator may or may not flag; loudness recorded)
  FP-suspect   validator-error + ngspice-quiet + matches numpy (manual review)
  SILENT-WRONG validator-pass + ngspice-quiet + numpy mismatch (hole; fail loud)
  CHECK        anything needing eyes (listed with values)
Writes logs/advers.json.
"""
import sys
import os
import json
import random
import re

sys.path.insert(0, os.path.dirname(__file__))
import validator as V
import runtime as RT
import bsource as BS
from helper_safety import run_cir, tran_run, op_float

REC = []


def emit(r):
    REC.append(r)
    print(f"{r['id']:26s} {r.get('note','')}")


def sim_op(net):
    ctl = ([l for l in net if l.strip().lower() != '.end']
           + ['.control', 'run', 'rusage all', '.endc', '.end'])
    return run_cir(ctl, timeout=20)


def partA():
    cases = [
        ('A01-vloop-via-L', ['t', 'V1 a 0 DC 5', 'L1 a 0 1mH', '.op']),
        ('A02-vloop-via-0R', ['t', 'V1 a 0 DC 5', 'R0 a 0 0', '.op']),
        ('A03-vloop-via-E', ['t', 'V1 a 0 DC 5', 'E1 a 0 0 0 1', '.op']),
        ('A04-vloop-via-Bv', ['t', 'V1 a 0 DC 5', 'B1 a 0 V=5', '.op']),
        ('A05-v-plus-R-valid', ['t', 'V1 a 0 DC 5', 'R1 a 0 1k', '.op']),
        ('A06-g-open', ['t', 'G1 out 0 0 0 1m', '.op']),
        ('A07-f-open', ['t', 'Vs a 0 DC 1', 'F1 out 0 Vs 2', '.op']),
        ('A08-h-unloaded', ['t', 'Vs a 0 DC 1m', 'R1 a 0 1k',
                            'H1 out 0 Vs 10', '.op']),
        ('A09-e-unloaded', ['t', 'V1 a 0 DC 1', 'E1 out 0 a 0 5', '.op']),
        ('A10-diode-float', ['t', 'D1 n1 n2 DMOD',
                             '.model DMOD D(IS=1e-14)', '.op']),
        ('A11-bv-cap-float', ['t', 'B1 mid 0 V=5', 'C1 mid flt 1u', '.op']),
        ('A12-parallel-sameV', ['t', 'V1 out 0 DC 5', 'V2 out 0 DC 5',
                               '.op']),
        ('A13-zeroR-series', ['t', 'V1 a 0 DC 5', 'R0 a b 0', 'R1 b 0 1k',
                             '.op']),
        ('A14-dup-names', ['t', 'V1 a 0 DC 5', 'R1 a 0 1k', 'R1 b 0 2k',
                           '.op']),
        ('A15-unknown-kind', ['t', 'V1 a 0 DC 5', 'Z1 a 0 1k', '.op']),
        ('A16-malformed-R', ['t', 'V1 a 0 DC 5', 'R1 a', '.op']),
        ('A17-unseeded-latch', ['t', 'B1 o1 0 V=(V(o2)>2.5)?0:5',
                               'R1 o1 0 1k', 'B2 o2 0 V=(V(o1)>2.5)?0:5',
                               'R2 o2 0 1k', '.op']),
        ('A18-seeded-latch', ['t', 'B1 o1 0 V=(V(o2)>2.5)?0:5', 'R1 o1 0 1k',
                              'C1 o1 0 1n', 'B2 o2 0 V=(V(o1)>2.5)?0:5',
                              'R2 o2 0 1k', 'C2 o2 0 1n',
                              '.ic v(o1)=5 v(o2)=0', '.tran 10u 1m UIC']),
        ('A19-opaque-X-valid', ['t', 'X1 int 0 div2', '.subckt div2 a 0',
                                'R1 a m 1k', 'R2 m 0 1k', '.ends',
                                'V1 int 0 DC 5', '.op']),
        ('A20-C-plus-Isrc', ['t', 'I1 0 n DC 1m', 'C1 n 0 1u', '.op']),
    ]
    for cid, net in cases:
        net = net + ['.end'] if net[-1].strip().lower() != '.end' else net
        f = V.validate(net)
        errs, warns = V.codes(f, 'error'), V.codes(f, 'warn')
        r = sim_op(net)
        loud = bool(r['warns'] or r['fatals'] or r['rc'] != 0)
        emit({'id': cid, 'errors': errs, 'warns': warns,
              'ngspice_loud': loud, 'rc': r['rc'],
              'fatals': r['fatals'][:2],
              'note': f"errors={errs} warns={warns} loud={loud} rc={r['rc']}"})
    # A21: well-bounded sine must NOT raise EVENT-COVERAGE (no false flag)
    import math
    w = 2 * math.pi * 1e5
    T = 1e-5
    net = ['t', f'B1 in 0 V=5*sin({w}*TIME)', 'R1 in 0 1k',
           f'.tran 100n 50u 0 {T / 50} UIC', '.end']
    req, _ = BS.required_tmax_for_netlist(net)
    r, s = tran_run(net, ['v(in)'], timeout=60, tag='A21')
    chk = RT.check_runtime(r, s, net, tmax_required=req)
    emit({'id': 'A21-bounded-sine-noflag', 'required': req,
          'verdict': chk['verdict'], 'reasons': chk['reasons'],
          'note': f"req={req} verdict={chk['verdict']} reasons={chk['reasons']}"})


# ---------- Part B: linear fuzzer with numpy oracle ----------

def numpy_op(nodes, devices):
    """Solve DC MNA with numpy. devices: list of (kind,n1,n2,val).
    R: conductance; V: voltage constraint (extra current unknown);
    I: current injection (from n1 to n2); C: ignored (open).
    Returns ({node: V}, singular_bool). Ground '0' excluded from unknowns."""
    import numpy as np
    nn = [n for n in nodes if n != '0']
    idx = {n: i for i, n in enumerate(nn)}
    n = len(nn)
    vsrcs = [(a, b, v) for k, a, b, v in devices if k == 'V']
    m = len(vsrcs)
    N = n + m
    if N == 0:
        return {}, False
    G = np.zeros((N, N))
    z = np.zeros(N)
    for k, a, b, v in devices:
        if k == 'R':
            g = 1.0 / v
            if a != '0':
                G[idx[a], idx[a]] += g
            if b != '0':
                G[idx[b], idx[b]] += g
            if a != '0' and b != '0':
                G[idx[a], idx[b]] -= g
                G[idx[b], idx[a]] -= g
        elif k == 'I':
            # current v flows from a to b: KCL: -v leaves a... define: current
            # source pushes v amps from node a into node b.
            if a != '0':
                z[idx[a]] -= v
            if b != '0':
                z[idx[b]] += v
    for j, (a, b, v) in enumerate(vsrcs):
        row = n + j
        if a != '0':
            G[row, idx[a]] = 1
            G[idx[a], row] = 1
        if b != '0':
            G[row, idx[b]] = -1
            G[idx[b], row] = -1
        z[row] = v
    try:
        x = np.linalg.solve(G, z)
    except np.linalg.LinAlgError:
        return {}, True
    return {nn[i]: float(x[i]) for i in range(n)}, False


def fuzz(seed0=7, count=300):
    import numpy as np  # noqa (oracle)
    rnd = random.Random(seed0)
    tall = {'agree-ok': 0, 'ngspice-loud': 0, 'FP-suspect': 0,
            'SILENT-WRONG': 0, 'CHECK': 0}
    for k in range(count):
        nn = rnd.randint(1, 3)
        names = [f'n{i + 1}' for i in range(nn)] + ['0']
        ndev = rnd.randint(1, 5)
        devs, net = [], ['t-fuzz']
        for i in range(ndev):
            kind = rnd.choice(['R', 'R', 'R', 'V', 'I', 'C'])
            a, b = rnd.sample(names, 2)
            if kind == 'R':
                val = rnd.choice([100, 1e3, 1e4, 1e6, 1e9, 1e12])
                devs.append(('R', a, b, val))
                net.append(f'R{i} {a} {b} {val}')
            elif kind == 'C':
                devs.append(('C', a, b, 1e-6))
                net.append(f'C{i} {a} {b} 1u')
            elif kind == 'V':
                val = rnd.choice([1, 5, -3])
                devs.append(('V', a, b, val))
                net.append(f'V{i} {a} {b} DC {val}')
            else:
                val = rnd.choice([1e-6, 1e-3, 1])
                devs.append(('I', a, b, val))
                net.append(f'I{i} {a} {b} DC {val}')
        net += ['.op', '.control', 'run',
                'print ' + ' '.join(f'v({x})' for x in names if x != '0'),
                '.endc', '.end']
        f = V.validate(net)
        errs = V.codes(f, 'error')
        r = sim_op(net)
        loud = bool(r['warns'] or r['fatals'] or r['rc'] != 0)
        allnodes = set(names)
        ref, sing = numpy_op(allnodes, [(k2, a, b, v) for k2, a, b, v in devs
                                        if k2 in ('R', 'V', 'I')])
        got = {}
        for x in names:
            if x != '0':
                got[x] = op_float(r['out'], f'v({x})')
        grounded = any(a == '0' or b == '0' for _, a, b, _ in devs)
        if sing:
            status = ('ngspice-loud' if loud
                      else ('validator-error-ok' if errs else 'CHECK-singular-quiet'))
            if status.startswith('CHECK'):
                tall['CHECK'] += 1
                emit({'id': f'F{k:03d}', 'net': net, 'errors': errs,
                      'loud': loud, 'ref': 'SINGULAR', 'got': got,
                      'note': 'oracle-singular but ngspice quiet '
                              f"errors={errs} got={got}"})
            elif status == 'ngspice-loud':
                tall['ngspice-loud'] += 1
            else:
                tall['ngspice-loud'] += 1  # rejected pre-sim; fine
            continue
        if grounded:
            mismatch = any(g is None or abs(g - ref[x]) > max(abs(ref[x]) * 1e-6,
                                                              1e-9)
                           for x, g in got.items())
        else:
            # no ground reference: absolute values are undefined, only
            # pairwise differences are meaningful (F093 lesson). Print
            # precision (~6 digits) limits oracle resolution: 1e-3 relative.
            ns = [x for x in names if x != '0']
            mismatch = False
            for i in range(len(ns)):
                for j in range(i + 1, len(ns)):
                    a, b = ns[i], ns[j]
                    if got[a] is None or got[b] is None:
                        mismatch = True
                    else:
                        rd = ref[a] - ref[b]
                        if abs((got[a] - got[b]) - rd) > max(1e-6, 1e-3 * abs(rd)):
                            mismatch = True
        # A validator ERROR is a false positive only if the circuit is
        # actually well-posed (grounded DC paths: none of the malformed-
        # topology codes) yet flagged. NO-DC-PATH/NO-GROUND/ISRC-OPEN flags
        # on pathless circuits are correct rejects even when numbers
        # coincide (sourceless 0V) or share regularization pathology
        # (F195/F197 1e18 agreement): .op there is mathematically undefined.
        wellposed = not any(e in ('NO-DC-PATH', 'NO-GROUND', 'ISRC-OPEN')
                            for e in errs)
        if errs and not loud and not mismatch:
            if wellposed:
                tall['FP-suspect'] += 1
                emit({'id': f'F{k:03d}', 'net': net, 'errors': errs,
                      'loud': loud, 'ref': ref, 'got': got,
                      'note': f"FP-SUSPECT errors={errs} ref={ref} got={got}"})
            else:
                # ungrounded/singular-oracle circuits are malformed regardless
                # of numeric coincidence (F029/F106/F237 lesson): correct reject
                tall['ngspice-loud'] += 1  # counted as contained, not clean
                emit({'id': f'F{k:03d}', 'net': net, 'errors': errs,
                      'loud': loud, 'ref': ref, 'got': got,
                      'note': f"correct-reject (malformed; numeric coincidence) "
                              f"errors={errs}"})
        elif not errs and not loud and mismatch:
            tall['SILENT-WRONG'] += 1
            emit({'id': f'F{k:03d}', 'net': net, 'errors': errs,
                  'loud': loud, 'ref': ref, 'got': got,
                  'note': f"SILENT-WRONG ref={ref} got={got}"})
        elif loud:
            tall['ngspice-loud'] += 1
        elif not errs and not mismatch:
            tall['agree-ok'] += 1
        else:
            tall['CHECK'] += 1
            emit({'id': f'F{k:03d}', 'net': net, 'errors': errs,
                  'loud': loud, 'ref': ref, 'got': got,
                  'note': f"CHECK errors={errs} ref={ref} got={got}"})
    emit({'id': 'FUZZ-TALLY', 'note': str(tall), **tall})


def main():
    partA()
    fuzz()
    with open('logs/advers.json', 'w') as fh:
        json.dump(REC, fh, indent=1, default=str)
    print('-> logs/advers.json')


if __name__ == '__main__':
    main()
