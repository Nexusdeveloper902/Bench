"""Runtime + post-simulation health layer (after/around ngspice).

Signals evaluated (each experimentally qualified in run_runtime.py):
  RC/TIMEOUT        process status (rc!=0, timeout, wrdata missing)
  LOG               fatal/warning regex scan (singular|timestep|no convergence|
                    stepping failed|aborted|...). Catches every loud failure.
  NONFINITE         any NaN/Inf in any extracted series vector.
  RAIL              max|v| or max|i| implausible vs source scale:
                    |v| > max(1e4, 1e6 * max_source_V). Qualified: never fires
                    on valid extreme-value cases (10kV, 1T dividers, kickback
                    18V, ring osc); fires on unbounded pos-feedback (1e13).
  REJECT            rejected/accepted > 0.5 (never seen on benign cases: worst
                    benign observed ~0.02; Schmitt 7 rejected / ~3000 acc).
  ITER              tran iterations per accepted point > 30 (benign ~2.0-2.2).
  NPTS              pathological point counts (0/1) or cost explosion (>500k).
  EVENT             dt_max (observed) > tmax_required (from bsource.py):
                    sim cannot have resolved the fastest known B-source event.

Verdict: PASS | FAIL (with loud/silent flag + reasons) | QUESTIONABLE.
D4-class silence is a documented BLIND SPOT: runtime returns PASS with
reason 'static-only hazard' when the netlist matches the ISRC-OPEN pattern;
the validator (not the runtime) is load-bearing there.
"""
import math
import re

RAIL_ABS_V = 1e4
RAIL_RATIO = 1e6
REJECT_RATIO = 0.5
ITER_PER_PT = 30
NPTS_COST = 500000


def max_source_scale(lines):
    """Crude max |V|/|I| DC scale from V/I device lines (for RAIL heuristic)."""
    from validator import parse_value
    mx = 0.0
    for raw in lines[1:]:
        s = raw.strip()
        if not s or s.startswith(('*', '.')):
            continue
        parts = s.split()
        name = parts[0]
        if not parts or name[0].upper() not in ('V', 'I'):
            continue
        # find DC token
        up = s.upper()
        m = re.search(r'\bDC\s+([^\s]+)', up)
        if m:
            v = parse_value(m.group(1))
            if v is not None:
                mx = max(mx, abs(v))
            continue
        # bare value form: V1 n+ n- 5
        if len(parts) >= 4:
            v = parse_value(parts[3])
            if v is not None:
                mx = max(mx, abs(v))
    return mx if mx > 0 else 1.0


def check_runtime(r, series, lines, tmax_required=None):
    """r: run_cir/tran_run dict; series: {vec:[(t,v)]}; lines: netlist.

    Returns dict {verdict, loud, reasons, signals}.
    """
    reasons = []
    signals = {}
    loud = bool(r.get('warns')) or bool(r.get('fatals'))

    # RC / timeout / missing data
    signals['rc'] = r.get('rc')
    if r.get('timeout'):
        reasons.append('TIMEOUT')
    if r.get('rc') not in (0,):
        reasons.append(f"RC={r.get('rc')}")
    if r.get('wrdata_missing'):
        reasons.append('WRDATA-MISSING')
    if r.get('fatals'):
        reasons.append('FATAL-LOG: ' + ','.join(r['fatals'][:3]))

    # warnings alone are loud-context, not failure (cap-loop .op warns AND
    # returns garbage -> failure comes from converged=False convention by caller;
    # here: warnings recorded, verdict FAIL only with garbage evidence or rc).
    signals['warns'] = len(r.get('warns', []))
    signals['fatals'] = len(r.get('fatals', []))

    # nonfinite
    nonfinite = False
    peak = 0.0
    for vec, pts in (series or {}).items():
        for _, v in pts:
            try:
                if not math.isfinite(v):
                    nonfinite = True
                peak = max(peak, abs(v))
            except TypeError:
                nonfinite = True
    # also scan .op print values? caller passes op_val separately; keep series-only.
    signals['nonfinite'] = nonfinite
    signals['peak'] = peak
    if nonfinite:
        reasons.append('NONFINITE')

    # rail
    msrc = max_source_scale(lines)
    rail_lim = max(RAIL_ABS_V, RAIL_RATIO * msrc)
    signals['rail_lim'] = rail_lim
    if peak > rail_lim:
        reasons.append(f'RAIL: peak={peak:.3g} > lim={rail_lim:.3g}')

    # reject / iter health
    acc = r.get('accepted') or 0
    rej = r.get('rejected') or 0
    ipp = r.get('iter_per_pt')
    signals['reject_ratio'] = (rej / max(acc, 1)) if acc else 0.0
    signals['iter_per_pt'] = ipp
    if acc and rej / max(acc, 1) > REJECT_RATIO:
        reasons.append(f"REJECT-RATE {rej}/{acc}")
    if ipp is not None and ipp > ITER_PER_PT:
        reasons.append(f"ITER/PT {ipp:.1f}")

    # point counts
    npts = r.get('npts')
    signals['npts'] = npts
    if npts is not None:
        if npts <= 1:
            reasons.append(f'NPTS={npts}')
        elif npts > NPTS_COST:
            reasons.append(f'COST-NPTS={npts}')

    # event coverage (with 0.1% float-boundary margin: an explicitly set
    # tmax that prints as equal must not self-flag on rounding)
    signals['dt_max'] = r.get('dt_max')
    signals['tmax_required'] = tmax_required
    if tmax_required is not None and r.get('dt_max') is not None:
        if r['dt_max'] > tmax_required * 1.001:
            reasons.append(f"EVENT-COVERAGE: dt_max={r['dt_max']:.3g} > required={tmax_required:.3g}")

    # warnings without other evidence: note but do not fail (loud context)
    if r.get('warns') and not reasons:
        verdict = 'PASS'
        reasons.append('LOUD-CONTEXT-ONLY (warnings, no failure evidence)')
    elif reasons and any(k in reasons[0] for k in ()):
        verdict = 'FAIL'
    if not reasons:
        verdict = 'PASS'
    elif any(x.startswith(('RC=', 'TIMEOUT', 'WRDATA', 'FATAL-LOG', 'NONFINITE',
                            'RAIL', 'NPTS=0', 'NPTS=1')) for x in reasons):
        verdict = 'FAIL'
    elif any(x.startswith(('REJECT', 'ITER', 'COST', 'EVENT')) for x in reasons):
        verdict = 'QUESTIONABLE'
    else:
        verdict = 'PASS' if reasons == ['LOUD-CONTEXT-ONLY (warnings, no failure evidence)'] else 'FAIL'

    # D4 blind-spot annotation
    blind = False
    try:
        import validator as V
        f = V.validate(lines)
        if any(x['code'] == 'ISRC-OPEN' for x in f) and verdict == 'PASS':
            blind = True
            reasons.append('BLIND-SPOT: ISRC-OPEN static hazard invisible to runtime (validator load-bearing)')
    except Exception:
        pass
    return {'verdict': verdict, 'loud': loud, 'reasons': reasons,
            'signals': signals, 'blind_spot': blind}
