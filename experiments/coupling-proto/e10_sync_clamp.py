"""E10: adaptive synchronization via GetSyncData delta clamp (MCU-driven bound).
Same 10kHz GPIO->LED case as E03. Compares:
  A) coarse tstep, no clamp (miss expected)
  B) coarse tstep + sync callback clamps delta <= period/100 (adaptive bound)
  C) breakpoint reference
Also records Python-callback overhead: extra wall vs E03-tmax equivalent.
"""
import sys, os, json, time, ctypes, bisect
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from ngspice_ctypes import NgSpice, GetVSRCData, GetISRCData, GetSyncData

FREQ, TSIM = 10_000, 4e-4
PERIOD = 1.0 / FREQ
EDGES = [i * PERIOD / 2 for i in range(int(TSIM / (PERIOD / 2)) + 1)]

def drive(tt):
    return 5.0 if (bisect.bisect_right(EDGES, tt) % 2 == 1) else 0.0

def run(mode):
    state = {"vsrc": 0, "sync": 0, "clamps": 0}
    bound = PERIOD / 100
    ng = NgSpice(); assert ng.init() == 0
    def _vsrc(pv, tt, name, ident, ud):
        state["vsrc"] += 1; pv[0] = drive(tt); return 0
    def _isrc(pc, tt, name, ident, ud):
        pc[0] = 0.0; return 0
    def _sync(tt, pdelta, old, redo, ident, loc, ud):
        state["sync"] += 1
        if mode == "clamp" and pdelta[0] > bound:
            pdelta[0] = bound; state["clamps"] += 1
        return 0
    cbs = [GetVSRCData(_vsrc), GetISRCData(_isrc), GetSyncData(_sync)]
    ng._cbs.extend(cbs)
    ident = ctypes.c_int(0)
    assert ng.lib.ngSpice_Init_Sync(cbs[0], cbs[1], cbs[2], ctypes.byref(ident), None) == 0
    tstep = TSIM / 50.0
    assert ng.circ(["gpio led", "V1 gpio 0 EXTERNAL", "R1 gpio anode 1k", "D1 anode 0 DLED",
                    ".model DLED D(IS=1e-14 N=1.5 BV=5 IBV=1u)",
                    f".tran {tstep} {TSIM}", ".end"]) == 0
    if mode == "breakpoint":
        for e in EDGES[1:]:
            ng.lib.ngSpice_SetBkpt(ctypes.c_double(e))
    t0 = time.perf_counter(); ng.command("run"); wall = time.perf_counter() - t0
    tvec, vgpio = ng.vec("time"), ng.vec("v(gpio)")
    target = EDGES[2]
    err = next((tt - target for tt, vv in zip(tvec, vgpio) if tt >= target and vv > 2.5), None)
    # max accepted step (approx from time vector diffs)
    diffs = [b - a for a, b in zip(tvec, tvec[1:])] if len(tvec) > 1 else [0]
    return {"mode": mode, "wall_ms": wall * 1e3, "npts": len(tvec),
            "vsrc": state["vsrc"], "sync": state["sync"], "clamps": state["clamps"],
            "edge_err_ns": err * 1e9 if err is not None else None,
            "max_step_us": max(diffs) * 1e6 if diffs else None}

if __name__ == "__main__":
    rows = [run(m) for m in ["none", "clamp", "breakpoint"]]
    for r in rows:
        print(f"mode={r['mode']:10s} wall={r['wall_ms']:7.2f}ms npts={r['npts']:5d} vsrc={r['vsrc']:6d} "
              f"sync={r['sync']:5d} clamps={r['clamps']:5d} edge_err={r['edge_err_ns']:.1f}ns max_step={r['max_step_us']:.2f}us")
    with open("results/e10.json", "w") as f:
        json.dump(rows, f, indent=1)
    print("saved results/e10.json")
