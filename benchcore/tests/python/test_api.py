"""Public Python API smoke: `import benchcore` from the installed wheel
(regression: the wheel used to ship only `_benchcore`), RC vs analytic, and
unknown observables raising instead of returning empty series.

Run: maturin build && pip install --force-reinstall target/wheels/*.whl
     python -m pytest tests/python
"""
import math

import pytest

import benchcore
from benchcore import InvalidConfigError, Simulation


def rc():
    return Simulation("RC").build_circuit(
        ["V1 in 0 DC 5", "R1 in out 1k", "C1 out 0 1u IC=0"]
    ).configure(tstep=10e-6, tstop=5e-3, tmax=10e-6)


def test_rc_matches_analytic():
    s = rc().observe("v(out)")
    s.run()
    pts = s.observable("v(out)")
    assert len(pts) > 50
    # 20 mV: same rationale as Rust T01 (docs/testing.md)
    assert max(abs(v - 5 * (1 - math.exp(-t / 1e-3))) for t, v in pts) < 0.020


def test_unknown_observable_raises():
    with pytest.raises(InvalidConfigError, match="v\\(nope\\)"):
        rc().observe("v(out)", "v(nope)").run()


def test_wrapper_is_the_package():
    assert benchcore.Simulation.__module__ == "benchcore"


# Standard 2N2222 card (task 7 ships the same).
Q2N2222 = (
    ".model q2n2222 NPN(IS=14.34f XTI=3 EG=1.11 VAF=74.03 BF=255.9 NE=1.307 "
    "ISE=14.34f IKF=.2847 XTB=1.5 BR=6.092 NC=2 ISC=0 IKR=0 RC=1 CJC=7.306p "
    "MJC=.3416 VJC=.75 FC=.5 CJE=22.01p MJE=.377 VJE=.75 TR=46.91n TF=411.1p "
    "ITF=.6 VTF=1.7 XTF=3 RB=10)"
)


def ce_amp(save=True):
    s = Simulation("ce").build_circuit(
        ["Vcc vcc 0 DC 5", "Rb vcc b 430k", "Rc vcc c 1k", "Q1 c b 0 q2n2222"]
    ).add_model(Q2N2222)
    if save:
        s.add(".save all @q1[ic] @q1[ib]")
    s.configure(tstep=1e-6, tstop=1e-4)
    return s.observe("v(c)", "v(b)", "@q1[ic]", "@q1[ib]")


def test_ce_amp_operating_point():
    s = ce_amp()
    assert s.validate() == []
    s.run()
    vc, vb, ic, ib = (
        s.observable(n)[-1][1] for n in ("v(c)", "v(b)", "@q1[ic]", "@q1[ib]")
    )
    assert abs(vc - 3.389) < 0.01
    assert abs(ic - 1.611e-3) < 1e-5
    assert abs(ib - 10.10e-6) < 0.05e-6
    assert abs(ic - (5 - vc) / 1e3) < 1e-7    # KVL on Rc (not Bf*Ib: Early effect)
    assert abs(ib - (5 - vb) / 430e3) < 1e-9  # KVL on Rb


def test_unsaved_device_current_raises():
    with pytest.raises(InvalidConfigError, match="@q1\\[ic\\]"):
        ce_amp(save=False).run()


# ---- engine robustness (docs/plans/ngspice-library/DESIGN.md 4.5) ----
from benchcore import BackendError, InvalidCircuitError, SolverError


def divider():
    s = Simulation("div").build_circuit(["V1 a 0 DC 4", "R1 a b 1k", "R2 b 0 3k"])
    s.configure(tstep=1e-6, tstop=2e-5).observe("v(b)").run()
    return s.observable("v(b)")[-1][1]


def test_poisoned_engine_recovers():
    """L7: a deck that makes libngspice abort used to leave it refusing every later circuit in the process.
    Task 4 changed the trigger deliberately: an undefined {param} no longer reaches the engine (the validator refuses
    it, UNDEFINED-PARAM), so the abort comes from a missing .include file, which the validator lets through."""
    undefined = Simulation("undef").build_circuit(["V1 a 0 DC 1", "R1 a 0 {nope}"]).configure(tstep=1e-6, tstop=2e-5)
    with pytest.raises(InvalidCircuitError, match=r"UNDEFINED-PARAM.*'nope'"):
        undefined.observe("v(a)").run()
    bad = Simulation("poison").build_circuit(["V1 a 0 DC 1", "R1 a 0 1k", ".include /nonexistent/bench_missing.lib"])
    bad.configure(tstep=1e-6, tstop=2e-5).observe("v(a)")
    with pytest.raises(BackendError, match="(?i)reloaded.*bench_missing"):
        bad.run()
    assert divider() == pytest.approx(3.0, abs=1e-6)
    with pytest.raises(BackendError):
        bad.run()
    assert divider() == pytest.approx(3.0, abs=1e-6)


def test_unknown_subckt_is_an_error_not_an_empty_success(tmp_path):
    """L6: ngspice prints 'unknown subckt' but returns rc 0 and no data; that used to be ok with 0 points.
    Updated deliberately in task 4: the static UNDEFINED-SUBCKT check now refuses it before the engine (this test
    expected the runtime SolverError). The runtime scan stays as a second gate: correctness.rs t62 drives the raw
    adapter, and an .include (which may supply the definition) still reaches the engine and fails there."""
    s = Simulation("us").build_circuit(["V1 a 0 DC 1", "R1 a 0 1k", "X1 a 0 nosuch"])
    with pytest.raises(InvalidCircuitError, match=r"UNDEFINED-SUBCKT.*'nosuch'"):
        s.configure(tstep=1e-6, tstop=2e-5).observe("v(a)").run()
    assert [(f["code"], f["class"], f["scope"]) for f in s.validate() if f["severity"] == "error"] == [
        ("UNDEFINED-SUBCKT", "dependency", "")]
    (tmp_path / "empty.lib").write_text("* defines nothing\n")
    inc = Simulation("inc").build_circuit(["V1 a 0 DC 1", "R1 a 0 1k", "X1 a 0 nosuch", f".include {tmp_path}/empty.lib"])
    inc.configure(tstep=1e-6, tstop=2e-5).observe("v(a)")
    assert [(f["code"], f["class"]) for f in inc.validate()] == [("OPAQUE-X", "limited")]
    with pytest.raises(SolverError, match="unknown subckt"):
        inc.run()
    assert divider() == pytest.approx(3.0, abs=1e-6)


SPICEINIT_PROBE = """
import benchcore
s = benchcore.Simulation("tc").build_circuit(["V1 b 0 DC 1", "R1 b 0 1k TC=0.01,0"])
s.configure(tstep=1e-6, tstop=2e-5).observe("v(b)")
try:
    s.run(); print("RAN", s.observable("v(b)")[-1][1])
except benchcore.SolverError as e:
    print("REFUSED", e)
"""


def test_spiceinit_cannot_change_the_compat_mode(tmp_path):
    """L13: libngspice runs ./.spiceinit at init. `TC=a,b` parses only in PSpice mode (psa: v(b) = 1, verified with
    the pin removed), so a .spiceinit that selects psa must not make this deck run: Bench pins the mode."""
    import subprocess, sys
    (tmp_path / ".spiceinit").write_text("set ngbehavior=psa\n")
    out = subprocess.run([sys.executable, "-c", SPICEINIT_PROBE], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert out.stdout.startswith("REFUSED"), out.stdout + out.stderr


PS_LIB = """* PSpice-syntax model
.SUBCKT PSLIM in out PARAMS: LO=-1 HI=1
E1 x 0 VALUE = { LIMIT(V(in), {LO}, {HI}) }
R1 x out 1
R2 out 0 1MEG
.ENDS
"""


def test_compat_marker_selects_ps_mode_for_one_deck_only(tmp_path):
    """G10: the deck comment `* bench-compat: ps` makes the engine `set ngbehavior=ps` after the usual unset, for that
    circuit only. Without it an included LIMIT() is silently 0 (a wrong success); the next marker-free deck is pinned
    off again."""
    lib = tmp_path / "ps.lib"
    lib.write_text(PS_LIB)

    def out(marker):
        s = Simulation("t").build_circuit(["V3 big 0 DC 3", "X2 big o2 PSLIM LO=-1 HI=2", "R10 o2 0 10k"])
        s.add_model(f'.include "{lib}"')
        if marker:
            s.add_model("* bench-compat: ps")
        s.configure(tstep=1e-5, tstop=1e-4).observe("v(o2)")
        s.run()
        return s.observable("v(o2)")[-1][1]
    assert out(False) == 0.0
    assert out(True) == pytest.approx(2.0, abs=1e-3)
    assert out(False) == 0.0


def test_validate_reports_class_and_scope_through_nested_subcircuits():
    """Task 4: findings carry `class` (syntax, dependency, connectivity, unsupported, limited, value) and `scope` ("" top
    level, ".subckt <name>" a definition, "x1.xa" an instance path); benchcore.validate needs no simulation session."""
    deck = ["t", "V1 a 0 DC 1", "X1 a outer", "X2 a outer",
            ".subckt outer p", "XA p inner", "A1 [p] [d] adc", ".model adc adc_bridge", ".subckt inner q",
            "R1 q 0 1k", "C1 m n 1n", "D1 q 0 nomodel", ".ends", ".ends", ".tran 1u 10u uic", ".end"]
    got = sorted((f["code"], f["class"], f["scope"], f["severity"]) for f in benchcore.validate(deck)
                 if f["code"] != "DANGLING")                            # (x1.d, x1.xa.m, ...: single-terminal nodes)
    assert got == sorted([
        ("LIMITED-ANALYSIS", "limited", ".subckt outer", "warn"),               # once per definition, not per instance
        ("UNDEFINED-MODEL", "dependency", ".subckt outer/inner", "error"),
        *[("NO-DC-PATH", "connectivity", f"{x}.xa", "error") for x in ("x1", "x2") for _ in "mn"],
    ]), got


PAUSE_UNSAVED_PROBE = """
import benchcore
s = benchcore.Simulation("g32").build_circuit(["V1 a 0 DC 4", "R1 a b 1k", "R2 b 0 3k"])
s.configure(tstep=1e-6, tstop=1e-3).observe("v(b)", "@v1[dc]").pause_at(5e-4)
try:
    s.run(); print("RAN")
except benchcore.InvalidConfigError as e:
    print("REFUSED", e)
s = benchcore.Simulation("after").build_circuit(["V1 a 0 DC 4", "R1 a b 1k", "R2 b 0 3k"])
s.configure(tstep=1e-6, tstop=2e-5).observe("v(b)").run()
print("AFTER", s.observable("v(b)")[-1][1])
"""


def test_pause_with_an_unsaved_device_observable_is_refused_not_a_crash():
    """G32 (task 9.6): reading an unsaved @dev[param] through ngGet_Vec_Info at a pause corrupts ngspice and the next
    resume dumped core. The batch pause path now refuses it before the engine, like live runs (and like the full path,
    which fails after the run with the same error class). Run in a subprocess: the old behaviour kills the process."""
    import subprocess, sys
    out = subprocess.run([sys.executable, "-c", PAUSE_UNSAVED_PROBE], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"process died (rc {out.returncode}): {out.stdout}{out.stderr}"
    lines = out.stdout.splitlines()
    assert lines[0].startswith("REFUSED") and "@v1[dc]" in lines[0] and ".save" in lines[0], out.stdout
    assert lines[1] == "AFTER 3.0", out.stdout


def test_gear_method_is_one_options_line():
    """G30 (task 9.6): configure(method="gear") adds `.options method=gear` before the .tran line; trap (the default)
    adds nothing, so existing decks are unchanged; anything else is a config error."""
    s = rc()
    assert not any(l.startswith(".options") for l in s._deck())
    deck = s.configure(method="gear")._deck()
    assert deck[-3:-1] == [".options method=gear", deck[-2]] and deck[-2].startswith(".tran")
    s.observe("v(out)").run()
    assert abs(s.observable("v(out)")[-1][1] - 5 * (1 - math.exp(-5))) < 0.02
    with pytest.raises(InvalidConfigError):
        rc().configure(method="euler")


# ---- AC small-signal analysis (G17) ----
def test_ac_rc_lowpass_minus_3db_and_bjt_small_signal_gain():
    """Simulation.ac() builds the .ac card; observable() gives (re, im) per frequency. The RC low-pass is -3.0103 dB
    and -45° at fc = 1/(2πRC); the 2N2222 common-emitter stage (ce_amp's bias, driven through 10 µF) has the
    textbook mid-band gain -gm·(Rc ‖ ro), gm = Ic/VT, ro = (VAF + Vce)/Ic, less the 10 Ω base resistance's share of
    vbe (rπ = β/gm with β = Ic/Ib): within 1 %."""
    fc = 1 / (2 * math.pi * 1e3 * 1e-6)
    s = Simulation("rc").build_circuit(["V1 in 0 DC 0 AC 1", "R1 in out 1k", "C1 out 0 1u"])
    s.ac("lin", 1, fc, fc).observe("v(out)").run()
    (re, im), = s.observable("v(out)")
    assert s.frequency == pytest.approx([fc])
    assert 20 * math.log10(math.hypot(re, im)) == pytest.approx(-10 * math.log10(2), abs=1e-9)
    assert math.degrees(math.atan2(im, re)) == pytest.approx(-45, abs=1e-7)
    op = ce_amp()
    op.run()
    vc, ic, ib = (op.observable(n)[-1][1] for n in ("v(c)", "@q1[ic]", "@q1[ib]"))
    gm = ic / (1.380649e-23 * 300.15 / 1.602176634e-19)
    ro, rpi = (74.03 + vc) / ic, (ic / ib) / gm
    want = gm * (1e3 * ro / (1e3 + ro)) * rpi / (rpi + 10)
    amp = Simulation("ce ac").build_circuit(
        ["Vcc vcc 0 DC 5", "Rb vcc b 430k", "Rc vcc c 1k", "Q1 c b 0 q2n2222", "Vin in 0 DC 0 AC 1", "Cin in b 10u"]
    ).add_model(Q2N2222).ac("dec", 10, 100, 100e3).observe("v(c)")
    amp.run()
    k = amp.frequency.index(pytest.approx(1e3))
    z = complex(*amp.observable("v(c)")[k])
    assert abs(z) == pytest.approx(want, rel=0.01)
    assert abs(math.degrees(math.atan2(z.imag, z.real))) == pytest.approx(180, abs=3)


def test_ac_refusals_and_transient_unchanged():
    """A bad sweep and a device @ vector are config errors; ac(None) goes back to the unchanged transient deck."""
    s = rc().observe("v(out)")
    tran_deck = s._deck()
    with pytest.raises(InvalidConfigError, match="fstart"):
        s.ac("dec", 10, 0, 1e3).run()
    with pytest.raises(InvalidConfigError, match="operating-point"):
        ce_amp().ac("dec", 10, 1, 1e3).run()
    assert s.ac(None)._deck() == tran_deck
    s.run()
    assert len(s.time) > 50 and s.frequency == []


# ---- G29: value checks of behavioural expressions (benchcore/src/interval.rs) ----
LDR_DECK = ["t", ".param lx=10", "X1 a b ldr LUX={lx}", "V1 a 0 5", "R1 b 0 1k",
            ".subckt ldr a b params: LUX=10", "VSTIM stim 0 DC {LUX}", "RSTIM stim 0 1G",
            "B1 a b I={V(a,b)/(1k*pow(V(stim)/10,-0.7))}", ".ends"]


def codes(findings, severity="error"):
    return sorted(f["code"] for f in findings if f["severity"] == severity)


def test_validate_takes_declared_ranges_for_params():
    """A range replaces the param's one value: (0, 1e5] proves the LDR law, [0, 1e5] reaches pow(0, -0.7)."""
    open_lo = benchcore.validate(LDR_DECK, {"lx": (0.0, 1e5, True, False)})
    assert codes(open_lo) == [] and not any(f["class"] == "limited" for f in open_lo), open_lo
    closed = benchcore.validate(LDR_DECK, {"lx": (0.0, 1e5, False, False)})
    assert codes(closed) == ["B-DOMAIN"] and "LUX = 0" in closed[0]["message"] and closed[0]["scope"] == "x1", closed
    assert codes(benchcore.validate(LDR_DECK)) == []                       # the default, 10, is fine on its own


def test_a_run_with_a_value_hazard_is_refused_before_ngspice():
    """The deck runs through the same checks: sqrt of a negative DC node is a fatal ngspice error, caught statically."""
    deck = ["V1 in 0 DC 5", "R1 in 0 1k", "B1 o 0 V=sqrt(V(in)-6)", "R2 o 0 1k"]
    s = Simulation("hazard").build_circuit(deck).configure(tstep=1e-6, tstop=1e-5).observe("v(o)")
    with pytest.raises(InvalidCircuitError, match="B-DOMAIN"):
        s.run()
    ok = Simulation("fine").build_circuit(["V1 in 0 DC 5", "R1 in 0 1k", "B1 o 0 V=sqrt(V(in))", "R2 o 0 1k"])
    ok.configure(tstep=1e-6, tstop=1e-5).observe("v(o)").run()
    assert abs(ok.observable("v(o)")[-1][1] - 5 ** 0.5) < 1e-6


@pytest.mark.parametrize("word", ["failed", "timestep", "convergence", "aborted", "singular"])
def test_trigger_words_in_user_names_are_not_solver_failures(word):
    """ngspice echoes the title (`Circuit: <title>`), node names (result tables) and `.model` lines; a title, node,
    device or model named like a failure message must not be reported as a solver failure."""
    s = Simulation(f"{word} {word} demo").build_circuit([
        f"V{word} {word} 0 DC 1", f"R{word} {word} n{word} 1k", f"D{word} n{word} 0 m{word}",
        f".model m{word} D(Is=1e-14 bogus_{word}=3)",  # benign stderr warning that echoes the model line
    ]).configure(tstep=1e-6, tstop=1e-5).observe(f"v(n{word})")
    s.run()
    assert 0.3 < s.observable(f"v(n{word})")[-1][1] < 1.0


def test_missing_libngspice_names_every_path_tried(tmp_path):
    """The library is loaded once per process, so the lookup is exercised in a subprocess."""
    import os, subprocess, sys
    probe = (
        "import benchcore\n"
        "s = benchcore.Simulation('x').build_circuit(['V1 a 0 DC 1', 'R1 a 0 1k'])\n"
        "s.configure(tstep=1e-6, tstop=1e-5).observe('v(a)')\n"
        "try:\n    s.run()\nexcept benchcore.BackendError as e:\n    print('BACKEND', e)\n"
    )
    env = dict(os.environ, BENCH_LIBNGSPICE="/nonexistent/libngspice.so")
    out = subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert out.stdout.startswith("BACKEND"), out.stdout + out.stderr
    assert "/nonexistent/libngspice.so" in out.stdout and "BENCH_LIBNGSPICE" in out.stdout
