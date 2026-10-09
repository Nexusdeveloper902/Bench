"""Native-resume regression tests (Spike 6).

Each test compares the module's native pause path against a raw
breakpoint-continuous reference (same breakpoint grid, no pause): the two
must be numerically identical. That isolates pause/resume state
preservation from breakpoint-grid effects.

Run: python3 exp-native/tests/test_native.py  (ALL PASS expected)
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_NAT = os.path.dirname(_HERE)
if _NAT not in sys.path:
    sys.path.insert(0, _NAT)
sys.path.insert(0, '<repo>/experiments/coupling-proto/src')

from native_resume import SimRequest, run_simulation
from ngspice_ctypes import NgSpice

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("ok    " if cond else "FAIL  ") + name + (" " + str(detail) if detail else ""))


def raw_bkpt_cont(lines, tmid, observables):
    ng = NgSpice()
    ng.init()
    ng.circ(list(lines))
    ng.lib.ngSpice_SetBkpt(float(tmid))
    ng.command("run")
    try:
        cur = ng.lib.ngSpice_CurPlot()
        plot = cur.decode() if isinstance(cur, bytes) else cur
    except Exception:
        plot = ""
    t = None
    if plot:
        try:
            t = ng.vec("%s.time" % plot)
        except Exception:
            t = None
    if not t:
        t = ng.measure_time_vec()
    t = list(t)
    vecs = {}
    for v in observables:
        d = None
        if plot:
            try:
                d = ng.vec("%s.%s" % (plot, v))
            except Exception:
                d = None
        vecs[v] = list(d or ng.vec(v) or [])
    return t, vecs


def cmp_series(name, t_ref, vecs_ref, res, tol_t=1e-12, tol_v=1e-9):
    if not res.used_pauses:
        check(name, False, "pause path not used fb=%s" % res.fallback_reason)
        return
    t = res.time
    if len(t) != len(t_ref):
        check(name, False, "len %d vs %d" % (len(t), len(t_ref)))
        return
    mdt = max(abs(a - b) for a, b in zip(t, t_ref))
    ok = mdt <= tol_t
    det = "max|dt|=%.2e" % mdt
    for v in vecs_ref:
        d = [x for _, x in res.series.get(v, [])]
        r = vecs_ref[v]
        if len(d) != len(r):
            check(name, False, "%s len %d vs %d" % (v, len(d), len(r)))
            return
        peak = max([abs(x) for x in r] or [0.0])
        md = max([abs(a - b) for a, b in zip(d, r)] or [0.0])
        rel = md / max(peak, 1e-12)
        ok = ok and rel <= 1e-9
        det += " %s %.2e(rel %.1e)" % (v, md, rel)
    check(name, ok, det)


RC = ["t", "V1 in 0 PULSE(0 5 1e-6 10n 10n 3e-6 10e-6)", "R1 in out 1k",
      "C1 out 0 1n", ".tran 10n 10e-06 0 10n", ".end"]
DIODE = ["t", "V1 in 0 PULSE(0 5 1e-6 10n 10n 3e-6 10e-6)", "R1 in out 100",
         "D1 out 0 DMOD", ".model DMOD D(IS=1e-14 N=1.5 TT=20n CJO=2p VJ=0.7 M=0.5)",
         ".tran 10n 10e-06 0 10n", ".end"]
DDT = ["t", "V1 in 0 PULSE(0 5 1e-6 10n 10n 3e-6 10e-6)", "R1 in mid 1k",
       "C1 mid 0 1n", "B1 out 0 V=ddt(V(mid))", "R2 out 0 1k",
       ".tran 10n 10e-06 0 10n", ".end"]
COUPLED = ["t", "V1 in 0 PULSE(0 5 1e-6 10n 10n 3e-6 10e-6)", "R1 in a 10",
           "L1 a 0 10u", "L2 b 0 10u", "K1 L1 L2 0.9", "R2 b 0 100",
           ".tran 10n 10e-06 0 10n UIC", ".end"]
TLINE = ["t", "V1 in 0 PULSE(0 5 1e-6 10n 10n 2e-6 10e-6)", "R1 in a 50",
         "T1 a 0 b 0 Z0=50 TD=1e-6", "R2 b 0 50",
         ".tran 10n 10e-06 0 10n", ".end"]
BJT = ["t", "V1 c 0 DC 5", "V2 b 0 PULSE(0 1 1e-6 10n 10n 3e-6 10e-6)",
       "RC c cm 1k", "RB b bm 10k", "Q1 cm bm 0 NPN",
       ".model NPN NPN(IS=1e-16 BF=100 CJC=1p TF=0.3n)",
       ".tran 10n 10e-06 0 10n", ".end"]


def t_easy():
    req = SimRequest(netlist=tuple(RC), observables=("v(out)",), stops=(5e-6,))
    res = run_simulation(req)
    t_ref, vecs_ref = raw_bkpt_cont(RC, 5e-6, ["v(out)"])
    cmp_series("easy RC pause==bkpt-cont", t_ref, vecs_ref, res)


def t_diode_tt():
    req = SimRequest(netlist=tuple(DIODE), observables=("v(out)",), stops=(5e-6,))
    res = run_simulation(req)
    t_ref, vecs_ref = raw_bkpt_cont(DIODE, 5e-6, ["v(out)"])
    cmp_series("diode TT/CJO (prev REFUSE) exact", t_ref, vecs_ref, res)


def t_ddt():
    req = SimRequest(netlist=tuple(DDT), observables=("v(mid)",), stops=(5e-6,))
    res = run_simulation(req)
    t_ref, vecs_ref = raw_bkpt_cont(DDT, 5e-6, ["v(mid)"])
    cmp_series("B ddt (prev REFUSE) exact", t_ref, vecs_ref, res)


def t_coupled():
    req = SimRequest(netlist=tuple(COUPLED), observables=("v(a)", "v(b)"), stops=(5e-6,))
    res = run_simulation(req)
    t_ref, vecs_ref = raw_bkpt_cont(COUPLED, 5e-6, ["v(a)", "v(b)"])
    cmp_series("coupled L (prev REFUSE) exact", t_ref, vecs_ref, res)


def t_tline():
    req = SimRequest(netlist=tuple(TLINE), observables=("v(a)", "v(b)"), stops=(5e-6,))
    res = run_simulation(req)
    t_ref, vecs_ref = raw_bkpt_cont(TLINE, 5e-6, ["v(a)", "v(b)"])
    cmp_series("T-line (prev REFUSE) exact", t_ref, vecs_ref, res)


def t_bjt():
    req = SimRequest(netlist=tuple(BJT), observables=("v(cm)",), stops=(5e-6,))
    res = run_simulation(req)
    t_ref, vecs_ref = raw_bkpt_cont(BJT, 5e-6, ["v(cm)"])
    cmp_series("BJT charge (prev REFUSE) exact", t_ref, vecs_ref, res)


def t_fallback_outside():
    req = SimRequest(netlist=tuple(RC), observables=("v(out)",), stops=(20e-6,))
    res = run_simulation(req)
    ref = run_simulation(SimRequest(netlist=tuple(RC), observables=("v(out)",), stops=()))
    ok = (not res.used_pauses and res.fallback_reason == "stop_outside_interval"
          and len(res.time) == len(ref.time)
          and max(abs(a - b) for a, b in zip(
              [x for _, x in res.series["v(out)"]],
              [x for _, x in ref.series["v(out)"]])) == 0.0)
    check("fallback outside-interval == full sim", ok,
          "fb=%s n=%d" % (res.fallback_reason, res.npts))


def t_fallback_unsorted():
    req = SimRequest(netlist=tuple(RC), observables=("v(out)",), stops=(7e-6, 5e-6))
    res = run_simulation(req)
    check("fallback unsorted stops", not res.used_pauses and res.fallback_reason is not None,
          "fb=%s" % res.fallback_reason)


def t_multi_pause():
    req = SimRequest(netlist=tuple(RC), observables=("v(out)",), stops=(3e-6, 5e-6, 7e-6))
    res = run_simulation(req)
    check("multi-pause exact", res.used_pauses and res.fallback_reason is None
          and abs(res.time[-1] - 10e-6) <= max(1e-12, 10e-6 * 1e-9),
          "n=%d fb=%s" % (res.npts, res.fallback_reason))


def t_sequential_isolation():
    r1 = run_simulation(SimRequest(netlist=tuple(RC), observables=("v(out)",), stops=(5e-6,)))
    other = ["t", "V1 in 0 DC 3", "R1 in out 2k", "C1 out 0 1n",
             ".tran 10n 5e-06 0 10n", ".end"]
    r2 = run_simulation(SimRequest(netlist=tuple(other), observables=("v(out)",), stops=()))
    ok = (r1.used_pauses and abs(r1.time[-1] - 10e-6) < 1e-9
          and abs(r2.time[-1] - 5e-6) < 1e-9)
    check("sequential reuse isolated", ok,
          "t1=%s t2=%s" % (r1.time[-1] if r1.time else None,
                            r2.time[-1] if r2.time else None))


if __name__ == "__main__":
    t_easy()
    t_diode_tt()
    t_ddt()
    t_coupled()
    t_tline()
    t_bjt()
    t_fallback_outside()
    t_fallback_unsorted()
    t_multi_pause()
    t_sequential_isolation()
    print()
    print("PASS %d  FAIL %d" % (len(PASS), len(FAIL)))
    print("ALL PASS" if not FAIL else "FAILURES: %s" % FAIL)
    sys.exit(1 if FAIL else 0)
