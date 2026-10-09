"""B-source temporal bandwidth -> timestep bound policy.

ngspice gives TIME-dependent B-sources NO automatic breakpoints (verified:
10ns blip stepped over with dtmax~1us, zero warnings), while PWL/SIN/PULSE
independent sources DO get solver breakpoints (verified in run_events.py:
PWL blip caught at coarse tmax). Scope here is therefore B-sources (and only
B-sources) containing TIME.

Feature extraction (conservative, syntactic):
  u(TIME-t0)/uramp(TIME-t0) step at t0            -> event time t0
  (u(TIME-a)-u(TIME-b)) pulse width |b-a|         -> width w
  sin/cos(k*TIME...) with coefficient k=2*pi*f    -> period T=2*pi/k
  pwl(TIME, t0,v0, t1,v1, ...)                    -> min interval between points
  ternary (TIME>x)?a:b                            -> event time x
  unknown TIME dependence                         -> unquantified (fallback)

Policy (experimentally fitted in run_events.py):
  event bound:  tmax <= min_width / 10
  sine bound:   tmax <= min_period / 50
  combined:     tmax <= min over all applicable bounds
  unknown TIME: refuse silent default; require explicit user tmax
                (verdict 'unknown', caller must warn + demand bound).

tmax here is the .tran TMAX slot (4th positional arg:
.tran TSTEP TSTOP TSTART TMAX [UIC]). Putting the value in the TSTEP slot
only densifies output, it does NOT bound the solver (verified B2b fix).
"""
import re
import math

from validator import parse_value


def _num(tok):
    v = parse_value(tok)
    return v


def extract_bandwidth(expr):
    """Return dict {widths:[s], periods:[s], events:[s], unknown_time:bool}."""
    widths, periods, events = [], [], []
    has_time = bool(re.search(r'\bTIME\b', expr, re.I))
    if not has_time:
        return {'widths': [], 'periods': [], 'events': [],
                'unknown_time': False, 'has_time': False}
    e = expr
    # pulse widths: u(TIME-a)-u(TIME-b) with any nesting/signs
    times = []
    for m in re.finditer(r'u\s*\(\s*TIME\s*-\s*([^\s)]+)\s*\)', e, re.I):
        v = _num(m.group(1))
        if v is not None:
            times.append(v)
    if len(times) >= 2:
        times.sort()
        for a, b in zip(times, times[1:]):
            if b > a:
                widths.append(b - a)
    # generic step events: u(TIME-x), uramp(TIME-x), TIME>x, TIME>=x, TIME<x
    for m in re.finditer(r'u(?:ramp)?\s*\(\s*TIME\s*-\s*([^\s)]+)\s*\)', e, re.I):
        v = _num(m.group(1))
        if v is not None and v not in events:
            events.append(v)
    for m in re.finditer(r'TIME\s*[<>=]+\s*([^\s)?,;]+)', e, re.I):
        v = _num(m.group(1))
        if v is not None:
            events.append(v)
    # pwl(TIME, t0,v0, t1,v1, ...): min dt between consecutive time points
    for m in re.finditer(r'pwl\s*\(\s*TIME\s*,([^)]*)\)', e, re.I):
        nums = []
        for tok in m.group(1).split(','):
            v = _num(tok.strip())
            if v is not None:
                nums.append(v)
        # pairs (t,v): times are even indices
        ts = nums[0::2]
        if len(ts) >= 2:
            dts = [b - a for a, b in zip(ts, ts[1:]) if b > a]
            if dts:
                widths.append(min(dts))
    # sin/cos(k*TIME): coefficient k -> f = k/2pi
    for m in re.finditer(r'(?:sin|cos)\s*\(\s*([^)]*TIME[^)]*)\)', e, re.I):
        arg = m.group(1)
        # find numeric coefficient multiplying TIME
        m2 = re.search(r'([0-9.eE+-]+(?:meg|k|u|n|p|g|m|t|f)?)\s*\*\s*TIME', arg, re.I)
        m3 = re.search(r'TIME\s*\*\s*([0-9.eE+-]+(?:meg|k|u|n|p|g|m|t|f)?)', arg, re.I)
        c = None
        if m2:
            c = _num(m2.group(1))
        elif m3:
            c = _num(m3.group(1))
        if c:
            periods.append(2 * math.pi / abs(c))
    unknown = has_time and not (widths or periods or events)
    return {'widths': widths, 'periods': periods, 'events': events,
            'unknown_time': unknown, 'has_time': has_time}


def required_tmax_for_expr(expr, event_div=10, sine_div=50):
    bw = extract_bandwidth(expr)
    bounds = []
    if bw['widths']:
        bounds.append(min(bw['widths']) / event_div)
    if bw['periods']:
        bounds.append(min(bw['periods']) / sine_div)
    if not bw['has_time']:
        return None, bw  # no bound needed
    if bw['unknown_time']:
        return 'unknown', bw
    if bounds:
        return min(bounds), bw
    # TIME present but only as bare event list (steps without width, e.g. single
    # edge): bound by nothing quantifiable -> still needs a bound for placement
    # luck; report unknown so the caller demands an explicit policy value.
    return 'unknown', bw


def required_tmax_for_netlist(lines, event_div=10, sine_div=50):
    """Min over all B-source expressions. Returns (tmax|None|'unknown', detail)."""
    import validator as V
    _, b_exprs, _, _, _ = V.parse_netlist(lines)
    bounds, detail, unknown = [], {}, False
    for name, (_, expr, _) in b_exprs.items():
        r, bw = required_tmax_for_expr(expr, event_div, sine_div)
        detail[name] = {'bound': r, **bw}
        if r == 'unknown':
            unknown = True
        elif r is not None:
            bounds.append(r)
    if unknown and not bounds:
        return 'unknown', detail
    if unknown and bounds:
        return min(bounds), detail  # quantified part still bounds; unknown flagged in detail
    if bounds:
        return min(bounds), detail
    return None, detail
