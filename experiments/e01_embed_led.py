"""E01: embed libngspice directly. R + LED(diode) + GND, DC + tran.
Measures LED current, node voltages, wall-clock, solve counts.
"""
import time, sys, json, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from ngspice_ctypes import NgSpice

NET = [
    "LED test - R + D + GND",
    "V1 in 0 DC 5",
    "R1 in out 1k",
    "D1 out 0 DLED",
    ".model DLED D(IS=1e-14 N=1.5 BV=5 IBV=1u)",
    ".tran 1u 1m",
    ".end",
]

def run_once():
    ng = NgSpice()
    assert ng.init() == 0
    assert ng.circ(NET) == 0
    t0 = time.perf_counter()
    ng.command("op")
    t_op = time.perf_counter() - t0
    v_out_op = ng.vec("v(out)")
    v_in_op = ng.vec("v(in)")
    i_v1_op = ng.vec("i(v1)")
    t0 = time.perf_counter()
    ng.command("run")
    t_tran = time.perf_counter() - t0
    v_out = ng.vec("v(out)")
    v_in = ng.vec("v(in)")
    tvec = ng.vec("time")
    cur = ng.vec("tran1.i(v1)") or ng.vec("i(v1)")
    return {
        "t_op": t_op, "t_tran": t_tran,
        "v_out_op": v_out_op, "v_in_op": v_in_op, "i_v1_op": i_v1_op,
        "v_out_last": v_out[-1] if v_out else None,
        "v_out_len": len(v_out) if v_out else 0,
        "time_last": tvec[-1] if tvec else None,
        "time_len": len(tvec) if tvec else 0,
        "i_last": cur[-1] if cur else None,
        "senddata_n": ng.senddata_n,
        "n_stat": len(ng.stat_lines),
        "errors": [l for _, _, l in ng.log_lines if "error" in l.lower() or "fail" in l.lower()][:5],
    }

if __name__ == "__main__":
    # warmup + 5 timed runs
    run_once()
    runs = [run_once() for _ in range(5)]
    for i, r in enumerate(runs):
        print(f"run{i}: t_op={r['t_op']*1e3:.3f}ms t_tran={r['t_tran']*1e3:.3f}ms "
              f"Vout_op={r['v_out_op']} Vin_op={r['v_in_op']} I_op={r['i_v1_op']} "
              f"Vout_last={r['v_out_last']:.6f} I_last={r['i_last']:.6e} npts={r['time_len']} senddata={r['senddata_n']}")
    # analytical check: LED Vf ~0.7-1.0V for D(IS=1e-14,N=1.5): I=(5-Vf)/1k; solve approx
    # just report measured Vf and I consistency: I*R + Vf should equal 5
    r = runs[0]
    vf = r["v_out_last"]; i = abs(r["i_last"])
    print(f"consistency: Vf={vf:.6f} I={i:.6e} I*R+Vf={i*1000+vf:.6f} (expect 5.0)")
    os.makedirs("results", exist_ok=True)
    with open("results/e01.json", "w") as f:
        json.dump(runs, f, indent=1, default=str)
    print("saved results/e01.json")
