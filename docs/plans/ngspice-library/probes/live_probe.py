"""Task 9.5a probes (not app code): mid-transient value changes on libngspice, driven like benchcore/src/ngspice.rs.
usage: python3 live_probe.py <probe>   (one probe per process: some leave the library unusable)
Results are summarised in ../LIVE.md."""
import math, sys, time

from ng_probe import C, LOG, lib, VI  # noqa: F401  (ng_probe initialises the library on import)

lib.ngSpice_running.restype = C.c_bool

LDR = ["live ldr", "V1 vcc 0 DC 5", "XRL1 vcc out bench_ldr R10=10000 GAMMA=0.7 LUX=10", "R1 out 0 10k",
       ".subckt bench_ldr a b params: R10=10k GAMMA=0.7 LUX=10", "VSTIM stim 0 DC {LUX}", "RSTIM stim 0 1G",
       "B1 a b I={V(a,b)/(R10*pow(V(stim)/10,-GAMMA))}", ".ends bench_ldr", ".tran 10u 2m 0 10u uic", ".end"]
RC = ["live rc", "V1 in 0 DC 5", "R1 in out 1k", "C1 out 0 1u IC=0", ".tran 10u 3m 0 10u uic", ".end"]


def circ(lines):
    arr = (C.c_char_p * (len(lines) + 1))(*[l.encode() for l in lines], None)
    return lib.ngSpice_Circ(arr)


def cmd(c):
    LOG.clear()
    rc = lib.ngSpice_Command(c.encode())
    return rc, [l for l in LOG if l.startswith("stderr") and l.strip() != "stderr"]


def vec(name):
    p = lib.ngGet_Vec_Info(name.encode())
    if not p or p.contents.len <= 0 or not p.contents.real:
        return []
    return [p.contents.real[i] for i in range(p.contents.len)]


def at(t, v, tq):   # first point at or after tq
    return next(y for x, y in zip(t, v) if x >= tq - 1e-12)


def ldr_out(lux):
    r = 10e3 * (lux / 10) ** -0.7
    return 5 * 10e3 / (10e3 + r)


def p_names():
    """Which alter forms reach a source inside a subckt instance while paused, and what readback says."""
    for form in ["alter v.xrl1.vstim dc = 100", "alter v.xrl1.vstim = 100", "alter @v.xrl1.vstim[dc] = 100",
                 "alter v.xrl1.vstim dc 100", "alter vstim dc = 100", "alter xrl1.vstim dc = 100"]:
        assert circ(LDR) == 0
        cmd("stop when time = 1m"); cmd("run")
        rc, err = cmd(form)
        cmd("resume")
        t, v = vec("time"), vec("v(out)")
        print(f"{form!r:40} rc={rc} err={err[:2]} out@0.5m={at(t, v, 0.5e-3):.5f} out@end={v[-1]:.5f}"
              f" (want {ldr_out(10):.5f} -> {ldr_out(100):.5f})")
        cmd("destroy all")
    assert circ(LDR) == 0
    cmd("stop when time = 1m"); cmd("run")
    LOG.clear(); lib.ngSpice_Command(b"print @v.xrl1.vstim[dc]")
    print("readback before:", [l for l in LOG if "dc" in l.lower()])
    cmd("alter v.xrl1.vstim dc = 100")
    LOG.clear(); lib.ngSpice_Command(b"print @v.xrl1.vstim[dc]")
    print("readback after:", [l for l in LOG if "dc" in l.lower()])
    rc, err = cmd("alter v.xrl1.nope dc = 1")
    print("unknown device: rc", rc, err)
    rc, err = cmd("alter v.xrl1.vstim nope = 1")
    print("unknown param: rc", rc, err)
    cmd("resume"); cmd("destroy all")


def p_step():
    """LDR: alter at a stop. Is the point at t1 old, the next one new, nothing in between?"""
    assert circ(LDR) == 0
    cmd("stop when time = 1m"); cmd("run")
    t = vec("time"); print("paused at", t[-1], "n", len(t))
    cmd("alter v.xrl1.vstim dc = 100"); cmd("resume")
    t, v = vec("time"), vec("v(out)")
    k = next(i for i, x in enumerate(t) if x >= 1e-3 - 1e-15)
    for i in range(k - 2, k + 4):
        print(f"  t={t[i]:.9e} v={v[i]:.6f}")
    pre = max(abs(y - ldr_out(10)) for x, y in zip(t, v) if x <= 1e-3)
    post = max(abs(y - ldr_out(100)) for x, y in zip(t, v) if x > 1e-3)
    print(f"max err before {pre:.2e}, after {post:.2e}, end t {t[-1]}")


def p_rc():
    """RC: source change at a pause vs analytic and vs a batch PWL step; capacitor continuity across the pause."""
    tau, t1 = 1e-3, 1e-3
    def ana(x):
        v1 = 5 * (1 - math.exp(-t1 / tau))
        return 5 * (1 - math.exp(-x / tau)) if x <= t1 else v1 * math.exp(-(x - t1) / tau)
    assert circ(RC) == 0
    cmd("stop when time = 1m"); cmd("run")
    vpause = vec("v(out)")[-1]
    cmd("alter v1 dc = 0"); cmd("resume")
    t, v = vec("time"), vec("v(out)")
    k = next(i for i, x in enumerate(t) if x >= t1 - 1e-15)
    print("pause v", vpause, "point at t1", v[k], "next", t[k + 1], v[k + 1])
    print("live max err vs analytic", max(abs(y - ana(x)) for x, y in zip(t, v)))
    cmd("destroy all")
    pwl = [l if not l.startswith("V1") else "V1 in 0 PWL(0 5 1m 5 1.000000001m 0)" for l in RC]
    assert circ(pwl) == 0
    cmd("run")
    tb, vb = vec("time"), vec("v(out)")
    print("batch PWL max err vs analytic", max(abs(y - ana(x)) for x, y in zip(tb, vb)), "pts", len(tb), len(t))


def p_stops():
    """Stops added while paused: does each next one pause exactly? Does 'stop clear' while paused clear them?"""
    assert circ(RC) == 0
    cmd("stop when time = 0.5m"); cmd("run")
    print("pause 1", vec("time")[-1])
    for want in (1e-3, 1.25e-3, 2e-3):
        rc, err = cmd(f"stop when time = {want:.17e}")
        cmd("resume")
        print(f"want {want} got {vec('time')[-1]!r} rc={rc} err={err}")
    cmd("resume")
    print("final", vec("time")[-1])
    cmd("destroy all")
    # stop added while paused, then 'stop clear' (as teardown does) -> run to the end?
    assert circ(RC) == 0
    cmd("stop when time = 0.5m"); cmd("run")
    cmd("stop when time = 1m"); cmd("stop clear"); cmd("resume")
    print("after stop clear, end", vec("time")[-1])


def p_alter_r():
    """Resistance alter mid-run (switch state / pot wiper)."""
    deck = ["r", "V1 a 0 DC 5", "RSW1 a out 1e9", "R2 out 0 1k", ".tran 10u 2m 0 10u uic", ".end"]
    assert circ(deck) == 0
    cmd("stop when time = 1m"); cmd("run")
    rc, err = cmd("alter rsw1 = 0.01"); cmd("resume")
    t, v = vec("time"), vec("v(out)")
    print("switch: before", at(t, v, 0.5e-3), "after", v[-1], "rc", rc, err)
    cmd("destroy all")
    pot = ["p", "V1 a 0 DC 5", "XRV1 a w 0 bench_pot R=10000 POS=0.5 RW=1",
           ".subckt bench_pot a w b params: R=10k POS=0.5 RW=1", "R1 a w {R*POS+RW}", "R2 w b {R*(1-POS)+RW}",
           ".ends bench_pot", ".tran 10u 2m 0 10u uic", ".end"]
    assert circ(pot) == 0
    cmd("stop when time = 1m"); cmd("run")
    cmd("alter r.xrv1.r1 = 2501"); rc, err = cmd("alter r.xrv1.r2 = 7501"); cmd("resume")
    t, v = vec("time"), vec("v(w)")
    print("pot: before", at(t, v, 0.5e-3), "after", v[-1], "want", 5 * 7501 / 10002, rc, err)


def p_error():
    """A change that breaks the solve: lux -> 0 makes the LDR resistance infinite/NaN."""
    assert circ(LDR) == 0
    cmd("stop when time = 1m"); cmd("run")
    cmd("alter v.xrl1.vstim dc = 0")
    LOG.clear(); rc = lib.ngSpice_Command(b"resume")
    print("resume rc", rc, [l for l in LOG if "rror" in l or "step" in l.lower()][:5])
    t, v = vec("time"), vec("v(out)")
    print("end t", t[-1], "last v", v[-3:])


def p_bg():
    """Mechanism (c): bg_run + bg_halt + alter + bg_resume. Where does it halt?"""
    deck = LDR[:-2] + [".tran 1u 200m 0 1u uic", ".end"]
    assert circ(deck) == 0
    cmd("bg_run"); time.sleep(0.05)
    cmd("bg_halt")
    while lib.ngSpice_running():
        time.sleep(0.001)
    th = vec("time")[-1]
    print("halted at", repr(th), "(arbitrary, not a breakpoint)")
    cmd("alter v.xrl1.vstim dc = 100"); cmd("bg_resume")
    time.sleep(0.01)
    while lib.ngSpice_running():
        time.sleep(0.01)
    time.sleep(0.2)   # REPORT e11: let the bg thread join before anything else
    t, v = vec("time"), vec("v(out)")
    print("end", t[-1], "v end", v[-1], "want", ldr_out(100))


def p_xspice():
    """XSPICE (event-driven) decks: does chunked stop/delete/resume give the same waveform as one run?"""
    deck = ["x", "V1 clk 0 PULSE(0 5 10u 1u 1u 49u 100u)", "abr [clk] [dclk] adc1", "aff qb dclk null null q qb dff1",
            "adac [q] [qa] dac1", "Rl qa 0 1k", ".model adc1 adc_bridge(in_low=1 in_high=2)",
            ".model dff1 d_dff(clk_delay=10n set_delay=10n reset_delay=10n)",
            ".model dac1 dac_bridge(out_low=0 out_high=5 t_rise=1u t_fall=1u)", ".tran 1u 2m 0 1u", ".end"]
    assert circ(deck) == 0
    cmd("run")
    t0, v0 = vec("time"), vec("v(qa)")
    cmd("destroy all"); cmd("delete all")
    assert circ(deck) == 0
    n = 97                        # boundaries that do not line up with clock edges
    cmd(f"stop when time = {2e-3 / n:.17e}"); cmd("run")
    for k in range(2, n):
        cmd("delete all"); cmd(f"stop when time = {k * 2e-3 / n:.17e}"); cmd("resume")
    cmd("delete all"); cmd("resume")
    t1, v1 = vec("time"), vec("v(qa)")
    edges = lambda t, v: [round(t[i] * 1e6, 3) for i in range(1, len(t)) if (v[i - 1] < 2.5) != (v[i] < 2.5)]
    e0, e1 = edges(t0, v0), edges(t1, v1)
    print("end", t1[-1], "edges", len(e0), len(e1), "max edge shift us", max(abs(a - b) for a, b in zip(e0, e1)),
          "pts", len(t0), len(t1))


if __name__ == "__main__":
    globals()["p_" + sys.argv[1]]()
