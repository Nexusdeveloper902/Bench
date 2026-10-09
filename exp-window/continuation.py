"""Production continuation capability over the validated windowing mechanism.

Experimental mechanism: ``window.py`` (gates G1-G6, validated across four
attack campaigns; see REPORT.md .. REPORT4.md). This module does NOT change
the electrical solver, does NOT expand the supported model class, and does
NOT introduce a generic state-transfer framework. It is a thin planning /
execution split plus a first-class refusal taxonomy over the exact same
gates.

Public API (callers need no knowledge of G1-G6 internals):

    req = ContinuationRequest(netlist=[...], t_stop=..., windows=None,
                              tmax_coarse=..., tmax_fine=...,
                              handoff_nodes=[...], handoff_l=[...],
                              observables=[...], resonant_period=...)
    pres = plan(req)                 # static analysis only, never simulates
    if isinstance(pres, PlanRefused):
        for r in pres.refusals:      # stable RefusalCode, human message,
            print(r.code, r.hint)    # agent-actionable hint
    else:
        eres = execute(pres.plan, req)   # runs the validated segment plan
        # ExecutionOk | ExecutionRefused | ExecutionFailed

Separation:

* :func:`plan` performs every static check the mechanism supports
  (state-class allowlist, handoff completeness, observable mapping, window
  sanity, event containment, timestep bounds, resonant-period requirement,
  plus a dry-run TIME-shift / seam-hygiene pass at every segment start).
  It rejects unsafe requests BEFORE any simulation runs.
* :func:`execute` first validates the plan seal, rejects any post-planning
  mutation (:data:`RefusalCode.STALE_PLAN`), re-runs the static checks
  (defense in depth, so a hand-built plan can never bypass classification),
  then delegates to the validated ``window.run_segmented`` runner. It never
  reinterprets an invalid plan: any static drift refuses, and all dynamic
  checks (EVENT coverage, stitch continuity) keep their fail-closed form.

Refusal taxonomy: every REFUSE path in ``window.py`` maps to a stable
:data:`RefusalCode` (see :func:`classify_reason`). Tests assert codes, not
error text.
"""

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from enum import Enum

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_SAF = os.path.join(os.path.dirname(_HERE), 'exp-safety')
if _SAF not in sys.path:
    sys.path.insert(0, _SAF)

import window as _W


class RefusalCode(str, Enum):
    """Stable refusal classification. Values are asserted by tests."""

    UNSUPPORTED_HIDDEN_STATE = "unsupported_hidden_state"
    INCOMPLETE_STATE_HANDOFF = "incomplete_state_handoff"
    UNSHIFTABLE_TIME = "unshiftable_time"
    UNKNOWN_EVENT_TIMING = "unknown_event_timing"
    EVENT_OUTSIDE_WINDOW = "event_outside_window"
    OVERLAPPING_WINDOWS = "overlapping_windows"
    INVALID_WINDOW = "invalid_window"
    COARSE_BOUND_EXCEEDED = "coarse_bound_exceeded"
    FINE_BOUND_EXCEEDED = "fine_bound_exceeded"
    RESONANT_PERIOD_UNAVAILABLE = "resonant_period_unavailable"
    INVALID_OBSERVABLE_MAPPING = "invalid_observable_mapping"
    DRIVEN_NODE_INITIALIZATION = "driven_node_initialization"
    UNSUPPORTED_INCLUDED_CONTENT = "unsupported_included_content"
    SEAM_DISCONTINUITY = "seam_discontinuity"
    STALE_PLAN = "stale_plan"
    INVALID_PLAN = "invalid_plan"
    UNCLASSIFIED_REFUSAL = "unclassified_refusal"


_HINTS = {
    RefusalCode.UNSUPPORTED_HIDDEN_STATE:
        "Replace the device with a supported class (R/C/plain-D/memoryless, "
        "or L/K with explicit IC handoff). This state has no restore path "
        "(.ic/UIC cannot carry it); continuation cannot be made safe.",
    RefusalCode.INCOMPLETE_STATE_HANDOFF:
        "Add every missing node/winding to handoff_nodes/handoff_l so the "
        "full supported state is carried across seams.",
    RefusalCode.UNSHIFTABLE_TIME:
        "Rewrite the TIME expression into a provably shiftable form "
        "(u-pulse difference, TIME comparison, bare pwl(TIME,...), or "
        "numeric V/I PWL/PULSE/SIN) or remove TIME from the expression.",
    RefusalCode.UNKNOWN_EVENT_TIMING:
        "Supply explicit event windows plus a fine timestep bound: the "
        "event timing is not statically quantifiable, so auto-derivation "
        "is refused.",
    RefusalCode.EVENT_OUTSIDE_WINDOW:
        "Widen or add fine windows so every quantified event lies strictly "
        "inside a fine window; events left to coarse steps are refused.",
    RefusalCode.OVERLAPPING_WINDOWS:
        "Sort windows and remove overlaps/ time-travel before re-planning.",
    RefusalCode.INVALID_WINDOW:
        "Constrain every window to [0, t_stop] with end > start.",
    RefusalCode.COARSE_BOUND_EXCEEDED:
        "Reduce tmax_coarse to within the stated bound (background/ "
        "continuous-drive adequacy); coarse savings cannot be bought with "
        "an unresolving grid.",
    RefusalCode.FINE_BOUND_EXCEEDED:
        "Reduce tmax_fine to within the stated bound (event resolution "
        "and driven-dynamics adequacy).",
    RefusalCode.RESONANT_PERIOD_UNAVAILABLE:
        "Supply resonant_period = minimum relevant oscillation period "
        "(enforced as tmax <= period/50; tighten to /100, /200 for long "
        "runs since phase accumulates per period).",
    RefusalCode.INVALID_OBSERVABLE_MAPPING:
        "Add every handoff node to observables so seam continuity is "
        "verifiable; unobserved state handoff is refused.",
    RefusalCode.DRIVEN_NODE_INITIALIZATION:
        "Remove the driven node from handoff_nodes: a behavioral or "
        "time-varying source enforces that node, so .ic would fight it.",
    RefusalCode.UNSUPPORTED_INCLUDED_CONTENT:
        "Inline the referenced file content into the netlist (or remove "
        "the directive): file content is invisible to static "
        "classification while ngspice expands it.",
    RefusalCode.SEAM_DISCONTINUITY:
        "Do not loosen the tolerance: the seam disagrees beyond the "
        "explicit continuity bound. Check for hidden state, kink-on-seam, "
        "or an event left in coarse.",
    RefusalCode.STALE_PLAN:
        "Re-run plan() after the change; a plan is valid only for the "
        "exact request it was derived from. Never reuse plans across edits.",
    RefusalCode.INVALID_PLAN:
        "Obtain plans only via plan(); hand-constructed plans are rejected "
        "and re-validated against classification before any simulation.",
    RefusalCode.UNCLASSIFIED_REFUSAL:
        "Fail-closed catch-all: update the taxonomy to name this mode.",
}


@dataclass(frozen=True)
class Refusal:
    """One fail-closed refusal: stable code + human/agent detail."""

    code: RefusalCode
    message: str
    hint: str
    gate: str = ""
    detail: str = ""


@dataclass(frozen=True)
class ContinuationRequest:
    """Smallest clean statement of a segmented-simulation request.

    windows=None means "auto-derive from quantifiable B-source events";
    unquantifiable timing then refuses with UNKNOWN_EVENT_TIMING instead
    of guessing. observables are the ngspice vectors to stitch
    (e.g. ``v(n)``); inductor currents are appended automatically.
    """

    netlist: tuple
    t_stop: float
    windows: object  # tuple[tuple[float,float], ...] | None
    tmax_coarse: float
    tmax_fine: float
    handoff_nodes: tuple = ()
    handoff_l: tuple = ()
    observables: tuple = ()
    resonant_period: object = None
    timeout: float = 120


@dataclass(frozen=True)
class SegmentPlan:
    """Validated execution plan. Opaque: build only via :func:`plan`.

    ``fingerprint`` binds the plan to the exact request it was derived
    from; :func:`execute` recomputes it, so manual construction or
    post-planning mutation refuses instead of simulating.
    """

    request: ContinuationRequest
    fingerprint: str
    segments: tuple  # ((t_start, t_end, tmax, kind), ...)
    windows: tuple
    auto_windows: bool
    bounds: str  # JSON snapshot of the enforced timestep bounds
    capability: str  # JSON snapshot of the derived state capability


@dataclass
class PlanOk:
    plan: SegmentPlan


@dataclass
class PlanRefused:
    refusals: list


@dataclass
class ExecutionOk:
    series: dict
    npts: int
    wall_ms: float
    segments: tuple


@dataclass
class ExecutionRefused:
    refusals: list


@dataclass
class ExecutionFailed:
    reasons: list


# ---------------------------------------------------------------------------
# reason classification (every window.py REFUSE path -> stable code)
# ---------------------------------------------------------------------------

def _ref(code, message, gate="", detail=""):
    code = code if isinstance(code, RefusalCode) else RefusalCode(code)
    return Refusal(code=code, message=message, hint=_HINTS[code],
                   gate=gate, detail=detail or message)


def classify_reason(reason):
    """Map one raw window.py reason string to a Refusal (stable code)."""
    t = reason or ""
    gate = t[:2] if t[:1] == 'G' and len(t) > 2 and t[1].isdigit() else ""

    def mk(code):
        return _ref(code, t, gate=gate)

    if 'STALE-PLAN' in t or 'request changed after planning' in t:
        return mk(RefusalCode.STALE_PLAN)
    if 'INVALID-PLAN' in t or 'hand-constructed plan' in t:
        return mk(RefusalCode.INVALID_PLAN)
    if 'K coupling' in t and 'missing winding' in t:
        return mk(RefusalCode.INCOMPLETE_STATE_HANDOFF)
    if 'C-state nodes missing from handoff' in t or \
            'inductor currents missing from handoff' in t:
        return mk(RefusalCode.INCOMPLETE_STATE_HANDOFF)
    if 'handoff nodes not in vecs' in t:
        return mk(RefusalCode.INVALID_OBSERVABLE_MAPPING)
    if '.include' in t or '.lib' in t:
        # G1 file-content invisibility denial (only this denial names them).
        if 'present' in t or 'invisible' in t:
            return mk(RefusalCode.UNSUPPORTED_INCLUDED_CONTENT)
    for frag in ('stored charge', 'junction charge', 'base/channel/gate charge',
                 'XSPICE', 'transmission line', 'opaque subcircuit',
                 'exotic kind', 'LAPLACE/FREQ', 'OSDI', 'ddt() history'):
        if frag in t:
            return mk(RefusalCode.UNSUPPORTED_HIDDEN_STATE)
    for frag in ('behavioral', 'carries TIME', 'TIME-bearing .func',
                 'TIME mediated by .param', 'G2:'):
        if frag in t:
            return mk(RefusalCode.UNSHIFTABLE_TIME)
    if 'bandwidth parse failed' in t:
        return mk(RefusalCode.UNKNOWN_EVENT_TIMING)
    if 'inside no fine window' in t:
        return mk(RefusalCode.EVENT_OUTSIDE_WINDOW)
    if 'overlapping/unsorted' in t:
        return mk(RefusalCode.OVERLAPPING_WINDOWS)
    if 'window outside' in t:
        return mk(RefusalCode.INVALID_WINDOW)
    if 'supply resonant_period' in t or ('resonant_period' in t and
                                          'USER-SUPPLIED' in t):
        return mk(RefusalCode.RESONANT_PERIOD_UNAVAILABLE)
    if 'tmax_coarse' in t and 'exceeds' in t:
        return mk(RefusalCode.COARSE_BOUND_EXCEEDED)
    if 'tmax_fine' in t and 'exceeds' in t:
        return mk(RefusalCode.FINE_BOUND_EXCEEDED)
    if 'EVENT-COVERAGE' in t:
        return mk(RefusalCode.FINE_BOUND_EXCEEDED)
    if 'stitch jump' in t or 'inductor jump' in t:
        return mk(RefusalCode.SEAM_DISCONTINUITY)
    if 'refusing .ic on driven node' in t:
        return mk(RefusalCode.DRIVEN_NODE_INITIALIZATION)
    if 'user must supply windows' in t or 'unknown' in t.lower().split(':')[0]:
        return mk(RefusalCode.UNKNOWN_EVENT_TIMING)
    return mk(RefusalCode.UNCLASSIFIED_REFUSAL)


# ---------------------------------------------------------------------------
# fingerprinting (post-planning mutation gate)
# ---------------------------------------------------------------------------

def _canon_netlist(lines):
    return [l.rstrip() for l in lines]


def _fingerprint(req):
    canon = {
        'netlist': _canon_netlist(list(req.netlist)),
        't_stop': repr(float(req.t_stop)),
        'windows': None if req.windows is None
        else [[repr(float(a)), repr(float(b))] for a, b in req.windows],
        'tmax_coarse': repr(float(req.tmax_coarse)),
        'tmax_fine': repr(float(req.tmax_fine)),
        'handoff_nodes': sorted(n.lower() for n in (req.handoff_nodes or ())),
        'handoff_l': sorted(l.lower() for l in (req.handoff_l or ())),
        'observables': list(req.observables or ()),
        'resonant_period': None if req.resonant_period is None
        else repr(float(req.resonant_period)),
    }
    return hashlib.sha256(
        json.dumps(canon, sort_keys=True).encode()).hexdigest()


def _field_diff(old, new):
    diffs = []
    if _canon_netlist(list(old.netlist)) != _canon_netlist(list(new.netlist)):
        diffs.append('netlist (model/source/include content changed)')
    for f in ('t_stop', 'tmax_coarse', 'tmax_fine', 'resonant_period'):
        a, b = getattr(old, f), getattr(new, f)
        sa = None if a is None else repr(float(a))
        sb = None if b is None else repr(float(b))
        if sa != sb:
            diffs.append('%s (%s -> %s)' % (f, sa, sb))
    if (old.windows is None) != (new.windows is None) or \
            (old.windows is not None and
             [[repr(float(a)), repr(float(b))] for a, b in old.windows] !=
             [[repr(float(a)), repr(float(b))] for a, b in new.windows]):
        diffs.append('windows')
    if sorted(n.lower() for n in (old.handoff_nodes or ())) != \
            sorted(n.lower() for n in (new.handoff_nodes or ())):
        diffs.append('handoff_nodes')
    if sorted(l.lower() for l in (old.handoff_l or ())) != \
            sorted(l.lower() for l in (new.handoff_l or ())):
        diffs.append('handoff_l')
    if list(old.observables or ()) != list(new.observables or ()):
        diffs.append('observables')
    return diffs


# ---------------------------------------------------------------------------
# static analysis (the plan half; no simulation)
# ---------------------------------------------------------------------------

def _static_checks(req, windows):
    """Mirror of run_segmented's static prefix. Returns (refusals, segments,
    bounds_info, capability). refusals is a list of raw reason strings."""
    lines = list(req.netlist)
    vecs = list(req.observables or ())
    handoff_nodes = list(req.handoff_nodes or ())
    handoff_l = list(req.handoff_l or ())
    t_stop = float(req.t_stop)
    tmax_coarse = float(req.tmax_coarse)
    tmax_fine = float(req.tmax_fine)
    resonant_period = req.resonant_period

    ok, rs, info = _W.classify_state(lines, handoff_l_names=handoff_l)
    if not ok:
        return (['G1: %s' % rs], None, None, None)

    import validator as V
    devices, _, _, _, _ = V.parse_netlist(lines)
    cnodes = sorted({n for d in devices if d.kind == 'C' for n in d.nodes
                     if n != '0'})
    lnames = sorted({d.name for d in devices if d.kind == 'L'})
    hn_low = set(n.lower() for n in handoff_nodes)
    hl_low = set(l.lower() for l in handoff_l)
    missing_v = [n for n in cnodes if n not in hn_low]
    if missing_v:
        return (['G5: C-state nodes missing from handoff: %s (S8 ghost)'
                 % missing_v], None, None, None)
    missing_l = [l for l in lnames if l.lower() not in hl_low]
    if missing_l:
        return (['G5: inductor currents missing from handoff: %s (S11)'
                 % missing_l], None, None, None)
    missing_vec = [n for n in handoff_nodes
                   if not any(v.lower() == 'v(%s)' % n.lower() for v in vecs)]
    if missing_vec:
        return (['G5: handoff nodes not in vecs (continuity unverifiable): %s'
                 % missing_vec], None, None, None)

    bounds = sorted(windows)
    for s, e in bounds:
        if s < 0 or e > t_stop or e <= s:
            return (['G3: window outside [0,T]'], None, None, None)

    import bsource as BS
    try:
        t_req_event, detail = BS.required_tmax_for_netlist(lines)
    except Exception as e:
        return (['G3: bandwidth parse failed (%s)' % e], None, None, None)
    if t_req_event not in (None, 'unknown') and \
            tmax_fine > t_req_event * 1.001:
        return (['G4: tmax_fine=%g exceeds event bound %g '
                 '(fine=w/3-class smear)' % (tmax_fine, t_req_event)],
                None, None, None)

    tau_min = _W.estimate_tau_min(lines)
    tau_bound = tau_min / _W.TAU_DIV if tau_min else None
    if tau_bound is not None:
        if tmax_coarse > tau_bound * 1.001:
            return (['G4: tmax_coarse=%g exceeds tau_min/1000=%g '
                     '(background truncation)' % (tmax_coarse, tau_bound)],
                    None, None, None)
        if tmax_fine > tau_bound * 1.001:
            return (['G4: tmax_fine=%g exceeds tau_min/1000=%g '
                     '(driven-dynamics smear)' % (tmax_fine, tau_bound)],
                    None, None, None)
    tau_l = _W.estimate_tau_l(lines)
    tau_l_bound = tau_l / _W.TAU_L_DIV if tau_l else None
    if tau_l_bound is not None:
        if tmax_coarse > tau_l_bound * 1.001:
            return (['G4: tmax_coarse=%g exceeds tau_L/100=%g '
                     '(inductive background truncation)' % (tmax_coarse, tau_l_bound)],
                    None, None, None)
        if tmax_fine > tau_l_bound * 1.001:
            return (['G4: tmax_fine=%g exceeds tau_L/100=%g '
                     '(inductive driven smear)' % (tmax_fine, tau_l_bound)],
                    None, None, None)
    sin_bound = _W.vsin_bound(lines)
    if sin_bound is not None:
        if tmax_coarse > sin_bound * 1.001:
            return (['G4: tmax_coarse=%g exceeds SIN period/50=%g '
                     '(continuous drive unresolvable outside windows)'
                     % (tmax_coarse, sin_bound)], None, None, None)
        if tmax_fine > sin_bound * 1.001:
            return (['G4: tmax_fine=%g exceeds SIN period/50=%g'
                     % (tmax_fine, sin_bound)], None, None, None)
    res_l = _W.detect_resonant(lines)
    if res_l:
        if resonant_period is None or resonant_period <= 0:
            return (['G4: resonant L(%s) shares node with C: automatic coarse '
                     'adequacy unprovable (USER-SUPPLIED BOUND REQUIRED; '
                     'supply resonant_period=min oscillation period)'
                     % ','.join(res_l)], None, None, None)
        res_bound = resonant_period / _W.RES_DIV
        if tmax_coarse > res_bound * 1.001:
            return (['G4: tmax_coarse=%g exceeds resonant period/50=%g '
                     '(L=%s)' % (tmax_coarse, res_bound, ','.join(res_l))],
                    None, None, None)
        if tmax_fine > res_bound * 1.001:
            return (['G4: tmax_fine=%g exceeds resonant period/50=%g (L=%s)'
                     % (tmax_fine, res_bound, ','.join(res_l))],
                    None, None, None)

    if t_req_event not in (None, 'unknown'):
        evs = _W._quantified_events(lines) or []
        for ev in evs:
            if not any(s < ev < e for s, e in bounds):
                return (['G3: quantified event at %g inside no fine window '
                         '(S7 coarse-miss)' % ev], None, None, None)

    # segment plan: identical construction to run_segmented.
    segs = []
    cur = 0.0
    for s, e in bounds:
        if s < cur:
            return (['G3: overlapping/unsorted windows '
                     '(time-travel double-count)'], None, None, None)
        if s > cur:
            segs.append((cur, s, tmax_coarse, 'coarse'))
        segs.append((s, e, tmax_fine, 'fine'))
        cur = e
    if cur < t_stop:
        segs.append((cur, t_stop, tmax_coarse, 'coarse'))

    # dry-run TIME-shift + seam-hygiene at every segment start: surfaces
    # G2/G6 before any simulation runs.
    for idx, (a, b, tm, _kind) in enumerate(segs):
        span = b - a
        if idx == 0:
            hv, hi = {}, {}
        else:
            hv = {n: 0.0 for n in handoff_nodes}
            hi = {l: 0.0 for l in handoff_l}
        try:
            _W._rewrite_segment_lines(lines, a, span, hv, hi, tm, True)
        except ValueError as ex:
            return (['%s' % ex], None, None, None)

    bounds_info = {
        'event_bound': t_req_event if t_req_event != 'unknown' else 'unknown',
        'tau_min': tau_min, 'tau_bound': tau_bound,
        'tau_l': tau_l, 'tau_l_bound': tau_l_bound,
        'sin_bound': sin_bound,
        'resonant_windings': res_l,
        'resonant_bound': (resonant_period / _W.RES_DIV)
        if res_l and resonant_period else None,
    }
    capability = {
        'inductors': lnames,
        'k_windings': sorted(info.get('K_windings', [])),
        'cap_nodes': cnodes,
        'state_class': 'R/C/plain-D/memoryless%s%s' % (
            ' +L(%s)' % ','.join(lnames) if lnames else '',
            ' +K(%s)' % ','.join(sorted(info.get('K_windings', [])))
            if info.get('K_windings') else ''),
    }
    return ([], segs, bounds_info, capability)


def plan(req):
    """Analyze a continuation request. Never simulates.

    Returns PlanOk (validated SegmentPlan) or PlanRefused (stable
    Refusal list). windows=None auto-derives; unquantifiable timing
    refuses with UNKNOWN_EVENT_TIMING.
    """
    if not req.netlist or len(list(req.netlist)) < 2:
        raise ValueError('netlist must include a title line plus content')
    if not (req.t_stop and float(req.t_stop) > 0):
        raise ValueError('t_stop must be positive')
    if not (req.tmax_coarse and float(req.tmax_coarse) > 0) or \
            not (req.tmax_fine and float(req.tmax_fine) > 0):
        raise ValueError('tmax_coarse/tmax_fine must be positive')

    lines = list(req.netlist)
    auto = req.windows is None
    if auto:
        try:
            wins, _t_req, _detail, verdict = \
                _W.derive_windows(lines, float(req.tmax_coarse))
        except Exception as e:
            return PlanRefused([_ref(
                RefusalCode.UNKNOWN_EVENT_TIMING,
                'event timing not derivable: bandwidth parse failed (%s)' % e,
                gate='G3', detail=str(e))])
        if verdict != 'ok':
            return PlanRefused([_ref(
                RefusalCode.UNKNOWN_EVENT_TIMING,
                'event timing not quantifiable (%s); supply explicit '
                'windows plus a fine timestep bound' % verdict,
                gate='G3', detail=str(verdict))])
        windows = tuple((float(s), float(e)) for s, e in wins)
    else:
        try:
            windows = tuple((float(s), float(e)) for s, e in req.windows)
        except (TypeError, ValueError):
            return PlanRefused([_ref(
                RefusalCode.INVALID_WINDOW,
                'windows must be [(start, end), ...] in [0, t_stop]',
                gate='G3')])

    raw, segs, bounds_info, capability = _static_checks(req, windows)
    if raw:
        return PlanRefused([classify_reason(r) for r in raw])
    fp_req = ContinuationRequest(
        netlist=tuple(lines), t_stop=float(req.t_stop),
        windows=None if auto else tuple(
            (float(s), float(e)) for s, e in req.windows),
        tmax_coarse=float(req.tmax_coarse), tmax_fine=float(req.tmax_fine),
        handoff_nodes=tuple(req.handoff_nodes or ()),
        handoff_l=tuple(req.handoff_l or ()),
        observables=tuple(req.observables or ()),
        resonant_period=None if req.resonant_period is None
        else float(req.resonant_period),
        timeout=float(req.timeout or 120))
    return PlanOk(SegmentPlan(
        request=fp_req, fingerprint=_fingerprint(fp_req),
        segments=tuple(segs), windows=tuple(windows), auto_windows=auto,
        bounds=json.dumps(bounds_info, default=str, sort_keys=True),
        capability=json.dumps(capability, default=str, sort_keys=True)))


# ---------------------------------------------------------------------------
# execution (the run half; validated plan only)
# ---------------------------------------------------------------------------

def execute(plan_obj, req):
    """Execute an already-validated plan. Returns ExecutionOk |
    ExecutionRefused | ExecutionFailed. Never reinterprets an invalid plan:
    seal mismatch, post-planning mutation, or static drift all refuse
    before (or without) simulating.
    """
    if not isinstance(plan_obj, SegmentPlan):
        return ExecutionRefused([_ref(
            RefusalCode.INVALID_PLAN,
            'INVALID-PLAN: hand-constructed plan object '
            '(obtain plans only via plan())',
            gate='PLAN')])
    if plan_obj.fingerprint != _fingerprint(plan_obj.request):
        return ExecutionRefused([_ref(
            RefusalCode.INVALID_PLAN,
            'INVALID-PLAN: hand-constructed plan (seal mismatch)',
            gate='PLAN')])
    # defense in depth: the embedded request re-validates even if the
    # caller bypassed plan().
    raw, _segs, _b, _c = _static_checks(plan_obj.request, plan_obj.windows)
    if raw:
        return ExecutionRefused([classify_reason(r) for r in raw])
    if _fingerprint(req) != plan_obj.fingerprint:
        diffs = _field_diff(plan_obj.request, req)
        return ExecutionRefused([_ref(
            RefusalCode.STALE_PLAN,
            'STALE-PLAN: request changed after planning (%s); re-run '
            'plan() and never reuse plans across edits' % '; '.join(diffs or ['?']),
            gate='PLAN',
            detail='; '.join(diffs))])

    eff_windows = plan_obj.windows
    out = _W.run_segmented(
        list(req.netlist), list(req.observables or ()), float(req.t_stop),
        list(eff_windows), float(req.tmax_coarse), float(req.tmax_fine),
        list(req.handoff_nodes or ()), handoff_l=list(req.handoff_l or ()),
        timeout=float(req.timeout or 120),
        resonant_period=req.resonant_period)
    verdict = out.get('verdict')
    if verdict == 'PASS':
        return ExecutionOk(series=out.get('series', {}),
                           npts=out.get('npts', 0),
                           wall_ms=out.get('wall_ms', 0.0),
                           segments=tuple(out.get('plan', ())))
    if verdict == 'REFUSE':
        return ExecutionRefused(
            [classify_reason(r) for r in out.get('reasons', [])])
    return ExecutionFailed(list(out.get('reasons', ['unknown failure'])))
