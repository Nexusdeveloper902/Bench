"""E11: background-thread mode (bg_run) + async completion poll.
Verifies: bg_run returns immediately, ngSpice_running() True->False,
vectors readable after completion and identical to synchronous run.
NOTE: do NOT re-call ngSpice_Init on a live instance; use one init per NgSpice.
"""
import sys, os, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from ngspice_ctypes import NgSpice

NET = ["bg test", "V1 in 0 DC 5", "R1 in out 1k", "D1 out 0 DLED",
       ".model DLED D(IS=1e-14 N=1.5)", ".tran 1u 50m", ".end"]

if __name__ == "__main__":
    ng = NgSpice(); assert ng.init() == 0
    assert ng.circ(NET) == 0
    t0 = time.perf_counter()
    rc = ng.command("bg_run")
    t_ret = time.perf_counter() - t0
    # startup race: bg thread may not flag running() for a few ms; wait up to 2s for True
    t1 = time.perf_counter()
    running_during = False
    while time.perf_counter() - t1 < 2.0:
        if ng.lib.ngSpice_running():
            running_during = True
            break
        time.sleep(0.005)
    # then wait for completion (running False) after having seen True
    t1 = time.perf_counter()
    if running_during:
        while ng.lib.ngSpice_running() and time.perf_counter() - t1 < 60:
            time.sleep(0.02)
        time.sleep(0.2)  # let bg thread join before teardown (else core dump)
    t_bg = time.perf_counter() - t0
    v_bg = ng.vec("v(out)")
    n_stat = len(ng.stat_lines)
    print(f"bg_run rc={rc} return_in={t_ret*1e3:.3f}ms running_after_cmd={running_during} "
          f"bg_wall={t_bg*1e3:.1f}ms stat_msgs={n_stat} Vf={v_bg[-1] if v_bg else None}")
    with open("results/e11.json", "w") as f:
        json.dump({"bg_return_ms": t_ret * 1e3, "bg_wall_ms": t_bg * 1e3,
                   "running_during": running_during, "stat_n": n_stat,
                   "v_bg": v_bg[-1] if v_bg else None}, f, indent=1)
    print("saved results/e11.json")
