"""Adversarial numerical-robustness benchmark for ngspice-47 batch sims. (rev2)

Each case runs contained subprocess sims and compares against an analytic or
strong reference. Output: logs/num_results.json + per-case raw outputs.

Rev2 fixes (adversarial self-review of rev1):
- C1: rev1 follower topology did not close the loop (B output referenced to its
  own input node); replaced with E-source non-inverting amp (correct topology).
- D6a: rev1 reference formula was wrong (sim was right); fixed to R-divider.
- D8L: rev1 reference was wrong (RL-to-ground output tends to 0, not 5V); fixed.
- C2: rev1 read V(out) directly in the B expression (algebraic loop); replaced
  with network-closed resistive-feedback Schmitt.
- A2: rev1's 500k points were output-density (TSTEP grid), not solver need;
  kept as cost note + added A2b coarse-TSTEP proving LTE step growth.
- Classes: added orthogonal `loud` flag (warnings/fatals present). "Detected"
  is not "reliable": garbage-with-warnings => failed, not reliable.
"""
import sys, os, json, math, time
sys.path.insert(0, os.path.dirname(__file__))
from helper_num import run_cir, tran_run, op_float, classify

NGSPICE_VER = os.popen("ngspice --version 2>&1 | head -2").read().strip()
print("ngspice:", NGSPICE_VER)

REC = []
def emit(r):
    r['loud'] = bool(r.get('warns')) or bool(r.get('fatals'))
    REC.append(r)
    print(f"{r['id']:8s} {r['class']:15s} conv={r['converged']} loud={r['loud']} "
          f"err={r.get('err_str','-')} wall={r.get('wall_ms',0):.0f}ms npts={r.get('npts','-')} "
          f"rej={r.get('rejected','-')} it/pt={r.get('iter_per_pt','-')} | {r['title']}")

def base_ctrl(base, vecs, timeout=30, tag="t"):
    r, s = tran_run(base, vecs, timeout=timeout, tag=tag)
    return r, s

def converged_of(r):
    if r.get('timeout') or r.get('rc') != 0:
        return False
    if r.get('wrdata_missing'):
        return False
    if any('timestep too small' in p for p in r.get('fatals', [])):
        return False
    if any('no convergence' in p for p in r.get('fatals', [])):
        return False
    return True

def maxabserr(series, ref_fn):
    errs = [abs(v - ref_fn(t)) for t, v in series]
    return max(errs) if errs else None

# ---------------- A: linear dynamics & stiffness ----------------

def case_A1():
    """Nominal RC step vs analytic."""
    tau, V0 = 1e-3, 5.0
    base = ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'C1 out 0 1u IC=0',
            '.tran 10u 5m UIC']
    r, s = base_ctrl(base, ['v(out)'])
    ref = lambda t: V0 * (1 - math.exp(-t / tau))
    err = maxabserr(s['v(out)'], ref)
    conv = converged_of(r) and len(s['v(out)']) > 10
    cls = classify(conv, err, 0.02, r)
    emit({'id': 'A1', 'title': 'RC nominal step vs analytic', 'converged': conv,
          'err': err, 'err_str': f'{err:.2e}' if err is not None else '-',
          'tol': 0.02, 'class': cls, 'wall_ms': r['wall_ms'], 'npts': r['npts'],
          'rejected': r.get('rejected'), 'iter_per_pt': r.get('iter_per_pt'),
          'dt_min': r.get('dt_min'), 'dt_max': r.get('dt_max'),
          'warns': r['warns'], 'fatals': r['fatals']})

def case_A2():
    """Stiff pair, dense output: cost of forcing TSTEP=100ns grid (output-driven)."""
    base = ['t', 'V1 in 0 DC 5',
            'R1 in f 1k', 'C1 f 0 1n IC=0',
            'R2 in s 1k', 'C2 s 0 10u IC=0',
            '.tran 100n 50m UIC']
    r, s = base_ctrl(base, ['v(f)', 'v(s)'], timeout=60)
    ef = maxabserr(s['v(f)'], lambda t: 5 * (1 - math.exp(-t / 1e-6))) if s['v(f)'] else None
    es = maxabserr(s['v(s)'], lambda t: 5 * (1 - math.exp(-t / 1e-2))) if s['v(s)'] else None
    err = max([e for e in (ef, es) if e is not None], default=None)
    conv = converged_of(r) and ef is not None
    cls = classify(conv, err, 0.05, r)
    emit({'id': 'A2', 'title': 'stiff pair ratio 1e4, dense output (cost note)',
          'converged': conv, 'err': err,
          'err_str': f'fast={ef:.2e} slow={es:.2e}' if ef is not None else '-',
          'tol': 0.05, 'class': cls, 'wall_ms': r['wall_ms'], 'npts': r['npts'],
          'rejected': r.get('rejected'), 'iter_per_pt': r.get('iter_per_pt'),
          'dt_min': r.get('dt_min'), 'dt_max': r.get('dt_max'),
          'dt_ratio': r.get('dt_ratio'), 'warns': r['warns'], 'fatals': r['fatals']})

def case_A2b():
    """Same stiff pair, coarse TSTEP: LTE must grow steps (solver handles stiffness)."""
    base = ['t', 'V1 in 0 DC 5',
            'R1 in f 1k', 'C1 f 0 1n IC=0',
            'R2 in s 1k', 'C2 s 0 10u IC=0',
            '.tran 1m 50m UIC']
    r, s = base_ctrl(base, ['v(f)', 'v(s)'], timeout=30)
    es = maxabserr(s['v(s)'], lambda t: 5 * (1 - math.exp(-t / 1e-2))) if s['v(s)'] else None
    conv = converged_of(r) and es is not None
    grown = (r.get('dt_ratio') or 0) > 100
    cls = classify(conv and grown, es, 0.05, r)
    emit({'id': 'A2b', 'title': 'stiff pair coarse output: LTE step growth',
          'converged': conv, 'err': es,
          'err_str': f'slowerr={es:.2e} dtratio={r.get("dt_ratio"):.1f}' if es is not None else '-',
          'tol': 0.05, 'class': cls, 'wall_ms': r['wall_ms'], 'npts': r['npts'],
          'rejected': r.get('rejected'), 'iter_per_pt': r.get('iter_per_pt'),
          'dt_min': r.get('dt_min'), 'dt_max': r.get('dt_max'),
          'dt_ratio': r.get('dt_ratio'), 'warns': r['warns'], 'fatals': r['fatals']})

def case_A3():
    """Fast-on-slow two-tone through RC vs per-tone analytic (steady state)."""
    base = ['t', 'B1 in 0 V=5*sin(2*3.14159265*100*TIME)+0.05*sin(2*3.14159265*100e3*TIME)',
            'R1 in out 1k', 'C1 out 0 1n IC=0', '.tran 100n 30m UIC']
    r, s = base_ctrl(base, ['v(out)'], timeout=60)
    def H(f):
        w = 2 * math.pi * f
        return 1 / complex(1, w * 1e3 * 1e-9)
    tail = [(t, v) for t, v in s['v(out)'] if t > 29e-3]
    def ref(t):
        a1, p1 = abs(H(100)), math.atan2(H(100).imag, H(100).real)
        a2, p2 = abs(H(100e3)), math.atan2(H(100e3).imag, H(100e3).real)
        return 5 * a1 * math.sin(2 * math.pi * 100 * t + p1) + 0.05 * a2 * math.sin(2 * math.pi * 100e3 * t + p2)
    err = maxabserr(tail, ref) if tail else None
    conv = converged_of(r) and len(tail) > 50
    cls = classify(conv, err, 0.05, r)
    emit({'id': 'A3', 'title': 'two-tone fast-on-slow vs analytic', 'converged': conv,
          'err': err, 'err_str': f'{err:.2e}' if err is not None else '-', 'tol': 0.05,
          'class': cls, 'wall_ms': r['wall_ms'], 'npts': r['npts'],
          'rejected': r.get('rejected'), 'iter_per_pt': r.get('iter_per_pt'),
          'dt_min': r.get('dt_min'), 'dt_max': r.get('dt_max'),
          'warns': r['warns'], 'fatals': r['fatals']})

def case_A4():
    """LC tank free oscillation: frequency + amplitude stability."""
    L, C = 1e-3, 1e-6
    f0 = 1 / (2 * math.pi * math.sqrt(L * C))
    base = ['t', 'L1 out 0 1mH IC=0', 'C1 out 0 1uF IC=5',
            '.tran 1u 2m UIC']
    r, s = base_ctrl(base, ['v(out)'], timeout=30)
    data = s['v(out)']
    cross = []
    for (t0, v0), (t1, v1) in zip(data, data[1:]):
        if v0 < 0 <= v1 and t1 > t0:
            cross.append(t0 - v0 * (t1 - t0) / (v1 - v0))
    periods = [b - a for a, b in zip(cross, cross[1:])]
    fmeas = 1 / (sum(periods) / len(periods)) if len(periods) >= 3 else None
    ferr = abs(fmeas - f0) / f0 if fmeas else None
    peak0 = max((abs(v) for t, v in data if t < 0.3e-3), default=None)
    late = [abs(v) for t, v in data if t > 1.5e-3]
    peakL = max(late) if late else None
    decay = abs(peakL - peak0) / peak0 if peak0 and peakL else None
    conv = converged_of(r) and fmeas is not None
    cls = classify(conv, ferr, 0.01, r)
    emit({'id': 'A4', 'title': 'LC tank f0 + amplitude stability', 'converged': conv,
          'err': ferr, 'err_str': f'ferr={ferr:.2e} decay={decay:.2e}' if ferr is not None else '-',
          'tol': 0.01, 'class': cls, 'wall_ms': r['wall_ms'], 'npts': r['npts'],
          'rejected': r.get('rejected'), 'iter_per_pt': r.get('iter_per_pt'),
          'f0': f0, 'fmeas': fmeas, 'warns': r['warns'], 'fatals': r['fatals']})

def case_A5():
    """1MHz sine through RC lowpass: steady-state amplitude vs |H|."""
    f = 1e6
    w = 2 * math.pi * f
    mag = 1 / math.sqrt(1 + (w * 1e3 * 1e-9) ** 2)
    base = ['t', 'V1 in 0 SIN(0 5 1MEG)', 'R1 in out 1k', 'C1 out 0 1n IC=0',
            '.tran 5n 10u UIC']
    r, s = base_ctrl(base, ['v(out)'], timeout=30)
    tail = [v for t, v in s['v(out)'] if t > 8e-6]
    amp = (max(tail) - min(tail)) / 2 if len(tail) > 10 else None
    expect = 5 * mag
    err = abs(amp - expect) / expect if amp else None
    conv = converged_of(r) and amp is not None
    cls = classify(conv, err, 0.05, r)
    emit({'id': 'A5', 'title': '1MHz sine amplitude vs |H|', 'converged': conv,
          'err': err, 'err_str': f'amp={amp:.4f} exp={expect:.4f}' if amp else '-',
          'tol': 0.05, 'class': cls, 'wall_ms': r['wall_ms'], 'npts': r['npts'],
          'rejected': r.get('rejected'), 'iter_per_pt': r.get('iter_per_pt'),
          'warns': r['warns'], 'fatals': r['fatals']})

def case_A6():
    """Long transient: RC run to 100 tau, final-value drift."""
    base = ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'C1 out 0 1u IC=0',
            '.tran 100u 100m UIC']
    r, s = base_ctrl(base, ['v(out)'], timeout=60)
    final = s['v(out)'][-1][1] if s['v(out)'] else None
    err = abs(final - 5.0) / 5.0 if final is not None else None
    conv = converged_of(r) and final is not None
    cls = classify(conv, err, 1e-3, r)
    emit({'id': 'A6', 'title': 'RC to 100tau final drift', 'converged': conv,
          'err': err, 'err_str': f'final={final:.6f}' if final is not None else '-',
          'tol': 1e-3, 'class': cls, 'wall_ms': r['wall_ms'], 'npts': r['npts'],
          'rejected': r.get('rejected'), 'iter_per_pt': r.get('iter_per_pt'),
          'warns': r['warns'], 'fatals': r['fatals']})

# ---------------- B: sharp / hard nonlinear ----------------

def case_B1():
    """1ps PWL edge into RC: overshoot/ringing check."""
    base = ['t', 'V1 in 0 PWL(0 0 1m 0 1.000001m 5 5m 5)', 'R1 in out 1k',
            'C1 out 0 1u IC=0', '.tran 1u 3m UIC']
    r, s = base_ctrl(base, ['v(out)'], timeout=30)
    vals = [v for _, v in s['v(out)']]
    over = max(vals) - 5.0 if vals else None
    under = min(vals) if vals else None
    err = max(over if over and over > 0 else 0, -under if under and under < 0 else 0)
    conv = converged_of(r) and len(vals) > 10
    cls = classify(conv, err, 0.05, r)
    emit({'id': 'B1', 'title': '1ps PWL edge: overshoot/ring', 'converged': conv,
          'err': err, 'err_str': f'over={over:.2e} min={under:.2e}' if vals else '-',
          'tol': 0.05, 'class': cls, 'wall_ms': r['wall_ms'], 'npts': r['npts'],
          'rejected': r.get('rejected'), 'iter_per_pt': r.get('iter_per_pt'),
          'dt_min': r.get('dt_min'), 'warns': r['warns'], 'fatals': r['fatals']})

def case_B2():
    """Narrow B-source blip (no breakpoints): silent-miss test."""
    base = ['t', 'B1 in 0 V=5*(u(TIME-500u)-u(TIME-500.01u))', 'R1 in 0 1k',
            'R2 in out 1k', 'C1 out 0 1n IC=0', '.tran 1u 1m UIC']
    r, s = base_ctrl(base, ['v(in)', 'v(out)'], timeout=30)
    peak = max((v for _, v in s['v(in)']), default=None)
    err = (5.0 - peak) / 5.0 if peak is not None else None
    conv = converged_of(r) and peak is not None
    cls = classify(conv, err, 0.5, r)
    emit({'id': 'B2', 'title': '10ns B-blip miss test (unbounded)', 'converged': conv,
          'err': err, 'err_str': f'peak={peak:.4f}/5V dtmax={r.get("dt_max"):.2e}' if peak is not None else '-',
          'tol': 0.5, 'class': cls, 'wall_ms': r['wall_ms'], 'npts': r['npts'],
          'rejected': r.get('rejected'), 'iter_per_pt': r.get('iter_per_pt'),
          'dt_max': r.get('dt_max'), 'warns': r['warns'], 'fatals': r['fatals']})

def case_B2b():
    """Same blip with tmax bound (mitigation: bound fixes the miss)."""
    base = ['t', 'B1 in 0 V=5*(u(TIME-500u)-u(TIME-500.01u))', 'R1 in 0 1k',
            'R2 in out 1k', 'C1 out 0 1n IC=0', '.tran 1u 1m 0 5n UIC']
    r, s = base_ctrl(base, ['v(in)', 'v(out)'], timeout=60)
    peak = max((v for _, v in s['v(in)']), default=None)
    err = (5.0 - peak) / 5.0 if peak is not None else None
    conv = converged_of(r) and peak is not None
    cls = classify(conv, err, 0.5, r)
    emit({'id': 'B2b', 'title': '10ns B-blip with tmax=5n (mitigation)', 'converged': conv,
          'err': err, 'err_str': f'peak={peak:.4f}/5V' if peak is not None else '-',
          'tol': 0.5, 'class': cls, 'wall_ms': r['wall_ms'], 'npts': r['npts'],
          'rejected': r.get('rejected'), 'iter_per_pt': r.get('iter_per_pt'),
          'warns': r['warns'], 'fatals': r['fatals']})

def case_B3():
    """Comparator on slow ramp: single clean switch at 2.5V."""
    base = ['t', 'V1 ramp 0 PWL(0 0 2m 5)', 'B1 out 0 V=(V(ramp)>2.5)?5:0',
            'R1 out 0 1k', '.tran 5u 2m']
    r, s = base_ctrl(base, ['v(out)', 'v(ramp)'], timeout=30)
    data = s['v(out)']
    trans = sum(1 for (_, a), (_, b) in zip(data, data[1:])
                if (a < 2.5) != (b < 2.5))
    tsw = next((t for t, v in data if v > 2.5), None)
    v_at_sw = None
    if tsw is not None and s['v(ramp)']:
        v_at_sw = min(s['v(ramp)'], key=lambda tv: abs(tv[0] - tsw))[1]
    err = abs((v_at_sw or 0) - 2.5) / 2.5 if v_at_sw is not None else None
    conv = converged_of(r) and trans >= 1
    chatter = trans - 1
    cls = 'questionable' if (conv and chatter > 2) else classify(conv, err, 0.05, r)
    emit({'id': 'B3', 'title': 'comparator ramp: threshold+chatter', 'converged': conv,
          'err': err, 'err_str': f'trans={trans} verr={err:.2e}' if err is not None else f'trans={trans}',
          'tol': 0.05, 'class': cls, 'wall_ms': r['wall_ms'], 'npts': r['npts'],
          'rejected': r.get('rejected'), 'iter_per_pt': r.get('iter_per_pt'),
          'warns': r['warns'], 'fatals': r['fatals']})

def case_B4():
    """ddt() of a step: spike must be finite, sim must converge."""
    base = ['t', 'B1 out 0 V=ddt(u(TIME-1m)*5)', 'R1 out 0 1k',
            '.tran 10u 2m UIC']
    r, s = base_ctrl(base, ['v(out)'], timeout=30)
    vals = [v for _, v in s['v(out)']]
    peak = max((abs(v) for v in vals), default=None)
    bad = any(not math.isfinite(v) for v in vals)
    conv = converged_of(r) and not bad and peak is not None
    # Peak magnitude is 5V/dt (verified 5e5/5e6/5e7 at tmax 10u/1u/100n):
    # converges and conserves impulse area, but the number is a pure
    # timestep artifact around a discontinuity -> questionable, not reliable.
    cls = 'questionable' if conv else 'failed'
    emit({'id': 'B4', 'title': 'ddt(step): finite spike + converge', 'converged': conv,
          'err': None, 'err_str': f'peak={peak:.2e}' if peak is not None else '-',
          'tol': None, 'class': cls, 'wall_ms': r['wall_ms'], 'npts': r['npts'],
          'rejected': r.get('rejected'), 'iter_per_pt': r.get('iter_per_pt'),
          'warns': r['warns'], 'fatals': r['fatals'], 'nonfinite': bad})

def case_B5():
    """Diode clipper DC sweep vs bisection reference."""
    def shockley(vs, R=1000.0, Is=1e-14, nvt=0.038775):
        f = lambda vd: (vs - vd) / R - Is * (math.exp(vd / nvt) - 1)
        lo, hi = 0.0, vs
        for _ in range(200):
            mid = (lo + hi) / 2
            if f(mid) > 0:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2
    worst, walls, ok = 0.0, [], True
    for vs in (1.0, 3.0, 5.0):
        net = ['t', f'V1 in 0 DC {vs}', 'R1 in out 1k', 'D1 out 0 DLED',
               '.model DLED D(IS=1e-14 N=1.5)', '.op',
               '.control', 'run', 'print v(out)', '.endc', '.end']
        rr = run_cir(net, timeout=20)
        walls.append(rr['wall_ms'])
        v = op_float(rr['out'])
        if v is None:
            ok = False
            worst = None
            break
        worst = max(worst, abs(v - shockley(vs)))
    cls = classify(ok, worst, 1e-3, None)
    emit({'id': 'B5', 'title': 'diode DC sweep vs bisection', 'converged': ok,
          'err': worst, 'err_str': f'maxerr={worst:.2e}' if worst is not None else 'NO-VALUE',
          'tol': 1e-3, 'class': cls, 'wall_ms': sum(walls) / len(walls),
          'npts': '-', 'rejected': '-', 'iter_per_pt': '-', 'warns': [], 'fatals': []})

def case_B6():
    """tanh saturation DC vs math reference."""
    net = ['t', 'V1 in 0 DC 5', 'R1 in 0 1k', 'B1 out 0 V=10*tanh(V(in)/2)',
           'R2 out 0 1k', '.op', '.control', 'run', 'print v(out)', '.endc', '.end']
    rr = run_cir(net, timeout=20)
    v = op_float(rr['out'])
    ref = 10 * math.tanh(5 / 2)
    err = abs((v or 0) - ref) if v is not None else None
    conv = v is not None and rr['rc'] == 0
    cls = classify(conv, err, 1e-6, None)
    emit({'id': 'B6', 'title': 'tanh DC vs math', 'converged': conv,
          'err': err, 'err_str': f'{err:.2e}' if err is not None else 'NO-VALUE',
          'tol': 1e-6, 'class': cls, 'wall_ms': rr['wall_ms'],
          'npts': '-', 'rejected': '-', 'iter_per_pt': '-',
          'warns': rr['warns'], 'fatals': rr['fatals']})

# ---------------- C: feedback ----------------

def case_C1():
    """E-source non-inverting amp gain=2 (native linear feedback, positive control)."""
    net = ['t', 'V1 inp 0 DC 2.5', 'E1 out 0 inp fb 100000',
           'R1 out fb 1k', 'R2 fb 0 1k',
           '.op', '.control', 'run', 'print v(out)', '.endc', '.end']
    rr = run_cir(net, timeout=20)
    v = op_float(rr['out'])
    expect = 2 * 2.5 * (100000 / (100000 + 2))  # finite-gain correction approx
    expect = 5.0
    err = abs((v or 0) - expect) / expect if v is not None else None
    conv = v is not None and rr['rc'] == 0 and not rr['fatals']
    cls = classify(conv, err, 1e-3, rr)
    emit({'id': 'C1', 'title': 'E-source amp gain=2 closed loop', 'converged': conv,
          'err': err, 'err_str': f'v={v} exp~{expect}',
          'tol': 1e-3, 'class': cls, 'wall_ms': rr['wall_ms'],
          'npts': '-', 'rejected': '-', 'iter_per_pt': rr.get('total_iter'),
          'warns': rr['warns'], 'fatals': rr['fatals']})

def case_C2():
    """Resistive-feedback Schmitt (loop closed through network, no direct self-read)."""
    net = ['t', 'V1 in 0 PWL(0 0 1m 0 1.01m 5 2m 5 2.01m 0 3m 0)',
           'R1 in p 10k', 'R2 out p 30k', 'C1 p 0 100p IC=0',
           'B1 out 0 V=(V(p)>2.5)?5:0', 'R3 out 0 1k',
           '.tran 5u 3m UIC']
    r, s = base_ctrl(net, ['v(out)', 'v(p)'], timeout=30)
    data = s['v(out)']
    hi = max((v for _, v in data), default=None)
    lo = min((v for _, v in data), default=None)
    sw = sum(1 for (_, a), (_, b) in zip(data, data[1:]) if (a < 2.5) != (b < 2.5))
    conv = converged_of(r) and hi is not None
    ok_levels = hi is not None and hi > 4.5 and lo < 0.5 and sw >= 2
    cls = classify(conv and ok_levels, 0.0 if ok_levels else 1.0, 0.5, r)
    emit({'id': 'C2', 'title': 'Schmitt network-feedback hysteresis', 'converged': conv,
          'err': None, 'err_str': f'hi={hi:.2f} lo={lo:.2f} switches={sw}' if hi is not None else '-',
          'tol': None, 'class': cls, 'wall_ms': r['wall_ms'], 'npts': r['npts'],
          'rejected': r.get('rejected'), 'iter_per_pt': r.get('iter_per_pt'),
          'warns': r['warns'], 'fatals': r['fatals']})

def case_C3():
    """Cross-coupled latch without seeding: must fail LOUDLY, never silently."""
    net = ['t', 'B1 o1 0 V=(V(o2)>2.5)?0:5', 'R1 o1 0 1k',
           'B2 o2 0 V=(V(o1)>2.5)?0:5', 'R2 o2 0 1k',
           '.op', '.control', 'run', 'print v(o1) v(o2)', '.endc', '.end']
    rr = run_cir(net, timeout=20)
    v1, v2 = op_float(rr['out'], 'v(o1)'), op_float(rr['out'], 'v(o2)')
    loud = bool(rr['warns'] or rr['fatals'] or rr['rc'] != 0 or v1 is None)
    cls = 'failed' if loud else 'silent-failure'
    emit({'id': 'C3', 'title': 'unseeded latch: loud-vs-silent', 'converged': False,
          'err': None, 'err_str': f'v1={v1} v2={v2} warns={len(rr["warns"])} fatals={rr["fatals"]}',
          'tol': None, 'class': cls, 'wall_ms': rr['wall_ms'], 'npts': '-',
          'rejected': '-', 'iter_per_pt': '-', 'warns': rr['warns'][:4], 'fatals': rr['fatals']})

def case_C3b():
    """Same latch WITH .ic seeds: bistability, both states reachable + stable."""
    states = []
    ok = True
    for ic1, ic2 in (('5', '0'), ('0', '5')):
        net = ['t',
               'B1 o1 0 V=(V(o2)>2.5)?0:5', 'R1 o1 0 1k', 'C1 o1 0 1n',
               'B2 o2 0 V=(V(o1)>2.5)?0:5', 'R2 o2 0 1k', 'C2 o2 0 1n',
               f'.ic v(o1)={ic1} v(o2)={ic2}',
               '.tran 10u 1m UIC']
        r, s = base_ctrl(net, ['v(o1)', 'v(o2)'], timeout=30)
        if not s['v(o1)']:
            ok = False
            states.append(None)
            continue
        f1, f2 = s['v(o1)'][-1][1], s['v(o2)'][-1][1]
        states.append((round(f1, 3), round(f2, 3)))
    good = (ok and states[0] is not None and states[1] is not None
            and abs(states[0][0] - 5) < 0.5 and abs(states[0][1]) < 0.5
            and abs(states[1][0]) < 0.5 and abs(states[1][1] - 5) < 0.5)
    cls = 'reliable' if good else 'failed'
    emit({'id': 'C3b', 'title': 'seeded latch bistability', 'converged': good,
          'err': None, 'err_str': f'states={states}', 'tol': None, 'class': cls,
          'wall_ms': 0, 'npts': '-', 'rejected': '-', 'iter_per_pt': '-',
          'warns': [], 'fatals': []})

def case_C4():
    """Self-referential B-source V=V(out)+1: has NO solution; garbage must be LOUD."""
    net = ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'B1 out 0 V=V(out)+1',
           'R2 out 0 1k', '.op', '.control', 'run', 'print v(out)', '.endc', '.end']
    rr = run_cir(net, timeout=20)
    v = op_float(rr['out'], 'v(out)')
    loud = bool(rr['warns'] or rr['fatals'])
    garbage = v is not None and abs(v) > 1e6
    cls = 'failed' if (loud and garbage) else ('silent-failure' if garbage else 'questionable')
    emit({'id': 'C4', 'title': 'self-ref B (no solution) regression', 'converged': False,
          'err': None, 'err_str': f'v(out)={v} fatals={rr["fatals"]}',
          'tol': None, 'class': cls, 'wall_ms': rr['wall_ms'],
          'npts': '-', 'rejected': '-', 'iter_per_pt': '-',
          'warns': rr['warns'][:4], 'fatals': rr['fatals']})

def case_C5():
    """Positive feedback without hysteresis (rail runaway): must rail, not NaN."""
    net = ['t', 'V1 inp 0 DC 2.51', 'V2 inm 0 DC 2.5',
           'B1 out inm V=1e4*(V(inp)-V(inm))+V(out)', 'R1 out 0 1k',
           '.op', '.control', 'run', 'print v(out)', '.endc', '.end']
    rr = run_cir(net, timeout=20)
    v = op_float(rr['out'], 'v(out)')
    finite = v is not None and math.isfinite(v)
    loud = bool(rr['warns'] or rr['fatals'])
    bounded = finite and abs(v) < 1e6
    cls = 'reliable' if (bounded and not loud) else ('failed' if loud else 'questionable')
    emit({'id': 'C5', 'title': 'pos-feedback unbounded rail check', 'converged': finite,
          'err': None, 'err_str': f'v(out)={v}', 'tol': None, 'class': cls,
          'wall_ms': rr['wall_ms'], 'npts': '-', 'rejected': '-',
          'iter_per_pt': '-', 'warns': rr['warns'][:4], 'fatals': rr['fatals']})

# ---------------- D: singular / extreme ----------------

def case_D1():
    """Floating node pair: must warn (loud), must not silently invent 0."""
    net = ['t', 'V1 in 0 DC 5', 'R1 in mid 1k', 'Rfloat n1 n2 1k',
           '.op', '.control', 'run', 'print v(mid) v(n1)', '.endc', '.end']
    rr = run_cir(net, timeout=20)
    loud = bool(rr['warns'] or rr['fatals'])
    cls = 'failed' if loud else 'silent-failure'
    emit({'id': 'D1', 'title': 'floating node loudness', 'converged': False,
          'err': None, 'err_str': f'warns={len(rr["warns"])} fatals={rr["fatals"]} rc={rr["rc"]}',
          'tol': None, 'class': cls, 'wall_ms': rr['wall_ms'],
          'npts': '-', 'rejected': '-', 'iter_per_pt': '-',
          'warns': rr['warns'][:4], 'fatals': rr['fatals']})

def case_D2():
    """Weak 1T tie to ground on driven node: accuracy vs analytic."""
    net = ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'R2 out 0 1T',
           '.op', '.control', 'run', 'print v(out)', '.endc', '.end']
    rr = run_cir(net, timeout=20)
    v = op_float(rr['out'])
    ref = 5 * 1e12 / (1e3 + 1e12)
    err = abs((v or 0) - ref) if v is not None else None
    conv = v is not None and rr['rc'] == 0
    cls = classify(conv, err, 1e-6, rr)
    emit({'id': 'D2', 'title': 'weak-tie node accuracy', 'converged': conv,
          'err': err, 'err_str': f'v={v} ref={ref:.9f}' if v is not None else 'NO-VALUE',
          'tol': 1e-6, 'class': cls, 'wall_ms': rr['wall_ms'],
          'npts': '-', 'rejected': '-', 'iter_per_pt': '-',
          'warns': rr['warns'], 'fatals': rr['fatals']})

def case_D3():
    """Parallel opposing voltage sources: singular must be loud."""
    net = ['t', 'V1 out 0 DC 5', 'V2 out 0 DC 4',
           '.op', '.control', 'run', 'print v(out)', '.endc', '.end']
    rr = run_cir(net, timeout=20)
    loud = bool(rr['warns'] or rr['fatals'] or rr['rc'] != 0)
    cls = 'failed' if loud else 'silent-failure'
    emit({'id': 'D3', 'title': 'V-loop singular loudness', 'converged': False,
          'err': None, 'err_str': f'rc={rr["rc"]} fatals={rr["fatals"]} warns={len(rr["warns"])}',
          'tol': None, 'class': cls, 'wall_ms': rr['wall_ms'],
          'npts': '-', 'rejected': '-', 'iter_per_pt': '-',
          'warns': rr['warns'][:4], 'fatals': rr['fatals']})

def case_D4():
    """Current source into open: value is arbitrary (1ohm x I); silence is the finding."""
    vals = {}
    for tag, iso in (('1mA', '1m'), ('1A', '1'), ('1uA', '1u')):
        net = ['t', f'I1 0 out DC {iso}', '.op',
               '.control', 'run', 'print v(out)', '.endc', '.end']
        rr = run_cir(net, timeout=20)
        vals[tag] = (op_float(rr['out'], 'v(out)'), rr['rc'], len(rr['warns']), rr['fatals'])
    silent = all(v is not None and rc == 0 and nw == 0 and not fl
                 for v, rc, nw, fl in vals.values())
    linear1ohm = (vals['1mA'][0] is not None and abs(vals['1mA'][0] - 1e-3) < 1e-9
                  and abs(vals['1A'][0] - 1.0) < 1e-9 and abs(vals['1uA'][0] - 1e-6) < 1e-12)
    cls = 'silent-failure' if (silent and linear1ohm) else 'questionable'
    emit({'id': 'D4', 'title': 'isrc-into-open silent 1ohm artifact', 'converged': True,
          'err': None, 'err_str': f'v={ {k: v[0] for k, v in vals.items()} } silent={silent} 1ohm={linear1ohm}',
          'tol': None, 'class': cls, 'wall_ms': 0, 'npts': '-',
          'rejected': '-', 'iter_per_pt': '-', 'warns': [], 'fatals': []})

def case_D5():
    """Capacitor loop with no DC path (.op): must fail loudly or be sane."""
    net = ['t', 'V1 in 0 DC 5', 'C1 in mid 1u', 'C2 mid out 1u', 'R1 out 0 1k',
           '.op', '.control', 'run', 'print v(mid) v(out)', '.endc', '.end']
    rr = run_cir(net, timeout=20)
    vm = op_float(rr['out'], 'v(mid)')
    loud = bool(rr['warns'] or rr['fatals'])
    cls = 'failed' if (rr['rc'] == 0 and loud) else ('silent-failure' if rr['rc'] == 0 else 'failed')
    if rr['rc'] == 0 and not loud and vm is not None:
        cls = 'reliable'
    emit({'id': 'D5', 'title': 'cap-loop DC path loudness', 'converged': False,
          'err': None, 'err_str': f'v(mid)={vm} warns={len(rr["warns"])} fatals={rr["fatals"]}',
          'tol': None, 'class': cls, 'wall_ms': rr['wall_ms'],
          'npts': '-', 'rejected': '-', 'iter_per_pt': '-',
          'warns': rr['warns'][:4], 'fatals': rr['fatals']})

def case_D5b():
    """D5 under gmin 1e-9: value stability must NOT be mistaken for correctness."""
    net = ['t', '.options gmin=1e-9', 'V1 in 0 DC 5', 'C1 in mid 1u',
           'C2 mid out 1u', 'R1 out 0 1k',
           '.op', '.control', 'run', 'print v(mid)', '.endc', '.end']
    rr = run_cir(net, timeout=20)
    vm = op_float(rr['out'], 'v(mid)')
    same = vm is not None and abs(vm - 4.950497) < 1e-3
    cls = 'failed' if (same and rr['warns']) else 'questionable'
    emit({'id': 'D5b', 'title': 'cap-loop gmin shift determinism-trap', 'converged': False,
          'err': None, 'err_str': f'v(mid)={vm} same-as-default={same}',
          'tol': None, 'class': cls, 'wall_ms': rr['wall_ms'],
          'npts': '-', 'rejected': '-', 'iter_per_pt': '-',
          'warns': rr['warns'][:2], 'fatals': rr['fatals']})

def case_D6():
    """Abstol boundary: uV divider output (1T/1k -> 5uV) and near-rail (1k/1T)."""
    for k, r1, r2 in (('a', '1k', '1T'), ('b', '1T', '1k')):
        R1 = 1e3 if r1 == '1k' else 1e12
        R2 = 1e3 if r2 == '1k' else 1e12
        ref = 5 * R2 / (R1 + R2)
        net = ['t', 'V1 in 0 DC 5', f'R1 in out {r1}', f'R2 out 0 {r2}',
               '.op', '.control', 'run', 'print v(out)', '.endc', '.end']
        rr = run_cir(net, timeout=20)
        v = op_float(rr['out'])
        err = abs((v or 0) - ref) if v is not None else None
        tol = max(abs(ref) * 1e-6, 1e-12)
        conv = v is not None and rr['rc'] == 0
        cls = classify(conv, err, tol, rr)
        emit({'id': f'D6{k}', 'title': f'dynamic-range divider {r1}/{r2}',
              'converged': conv, 'err': err,
              'err_str': f'v={v} ref={ref:.6e}', 'tol': tol,
              'class': cls, 'wall_ms': rr['wall_ms'], 'npts': '-',
              'rejected': '-', 'iter_per_pt': '-', 'warns': rr['warns'], 'fatals': rr['fatals']})

def case_D7():
    """10kV divider: large-signal handling."""
    net = ['t', 'V1 in 0 DC 10k', 'R1 in out 1k', 'R2 out 0 1k',
           '.op', '.control', 'run', 'print v(out)', '.endc', '.end']
    rr = run_cir(net, timeout=20)
    v = op_float(rr['out'])
    err = abs((v or 0) - 5000.0) if v is not None else None
    conv = v is not None and rr['rc'] == 0
    cls = classify(conv, err, 1.0, rr)
    emit({'id': 'D7', 'title': '10kV divider', 'converged': conv,
          'err': err, 'err_str': f'v={v} ref=5000' if v is not None else 'NO-VALUE',
          'tol': 1.0, 'class': cls, 'wall_ms': rr['wall_ms'],
          'npts': '-', 'rejected': '-', 'iter_per_pt': '-',
          'warns': rr['warns'], 'fatals': rr['fatals']})

def case_D8():
    """Extreme L and C values with tau=1s: corrected references."""
    specs = (('L', ['V1 in 0 DC 5', 'R1 in out 10', 'L1 out 0 10H IC=0',
                     '.tran 10m 5s UIC'], lambda: 5 * math.exp(-5.0)),
             ('C', ['V1 in 0 DC 5', 'R1 in out 1', 'C1 out 0 1F IC=0',
                     '.tran 10m 5s UIC'], lambda: 5 * (1 - math.exp(-5.0))))
    for k, extra, reffn in specs:
        base = ['t'] + extra
        ref = reffn()
        r, s = base_ctrl(base, ['v(out)'], timeout=60)
        final = s['v(out)'][-1][1] if s['v(out)'] else None
        err = abs((final or 0) - ref) if final is not None else None
        conv = converged_of(r) and final is not None
        cls = classify(conv, err, 0.05, r)
        emit({'id': f'D8{k}', 'title': f'extreme {"inductor" if k=="L" else "capacitor"} tau=1s',
              'converged': conv, 'err': err,
              'err_str': f'final={final:.4f} ref={ref:.4f}' if final is not None else '-',
              'tol': 0.05, 'class': cls, 'wall_ms': r['wall_ms'], 'npts': r['npts'],
              'rejected': r.get('rejected'), 'iter_per_pt': r.get('iter_per_pt'),
              'warns': r['warns'], 'fatals': r['fatals']})

# ---------------- E: tolerance / determinism ----------------

def case_E1():
    """Stiff pair (A2) under reltol 1e-2 / 1e-3 / 1e-6: sensitivity."""
    for rel in ('1e-2', '1e-3', '1e-6'):
        base = ['t', f'.options reltol={rel}', 'V1 in 0 DC 5',
                'R1 in f 1k', 'C1 f 0 1n IC=0',
                'R2 in s 1k', 'C2 s 0 10u IC=0',
                '.tran 100n 50m UIC']
        r, s = base_ctrl(base, ['v(f)', 'v(s)'], timeout=60)
        ef = maxabserr(s['v(f)'], lambda t: 5 * (1 - math.exp(-t / 1e-6))) if s['v(f)'] else None
        es = maxabserr(s['v(s)'], lambda t: 5 * (1 - math.exp(-t / 1e-2))) if s['v(s)'] else None
        err = max([e for e in (ef, es) if e is not None], default=None)
        conv = converged_of(r) and ef is not None
        cls = classify(conv, err, 0.05, r)
        emit({'id': f'E1-{rel}', 'title': f'stiff pair reltol={rel}', 'converged': conv,
              'err': err, 'err_str': f'fast={ef:.2e} slow={es:.2e}' if ef is not None else '-',
              'tol': 0.05, 'class': cls, 'wall_ms': r['wall_ms'], 'npts': r['npts'],
              'rejected': r.get('rejected'), 'iter_per_pt': r.get('iter_per_pt'),
              'dt_min': r.get('dt_min'), 'warns': r['warns'], 'fatals': r['fatals']})

def case_E2():
    """Parameter sweep determinism: 5 R values, analytic each, repeat 2x."""
    worst, rep_spread, ok = 0.0, 0.0, True
    for rv in (100, 1e3, 1e4, 1e5, 1e6):
        vals = []
        for _ in range(2):
            net = ['t', 'V1 in 0 DC 5', f'R1 in out {rv}', 'R2 out 0 1k',
                   '.op', '.control', 'run', 'print v(out)', '.endc', '.end']
            rr = run_cir(net, timeout=20)
            v = op_float(rr['out'])
            if v is None:
                ok = False
            else:
                vals.append(v)
        ref = 5 * 1e3 / (rv + 1e3)
        if len(vals) == 2:
            worst = max(worst, max(abs(a - ref) for a in vals))
            rep_spread = max(rep_spread, abs(vals[0] - vals[1]))
    cls = classify(ok, worst, 1e-6, None)
    emit({'id': 'E2', 'title': 'R-sweep 100..1M analytic+repeat', 'converged': ok,
          'err': worst, 'err_str': f'maxerr={worst:.2e} spread={rep_spread:.2e}',
          'tol': 1e-6, 'class': cls, 'wall_ms': 0, 'npts': '-',
          'rejected': '-', 'iter_per_pt': '-', 'warns': [], 'fatals': []})

def case_E3():
    """Repeat determinism on transient (A1 x3): final-value spread."""
    finals, rej = [], []
    for _ in range(3):
        base = ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'C1 out 0 1u IC=0',
                '.tran 10u 5m UIC']
        r, s = base_ctrl(base, ['v(out)'], timeout=30)
        if s['v(out)']:
            finals.append(s['v(out)'][-1][1])
            rej.append(r.get('rejected'))
    spread = max(finals) - min(finals) if len(finals) == 3 else None
    conv = len(finals) == 3
    cls = 'reliable' if (conv and (spread or 0) == 0.0) else ('questionable' if conv else 'failed')
    emit({'id': 'E3', 'title': 'transient repeat x3 spread', 'converged': conv,
          'err': spread, 'err_str': f'spread={spread:.2e}' if spread is not None else 'MISSING-RUN',
          'tol': 0.0, 'class': cls, 'wall_ms': 0, 'npts': '-',
          'rejected': str(rej), 'iter_per_pt': '-', 'warns': [], 'fatals': []})

# ---------------- F: dynamics / energy / self-convergence ----------------

def case_F1():
    """3-stage B-inverter + RC ring: must oscillate rail-to-rail, stable period."""
    net = ['t',
           'B1 o1 0 V=5*(1-u(V(o3)-2.5))', 'R1 o1 n1 1k', 'C1 n1 0 1n IC=0',
           'B2 o2 0 V=5*(1-u(V(n1)-2.5))', 'R2 o2 n2 1k', 'C2 n2 0 1n IC=2',
           'B3 o3 0 V=5*(1-u(V(n2)-2.5))', 'R3 o3 0 1k',
           '.tran 10n 10u UIC']
    r, s = base_ctrl(net, ['v(o1)'], timeout=30)
    v = [x for _, x in s['v(o1)']]
    sw = sum(1 for a, b in zip(v, v[1:]) if (a < 2.5) != (b < 2.5)) if v else 0
    ok = converged_of(r) and v and min(v) < 0.5 and max(v) > 4.5 and sw > 10
    # period stability: last two rising-crossing intervals agree within 10%
    cross = []
    for (t0, a), (t1, b) in zip(s['v(o1)'], s['v(o1)'][1:]):
        if a < 2.5 <= b:
            cross.append(t0 + (2.5 - a) * (t1 - t0) / (b - a))
    per = [b - a for a, b in zip(cross, cross[1:])]
    stable = len(per) >= 3 and (max(per[-3:]) - min(per[-3:])) / per[-1] < 0.1
    cls = 'reliable' if (ok and stable) else ('questionable' if ok else 'failed')
    emit({'id': 'F1', 'title': 'ring oscillator rail+period', 'converged': bool(ok),
          'err': None, 'err_str': f'sw={sw} per_last={per[-1]:.2e}' if per else f'sw={sw}',
          'tol': None, 'class': cls, 'wall_ms': r['wall_ms'], 'npts': r['npts'],
          'rejected': r.get('rejected'), 'iter_per_pt': r.get('iter_per_pt'),
          'warns': r['warns'], 'fatals': r['fatals']})

def case_F2():
    """Interrupted 10mA in 10mH with 1nF snub: bounded kickback, no nonfinite."""
    net = ['t', 'I1 0 n1 DC 10m', 'L1 n1 0 10mH IC=0', 'C1 n1 0 1n IC=0',
           'B1 n1 0 I=V(n1)/(1+(1MEG-1)*u(TIME-5m))',
           '.tran 1u 10m UIC']
    r, s = base_ctrl(net, ['v(n1)'], timeout=60)
    v = [x for _, x in s['v(n1)']]
    bad = any(not math.isfinite(x) for x in v)
    peak = max((abs(x) for x in v), default=None)
    conv = converged_of(r) and not bad and peak is not None
    sane = peak is not None and 1.0 < peak < 1e6
    cls = classify(conv and sane, None, None, r)
    emit({'id': 'F2', 'title': 'inductor interrupt kickback bounded', 'converged': conv,
          'err': None, 'err_str': f'peak={peak:.3e}' if peak is not None else '-',
          'tol': None, 'class': cls, 'wall_ms': r['wall_ms'], 'npts': r['npts'],
          'rejected': r.get('rejected'), 'iter_per_pt': r.get('iter_per_pt'),
          'warns': r['warns'], 'fatals': r['fatals']})

def case_F3():
    """5-stage RC ladder: default vs tight reference (self-convergence)."""
    ladder = ['V1 in 0 PWL(0 0 10u 0 11u 5 2m 5)']
    prev = 'in'
    for i in range(5):
        ladder += [f'R{i} {prev} n{i} 1k', f'C{i} n{i} 0 1n IC=0']
        prev = f'n{i}'
    base = ['t'] + ladder + ['.tran 1u 2m UIC']
    r1, s1 = base_ctrl(base, ['v(n4)'], timeout=30)
    base_t = ['t', '.options reltol=1e-6 abstol=1e-12'] + ladder + ['.tran 100n 2m UIC']
    r2, s2 = base_ctrl(base_t, ['v(n4)'], timeout=60)
    d2 = dict(((round(t, 12), v) for t, v in s2['v(n4)']))
    errs = [abs(v - d2.get(round(t, 12), v)) for t, v in s1['v(n4)']
            if round(t, 12) in d2]
    err = max(errs) if errs else None
    conv = converged_of(r1) and converged_of(r2) and err is not None
    cls = classify(conv, err, 0.02, r1)
    emit({'id': 'F3', 'title': 'RC ladder self-convergence', 'converged': conv,
          'err': err, 'err_str': f'{err:.2e}' if err is not None else '-',
          'tol': 0.02, 'class': cls, 'wall_ms': r1['wall_ms'], 'npts': r1['npts'],
          'rejected': r1.get('rejected'), 'iter_per_pt': r1.get('iter_per_pt'),
          'warns': r1['warns'], 'fatals': r1['fatals']})

CASES = [case_A1, case_A2, case_A2b, case_A3, case_A4, case_A5, case_A6,
         case_B1, case_B2, case_B2b, case_B3, case_B4, case_B5, case_B6,
         case_C1, case_C2, case_C3, case_C3b, case_C4, case_C5,
         case_D1, case_D2, case_D3, case_D4, case_D5, case_D5b, case_D6, case_D7, case_D8,
         case_E1, case_E2, case_E3,
         case_F1, case_F2, case_F3]

if __name__ == '__main__':
    t0 = time.perf_counter()
    for c in CASES:
        try:
            c()
        except Exception as e:
            import traceback
            traceback.print_exc()
            emit({'id': getattr(c, '__name__', '?'), 'title': f'HARNESS-ERROR {e!r}',
                  'converged': False, 'err': None, 'err_str': f'HARNESS {e!r}',
                  'tol': None, 'class': 'failed', 'wall_ms': 0, 'npts': '-',
                  'rejected': '-', 'iter_per_pt': '-', 'warns': [], 'fatals': ['harness']})
    os.makedirs('logs', exist_ok=True)
    with open('logs/num_results.json', 'w') as f:
        json.dump({'ngspice': NGSPICE_VER, 'records': REC}, f, indent=1, default=str)
    dt = time.perf_counter() - t0
    print(f'\n{len(REC)} records in {dt:.1f}s -> logs/num_results.json')
    from collections import Counter
    print(Counter(r['class'] for r in REC))
