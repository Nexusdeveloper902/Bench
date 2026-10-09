"""E04: ADC-style sync where MCU execution depends on analog value.
Circuit: 5V step -> R(10k) -> C(100n) -> GND (tau=1ms), Vcap read by ADC.
Ground truth: single fine ngspice run. MCU: polls Vcap every T_poll, detects 2.5V crossing.
Quantifies detection delay vs number of ADC reads (bounded polling cost).
Also validates chunked continuation (.ic + uic) against single-run ground truth.
"""
import sys, os, json, time, ctypes
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from ngspice_ctypes import NgSpice

R, C = 10e3, 100e-9
VDRV, VTH = 5.0, 2.5
TSIM = 2e-3

def ground_truth(tstep=1e-6):
    ng = NgSpice()
    assert ng.init() == 0
    net = ["rc charge", f"V1 drv 0 DC {VDRV}", f"R1 drv cap {R}", f"C1 cap 0 {C}",
           ".ic v(cap)=0", f".tran {tstep} {TSIM} uic", ".end"]
    assert ng.circ(net) == 0
    t0 = time.perf_counter(); ng.command("run"); wall = time.perf_counter() - t0
    tvec, vcap = ng.vec("time"), ng.vec("v(cap)")
    return tvec, vcap, wall

def crossing(tvec, vcap, vth=VTH):
    for tt, vv in zip(tvec, vcap):
        if vv >= vth:
            return tt
    return None

def poll_experiment(tvec, vcap, t_poll):
    import bisect
    n_reads = 0
    t = 0.0
    detected = None
    while t <= tvec[-1]:
        n_reads += 1
        # sample by interpolation
        i = bisect.bisect_right(tvec, t)
        v = vcap[min(i, len(vcap) - 1)]
        if v >= VTH:
            detected = t
            break
        t += t_poll
    return detected, n_reads

def chunked_continuation(nseg=4):
    """Run TSIM as nseg chained segments using .ic v(cap)=X + uic; compare to ground truth."""
    import math
    seg = TSIM / nseg
    ng = NgSpice()
    assert ng.init() == 0
    t_all, v_all = [], []
    v0 = 0.0
    wall = 0.0
    for k in range(nseg):
        t0 = time.perf_counter()
        net = ["rc charge", f"V1 drv 0 DC {VDRV}", f"R1 drv cap {R}", f"C1 cap 0 {C}",
               f".ic v(cap)={v0}", f".tran 1u {seg} uic", ".end"]
        assert ng.circ(net) == 0
        ng.command("run")
        wall += time.perf_counter() - t0
        tvec, vcap = ng.vec("time"), ng.vec("v(cap)")
        off = k * seg
        t_all += [tt + off for tt in tvec]
        v_all += list(vcap)
        v0 = vcap[-1]
    return t_all, v_all, wall

if __name__ == "__main__":
    tvec, vcap, wall_gt = ground_truth()
    t_true = crossing(tvec, vcap)
    import math
    t_analytic = -R * C * math.log(1 - VTH / VDRV)
    print(f"ground truth: npts={len(tvec)} wall={wall_gt*1e3:.2f}ms t_cross={t_true*1e6:.2f}us analytic={t_analytic*1e6:.2f}us err={(t_true-t_analytic)*1e9:.1f}ns")
    rows = []
    for tp in [5e-6, 10e-6, 25e-6, 50e-6, 100e-6, 200e-6]:
        det, n = poll_experiment(tvec, vcap, tp)
        delay = (det - t_true) * 1e6 if det else None
        rows.append({"t_poll_us": tp * 1e6, "n_reads": n, "detect_delay_us": delay})
        print(f"T_poll={tp*1e6:6.1f}us reads={n:4d} detect_delay={delay:.1f}us")
    # chunked validation
    tc, vc, wall_c = chunked_continuation(4)
    # compare final voltage + crossing
    print(f"chunked x4: npts={len(tc)} wall={wall_c*1e3:.2f}ms Vfinal={vc[-1]:.6f} gt={vcap[-1]:.6f} diff={abs(vc[-1]-vcap[-1])*1e3:.4f}mV t_cross_chunk={crossing(tc,vc)*1e6:.2f}us")
    os.makedirs("results", exist_ok=True)
    with open("results/e04.json", "w") as f:
        json.dump({"t_true_us": t_true * 1e6, "t_analytic_us": t_analytic * 1e6,
                   "wall_gt_ms": wall_gt * 1e3, "polls": rows,
                   "chunked_Vfinal": vc[-1], "gt_Vfinal": vcap[-1]}, f, indent=1)
    print("saved results/e04.json")
