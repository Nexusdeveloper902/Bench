"""L1 (trivial) + L2 (nonlinear) models: native B-source vs analytic/.model reference."""
import sys, json, time, math, re
sys.path.insert(0, '.')
from helper import run_cir, has_error

def op_val(out, vec='v(out)'):
    m = re.search(re.escape(vec) + r'\s*=\s*([-\deE+.NaNnUuinfINFloc]+)', out)
    return m.group(1) if m else None

def timed(label, net, vec='v(out)', reps=5):
    """Run reps times, return avg wall + last value."""
    walls = []
    last = None
    for _ in range(reps):
        t0 = time.perf_counter()
        r = run_cir(net)
        walls.append((time.perf_counter() - t0) * 1e3)
        last = r
    v = op_val(last['out'], vec)
    return {'label': label, 'avg_wall_ms': sum(walls)/len(walls),
            'val': float(v) if v not in (None,'nan') else None,
            'raw': v, 'errs': has_error(last['out']), 'rc': last['rc'],
            'net': net}

def shockley_vd(vs=5.0, r=1000.0, Is=1e-14, nvt=0.038775):
    """Bisection solve (Vs-Vd)/R = Is*(exp(Vd/nvt)-1). Newton overshoots on
    exponentials (first attempt stalled at 1.091073); bisection is exact."""
    f = lambda vd: (vs - vd) / r - Is * (math.exp(vd / nvt) - 1)
    lo, hi = 0.0, vs
    assert f(lo) > 0 and f(hi) < 0
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2

R = {}
# ---- L1 ----
R['L1-R'] = timed('R-behav', ['t', 'V1 in 0 DC 5', 'B1 out 0 I=V(out)/1k', 'R1 in out 0.001', '.op',
    '.control','run','print v(out) i(v1)','.endc','.end'])
# note: B R between out-0 in parallel? simpler: V->B->R series
R['L1-R-series'] = timed('R-series', ['t', 'V1 in 0 DC 5', 'R1 in mid 1k',
    'B1 mid out I=V(mid,out)/1k', 'R2 out 0 1k', '.op',
    '.control','run','print v(out)','.endc','.end'])
R['L1-Vconst'] = timed('Vconst', ['t', 'B1 out 0 V=5', 'R1 out 0 1k', '.op',
    '.control','run','print v(out)','.endc','.end'])
R['L1- gain'] = timed('gain10', ['t', 'V1 in 0 DC 0.5', 'R1 in 0 1k', 'B1 out 0 V=10*V(in)', 'R2 out 0 1k', '.op',
    '.control','run','print v(out)','.endc','.end'])
R['L1-affine'] = timed('affine', ['t', 'V1 in 0 DC 0.5', 'R1 in 0 1k', 'B1 out 0 V=2*V(in)+0.7', 'R2 out 0 1k', '.op',
    '.control','run','print v(out)','.endc','.end'])
R['L1-Isrc'] = timed('isrc', ['t', 'I1 0 out DC 1m', 'R1 out 0 1k', 'B1 bout 0 V=I(I1)*1k', 'R2 bout 0 1k', '.op',
    '.control','run','print v(out) v(bout)','.endc','.end'], vec='v(bout)')

# ---- L2 ----
ref_vd = shockley_vd()
R['analytic_diode_Vd'] = ref_vd
R['L2-Dref'] = timed('D-model', ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'D1 out 0 DLED',
    '.model DLED D(IS=1e-14 N=1.5)', '.op', '.control','run','print v(out)','.endc','.end'])
R['L2-Bdiode'] = timed('B-diode', ['t', 'V1 in 0 DC 5', 'R1 in out 1k',
    'B1 out 0 I=1e-14*(exp(V(out)/0.038775)-1)', 'C1 out 0 1p', '.op',
    '.control','run','print v(out)','.endc','.end'])
R['L2-Bdiode-nocap'] = timed('B-diode-nocap', ['t', 'V1 in 0 DC 5', 'R1 in out 1k',
    'B1 out 0 I=1e-14*(exp(V(out)/0.038775)-1)', '.op',
    '.control','run','print v(out)','.endc','.end'])
R['L2-tanh'] = timed('tanh-sat', ['t', 'V1 in 0 DC 5', 'R1 in 0 1k', 'B1 out 0 V=10*tanh(V(in)/2)', 'R2 out 0 1k', '.op',
    '.control','run','print v(out)','.endc','.end'])
R['L2-tanh-expect'] = 10*math.tanh(5/2)
R['L2-clip'] = timed('clip', ['t', 'V1 in 0 DC 5', 'R1 in 0 1k', 'B1 out 0 V=min(max(V(in),-1),1)', 'R2 out 0 1k', '.op',
    '.control','run','print v(out)','.endc','.end'])
R['L2-pwl'] = timed('pwl', ['t', 'V1 in 0 DC 1.5', 'R1 in 0 1k', 'B1 out 0 V=pwl(V(in),0,0,1,1,2,0)', 'R2 out 0 1k', '.op',
    '.control','run','print v(out)','.endc','.end'])
R['L2-log'] = timed('log', ['t', 'V1 in 0 DC 2.71828', 'R1 in 0 1k', 'B1 out 0 V=ln(V(in))', 'R2 out 0 1k', '.op',
    '.control','run','print v(out)','.endc','.end'])
R['L2-log-expect'] = 1.0
R['L2-param'] = timed('param-diode', ['t', '.param IS=1e-14 NVT=0.038775', 'V1 in 0 DC 5', 'R1 in out 1k',
    'B1 out 0 I={IS}*(exp(V(out)/{NVT})-1)', 'C1 out 0 1p', '.op',
    '.control','run','print v(out)','.endc','.end'])
# pathological inputs
R['L2-rev50'] = timed('rev-50V', ['t', 'V1 in 0 DC -50', 'R1 in out 1k',
    'B1 out 0 I=1e-14*(exp(V(out)/0.038775)-1)', 'C1 out 0 1p', '.op',
    '.control','run','print v(out)','.endc','.end'])
R['L2-rev50-ref'] = timed('rev-50V-ref', ['t', 'V1 in 0 DC -50', 'R1 in out 1k', 'D1 out 0 DLED',
    '.model DLED D(IS=1e-14 N=1.5 BV=5 IBV=1u)', '.op', '.control','run','print v(out)','.endc','.end'])

for k, v in R.items():
    if isinstance(v, dict) and 'val' in v:
        print(f"{k:18s} val={v['raw']} wall={v['avg_wall_ms']:.1f}ms errs={v['errs']} rc={v['rc']}")
    else:
        print(f"{k:18s} {v}")

# errors vs analytic
print("\n--- accuracy ---")
print(f"analytic Vd={ref_vd:.6f}")
for k in ['L2-Dref','L2-Bdiode','L2-Bdiode-nocap','L2-param']:
    v = R[k]['val']
    print(f"{k}: Vd={v:.6f} err={(v-ref_vd)*1e3:+.3f} mV" if v else f"{k}: FAILED")

with open('logs/l12_results.json','w') as f:
    json.dump(R, f, indent=1, default=str)
