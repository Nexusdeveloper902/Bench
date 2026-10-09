"""Event-resolution safety: B-source blip/sine/multi-source/MCU-like sweeps.

Questions: which events are caught vs missed at which tmax, what the
principled bound is (min_width/10? min_period/50?), what it costs, and
whether PWL/SIN independent sources need bounds at all (control).
Writes logs/events.json.
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(__file__))
from helper_safety import tran_run
import bsource as BS

REC = []


def blip_net(width, tmax=None):
    t0 = 500e-6
    base = ['t', f'B1 in 0 V=5*(u(TIME-{t0})-u(TIME-{t0 + width}))',
            'R1 in 0 1k', 'R2 in out 1k', 'C1 out 0 1n IC=0']
    tmax_s = '' if tmax is None else f' 0 {tmax}'
    base.append(f'.tran 1u 1m{tmax_s} UIC')
    return base


def emit(r):
    REC.append(r)
    print(f"{r['id']:28s} peak={r.get('peak','-')} caught={r.get('caught')} "
          f"npts={r.get('npts')} wall={r.get('wall_ms',0):.0f}ms "
          f"dtmax={r.get('dt_max')} tmax={r.get('tmax')}")


def sweep_blips():
    for w in (10e-9, 100e-9, 1e-6, 10e-6):
        req, _ = BS.required_tmax_for_expr(
            f'5*(u(TIME-500e-6)-u(TIME-{500e-6 + w}))')
        for div, tm in [('unbounded', None), ('w/1', w), ('w/2', w / 2),
                        ('w/5', w / 5), ('w/10', w / 10), ('w/20', w / 20)]:
            base = blip_net(w, tm)
            r, s = tran_run(base, ['v(in)', 'v(out)'], timeout=120,
                            tag=f'blip{int(w * 1e9)}n')
            peak = max((v for _, v in s['v(in)']), default=None)
            caught = peak is not None and peak >= 4.5
            emit({'id': f'blip w={w:.0e} tmax={div}', 'width': w, 'tmax': tm,
                  'tmax_div': div, 'required': req, 'peak': peak,
                  'caught': caught, 'npts': r['npts'], 'wall_ms': r['wall_ms'],
                  'dt_max': r.get('dt_max'), 'warns': len(r['warns']),
                  'fatals': r['fatals'][:2]})


def sweep_sine():
    import math
    for f in (10e3, 100e3, 1e6):
        T = 1 / f
        w = 2 * math.pi * f
        for div, tm in [('T/10', T / 10), ('T/20', T / 20), ('T/50', T / 50),
                        ('T/100', T / 100)]:
            base = ['t', f'B1 in 0 V=5*sin({w}*TIME)', 'R1 in 0 1k',
                    'R2 in out 1k', 'C1 out 0 1n IC=0',
                    f'.tran 5n 30u 0 {tm} UIC']
            r, s = tran_run(base, ['v(in)'], timeout=60, tag='sine')
            tail = [v for t, v in s['v(in)'] if t > 20e-6]
            amp = (max(tail) - min(tail)) / 2 if len(tail) > 10 else None
            err = abs((amp or 0) - 5) / 5 if amp else None
            emit({'id': f'sine f={f:.0e} tmax={div}', 'freq': f, 'tmax': tm,
                  'tmax_div': div, 'amp': amp, 'peak': amp,
                  'caught': err is not None and err < 0.02,
                  'amp_err': err, 'npts': r['npts'], 'wall_ms': r['wall_ms'],
                  'dt_max': r.get('dt_max'), 'warns': len(r['warns']),
                  'fatals': r['fatals'][:2]})


def multi_and_mixed():
    # two blips, fast 100ns + slow 10us on same node
    expr = ('5*(u(TIME-100u)-u(TIME-100.1u))'
            '+2*(u(TIME-500u)-u(TIME-510u))')
    req, detail = BS.required_tmax_for_expr(expr)
    for div, tm in [('unbounded', None), ('slow-only 1u', 1e-6),
                    ('policy', req)]:
        base = ['t', f'B1 in 0 V={expr}', 'R1 in 0 1k', 'R2 in out 1k',
                'C1 out 0 1n IC=0',
                f'.tran 1u 1m{"" if tm is None else f" 0 {tm}"} UIC']
        r, s = tran_run(base, ['v(in)'], timeout=120, tag='multi')
        vals = [v for _, v in s['v(in)']]
        fast = max(vals) if vals else None
        emit({'id': f'multi fast+slow tmax={div}', 'peak': fast,
              'caught': fast is not None and fast >= 6.5,
              'required': req, 'npts': r['npts'], 'wall_ms': r['wall_ms'],
              'dt_max': r.get('dt_max'), 'tmax': tm,
              'warns': len(r['warns']), 'fatals': r['fatals'][:2]})
    # short pulse on slow RC charge (slow dynamics + fast event)
    for div, tm in [('unbounded', None), ('policy 10n', 10e-9)]:
        base = ['t', 'V1 s 0 PWL(0 0 10m 5)',
                f'B1 in 0 V=5*(u(TIME-5m)-u(TIME-5.0001m))',
                'R1 s n 1k', 'R2 in n 1k', 'C1 n 0 1u IC=0',
                f'.tran 10u 10m{"" if tm is None else f" 0 {tm}"} UIC']
        r, s = tran_run(base, ['v(in)'], timeout=120, tag='mixed')
        peak = max((v for _, v in s['v(in)']), default=None)
        emit({'id': f'mixed slow-charge+100ns tmax={div}', 'peak': peak,
              'caught': peak is not None and peak >= 4.5,
              'npts': r['npts'], 'wall_ms': r['wall_ms'],
              'dt_max': r.get('dt_max'), 'tmax': tm,
              'warns': len(r['warns']), 'fatals': r['fatals'][:2]})
    # MCU-like 1MHz square via B on slow RC (500ns half-period)
    for div, tm in [('unbounded', None), ('policy 50n', 50e-9)]:
        base = ['t', 'B1 in 0 V=5*(0.5+0.5*sgn(sin(2*3.14159265*1e6*TIME)))',
                'R1 in 0 1k', 'R2 in out 100k', 'C1 out 0 100n IC=0',
                f'.tran 10n 10u{"" if tm is None else f" 0 {tm}"} UIC']
        r, s = tran_run(base, ['v(in)', 'v(out)'], timeout=120, tag='mcu')
        vin = [v for _, v in s['v(in)']]
        hi = max(vin) if vin else None
        lo = min(vin) if vin else None
        ok = hi is not None and hi > 4.5 and lo is not None and lo < 0.5
        emit({'id': f'mcu 1MHz-square tmax={div}', 'peak': hi,
              'caught': ok, 'npts': r['npts'],
              'wall_ms': r['wall_ms'], 'dt_max': r.get('dt_max'),
              'tmax': tm, 'warns': len(r['warns']),
              'fatals': r['fatals'][:2]})
    # PWL control: identical blip via PWL source (solver breakpoints expected)
    base = ['t', 'V1 in 0 PWL(0 0 500u 0 500.01u 5 500.02u 5 500.03u 0 1m 0)',
            'R1 in 0 1k', 'R2 in out 1k', 'C1 out 0 1n IC=0',
            '.tran 1u 1m UIC']
    r, s = tran_run(base, ['v(in)'], timeout=60, tag='pwlctl')
    peak = max((v for _, v in s['v(in)']), default=None)
    emit({'id': 'control PWL 10ns-ish blip unbounded', 'peak': peak,
          'caught': peak is not None and peak >= 4.5, 'npts': r['npts'],
          'wall_ms': r['wall_ms'], 'dt_max': r.get('dt_max'), 'tmax': None,
          'warns': len(r['warns']), 'fatals': r['fatals'][:2]})


if __name__ == '__main__':
    t0 = time.perf_counter()
    sweep_blips()
    sweep_sine()
    multi_and_mixed()
    os.makedirs('logs', exist_ok=True)
    with open('logs/events.json', 'w') as fh:
        json.dump(REC, fh, indent=1, default=str)
    print(f"\n{len(REC)} records in {time.perf_counter() - t0:.1f}s -> logs/events.json")
