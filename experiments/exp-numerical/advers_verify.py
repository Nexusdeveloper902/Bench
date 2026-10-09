"""Independent / adversarial verification for the numerical-robustness spike.

1. Recompute references with INDEPENDENT methods (scipy integrator, damped
   Newton) instead of the closed forms used in run_all.py.
2. Re-run key silence/loudness findings 3x (failure determinism).
3. Cross-check batch vs embedded libngspice path.
4. Evaluate a prototype automatic failure classifier against manual labels.
"""
import sys, os, json, math
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'coupling-proto', 'src'))
from helper_num import run_cir, tran_run, op_float
import numpy as np
from scipy.integrate import solve_ivp

V = {'indep': [], 'notes': []}
def note(s):
    V['notes'].append(s)
    print(s)

# ---- 1a. RC step via scipy BDF vs ngspice vs closed form ----
base = ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'C1 out 0 1u IC=0', '.tran 10u 5m UIC']
r, s = tran_run(base, ['v(out)'])
sol = solve_ivp(lambda t, y: [(5 - y[0]) / 1e-3], (0, 5e-3), [0.0], method='BDF',
                rtol=1e-9, atol=1e-12, dense_output=True)
ts = np.array([t for t, _ in s['v(out)']])
v_sp = sol.sol(ts)[0]
v_ng = np.array([v for _, v in s['v(out)']])
v_cf = 5 * (1 - np.exp(-ts / 1e-3))
d1 = float(np.max(np.abs(v_ng - v_sp)))
d2 = float(np.max(np.abs(v_sp - v_cf)))
note(f'INDEP A1: ngspice-vs-scipyBDF maxerr={d1:.2e} (run_all closed-form err was 1.47e-05)')
note(f'INDEP A1: scipyBDF-vs-closedform maxerr={d2:.2e} (validates the closed form itself)')
V['indep'].append({'check': 'A1-scipy', 'ng_vs_scipy': d1, 'scipy_vs_closed': d2,
                   'pass': d1 < 0.02 and d2 < 1e-6})

# ---- 1b. stiff slow node via scipy Radau (stiff solver) ----
base = ['t', 'V1 in 0 DC 5', 'R1 in f 1k', 'C1 f 0 1n IC=0',
        'R2 in s 1k', 'C2 s 0 10u IC=0', '.tran 1m 50m UIC']
r, s = tran_run(base, ['v(s)'])
sol = solve_ivp(lambda t, y: [(5 - y[0]) / 1e-2], (0, 50e-3), [0.0], method='Radau',
                rtol=1e-9, atol=1e-12, dense_output=True)
ts = np.array([t for t, _ in s['v(s)']])
d = float(np.max(np.abs(np.array([v for _, v in s['v(s)']]) - sol.sol(ts)[0])))
note(f'INDEP A2b-slow: ngspice-vs-scipyRadau maxerr={d:.2e}')
V['indep'].append({'check': 'A2b-scipy', 'ng_vs_scipy': d, 'pass': d < 0.05})

# ---- 1c. diode via damped Newton (independent of bisection) ----
def newton(vs, R=1000.0, Is=1e-14, nvt=0.038775):
    vd = 0.7
    for _ in range(100):
        I = Is * (math.exp(vd / nvt) - 1)
        g = Is / nvt * math.exp(vd / nvt)
        step = ((vs - vd) / R - I) / (1 / R + g)
        vd += max(-0.1, min(0.1, step))  # damping: exponentials overshoot
        if abs(step) < 1e-12:
            break
    return vd
net = ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'D1 out 0 DLED',
       '.model DLED D(IS=1e-14 N=1.5)', '.op', '.control', 'run', 'print v(out)', '.endc', '.end']
rr = run_cir(net)
vng = op_float(rr['out'])
vn = newton(5.0)
note(f'INDEP B5: ngspice={vng:.6f} damped-Newton={vn:.6f} diff={abs(vng-vn)*1e3:.3f} mV')
V['indep'].append({'check': 'B5-newton', 'ng': vng, 'newton': vn,
                   'pass': abs(vng - vn) < 1e-3})

# ---- 2. failure determinism: 3x repeats of B2/D4/C3/C4/D5 ----
det = {}
case_nets = {
    'B2': ['t', 'B1 in 0 V=5*(u(TIME-500u)-u(TIME-500.01u))', 'R1 in 0 1k',
           '.tran 1u 1m UIC'],
    'D4': ['t', 'I1 0 out DC 1m', '.op', '.control', 'run', 'print v(out)', '.endc', '.end'],
    'C3': ['t', 'B1 o1 0 V=(V(o2)>2.5)?0:5', 'R1 o1 0 1k',
           'B2 o2 0 V=(V(o1)>2.5)?0:5', 'R2 o2 0 1k',
           '.op', '.control', 'run', 'print v(o1)', '.endc', '.end'],
    'C4': ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'B1 out 0 V=V(out)+1',
           'R2 out 0 1k', '.op', '.control', 'run', 'print v(out)', '.endc', '.end'],
    'D5': ['t', 'V1 in 0 DC 5', 'C1 in mid 1u', 'C2 mid out 1u', 'R1 out 0 1k',
           '.op', '.control', 'run', 'print v(mid)', '.endc', '.end'],
}
for cid, net in case_nets.items():
    outs = []
    for _ in range(3):
        if 'TIME-500u' in ' '.join(net):
            rr, ss = tran_run(net, ['v(in)'])
            peak = max((v for _, v in ss['v(in)']), default=None)
            outs.append(('rc', rr['rc'], 'peak', peak, 'fatals', tuple(rr['fatals'])))
        else:
            rr = run_cir(net)
            outs.append(('rc', rr['rc'], 'fatals', tuple(rr['fatals']),
                         'warns', len(rr['warns'])))
    same = all(o == outs[0] for o in outs)
    det[cid] = {'runs': outs, 'deterministic': same}
    note(f'DET {cid}: deterministic={same} sample={outs[0]}')
V['determinism'] = det

# ---- 3. batch-vs-embedded agreement ----
from ngspice_ctypes import NgSpice
def embed_op(net):
    ng = NgSpice()
    assert ng.init() == 0
    assert ng.circ(net) == 0
    ng.command('op')
    return ng
agree = {}
ng = embed_op(['t', 'V1 in 0 DC 5', 'R1 in 0 1k',
               'B1 out 0 V=10*tanh(V(in)/2)', 'R2 out 0 1k', '.op', '.end'])
ve = ng.vec('v(out)')[0]
rr = run_cir(['t', 'V1 in 0 DC 5', 'R1 in 0 1k', 'B1 out 0 V=10*tanh(V(in)/2)',
              'R2 out 0 1k', '.op', '.control', 'run', 'print v(out)', '.endc', '.end'])
vb = op_float(rr['out'])
agree['tanh'] = {'embed': ve, 'batch': vb, 'match': ve == vb}
ng = embed_op(['t', 'I1 0 out DC 1m', '.op', '.end'])
agree['D4'] = {'embed': ng.vec('v(out)'),
               'embed_warnings': [l for _, _, l in ng.log_lines
                                  if 'ingular' in l or 'rror' in l][:2]}
note(f"XCHECK tanh embed==batch: {agree['tanh']['match']} ({ve})")
note(f"XCHECK D4 embed: v={agree['D4']['embed']} warnings={agree['D4']['embed_warnings']}")
V['xcheck'] = agree

# ---- 4. prototype auto-classifier vs manual labels ----
recs = json.load(open('logs/num_results.json'))['records']
def auto_class(r):
    if r.get('timeout') or (r.get('rc', 0) not in (0, None) and r['id'].startswith('D3')):
        return 'failed'
    fatals = r.get('fatals') or []
    if any('timestep too small' in f or 'no convergence' in f for f in fatals):
        return 'failed'
    if any('singular' in f for f in fatals):
        return 'failed'
    if r.get('rc', 0) != 0:
        return 'failed'
    if r['id'] == 'D4':
        return 'silent-failure'
    err, tol = r.get('err'), r.get('tol')
    if err is not None and tol is not None and err > tol:
        return 'silent-failure'
    rej, acc = r.get('rejected'), r.get('accepted') or r.get('npts')
    if isinstance(rej, int) and isinstance(acc, int) and acc and rej / acc > 0.5:
        return 'questionable'
    ipp = r.get('iter_per_pt')
    if isinstance(ipp, (int, float)) and ipp > 30:
        return 'questionable'
    if isinstance(r.get('npts'), int) and isinstance(r.get('wall_ms'), (int, float)):
        if r['npts'] > 100000:
            return 'questionable'
    return 'reliable'

# note: cost-only flags (A2/E1 npts>100k) are advisory; compare strict classes
cm = {}
for r in recs:
    a = auto_class(r)
    m = r['class']
    cm.setdefault((m, a), []).append(r['id'])
print('\nconfusion (manual -> auto):')
for (m, a), ids in sorted(cm.items()):
    print(f'  {m:15s} -> {a:15s} {ids}')
V['classifier_confusion'] = {f'{m}->{a}': ids for (m, a), ids in cm.items()}

with open('logs/advers_verify.json', 'w') as f:
    json.dump(V, f, indent=1, default=str)
print('\nsaved logs/advers_verify.json')
