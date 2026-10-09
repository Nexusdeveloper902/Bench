"""Native in-process continuation for the production kernel (Spike 6).

Mechanism (evidence in REPORT6.md): ngspice 47 in-process pause/resume via
``stop when time = T`` (exact breakpoint) + ``run`` + ``resume`` preserves
the live CKTcircuit -- CKTstates integration/device history, breakpoints,
adaptive-timestep bookkeeping -- across the boundary. No .ic/UIC
reconstruction, no TIME shifting, no per-segment netlists.

Public API (the smallest kernel-facing surface justified by evidence)::

    req = SimRequest(netlist=(...), observables=("v(out)",), stops=(5e-6,))
    res = run_simulation(req)   # SimResult(series, npts, wall_ms, ...)

Continuation is a pure optimization: any pause that cannot be applied
exactly falls back to the ordinary full simulation path. A refusal never
makes a correct simulation impossible.

Safety rules enforced in code:
  * exact-time stops only (``time = T`` form, internally a breakpoint);
    the ``time > T`` form overshoots by up to a step and is never used;
  * every pause time is verified against the request; any inexact,
    missing, or extra pause falls back to full simulation;
  * the circuit is never mutated between pause and resume (no alter /
    tran / run commands while in progress -- only ``resume``);
  * ``tran``/``run`` while in progress would restart the analysis, so
    only ``resume`` is issued after a pause;
  * vector reads are current-plot qualified to avoid stale-plot reuse
    across sequential simulations on one handle.
"""

import re
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class SimRequest:
    """Smallest statement of a simulation with optional native pauses.

    netlist: full circuit lines including title, ``.tran`` and ``.end``.
    observables: ngspice vector names to return, e.g. ("v(out)", "i(v1)").
    stops: exact simulation-time pause boundaries in (0, t_stop).
        Empty means ordinary full simulation.
    """

    netlist: tuple
    observables: tuple
    stops: tuple = ()


@dataclass(frozen=True)
class SimResult:
    """Outcome of run_simulation. Fallback is explicit, never silent."""

    series: dict          # observable -> [(t, v), ...]
    time: list            # common time grid (scale vector)
    npts: int
    wall_ms: float
    used_pauses: bool     # True only if every requested pause was exact
    fallback_reason: object  # None on the optimized path


def _parse_tstop(lines):
    """Extract t_stop from the .tran line. Returns None if unparseable."""
    for raw in lines:
        s = raw.strip()
        if s.lower().startswith('.tran'):
            toks = s.split()[1:]
            vals = []
            for t in toks:
                try:
                    vals.append(float(t.replace('m', 'e-3').replace('u', 'e-6')
                                       .replace('n', 'e-9').replace('p', 'e-12')))
                except ValueError:
                    try:
                        from validator import parse_value
                        v = parse_value(t)
                        vals.append(v)
                    except Exception:
                        vals.append(None)
            vals = [v for v in vals if v is not None]
            if len(vals) >= 2:
                return float(vals[1])
    return None


def _cur_plot(ng):
    try:
        cur = ng.lib.ngSpice_CurPlot()
        if isinstance(cur, bytes):
            return cur.decode()
        return cur or ""
    except Exception:
        return ""


def _cur_time(ng):
    """Pause-boundary time from the CURRENT plot (stale-plot safe).

    measure_time_vec() prefers tran1.time, which is stale when several
    simulations share one handle/process. Pause verification must read
    the current plot's scale vector.
    """
    plot = _cur_plot(ng)
    for cand in (([plot + ".time"] if plot else []) + ["time"]):
        try:
            got = ng.vec(cand)
        except Exception:
            got = None
        if got:
            return list(got)
    return list(ng.measure_time_vec())


def _new_handle():
    import sys
    import os
    sys.path.insert(0, '<repo>/experiments/coupling-proto/src')
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    from ngspice_ctypes import NgSpice
    ng = NgSpice()
    ng.init()
    return ng


def _read_series(ng, observables):
    """Read observables + time from the CURRENT plot (stale-plot safe)."""
    try:
        cur = ng.lib.ngSpice_CurPlot()
        plot = cur.decode() if isinstance(cur, bytes) else (cur or "")
    except Exception:
        plot = ""
    out = {}
    for v in observables:
        data = None
        if plot:
            try:
                data = ng.vec("%s.%s" % (plot, v))
            except Exception:
                data = None
        if not data:
            data = ng.vec(v) or []
        out[v] = list(data)
    t = _cur_time(ng)
    # pair with time grid (truncate to common length)
    series = {}
    n = len(t)
    for v in observables:
        d = out[v]
        m = min(len(t), len(d))
        series[v] = [(t[i], d[i]) for i in range(m)]
    return series, list(t)


def _run_full(lines, observables):
    """Ordinary full simulation path (the fallback and the default)."""
    ng = _new_handle()
    t0 = time.perf_counter()
    ng.circ(list(lines))
    ng.command("run")
    wall = (time.perf_counter() - t0) * 1e3
    series, t = _read_series(ng, list(observables))
    return series, t, wall


_PAUSE_TOL = 1e-9  # relative + absolute floor verified against minBreak-scale grids


def _pause_exact(got, want):
    tol = max(1e-12, abs(want) * 1e-9)
    return abs(got - want) <= tol


def run_simulation(req):
    """Run req, using native pauses when requested and exactly achievable.

    Returns SimResult. used_pauses=True only if every stop produced an
    exact pause and the run completed at t_stop. Anything else falls
    back to the ordinary full simulation (used_pauses=False,
    fallback_reason names the cause). The fallback series is the
    correctness baseline, never a reconstruction.
    """
    lines = list(req.netlist)
    obs = list(req.observables or ())
    stops = tuple(float(s) for s in (req.stops or ()))
    t_stop = _parse_tstop(lines)

    def fallback(reason):
        series, t, wall = _run_full(lines, obs)
        return SimResult(series=series, time=t, npts=len(t), wall_ms=wall,
                         used_pauses=False, fallback_reason=reason)

    if not stops:
        series, t, wall = _run_full(lines, obs)
        return SimResult(series=series, time=t, npts=len(t), wall_ms=wall,
                         used_pauses=False, fallback_reason=None)
    if t_stop is None:
        return fallback("unknown_tstop")
    if any(not (0.0 < s < t_stop) for s in stops):
        return fallback("stop_outside_interval")
    if any(b <= a for a, b in zip(stops, stops[1:])):
        return fallback("stops_not_strictly_increasing")
    if len(set(stops)) != len(stops):
        return fallback("duplicate_stops")

    ng = _new_handle()
    t0 = time.perf_counter()
    ng.circ(lines)
    for s in stops:
        rc = ng.command("stop when time = %.12g" % s)
        if rc != 0:
            return fallback("stop_rejected")
    ng.command("run")
    t = _cur_time(ng)
    if not t or not _pause_exact(t[-1], stops[0]):
        return fallback("inexact_first_pause")
    for i, s in enumerate(stops[1:], 1):
        ng.command("resume")
        t = _cur_time(ng)
        if not t or not _pause_exact(t[-1], s):
            return fallback("inexact_pause_%d" % i)
    ng.command("resume")
    wall = (time.perf_counter() - t0) * 1e3
    series, t = _read_series(ng, obs)
    if not t or not _pause_exact(t[-1], t_stop):
        return fallback("incomplete_after_resume")
    try:
        ng.command("delete all")
    except Exception:
        pass
    return SimResult(series=series, time=t, npts=len(t), wall_ms=wall,
                     used_pauses=True, fallback_reason=None)
