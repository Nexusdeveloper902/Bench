"""Continuation-aware event windowing: minimal production mechanism.

Scope (see REPORT2.md for the evidence-backed boundary):
  * Shiftable TIME events: B u-pulse/comparison/pwl(TIME) forms and V/I
    PWL/PULSE/SIN sources (per-segment -t_start shifting implemented).
  * Continuation-safe state ONLY: R/C/plain-D/memoryless (+L/K with
    explicit IC handoff of every inductor winding, enforced; resonant
    LC windings additionally REQUIRE user-supplied resonant_period,
    enforced as tmax<=period/50, auto-coarse REFUSEd [E spike-4]).
  * Everything else (D with TT/CJO, Q/M/J, A, B with ddt/sin/bare-TIME,
    behavioral R/C, .param-mediated TIME, T-lines, K without full
    handoff, OSDI/X/exotic) is REFUSED, not approximated.
  * Unquantifiable/state-dependent events need user windows/triggers.

Two supported cost escapes (both keep the validated TMAX<=w/10 bound inside
refined regions):
  1. segmented(): coarse run + fine window(s) + coarse, chained with .ic+uic
     handoff, device IC= rewrite for L, and -t_start TIME-shift of every
     B/PWL/PULSE/SIN time constant per segment.
  2. PWL restatement (helper pwl_pulse_source): known u-pulse events rewritten
     as PWL independent sources get free solver breakpoints (prior recipe).

Gates enforced in code (adversarial findings: runtime.py catches 0/6 silent
continuation breaks, so these gates are load-bearing):
  G1 state-class allowlist (classify_state), ENFORCED inside run_segmented;
     denies D-TT/CJO, Q/M/J, A, B-ddt, behavioral R/C, T, K without complete
     winding handoff, OSDI/X/exotic, .param-mediated TIME, E/G/H/F TIME
     (VALUE smuggling), TIME-bearing .func called from exprs, .include/.lib
  G2 TIME audit (every segment TIME-shifts B TIME-consts/comparisons,
     pwl(TIME) args, V/I PWL/PULSE/SIN delays/phases; any residual TIME
     fails closed with REFUSE; non-bare pwl(TIME...) control vars REFUSE)
  G3 margin rule: margin = 10*min edge interval; per-edge windows +
     clustering (multi-event first-pair bug fixed); overlap/unsorted
     windows REFUSEd; every quantified event must sit strictly inside a
     fine window (coarse-miss REFUSE)
  G4 timestep bounds enforced: tmax_fine <= event bound (w/10); both
     tmax_coarse and tmax_fine <= tau_min/1000 (RC background/driven
     truncation) and <= tau_L/100 (inductive background) and <= SIN
     period/50 (continuous drive) and <= resonant_period/50
     (USER-SUPPLIED min oscillation period; resonant auto-coarse REFUSEd
     without it [E spike-4]); per-segment EVENT-COVERAGE on fine segs
  G5 handoff completeness (every C node + every L in handoff, handoff in
     vecs) + stitch-continuity on node voltages AND inductor branch
     currents (>= tol refuses)
  G6 seam hygiene: never .ic a driven node (B both terminals, E outputs
     nodes[:2], H outputs, time-varying V both terminals)

Usage: see tests/test_window.py. Import path: this file lives in exp-window/
and imports exp-safety modules (validator, bsource, helper_safety, runtime).
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SAF = os.path.join(os.path.dirname(_HERE), 'exp-safety')
if _SAF not in sys.path:
    sys.path.insert(0, _SAF)

import validator as V
import bsource as BS
from helper_safety import tran_run
import runtime as RT

MARGIN_MULT = 10.0  # margin = 10 * min edge-to-edge interval [E]
TAU_DIV = 1000.0  # timestep bounds: tmax <= tau_min/1000 [E/I]
TAU_L_DIV = 100.0  # inductive bound: tmax <= tau_L/100 [E: t3 RL PASS at tau/100, K2 lossy at tau/2]
SIN_DIV = 50.0  # continuous SIN bound: tmax <= period/50 (same divisor as B-sine [E])
RES_DIV = 50.0  # resonant bound: tmax <= resonant_period/50 [E spike-4:
                # N=50 gives ~0.5deg/period Q-independent 2nd-order
                # (13.2->3.42->0.56->0.14->0.035deg for N=10/20/50/100/200),
                # local amp <0.1%, but phase accumulates per-period x periods
                # (46.7deg/100periods at N=50 with amp err -0.14%), so the
                # period MUST be user-supplied (T0 not auto-extractable for
                # coupled/multi-rate tanks) and the user must budget total
                # phase (tighten to /100, /200 per table) or keep runs short]
G5_REL = 1e-3  # stitch continuity relative tolerance
G5_ABS = 1e-6  # stitch continuity absolute floor (V/A)


def estimate_tau_min(lines):
    """Conservative fastest RC state timescale from nominal R/C values.

    tau = min over C elements of (C * min incident R, R=0 skipped).
    Inductors excluded: L/R vs LC-resonance needs real topology analysis
    (a coupled ring measured exact at 200ns where L/R alone would demand
    100ps [E]); inductive coarse adequacy stays [U] and L/K ride on
    explicit handoff + G5. Semiconductors excluded (dynamic R
    unparseable; validated only on tested diode classes [I]).
    Returns None if no C state or nothing parseable. Underestimates tau
    (safe direction: tighter bound) within stated scope.
    """
    devices, _, _, _, _ = V.parse_netlist(lines)
    rval, cval = {}, {}
    for d in devices:
        parts = d.raw.split()
        if len(parts) < 4:
            continue
        if d.kind == 'R':
            v = V.parse_value(parts[3])
            if v is not None and v > 0:
                rval[d.name] = v
        elif d.kind == 'C':
            v = V.parse_value(parts[3])
            if v is not None and v > 0:
                cval[d.name] = (d.nodes, v)
    rdev = [(d.name, d.nodes) for d in devices if d.name in rval]
    taus = []
    for name, (nodes, c) in cval.items():
        inc = [rval[r] for r, rn in rdev
               if any(n in nodes for n in rn)]
        if inc:
            taus.append(c * min(inc))
    return min(taus) if taus else None


def estimate_tau_l(lines):
    """Conservative slowest-inductive timescale: min over L of L/max incident R.

    Incident = sharing a NON-GROUND node (series R; ground excluded so
    shunt-L rings with no series R yield no bound [E: h17 K-ring unaffected]).
    LC-resonant windings (any non-ground node touching a C) are SKIPPED:
    L/R alone would demand 100ps on the h17 coupled ring measured exact at
    200ns [E REPORT2]; resonant adequacy stays [U] riding on handoff+G5.
    R=0 skipped. Returns None if no non-resonant L with series R.
    Bound tmax <= tau_L/100 [E: t3 RL (100us) PASS-exact at 1us=tau/100;
    K2 (100us) lossy 2mV at 50us=tau/2; thread-A small-L lossy 4.75mV].
    """
    devices, _, _, _, _ = V.parse_netlist(lines)
    rval, lval = {}, {}
    cnodes = set()
    for d in devices:
        if d.kind == 'C':
            cnodes.update(n for n in d.nodes if n != '0')
    for d in devices:
        parts = d.raw.split()
        if len(parts) < 4:
            continue
        if d.kind == 'R':
            v = V.parse_value(parts[3])
            if v is not None and v > 0:
                rval[d.name] = v
        elif d.kind == 'L':
            v = V.parse_value(parts[3])
            if v is not None and v > 0:
                lval[d.name] = (d.nodes, v)
    rdev = [(d.name, d.nodes) for d in devices if d.name in rval]
    taus = []
    for name, (nodes, l) in lval.items():
        nz = [n for n in nodes if n != '0']
        if not nz:
            continue
        if any(n in cnodes for n in nz):
            continue  # LC-resonant: L/R bound invalid, skip (stays [U])
        inc = [rval[r] for r, rn in rdev
               if any(n in nz for n in rn if n != '0')]
        if inc:
            taus.append(l / max(inc))
    return min(taus) if taus else None


def detect_resonant(lines):
    """Windings whose adequacy is resonant, not L/R: L sharing a NON-GROUND
    node with a C (same incidence rule as the estimate_tau_l skip).

    Such windings have NO automatic coarse bound: L/R is 2.5-300x over-tight
    or vacuous, T0 is not auto-extractable in general (coupled/multi-rate
    tanks take min-T0; self-oscillation has no source period), and fixed
    T0/N cannot bound total accumulated phase for unbounded TSTOP [E spike-4:
    N=50 exact locally yet 46.7deg/100periods]. run_segmented REFUSEs these
    circuits unless the caller supplies resonant_period (USER-SUPPLIED BOUND
    REQUIRED). Returns sorted list of resonant L names (empty if none).
    """
    devices, _, _, _, _ = V.parse_netlist(lines)
    cnodes = set(n for d in devices if d.kind == 'C'
                 for n in d.nodes if n != '0')
    res = []
    for d in devices:
        if d.kind == 'L':
            nz = [n for n in d.nodes if n != '0']
            if any(n in cnodes for n in nz):
                res.append(d.name)
    return sorted(res)


def vsin_bound(lines):
    """Fastest continuous V/I SIN oscillation bound: min period/50 or None.

    SIN runs the whole sim (unlike windowed pulses), so coarse must resolve
    it everywhere [E: K1b 100kHz SIN PASS-but-185mV at 1us vs 200ns bound].
    PULSE/PWL are event-localized AND get solver breakpoints, so they need
    no whole-sim bound (windowing covers them; enforcing would kill savings).
    """
    devices, _, _, _, _ = V.parse_netlist(lines)
    bounds = []
    for d in devices:
        if d.kind not in ('V', 'I'):
            continue
        m = re.search(r'\bsin\s*\(([^)]*)\)', d.raw, re.I)
        if not m:
            continue
        args = [a.strip() for a in m.group(1).split()]
        vals = [V.parse_value(a) for a in args]
        if len(vals) >= 3 and vals[2] and vals[2] > 0:
            bounds.append((1.0 / vals[2]) / SIN_DIV)
    return min(bounds) if bounds else None


def shift_b_expr(expr, t_off):
    """Shift absolute TIME constants by -t_off for a segment starting at t_off.

    Rewrites `TIME-<num>` and `TIME<op><num>` (ternary/step comparisons)
    where <num> parses. Anything else containing TIME (bare TIME, sin-phase,
    pwl(TIME,..) handled separately, .param names, TIME-(a+b)) is left
    verbatim and MUST be caught by residual-TIME refusal downstream (G2).
    Higher-precedence offset tails (`TIME - 1m * 2`, `TIME>1m*2`: the
    offset is 1m*2=2m, not 1m) are UNSHIFTABLE -> ValueError (REFUSE)
    [E spike-3 H1/H2: shifting only the 1m silently re-bound the tree,
    2.75V PASS-but-wrong, spacing-dependent]. Same-precedence tails
    (`TIME-1m+2m`, `(TIME-c)*k` parenthesized) remain exact and pass.
    """
    def repl_sub(m):
        v = V.parse_value(m.group(1))
        if v is None:
            return m.group(0)
        tail = expr[m.end(1):]
        if re.match(r'\s*[*/]', tail):
            raise ValueError('G2: TIME offset continues with */ (precedence unshiftable: %s)' % m.group(0)[:40])
        return 'TIME-%s' % ('%.12g' % (v - t_off))
    expr = re.sub(r'TIME\s*-\s*([^\s)]+)', repl_sub, expr)

    def repl_cmp(m):
        v = V.parse_value(m.group(2))
        if v is None:
            return m.group(0)
        tail = expr[m.end(2):]
        if re.match(r'\s*[*/]', tail):
            raise ValueError('G2: TIME comparison threshold continues with */ (precedence unshiftable: %s)' % m.group(0)[:40])
        return 'TIME%s%s' % (m.group(1), '%.12g' % (v - t_off))
    expr = re.sub(r'TIME\s*(>=|<=|>|<|=)\s*([^\s)?,;]+)', repl_cmp, expr)
    return expr


def _shift_tv_pairs(pairs, t_off, what):
    """Shift (time, value) pairs by -t_off with exact prefix-drop.

    Points shifted before segment start are dropped when the nearest
    dropped value equals the first kept value (the invented local prefix
    [0, first_kept_t) then holds exactly that constant [E]; middle dropped
    history is irrelevant to the local segment). Otherwise the
    pre-segment history is unrepresentable -> ValueError (REFUSE).
    """
    shifted = [(t - t_off, v) for t, v in pairs]
    idx = next((i for i, (t, v) in enumerate(shifted) if t >= 0), None)
    if idx is None:
        raise ValueError('G2: %s entirely precedes segment start' % what)
    if idx > 0 and not _same_value(shifted[idx - 1][1], shifted[idx][1]):
        raise ValueError('G2: %s varies across segment start '
                         '(would re-fire/lose history)' % what)
    return shifted[idx:]


def _parse_tv_pairs(toks, what):
    if len(toks) % 2:
        raise ValueError('G2: odd arg count in %s' % what)
    pairs = []
    for i in range(0, len(toks), 2):
        t = V.parse_value(toks[i])
        if t is None:
            raise ValueError('G2: unparseable time %s in %s' % (toks[i], what))
        pairs.append((t, toks[i + 1].strip()))
    return pairs


def _same_value(a, b):
    try:
        return float(a) == float(b)
    except ValueError:
        return a.strip() == b.strip()


def shift_pwl_time_args(expr, t_off):
    """Shift time coordinates inside pwl(TIME, t0,v0, t1,v1, ...) by -t_off.

    Unlike V-PWL (which holds endpoint values), B-source pwl() linearly
    EXTRAPOLATES outside the table [E] — so points shifted before segment
    start are KEPT (negative ordinates are inert data; local t>=0
    interpolates exactly as the global form). Unparseable coordinates ->
    ValueError (REFUSE).
    """
    def repl(m):
        toks = [t for t in re.split(r'[,\s]+', m.group(1).strip()) if t]
        if len(toks) % 2:
            raise ValueError('G2: odd pwl(TIME) arg count')
        out = []
        for i in range(0, len(toks), 2):
            v = V.parse_value(toks[i])
            if v is None:
                raise ValueError('G2: unparseable pwl(TIME) coordinate: %s'
                                 % toks[i])
            out += ['%.12g' % (v - t_off), toks[i + 1].strip()]
        return 'pwl(TIME,' + ','.join(out) + ')'
    return re.sub(r'pwl\s*\(\s*TIME\s*,([^)]*)\)', repl, expr, flags=re.I)


def shift_v_line(raw, t_off):
    """Shift an independent V/I source line (PULSE/PWL/SIN) by -t_off.

    PULSE delays, PWL time points, SIN phase (deg += 360*f*dt) are moved;
    anything moving before segment start -> ValueError (REFUSE, fail-closed).
    Lines without time dependence pass through unchanged.
    """
    m = re.search(r'pulse\s*\(([^)]*)\)', raw, re.I)
    if m:
        args = [a.strip() for a in m.group(1).split()]
        if len(args) >= 3:
            v = V.parse_value(args[2])
            if v is None:
                raise ValueError('G2: unparseable PULSE delay: %s' % args[2])
            nv = v - t_off
            if nv >= 0:
                args[2] = '%.12g' % nv
                return raw[:m.start(1)] + ' '.join(args) + raw[m.end(1):]
            # Segment starts mid-train: modular shift is exact iff the whole
            # local prefix [0, local_delay) maps into the V1-hold phase, i.e.
            # start phase u0 >= tr+pw+tf. Else REFUSE (fail-closed).
            vals = [V.parse_value(a) for a in args[2:7]]
            if len(vals) < 5 or any(x is None for x in vals):
                raise ValueError('G2: PULSE started before segment; full PULSE spec needed for mid-train shift')
            _td, tr, tf, pw, per = vals
            if per <= 0:
                raise ValueError('G2: PULSE delay precedes segment start (nonperiodic)')
            u0 = (t_off - v) % per
            if u0 < (tr or 0) + (pw or 0) + (tf or 0) and abs(u0) > 0:
                raise ValueError('G2: segment starts inside PULSE active region (phase %g)' % u0)
            args[2] = '%.12g' % ((v - t_off) % per)
            return raw[:m.start(1)] + ' '.join(args) + raw[m.end(1):]
        return raw
    m = re.search(r'\bpwl\s*\(([^)]*)\)', raw, re.I)
    if m:
        toks = [t for t in re.split(r'[,\s]+', m.group(1).strip()) if t]
        pairs = _parse_tv_pairs(toks, 'PWL')
        kept = _shift_tv_pairs(pairs, t_off, 'PWL')
        flat = []
        for t, v in kept:
            flat += ['%.12g' % t, v]
        return raw[:m.start(1)] + ' '.join(flat) + raw[m.end(1):]
    m = re.search(r'\bsin\s*\(([^)]*)\)', raw, re.I)
    if m:
        # SIN(Voff Vamp Freq Td Theta Phase): phase += 360*f*t_off is exact;
        # a nonzero delay Td is envelope history: shift like a PULSE delay
        # (negative -> REFUSE, fail-closed).
        args = [a.strip() for a in m.group(1).split()]
        vals = [V.parse_value(a) for a in args]
        if t_off and not (len(vals) >= 3 and vals[2]):
            # Unparseable-freq SIN with t_off>0 would pass through verbatim
            # (no TIME token for residual to catch) [E spike-3 H3:
            # SIN(0 5 {FREQ}..) 540-deg silent]. Zero-freq (DC) is
            # shift-invariant -> allow through.
            if len(args) >= 3 and args[2].strip() not in ('', '0') \
                    and V.parse_value(args[2]) is None:
                raise ValueError('G2: SIN freq %s unparseable with t_off>0 (param-mediated phase unshiftable)' % args[2][:24])
            return raw
        if len(vals) >= 3 and vals[2]:
            import math
            if t_off:
                # Phase advance 360*f*t_off needs numeric freq AND numeric
                # phase; param-mediated ({FREQ}/{PH}) would silently pass
                # through with wrong phase [E spike-3 H3: 540 deg, 10Vpp].
                for pos, tag in ((2, 'freq'), (5, 'phase')):
                    if len(args) > pos and args[pos].strip() not in ('', '0'):
                        if V.parse_value(args[pos]) is None:
                            raise ValueError('G2: SIN %s %s unparseable with t_off>0 (param-mediated phase unshiftable)' % (tag, args[pos][:24]))
            td_orig = vals[3] if len(vals) > 3 and vals[3] else 0.0
            args += ['0'] * (6 - len(args))
            if td_orig and 0 < t_off < td_orig:
                # Segment starts strictly inside the pre-delay region:
                # pre-delay output (Voff+Vamp*sin(phase)) and post-delay
                # oscillation need different phases -> unrepresentable.
                # Seams must sit at 0 or >= td [E].
                raise ValueError('G2: segment starts inside SIN pre-delay region (seam must be at 0 or >= delay)')
            if td_orig and t_off >= td_orig:
                # Delay elapsed locally: oscillation fully carried by phase,
                # advanced by the oscillation's elapsed time (t_off - td).
                # Damped envelopes (Theta!=0) are history -> REFUSE.
                theta = vals[4] if len(vals) > 4 and vals[4] else 0.0
                if theta:
                    raise ValueError('G2: SIN delay precedes segment start (damped envelope history)')
                args[3] = '0'
                ph = vals[5] if len(vals) > 5 and vals[5] else 0.0
                args[5] = '%.12g' % (ph + 360.0 * vals[2] * (t_off - td_orig))
            elif td_orig:
                # t_off == 0: verbatim (delay + phase unchanged).
                pass
            else:
                # No delay: plain phase advance by t_off [E: 2.4e-05].
                ph = vals[5] if len(vals) > 5 and vals[5] else 0.0
                args[5] = '%.12g' % (ph + 360.0 * vals[2] * t_off)
            return raw[:m.start(1)] + ' '.join(args) + raw[m.end(1):]
        return raw
    return raw


def assert_no_residual_time(text, where):
    """Fail-closed G2: any TIME surviving shifting is an unshiftable form.

    Correctly shifted numeric TIME-<num> / TIME<op><num> forms (exactly what
    shift_b_expr produces) are stripped before the check, as is the
    bare pwl(TIME, local-time marker (its coordinates were shifted-or-refused
    just before); symbolic leftovers (TIME-t0 from .param mediation),
    bare TIME, sin-phase etc. fail closed.
    The pwl marker strip matches ONLY the bare form `pwl(TIME,` [E: spike-3
    H1 — the old prefix strip `pwl(TIME` masked non-bare control vars
    pwl(TIME-400u,...)/pwl(TIME*2,...) into silent 12-50V PASS-but-wrong].
    Any other TIME inside a pwl(...) span is unshiftable -> REFUSE.
    """
    def strip_num(pat, tmp):
        def drop(m):
            return 'T' if V.parse_value(m.group(1)) is not None else m.group(0)
        return re.sub(pat, drop, tmp)
    tmp = strip_num(r'TIME\s*-\s*([^\s)]+)', text)
    tmp = strip_num(r'TIME\s*(?:>=|<=|>|<|=)\s*([^\s)?,;]+)', tmp)
    tmp = re.sub(r'pwl\s*\(\s*TIME\s*,', 'pwl(T,', tmp, flags=re.I)
    if re.search(r'pwl\s*\([^)]*TIME', tmp, re.I):
        raise ValueError('G2: unshiftable TIME form in pwl() control var in %s (only bare pwl(TIME,...) shiftable)' % where)
    if re.search(r'\bTIME\b', tmp, re.I):
        raise ValueError('G2: unshiftable TIME form in %s (param/sin/bare-TIME?)' % where)


def classify_state(lines, handoff_l_names=None):
    """State-class gate G1. Returns (allowed:bool, reasons:list, info:dict).

    allowed=True ONLY for R/C/memoryless (+L/K iff every inductor winding
    gets an explicit IC handoff, which segmented() implements and verifies;
    +plain D without TT/CJO charge storage; +E/G/H/F memoryless controlled
    sources; +time-shifted independent sources).
    Deny: D with TT or CJO/CJP charge storage, Q/M/J (base/channel charge),
    A XSPICE code models, B with ddt, behavioral R/C carrying TIME, T-lines,
    K without complete winding handoff, OSDI/X/exotic, .param-mediated TIME.
    """
    devices, b_exprs, _, _, _ = V.parse_netlist(lines)
    reasons = []
    deny = False
    l_names, d_tt, b_ddt, bad = [], [], [], []
    joined = '\n'.join(lines)
    for d in devices:
        k = d.kind
        if k == 'L':
            l_names.append(d.name)
        elif k == 'D':
            if re.search(r'\bTT\s*=', d.raw, re.I):
                m = re.search(r'\bTT\s*=\s*([^\s]+)', d.raw, re.I)
                v = V.parse_value(m.group(1)) if m else None
                if v:
                    deny = True
                    d_tt.append(d.name)
                    reasons.append(f'DENY: {d.name} has TT={m.group(1)} stored charge (A1c: 43.5mV silent)')
        elif k in ('Q', 'M', 'J'):
            deny = True
            bad.append(d.name)
            reasons.append(f'DENY: {d.name} kind {k} carries base/channel/gate charge not restored by .ic (S5: 4.4V silent)')
        elif k == 'A':
            deny = True
            bad.append(d.name)
            reasons.append(f'DENY: {d.name} XSPICE code model (internal int_ic state opaque)')
        elif k == 'T':
            deny = True
            bad.append(d.name)
            reasons.append(f'DENY: {d.name} transmission line in-flight state uncarriable (A9: 5V lost)')
        elif k == 'K':
            pass  # decided below: allowed iff every winding handed off
        elif k == 'X':
            deny = True
            bad.append(d.name)
            reasons.append(f'DENY: {d.name} opaque subcircuit (hidden state unknown)')
        elif k in ('O', 'U', 'Y', 'N', 'S', 'W', 'Z'):
            deny = True
            bad.append(d.name)
            reasons.append(f'DENY: {d.name} exotic kind outside continuation coverage')
        elif k == 'R' or k == 'C':
            if re.search(r'\bTIME\b', d.raw, re.I):
                deny = True
                bad.append(d.name)
                reasons.append(f'DENY: {d.name} behavioral {k} carries TIME (S6: 30% silent)')
        elif k in ('E', 'G'):
            if re.search(r'\b(LAPLACE|FREQ)\b', d.raw, re.I):
                deny = True
                bad.append(d.name)
                reasons.append(f'DENY: {d.name} LAPLACE/FREQ transfer state uncarriable')
            if re.search(r'\bTIME\b', d.raw, re.I):
                deny = True
                bad.append(d.name)
                reasons.append(f'DENY: {d.name} E/G carries TIME (VALUE smuggling: 5->0 silent PASS [E spike-3 H2]; no E/G TIME shifting implemented)')
        elif k in ('H', 'F'):
            if re.search(r'\bTIME\b', d.raw, re.I):
                deny = True
                bad.append(d.name)
                reasons.append(f'DENY: {d.name} H/F carries TIME (no H/F TIME shifting implemented)')
    # A-devices (XSPICE code models) never reach parse_netlist as Devices
    # (UNKNOWN-KIND); detect by line shape. A = analog code model.
    for raw in lines[1:]:
        s = raw.strip()
        if re.match(r'^[Aa]\S*\s+\S+', s) and not s.startswith('.'):
            deny = True
            bad.append(s.split()[0])
            reasons.append(f'DENY: {s.split()[0]} XSPICE code model (internal int_ic state opaque)')
            break
    # K coupling: every winding needs explicit IC handoff (both-IC exact [E]).
    coupled = set()
    for raw in lines[1:]:
        s = raw.strip()
        if re.match(r'^[Kk]\S*\s', s):
            parts = s.split()
            for tok in parts[1:]:
                if re.match(r'^[Ll]\S*$', tok):
                    coupled.add(tok)
                else:
                    break
    if coupled:
        given = set(g.lower() for g in (handoff_l_names or []))
        missing = sorted([c for c in coupled if c.lower() not in given])
        if missing:
            deny = True
            reasons.append('DENY: K coupling %s missing winding IC handoff for %s (no-IC: 3.2V silent)'
                           % (sorted(coupled), missing))
        else:
            reasons.append('ALLOW-K: all windings %s via explicit IC handoff' % sorted(coupled))
    if re.search(r'\.osdi\b', joined, re.I):
        deny = True
        reasons.append('DENY: OSDI device (internal state uncharacterized)')
    for m in re.finditer(r'^\s*\.(include|lib)\b[^\n]*', joined, re.I | re.M):
        deny = True
        reasons.append('DENY: %s present (file content invisible to static classification; smuggled B-TIME/state fires in ngspice [E spike-3 H4])' % m.group(0).strip()[:60])
        break
    # D TT/CJO may live in the .model line, not the device line: scan models.
    for m in re.finditer(r'\.model\s+(\S+)\s+D\s*\(([^)]*)\)', joined, re.I):
        tm = re.search(r'\bTT\s*=\s*([^\s)]+)', m.group(2), re.I)
        if tm and (V.parse_value(tm.group(1)) or 0):
            deny = True
            reasons.append('DENY: .model %s has TT=%s stored charge (A1c: 43.5mV silent)'
                           % (m.group(1), tm.group(1)))
        for cj in re.finditer(r'\b(CJ[O0]|CJP)\s*=\s*([^\s)]+)', m.group(2), re.I):
            if (V.parse_value(cj.group(2)) or 0) > 0:
                deny = True
                reasons.append('DENY: .model %s has %s=%s junction charge (seam-local ~0.2mV silent at coarse tmax)'
                               % (m.group(1), cj.group(1), cj.group(2)))
    # .param/.func names consumed inside TIME-bearing B exprs are unshiftable.
    param_names = set()
    for m in re.finditer(r'\.(?:param|func)\s+([A-Za-z_][A-Za-z0-9_]*)', joined, re.I):
        param_names.add(m.group(1).lower())
    # .func bodies hiding TIME, called from a B/E/G/H/F expression, smuggle
    # events past the call-site TIME scan [E spike-3 H3: .func f(x){5*u(x+TIME-200u)}
    # + B1 V=f(0) ALLOWs while ngspice fires the 5V step].
    func_time = {}  # fname.lower() -> body has TIME
    for m in re.finditer(r'\.func\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*\{?([^}\n]*)', joined, re.I):
        if re.search(r'\bTIME\b', m.group(2)):
            func_time[m.group(1).lower()] = True
    if func_time:
        ctrl_exprs = [(name, expr) for name, (_, expr, _) in b_exprs.items()]
        for d in devices:
            if d.kind in ('E', 'G', 'H', 'F'):
                ctrl_exprs.append((d.name, d.raw))
        for name, expr in ctrl_exprs:
            called = [f for f in func_time
                      if re.search(r'\b%s\s*\(' % re.escape(f), expr, re.I)]
            if called:
                deny = True
                reasons.append(f'DENY: {name} calls TIME-bearing .func {called} (H3 smuggling)')
                break
    for name, (_, expr, _) in b_exprs.items():
        if re.search(r'\bddt\s*\(', expr, re.I):
            deny = True
            b_ddt.append(name)
            reasons.append(f'DENY: {name} ddt() history uncarriable across seams (A1b: impulse 1->0)')
        if re.search(r'\bTIME\b', expr, re.I):
            used = [p for p in param_names
                    if re.search(r'\b%s\b' % re.escape(p), expr, re.I)]
            if used:
                deny = True
                reasons.append(f'DENY: {name} TIME mediated by .param/.func {used} (S1: 5V silent miss)')
    info = {'L': l_names, 'D_TT': d_tt, 'B_ddt': b_ddt, 'other': bad,
            'K_windings': sorted(coupled)}
    if not deny:
        reasons.append('ALLOW: R/C/plain-D/memoryless%s%s' % (
            ' +L(%s) via IC handoff' % ','.join(l_names) if l_names else '',
            ' +K(%s)' % ','.join(sorted(coupled)) if coupled else ''))
    return (not deny), reasons, info


def derive_windows(lines, tmax_coarse, margin_mult=MARGIN_MULT):
    """Auto-derive refinement windows from bsource bandwidth info.

    Returns (windows, tmax_fine, detail, verdict). windows = [(start,end)] in
    absolute sim time. verdict 'ok' or 'unknown' (user must supply windows).
    Rule: margin = margin_mult * min edge-to-edge interval (default 10x [E];
    the old max(10w, tmax_coarse) bought nothing and killed savings [E]).
    One window per parsed event edge, clustered when closer than 2*margin
    (G3/G6). Multi-event first-pair-only derivation was a silent-miss bug;
    every edge is windowed now.
    """
    try:
        t_req, detail = BS.required_tmax_for_netlist(lines)
    except Exception as e:  # pragma: no cover - defensive
        return [], None, {}, 'unknown: bandwidth parse failed (%s)' % e
    if t_req == 'unknown' or t_req is None:
        # check whether any B-source even has TIME
        timed = [k for k, d in detail.items() if d.get('has_time')]
        if not timed:
            return [], None, detail, ('ok: no TIME-bearing B-sources; no auto windows derivable'
                                      ' - user must confirm no state-dependent sharp features'
                                      ' (Schmitt edge err ~= TMAX), else supply windows')
        return [], None, detail, 'unknown: unquantifiable TIME dependence, user must supply windows'
    evs = sorted({e for d in detail.values() for e in (d.get('events') or [])})
    if len(evs) < 1:
        return [], t_req, detail, 'unknown: no events extracted, user must supply windows'
    if len(evs) < 2:
        return [], t_req, detail, 'unknown: single bare edge (no width quantifiable), user must supply windows'
    gaps = [b - a for a, b in zip(evs, evs[1:]) if b > a]
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return [], None, detail, 'unknown: no positive edge intervals, user must supply windows'
    w_min = min(gaps)
    margin = margin_mult * w_min
    if margin <= 0:
        return [], None, detail, 'unknown: non-positive margin'
    wins = [(max(0.0, e - margin), e + margin) for e in evs]
    # cluster overlapping/adjacent
    wins.sort()
    merged = []
    for s, e in wins:
        if merged and s - merged[-1][1] < 2 * margin:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged, t_req, {'w_min': w_min, 'margin': margin, **detail}, 'ok'


def _quantified_events(base_lines):
    """Parsed B-source event times, or None if unquantifiable (G3/G4 input)."""
    try:
        t_req, detail = BS.required_tmax_for_netlist(base_lines)
    except Exception:
        return None
    if t_req in (None, 'unknown'):
        return None
    evs = sorted({e for d in detail.values() for e in (d.get('events') or [])})
    return evs or None


def _rewrite_segment_lines(base_lines, t_start, span, handoff_v, handoff_i,
                           tmax_seg, use_uic=True):
    """Build one segment netlist: TIME-shifted B/V/PWL/PULSE/SIN, .ic, L IC=, UIC.

    Raises ValueError (→ REFUSE in run_segmented) on G2 unshiftable TIME and
    G6 .ic-on-driven-node. C device IC= is stripped ONLY when that node's
    voltage arrives via .ic this segment (S12: stripping unconditionally
    breaks seg-0 initial conditions). Node matching is case-insensitive.
    """
    hv_low = {k.lower(): v for k, v in (handoff_v or {}).items()}
    hi_low = {k.lower(): v for k, v in (handoff_i or {}).items()}
    devices, b_exprs, _, _, _ = V.parse_netlist(base_lines)
    bnode = {}  # nodes driven by behavioral/time-varying sources (G6)
    for d in devices:
        if d.kind == 'B':
            for n in d.nodes[:2]:
                bnode[n] = d.name
        elif d.kind == 'E':
            # ngspice E: Exxx Nout+ Nout- NC+ NC- gain -> outputs nodes[:2]
            # [E spike-3: old code tagged nodes[2:4] (the sense pair)].
            for n in d.nodes[:2]:
                bnode[n] = d.name
        elif d.kind == 'H':
            # Hxxx N+ N- Vnam ... : transresistance voltage output nodes[:2].
            for n in d.nodes[:2]:
                bnode[n] = d.name
        elif d.kind == 'V' and re.search(r'\b(pwl|pulse|sin)\s*\(', d.raw, re.I):
            for n in d.nodes[:2]:
                bnode[n] = d.name
    out = [base_lines[0]]
    for raw in base_lines[1:]:
        s = raw.strip()
        low = s.lower()
        if s.startswith('.'):
            if low.startswith('.tran') or low == '.end':
                continue
            if low.startswith('.ic') and t_start != 0:
                continue  # handoff .ic replaces base ICs past seg-0;
                          # seg-0 keeps base .ic (initial excitation, S-h17)
            out.append(raw)
            continue
        parts = s.split()
        kind = parts[0][0].upper() if parts else ''
        if kind == 'B':
            m = re.search(r'\b([VI])\s*=\s*(.*)$', s, re.I)
            if m:
                tag = m.group(1)
                expr = shift_b_expr(m.group(2), t_start)
                if re.search(r'\bpwl\s*\(\s*TIME', expr, re.I):
                    expr = shift_pwl_time_args(expr, t_start)
                assert_no_residual_time(expr, 'B %s' % parts[0])
                out.append('%s %s %s=%s' % (parts[0], ' '.join(parts[1:3]),
                                            tag, expr))
                continue
        if kind in ('V', 'I'):
            s = shift_v_line(s, t_start)
            assert_no_residual_time(s, '%s %s' % (kind, parts[0]))
        if kind in ('E', 'G', 'H', 'F'):
            # No E/G/H/F TIME shifting is implemented (G1 denies them); the
            # residual scan is defense-in-depth so a TIME-bearing VALUE can
            # never propagate silently even if classification is bypassed.
            assert_no_residual_time(s, '%s %s' % (kind, parts[0]))
        if kind == 'L' and parts[0].lower() in hi_low:
            # replace-or-append IC=
            if re.search(r'\bIC\s*=', s, re.I):
                s = re.sub(r'\bIC\s*=\s*[^\s]+', 'IC=%.12g' % hi_low[parts[0].lower()], s, flags=re.I)
            else:
                s = s + ' IC=%.12g' % hi_low[parts[0].lower()]
        if re.search(r'\bIC\s*=', s, re.I) and kind in ('C',):
            cnodes = [p.lower() for p in parts[1:3]]
            if any(n in hv_low for n in cnodes):
                s = re.sub(r'\bIC\s*=\s*[^\s]+', '', s, flags=re.I)  # device IC overrides .ic (circuits §1)
        out.append(s)
    for node, val in (handoff_v or {}).items():
        if node.lower() in bnode:
            raise ValueError('G6: refusing .ic on driven node %s (%s)' % (node, bnode[node]))
        out.append('.ic v(%s)=%.12g' % (node, val))
    out.append('.tran %s %s 0 %s%s' % ('%.12g' % tmax_seg, '%.12g' % span, '%.12g' % tmax_seg, ' UIC' if use_uic else ''))
    out.append('.end')
    return out


def run_segmented(base_lines, vecs, t_stop, windows, tmax_coarse, tmax_fine,
                  handoff_nodes, handoff_l=None, timeout=120,
                  resonant_period=None):
    """Coarse + fine-window chain. Returns dict with stitched series + gates.

    handoff_nodes: cap/state node names whose voltages carry via .ic.
    handoff_l: list of inductor names whose currents carry via device IC=.
    resonant_period: REQUIRED (USER-SUPPLIED BOUND) when any L winding shares
      a non-ground node with a C (detect_resonant): min relevant oscillation
      period; both timesteps must satisfy tmax <= period/50. Without it the
      run REFUSEs (automatic resonant coarse adequacy is unprovable [E
      spike-4]). The /50 divisor holds ~0.5deg/period Q-independently, but
      total phase = per-period x periods, so for long runs supply a tighter
      bound (period/100, /200 per the spike-4 table) or keep runs short;
      pulses injected into a resonator additionally need the w/10 event bound
      (T0/50 coarse silently steps over a T0/100 event: +99% post-peak [E]).
    Enforces G1 (state class, incl. complete L/K handoff), G2 (residual TIME
    refusal), G3 (window sanity + overlap + quantified-event containment),
    G4 (tmax_fine <= event bound; both timesteps <= tau_min/1000; per-seg
    EVENT on fine segs), G5 continuity (all vecs + all handoff currents),
    G6 seam hygiene (no .ic on driven nodes).
    G2/G6 rewrite failures return REFUSE (never propagate, never silent).
    """
    handoff_l = list(handoff_l or [])
    # G1: state class (caller-side classify_state alone is not enforcement).
    ok, rs, info = classify_state(base_lines, handoff_l_names=handoff_l)
    if not ok:
        return {'verdict': 'REFUSE', 'reasons': ['G1: %s' % rs]}
    # G5 completeness: every C node and every L must be handed off and observed.
    devices, _, _, _, _ = V.parse_netlist(base_lines)
    cnodes = sorted({n for d in devices if d.kind == 'C' for n in d.nodes
                     if n != '0'})
    lnames = sorted({d.name for d in devices if d.kind == 'L'})
    hn_low = set(n.lower() for n in (handoff_nodes or []))
    hl_low = set(l.lower() for l in handoff_l)
    missing_v = [n for n in cnodes if n not in hn_low]
    if missing_v:
        return {'verdict': 'REFUSE',
                'reasons': ['G5: C-state nodes missing from handoff: %s (S8 ghost)' % missing_v]}
    missing_l = [l for l in lnames if l.lower() not in hl_low]
    if missing_l:
        return {'verdict': 'REFUSE',
                'reasons': ['G5: inductor currents missing from handoff: %s (S11)' % missing_l]}
    missing_vec = [n for n in (handoff_nodes or [])
                   if not any(v.lower() == 'v(%s)' % n.lower() for v in vecs)]
    if missing_vec:
        return {'verdict': 'REFUSE',
                'reasons': ['G5: handoff nodes not in vecs (continuity unverifiable): %s' % missing_vec]}
    bounds = sorted(windows)
    # G3: margins sane + overlap/unsorted refusal (backward-time plans).
    for s, e in bounds:
        if s < 0 or e > t_stop or e <= s:
            return {'verdict': 'REFUSE', 'reasons': ['G3: window outside [0,T]']}
    # G3/G4 bounds from bandwidth + tau estimates.
    try:
        t_req_event, detail = BS.required_tmax_for_netlist(base_lines)
    except Exception as e:  # pragma: no cover - defensive
        return {'verdict': 'REFUSE', 'reasons': ['G3: bandwidth parse failed (%s)' % e]}
    if t_req_event not in (None, 'unknown') and tmax_fine > t_req_event * 1.001:
        return {'verdict': 'REFUSE',
                'reasons': ['G4: tmax_fine=%g exceeds event bound %g (fine=w/3-class smear)' % (tmax_fine, t_req_event)]}
    tau_min = estimate_tau_min(base_lines)
    tau_bound = tau_min / TAU_DIV if tau_min else None
    if tau_bound is not None:
        if tmax_coarse > tau_bound * 1.001:
            return {'verdict': 'REFUSE',
                    'reasons': ['G4: tmax_coarse=%g exceeds tau_min/1000=%g (background truncation)' % (tmax_coarse, tau_bound)]}
        if tmax_fine > tau_bound * 1.001:
            return {'verdict': 'REFUSE',
                    'reasons': ['G4: tmax_fine=%g exceeds tau_min/1000=%g (driven-dynamics smear)' % (tmax_fine, tau_bound)]}
    tau_l = estimate_tau_l(base_lines)
    tau_l_bound = tau_l / TAU_L_DIV if tau_l else None
    if tau_l_bound is not None:
        if tmax_coarse > tau_l_bound * 1.001:
            return {'verdict': 'REFUSE',
                    'reasons': ['G4: tmax_coarse=%g exceeds tau_L/100=%g (inductive background truncation [E spike-3: K2 2mV, small-L 4.75mV])' % (tmax_coarse, tau_l_bound)]}
        if tmax_fine > tau_l_bound * 1.001:
            return {'verdict': 'REFUSE',
                    'reasons': ['G4: tmax_fine=%g exceeds tau_L/100=%g (inductive driven smear)' % (tmax_fine, tau_l_bound)]}
    sin_bound = vsin_bound(base_lines)
    if sin_bound is not None:
        if tmax_coarse > sin_bound * 1.001:
            return {'verdict': 'REFUSE',
                    'reasons': ['G4: tmax_coarse=%g exceeds SIN period/50=%g (continuous drive unresolvable outside windows [E spike-3 K1b 185mV])' % (tmax_coarse, sin_bound)]}
        if tmax_fine > sin_bound * 1.001:
            return {'verdict': 'REFUSE',
                    'reasons': ['G4: tmax_fine=%g exceeds SIN period/50=%g' % (tmax_fine, sin_bound)]}
    res_l = detect_resonant(base_lines)
    if res_l:
        # Resonant LC adequacy is not auto-provable (G4): L/R over-tight and
        # T0 unextractable; fixed N cannot bound accumulated phase [E spike-4:
        # ideal LC 1mH/1uF N=50 PASS-local yet 46.7deg drift over 100 periods
        # with amp err -0.14%; L/R bound would demand 200ps-class vs needed
        # T0/50]. Fail closed unless the user supplies min period.
        if resonant_period is None or resonant_period <= 0:
            return {'verdict': 'REFUSE',
                    'reasons': ['G4: resonant L(%s) shares node with C: automatic coarse adequacy unprovable '
                                '(spike-4: T0/50 exact per-period yet 46.7deg/100periods; supply resonant_period=min '
                                'oscillation period, enforced as tmax<=period/50, tighten for long runs)' % ','.join(res_l)]}
        res_bound = resonant_period / RES_DIV
        if tmax_coarse > res_bound * 1.001:
            return {'verdict': 'REFUSE',
                    'reasons': ['G4: tmax_coarse=%g exceeds resonant period/50=%g (L=%s; per-period ~0.5deg, '
                                'accumulates x periods [E spike-4])' % (tmax_coarse, res_bound, ','.join(res_l))]}
        if tmax_fine > res_bound * 1.001:
            return {'verdict': 'REFUSE',
                    'reasons': ['G4: tmax_fine=%g exceeds resonant period/50=%g (L=%s)' % (tmax_fine, res_bound, ','.join(res_l))]}
    if t_req_event not in (None, 'unknown'):
        evs = _quantified_events(base_lines) or []
        for ev in evs:
            if not any(s < ev < e for s, e in bounds):
                return {'verdict': 'REFUSE',
                        'reasons': ['G3: quantified event at %g inside no fine window (S7 coarse-miss)' % ev]}
    # segment plan: [0,w0s],[w0s,w0e],... coarse outside, fine inside
    plan = []
    cur = 0.0
    for s, e in bounds:
        if s < cur:
            return {'verdict': 'REFUSE',
                    'reasons': ['G3: overlapping/unsorted windows (time-travel double-count)']}
        if s > cur:
            plan.append((cur, s, tmax_coarse, 'coarse'))
        plan.append((s, e, tmax_fine, 'fine'))
        cur = e
    if cur < t_stop:
        plan.append((cur, t_stop, tmax_coarse, 'coarse'))
    stitched = {v: [] for v in vecs}
    total_pts, total_wall, reasons = 0, 0.0, []
    prev_end_v, prev_end_i = {}, {}
    for idx, (a, b, tm, kind) in enumerate(plan):
        span = b - a
        if idx == 0:
            hv, hi = {}, {}
        else:
            hv = {}
            for n in handoff_nodes:
                key = next((v for v in vecs
                            if v.lower() == 'v(%s)' % n.lower()), None)
                if key is not None and key in prev_end_v:
                    hv[n] = prev_end_v[key]
            hi = {l: prev_end_i[l] for l in handoff_l if l in prev_end_i}
        try:
            seg = _rewrite_segment_lines(base_lines, a, span, hv, hi, tm, True)
        except ValueError as ex:
            return {'verdict': 'REFUSE', 'reasons': ['%s' % ex], 'plan': plan}
        r, s = tran_run(seg, vecs + (['i(%s)' % l for l in handoff_l if 'i(%s)' % l not in vecs]),
                        timeout=timeout, tag='windowed')
        # G4: EVENT coverage on fine segs (coarse segs carry no quantified
        # events by the containment check above; coarse adequacy is gated
        # statically via tau_min/1000 instead).
        cov_req = tmax_fine if kind == 'fine' else None
        chk = RT.check_runtime(r, s, seg, tmax_required=cov_req)
        if kind == 'fine' and any(x.startswith('EVENT') for x in chk['reasons']):
            return {'verdict': 'REFUSE', 'reasons': ['G4: fine window violates EVENT-COVERAGE: %s' % chk['reasons']],
                    'plan': plan}
        if r.get('rc') != 0 or r.get('timeout'):
            return {'verdict': 'FAIL', 'reasons': ['segment %d rc/timeout' % idx], 'plan': plan}
        # G5 continuity (skip first segment): compare seg start vs prev end
        for v in vecs:
            pts = s.get(v, [])
            if not pts:
                return {'verdict': 'FAIL', 'reasons': ['segment %d empty series %s' % (idx, v)], 'plan': plan}
            if idx > 0:
                jump = abs(pts[0][1] - prev_end_v.get(v, pts[0][1]))
                scale = max(1.0, abs(prev_end_v.get(v, 0.0)))
                if jump / scale >= G5_REL and jump >= G5_ABS:
                    return {'verdict': 'REFUSE', 'reasons': ['G5: stitch jump %.3g on %s at %.3g' % (jump, v, a)],
                            'plan': plan}
            prev_end_v[v] = pts[-1][1]
        for l in handoff_l:
            key = 'i(%s)' % l
            pts = s.get(key, [])
            if not pts:
                return {'verdict': 'FAIL', 'reasons': ['segment %d empty current series %s' % (idx, key)], 'plan': plan}
            if idx > 0 and l in prev_end_i:
                jump = abs(pts[0][1] - prev_end_i[l])
                if jump >= max(G5_ABS, G5_REL * max(1.0, abs(prev_end_i[l]))):
                    return {'verdict': 'REFUSE', 'reasons': ['G5: inductor jump %.3g on %s' % (jump, key)],
                            'plan': plan}
            prev_end_i[l] = pts[-1][1]
        for v in vecs:
            stitched[v] += [(t + a, val) for t, val in s[v]]
        total_pts += r.get('npts') or 0
        total_wall += r.get('wall_ms') or 0.0
    return {'verdict': 'PASS', 'reasons': reasons or ['gates G1-G6 held (G1/G3/G4-completeness/G5/G2-residual enforced)'],
            'series': stitched, 'npts': total_pts, 'wall_ms': total_wall, 'plan': plan}


def pwl_pulse_source(name, n1, n2, t0, width, height, t_end, edge=1e-9):
    """Restate a canonical 0->H->0 u-pulse as a PWL source line (breakpoints)."""
    return ('%s %s %s PWL(0 0 %.12g 0 %.12g %.12g %.12g %.12g %.12g 0 %.12g 0)'
            % (name, n1, n2, t0 - edge, t0, height, t0 + width, height,
               t0 + width + edge, t_end))
