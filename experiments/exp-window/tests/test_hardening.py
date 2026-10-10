"""Hardening regression tests: every adversarial SEVERE hole must now REFUSE
(or simulate exactly where shifting was implemented). Fast scales only.

Run: cd exp-window && python3 tests/test_hardening.py  (exit!=0 on any FAIL)
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..'))
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'exp-safety'))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'exp-window'))

from helper_safety import tran_run
import window as W

FAIL = []


def check(name, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        FAIL.append(name)


def peak(s, v):
    vals = [x for _, x in s.get(v, [])]
    return max(vals) if vals else None


def refused(out):
    return out.get('verdict') == 'REFUSE'


# S1 param-mediated TIME: G1 deny, run REFUSEs without simulating
def h1_param_time():
    base = ['t', '.param t0=500u',
            'B1 out 0 V=5*(u(TIME-t0)-u(TIME-t0-100n))', 'R1 out 0 1k']
    ok, rs, _ = W.classify_state(base)
    check('S1 param-TIME G1 deny', not ok, str(rs[:1]))
    out = W.run_segmented(base, ['v(out)'], 510e-6, [(499e-6, 501e-6)],
                          1e-6, 10e-9, [], timeout=60)
    check('S1 param-TIME run REFUSE', refused(out), str(out.get('reasons'))[:80])


# S2 ternary: comparison shifting now supports it -> PASS + exact peak
def h2_ternary():
    base = ['t', 'B1 out 0 V=(TIME>500u)?5:0', 'R1 out 0 1k']
    r, s = tran_run(base + ['.tran 10n 510u 0 10n UIC'], ['v(out)'],
                    timeout=60, tag='h')
    out = W.run_segmented(base, ['v(out)'], 510e-6, [(499e-6, 501e-6)],
                          1e-6, 10e-9, [], timeout=60)
    check('S2 ternary PASS', out.get('verdict') == 'PASS', str(out.get('verdict')))
    if out.get('verdict') == 'PASS':
        check('S2 ternary peak exact', peak(out['series'], 'v(out)') == peak(s, 'v(out)'),
              f"{peak(out['series'], 'v(out)')} vs {peak(s, 'v(out)')}")


# S3 pwl(TIME) in B: shifted now -> PASS + exact peak (flat-tailed pulse so
# the seam sits on quiet signal; bare-table-end extrapolation ramps forever)
def h3_pwl_time():
    base = ['t', 'B1 out 0 V=pwl(TIME,0,0, 500u,0, 500.1u,5, 500.2u,0, 510u,0)',
            'R1 out 0 1k']
    r, s = tran_run(base + ['.tran 10n 510u 0 10n UIC'], ['v(out)'],
                    timeout=60, tag='h')
    out = W.run_segmented(base, ['v(out)'], 510e-6, [(499e-6, 501e-6)],
                          1e-6, 10e-9, [], timeout=60)
    check('S3 pwl-TIME PASS', out.get('verdict') == 'PASS', str(out.get('verdict')))
    if out.get('verdict') == 'PASS':
        rp, sp = peak(s, 'v(out)'), peak(out['series'], 'v(out)')
        check('S3 pwl-TIME peak ~exact', sp is not None and abs(sp - rp) < 0.05,
              f"{sp} vs {rp}")


# S4 sin(k*TIME): residual TIME -> REFUSE
def h4_sine_phase():
    base = ['t', 'B1 out 0 V=5*sin(2*3.14159*1e3*TIME)', 'R1 out 0 1k']
    out = W.run_segmented(base, ['v(out)'], 3e-3, [(1.5e-3, 3e-3)],
                          1e-6, 10e-9, [], timeout=60)
    check('S4 sine-phase REFUSE', refused(out), str(out.get('reasons'))[:80])


# S5/S6/S13: G1 denies Q, behavioral-R, ddt; run REFUSEs statically
def h5_state_denies():
    for name, net in [
        ('S5-Q', ['t', 'Vcc v 0 5', 'Rc v o 1k', 'Rb x b 10k',
                  'Q1 o b 0 QM', '.model QM NPN(BF=100 TF=20n)']),
        ('S5-M', ['t', 'Vcc v 0 5', 'Rd v o 1k', 'M1 o g 0 0 MM',
                  '.model MM NMOS(VTO=1)']),
        ('S5-J', ['t', 'Vcc v 0 5', 'Rd v o 1k', 'J1 o g 0 JM',
                  '.model JM NJF(BETA=1e-3)']),
        ('S6-behavR', ['t', 'V1 s 0 DC 5', "R1 a b R='1k*(1+TIME/1m)'",
                       'C1 b 0 1u']),
        ('S13-ddt', ['t', 'B1 o 0 V=ddt(v(x))', 'V1 x 0 DC 1',
                     'R1 x 0 1k']),
        ('Adev', ['t', 'A1 1 0 2 0 swmod', '.model swmod s_xfer()']),
        ('D-CJO', ['t', 'V1 a 0 DC 5', 'R1 a d 100', 'D1 d 0 DM',
                   '.model DM D(IS=1e-14 CJO=100p)']),
    ]:
        ok, rs, _ = W.classify_state(net)
        check(f'G1 deny {name}', not ok, str(rs[:1])[:70])
    out = W.run_segmented(['t', 'Vcc v 0 5', 'Rc v o 1k', 'Rb x b 10k',
                           'Q1 o b 0 QM', '.model QM NPN(BF=100 TF=20n)'],
                          ['v(o)'], 1e-3, [(200e-6, 400e-6)], 1e-6, 10e-9,
                          ['o'], timeout=60)
    check('S5-Q run REFUSE', refused(out), str(out.get('reasons'))[:70])


# S7 coarse event miss: quantified event outside fine windows -> REFUSE
def h7_coarse_miss():
    base = ['t', 'V1 s 0 DC 5', 'R1 s n 10k', 'C1 n 0 100n',
            'B1 e 0 V=5*(u(TIME-500u)-u(TIME-500.1u))', 'R2 e n 100k']
    base[4] = 'B1 e 0 V=5*(u(TIME-500u)-u(TIME-500.1u))'
    out = W.run_segmented(base, ['v(e)', 'v(n)'], 1e-3,
                          [(500.5e-6, 500.7e-6)], 2e-6, 10e-9,
                          ['n'], timeout=60)
    check('S7 coarse-miss REFUSE', refused(out), str(out.get('reasons'))[:80])


# S8 ghost cap: incomplete handoff -> REFUSE (static, no sim)
def h8_ghost():
    base = ['t', 'V1 s 0 DC 5', 'R1 s hv 10k', 'C1 hv 0 100n',
            'R2 hv aux 10k', 'C2 aux 0 100n']
    out = W.run_segmented(base, ['v(hv)'], 2e-3, [(500e-6, 700e-6)],
                          1e-6, 10e-9, ['hv'], timeout=60)
    check('S8 ghost-cap REFUSE', refused(out), str(out.get('reasons'))[:80])


# S10 V-PWL now shifted -> PASS + exact peak (was 5->0 silent)
def h10_vpwl():
    T0, Wd = 500e-6, 100e-9
    base = ['t', 'V1 s 0 DC 5', 'R1 s n 10k', 'C1 n 0 100n',
            'V2 e 0 PWL(0 0 %.12g 0 %.12g 5 %.12g 5 %.12g 0 1m 0)'
            % (T0 - 1e-9, T0, T0 + Wd, T0 + Wd + 1e-9), 'R2 e n 100k']
    r, s = tran_run(base + ['.tran 10n 1m 0 10n UIC'], ['v(e)', 'v(n)'],
                    timeout=120, tag='h')
    wins, tf, _, vd = W.derive_windows(
        ['t', 'V1 s 0 DC 5', 'R1 s n 10k', 'C1 n 0 100n',
         f'B1 e 0 V=5*(u(TIME-{T0})-u(TIME-{T0+Wd}))', 'R2 e n 100k'], 1e-6)
    out = W.run_segmented(base, ['v(e)', 'v(n)'], 1e-3, wins, 1e-6, tf,
                          ['n'], timeout=120)
    check('S10 V-PWL PASS', out.get('verdict') == 'PASS', str(out.get('verdict')))
    if out.get('verdict') == 'PASS':
        check('S10 V-PWL peak exact',
              peak(out['series'], 'v(e)') == peak(s, 'v(e)'),
              f"{peak(out['series'], 'v(e)')} vs {peak(s, 'v(e)')}")


def h10b_sin():
    import bisect

    def maxerr(out, s):
        ts = [t for t, _ in s['v(o)']]
        vs = [v for _, v in s['v(o)']]
        err = 0.0
        for t, v in out['series']['v(o)']:
            i = min(bisect.bisect_right(ts, t), len(ts) - 1)
            i0 = max(i - 1, 0)
            t1, v1, t2, v2 = ts[i0], vs[i0], ts[i], vs[i]
            ref = v1 if t2 == t1 else v1 + (v2 - v1) * (t - t1) / (t2 - t1)
            err = max(err, abs(v - ref))
        return err

    base = ['t', 'V1 o 0 SIN(0 5 1k 500u 0 0)', 'R1 o 0 1k']
    out = W.run_segmented(base, ['v(o)'], 2e-3, [(600e-6, 700e-6)],
                          10e-6, 1e-6, [], timeout=60)
    check('SIN-delay seam-after-td PASS', out.get('verdict') == 'PASS',
          str(out.get('verdict')))
    if out.get('verdict') == 'PASS':
        r, s = tran_run(base + ['.tran 1u 2m 0 1u UIC'], ['v(o)'],
                        timeout=60, tag='h')
        check('SIN-delay seam-after-td exact', maxerr(out, s) < 1e-3,
              f"maxerr={maxerr(out, s):.3g}")
    out = W.run_segmented(base, ['v(o)'], 2e-3, [(400e-6, 700e-6)],
                          10e-6, 1e-6, [], timeout=60)
    check('SIN-delay seam-in-predelay REFUSE', refused(out),
          str(out.get('reasons'))[:70])
    damp = ['t', 'V1 o 0 SIN(0 5 1k 500u 1e3 0)', 'R1 o 0 1k']
    out = W.run_segmented(damp, ['v(o)'], 2e-3, [(600e-6, 700e-6)],
                          10e-6, 1e-6, [], timeout=60)
    check('SIN damped post-delay REFUSE', refused(out),
          str(out.get('reasons'))[:70])


# S11 omit-L: REFUSE; S12 C-IC strip only with .ic present (unit-level)
def h11_l_and_cic():
    base = ['t', 'V1 a 0 DC 5', 'R1 a b 100', 'L1 b 0 10mH IC=0']
    out = W.run_segmented(base, ['v(b)'], 1e-3, [(200e-6, 400e-6)],
                          1e-6, 100e-9, ['b'], timeout=60)
    check('S11 omit-L REFUSE', refused(out), str(out.get('reasons'))[:70])
    seg0 = W._rewrite_segment_lines(
        ['t', 'V1 s 0 DC 5', 'R1 s n 10k', 'C1 n 0 100n IC=5'],
        0.0, 500e-6, {}, {}, 1e-6)
    check('S12 C-IC kept in seg0', any('IC=5' in l for l in seg0), str(seg0[3]))
    seg1 = W._rewrite_segment_lines(
        ['t', 'V1 s 0 DC 5', 'R1 s n 10k', 'C1 n 0 100n IC=5'],
        500e-6, 500e-6, {'n': 1.0}, {}, 1e-6)
    has_ic_line = any(l.startswith('.ic') for l in seg1)
    kept_dev_ic = any('IC=5' in l and l.startswith('C1') for l in seg1)
    check('S12 C-IC stripped iff .ic present', has_ic_line and not kept_dev_ic,
          str(seg1[3:5])[:90])


# A2 G6 both terminals: reversed-B .ic on output refused (unit-level)
def h13_g6_terminals():
    base = ['t', 'B1 0 drv V=5*u(TIME-500u)', 'R1 drv 0 1k']
    try:
        W._rewrite_segment_lines(base, 400e-6, 200e-6, {'drv': 0.0}, {},
                                 10e-9)
        check('A2 reversed-B G6 refuse', False, 'no raise')
    except ValueError as e:
        check('A2 reversed-B G6 refuse', True, str(e)[:70])


# multi-event: auto derives every edge; overlap windows REFUSE
def h14_multi():
    base = ['t', 'V1 s 0 DC 5', 'R1 s n 10k', 'C1 n 0 100n',
            'B1 e 0 V=5*(u(TIME-3e-4)-u(TIME-3.001e-4))'
            ' + 5*(u(TIME-7e-4)-u(TIME-7.001e-4))',
            'R1e e 0 1k', 'R2 e n 100k']
    wins, tf, _, vd = W.derive_windows(base, 1e-6)
    check('multi auto 2 windows', vd == 'ok' and len(wins) == 2, f"{vd} {wins}")
    cov = (any(s < 3e-4 < e for s, e in wins)
           and any(s < 7e-4 < e for s, e in wins))
    check('multi auto covers both events', cov, str(wins))
    ov = W.run_segmented(base, ['v(e)', 'v(n)'], 1e-3,
                         [(499e-6, 501.1e-6), (499.6e-6, 501.7e-6)],
                         1e-6, 10e-9, ['n'], timeout=60)
    check('overlap windows REFUSE', refused(ov), str(ov.get('reasons'))[:70])


# G4 tau bounds: coarse and fine beyond tau_min/1000 REFUSE (static)
def h16_tau_bounds():
    base = ['t', 'V1 s 0 DC 5', 'R1 s n 10k', 'C1 n 0 1u',
            'B1 e 0 V=5*(u(TIME-5m)-u(TIME-5.0001m))', 'R2 e n 100k']
    base[4] = 'B1 e 0 V=5*(u(TIME-5m)-u(TIME-5.0001m))'
    tau = W.estimate_tau_min(base)
    check('tau estimate 10ms', tau == 0.01, f"tau={tau}")
    wins = [(5e-3 - 1e-6, 5e-3 + 1.1e-6)]
    out = W.run_segmented(base, ['v(e)', 'v(n)'], 10e-3, wins, 100e-6, 10e-9,
                          ['n'], timeout=60)
    check('G4 coarse>tau/1000 REFUSE', refused(out), str(out.get('reasons'))[:70])
    out = W.run_segmented(base, ['v(e)', 'v(n)'], 10e-3, wins, 10e-6, 100e-9,
                          ['n'], timeout=60)
    check('G4 fine>tau/1000 REFUSE', refused(out), str(out.get('reasons'))[:70])


# K both-winding handoff: PASS + exact vs continuous ref; missing: REFUSE
def h17_k_handoff():
    base = ['t', 'L1 n1 0 1mH IC=0', 'C1 n1 0 1uF', 'L2 n2 0 1mH IC=0',
            'C2 n2 0 1uF', 'R2 n2 0 10k', 'K1 L1 L2 0.9', '.ic v(n1)=5']
    r, s = tran_run(base + ['.tran 200n 2m 0 200n UIC'], ['v(n1)', 'v(n2)'],
                    timeout=120, tag='h')
    check('K ref excited', abs(s['v(n1)'][10][1]) > 1.0,
          f"{s['v(n1)'][10][1]}")
    out = W.run_segmented(base, ['v(n1)', 'v(n2)'], 2e-3,
                          [(1e-3 - 1e-6, 1e-3 + 1e-6)],
                          200e-9, 200e-9, ['n1', 'n2'], handoff_l=['L1', 'L2'],
                          timeout=120, resonant_period=198.69176531592202e-6)
    check('K both-IC PASS', out.get('verdict') == 'PASS', str(out.get('verdict')))
    if out.get('verdict') == 'PASS':
        import bisect
        ts = [t for t, _ in s['v(n1)']]
        vs = [v for _, v in s['v(n1)']]
        err = 0.0
        for t, v in out['series']['v(n1)']:
            i = min(bisect.bisect_right(ts, t), len(ts) - 1)
            i0 = max(i - 1, 0)
            t1, v1, t2, v2 = ts[i0], vs[i0], ts[i], vs[i]
            ref = v1 if t2 == t1 else v1 + (v2 - v1) * (t - t1) / (t2 - t1)
            err = max(err, abs(v - ref))
        check('K both-IC exact (<1mV)', err < 1e-3, f"maxerr={err:.3g}")
    out2 = W.run_segmented(base, ['v(n1)', 'v(n2)'], 2e-3,
                           [(1e-3 - 1e-6, 1e-3 + 1e-6)],
                           200e-9, 200e-9, ['n1', 'n2'], handoff_l=['L1'],
                           timeout=60, resonant_period=198.69176531592202e-6)
    check('K missing-winding REFUSE', refused(out2), str(out2.get('reasons'))[:70])


# Spike-3 H1/H2: B operator-precedence fragility. Shifting only the first
# number of `TIME - 1m * 2` re-binds the tree (2m threshold -> -1m);
# 2.75V silent PASS. Both spaced forms must REFUSE; exact forms still shift.
def h18_precedence():
    for expr in ['5*u(TIME - 1m * 2)', '5*u(TIME - 2m / 2)',
                 '(TIME > 1m * 2) ? 5 : 0']:
        try:
            W.shift_b_expr(expr, 1.5e-3)
            check('precedence REFUSE %s' % expr[:18], False, 'no raise')
        except ValueError as e:
            check('precedence REFUSE %s' % expr[:18], True, str(e)[:50])
    for expr, t_off in [('5*u(TIME-550u)', 100e-6),
                        ('5*u(TIME - 1m + 2m)', 1.5e-3),
                        ('(TIME>500u)?5:0', 100e-6)]:
        try:
            W.shift_b_expr(expr, t_off)
            check('precedence exact %s' % expr[:18], True, '')
        except ValueError as e:
            check('precedence exact %s' % expr[:18], False, str(e)[:50])
    base = ['t', 'B1 mid 0 V=5*u(TIME - 1m * 2)', 'R1 mid 0 1k']
    out = W.run_segmented(base, ['v(mid)'], 3e-3, [(1.9e-3, 2.1e-3)],
                          1e-6, 10e-9, [], timeout=60)
    check('precedence run REFUSE', refused(out), str(out.get('reasons'))[:70])


# Spike-3 H3: param-mediated SIN freq/phase passed through verbatim with
# unadvanced phase (540-deg, 10Vpp silent). Must REFUSE when t_off > 0.
def h19_param_sin():
    for net in [['t', 'V1 mid 0 SIN(0 5 {FREQ} 0 0 0)', 'R1 mid 0 1k'],
                ['t', 'V1 m 0 SIN(0 5 1k 0 0 {PH})', 'R1 m 0 1k']]:
        try:
            W.shift_v_line(net[1], 1.5e-3)
            check('param-SIN REFUSE %s' % net[1][9:22], False, 'no raise')
        except ValueError as e:
            check('param-SIN REFUSE %s' % net[1][9:22], True, str(e)[:50])
    try:
        s = W.shift_v_line('V1 m 0 SIN(0 5 1k 0 0 0)', 1.5e-3)
        check('numeric SIN still shifts', '540' in s, s)
    except ValueError as e:
        check('numeric SIN still shifts', False, str(e)[:50])


# Spike-3 H2/H3/H4/H5: E/G/H/F TIME smuggling, TIME-bearing .func,
# .include invisibility, + continuation join.
def h20_smuggle():
    for name, net in [
        ('E-VALUE-TIME', ['t', 'E1 out 0 VALUE={5*(u(TIME-200u)-u(TIME-200.1u))}',
                          'R1 out 0 1k']),
        ('G-VALUE-TIME', ['t', 'V1 c 0 DC 1', 'R1 c 0 1k',
                          'G1 out 0 VALUE={5*(u(TIME-200u)-u(TIME-200.1u))}',
                          'R2 out 0 1k']),
    ]:
        ok, rs, _ = W.classify_state(net)
        check('G1 deny %s' % name, not ok, str(rs[:1])[:70])
    out = W.run_segmented(['t', 'E1 out 0 VALUE={5*(u(TIME-200u)-u(TIME-200.1u))}',
                           'R1 out 0 1k'], ['v(out)'], 500e-6,
                          [(199e-6, 201e-6)], 10e-6, 10e-9, [], timeout=60)
    check('E-VALUE-TIME run REFUSE', refused(out), str(out.get('reasons'))[:70])
    ok, _, _ = W.classify_state(['t', 'V1 c 0 DC 1', 'R1 c 0 1k',
                                 'E1 out 0 c 0 10', 'R2 out 0 1k'])
    check('plain-E still ALLOW', ok, '')
    ok, rs, _ = W.classify_state(['t', '.func f(x) {5*u(x+TIME-200u)}',
                                  'B1 out 0 V=f(0)', 'R1 out 0 1k'])
    check('G1 deny func-TIME', not ok, str(rs[:1])[:70])
    ok, _, _ = W.classify_state(['t', '.include /tmp/x.inc', 'V1 out 0 DC 1',
                                 'R1 out 0 1k'])
    check('G1 deny include', not ok, '')
    import validator as V
    devs, bx, _, _, errs = V.parse_netlist(
        ['t', 'B1 out 0 V=5*(u(TIME-200u)', '+ -u(TIME-200.1u))',
         'R1 out 0 1k'])
    check('+ continuation joined',
          not any(e['code'] == 'UNKNOWN-KIND' for e in errs)
          and '200.1u' in bx.get('B1', ('', ''))[1], str(bx.get('B1'))[:60])


# Spike-3 H6/H7: continuous-SIN whole-sim bound (period/50) and non-resonant
# inductive bound (tau_L/100). K1b 100kHz SIN at 1us was PASS-but-185mV;
# K2 RL at tau/2 was PASS-but-2mV.
def h21_drive_bounds():
    base = ['t', 'V1 s 0 SIN(0 5 100k)', 'D1 s n DMOD',
            '.model DMOD D(IS=1e-14)', 'C1 n 0 10n', 'R1 n 0 100k']
    check('SIN bound 200ns', abs(W.vsin_bound(base) - 200e-9) < 1e-12,
          str(W.vsin_bound(base)))
    out = W.run_segmented(base, ['v(n)'], 30e-6, [(12e-6, 14e-6)],
                          1e-6, 1e-6, handoff_nodes=['n'], timeout=60)
    check('K1b coarse 1us REFUSE', refused(out), str(out.get('reasons'))[:70])
    b2 = ['t', 'B1 e 0 V=5*(u(TIME-10u)-u(TIME-10.1u))', 'R1 e b 10',
          'L1 b 0 1mH IC=0']
    check('tau_L 100us', abs(W.estimate_tau_l(b2) - 100e-6) < 1e-12,
          str(W.estimate_tau_l(b2)))
    out = W.run_segmented(b2, ['v(b)', 'i(l1)'], 300e-6, [(9e-6, 11e-6)],
                          50e-6, 10e-9, handoff_nodes=['b'],
                          handoff_l=['L1'], timeout=60)
    check('K2 coarse 50us REFUSE', refused(out), str(out.get('reasons'))[:70])
    # No over-refusal: t3 RL at tau/100 and h17 resonant ring still PASS.
    out = W.run_segmented(['t', 'V1 a 0 DC 5', 'R1 a b 100',
                           'L1 b 0 10mH IC=0'], ['v(b)', 'i(l1)'], 1e-3,
                          [(200e-6, 400e-6)], 1e-6, 100e-9,
                          handoff_nodes=['b'], handoff_l=['L1'], timeout=60)
    check('t3 RL still PASS', out.get('verdict') == 'PASS',
          str(out.get('verdict')))
    check('h17 tau_L skipped',
          W.estimate_tau_l(['t', 'L1 n1 0 1mH IC=0', 'C1 n1 0 1uF',
                            'L2 n2 0 1mH IC=0', 'C2 n2 0 1uF',
                            'R2 n2 0 10k', 'K1 L1 L2 0.9']) is None, '')


# Spike-3 H8: G6 E-terminal mapping (outputs are nodes[:2], not [2:4]).
def h22_e_mapping():
    try:
        W._rewrite_segment_lines(['t', 'E1 o1 o2 c1 c2 10', 'V1 c1 0 DC 1',
                                  'R1 c1 0 1k', 'C1 o1 0 1u'],
                                 100e-6, 100e-6, {'o1': 1.0}, {}, 1e-6)
        check('E-output .ic REFUSE', False, 'allowed')
    except ValueError as e:
        check('E-output .ic REFUSE', True, str(e)[:60])


# Spike-4: resonant auto-coarse REFUSE (USER-SUPPLIED BOUND REQUIRED).
# Minimized hole: ideal LC 1mH/1uF at T0/50 PASSes locally (amp -0.06%)
# while accumulating 2.2deg/5periods, 46.7deg/100periods [E]. T0 is not
# auto-extractable (coupled/multi-rate take min-T0), so without an explicit
# user period the run must REFUSE rather than pass an unprovable grid.
def h23_resonant_bound():
    import math
    T0 = 2 * math.pi * math.sqrt(1e-3 * 1e-6)  # 198.69us
    base = ['t', 'L1 n1 0 1mH IC=0', 'C1 n1 0 1uF IC=5']
    check('resonant detected',
          W.detect_resonant(base) == ['L1'], str(W.detect_resonant(base)))
    check('non-resonant L undetected',
          W.detect_resonant(['t', 'V1 a 0 DC 5', 'R1 a b 100',
                             'L1 b 0 10mH IC=0']) == [], '')
    out = W.run_segmented(base, ['v(n1)', 'i(l1)'], 5 * T0,
                          [(2.5 * T0 - 1e-6, 2.5 * T0 + 1e-6)],
                          T0 / 50, T0 / 50, ['n1'], handoff_l=['L1'],
                          timeout=120)
    check('resonant auto-coarse REFUSE', refused(out),
          str(out.get('reasons'))[:70])
    out = W.run_segmented(base, ['v(n1)', 'i(l1)'], 5 * T0,
                          [(2.5 * T0 - 1e-6, 2.5 * T0 + 1e-6)],
                          T0 / 50, T0 / 50, ['n1'], handoff_l=['L1'],
                          timeout=120, resonant_period=T0)
    check('resonant with period PASS', out.get('verdict') == 'PASS',
          str(out.get('verdict')))
    if out.get('verdict') == 'PASS':
        pk = peak(out['series'], 'v(n1)')
        check('resonant with period peak ~5V',
              pk is not None and abs(pk - 5.0) < 0.01, f"peak={pk}")
    out = W.run_segmented(base, ['v(n1)', 'i(l1)'], 5 * T0,
                          [(2.5 * T0 - 1e-6, 2.5 * T0 + 1e-6)],
                          T0 / 10, T0 / 50, ['n1'], handoff_l=['L1'],
                          timeout=120, resonant_period=T0)
    check('resonant coarse T0/10 REFUSE', refused(out),
          str(out.get('reasons'))[:70])


def main():
    h1_param_time()
    h2_ternary()
    h3_pwl_time()
    h4_sine_phase()
    h5_state_denies()
    h7_coarse_miss()
    h8_ghost()
    h10_vpwl()
    h10b_sin()
    h11_l_and_cic()
    h13_g6_terminals()
    h14_multi()
    h16_tau_bounds()
    h17_k_handoff()
    h18_precedence()
    h19_param_sin()
    h20_smuggle()
    h21_drive_bounds()
    h22_e_mapping()
    h23_resonant_bound()
    print(f"\n{len(FAIL)} FAILURES: {FAIL}" if FAIL else "\nALL PASS")
    sys.exit(1 if FAIL else 0)


if __name__ == '__main__':
    main()
