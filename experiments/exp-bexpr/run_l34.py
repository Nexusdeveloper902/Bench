"""L3 dynamic + L4 realistic components, native B-sources."""
import sys, json, time, math, re
sys.path.insert(0, '.')
from helper import run_cir, has_error

def tran_final(net, vec='v(out)', reps=3):
    walls = []
    last = None
    for _ in range(reps):
        t0 = time.perf_counter()
        last = run_cir(net)
        walls.append((time.perf_counter()-t0)*1e3)
    out = last['out']
    # last printed value of vec in tran: ngspice prints table; take last match
    ms = re.findall(re.escape(vec) + r'\s+([-\deE+.]+)', out)
    # also handle 'v(out) = x' op style
    if not ms:
        ms = re.findall(re.escape(vec) + r'\s*=\s*([-\deE+.]+)', out)
    return {'avg_wall_ms': sum(walls)/len(walls), 'last': ms[-1] if ms else None,
            'npts': len(ms), 'errs': has_error(out), 'rc': last['rc'], 'out_tail': out[-600:]}

def tran_series(net, vec='v(out)'):
    """Return full time series via wrdata? Use print + parse all rows."""
    r = run_cir(net)
    out = r['out']
    rows = re.findall(r'(\d+\.\d+e[+-]\d+)\s+([-\deE+.]+)', out)
    return r, rows

R = {}
# L3.1 sine via TIME
net_sine = ['t', 'B1 out 0 V=5*sin(2*3.14159*1000*TIME)', 'R1 out 0 1k',
    '.tran 10u 2m', '.control', 'run', 'print v(out)', '.endc', '.end']
r, rows = tran_series(net_sine)
vals = [float(v) for _, v in rows]
R['L3-sine'] = {'n': len(vals), 'max': max(vals) if vals else None, 'min': min(vals) if vals else None,
                'expect': '+-5V sine 1kHz', 'errs': has_error(r['out']), 'rc': r['rc'],
                'pass': vals and abs(max(vals)-5)<0.05 and abs(min(vals)+5)<0.05}
print('L3-sine:', R['L3-sine'])

# L3.2 step discontinuity via u()
net_step = ['t', 'B1 out 0 V=u(TIME-1m)*5', 'R1 out 0 1k',
    '.tran 10u 2m', '.control', 'run', 'print v(out)', '.endc', '.end']
r, rows = tran_series(net_step)
vals = [float(v) for _, v in rows]
R['L3-step'] = {'n': len(vals), 'final': vals[-1] if vals else None, 'first': vals[0] if vals else None,
                'errs': has_error(r['out']), 'rc': r['rc'],
                'pass': vals and abs(vals[-1]-5)<0.01 and abs(vals[0])<0.6}
print('L3-step:', {k: v for k, v in R['L3-step'].items() if k != 'pass'}, 'pass=', R['L3-step']['pass'])

# L3.3 ddt differentiator: V=ddt(1m*TIME) should be 1m
net_ddt = ['t', 'B1 out 0 V=ddt(0.001*TIME)', 'R1 out 0 1k',
    '.tran 10u 2m UIC', '.control', 'run', 'print v(out)', '.endc', '.end']
R['L3-ddt'] = tran_final(net_ddt)
R['L3-ddt']['expect'] = 0.001
print('L3-ddt:', R['L3-ddt']['last'], R['L3-ddt']['errs'])

# L3.4 RC integrator via C + B current source (no idt): I=1mA into 1uF -> dV/dt=1000 V/s, V(1ms)=1V
net_int = ['t', 'B1 cap 0 I=1m', 'C1 cap 0 1u IC=0', 'R1 cap out 1', 'B2 out 0 V=V(cap)',
    '.tran 10u 2m UIC', '.control', 'run', 'print v(out)', '.endc', '.end']
r, rows = tran_series(net_int)
vals = [float(v) for _, v in rows]
R['L3-integrator'] = {'n': len(vals), 'final': vals[-1] if vals else None, 'expect_1ms': '~1.0 (V@1ms in 2ms run -> ~2V)',
    'errs': has_error(r['out']), 'rc': r['rc']}
print('L3-integrator final=', R['L3-integrator']['final'], '(expect ~2.0 at t=2ms)')

# L3.5 Schmitt (hysteresis via ternary + feedback through RC to avoid alg loop)
net_schmitt = ['t', 'V1 in 0 PWL(0 0 1m 0 1.01m 5 2m 5 2.01m 0 3m 0)',
    'R1 in mid 1k', 'B1 mid 0 I=V(mid)/1k',
    'B2 out 0 V=(V(mid)>2.75)?5:((V(mid)<2.25)?0:V(out))', 'R2 out 0 1k',
    '.tran 10u 3m', '.control', 'run', 'print v(out) v(mid)', '.endc', '.end']
r = run_cir(net_schmitt)
R['L3-schmitt-attempt'] = {'errs': has_error(r['out']), 'rc': r['rc'], 'log': r['out'][:800]}
print('L3-schmitt:', R['L3-schmitt-attempt']['errs'], r['out'][:400])

# L3.6 comparator no-hysteresis (works?)
net_comp = ['t', 'V1 inp 0 DC 3', 'V2 inm 0 DC 2', 'B1 out 0 V=(V(inp)>V(inm))?5:0', 'R1 out 0 1k',
    '.op', '.control', 'run', 'print v(out)', '.endc', '.end']
R['L3-comparator'] = tran_final(net_comp)
print('L3-comparator:', R['L3-comparator']['last'], '(expect 5)')

# ---- L4 realistic ----
# LED: Vf~2V + Rs 10: B diode + series R inside subckt
net_led = ['t', '.param IS=1e-14 NVT=0.05', 'V1 in 0 DC 5', 'R1 in out 1k',
    'X1 out 0 LEDm', '.subckt LEDm a k', 'Rs a m 10', 'B1 m k I={IS}*(exp(V(m,k)/{NVT})-1)', 'C1 m k 1p', '.ends LEDm',
    '.op', '.control', 'run', 'print v(out)', '.endc', '.end']
R['L4-LED'] = tran_final(net_led)
print('L4-LED Vf node:', R['L4-LED']['last'], R['L4-LED']['errs'])

# Sensor: affine + clamp 0..5: V=clamp(0.5*T+0.1) fake temp sweep via dc
net_sensor = ['t', 'V1 temp 0 DC 25', 'R1 temp 0 1k', 'B1 out 0 V=min(max(0.01*V(temp)+0.5,0),5)', 'R2 out 0 1k',
    '.op', '.control', 'run', 'print v(out)', '.endc', '.end']
R['L4-sensor'] = tran_final(net_sensor)
print('L4-sensor (expect 0.75):', R['L4-sensor']['last'])

# Regulator 5->3.3 with current limit: Vout=min(3.3, ...) + limit via load current sense
net_reg = ['t', 'V1 vin 0 DC 5', 'Rload out 0 6.6',
    'Vsense vin mid DC 0', 'B1 mid out V=3.3-0.1*I(Vsense)',
    '.op', '.control', 'run', 'print v(out) i(vsense)', '.endc', '.end']
r = run_cir(net_reg)
R['L4-regulator'] = {'errs': has_error(r['out']), 'rc': r['rc'], 'log': r['out'][:1000]}
m1 = re.search(r'v\(out\)\s*=\s*([-\deE+.]+)', r['out'])
print('L4-regulator v(out)=', m1.group(1) if m1 else 'N/A (see log)', '| expect ~3.3 with 0.5A load')
print(r['out'][:600])

# Nonlinear load: R varies with V: I = V/1k*(1+0.1*V)
net_nll = ['t', 'V1 in 0 DC 10', 'B1 in 0 I=V(in)/1k*(1+0.05*V(in))',
    '.op', '.control', 'run', 'print i(v1)', '.endc', '.end']
r = run_cir(net_nll)
m1 = re.search(r'i\(v1\)\s*=\s*([-\deE+.]+)', r['out'])
R['L4-nonlinear-load'] = {'i_v1': m1.group(1) if m1 else None, 'expect_A': -(10/1e3*(1+0.5)),
    'errs': has_error(r['out']), 'rc': r['rc']}
print('L4-nonlinear-load:', R['L4-nonlinear-load'])

with open('logs/l34_results.json', 'w') as f:
    json.dump(R, f, indent=1, default=str)
print('saved')
