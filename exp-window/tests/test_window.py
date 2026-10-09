"""Regression tests for continuation-aware event windowing (fast: ~1ms windows).

Run: cd exp-window && python3 tests/test_window.py  (exit!=0 on any FAIL)
Covers: TIME-shift necessity, RC/L handoff, PWL alternative, auto-windows,
state-class refusals, seam hygiene, scaled target (<=5% cost, uV agreement).
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..'))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), '..', 'Sim', 'exp-safety'))
sys.path.insert(0, '<repo>/exp-safety')
sys.path.insert(0, '<repo>/exp-window')

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


def final(s, v):
    p = s.get(v, [])
    return p[-1][1] if p else None


def t1_time_shift():
    base = ['t', 'B1 out 0 V=5*(u(TIME-500u)-u(TIME-500.1u))', 'R1 out 0 1k']
    r, s = tran_run(base + ['.tran 10n 600u 0 10n UIC'], ['v(out)'], timeout=60, tag='t')
    check('time-shift ref peak', peak(s, 'v(out)') == 5.0, f"got={peak(s,'v(out)')}")
    naive = ['t', 'B1 out 0 V=5*(u(TIME-500u)-u(TIME-500.1u))', 'R1 out 0 1k',
             '.tran 10n 200u 0 10n UIC']
    _, sn = tran_run(naive, ['v(out)'], timeout=60, tag='t')
    check('time-shift naive misses', peak(sn, 'v(out)') == 0.0, f"got={peak(sn,'v(out)')}")
    sh = W.shift_b_expr('5*(u(TIME-500u)-u(TIME-500.1u))', 400e-6)
    seg = ['t', f'B1 out 0 V={sh}', 'R1 out 0 1k', '.tran 10n 200u 0 10n UIC']
    _, ss = tran_run(seg, ['v(out)'], timeout=60, tag='t')
    check('time-shift shifted hits', peak(ss, 'v(out)') == 5.0, f"got={peak(ss,'v(out)')}")


def t2_rc_segmented():
    T0, Wd = 500e-6, 100e-9
    base = ['t', 'V1 s 0 DC 5', 'R1 s n 10k', 'C1 n 0 100n',
            f'B1 e 0 V=5*(u(TIME-{T0})-u(TIME-{T0+Wd}))', 'R2 e n 100k']
    base[4] = f'B1 e 0 V=5*(u(TIME-{T0})-u(TIME-{T0+Wd}))'
    r, s = tran_run(base + ['.tran 10n 1m 0 10n UIC'], ['v(e)', 'v(n)'], timeout=120, tag='t')
    ref_peak, ref_final, ref_n = peak(s, 'v(e)'), final(s, 'v(n)'), r['npts']
    wins, tf, _, vd = W.derive_windows(base, 1e-6)
    check('auto-window ok', vd == 'ok' and len(wins) == 1, f"{vd} {wins}")
    ok, rs, _ = W.classify_state(base)
    check('classify RC allow', ok, f"{rs}")
    out = W.run_segmented(base, ['v(e)', 'v(n)'], 1e-3, wins, 1e-6, tf,
                          handoff_nodes=['n'], timeout=120)
    check('segmented PASS', out['verdict'] == 'PASS', f"{out.get('verdict')} {out.get('reasons')}")
    if out['verdict'] == 'PASS':
        sp, sf = peak(out['series'], 'v(e)'), final(out['series'], 'v(n)')
        check('segmented peak exact', sp == ref_peak, f"{sp} vs {ref_peak}")
        check('segmented final uV', abs(sf - ref_final) < 5e-6, f"err={(sf-ref_final)*1e6:.2f}uV")
        check('segmented <=5% cost', out['npts'] / ref_n <= 0.05,
              f"{out['npts']}/{ref_n}={out['npts']/ref_n*100:.2f}%")


def t3_l_handoff():
    base = ['t', 'V1 a 0 DC 5', 'R1 a b 100', 'L1 b 0 10mH IC=0']
    ok, rs, info = W.classify_state(base)
    check('classify L allow-with-IC', ok and info['L'] == ['L1'], f"{rs}")
    r, s = tran_run(base + ['.tran 1u 1m 0 1u UIC'], ['v(b)', 'i(l1)'], timeout=60, tag='t')
    out = W.run_segmented(base, ['v(b)', 'i(l1)'], 1e-3, [(200e-6, 400e-6)], 1e-6, 100e-9,
                          handoff_nodes=['b'], handoff_l=['L1'], timeout=60)
    check('L segmented PASS', out['verdict'] == 'PASS', f"{out.get('verdict')} {out.get('reasons')}")
    if out['verdict'] == 'PASS':
        import bisect
        pts = out['series']['i(l1)']
        err = abs(pts[-1][1] - s['i(l1)'][-1][1])
        check('L final exact', err < 1e-6, f"err={err:.2g}")


def t4_pwl_cheap():
    T0, Wd = 500e-6, 100e-9
    bf = ['t', f'B1 in 0 V=5*(u(TIME-{T0})-u(TIME-{T0+Wd}))', 'R1 in 0 1k',
          '.tran 10n 1m 0 10n UIC']
    r, s = tran_run(bf, ['v(in)'], timeout=120, tag='t')
    pl = ['t', W.pwl_pulse_source('V1', 'in', '0', T0, Wd, 5.0, 1e-3),
          'R1 in 0 1k', '.tran 10u 1m UIC']
    rp, sp = tran_run(pl, ['v(in)'], timeout=60, tag='t')
    check('PWL peak exact', peak(sp, 'v(in)') == 5.0, f"got={peak(sp,'v(in)')}")
    check('PWL <=5% of fine-B', rp['npts'] / r['npts'] <= 0.05,
          f"{rp['npts']}/{r['npts']}={rp['npts']/r['npts']*100:.3f}%")


def t5_refusals():
    for name, net in [
        ('ddt', ['t', 'B1 o 0 V=ddt(v(in))', 'V1 in 0 DC 1', 'R1 in 0 1k']),
        ('D-TT', ['t', 'V1 a 0 DC 5', 'R1 a d 100', 'D1 d 0 DMOD',
                  '.model DMOD D(IS=1e-14 TT=50n)']),
        ('Tline', ['t', 'V1 in 0 PULSE(0 5 0 1n 1n 300n 1m)', 'R1 in t1 50',
                   'T1 t1 0 t2 0 Z0=50 TD=200n', 'R2 t2 0 50']),
        ('K', ['t', 'V1 a 0 DC 5', 'R1 a b 10', 'L1 b 0 1mH', 'L2 c 0 1mH',
               'K1 L1 L2 1', 'R2 c 0 10']),
    ]:
        ok, rs, _ = W.classify_state(net)
        check(f'classify refuse {name}', not ok, f"{rs}")


def t6_seam_hygiene():
    try:
        W._rewrite_segment_lines(
            ['t', 'B1 e 0 V=5*(u(TIME-500u)-u(TIME-500.1u))', 'R1 e 0 1k'],
            0.0, 100e-6, {'e': 5.0}, {}, 10e-9)
        check('G6 refuse .ic on B-node', False, 'no raise')
    except ValueError as e:
        check('G6 refuse .ic on B-node', True, str(e)[:60])
    unk = ['t', 'B1 o 0 V=TIME*TIME+V(in)', 'V1 in 0 DC 1', 'R1 in 0 1k']
    _, _, _, vd = W.derive_windows(unk, 1e-6)
    check('unknown TIME needs user windows', vd.startswith('unknown'), vd)


def main():
    t1_time_shift()
    t2_rc_segmented()
    t3_l_handoff()
    t4_pwl_cheap()
    t5_refusals()
    t6_seam_hygiene()
    print(f"\n{len(FAIL)} FAILURES: {FAIL}" if FAIL else "\nALL PASS")
    sys.exit(1 if FAIL else 0)


if __name__ == '__main__':
    main()
