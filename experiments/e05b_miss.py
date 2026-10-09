"""E05b: honest miss-probe with EXTERNAL-driven narrow blip (no breakpoints).
2us-wide 5V blip at t~700us on top of slow ramp; coarse tmax=50us should MISS it,
fine tmax catches it. Proves polling/bounding requirement for unknown analog events.
"""
import sys, os, json, time, ctypes
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from ngspice_ctypes import NgSpice, GetVSRCData, GetISRCData, GetSyncData

def blip(t):
    # slow ramp 0->2V over 2ms (never crosses 2.5V alone) + 2us 5V blip at 700us
    base = 2.0 * t / 2e-3
    if 699e-6 <= t <= 701e-6:
        return 5.0
    return base

def run(tstep):
    state = {"calls": 0}
    ng = NgSpice(); assert ng.init() == 0
    def _vsrc(p, tt, name, ident, ud):
        state["calls"] += 1; p[0] = blip(tt); return 0
    def _isrc(p, tt, name, ident, ud):
        p[0] = 0.0; return 0
    def _sync(tt, pd, old, redo, ident, loc, ud):
        return 0
    cbs = [GetVSRCData(_vsrc), GetISRCData(_isrc), GetSyncData(_sync)]
    ng._cbs.extend(cbs)
    ident = ctypes.c_int(0)
    assert ng.lib.ngSpice_Init_Sync(cbs[0], cbs[1], cbs[2], ctypes.byref(ident), None) == 0
    net = ["ext blip miss test", "V1 drv 0 EXTERNAL", "R1 drv cap 1k", "C1 cap 0 1n",
           "B1 cmp 0 V=V(cap)>2.5?5:0", ".ic v(cap)=0", f".tran {tstep} 2m uic", ".end"]
    assert ng.circ(net) == 0
    t0 = time.perf_counter(); ng.command("run"); wall = time.perf_counter() - t0
    vcmp = ng.vec("v(cmp)") or [0.0]
    vcap = ng.vec("v(cap)") or [0.0]
    return {"tstep": tstep, "npts": len(vcmp), "wall_ms": wall * 1e3,
            "max_cmp": max(vcmp), "max_cap": max(vcap), "caught": max(vcmp) > 2.5}

if __name__ == "__main__":
    rows = [run(ts) for ts in [50e-6, 5e-6, 0.5e-6]]
    for r in rows:
        print(f"tstep={r['tstep']*1e6:5.1f}us npts={r['npts']:5d} wall={r['wall_ms']:6.2f}ms "
              f"maxVcap={r['max_cap']:.3f} maxVcmp={r['max_cmp']:.2f} caught={r['caught']}")
    os.makedirs("results", exist_ok=True)
    with open("results/e05b.json", "w") as f:
        json.dump(rows, f, indent=1)
    print("saved results/e05b.json")
