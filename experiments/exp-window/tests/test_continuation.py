"""Production API tests: taxonomy, plan/execute split, bypass resistance.

Run: cd exp-window && python3 tests/test_continuation.py (exit!=0 on FAIL)

Covers:
  * PASS paths via plan()+execute() (RC segmented exactness, ternary shift)
  * every RefusalCode reachable from a real spike-1..4 failure mode
  * plan() performs no simulation (guarded)
  * all API-level bypasses refuse (manual plan, tampered state/windows,
    post-planning model/timestep/source/include/resonant changes)
"""

import os
import sys
from dataclasses import replace

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..'))
sys.path.insert(0, '<repo>/experiments/exp-safety')
sys.path.insert(0, '<repo>/experiments/exp-window')

from helper_safety import tran_run
import continuation as C
import window as W

FAIL = []


def check(name, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        FAIL.append(name)


def plan_code(req):
    pres = C.plan(req)
    if isinstance(pres, C.PlanOk):
        return ('OK', pres)
    codes = sorted(r.code.value for r in pres.refusals)
    return (codes[0] if codes else '?', pres)


def RC_BASE():
    T0, Wd = 500e-6, 100e-9
    return ['t', 'V1 s 0 DC 5', 'R1 s n 10k', 'C1 n 0 100n',
            f'B1 e 0 V=5*(u(TIME-{T0})-u(TIME-{T0+Wd}))', 'R2 e n 100k']


def RC_REQ(windows='auto'):
    wins, tf, _, vd = W.derive_windows(RC_BASE(), 1e-6)
    assert vd == 'ok', vd
    return C.ContinuationRequest(
        netlist=tuple(RC_BASE()), t_stop=1e-3,
        windows=None if windows == 'auto' else tuple(windows),
        tmax_coarse=1e-6, tmax_fine=tf,
        handoff_nodes=('n',), handoff_l=(),
        observables=('v(e)', 'v(n)'))


def peak(s, v):
    vals = [x for _, x in s.get(v, [])]
    return max(vals) if vals else None


# ---- 1. PASS paths -------------------------------------------------------

def t_pass_rc():
    req = RC_REQ()
    guarded = []
    orig = C._W.run_segmented

    def boom(*a, **k):
        guarded.append(1)
        raise AssertionError('plan() must not simulate')
    C._W.run_segmented = boom
    try:
        pres = C.plan(req)
    finally:
        C._W.run_segmented = orig
    check('plan RC ok', isinstance(pres, C.PlanOk),
          str(getattr(pres, 'refusals', ''))[:80])
    check('plan ran no simulation', not guarded, str(guarded))
    if not isinstance(pres, C.PlanOk):
        return
    check('plan has 3 segments', len(pres.plan.segments) == 3,
          str(pres.plan.segments))
    eres = C.execute(pres.plan, req)
    check('execute RC ok', isinstance(eres, C.ExecutionOk),
          str(getattr(eres, 'refusals', getattr(eres, 'reasons', '')))[:80])
    if not isinstance(eres, C.ExecutionOk):
        return
    check('executor kept plan boundaries',
          tuple(eres.segments) == tuple(pres.plan.segments),
          f"{eres.segments} vs {pres.plan.segments}")
    r, s = tran_run(RC_BASE() + ['.tran 10n 1m 0 10n UIC'],
                    ['v(e)', 'v(n)'], timeout=120, tag='c')
    check('RC peak exact', peak(eres.series, 'v(e)') == peak(s, 'v(e)'),
          f"{peak(eres.series,'v(e)')} vs {peak(s,'v(e)')}")
    fe = eres.series['v(n)'][-1][1]
    fr = s['v(n)'][-1][1]
    check('RC final uV', abs(fe - fr) < 5e-6, f"err={(fe-fr)*1e6:.2f}uV")


def t_pass_ternary():
    base = ['t', 'B1 out 0 V=(TIME>500u)?5:0', 'R1 out 0 1k']
    req = C.ContinuationRequest(
        netlist=tuple(base), t_stop=510e-6, windows=((499e-6, 501e-6),),
        tmax_coarse=1e-6, tmax_fine=10e-9,
        handoff_nodes=(), handoff_l=(), observables=('v(out)',))
    pres = C.plan(req)
    check('plan ternary ok', isinstance(pres, C.PlanOk),
          str(getattr(pres, 'refusals', ''))[:80])
    if not isinstance(pres, C.PlanOk):
        return
    eres = C.execute(pres.plan, req)
    check('execute ternary ok', isinstance(eres, C.ExecutionOk),
          str(getattr(eres, 'refusals', getattr(eres, 'reasons', '')))[:80])
    if isinstance(eres, C.ExecutionOk):
        check('ternary peak shifted (5V, not 0V)',
              peak(eres.series, 'v(out)') == 5.0,
              f"got={peak(eres.series,'v(out)')}")


# ---- 2. taxonomy: one live reproducer per code ----------------------------

def t_taxonomy():
    T0, Wd = 500e-6, 100e-9
    cases = [
        ('hidden Q', ['t', 'Vcc v 0 5', 'Rc v o 1k', 'Rb x b 10k',
                      'Q1 o b 0 QM', '.model QM NPN(BF=100 TF=20n)'],
         dict(t_stop=1e-3, windows=((200e-6, 400e-6),), tmax_coarse=1e-6,
              tmax_fine=10e-9, handoff_nodes=('o',), observables=('v(o)',)),
         'unsupported_hidden_state'),
        ('hidden D-TT', ['t', 'V1 a 0 DC 5', 'R1 a d 100', 'D1 d 0 DMOD',
                         '.model DMOD D(IS=1e-14 TT=50n)'],
         dict(t_stop=1e-3, windows=((200e-6, 400e-6),), tmax_coarse=1e-6,
              tmax_fine=10e-9, handoff_nodes=(), observables=('v(d)',)),
         'unsupported_hidden_state'),
        ('hidden D-CJO', ['t', 'V1 a 0 DC 5', 'R1 a d 100', 'D1 d 0 DM',
                          '.model DM D(IS=1e-14 CJO=100p)'],
         dict(t_stop=1e-3, windows=((200e-6, 400e-6),), tmax_coarse=1e-6,
              tmax_fine=10e-9, handoff_nodes=(), observables=('v(d)',)),
         'unsupported_hidden_state'),
        ('hidden ddt', ['t', 'B1 o 0 V=ddt(v(x))', 'V1 x 0 DC 1',
                        'R1 x 0 1k'],
         dict(t_stop=1e-3, windows=((200e-6, 400e-6),), tmax_coarse=1e-6,
              tmax_fine=10e-9, handoff_nodes=(), observables=('v(o)',)),
         'unsupported_hidden_state'),
        ('hidden Tline', ['t', 'V1 in 0 PULSE(0 5 0 1n 1n 300n 1m)',
                          'R1 in t1 50', 'T1 t1 0 t2 0 Z0=50 TD=200n',
                          'R2 t2 0 50'],
         dict(t_stop=1e-3, windows=((200e-6, 400e-6),), tmax_coarse=1e-6,
              tmax_fine=10e-9, handoff_nodes=(), observables=('v(t2)',)),
         'unsupported_hidden_state'),
        ('hidden X', ['t', 'V1 a 0 DC 5', 'R1 a b 1k', 'X1 b 0 mysub'],
         dict(t_stop=1e-3, windows=((200e-6, 400e-6),), tmax_coarse=1e-6,
              tmax_fine=10e-9, handoff_nodes=(), observables=('v(b)',)),
         'unsupported_hidden_state'),
        ('hidden A', ['t', 'A1 1 0 2 0 swmod', '.model swmod s_xfer()'],
         dict(t_stop=1e-3, windows=((200e-6, 400e-6),), tmax_coarse=1e-6,
              tmax_fine=10e-9, handoff_nodes=(), observables=('v(2)',)),
         'unsupported_hidden_state'),
        ('unshiftable param-TIME', ['t', '.param t0=500u',
                                    'B1 out 0 V=5*(u(TIME-t0)-u(TIME-t0-100n))',
                                    'R1 out 0 1k'],
         dict(t_stop=510e-6, windows=((499e-6, 501e-6),), tmax_coarse=1e-6,
              tmax_fine=10e-9, handoff_nodes=(), observables=('v(out)',)),
         'unshiftable_time'),
        ('unshiftable sine-phase', ['t', 'B1 out 0 V=5*sin(2*3.14159*1e3*TIME)',
                                    'R1 out 0 1k'],
         dict(t_stop=3e-3, windows=((1.5e-3, 3e-3),), tmax_coarse=1e-6,
              tmax_fine=10e-9, handoff_nodes=(), observables=('v(out)',)),
         'unshiftable_time'),
        ('unshiftable E-VALUE', ['t', 'E1 out 0 VALUE={5*(u(TIME-200u)-u(TIME-200.1u))}',
                                 'R1 out 0 1k'],
         dict(t_stop=500e-6, windows=((199e-6, 201e-6),), tmax_coarse=10e-6,
              tmax_fine=10e-9, handoff_nodes=(), observables=('v(out)',)),
         'unshiftable_time'),
        ('unshiftable func', ['t', '.func f(x) {5*u(x+TIME-200u)}',
                              'B1 out 0 V=f(0)', 'R1 out 0 1k'],
         dict(t_stop=500e-6, windows=((199e-6, 201e-6),), tmax_coarse=10e-6,
              tmax_fine=10e-9, handoff_nodes=(), observables=('v(out)',)),
         'unshiftable_time'),
        ('unshiftable precedence', ['t', 'B1 mid 0 V=5*u(TIME - 1m * 2)',
                                    'R1 mid 0 1k'],
         dict(t_stop=3e-3, windows=((1.9e-3, 2.1e-3),), tmax_coarse=1e-6,
              tmax_fine=10e-9, handoff_nodes=(), observables=('v(mid)',)),
         'unshiftable_time'),
        ('unshiftable param-SIN', ['t', 'V1 mid 0 SIN(0 5 {FREQ} 0 0 0)',
                                   'R1 mid 0 1k'],
         dict(t_stop=2e-3, windows=((600e-6, 700e-6),), tmax_coarse=10e-6,
              tmax_fine=1e-6, handoff_nodes=(), observables=('v(mid)',)),
         'unshiftable_time'),
        ('incomplete ghost-cap', ['t', 'V1 s 0 DC 5', 'R1 s hv 10k',
                                  'C1 hv 0 100n', 'R2 hv aux 10k',
                                  'C2 aux 0 100n'],
         dict(t_stop=2e-3, windows=((500e-6, 700e-6),), tmax_coarse=1e-6,
              tmax_fine=10e-9, handoff_nodes=('hv',),
              observables=('v(hv)',)),
         'incomplete_state_handoff'),
        ('incomplete omit-L', ['t', 'V1 a 0 DC 5', 'R1 a b 100',
                               'L1 b 0 10mH IC=0'],
         dict(t_stop=1e-3, windows=((200e-6, 400e-6),), tmax_coarse=1e-6,
              tmax_fine=100e-9, handoff_nodes=('b',), handoff_l=(),
              observables=('v(b)',)),
         'incomplete_state_handoff'),
        ('incomplete K-winding', ['t', 'L1 n1 0 1mH IC=0', 'C1 n1 0 1uF',
                                  'L2 n2 0 1mH IC=0', 'C2 n2 0 1uF',
                                  'R2 n2 0 10k', 'K1 L1 L2 0.9',
                                  '.ic v(n1)=5'],
         dict(t_stop=2e-3, windows=((1e-3 - 1e-6, 1e-3 + 1e-6),),
              tmax_coarse=200e-9, tmax_fine=200e-9,
              handoff_nodes=('n1', 'n2'), handoff_l=('L1',),
              observables=('v(n1)', 'v(n2)'),
              resonant_period=198.69176531592202e-6),
         'incomplete_state_handoff'),
        ('event outside', ['t', 'V1 s 0 DC 5', 'R1 s n 10k', 'C1 n 0 100n',
                           f'B1 e 0 V=5*(u(TIME-{T0})-u(TIME-{T0+Wd}))',
                           'R2 e n 100k'],
         dict(t_stop=1e-3, windows=((500.5e-6, 500.7e-6),),
              tmax_coarse=1e-6, tmax_fine=10e-9, handoff_nodes=('n',),
              observables=('v(e)', 'v(n)')),
         'event_outside_window'),
        ('overlap', RC_BASE(),
         dict(t_stop=1e-3, windows=((499e-6, 501.1e-6), (499.6e-6, 501.7e-6)),
              tmax_coarse=1e-6, tmax_fine=10e-9, handoff_nodes=('n',),
              observables=('v(e)', 'v(n)')),
         'overlapping_windows'),
        ('invalid window past-T', RC_BASE(),
         dict(t_stop=1e-3, windows=((900e-6, 1100e-6),), tmax_coarse=1e-6,
              tmax_fine=10e-9, handoff_nodes=('n',),
              observables=('v(e)', 'v(n)')),
         'invalid_window'),
        ('coarse tau', ['t', 'V1 s 0 DC 5', 'R1 s n 10k', 'C1 n 0 1u',
                        'B1 e 0 V=5*(u(TIME-5m)-u(TIME-5.0001m))',
                        'R2 e n 100k'],
         dict(t_stop=10e-3, windows=((5e-3 - 1e-6, 5e-3 + 1.1e-6),),
              tmax_coarse=100e-6, tmax_fine=10e-9, handoff_nodes=('n',),
              observables=('v(e)', 'v(n)')),
         'coarse_bound_exceeded'),
        ('fine event', RC_BASE(),
         dict(t_stop=1e-3, windows=((499e-6, 501.1e-6),), tmax_coarse=1e-6,
              tmax_fine=1e-6, handoff_nodes=('n',),
              observables=('v(e)', 'v(n)')),
         'fine_bound_exceeded'),
        ('resonant missing', ['t', 'L1 n1 0 1mH IC=0', 'C1 n1 0 1uF IC=5'],
         dict(t_stop=5 * 198.69176531592202e-6,
              windows=((2.5 * 198.69176531592202e-6 - 1e-6,
                        2.5 * 198.69176531592202e-6 + 1e-6),),
              tmax_coarse=198.69176531592202e-6 / 50,
              tmax_fine=198.69176531592202e-6 / 50,
              handoff_nodes=('n1',), handoff_l=('L1',),
              observables=('v(n1)', 'i(l1)')),
         'resonant_period_unavailable'),
        ('observable mapping', ['t', 'V1 s 0 DC 5', 'R1 s n 10k',
                                'C1 n 0 100n',
                                f'B1 e 0 V=5*(u(TIME-{T0})-u(TIME-{T0+Wd}))',
                                'R2 e n 100k'],
         dict(t_stop=1e-3, windows=None, tmax_coarse=1e-6, tmax_fine=10e-9,
              handoff_nodes=('n',), observables=('v(e)',)),
         'invalid_observable_mapping'),
        ('driven node', ['t', f'B1 e 0 V=5*(u(TIME-{T0})-u(TIME-{T0+Wd}))',
                         'R1 e 0 1k'],
         dict(t_stop=1e-3, windows=((400e-6, 600e-6),), tmax_coarse=1e-6,
              tmax_fine=10e-9, handoff_nodes=('e',),
              observables=('v(e)',)),
         'driven_node_initialization'),
        ('include', ['t', '.include /tmp/x.inc', 'V1 out 0 DC 1',
                     'R1 out 0 1k'],
         dict(t_stop=1e-3, windows=((200e-6, 400e-6),), tmax_coarse=1e-6,
              tmax_fine=10e-9, handoff_nodes=(), observables=('v(out)',)),
         'unsupported_included_content'),
        ('unknown bare-TIME', ['t', 'B1 o 0 V=TIME*TIME+V(in)',
                               'V1 in 0 DC 1', 'R1 in 0 1k'],
         dict(t_stop=1e-3, auto_derive=True, tmax_coarse=1e-6,
              tmax_fine=10e-9, handoff_nodes=(), observables=('v(o)',)),
         'unknown_event_timing'),
        ('unknown single-edge', ['t', 'B1 o 0 V=5*u(TIME-500u)',
                                 'R1 o 0 1k'],
         dict(t_stop=1e-3, auto_derive=True, tmax_coarse=1e-6,
              tmax_fine=10e-9, handoff_nodes=(), observables=('v(o)',)),
         'unknown_event_timing'),
    ]
    for name, net, kw, want in cases:
        auto = kw.pop('auto_derive', False)
        if kw.get('windows') is None and not auto:
            wins, tf, _, vd = W.derive_windows(net, kw['tmax_coarse'])
            assert vd == 'ok', (name, vd)
            kw = dict(kw, windows=tuple(wins), tmax_fine=tf)
        if auto:
            kw = dict(kw, windows=None)
        req = C.ContinuationRequest(netlist=tuple(net), **kw)
        got, pres = plan_code(req)
        check(f'taxonomy {name}', got == want, f"got={got} want={want}")
        if isinstance(pres, C.PlanRefused) and pres.refusals:
            r = pres.refusals[0]
            check(f'hint+gate {name}', bool(r.hint) and bool(r.code.value),
                  f"hint={r.hint[:40]!r} gate={r.gate!r}")


def t_seam_and_coverage_codes():
    r = C.classify_reason('G5: stitch jump 0.5 on v(n) at 0.0005')
    check('seam code', r.code == C.RefusalCode.SEAM_DISCONTINUITY, str(r.code))
    r = C.classify_reason(
        "G4: fine window violates EVENT-COVERAGE: ['EVENT-COVERAGE: dt_max=1e-6 > required=1e-08']")
    check('coverage code', r.code == C.RefusalCode.FINE_BOUND_EXCEEDED,
          str(r.code))


# ---- 3. bypass resistance --------------------------------------------------

def t_bypass():
    req = RC_REQ()
    pres = C.plan(req)
    assert isinstance(pres, C.PlanOk), pres
    plan = pres.plan

    fake = C.SegmentPlan(request=req, fingerprint='0' * 64, segments=(),
                         windows=(), auto_windows=False, bounds='{}',
                         capability='{}')
    out = C.execute(fake, req)
    check('manual plan refused',
          isinstance(out, C.ExecutionRefused) and
          out.refusals[0].code == C.RefusalCode.INVALID_PLAN,
          str(getattr(out, 'refusals', out))[:70])

    qnet = ['t', 'Vcc v 0 5', 'Rc v o 1k', 'Rb x b 10k',
            'Q1 o b 0 QM', '.model QM NPN(BF=100 TF=20n)']
    qreq = C.ContinuationRequest(
        netlist=tuple(qnet), t_stop=1e-3, windows=((200e-6, 400e-6),),
        tmax_coarse=1e-6, tmax_fine=10e-9, handoff_nodes=('o',),
        handoff_l=(), observables=('v(o)',))
    manual = C.SegmentPlan(request=qreq, fingerprint=C._fingerprint(qreq),
                           segments=((0.0, 200e-6, 1e-6, 'coarse'),
                                     (200e-6, 400e-6, 10e-9, 'fine'),
                                     (400e-6, 1e-3, 1e-6, 'coarse')),
                           windows=((200e-6, 400e-6),), auto_windows=False,
                           bounds='{}', capability='{}')
    out = C.execute(manual, qreq)
    check('manual plan cannot bypass classification',
          isinstance(out, C.ExecutionRefused) and
          out.refusals[0].code == C.RefusalCode.UNSUPPORTED_HIDDEN_STATE,
          str(getattr(out, 'refusals', out))[:70])

    req_explicit = replace(req, windows=tuple(pres.plan.windows))
    tampers = [
        ('dropped handoff', replace(req, handoff_nodes=()),
         'incomplete_state_handoff'),
        ('bad windows', replace(req, windows=((500.5e-6, 500.7e-6),)),
         'event_outside_window'),
        ('model mutated', replace(
            req, netlist=tuple(l.replace('10k', '100', 1) if i == 2 else l
                               for i, l in enumerate(req.netlist))),
         'coarse_bound_exceeded'),
        ('timestep changed', replace(req, tmax_fine=1e-6),
         'fine_bound_exceeded'),
        ('source retimed', replace(
            req_explicit, netlist=tuple(
                l.replace('TIME-0.0005', 'TIME-0.0006').replace(
                    'TIME-0.0005001', 'TIME-0.0006001') for l in req.netlist)),
         'event_outside_window'),
        ('include added', replace(
            req, netlist=tuple(list(req.netlist) + ['.include /tmp/x.inc'])),
         'unsupported_included_content'),
    ]
    for name, bad, want in tampers:
        out = C.execute(plan, bad)
        check(f'stale {name} refused',
              isinstance(out, C.ExecutionRefused) and
              out.refusals[0].code == C.RefusalCode.STALE_PLAN,
              str(getattr(out, 'refusals', out))[:80])
        got, _ = plan_code(bad)
        if want is not None:
            check(f'replan {name} names cause', got == want,
                  f"got={got} want={want}")
        else:
            check(f'replan {name} refuses', got != 'OK', f"got={got}")

    T0 = 198.69176531592202e-6
    rbase = ['t', 'L1 n1 0 1mH IC=0', 'C1 n1 0 1uF IC=5']
    rwin = ((2.5 * T0 - 1e-6, 2.5 * T0 + 1e-6),)
    rreq = C.ContinuationRequest(
        netlist=tuple(rbase), t_stop=5 * T0, windows=rwin,
        tmax_coarse=T0 / 50, tmax_fine=T0 / 50, handoff_nodes=('n1',),
        handoff_l=('L1',), observables=('v(n1)', 'i(l1)'),
        resonant_period=T0)
    rpres = C.plan(rreq)
    check('resonant plan with period ok', isinstance(rpres, C.PlanOk),
          str(getattr(rpres, 'refusals', ''))[:80])
    if isinstance(rpres, C.PlanOk):
        out = C.execute(rpres.plan, replace(rreq, resonant_period=None))
        check('resonant period dropped refused',
              isinstance(out, C.ExecutionRefused) and
              out.refusals[0].code == C.RefusalCode.STALE_PLAN,
              str(getattr(out, 'refusals', out))[:70])


def main():
    t_pass_rc()
    t_pass_ternary()
    t_taxonomy()
    t_seam_and_coverage_codes()
    t_bypass()
    print(f"\n{len(FAIL)} FAILURES: {FAIL}" if FAIL else "\nALL PASS")
    sys.exit(1 if FAIL else 0)


if __name__ == '__main__':
    main()
