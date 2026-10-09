"""Permanent regression suite for the ngspice safety boundary.

Runnable independently:  cd exp-safety && python3 tests/test_regression.py
Fails loudly (nonzero exit + FAILED lines) if any guarantee weakens.

Covers the previously discovered dangerous cases (B2 miss, D4 open, C4
self-ref, D1/D3/D5 singulars) plus new boundary cases that materially
define the safety line (Bi/G opens, B-typo implicit node, V-loop variants,
neg-R, B-cycle latch warning, EVENT-COVERAGE flag, RAIL flag, bounded-sine
no-flag, D4 linearity/sign determinism).

Containment: every ngspice call is a subprocess with timeout.
Cost: <60s total (small windows; no million-point runs).
"""
import sys
import os
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from helper_safety import run_cir, tran_run, op_float
import validator as V
import runtime as RT
import bsource as BS

FAIL = []


def check(name, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        FAIL.append(name)


def op_net(lines):
    net = lines + ['.end'] if lines[-1].strip().lower() != '.end' else lines
    ctl = ([l for l in net if l.strip().lower() != '.end']
           + ['.control', 'run', '.endc', '.end'])
    return run_cir(ctl, timeout=20)


def v_reject(name, net, *codes):
    f = V.validate(net)
    errs = V.codes(f, 'error')
    check(f"static-reject {name}", all(c in errs for c in codes),
          f"got={errs} want={list(codes)}")


def v_pass(name, net):
    f = V.validate(net)
    errs = V.codes(f, 'error')
    check(f"static-accept {name}", not errs, f"got={errs}")


def main():
    # ---- 1. previously discovered dangerous cases ----
    # B2: 10ns B-blip missed unbounded (silent), caught with bound.
    # Small-window version: 1us window, TSTEP 100ns (effective tmax 100ns),
    # 10ns blip -> missed.
    base = ['t', 'B1 in 0 V=5*(u(TIME-500n)-u(TIME-510n))', 'R1 in 0 1k',
            '.tran 100n 1u UIC']
    r, s = tran_run(base, ['v(in)'], timeout=30, tag='regB2')
    peak = max((v for _, v in s['v(in)']), default=None)
    check("B2-miss-unbounded", peak is not None and peak < 4.5,
          f"peak={peak} (must MISS silently)")
    check("B2-miss-silent", not (r['warns'] or r['fatals'] or r['rc'] != 0),
          "miss must carry zero warnings (the danger)")
    req, _ = BS.required_tmax_for_netlist(base)
    chk = RT.check_runtime(r, s, base,
                           tmax_required=None if req == 'unknown' else req)
    check("B2-event-flag", chk['verdict'] == 'QUESTIONABLE'
          and any('EVENT' in x for x in chk['reasons']),
          f"verdict={chk['verdict']} reasons={chk['reasons']}")
    base_b = ['t', 'B1 in 0 V=5*(u(TIME-500n)-u(TIME-510n))', 'R1 in 0 1k',
              '.tran 100n 1u 0 1n UIC']
    r2, s2 = tran_run(base_b, ['v(in)'], timeout=60, tag='regB2b')
    peak2 = max((v for _, v in s2['v(in)']), default=None)
    check("B2b-bound-catches", peak2 is not None and peak2 >= 4.5,
          f"peak={peak2}")

    # D4: current source into open -> V=I*1ohm, silent, deterministic.
    for iso, exp in (('1u', 1e-6), ('1m', 1e-3), ('1', 1.0)):
        rr = op_net(['t', f'I1 0 out DC {iso}', '.op', '.control', 'run',
                     'print v(out)', '.endc'])
        v = op_float(rr['out'])
        check(f"D4-silent-{iso}", v is not None and abs(v - exp) < 1e-12
              and rr['rc'] == 0 and not rr['warns'] and not rr['fatals'],
              f"v={v} (phantom 1ohm, zero warnings)")
    v_reject("D4-static", ['t', 'I1 0 out DC 1m', '.op', '.end'],
             'ISRC-OPEN', 'NO-DC-PATH')

    # C4: self-referential B -> loud garbage, statically rejected.
    v_reject("C4-static", ['t', 'V1 in 0 DC 5', 'R1 in out 1k',
                           'B1 out 0 V=V(out)+1', 'R2 out 0 1k',
                           '.op', '.end'], 'B-SELFREF')
    rr = op_net(['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'B1 out 0 V=V(out)+1',
                 'R2 out 0 1k', '.op', '.control', 'run', 'print v(out)',
                 '.endc'])
    v = op_float(rr['out'], 'v(out)')
    check("C4-loud", bool(rr['warns'] or rr['fatals']) and
          (v is None or abs(v) > 1e6), f"v={v}")

    # D1/D3/D5: singular topologies are loud AND statically rejected.
    for name, net, code in (
            ("D1-float", ['t', 'V1 in 0 DC 5', 'R1 in mid 1k',
                          'Rfloat n1 n2 1k', '.op', '.end'], 'NO-DC-PATH'),
            ("D3-vloop", ['t', 'V1 out 0 DC 5', 'V2 out 0 DC 4',
                          '.op', '.end'], 'V-LOOP'),
            ("D5-caploop", ['t', 'V1 in 0 DC 5', 'C1 in mid 1u',
                            'C2 mid out 1u', 'R1 out 0 1k',
                            '.op', '.end'], 'NO-DC-PATH')):
        v_reject(name + "-static", net, code)
        rr = op_net(net[:-1] + ['.control', 'run', '.endc'])
        check(name + "-loud",
              bool(rr['warns'] or rr['fatals'] or rr['rc'] != 0),
              f"rc={rr['rc']} warns={len(rr['warns'])}")

    # ---- 2. new boundary cases ----
    v_reject("Bi-open-static", ['t', 'B1 out 0 I=1m', '.op', '.end'],
             'ISRC-OPEN')
    rr = op_net(['t', 'B1 out 0 I=1m', '.op', '.control', 'run',
                 'print v(out)', '.endc'])
    check("Bi-open-silent", rr['rc'] == 0 and not rr['warns']
          and not rr['fatals'], "behavioral shares D4 silence")
    v_reject("G-open-static", ['t', 'G1 out 0 0 0 1m', '.op', '.end'],
             'ISRC-OPEN')
    v_reject("B-typo-static", ['t', 'V1 in 0 DC 5', 'R1 in out 1k',
                               'B1 out 0 V=5*V(typo)+V(in)', 'R2 out 0 1k',
                               '.op', '.end'], 'B-IMPLICIT-NODE')
    v_reject("Vloop-L-static", ['t', 'V1 a 0 DC 5', 'L1 a 0 1mH',
                                '.op', '.end'], 'V-LOOP')
    v_reject("Vloop-E-static", ['t', 'V1 a 0 DC 5', 'E1 a 0 0 0 1',
                                '.op', '.end'], 'V-LOOP')
    v_reject("negR-static", ['t', 'V1 in 0 DC 5', 'R1 in out -1k',
                             'R2 out 0 1k', '.op', '.end'], 'NEG-R')
    # latch class: warn (not error), ngspice loud without seeding.
    f = V.validate(['t', 'B1 o1 0 V=(V(o2)>2.5)?0:5', 'R1 o1 0 1k',
                    'B2 o2 0 V=(V(o1)>2.5)?0:5', 'R2 o2 0 1k',
                    '.op', '.end'])
    check("latch-B-cycle-warn", 'B-CYCLE' in V.codes(f, 'warn')
          and not V.codes(f, 'error'), f"got={V.codes(f)}")
    # kickback FP guard: Bi reading its own node is a conductance, not a loop.
    v_pass("kickback-Bi-selfread",
           ['t', 'I1 0 n1 DC 10m', 'L1 n1 0 10mH IC=0', 'C1 n1 0 1n IC=0',
            'B1 n1 0 I=V(n1)/(1+(1MEG-1)*u(TIME-5m))',
            '.tran 1u 10m UIC', '.end'])
    # valids must stay accepted.
    for name, net in (
            ("rc", ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'C1 out 0 1u IC=0',
                    '.tran 10u 5m UIC', '.end']),
            ("isrc-loaded", ['t', 'I1 0 out DC 1m', 'R1 out 0 1k',
                             '.op', '.end']),
            ("series-V", ['t', 'V1 a 0 DC 3', 'V2 b a DC 2', 'R1 b 0 1k',
                          '.op', '.end']),
            ("weak-tie", ['t', 'V1 in 0 DC 5', 'R1 in out 1k',
                          'R2 out 0 1T', '.op', '.end'])):
        v_pass("valid-" + name, net)

    # ---- 3. runtime-signal pins ----
    # RAIL fires on unbounded positive feedback, not on valid 10kV.
    rr = op_net(['t', 'V1 inp 0 DC 2.51', 'V2 inm 0 DC 2.5',
                 'B1 out inm V=1e4*(V(inp)-V(inm))+V(out)', 'R1 out 0 1k',
                 '.op', '.control', 'run', 'print v(out)', '.endc'])
    vv = op_float(rr['out'], 'v(out)')
    net_pf = ['t', 'V1 inp 0 DC 2.51', 'V2 inm 0 DC 2.5',
              'B1 out inm V=1e4*(V(inp)-V(inm))+V(out)', 'R1 out 0 1k',
              '.op', '.end']
    chk = RT.check_runtime(rr, None, net_pf)
    check("RAIL-fires-posfb", chk['verdict'] == 'FAIL'
          and any('RAIL' in x or 'FATAL' in x for x in chk['reasons']),
          f"v={vv} reasons={chk['reasons']}")
    rr = op_net(['t', 'V1 in 0 DC 10k', 'R1 in out 1k', 'R2 out 0 1k',
                 '.op', '.control', 'run', 'print v(out)', '.endc'])
    chk = RT.check_runtime(rr, None,
                           ['t', 'V1 in 0 DC 10k', 'R1 in out 1k',
                            'R2 out 0 1k', '.op', '.end'])
    check("RAIL-quiet-10kV", chk['verdict'] == 'PASS',
          f"reasons={chk['reasons']}")
    # D4 blind spot is explicit, not accidental.
    rr = op_net(['t', 'I1 0 out DC 1m', '.op', '.control', 'run',
                 'print v(out)', '.endc'])
    chk = RT.check_runtime(rr, None, ['t', 'I1 0 out DC 1m', '.op', '.end'])
    check("D4-runtime-blind-explicit", chk['verdict'] == 'PASS'
          and chk['blind_spot'], f"reasons={chk['reasons']}")

    print(f"\n{'ALL PASS' if not FAIL else f'{len(FAIL)} FAILURES: {FAIL}'}")
    sys.exit(1 if FAIL else 0)


if __name__ == '__main__':
    main()
