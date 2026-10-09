"""Main-thread verification: TIME reset, RC/L handoff, PWL cost, auto-window, target demo.

Subprocess-contained ngspice batch only. Writes logs/window_main.json.
"""
import sys, os, json, time, math
sys.path.insert(0, '<repo>/experiments/exp-safety')
from helper_safety import tran_run
import bsource as BS

REC = []
def emit(r):
    REC.append(r)
    print(f"{r['id']:44s} {r.get('note','')} npts={r.get('npts')} wall={r.get('wall_ms',0):.0f}ms")

def peak_of(series, vec):
    vals = [v for _, v in series.get(vec, [])]
    return max(vals, key=abs) if vals else None

def final_of(series, vec):
    pts = series.get(vec, [])
    return pts[-1][1] if pts else None

def shift_time_expr(expr, t_off):
    """Shift absolute TIME constants by -t_off (numbers after TIME-)."""
    import re
    from validator import parse_value
    def fmt(v):
        return f"{v:.12g}"
    def repl(m):
        v = parse_value(m.group(1))
        if v is None: return m.group(0)
        return f"TIME-{fmt(v - t_off)}"
    return re.sub(r'TIME\s*-\s*([^\s)]+)', repl, expr)

# ---- V1: TIME reset probe ----
base = ['t', 'B1 out 0 V=5*(u(TIME-500u)-u(TIME-500.1u))', 'R1 out 0 1k',
        '.tran 10n 600u 0 10n UIC']
r, s = tran_run(base, ['v(out)'], timeout=60, tag='main')
ref_peak = peak_of(s, 'v(out)')
emit({'id': 'V1 ref full 600u fine', 'note': f'peak={ref_peak}', 'npts': r['npts'], 'wall_ms': r['wall_ms']})
# naive restart [400u,600u] no shift
naive = ['t', 'B1 out 0 V=5*(u(TIME-500u)-u(TIME-500.1u))', 'R1 out 0 1k',
         '.tran 10n 200u 0 10n UIC']
r2, s2 = tran_run(naive, ['v(out)'], timeout=60, tag='main')
emit({'id': 'V2 naive restart noshift', 'note': f'peak={peak_of(s2,"v(out)")} (expect 0)', 'npts': r2['npts'], 'wall_ms': r2['wall_ms']})
# shifted restart
expr_shift = shift_time_expr('5*(u(TIME-500u)-u(TIME-500.1u))', 400e-6)
shift = ['t', f'B1 out 0 V={expr_shift}', 'R1 out 0 1k',
         '.tran 10n 200u 0 10n UIC']
r3, s3 = tran_run(shift, ['v(out)'], timeout=60, tag='main')
emit({'id': 'V3 shifted restart', 'note': f'peak={peak_of(s3,"v(out)")} (expect 5)', 'npts': r3['npts'], 'wall_ms': r3['wall_ms']})

# ---- V4: RC handoff ----
ref = ['t', 'V1 drv 0 DC 5', 'R1 drv cap 10k', 'C1 cap 0 100n',
       '.tran 1u 1m 0 1u UIC']
rr, sr = tran_run(ref, ['v(cap)'], timeout=60, tag='main')
vref_final = final_of(sr, 'v(cap)')
# split at 500u
s1 = ['t', 'V1 drv 0 DC 5', 'R1 drv cap 10k', 'C1 cap 0 100n',
      '.tran 1u 500u 0 1u UIC']
ra, sa = tran_run(s1, ['v(cap)'], timeout=60, tag='main')
vmid = final_of(sa, 'v(cap)')
s2n = ['t', 'V1 drv 0 DC 5', 'R1 drv cap 10k', 'C1 cap 0 100n',
       f'.ic v(cap)={vmid:.12g}', '.tran 1u 500u 0 1u UIC']
rb, sb = tran_run(s2n, ['v(cap)'], timeout=60, tag='main')
vseg_final = final_of(sb, 'v(cap)')
emit({'id': 'V4 RC handoff', 'note': f'ref={vref_final:.8f} seg={vseg_final:.8f} err={(vseg_final-vref_final)*1e6:.3f}uV', 'npts': rr['npts']+0, 'wall_ms': rr['wall_ms']})

# ---- V5: L handoff needs IC= ----
rlref = ['t', 'V1 a 0 DC 5', 'R1 a b 100', 'L1 b 0 10mH IC=0',
         '.tran 1u 1m 0 1u UIC']
rrl, srl = tran_run(rlref, ['v(b)', 'i(l1)'], timeout=60, tag='main')
s1l = ['t', 'V1 a 0 DC 5', 'R1 a b 100', 'L1 b 0 10mH IC=0',
       '.tran 1u 500u 0 1u UIC']
ra2, sa2 = tran_run(s1l, ['v(b)', 'i(l1)'], timeout=60, tag='main')
il_mid = sa2['i(l1)'][-1][1]; vb_mid = sa2['v(b)'][-1][1]
# node-only (wrong)
bad = ['t', 'V1 a 0 DC 5', 'R1 a b 100', 'L1 b 0 10mH IC=0',
       f'.ic v(b)={vb_mid:.12g}', '.tran 1u 500u 0 1u UIC']
rbb, sbb = tran_run(bad, ['i(l1)'], timeout=60, tag='main')
# correct IC rewrite
good = ['t', 'V1 a 0 DC 5', 'R1 a b 100', f'L1 b 0 10mH IC={il_mid:.12g}',
        f'.ic v(b)={vb_mid:.12g}', '.tran 1u 500u 0 1u UIC']
rgg, sgg = tran_run(good, ['i(l1)'], timeout=60, tag='main')
il_bad0 = sbb['i(l1)'][0][1] if sbb['i(l1)'] else None
il_good0 = sgg['i(l1)'][0][1] if sgg['i(l1)'] else None
emit({'id': 'V5 L handoff', 'note': f'mid={il_mid*1e3:.4f}mA bad_start={il_bad0} good_start={il_good0}', 'npts': rrl['npts'], 'wall_ms': rrl['wall_ms']})

# ---- V6: PWL cost control (same blip as PWL, coarse) ----
pwl = ['t', 'V1 in 0 PWL(0 0 499.9u 0 500u 5 500.1u 5 500.2u 0 1m 0)',
       'R1 in 0 1k', '.tran 10u 1m UIC']
rp, sp = tran_run(pwl, ['v(in)'], timeout=60, tag='main')
emit({'id': 'V6 PWL coarse blip', 'note': f'peak={peak_of(sp,"v(in)")} (expect 5)', 'npts': rp['npts'], 'wall_ms': rp['wall_ms'], 'dt_max': rp.get('dt_max')})

# ---- V7: auto-window derivation from bsource.py ----
expr = '5*(u(TIME-5m)-u(TIME-5.0001m))'
req, bw = BS.required_tmax_for_expr(expr)
emit({'id': 'V7 bsource autoderive', 'note': f'req={req} widths={bw["widths"]} events={bw["events"]} unknown={bw["unknown_time"]}', 'npts': None, 'wall_ms': 0})

os.makedirs('<repo>/experiments/exp-window/logs', exist_ok=True)
with open('<repo>/experiments/exp-window/logs/window_main.json', 'w') as f:
    json.dump(REC, f, indent=1, default=str)
print('wrote window_main.json')
