"""CIRCUIT-MODELING thread: coarse->[fine window]->coarse via .ic+UIC vs PWL-rewrite.

Ladder T1..T7. Every ngspice call = subprocess + timeout. Tmp prefix cir_ in
/tmp/opencode. Runner logic vendored from exp-safety/helper_safety.py (same
tran_run/wrdata/rusage pattern) with cir_ prefixes per thread tmp policy;
read_wrdata + run helpers imported from helper_safety where noted.
"""
import subprocess, tempfile, os, re, time, math, json, sys
import numpy as np

sys.path.insert(0, '<repo>/exp-safety')
from validator import parse_value as pv
from helper_safety import read_wrdata  # reuse

TMP = '/tmp/opencode'
os.makedirs(TMP, exist_ok=True)
LOGDIR = '<repo>/exp-window/logs'

FATAL_RES = [re.compile(p, re.I) for p in (
    r'no convergence', r'timestep too small', r'singular matrix',
    r'simulation aborted', r'DC solution failed', r'Timeout')]

def run_cir(lines, timeout=90, tag='cir'):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.cir', delete=False,
                                     dir=TMP, prefix='cir_') as f:
        f.write('\n'.join(lines) + '\n')
        path = f.name
    t0 = time.perf_counter()
    try:
        p = subprocess.run(['ngspice', '-b', path], capture_output=True,
                           text=True, timeout=timeout)
        wall = (time.perf_counter() - t0) * 1e3
        res = {'rc': p.returncode, 'out': p.stdout + p.stderr,
               'wall_ms': wall, 'timeout': False}
    except subprocess.TimeoutExpired:
        wall = (time.perf_counter() - t0) * 1e3
        res = {'rc': -99, 'out': 'TIMEOUT', 'wall_ms': wall, 'timeout': True}
    finally:
        try: os.unlink(path)
        except Exception: pass
    res['fatals'] = [rx.pattern for rx in FATAL_RES if rx.search(res['out'])]
    return res

def tran_run(base_lines, vecs, timeout=90, tag='cir'):
    fd, wrpath = tempfile.mkstemp(dir=TMP, prefix='cir_wr_', suffix='.txt')
    os.close(fd)
    ctrl = ['.control', 'run', f'wrdata {wrpath} ' + ' '.join(vecs),
            'rusage all', '.endc']
    net = [l for l in base_lines if l.strip().lower() != '.end'] + ctrl + ['.end']
    r = run_cir(net, timeout=timeout, tag=tag)
    series = {v: [] for v in vecs}
    try:
        rows = read_wrdata(wrpath)
        n = len(vecs)
        for row in rows:
            if len(row) < 2 * n: continue
            for i, v in enumerate(vecs):
                series[v].append((row[2*i], row[2*i+1]))
    except FileNotFoundError:
        r['wrdata_missing'] = True
    finally:
        try: os.unlink(wrpath)
        except Exception: pass
    r['npts'] = len(series[vecs[0]]) if vecs else 0
    return r, series

# ---------- TIME-shift utilities ----------
def fmt(t): return f'{t:.12g}'

def _sh_num(tok, t0, full):
    v = pv(tok)
    if v is None: return full
    return None  # signal: replace

def shift_expr(e, t0):
    def r_u(m):
        v = pv(m.group(1))
        return f'u(TIME-{fmt(v-t0)})' if v is not None else m.group(0)
    def r_ur(m):
        v = pv(m.group(1))
        return f'uramp(TIME-{fmt(v-t0)})' if v is not None else m.group(0)
    def r_cmp(m):
        v = pv(m.group(2))
        return f'TIME{m.group(1)}{fmt(v-t0)}' if v is not None else m.group(0)
    def r_pwl(m):
        toks = m.group(1).split(',')
        out = []
        for i, tok in enumerate(toks):
            v = pv(tok.strip())
            out.append(fmt(v-t0) if (i % 2 == 0 and v is not None) else tok.strip())
        return 'pwl(TIME,' + ','.join(out) + ')'
    e = re.sub(r'uramp\s*\(\s*TIME\s*-\s*([^\s)]+)\s*\)', r_ur, e, flags=re.I)
    e = re.sub(r'u\s*\(\s*TIME\s*-\s*([^\s)]+)\s*\)', r_u, e, flags=re.I)
    e = re.sub(r'TIME\s*([<>=]+)\s*([^\s)?,;]+)', r_cmp, e, flags=re.I)
    e = re.sub(r'pwl\s*\(\s*TIME\s*,([^)]*)\)', r_pwl, e, flags=re.I)
    return e

def stitch(segs, t0s, vecs):
    out = {v: [] for v in vecs}
    for (r, s), t0 in zip(segs, t0s):
        for v in vecs:
            for t, x in s.get(v, []):
                out[v].append((t + t0, x))
    for v in vecs:
        out[v].sort()
        ded = []
        for t, x in out[v]:
            if ded and t <= ded[-1][0]: continue
            ded.append((t, x))
        out[v] = ded
    return out

def series_arr(s, v):
    pts = sorted(s.get(v, []))
    ded = []
    for t, x in pts:
        if ded and t <= ded[-1][0]: ded[-1] = (t, x)
        else: ded.append((t, x))
    a = np.array(ded, dtype=float)
    if len(a) == 0: return np.zeros(0), np.zeros(0)
    return a[:, 0], a[:, 1]

def metrics(ref, st, vecs, ev0, ev1, T, main='v(out)'):
    tr, vr = series_arr(ref, main)
    ts, vs = series_arr(st, main)
    if len(tr) == 0 or len(ts) == 0:
        return {'empty': True}
    vi = np.interp(tr, ts, vs)
    d = np.abs(vi - vr)
    ev = (tr >= ev0 - 0.5e-6) & (tr <= ev1 + 0.5e-6)
    post = tr > ev1 + 0.5e-6
    pk = lambda m: float(np.max(d[m])) if np.any(m) else None
    iref = np.argmax(vr[ev]) if np.any(ev) else 0
    tst = np.argmax(vi[ev]) if np.any(ev) else 0
    fin = {}
    for v in vecs:
        _, rr = series_arr(ref, v); _, ss = series_arr(st, v)
        fin[v] = float(ss[-1] - rr[-1]) if len(rr) and len(ss) else None
    return {'peak_err': pk(ev), 'post_err': pk(post),
            'ref_peak': float(np.max(vr[ev])) if np.any(ev) else None,
            'st_peak': float(np.max(vi[ev])) if np.any(ev) else None,
            'final_err': fin}

def summarize(runs):
    return {'npts': int(sum(r['npts'] for r, _ in runs)),
            'wall_ms': float(sum(r['wall_ms'] for r, _ in runs)),
            'rc': [r['rc'] for r, _ in runs],
            'fatals': [f for r, _ in runs for f in r.get('fatals', [])]}

def last(s, v):
    return s[v][-1][1] if s.get(v) else None

RES = {}
def _w(x):
    return f'{x:.0f}' if isinstance(x, (int, float)) else '-'
def emit(test_id, d):
    RES[test_id] = d
    print(f"{test_id:6s} ref_pk={d.get('ref_peak')} "
          f"seg_pkerr={d.get('seg_peak_err')} post={d.get('seg_post_err')} "
          f"fin={d.get('seg_final')} npts(r/s/p)={d.get('npts_ref')}/{d.get('npts_seg')}/{d.get('npts_pwl')} "
          f"wall(r/s/p)={_w(d.get('wall_ref'))}/{_w(d.get('wall_seg'))}/{_w(d.get('wall_pwl'))}ms")

T_END = 1e-3
TMAX_F = 10e-9
TMAX_C = 1e-6
MARGIN = 20e-6  # = 20 * TMAX_C

# ================= T1 RC memory =================
def t1_devices(t0, ic_out=None, first=False, ev_center=500e-6):
    a, b = ev_center - t0, (ev_center + 100e-9) - t0
    L = ['t', 'V1 in 0 DC 5', 'R1 in out 10k',
         f'C1 out 0 100n' + (' IC=0' if first else ''),
         f'B1 blk 0 V=5*(u(TIME-{a:.12g})-u(TIME-{b:.12g}))',
         'R2 blk out 10k']
    if ic_out is not None: L.append(f'.ic v(out)={ic_out:.12g}')
    return L

def run_T1():
    vecs = ['v(out)', 'v(blk)']
    ref_net = t1_devices(0.0, first=True) + [f'.tran 10n {T_END:.12g} 0 {TMAX_F:.12g} UIC']
    r_ref, s_ref = tran_run(ref_net, vecs, tag='cir_t1ref')
    a, b = 500e-6 - MARGIN, 500e-6 + 100e-9 + MARGIN
    segs, t0s = [], [0.0, a, b]
    r1, s1 = tran_run(t1_devices(0.0, first=True) + [f'.tran {TMAX_C:.12g} {a:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_t1s1')
    r2, s2 = tran_run(t1_devices(a, ic_out=last(s1, 'v(out)')) + [f'.tran {TMAX_F:.12g} {b-a:.12g} 0 {TMAX_F:.12g} UIC'], vecs, tag='cir_t1s2')
    r3, s3 = tran_run(t1_devices(b, ic_out=last(s2, 'v(out)')) + [f'.tran {TMAX_C:.12g} {T_END-b:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_t1s3')
    segs = [(r1, s1), (r2, s2), (r3, s3)]
    st = stitch(segs, t0s, vecs)
    m = metrics(s_ref, st, vecs, 500e-6, 500.1e-6, T_END)
    pwl = ['t', 'V1 in 0 DC 5', 'R1 in out 10k', 'C1 out 0 100n IC=0',
           'Vblk blk 0 PWL(0 0 499.999e-6 0 500e-6 5 500.1e-6 5 500.101e-6 0 1m 0)',
           'R2 blk out 10k', f'.tran {TMAX_C:.12g} {T_END:.12g} 0 {TMAX_C:.12g} UIC']
    r_p, s_p = tran_run(pwl, vecs, tag='cir_t1pwl')
    mp = metrics(s_ref, dict(s_p), vecs, 500e-6, 500.1e-6, T_END)
    emit('T1', {'seg_peak_err': m['peak_err'], 'seg_post_err': m['post_err'],
                'seg_final': m['final_err'], 'ref_peak': m['ref_peak'],
                'pwl_peak_err': mp['peak_err'], 'pwl_post_err': mp['post_err'],
                'pwl_final': mp['final_err'],
                'npts_ref': r_ref['npts'], 'npts_seg': summarize(segs)['npts'],
                'npts_pwl': r_p['npts'], 'wall_ref': r_ref['wall_ms'],
                'wall_seg': summarize(segs)['wall_ms'], 'wall_pwl': r_p['wall_ms'],
                'seg_rc': summarize(segs)['rc'], 'ref_rc': r_ref['rc']})

# ================= T2a RL step+blip =================
def t2a_devices(t0, ic_v=None, ic_i=None, first=False):
    a, b = 500e-6 - t0, 500.1e-6 - t0
    L = ['t', 'V1 in 0 DC 5', 'R1 in out 100',
         f'L1 out 0 10mH' + (' IC=0' if first else f' IC={ic_i:.12g}'),
         f'B1 blk 0 V=5*(u(TIME-{a:.12g})-u(TIME-{b:.12g}))', 'R2 blk out 1k']
    if ic_v is not None: L.append(f'.ic v(out)={ic_v:.12g}')
    return L

def run_T2a():
    vecs = ['v(out)', 'i(L1)', 'v(blk)']
    r_ref, s_ref = tran_run(t2a_devices(0.0, first=True) + [f'.tran 10n {T_END:.12g} 0 {TMAX_F:.12g} UIC'], vecs, tag='cir_t2aref')
    a, b = 500e-6 - MARGIN, 500e-6 + 100e-9 + MARGIN
    r1, s1 = tran_run(t2a_devices(0.0, first=True) + [f'.tran {TMAX_C:.12g} {a:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_t2as1')
    r2, s2 = tran_run(t2a_devices(a, ic_v=last(s1, 'v(out)'), ic_i=last(s1, 'i(L1)')) + [f'.tran {TMAX_F:.12g} {b-a:.12g} 0 {TMAX_F:.12g} UIC'], vecs, tag='cir_t2as2')
    r3, s3 = tran_run(t2a_devices(b, ic_v=last(s2, 'v(out)'), ic_i=last(s2, 'i(L1)')) + [f'.tran {TMAX_C:.12g} {T_END-b:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_t2as3')
    segs = [(r1, s1), (r2, s2), (r3, s3)]
    st = stitch(segs, [0.0, a, b], vecs)
    m = metrics(s_ref, st, vecs, 500e-6, 500.1e-6, T_END)
    mi = metrics(s_ref, st, vecs, 500e-6, 500.1e-6, T_END, main='i(L1)')
    pwl = ['t', 'V1 in 0 DC 5', 'R1 in out 100', 'L1 out 0 10mH IC=0',
           'Vblk blk 0 PWL(0 0 499.999e-6 0 500e-6 5 500.1e-6 5 500.101e-6 0 1m 0)',
           'R2 blk out 1k', f'.tran {TMAX_C:.12g} {T_END:.12g} 0 {TMAX_C:.12g} UIC']
    r_p, s_p = tran_run(pwl, vecs, tag='cir_t2apwl')
    mp = metrics(s_ref, dict(s_p), vecs, 500e-6, 500.1e-6, T_END)
    emit('T2a', {'seg_peak_err': m['peak_err'], 'seg_post_err': m['post_err'],
                 'seg_final': m['final_err'], 'seg_i_peak_err': mi['peak_err'],
                 'seg_i_final': mi['final_err'], 'ref_peak': m['ref_peak'],
                 'pwl_peak_err': mp['peak_err'], 'pwl_post_err': mp['post_err'],
                 'pwl_final': mp['final_err'],
                 'npts_ref': r_ref['npts'], 'npts_seg': summarize(segs)['npts'],
                 'npts_pwl': r_p['npts'], 'wall_ref': r_ref['wall_ms'],
                 'wall_seg': summarize(segs)['wall_ms'], 'wall_pwl': r_p['wall_ms']})

# ================= T2b flyback =================
def t2b_devices(t0, ic_v=None, ic_i=None, first=False):
    a, b = 500e-6 - t0, 500.1e-6 - t0
    # B current source: 10mA until 500us, 100ns ramp to 0 (no breakpoints [E]).
    # L pre-charged (tau=L/R=100ns); at turn-off 10mA freewheels into 1k -> -10V spike.
    expr = f'10m*(1-(uramp(TIME-{a:.12g})-uramp(TIME-{b:.12g}))/100n)'
    L = ['t', f'B1 0 n1 I={expr}', f'L1 n1 0 100u' + (' IC=0' if first else f' IC={ic_i:.12g}'),
         'R1 n1 0 1k']
    if ic_v is not None: L.append(f'.ic v(n1)={ic_v:.12g}')
    return L

def run_T2b():
    vecs = ['v(n1)', 'i(L1)']
    r_ref, s_ref = tran_run(t2b_devices(0.0, first=True) + [f'.tran 10n {T_END:.12g} 0 {TMAX_F:.12g} UIC'], vecs, tag='cir_t2bref')
    a, b = 500e-6 - MARGIN, 500e-6 + 100e-9 + MARGIN
    r1, s1 = tran_run(t2b_devices(0.0, first=True) + [f'.tran {TMAX_C:.12g} {a:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_t2bs1')
    r2, s2 = tran_run(t2b_devices(a, ic_v=last(s1, 'v(n1)'), ic_i=last(s1, 'i(L1)')) + [f'.tran {TMAX_F:.12g} {b-a:.12g} 0 {TMAX_F:.12g} UIC'], vecs, tag='cir_t2bs2')
    r3, s3 = tran_run(t2b_devices(b, ic_v=last(s2, 'v(n1)'), ic_i=last(s2, 'i(L1)')) + [f'.tran {TMAX_C:.12g} {T_END-b:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_t2bs3')
    segs = [(r1, s1), (r2, s2), (r3, s3)]
    st = stitch(segs, [0.0, a, b], vecs)
    m = metrics(s_ref, st, vecs, 500e-6, 500.2e-6, T_END, main='v(n1)')
    mi = metrics(s_ref, st, vecs, 500e-6, 500.2e-6, T_END, main='i(L1)')
    # PWL rewrite: current source with 100ns fall + 20ns interior kinks, coarse tmax
    pts = ['0 10m', '499.999e-6 10m', '500e-6 10m'] + \
          [f'{500e-6+k*20e-9:.12g} {10e-3*(1-k/5):.6g}' for k in range(1, 5)] + \
          ['500.1e-6 0', '1m 0']
    pwl = ['t', f'I1 0 n1 PWL(' + ' '.join(pts) + ')', 'L1 n1 0 100u IC=0', 'R1 n1 0 1k',
           f'.tran {TMAX_C:.12g} {T_END:.12g} 0 {TMAX_C:.12g} UIC']
    r_p, s_p = tran_run(pwl, vecs, tag='cir_t2bpwl')
    mp = metrics(s_ref, dict(s_p), vecs, 500e-6, 500.2e-6, T_END, main='v(n1)')
    trw = np.array([t for t, _ in s_ref['v(n1)']])
    vrw = np.array([v for _, v in s_ref['v(n1)']])
    win = (trw >= 499.5e-6) & (trw <= 500.7e-6)
    ref_min = float(np.min(vrw[win])) if np.any(win) else None
    emit('T2b', {'seg_peak_err': m['peak_err'], 'seg_post_err': m['post_err'],
                 'seg_final': m['final_err'], 'seg_i_peak_err': mi['peak_err'],
                 'seg_i_final': mi['final_err'], 'ref_peak': m['ref_peak'],
                 'ref_min_spike_V': ref_min,
                 'pwl_peak_err': mp['peak_err'], 'pwl_post_err': mp['post_err'],
                 'pwl_final': mp['final_err'],
                 'npts_ref': r_ref['npts'], 'npts_seg': summarize(segs)['npts'],
                 'npts_pwl': r_p['npts'], 'wall_ref': r_ref['wall_ms'],
                 'wall_seg': summarize(segs)['wall_ms'], 'wall_pwl': r_p['wall_ms']})

# ================= T3 diode clipper =================
F_SINE, A_SINE = 1000.0, 2.0
T_KNEE = math.asin(0.7 / A_SINE) / (2 * math.pi * F_SINE)  # ~56.8us rising
def sine_pwl_local(t0, t1, n=61):
    pts = []
    for i in range(n):
        t = t0 + (t1 - t0) * i / (n - 1)
        pts.append(f'{t-t0:.12g} {A_SINE*math.sin(2*math.pi*F_SINE*t):.12g}')
    return 'V1 in 0 PWL(' + ' '.join(pts) + ')'

def run_T3():
    vecs = ['v(out)', 'v(in)']
    EA, EB = T_KNEE, T_KNEE + 100e-9
    wa, wb = EA - MARGIN, EB + MARGIN
    def dev(t0, ic=None, first=False, dur=1.0):
        a, b = EA - t0, EB - t0
        if first:
            src = f'V1 in 0 SIN(0 {A_SINE} {F_SINE})'
        else:
            pts = []
            n = max(61, int(dur / 1e-6) + 1)
            for i in range(n):
                t = t0 + dur * i / (n - 1)
                pts.append(f'{t-t0:.12g} {A_SINE*math.sin(2*math.pi*F_SINE*t):.12g}')
            src = 'V1 in 0 PWL(' + ' '.join(pts) + ')'
        L = ['t', src, 'R1 in out 1k', 'D1 out 0 DMOD',
             '.model DMOD D(IS=1e-14 N=1)',
             f'C1 out 0 100p' + (' IC=0' if first else ''),
             f'B1 blk 0 V=5*(u(TIME-{a:.12g})-u(TIME-{b:.12g}))', 'R2 blk out 10k']
        if ic is not None: L.append(f'.ic v(out)={ic:.12g}')
        return L
    r_ref, s_ref = tran_run(dev(0.0, first=True, dur=T_END) + [f'.tran 10n {T_END:.12g} 0 {TMAX_F:.12g} UIC'], vecs, tag='cir_t3ref')
    r1, s1 = tran_run(dev(0.0, first=True, dur=wa) + [f'.tran {TMAX_C:.12g} {wa:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_t3s1')
    r2, s2 = tran_run(dev(wa, ic=last(s1, 'v(out)'), dur=wb - wa) + [f'.tran {TMAX_F:.12g} {wb-wa:.12g} 0 {TMAX_F:.12g} UIC'], vecs, tag='cir_t3s2')
    r3, s3 = tran_run(dev(wb, ic=last(s2, 'v(out)'), dur=T_END - wb) + [f'.tran {TMAX_C:.12g} {T_END-wb:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_t3s3')
    segs = [(r1, s1), (r2, s2), (r3, s3)]
    st = stitch(segs, [0.0, wa, wb], vecs)
    m = metrics(s_ref, st, vecs, EA, EB, T_END)
    # knee-region error
    tr, vr = series_arr(s_ref, 'v(out)')
    ts, vs = series_arr(st, 'v(out)')
    vi = np.interp(tr, ts, vs)
    knee = np.abs(vr - 0.7) < 0.25
    knee_err = float(np.max(np.abs(vi[knee] - vr[knee]))) if np.any(knee) else None
    pwl = ['t', f'V1 in 0 SIN(0 {A_SINE} {F_SINE})', 'R1 in out 1k', 'D1 out 0 DMOD',
           '.model DMOD D(IS=1e-14 N=1)', 'C1 out 0 100p IC=0',
           f'Vblk blk 0 PWL(0 0 {EA-1e-9:.12g} 0 {EA:.12g} 5 {EB:.12g} 5 {EB+1e-9:.12g} 0 1m 0)',
           'R2 blk out 10k', f'.tran {TMAX_C:.12g} {T_END:.12g} 0 {TMAX_C:.12g} UIC']
    r_p, s_p = tran_run(pwl, vecs, tag='cir_t3pwl')
    mp = metrics(s_ref, dict(s_p), vecs, EA, EB, T_END)
    _, vpr = series_arr(dict(s_p), 'v(out)')
    tpr, _ = series_arr(dict(s_p), 'v(out)')
    vpri = np.interp(tr, tpr, vpr)
    knee_pwl = float(np.max(np.abs(vpri[knee] - vr[knee]))) if np.any(knee) else None
    # edge-aware shape metrics (ideal-step straddle inflates pointwise diff)
    def edge_metrics(tA, vA, tB, vB):
        mA = (tA >= EA - 1e-6) & (tA <= EB + 1e-6)
        pkA = float(np.max(vA[mA])) if np.any(mA) else None
        mB = (tB >= EA - 1e-6) & (tB <= EB + 1e-6)
        pkB = float(np.max(vB[mB])) if np.any(mB) else None
        def xing(t, v, lev):
            for (t0, v0), (t1, v1) in zip(zip(t, v), zip(t[1:], v[1:])):
                if v0 < lev <= v1 and EA - 1e-6 < t1 < EB + 1e-6:
                    return t0 + (lev - v0) * (t1 - t0) / (v1 - v0)
            return None
        xA, xB = xing(tA, vA, 0.55), xing(tB, vB, 0.55)
        out = (tB >= EA - 0.3e-6) & (tB <= EB + 0.3e-6)
        km = (np.abs(vB - 0.7) < 0.25) & (~out)
        kerr = float(np.max(np.abs(vB[km] - np.interp(tB[km], tA, vA)))) if np.any(km) else None
        return pkA, pkB, xA, xB, kerr
    tsA, vsA = series_arr(st, 'v(out)')
    pk_r, pk_s, x_r, x_s, k_s = edge_metrics(tr, vr, tsA, vsA)
    _, pk_p, _, x_p, k_p = edge_metrics(tr, vr, tpr, vpr)
    emit('T3', {'seg_peak_err': m['peak_err'], 'seg_post_err': m['post_err'],
                'seg_final': m['final_err'], 'knee_err': knee_err,
                'knee_pwl_err': knee_pwl, 'ref_peak': m['ref_peak'],
                't_knee_us': T_KNEE * 1e6,
                'edge_pk_ref': pk_r, 'edge_pk_seg': pk_s, 'edge_pk_pwl': pk_p,
                'edge_xing_ref': x_r, 'edge_xing_seg': x_s, 'edge_xing_pwl': x_p,
                'knee_masked_seg': k_s, 'knee_masked_pwl': k_p,
                'pwl_peak_err': mp['peak_err'], 'pwl_post_err': mp['post_err'],
                'pwl_final': mp['final_err'],
                'npts_ref': r_ref['npts'], 'npts_seg': summarize(segs)['npts'],
                'npts_pwl': r_p['npts'], 'wall_ref': r_ref['wall_ms'],
                'wall_seg': summarize(segs)['wall_ms'], 'wall_pwl': r_p['wall_ms']})

# ================= T4 E-amp feedback =================
def t4_devices(t0, ic_out=None, first=False):
    a, b = 500e-6 - t0, 500.1e-6 - t0
    # series B injection: inp = 2.5 + blip exactly (no divider loading); gain-2 loop -> out 5V (+10V blip)
    L = ['t', 'V1 inp0 0 DC 2.5',
         f'B1 inp inp0 V=5*(u(TIME-{a:.12g})-u(TIME-{b:.12g}))',
         'E1 out 0 inp fb 100000', 'R1 out fb 1k', 'R2 fb 0 1k',
         f'C1 out 0 1n' + (' IC=5' if first else '')]
    if ic_out is not None: L.append(f'.ic v(out)={ic_out:.12g}')
    return L

def run_T4():
    vecs = ['v(out)', 'v(inp)']
    r_ref, s_ref = tran_run(t4_devices(0.0, first=True) + [f'.tran 10n {T_END:.12g} 0 {TMAX_F:.12g} UIC'], vecs, tag='cir_t4ref')
    a, b = 500e-6 - MARGIN, 500e-6 + 100e-9 + MARGIN
    r1, s1 = tran_run(t4_devices(0.0, first=True) + [f'.tran {TMAX_C:.12g} {a:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_t4s1')
    r2, s2 = tran_run(t4_devices(a, ic_out=last(s1, 'v(out)')) + [f'.tran {TMAX_F:.12g} {b-a:.12g} 0 {TMAX_F:.12g} UIC'], vecs, tag='cir_t4s2')
    r3, s3 = tran_run(t4_devices(b, ic_out=last(s2, 'v(out)')) + [f'.tran {TMAX_C:.12g} {T_END-b:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_t4s3')
    segs = [(r1, s1), (r2, s2), (r3, s3)]
    st = stitch(segs, [0.0, a, b], vecs)
    m = metrics(s_ref, st, vecs, 500e-6, 500.1e-6, T_END)
    pwl = ['t', 'V1 inp0 0 DC 2.5',
           'Vblk inp inp0 PWL(0 0 499.999e-6 0 500e-6 5 500.1e-6 5 500.101e-6 0 1m 0)',
           'E1 out 0 inp fb 100000', 'R1 out fb 1k', 'R2 fb 0 1k',
           'C1 out 0 1n IC=5', f'.tran {TMAX_C:.12g} {T_END:.12g} 0 {TMAX_C:.12g} UIC']
    r_p, s_p = tran_run(pwl, vecs, tag='cir_t4pwl')
    mp = metrics(s_ref, dict(s_p), vecs, 500e-6, 500.1e-6, T_END)
    emit('T4', {'seg_peak_err': m['peak_err'], 'seg_post_err': m['post_err'],
                'seg_final': m['final_err'], 'ref_peak': m['ref_peak'],
                'pwl_peak_err': mp['peak_err'], 'pwl_post_err': mp['post_err'],
                'pwl_final': mp['final_err'],
                'npts_ref': r_ref['npts'], 'npts_seg': summarize(segs)['npts'],
                'npts_pwl': r_p['npts'], 'wall_ref': r_ref['wall_ms'],
                'wall_seg': summarize(segs)['wall_ms'], 'wall_pwl': r_p['wall_ms']})

# ================= T5 state-dependent timing =================
def rc_cross(tmax):
    net = ['t', 'V1 in 0 DC 5', 'R1 in out 10k', 'C1 out 0 100n IC=0',
           f'.tran {tmax:.12g} {T_END:.12g} 0 {tmax:.12g} UIC']
    r, s = tran_run(net, ['v(out)'], tag='cir_t5pre')
    data = s['v(out)']
    tc = None
    for (t0, v0), (t1, v1) in zip(data, data[1:]):
        if v0 < 2.5 <= v1:
            tc = t0 + (2.5 - v0) * (t1 - t0) / (v1 - v0)
            break
    return tc, r

def run_T5():
    t_true = 1e-3 * math.log(2)  # 693.147us
    scans = {}
    for tm in (5e-6, 2e-6, 1e-6, 200e-9):
        tc, r = rc_cross(tm)
        scans[str(tm)] = {'t_cross': tc,
                          'err_s': (tc - t_true) if tc else None,
                          'npts': r['npts'], 'wall_ms': r['wall_ms']}
        print(f'  prescan tmax={tm:.12g}: tcross={tc}, err={scans[str(tm)]["err_s"]}')
    t_pred = scans['1e-06']['t_cross']
    E_true, E_pred = t_true + 5e-6, t_pred + 5e-6
    misplace = E_pred - E_true
    vecs = ['v(out)', 'v(blk)']
    r_ref, s_ref = tran_run(t1_devices(0.0, first=True, ev_center=E_true) + [f'.tran 10n {T_END:.12g} 0 {TMAX_F:.12g} UIC'], vecs, tag='cir_t5ref')
    a, b = E_pred - MARGIN, E_pred + 100e-9 + MARGIN
    in_window = (E_true - 100e-9 >= a) and (E_true + 200e-9 <= b)
    r1, s1 = tran_run(t1_devices(0.0, first=True, ev_center=E_pred) + [f'.tran {TMAX_C:.12g} {a:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_t5s1')
    r2, s2 = tran_run(t1_devices(a, ic_out=last(s1, 'v(out)'), ev_center=E_pred) + [f'.tran {TMAX_F:.12g} {b-a:.12g} 0 {TMAX_F:.12g} UIC'], vecs, tag='cir_t5s2')
    r3, s3 = tran_run(t1_devices(b, ic_out=last(s2, 'v(out)'), ev_center=E_pred) + [f'.tran {TMAX_C:.12g} {T_END-b:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_t5s3')
    segs = [(r1, s1), (r2, s2), (r3, s3)]
    st = stitch(segs, [0.0, a, b], vecs)
    m = metrics(s_ref, st, vecs, E_true, E_true + 100e-9, T_END)
    pwl = ['t', 'V1 in 0 DC 5', 'R1 in out 10k', 'C1 out 0 100n IC=0',
           f'Vblk blk 0 PWL(0 0 {E_pred-1e-9:.12g} 0 {E_pred:.12g} 5 {E_pred+100e-9:.12g} 5 {E_pred+101e-9:.12g} 0 1m 0)',
           'R2 blk out 10k', f'.tran {TMAX_C:.12g} {T_END:.12g} 0 {TMAX_C:.12g} UIC']
    r_p, s_p = tran_run(pwl, vecs, tag='cir_t5pwl')
    mp = metrics(s_ref, dict(s_p), vecs, E_true, E_true + 100e-9, T_END)
    emit('T5', {'t_true_us': t_true * 1e6, 't_pred_us': t_pred * 1e6,
                'misplace_us': misplace * 1e6, 'in_window': in_window,
                'margin_us': MARGIN * 1e6, 'scans': scans,
                'seg_peak_err': m['peak_err'], 'seg_post_err': m['post_err'],
                'seg_final': m['final_err'], 'ref_peak': m['ref_peak'],
                'pwl_peak_err': mp['peak_err'], 'pwl_post_err': mp['post_err'],
                'pwl_final': mp['final_err'],
                'npts_ref': r_ref['npts'], 'npts_seg': summarize(segs)['npts'],
                'npts_pwl': r_p['npts'], 'wall_ref': r_ref['wall_ms'],
                'wall_seg': summarize(segs)['wall_ms'], 'wall_pwl': r_p['wall_ms']})

# ================= T6 multiple events =================
def run_T6():
    E1A, E1B = 500e-6, 500.1e-6
    E2A, E2B = 550e-6, 550.1e-6
    E3A, E3B, HV = 700e-6, 701e-6, 3.0
    vecs = ['v(out)', 'v(blk)']
    def dev(t0, ic=None, first=False):
        a1, b1 = E1A - t0, E1B - t0
        a2, b2 = E2A - t0, E2B - t0
        a3, b3 = E3A - t0, E3B - t0
        L = ['t', 'V1 in 0 DC 5', 'R1 in out 10k',
             f'C1 out 0 100n' + (' IC=0' if first else ''),
             f'B1 blk 0 V=5*(u(TIME-{a1:.12g})-u(TIME-{b1:.12g}))+5*(u(TIME-{a2:.12g})-u(TIME-{b2:.12g}))+{HV}*sin(3.14159265*(TIME-{a3:.12g})/1u)*(u(TIME-{a3:.12g})-u(TIME-{b3:.12g}))',
             'R2 blk out 10k']
        if ic is not None: L.append(f'.ic v(out)={ic:.12g}')
        return L
    r_ref, s_ref = tran_run(dev(0.0, first=True) + [f'.tran 10n {T_END:.12g} 0 {TMAX_F:.12g} UIC'], vecs, tag='cir_t6ref')
    # per-event windows
    bounds = [(E1A - MARGIN, E1B + MARGIN), (E2A - MARGIN, E2B + MARGIN), (E3A - MARGIN, E3B + MARGIN)]
    edges = [0.0] + [x for w in bounds for x in w] + [T_END]
    segs, t0s = [], []
    prev_ic = None
    for i in range(len(edges) - 1):
        t0, t1 = edges[i], edges[i + 1]
        fine = any(abs(t0 - w[0]) < 1e-15 for w in bounds)
        tm = TMAX_F if fine else TMAX_C
        first = (i == 0)
        r, s = tran_run(dev(t0, ic=None if first else prev_ic, first=first) + [f'.tran {tm:.12g} {t1-t0:.12g} 0 {tm:.12g} UIC'], vecs, tag=f'cir_t6s{i}')
        prev_ic = last(s, 'v(out)')
        segs.append((r, s)); t0s.append(t0)
    st = stitch(segs, t0s, vecs)
    mall = metrics(s_ref, st, vecs, E1A, E3B, T_END)
    m1 = metrics(s_ref, st, vecs, E1A, E1B, T_END)
    m3 = metrics(s_ref, st, vecs, E3A, E3B, T_END)
    # merged window
    ma, mb = E1A - MARGIN, E3B + MARGIN
    rA, sA = tran_run(dev(0.0, first=True) + [f'.tran {TMAX_C:.12g} {ma:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_t6m0')
    rB, sB = tran_run(dev(ma, ic=last(sA, 'v(out)')) + [f'.tran {TMAX_F:.12g} {mb-ma:.12g} 0 {TMAX_F:.12g} UIC'], vecs, tag='cir_t6m1')
    rC, sC = tran_run(dev(mb, ic=last(sB, 'v(out)')) + [f'.tran {TMAX_C:.12g} {T_END-mb:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_t6m2')
    mseg = [(rA, sA), (rB, sB), (rC, sC)]
    stm = stitch(mseg, [0.0, ma, mb], vecs)
    mm = metrics(s_ref, stm, vecs, E1A, E3B, T_END)
    # PWL rewrite (coarse full)
    hump = ' '.join(f'{E3A+k*100e-9:.12g} {HV*math.sin(math.pi*k/10):.6g}' for k in range(11))
    pwl = ['t', 'V1 in 0 DC 5', 'R1 in out 10k', 'C1 out 0 100n IC=0',
           f'Vblk blk 0 PWL(0 0 499.999e-6 0 500e-6 5 500.1e-6 5 500.101e-6 0 549.999e-6 0 550e-6 5 550.1e-6 5 550.101e-6 0 {hump} 701.001e-6 0 1m 0)',
           'R2 blk out 10k', f'.tran {TMAX_C:.12g} {T_END:.12g} 0 {TMAX_C:.12g} UIC']
    r_p, s_p = tran_run(pwl, vecs, tag='cir_t6pwl')
    mp = metrics(s_ref, dict(s_p), vecs, E1A, E3B, T_END)
    emit('T6', {'per_peak_err': m1['peak_err'], 'per_hump_err': m3['peak_err'],
                'per_post_err': mall['post_err'], 'per_final': mall['final_err'],
                'merged_peak_err': mm['peak_err'], 'merged_post_err': mm['post_err'],
                'merged_final': mm['final_err'], 'ref_peak': mall['ref_peak'],
                'pwl_peak_err': mp['peak_err'], 'pwl_post_err': mp['post_err'],
                'pwl_final': mp['final_err'],
                'npts_ref': r_ref['npts'], 'npts_seg': summarize(segs)['npts'],
                'npts_merged': summarize(mseg)['npts'], 'npts_pwl': r_p['npts'],
                'wall_ref': r_ref['wall_ms'], 'wall_seg': summarize(segs)['wall_ms'],
                'wall_merged': summarize(mseg)['wall_ms'], 'wall_pwl': r_p['wall_ms']})

# ================= T7 boundary event =================
def run_T7():
    EA, EB = 500.05e-6, 500.15e-6  # 50ns after the 500us boundary
    vecs = ['v(out)', 'v(blk)']
    r_ref, s_ref = tran_run(t1_devices(0.0, first=True, ev_center=EA) + [f'.tran 10n {T_END:.12g} 0 {TMAX_F:.12g} UIC'], vecs, tag='cir_t7ref')
    # margin-0 split at boundary, re-coarsen 5us after event end
    B0 = 500e-6
    B2 = EB + 5e-6
    r1, s1 = tran_run(t1_devices(0.0, first=True, ev_center=EA) + [f'.tran {TMAX_C:.12g} {B0:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_t7s1')
    r2, s2 = tran_run(t1_devices(B0, ic_out=last(s1, 'v(out)'), ev_center=EA) + [f'.tran {TMAX_F:.12g} {B2-B0:.12g} 0 {TMAX_F:.12g} UIC'], vecs, tag='cir_t7s2')
    r2b, s2b = tran_run(t1_devices(B2, ic_out=last(s2, 'v(out)'), ev_center=EA) + [f'.tran {TMAX_C:.12g} {T_END-B2:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_t7s3')
    segs = [(r1, s1), (r2, s2), (r2b, s2b)]
    st = stitch(segs, [0.0, B0, B2], vecs)
    m = metrics(s_ref, st, vecs, EA, EB, T_END)
    # margin-5us control
    a, b = EA - 5e-6, EB + 5e-6
    c1, d1 = tran_run(t1_devices(0.0, first=True, ev_center=EA) + [f'.tran {TMAX_C:.12g} {a:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_t7c1')
    c2, d2 = tran_run(t1_devices(a, ic_out=last(d1, 'v(out)'), ev_center=EA) + [f'.tran {TMAX_F:.12g} {b-a:.12g} 0 {TMAX_F:.12g} UIC'], vecs, tag='cir_t7c2')
    c3, d3 = tran_run(t1_devices(b, ic_out=last(d2, 'v(out)'), ev_center=EA) + [f'.tran {TMAX_C:.12g} {T_END-b:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_t7c3')
    cseg = [(c1, d1), (c2, d2), (c3, d3)]
    cst = stitch(cseg, [0.0, a, b], vecs)
    mc = metrics(s_ref, cst, vecs, EA, EB, T_END)
    pwl = ['t', 'V1 in 0 DC 5', 'R1 in out 10k', 'C1 out 0 100n IC=0',
           f'Vblk blk 0 PWL(0 0 {EA-1e-9:.12g} 0 {EA:.12g} 5 {EB:.12g} 5 {EB+1e-9:.12g} 0 1m 0)',
           'R2 blk out 10k', f'.tran {TMAX_C:.12g} {T_END:.12g} 0 {TMAX_C:.12g} UIC']
    r_p, s_p = tran_run(pwl, vecs, tag='cir_t7pwl')
    mp = metrics(s_ref, dict(s_p), vecs, EA, EB, T_END)
    emit('T7', {'margin0_peak_err': m['peak_err'], 'margin0_post_err': m['post_err'],
                'margin0_final': m['final_err'], 'margin5_peak_err': mc['peak_err'],
                'margin5_post_err': mc['post_err'], 'margin5_final': mc['final_err'],
                'ref_peak': m['ref_peak'],
                'pwl_peak_err': mp['peak_err'], 'pwl_post_err': mp['post_err'],
                'pwl_final': mp['final_err'],
                'npts_ref': r_ref['npts'],
                'npts_seg': summarize(segs)['npts'], 'npts_ctrl': summarize(cseg)['npts'],
                'npts_pwl': r_p['npts'], 'wall_ref': r_ref['wall_ms'],
                'wall_seg': summarize(segs)['wall_ms'], 'wall_pwl': r_p['wall_ms']})

# ================= AUTO auto-derived windows via bsource.extract_bandwidth =================
def run_AUTO():
    sys.path.insert(0, '<repo>/exp-safety')
    from bsource import extract_bandwidth
    E1A, E1B = 500e-6, 500.1e-6
    expr = (f'5*(u(TIME-{E1A:.12g})-u(TIME-{E1B:.12g}))+5*(u(TIME-0.00055)-u(TIME-0.0005501))'
            f'+3.0*sin(3.14159265*(TIME-0.0007)/1u)*(u(TIME-0.0007)-u(TIME-0.000701))')
    bw = extract_bandwidth(expr)
    w_min = min(bw['widths']) if bw['widths'] else None
    margin = max(20 * TMAX_C, 5 * w_min) if w_min else 20 * TMAX_C
    evs = sorted(set(bw['events']))
    clusters, cur = [], [evs[0]]
    for e in evs[1:]:
        if e - cur[-1] < 2 * margin: cur.append(e)
        else: clusters.append(cur); cur = [e]
    clusters.append(cur)
    auto_windows = [(c[0] - margin, c[-1] + margin) for c in clusters]
    manual = [(E1A - MARGIN, E1B + MARGIN), (550e-6 - MARGIN, 550.1e-6 + MARGIN),
              (700e-6 - MARGIN, 701e-6 + MARGIN)]
    match = (len(auto_windows) == len(manual) and
             all(abs(a - m) < 1e-12 and abs(b - n) < 1e-12
                 for (a, b), (m, n) in zip(auto_windows, manual)))
    # execute first auto window as a real segmented pair on the T1-style circuit
    vecs = ['v(out)', 'v(blk)']
    wa, wb = auto_windows[0]
    r_ref, s_ref = tran_run(t1_devices(0.0, first=True) + [f'.tran 10n {T_END:.12g} 0 {TMAX_F:.12g} UIC'], vecs, tag='cir_autoref')
    r1, s1 = tran_run(t1_devices(0.0, first=True) + [f'.tran {TMAX_C:.12g} {wa:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_auto1')
    r2, s2 = tran_run(t1_devices(wa, ic_out=last(s1, 'v(out)')) + [f'.tran {TMAX_F:.12g} {wb-wa:.12g} 0 {TMAX_F:.12g} UIC'], vecs, tag='cir_auto2')
    r3, s3 = tran_run(t1_devices(wb, ic_out=last(s2, 'v(out)')) + [f'.tran {TMAX_C:.12g} {T_END-wb:.12g} 0 {TMAX_C:.12g} UIC'], vecs, tag='cir_auto3')
    segs = [(r1, s1), (r2, s2), (r3, s3)]
    st = stitch(segs, [0.0, wa, wb], vecs)
    m = metrics(s_ref, st, vecs, 500e-6, 500.1e-6, T_END)
    emit('AUTO', {'bw_widths': bw['widths'], 'bw_events': bw['events'],
                  'bw_periods': bw['periods'], 'unknown': bw['unknown_time'],
                  'w_min_us': w_min * 1e6 if w_min else None,
                  'margin_us': margin * 1e6, 'auto_windows_us': [[a * 1e6, b * 1e6] for a, b in auto_windows],
                  'matches_manual': match,
                  'seg_peak_err': m['peak_err'], 'seg_post_err': m['post_err'],
                  'seg_final': m['final_err'], 'ref_peak': m['ref_peak'],
                  'npts_ref': r_ref['npts'], 'npts_seg': summarize(segs)['npts'],
                  'npts_pwl': None, 'wall_ref': r_ref['wall_ms'],
                  'wall_seg': summarize(segs)['wall_ms'], 'wall_pwl': None})
if __name__ == '__main__':
    t0 = time.perf_counter()
    which = sys.argv[1:] or ['T1', 'T2a', 'T2b', 'T3', 'T4', 'T5', 'T6', 'T7', 'AUTO']
    FN = {'T1': run_T1, 'T2a': run_T2a, 'T2b': run_T2b, 'T3': run_T3,
          'T4': run_T4, 'T5': run_T5, 'T6': run_T6, 'T7': run_T7, 'AUTO': run_AUTO}
    for w in which:
        try:
            FN[w]()
        except Exception as e:
            import traceback; traceback.print_exc()
            RES[w] = {'harness_error': repr(e)}
    with open(os.path.join(LOGDIR, 'agent_circuits.json'), 'w') as f:
        json.dump({'tests': RES}, f, indent=1, default=str)
    print(f'TOTAL {time.perf_counter()-t0:.1f}s -> agent_circuits.json')