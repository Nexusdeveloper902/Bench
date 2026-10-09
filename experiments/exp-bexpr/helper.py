"""Batch ngspice helper: write netlist, run ngspice -b, capture output."""
import subprocess, tempfile, os, re, time

def run_cir(netlist_lines, timeout=20):
    """netlist_lines: list of str (first line is title). Returns dict with stdout, stderr, rc, wall_ms."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.cir', delete=False) as f:
        f.write("\n".join(netlist_lines) + "\n")
        path = f.name
    t0 = time.perf_counter()
    try:
        p = subprocess.run(['ngspice', '-b', path], capture_output=True, text=True, timeout=timeout)
        wall = (time.perf_counter() - t0) * 1e3
        return {'rc': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr,
                'out': p.stdout + p.stderr, 'wall_ms': wall, 'path': path}
    except subprocess.TimeoutExpired as e:
        return {'rc': -99, 'stdout': '', 'stderr': f'TIMEOUT: {e}', 'out': '', 'wall_ms': (time.perf_counter()-t0)*1e3, 'path': path}
    finally:
        try: os.unlink(path)
        except: pass

import re
_ERR_RES = [re.compile(p, re.I) for p in
    (r'\berror\b', r'no convergence', r'\bsingular\b', r'timestep too small',
     r'unknown (parameter|function|node|device)', r'\bundefined\b', r'syntax error',
     r'\binvalid\b', r'\bfailed\b', r'\bwarning\b', r'can\'t', r'cannot',
     r'no such (parameter|device|vector)', r'parse error')]

def has_error(out):
    return [p.pattern for p in _ERR_RES if p.search(out)]

def get_dc_op(out):
    """Parse nothing; we use explicit .control print/write. For batch, use `wrdata`? Simpler: add .control with print."""
    return out

# quick smoke
if __name__ == '__main__':
    r = run_cir(['test', 'V1 in 0 DC 5', 'R1 in 0 1k', '.op', '.control', 'print v(in)', '.endc', '.end'])
    print(r['rc'], r['wall_ms'])
    print(r['out'][:2000])
