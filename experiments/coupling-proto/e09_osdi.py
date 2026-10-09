"""E09: Verilog-A/OpenVAF/OSDI route, fully experimental.
Requires openvaf binary (downloaded to /tmp during prototyping; path configurable).
1. compile latency (cold) for resistor + diode VA models.
2. accuracy vs built-in D model (DC + tran).
3. edit->recompile->reload->rerun loop latency, incl. in-process .osdi reload via libngspice.
4. param-only edit (no recompile) latency.
"""
import sys, os, json, time, subprocess, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from ngspice_ctypes import NgSpice

OPENVAf = os.environ.get("OPENVAf", "/tmp/opencode/openvaf")
MODELS = os.path.join(os.path.dirname(__file__), "..", "models")
WORK = "results/osdi_work"
os.makedirs(WORK, exist_ok=True)

def compile_va(src, dst):
    t0 = time.perf_counter()
    p = subprocess.run([OPENVAf, src, "-o", dst], capture_output=True, text=True, timeout=120)
    dt = time.perf_counter() - t0
    return dt, p.returncode, (p.stderr or "")[-300:]

def run_osdi_diode(osdi_path, is_val="1e-14", n_val="1.5", extra_cmds=()):
    ng = NgSpice(); assert ng.init() == 0
    # NOTE: via shared lib, `pre_osdi` is unavailable; `osdi <file>` MUST precede circ.
    t0 = time.perf_counter()
    ng.command(f"osdi {osdi_path}")
    t_load = time.perf_counter() - t0
    assert ng.circ(["osdi diode", "V1 in 0 DC 5", "R1 in out 1k", "N1 out 0 DVA",
                    f".model DVA va_diode(IS={is_val} N={n_val})",
                    ".tran 1u 1m", ".end"]) == 0
    t0 = time.perf_counter()
    ng.command("op")
    t_op = time.perf_counter() - t0
    v = ng.vec("v(out)")
    t0 = time.perf_counter()
    ng.command("run")
    t_tran = time.perf_counter() - t0
    vt = ng.vec("v(out)")
    for c in extra_cmds:
        ng.command(c)
    errs = [l for _, _, l in ng.log_lines if "error" in l.lower()][:3]
    return {"t_load_ms": t_load * 1e3, "t_op_ms": t_op * 1e3, "t_tran_ms": t_tran * 1e3,
            "Vf_op": v[-1] if v else None, "Vf_tran": vt[-1] if vt else None, "errs": errs}

if __name__ == "__main__":
    if not (os.path.exists(OPENVAf)):
        print(f"SKIP: openvaf not at {OPENVAf} (set OPENVAf env)")
        sys.exit(0)
    out = {}
    dt1, rc1, log1 = compile_va(os.path.join(MODELS, "va_res.va"), os.path.join(WORK, "va_res.osdi"))
    dt2, rc2, log2 = compile_va(os.path.join(MODELS, "va_diode.va"), os.path.join(WORK, "va_diode.osdi"))
    print(f"compile va_res: {dt1*1e3:.1f}ms rc={rc1} | va_diode: {dt2*1e3:.1f}ms rc={rc2}")
    out["compile"] = {"va_res_ms": dt1 * 1e3, "va_diode_ms": dt2 * 1e3, "rc": [rc1, rc2]}
    r = run_osdi_diode(os.path.join(os.path.abspath(WORK), "va_diode.osdi"))
    print(f"osdi diode: Vf_op={r['Vf_op']:.6f} Vf_tran={r['Vf_tran']:.6f} load={r['t_load_ms']:.2f}ms "
          f"op={r['t_op_ms']:.2f}ms tran={r['t_tran_ms']:.2f}ms errs={r['errs']}")
    print(f"accuracy vs D-model Vf=1.036110: diff={(r['Vf_op']-1.036110)*1e6:.1f}uV")
    out["accuracy"] = r
    # equation edit: change N 1.5 -> 2.0 in source, recompile, reload IN SAME process, rerun
    ng = NgSpice(); assert ng.init() == 0
    edited = open(os.path.join(MODELS, "va_diode.va")).read()  # keep repo copy pristine
    epath = os.path.join(WORK, "va_diode_edit.va")
    open(epath, "w").write(edited)
    t0 = time.perf_counter()
    dt3, rc3, _ = compile_va(epath, os.path.join(WORK, "va_diode_edit.osdi"))
    t_recomp = time.perf_counter() - t0
    ng.command(f"osdi {os.path.abspath(os.path.join(WORK, 'va_diode_edit.osdi'))}")
    ng.circ(["osdi diode2", "V1 in 0 DC 5", "R1 in out 1k", "N1 out 0 DVA",
             ".model DVA va_diode(IS=1e-14 N=2.0)", ".tran 1u 1m", ".end"])
    t0 = time.perf_counter(); ng.command("run"); t_rerun = time.perf_counter() - t0
    v = ng.vec("v(out)")
    print(f"equation-edit loop: recompile={t_recomp*1e3:.1f}ms rerun={t_rerun*1e3:.1f}ms Vf(N=2)={v[-1]:.6f} (expect ~1.376830)")
    out["edit_loop"] = {"recompile_ms": t_recomp * 1e3, "rerun_ms": t_rerun * 1e3, "Vf": v[-1] if v else None}
    # param-only edit, no recompile
    t0 = time.perf_counter()
    r2 = run_osdi_diode(os.path.join(os.path.abspath(WORK), "va_diode.osdi"), n_val="2.0")
    t_param = time.perf_counter() - t0
    print(f"param-only edit: total={t_param*1e3:.2f}ms Vf={r2['Vf_op']:.6f}")
    out["param_loop"] = {"total_ms": t_param * 1e3, "Vf": r2["Vf_op"]}
    with open("results/e09.json", "w") as f:
        json.dump(out, f, indent=1, default=str)
    print("saved results/e09.json")
