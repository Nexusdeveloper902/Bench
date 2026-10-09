"""Shared batch-ngspice runner for the numerical-robustness spike.

Containment: every ngspice invocation is a subprocess with a timeout; a crash
(segfault), timeout, or hang in one case cannot destroy the campaign.
"""
import subprocess, tempfile, os, re, time, math

TMP = "/tmp/opencode"
os.makedirs(TMP, exist_ok=True)

FATAL_RES = [re.compile(p, re.I) for p in (
    r'no convergence', r'timestep too small', r'singular matrix',
    r'simulation aborted', r'note:?\s*can\'t', r'\bcannot\b',
    r'no such (parameter|function|vector|device)', r'unknown (parameter|function|node)',
    r'syntax error', r'\b Infantil\b', r'Timeout',
)]
WARN_RES = [re.compile(p, re.I) for p in (
    r'\bwarning\b', r'singular', r'no convergence', r'timestep',
    r'gmin stepping failed', r'source stepping failed', r'not converged',
    r'\bfailed\b', r'\berror\b',
)]

RUSAGE_RE = {
    'total_iter': re.compile(r'Total iterations\s*=\s*(\d+)'),
    'tran_iter': re.compile(r'Transient iterations\s*=\s*(\d+)'),
    'accepted': re.compile(r'Accepted timepoints\s*=\s*(\d+)'),
    'rejected': re.compile(r'Rejected timepoints\s*=\s*(\d+)'),
    'timepoints': re.compile(r'Transient timepoints\s*=\s*(\d+)'),
}

OP_RE_TMPL = r'{vec}\s*=\s*([-\deE+.NaNnUuinfINFloc]+)'


def _parse_rusage(out):
    d = {}
    for k, rx in RUSAGE_RE.items():
        m = rx.search(out)
        d[k] = int(m.group(1)) if m else None
    return d


def run_cir(netlist_lines, timeout=30, tag="case", keep_path=False):
    """Run a netlist in ngspice batch. Returns dict with rc/stdout/wall/etc."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.cir', delete=False,
                                     dir=TMP, prefix='num_') as f:
        f.write("\n".join(netlist_lines) + "\n")
        path = f.name
    t0 = time.perf_counter()
    try:
        p = subprocess.run(['ngspice', '-b', path], capture_output=True,
                           text=True, timeout=timeout)
        wall = (time.perf_counter() - t0) * 1e3
        res = {'rc': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr,
               'out': p.stdout + p.stderr, 'wall_ms': wall, 'path': path,
               'timeout': False}
    except subprocess.TimeoutExpired as e:
        wall = (time.perf_counter() - t0) * 1e3
        res = {'rc': -99, 'stdout': '', 'stderr': f'TIMEOUT after {timeout}s',
               'out': f'TIMEOUT after {timeout}s', 'wall_ms': wall,
               'path': path, 'timeout': True}
    finally:
        if not keep_path:
            try:
                os.unlink(path)
            except Exception:
                pass
    res['fatals'] = [rx.pattern for rx in FATAL_RES if rx.search(res['out'])]
    res['warns'] = [rx.pattern for rx in WARN_RES if rx.search(res['out'])]
    res.update(_parse_rusage(res['out']))
    return res


def op_val(out, vec='v(out)'):
    m = re.search(OP_RE_TMPL.format(vec=re.escape(vec)), out)
    return m.group(1) if m else None


def op_float(out, vec='v(out)'):
    v = op_val(out, vec)
    try:
        return float(v) if v not in (None, 'nan') else None
    except (ValueError, TypeError):
        return None


def read_wrdata(path):
    """Read a wrdata ascii file -> list of rows (lists of floats)."""
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('*'):
                continue
            try:
                rows.append([float(x) for x in line.split()])
            except ValueError:
                continue
    return rows


def tran_run(base_lines, vecs, timeout=30, tag="tran"):
    """Run a transient sim, extracting full series via wrdata.

    base_lines: netlist WITHOUT .control (must contain .tran line).
    vecs: list of vector names, e.g. ['v(out)'].
    Returns (result_dict, series_dict) where series_dict[vec] = [(t, val)...].
    """
    fd, wrpath = tempfile.mkstemp(dir=TMP, prefix='numwr_', suffix='.txt')
    os.close(fd)
    ctrl = ['.control', 'run',
            f'wrdata {wrpath} ' + ' '.join(vecs),
            'rusage all', '.endc']
    net = list(base_lines) + ctrl + ['.end']
    # base_lines already end with .end? strip it to avoid dup (harmless anyway)
    net = [l for l in base_lines if l.strip().lower() != '.end'] + ctrl + ['.end']
    r = run_cir(net, timeout=timeout, tag=tag)
    series = {v: [] for v in vecs}
    try:
        rows = read_wrdata(wrpath)
        # wrdata layout: t v1 t v2 ... (time repeated per vector)
        n = len(vecs)
        for row in rows:
            if len(row) < 2 * n:
                continue
            for i, v in enumerate(vecs):
                series[v].append((row[2 * i], row[2 * i + 1]))
    except FileNotFoundError:
        r['wrdata_missing'] = True
    finally:
        try:
            os.unlink(wrpath)
        except Exception:
            pass
    # timestep stats from first vector's time column
    ts = [t for t, _ in series[vecs[0]]] if vecs and series.get(vecs[0]) else []
    dts = [b - a for a, b in zip(ts, ts[1:])] if len(ts) > 1 else []
    r['npts'] = len(ts)
    r['dt_min'] = min(dts) if dts else None
    r['dt_max'] = max(dts) if dts else None
    r['dt_ratio'] = (max(dts) / min(dts)) if dts and min(dts) > 0 else None
    if r.get('accepted'):
        r['iter_per_pt'] = (r.get('tran_iter') or 0) / max(r['accepted'], 1)
    else:
        r['iter_per_pt'] = None
    return r, series


def classify(converged, err=None, tol=None, r=None, perf_wall_ms=None):
    """Failure-class helper. Returns one of:
    reliable / questionable / failed / silent-failure."""
    if not converged:
        return 'failed'
    if err is not None and tol is not None and err > tol:
        return 'silent-failure'
    if r is not None:
        rej = r.get('rejected') or 0
        acc = r.get('accepted') or r.get('npts') or 0
        ipp = r.get('iter_per_pt')
        if acc and rej / max(acc, 1) > 0.5:
            return 'questionable'
        if ipp is not None and ipp > 30:
            return 'questionable'
        if r.get('warns'):
            # warnings alone do not doom; caller decides loud-vs-silent
            pass
    if perf_wall_ms is not None and (r or {}).get('wall_ms', 0) > perf_wall_ms:
        return 'questionable'
    return 'reliable'
