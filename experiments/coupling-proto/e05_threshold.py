"""E05: threshold/comparator event without rollback.
RC charge + B-source comparator (5V if Vcap>2.5 else 0).
Q: can threshold-crossing detection work without rollback? What step strategy suffices?
Tests coarse/medium/fine tmax + bisection refinement (coarse locate, fine re-run of window).
"""
import sys, os, json, time, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from ngspice_ctypes import NgSpice

R, C, VDRV, VTH, TSIM = 10e3, 100e-9, 5.0, 2.5, 2e-3
T_TRUE = -R * C * math.log(1 - VTH / VDRV)

def run(tstep):
    ng = NgSpice()
    assert ng.init() == 0
    net = ["rc + comparator",
           f"V1 drv 0 DC {VDRV}", f"R1 drv cap {R}", f"C1 cap 0 {C}",
           "B1 cmp 0 V=V(cap)>2.5?5:0",
           ".ic v(cap)=0", f".tran {tstep} {TSIM} uic", ".end"]
    assert ng.circ(net) == 0
    t0 = time.perf_counter(); ng.command("run"); wall = time.perf_counter() - t0
    tvec, vcmp = ng.vec("time"), ng.vec("v(cmp)")
    det = next((tt for tt, vv in zip(tvec, vcmp) if vv > 2.5), None)
    return {"tstep": tstep, "npts": len(tvec), "wall_ms": wall * 1e3,
            "detected_us": det * 1e6 if det else None,
            "err_us": (det - T_TRUE) * 1e6 if det else None}

if __name__ == "__main__":
    rows = [run(ts) for ts in [50e-6, 10e-6, 2e-6, 0.5e-6]]
    for r in rows:
        print(f"tstep={r['tstep']*1e6:6.1f}us npts={r['npts']:5d} wall={r['wall_ms']:6.2f}ms "
              f"detect={r['detected_us']:.2f}us err={r['err_us']:.2f}us")
    # bisection refinement: coarse locate then fine re-run (full-span fine tmax, cost comparison)
    coarse = rows[0]
    # refinement run: tstep = coarse_err/100 over full span (no rollback, just second run)
    ref = run(0.5e-6)
    print(f"true={T_TRUE*1e6:.2f}us coarse_err={coarse['err_us']:.2f}us refined_err={ref['err_us']:.3f}us "
          f"cost_coarse+refined={coarse['wall_ms']+ref['wall_ms']:.2f}ms vs all-fine={ref['wall_ms']:.2f}ms")
    # missed-event probe: narrow pulse (comparator on 2us-wide over-threshold blip) with coarse steps
    ng = NgSpice(); assert ng.init() == 0
    net = ["narrow pulse miss test",
           "V1 drv 0 PWL(0 0 690u 0 691u 5 693u 5 694u 0 2m 0)",
           f"R1 drv cap 1k", f"C1 cap 0 1n",
           "B1 cmp 0 V=V(cap)>2.5?5:0",
           ".ic v(cap)=0", ".tran 50u 2m uic", ".end"]
    assert ng.circ(net) == 0
    ng.command("run")
    vcmp = ng.vec("v(cmp)") or [0]
    print(f"miss-probe coarse50us: max(Vcmp)={max(vcmp):.2f} (0.00 => MISSED narrow event, >2.5 => caught)")
    ng2 = NgSpice(); assert ng2.init() == 0
    net2 = [l.replace(".tran 50u 2m uic", ".tran 0.2u 2m uic") for l in net]
    assert ng2.circ(net2) == 0
    ng2.command("run")
    vcmp2 = ng2.vec("v(cmp)") or [0]
    print(f"miss-probe fine0.2us:  max(Vcmp)={max(vcmp2):.2f}")
    os.makedirs("results", exist_ok=True)
    with open("results/e05.json", "w") as f:
        json.dump({"t_true_us": T_TRUE * 1e6, "runs": rows,
                   "miss_coarse": max(vcmp), "miss_fine": max(vcmp2)}, f, indent=1)
    print("saved results/e05.json")
