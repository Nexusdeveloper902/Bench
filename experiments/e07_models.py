"""E07: electrical modeling paths + edit/rebuild/simulate loop.
P1 direct primitives (.model D): param edit -> circ-reload -> run; convergence + latency.
P2 behavioral B-source diode: accuracy vs D model, convergence, latency.
P3 subcircuit LED: reload latency.
P4 Verilog-A/OpenVAF/OSDI: availability probe (openvaf binary? osdi load path?).
E-loop: alter vs circ-reload x10 timing.
"""
import sys, os, json, time, shutil, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from ngspice_ctypes import NgSpice

def run_led(model_line, extra=None, tstep="1u", tstop="1m"):
    ng = NgSpice(); assert ng.init() == 0
    net = ["led", "V1 in 0 DC 5", "R1 in out 1k", "D1 out 0 DLED", model_line,
           f".tran {tstep} {tstop}", ".end"]
    if extra:
        net[3:3] = extra
    t0 = time.perf_counter()
    rc1 = ng.circ(net)
    t_circ = time.perf_counter() - t0
    t0 = time.perf_counter()
    rc2 = ng.command("run")
    t_run = time.perf_counter() - t0
    v = ng.vec("v(out)"); cur = ng.vec("i(v1)")
    errs = [l for _, _, l in ng.log_lines if "error" in l.lower() or "singular" in l.lower()
            or "failed" in l.lower() or "no convergence" in l.lower()][:3]
    return {"rc": (rc1, rc2), "t_circ_ms": t_circ * 1e3, "t_run_ms": t_run * 1e3,
            "Vf": v[-1] if v else None, "I": cur[-1] if cur else None, "errs": errs}

if __name__ == "__main__":
    out = {}
    # P1: direct primitive, sweep N
    p1 = {}
    for N in ["1.0", "1.5", "2.0"]:
        r = run_led(f".model DLED D(IS=1e-14 N={N} BV=5 IBV=1u)")
        p1[N] = r
        print(f"P1 N={N}: Vf={r['Vf']:.6f} I={abs(r['I'])*1e3:.4f}mA circ={r['t_circ_ms']:.2f}ms run={r['t_run_ms']:.2f}ms errs={r['errs']}")
    out["P1_direct"] = p1
    # P2: behavioral diode (B current source // fixed cap): I=Is*(exp(V/Vt)-1), Vt=N*25.85mV
    ng = NgSpice(); assert ng.init() == 0
    net = ["b-led", "V1 in 0 DC 5", "R1 in out 1k",
           "B1 out 0 I=1e-14*(exp(V(out)/0.038775)-1)",
           "C1 out 0 1p",  # help convergence (charge storage proxy)
           ".tran 1u 1m", ".end"]
    t0 = time.perf_counter(); ng.circ(net); t_c = time.perf_counter() - t0
    t0 = time.perf_counter(); ng.command("run"); t_r = time.perf_counter() - t0
    v = ng.vec("v(out)"); cur = ng.vec("i(v1)")
    errs = [l for _, _, l in ng.log_lines if "error" in l.lower() or "singular" in l.lower() or "failed" in l.lower()][:3]
    out["P2_behavioral"] = {"t_circ_ms": t_c * 1e3, "t_run_ms": t_r * 1e3,
                            "Vf": v[-1] if v else None, "I": cur[-1] if cur else None, "errs": errs}
    print(f"P2 behavioral: Vf={v[-1]:.6f} I={abs(cur[-1])*1e3:.4f}mA circ={t_c*1e3:.2f}ms run={t_r*1e3:.2f}ms errs={errs}")
    # P3: subcircuit LED (R + D + C)
    r3 = run_led(".model DLED D(IS=1e-14 N=1.5 BV=5 IBV=1u)",
                 extra=[".subckt LEDm a k", "D1 a k DLED", "C1 a k 10p", ".ends LEDm"])
    # NOTE: extra lines placed inside net but XLED not instantiated; this run just checks subckt parse+reload cost
    out["P3_subckt_parse"] = {"t_circ_ms": r3["t_circ_ms"], "t_run_ms": r3["t_run_ms"]}
    print(f"P3 subckt parse: circ={r3['t_circ_ms']:.2f}ms run={r3['t_run_ms']:.2f}ms")
    # true subcircuit instantiation
    ng3 = NgSpice(); assert ng3.init() == 0
    net3 = ["ledx", "V1 in 0 DC 5", "R1 in out 1k", "X1 out 0 LEDm",
            ".subckt LEDm a k", "D1 a k DLED", "C1 a k 10p", ".ends LEDm",
            ".model DLED D(IS=1e-14 N=1.5 BV=5 IBV=1u)", ".tran 1u 1m", ".end"]
    t0 = time.perf_counter(); ng3.circ(net3); t_c3 = time.perf_counter() - t0
    t0 = time.perf_counter(); ng3.command("run"); t_r3 = time.perf_counter() - t0
    v3 = ng3.vec("v(out)")
    out["P3_subckt"] = {"t_circ_ms": t_c3 * 1e3, "t_run_ms": t_r3 * 1e3, "Vf": v3[-1] if v3 else None}
    print(f"P3 subckt X: Vf={v3[-1]:.6f} circ={t_c3*1e3:.2f}ms run={t_r3*1e3:.2f}ms")
    # E-loop: alter vs circ-reload x10 (vary R1)
    ng4 = NgSpice(); assert ng4.init() == 0
    base = ["led", "V1 in 0 DC 5", "R1 in out 1k", "D1 out 0 DLED",
            ".model DLED D(IS=1e-14 N=1.5)", ".tran 1u 1m", ".end"]
    ng4.circ(base); ng4.command("run")
    t0 = time.perf_counter()
    for i in range(10):
        ng4.command(f"alter R1 {800 + i * 50}")
        ng4.command("run")
    t_alter = (time.perf_counter() - t0) / 10 * 1e3
    t0 = time.perf_counter()
    for i in range(10):
        ng5 = NgSpice(); ng5.init()
        b2 = [l.replace("R1 in out 1k", f"R1 in out {800 + i * 50}") for l in base]
        ng5.circ(b2); ng5.command("run")
    t_reload = (time.perf_counter() - t0) / 10 * 1e3
    out["Eloop"] = {"alter_ms_per_iter": t_alter, "circ_reload_ms_per_iter": t_reload}
    print(f"Eloop: alter+run={t_alter:.2f}ms/iter circ-reload+run={t_reload:.2f}ms/iter")
    # P4: OpenVAF/OSDI availability
    p4 = {"openvaf_on_path": shutil.which("openvaf") is not None,
          "osdi_files_shipped": os.listdir("/usr/lib/ngspice") if os.path.isdir("/usr/lib/ngspice") else []}
    ng6 = NgSpice(); assert ng6.init() == 0
    ng6.circ(["t", "V1 a 0 DC 1", "R1 a 0 1k", ".op", ".end"])
    ng6.command("osdi /nonexistent.osdi")
    ng6.command("run")
    p4["osdi_missing_file_log"] = [l for _, _, l in ng6.log_lines if "osdi" in l.lower()][-3:]
    # try quick openvaf download probe (bounded 60s, best-effort, no failure if offline)
    try:
        p = subprocess.run(["curl", "-sI", "--max-time", "10",
                            "https://github.com/OpenVAF/OpenVAF/releases"],
                           capture_output=True, text=True, timeout=20)
        p4["github_reachable"] = (p.returncode == 0 and "200" in p.stdout)
    except Exception as e:
        p4["github_reachable"] = f"probe failed: {e}"
    out["P4_osdi"] = p4
    print(f"P4: openvaf={p4['openvaf_on_path']} osdi_log={p4['osdi_missing_file_log']} github={p4.get('github_reachable')}")
    os.makedirs("results", exist_ok=True)
    with open("results/e07.json", "w") as f:
        json.dump(out, f, indent=1, default=str)
    print("saved results/e07.json")
