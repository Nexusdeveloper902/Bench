"""E03: couple GPIO square wave (AVR surrogate) to real nonlinear ngspice LED circuit.
Tests sync at different GPIO frequencies + strategies:
  A) naive default tmax (edge miss expected)
  B) tmax-bounded (tstep = period/100)
  C) breakpoint on known edges via ngSpice_SetBkpt
Measures: wall-clock, npts, vsrc evals, edge timing error, LED current accuracy,
          overhead split (event-gen vs spice vs analysis).
"""
import time, sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import ctypes
from ngspice_ctypes import NgSpice, GetVSRCData

def run_case(freq_hz, tsim, strategy):
    period = 1.0 / freq_hz
    # event generation (AVR-side surrogate: cycle-accurate toggle list) timed as glue
    t0 = time.perf_counter()
    edges = []
    t = 0.0
    v = 0.0
    while t < tsim:
        edges.append(t)
        t += period / 2
    n_edges = len(edges)
    t_gen = time.perf_counter() - t0

    # choose tran tstep by strategy
    if strategy == "naive":
        tstep = tsim / 50.0          # ngspice default-ish coarse
        use_bkpt = False
    elif strategy == "tmax":
        tstep = min(period / 100.0, tsim / 50.0)
        use_bkpt = False
    elif strategy == "breakpoint":
        tstep = tsim / 50.0
        use_bkpt = True
    else:
        raise ValueError(strategy)

    state = {"calls": 0}
    def gpio_val(tt):
        # square wave starting HIGH (edges[0]=t0; odd edge-count => HIGH)
        import bisect
        idx = bisect.bisect_right(edges, tt)  # number of edges <= tt
        return 5.0 if (idx % 2 == 1) else 0.0

    ng = NgSpice()
    assert ng.init() == 0
    def _vsrc(pvolt, tt, name, ident, ud):
        state["calls"] += 1
        pvolt[0] = gpio_val(tt)
        return 0
    def _isrc(pcur, tt, name, ident, ud):
        pcur[0] = 0.0
        return 0
    def _sync(tt, pdelta, old, redostep, ident, loc, ud):
        return 0
    from ngspice_ctypes import GetISRCData, GetSyncData
    cb = GetVSRCData(_vsrc)
    cb2 = GetISRCData(_isrc)
    cb3 = GetSyncData(_sync)
    ng._cbs.extend([cb, cb2, cb3])  # keep alive
    ident = ctypes.c_int(0)
    rc = ng.lib.ngSpice_Init_Sync(cb, cb2, cb3, ctypes.byref(ident), None)
    assert rc == 0, rc
    net = [
        "gpio led coupling",
        "V1 gpio 0 EXTERNAL",
        "R1 gpio anode 1k",
        "D1 anode 0 DLED",
        ".model DLED D(IS=1e-14 N=1.5 BV=5 IBV=1u)",
        f".tran {tstep} {tsim}",
        ".end",
    ]
    assert ng.circ(net) == 0
    # breakpoints must be set AFTER circ? SetBkpt works once circuit loaded; set before run
    if use_bkpt:
        for e in edges[1:]:  # skip t=0
            ng.lib.ngSpice_SetBkpt(ctypes.c_double(e))
    t0 = time.perf_counter()
    ng.command("run")
    t_spice = time.perf_counter() - t0
    t0 = time.perf_counter()
    tvec = ng.vec("time") or []
    vgpio = ng.vec("v(gpio)") or []
    vanode = ng.vec("v(anode)") or []
    cur = ng.vec("i(v1)") or []
    # edge timing error: first simulated crossing of 2.5V rising after 2nd edge
    # (edges[0]=t0 HIGH start, edges[1]=fall, edges[2]=rise)
    err = None
    if len(edges) > 2 and vgpio and tvec:
        target = edges[2]
        for tt, vv in zip(tvec, vgpio):
            if tt >= target and vv > 2.5:
                err = tt - target
                break
    # LED ON current accuracy: mean |I| over last period's HIGH half vs DC ref 3.96389mA
    acc = None
    if cur and tvec:
        tail = [(tt, c) for tt, c in zip(tvec, cur) if tt > tsim - period]
        high = [abs(c) for tt, c in tail if gpio_val(tt) > 2.5]
        if high:
            acc = sum(high) / len(high)
    t_analysis = time.perf_counter() - t0
    return {
        "freq": freq_hz, "strategy": strategy, "tsim": tsim, "period": period,
        "tstep": tstep, "n_edges": n_edges,
        "t_gen_ms": t_gen * 1e3, "t_spice_ms": t_spice * 1e3, "t_analysis_ms": t_analysis * 1e3,
        "wall_ms": (t_gen + t_spice + t_analysis) * 1e3,
        "npts": len(tvec), "vsrc_calls": state["calls"],
        "evals_per_step": state["calls"] / max(1, len(tvec)),
        "edge_err_s": err, "edge_err_ns": err * 1e9 if err is not None else None,
        "i_on_mean": acc,
        "sim_speed": tsim / max(1e-9, t_spice),
    }

if __name__ == "__main__":
    results = []
    for freq in [100, 1_000, 10_000, 100_000, 1_000_000]:
        period = 1.0 / freq
        tsim = min(max(4 * period, 200e-6), 20e-3)
        for strat in ["naive", "tmax", "breakpoint"]:
            r = run_case(freq, tsim=tsim, strategy=strat)
            results.append(r)
            print(f"f={freq:>7.0f}Hz tsim={tsim*1e3:6.2f}ms strat={strat:10s} wall={r['wall_ms']:8.2f}ms "
                  f"npts={r['npts']:5d} evals={r['vsrc_calls']:6d} ({r['evals_per_step']:.2f}/step) "
                  f"edge_err={r['edge_err_ns'] if r['edge_err_ns'] is not None else float('nan'):8.1f}ns "
                  f"Ion={r['i_on_mean']*1e3 if r['i_on_mean'] else float('nan'):6.3f}mA speed={r['sim_speed']:.1f}x")
    os.makedirs("results", exist_ok=True)
    with open("results/e03.json", "w") as f:
        json.dump(results, f, indent=1)
    print("saved results/e03.json")
