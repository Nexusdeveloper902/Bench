"""Smooth-hump fragility probe: zero-endpoint event at tmax=w/1 vs w/10.

A u-step blip at tmax=w/1 is sometimes rescued by kink-triggered LTE
refinement. A smooth (raised-sine, zero-endpoint) hump is the harder case:
a straddling step sees (0,0) and is accepted with the event invisible.
6 sub-step offsets x 2 bounds. Writes logs/events3.json.
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(__file__))
from helper_safety import tran_run

if __name__ == '__main__':
    w = 1e-6
    rec = []
    for k in range(6):
        t0 = 500e-6 + k * 0.17e-6
        for div, tm in [('w/1', w), ('w/10', w / 10)]:
            base = ['t', f'B1 in 0 V=5*sin(3.14159265*(TIME-{t0})/{w})'
                    f'*(u(TIME-{t0})-u(TIME-{t0 + w}))',
                    'R1 in 0 1k', f'.tran 1u 1m 0 {tm} UIC']
            r, s = tran_run(base, ['v(in)'], timeout=60, tag='hump')
            peak = max((v for _, v in s['v(in)']), default=None)
            rec.append({'id': f'hump k={k} {div}', 'peak': peak,
                        'caught': peak is not None and peak >= 4.5,
                        'npts': r['npts'], 'wall_ms': r['wall_ms'],
                        'dt_max': r.get('dt_max'), 'tmax': tm,
                        'width': w})
            print(f"k={k} {div}: peak={peak} "
                  f"caught={peak is not None and peak >= 4.5}")
    os.makedirs('logs', exist_ok=True)
    with open('logs/events3.json', 'w') as fh:
        json.dump(rec, fh, indent=1, default=str)
    print('-> logs/events3.json')
