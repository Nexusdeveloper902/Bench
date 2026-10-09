"""Target demo: 100ns B-blip at t=5ms inside 10ms slow RC charge.
Reference: full-fine TMAX=10ns over 10ms (~1M pts). Segmented: coarse 10us + fine window + coarse.
PWL: restated event, coarse throughout. Compares event + state + cost.
"""
import sys, os, json
sys.path.insert(0, '<repo>/experiments/exp-safety')
from helper_safety import tran_run
import re
from validator import parse_value

def shift_expr(expr, t_off):
    def fmt(v): return f"{v:.12g}"
    def repl(m):
        v = parse_value(m.group(1))
        if v is None: return m.group(0)
        return f"TIME-{fmt(v - t_off)}"
    return re.sub(r'TIME\s*-\s*([^\s)]+)', repl, expr)

def peak(s, v):
    vals = [x for _, x in s.get(v, [])]
    return max(vals) if vals else None
def final(s, v):
    p = s.get(v, []); return p[-1][1] if p else None
def at(s, v, t):
    import bisect
    pts = s.get(v, []); ts = [a for a, _ in pts]
    i = min(bisect.bisect_right(ts, t), len(pts)-1)
    return pts[i][1]

T0, W = 5e-3, 100e-9
EXPR = f'5*(u(TIME-{T0})-u(TIME-{T0+W}))'
# slow dynamics: DC 5V step into R10k/C1u (tau=10ms), B blip coupled via R into sense node
def mkbase(expr, tmax, tstop='10m', extra=()):
    return ['t', 'V1 s 0 DC 5', 'R1 s n 10k', 'C1 n 0 1u',
            f'B1 e 0 V={expr}', 'R2 e n 100k',
            *extra, f'.tran 10u {tstop} 0 {tmax} UIC']

print('REF full-fine starting (~1M pts, ~25s)...', flush=True)
r_ref, s_ref = tran_run(mkbase(EXPR, 10e-9), ['v(e)', 'v(n)'], timeout=300, tag='target')
ref_peak = peak(s_ref, 'v(e)'); ref_final = final(s_ref, 'v(n)')
ref_post = at(s_ref, 'v(n)', T0+W+2e-6)
print(f'REF peak={ref_peak} final={ref_final:.8f} post={ref_post:.8f} npts={r_ref["npts"]} wall={r_ref["wall_ms"]:.0f}ms dtmax={r_ref.get("dt_max")}', flush=True)

M = 1e-6  # margin/settle = 10x width
A0, A1 = 0.0, T0-M          # segA [0, A1]
B1 = T0+W+M                 # segB [A1, B1] fine, segC [B1, 10m] coarse
# segA coarse
rA, sA = tran_run(mkbase(EXPR, 10e-6, f'{A1}'), ['v(e)', 'v(n)'], timeout=120, tag='target')
vn_a = final(sA, 'v(n)')
# segB fine, shifted, .ic
exprB = shift_expr(EXPR, A1)
win = B1-A1
rB, sB = tran_run(['t', 'V1 s 0 DC 5', 'R1 s n 10k', 'C1 n 0 1u',
                   f'B1 e 0 V={exprB}', 'R2 e n 100k',
                   f'.ic v(n)={vn_a:.12g}', f'.tran 10n {win} 0 10n UIC'],
                  ['v(e)', 'v(n)'], timeout=120, tag='target')
vn_b = final(sB, 'v(n)')
peakB = peak(sB, 'v(e)')
# segC coarse, shifted, .ic
exprC = shift_expr(EXPR, B1)
rC, sC = tran_run(['t', 'V1 s 0 DC 5', 'R1 s n 10k', 'C1 n 0 1u',
                   f'B1 e 0 V={exprC}', 'R2 e n 100k',
                   f'.ic v(n)={vn_b:.12g}', f'.tran 10u {10e-3-B1} 0 10u UIC'],
                  ['v(e)', 'v(n)'], timeout=120, tag='target')
seg_final = final(sC, 'v(n)')
# post-event: segB tail near local t = W+M-? map global T0+W+2us -> local
seg_post = at(sB, 'v(n)', (T0+W+2e-6)-A1)
n_seg = rA['npts']+rB['npts']+rC['npts']
w_seg = rA['wall_ms']+rB['wall_ms']+rC['wall_ms']
print(f'SEG peak={peakB} (err {peakB-ref_peak}) final={seg_final:.8f} (err {(seg_final-ref_final)*1e6:.3f}uV) post-err={(seg_post-ref_post)*1e6:.3f}uV npts={n_seg} ({n_seg/r_ref["npts"]*100:.3f}%) wall={w_seg:.0f}ms', flush=True)

# PWL alternative: same event as PWL source, coarse 10us throughout
pwl = ['t', 'V1 s 0 DC 5', 'R1 s n 10k', 'C1 n 0 1u',
       f'V2 e 0 PWL(0 0 {T0-1e-9} 0 {T0} 5 {T0+W} 5 {T0+W+1e-9} 0 10m 0)',
       'R2 e n 100k', '.tran 10u 10m UIC']
rP, sP = tran_run(pwl, ['v(e)', 'v(n)'], timeout=120, tag='target')
p_peak = peak(sP, 'v(e)'); p_final = final(sP, 'v(n)'); p_post = at(sP, 'v(n)', T0+W+2e-6)
print(f'PWL peak={p_peak} (err {p_peak-ref_peak}) final-err={(p_final-ref_final)*1e6:.3f}uV post-err={(p_post-ref_post)*1e6:.3f}uV npts={rP["npts"]} ({rP["npts"]/r_ref["npts"]*100:.4f}%) wall={rP["wall_ms"]:.0f}ms', flush=True)

out = {'ref': {'peak': ref_peak, 'final': ref_final, 'npts': r_ref['npts'], 'wall_ms': r_ref['wall_ms']},
       'seg': {'peak': peakB, 'final': seg_final, 'npts': n_seg, 'wall_ms': w_seg,
               'ratio': n_seg/r_ref['npts']},
       'pwl': {'peak': p_peak, 'final': p_final, 'npts': rP['npts'], 'wall_ms': rP['wall_ms'],
               'ratio': rP['npts']/r_ref['npts']}}
os.makedirs('<repo>/experiments/exp-window/logs', exist_ok=True)
json.dump(out, open('<repo>/experiments/exp-window/logs/target_demo.json', 'w'), indent=1, default=str)
print('wrote target_demo.json')
