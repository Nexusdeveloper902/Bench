"""E06: libngspice multi-instance behavior (same process).
T1: sequential independent instances, alternating circuits, check isolation.
T2: concurrent threads, SAME .so (documents global-state hazard; guarded by timeout).
T3: concurrent threads, RENAMED .so copies (per-manual recipe), separate circuits.
"""
import sys, os, json, time, ctypes, shutil, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from ngspice_ctypes import NgSpice

def dc_value(nglib_circuit_lines, expect_node="v(out)"):
    ng = NgSpice()
    assert ng.init() == 0
    assert ng.circ(nglib_circuit_lines) == 0
    ng.command("op")
    v = ng.vec(expect_node)
    return v[0] if v else None

CIR_A = ["div A", "V1 in 0 DC 1", "R1 in out 1k", "R2 out 0 1k", ".end"]       # Vout=0.5
CIR_B = ["div B", "V1 in 0 DC 2", "R1 in out 1k", "R2 out 0 3k", ".end"]       # Vout=1.5

if __name__ == "__main__":
    out = {}
    # T1 sequential isolation x6 alternating
    t0 = time.perf_counter()
    seq = [dc_value(CIR_A if i % 2 == 0 else CIR_B) for i in range(6)]
    out["T1_seq"] = {"vals": seq, "wall_ms": (time.perf_counter() - t0) * 1e3,
                     "ok": all(abs(v - (0.5 if i % 2 == 0 else 1.5)) < 1e-9 for i, v in enumerate(seq))}
    print(f"T1 sequential alternating: {seq} ok={out['T1_seq']['ok']}")

    # T3 renamed copies concurrent (do before risky T2 so results survive a crash)
    for tag in ["A", "B"]:
        shutil.copy("/usr/lib/libngspice.so", f"/tmp/opencode/libngspice_{tag}.so")
    res = {}
    def worker(tag, cir, expect):
        try:
            ng = NgSpice(libpath=f"/tmp/opencode/libngspice_{tag}.so")
            assert ng.init() == 0
            assert ng.circ(cir) == 0
            # staggered tran runs overlap in time
            ng.command("tran 1u 2m")
            v = ng.vec("v(out)")
            res[tag] = {"last": v[-1] if v else None, "npts": len(v) if v else 0, "expect": expect}
        except Exception as e:
            res[tag] = {"error": str(e)}
    th = [threading.Thread(target=worker, args=("A", ["cA", "V1 in 0 DC 1", "R1 in out 1k", "R2 out 0 1k", ".tran 1u 2m", ".end"], 0.5)),
          threading.Thread(target=worker, args=("B", ["cB", "V1 in 0 DC 2", "R1 in out 1k", "R2 out 0 3k", ".tran 1u 2m", ".end"], 1.5))]
    t0 = time.perf_counter()
    [t.start() for t in th]; [t.join(60) for t in th]
    out["T3_renamed_concurrent"] = {"res": res, "wall_ms": (time.perf_counter() - t0) * 1e3,
                                    "alive": all(t.is_alive() is False for t in th)}
    print(f"T3 renamed concurrent: {res}")

    # T2 same-.so concurrent: run in SUBPROCESS to contain any crash, with timeout
    import subprocess
    try:
        p = subprocess.run(["timeout", "60", "python3", "scripts/t2_probe.py"], capture_output=True, text=True, timeout=70)
        out["T2_same_concurrent"] = {"rc": p.returncode, "stdout": p.stdout[-500:], "stderr": p.stderr[-500:]}
    except Exception as e:
        out["T2_same_concurrent"] = {"error": str(e)}
    print(f"T2 same-lib concurrent: rc={out['T2_same_concurrent'].get('rc')} out={out['T2_same_concurrent'].get('stdout', '')[-200:]}")
    os.makedirs("results", exist_ok=True)
    with open("results/e06.json", "w") as f:
        json.dump(out, f, indent=1, default=str)
    print("saved results/e06.json")
