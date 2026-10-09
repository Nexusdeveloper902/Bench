"""Event-resolution follow-ups: fixed sine-10kHz window, windowed multi-peak
metric, single-edge timing accuracy vs tmax, w/1 placement-fragility probe.
Appends logs/events2.json (does not overwrite events.json)."""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(__file__))
from helper_safety import tran_run

REC = []


def emit(r):
    REC.append(r)
    print(f"{r['id']:44s} {r.get('note','')} npts={r.get('npts')} "
          f"wall={r.get('wall_ms',0):.0f}ms dtmax={r.get('dt_max')}")


def sine_fixed():
    import math
    for f in (10e3,):
        T = 1 / f
        w = 2 * math.pi * f
        stop = 5 * T
        for div, tm in [('T/10', T / 10), ('T/20', T / 20), ('T/50', T / 50),
                        ('T/100', T / 100)]:
            base = ['t', f'B1 in 0 V=5*sin({w}*TIME)', 'R1 in 0 1k',
                    'R2 in out 1k', 'C1 out 0 1n IC=0',
                    f'.tran {T} {stop} 0 {tm} UIC']
            r, s = tran_run(base, ['v(in)'], timeout=60, tag='sine10k')
            tail = [v for t, v in s['v(in)'] if t > 4 * T]
            amp = (max(tail) - min(tail)) / 2 if len(tail) > 10 else None
            err = abs((amp or 0) - 5) / 5 if amp else None
            emit({'id': f'sine10k f=1e4 tmax={div}', 'amp': amp,
                  'amp_err': err,
                  'note': f"amp={amp:.4f}" if amp else 'NO-DATA',
                  'npts': r['npts'], 'wall_ms': r['wall_ms'],
                  'dt_max': r.get('dt_max')})


def multi_windowed():
    expr = ('5*(u(TIME-100u)-u(TIME-100.1u))'
            '+2*(u(TIME-500u)-u(TIME-510u))')
    for div, tm in [('unbounded', None), ('slow-only 1u', 1e-6),
                    ('policy 10n', 10e-9)]:
        base = ['t', f'B1 in 0 V={expr}', 'R1 in 0 1k', 'R2 in out 1k',
                'C1 out 0 1n IC=0',
                f'.tran 1u 1m{"" if tm is None else f" 0 {tm}"} UIC']
        r, s = tran_run(base, ['v(in)'], timeout=120, tag='multi2')
        d = dict(s['v(in)'])
        fast = max((v for t, v in s['v(in)'] if 90e-6 < t < 120e-6),
                   default=None)
        slow = max((v for t, v in s['v(in)'] if 490e-6 < t < 520e-6),
                   default=None)
        ok = (fast is not None and fast >= 4.5 and slow is not None
              and slow >= 1.9)
        emit({'id': f'multi2 tmax={div}',
              'note': f'fast={fast} slow={slow} both={ok}',
              'fast': fast, 'slow': slow, 'caught': ok,
              'npts': r['npts'], 'wall_ms': r['wall_ms'],
              'dt_max': r.get('dt_max')})


def single_edge_timing():
    # B step at t0=5us; measure v(in) 2.5V crossing error vs tmax
    t0 = 5e-6
    for div, tm in [('unbounded', None), ('1u', 1e-6), ('100n', 100e-9),
                    ('10n', 10e-9)]:
        base = ['t', f'B1 in 0 V=5*u(TIME-{t0})', 'R1 in 0 1k',
                f'.tran 100n 10u{"" if tm is None else f" 0 {tm}"}']
        r, s = tran_run(base, ['v(in)'], timeout=60, tag='edge')
        data = s['v(in)']
        cross = None
        for (a_t, a), (b_t, b) in zip(data, data[1:]):
            if a < 2.5 <= b:
                cross = a_t + (2.5 - a) * (b_t - a_t) / (b - a)
                break
        err = abs((cross or 0) - t0) if cross else None
        emit({'id': f'edge timing tmax={div}',
              'note': f'cross={cross} err={err}', 'cross': cross,
              'timing_err': err, 'npts': r['npts'],
              'wall_ms': r['wall_ms'], 'dt_max': r.get('dt_max')})


def fragility():
    # 1us blip, t0 shifted by sub-step offsets; tmax=w/1 vs w/10
    w = 1e-6
    for k in range(8):
        t0 = 500e-6 + k * 0.13e-6
        for div, tm in [('w/1', w), ('w/10', w / 10)]:
            base = ['t', f'B1 in 0 V=5*(u(TIME-{t0})-u(TIME-{t0 + w}))',
                    'R1 in 0 1k',
                    f'.tran 1u 1m 0 {tm} UIC']
            r, s = tran_run(base, ['v(in)'], timeout=60, tag='frag')
            peak = max((v for _, v in s['v(in)']), default=None)
            emit({'id': f'frag w/1-vs-w10 k={k} {div}',
                  'note': f'peak={peak}',
                  'peak': peak, 'caught': peak is not None and peak >= 4.5,
                  'npts': r['npts'], 'wall_ms': r['wall_ms'],
                  'dt_max': r.get('dt_max')})


if __name__ == '__main__':
    t0 = time.perf_counter()
    sine_fixed()
    multi_windowed()
    single_edge_timing()
    fragility()
    os.makedirs('logs', exist_ok=True)
    with open('logs/events2.json', 'w') as fh:
        json.dump(REC, fh, indent=1, default=str)
    print(f"\n{len(REC)} records in {time.perf_counter() - t0:.1f}s -> logs/events2.json")
