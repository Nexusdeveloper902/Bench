"""Contained ngspice batch runner for the safety-boundary spike.

Every simulation runs in a subprocess with a timeout so a crash, hang, or
pathological case cannot terminate the campaign. Also provides embedded
libngspice cross-checks (in-process, used sparingly) and shared log-scan
regexes + rusage/wrdata instrumentation.
"""
import subprocess
import tempfile
import os
import re
import time

TMP = "/tmp/opencode"
os.makedirs(TMP, exist_ok=True)

FATAL_RES = [re.compile(p, re.I) for p in (
    r'no convergence', r'timestep too small', r'singular matrix',
    r'simulation aborted', r"note:?\s*can\'t", r'\bcannot\b',
    r'no such (parameter|function|vector|device)', r'unknown (parameter|function|node)',
    r'syntax error', r'Timeout', r'DC solution failed',
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


def _parse_rusage(out):
    d = {}
    for k, rx in RUSAGE_RE.items():
        m = rx.search(out)
        d[k] = int(m.group(1)) if m else None
    return d


def run_cir(netlist_lines, timeout=30, tag="case", keep_path=False):
    """Run a netlist in ngspice batch. Returns dict with rc/stdout/wall/etc."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.cir', delete=False,
                                     dir=TMP, prefix='saf_') as f:
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
    except subprocess.TimeoutExpired:
        wall = (time.perf_counter() - t0) * 1e3
        res = {'rc': -99, 'stdout': '', 'stderr': 'TIMEOUT',
               'out': 'TIMEOUT', 'wall_ms': wall,
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


def read_wrdata(path):
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
    """Transient run with full series extraction via wrdata + rusage."""
    fd, wrpath = tempfile.mkstemp(dir=TMP, prefix='safwr_', suffix='.txt')
    os.close(fd)
    ctrl = ['.control', 'run',
            f'wrdata {wrpath} ' + ' '.join(vecs),
            'rusage all', '.endc']
    net = [l for l in base_lines if l.strip().lower() != '.end'] + ctrl + ['.end']
    r = run_cir(net, timeout=timeout, tag=tag)
    series = {v: [] for v in vecs}
    try:
        rows = read_wrdata(wrpath)
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


def op_float(out, vec='v(out)'):
    m = re.search(re.escape(vec) + r'\s*=\s*([-\deE+.NaNnUuinfINFloc]+)', out)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (ValueError, TypeError):
        return None


def run_embedded(netlines, vec="v(out)"):
    """Single embedded libngspice cross-check (in-process; crash kills caller).

    Call only from a subprocess-contained context for pathological circuits.
    Returns (value_or_None, log_text).
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'coupling-proto/src'))
    from ngspice_ctypes import NgSpice
    ng = NgSpice()
    ng.init()
    ng.circ(['t'] + [l for l in netlines if l.strip().lower() not in ('t', '.end')]
            + ['.end'])
    ng.command('op')
    ng.command('run')
    v = ng.vec('v(out)')
    log = "\n".join(m for _, _, m in ng.log_lines)
    val = v[-1] if v else None
    return val, log
