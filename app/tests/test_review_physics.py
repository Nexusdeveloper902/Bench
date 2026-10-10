"""Regression tests from the independent review (docs/review/test-review.md). Real path: Document -> app.sim ->
benchcore -> libngspice, oracles hard-coded or computed from the values TYPED into the model (never read back from
the emitted netlist/card, never from the SI parser under test).

Tolerances: DC resistive = 1e-6 (ngspice linear solve, vntol 1 uV); transient RC/RL = 1e-3 relative to the initial
value (trapezoidal, step = tau/100, measured ~1e-5); sources are sampled on the solver grid (step <= edge/10), so
waveform comparisons use 2 mV on 5 V; diode oracle rel 1e-4 (see test_analytic_diode)."""
import math
import pytest
from app.model import Document
from app.sim import run_document
from app.tests.test_analytic_diode import shockley
from app.tests.test_core_analytic import at, wire


def divider(r1, r2, v="10", grounds=1):
    d = Document()
    d.add_part("BAT", dc=v); d.add_part("R", R=r1); d.add_part("R", R=r2); d.add_part("GND")
    if grounds == 2: d.add_part("GND")
    wire(d, ("V1.p", "R1.a"), ("R1.b", "R2.a"), ("R2.b", "GND1.g"), ("V1.n", "GND2.g" if grounds == 2 else "GND1.g"))
    d.tstop, d.tstep = "1m", "100u"
    return d


def test_two_grounds_are_one_node():
    d = divider("1k", "1k", grounds=2)
    assert d.nets()["GND1.g"] == d.nets()["GND2.g"] == "0"
    r = run_document(d)
    assert r.ok, r.message
    assert r.stats("V(R2)")["final"] == pytest.approx(5.0, abs=1e-6)   # 10 V * 1k/2k


# hard-coded expected values: no parser in the oracle, uneven pair so a prefix error changes the ratio
@pytest.mark.parametrize("r1,r2,v,vout", [("1meg", "1k", "10", 10 * 1e3 / 1.001e6), ("1k", "1meg", "10", 10 * 1e6 / 1.001e6),
                                          ("2.2M", "4.7k", "3.3", 3.3 * 4.7e3 / 2.2047e6), ("100", "300", "12", 9.0)])
def test_divider_hardcoded(r1, r2, v, vout):
    r = run_document(divider(r1, r2, v))
    assert r.ok, r.message
    assert r.stats("V(R2)")["final"] == pytest.approx(vout, abs=1e-6)


def test_ic_roundtrip_capacitor_and_inductor(tmp_path):
    # C: 5 V discharging through 1k, 1u: v = 5 exp(-t/1ms)
    d = Document()
    d.add_part("C", C="1u", ic="5"); d.add_part("R", R="1k"); d.add_part("GND")
    wire(d, ("C1.a", "R1.a"), ("R1.b", "GND1.g"), ("C1.b", "GND1.g"))
    d.tstop, d.tstep = "5m", "10u"
    p = tmp_path / "c.json"; d.save(p)
    d2 = Document.load(p)
    assert d2.parts["C1"]["params"]["ic"] == "5"
    r = run_document(d2); assert r.ok, r.message
    s = r.get("V(C1)")
    for t in (0.5e-3, 1e-3, 3e-3):
        assert at(s, t) == pytest.approx(5 * math.exp(-t / 1e-3), abs=5e-3)
    # L: 10 mA through 100 ohm, 10 mH: i = 10m exp(-t R/L), tau = 100 us
    d = Document()
    d.add_part("L", L="10m", ic="10m"); d.add_part("R", R="100"); d.add_part("GND")
    wire(d, ("L1.a", "R1.a"), ("R1.b", "GND1.g"), ("L1.b", "GND1.g"))
    d.tstop, d.tstep = "500u", "1u"
    p = tmp_path / "l.json"; d.save(p)
    d2 = Document.load(p)
    assert d2.parts["L1"]["params"]["ic"] == "10m"
    r = run_document(d2); assert r.ok, r.message
    s = r.get("I(L1)")
    for t in (50e-6, 100e-6, 300e-6):
        assert abs(at(s, t)) == pytest.approx(10e-3 * math.exp(-t / 100e-6), abs=1e-5)


@pytest.mark.parametrize("vs,r,Is,n,rs", [("5", "1000", "1e-14", "1", "0"), ("1", "100", "1p", "1.5", "20"),
                                          ("3", "50", "10f", "1.2", "20")])
def test_diode_uses_typed_values(vs, r, Is, n, rs):
    """Oracle takes the numbers typed here; Rs = 20 ohm moves I by >15 % so an ignored Rs fails."""
    d = Document()
    d.add_part("V", dc=vs); d.add_part("R", R=r); d.add_part("D", Is=Is, N=n, Rs=rs); d.add_part("GND")
    wire(d, ("V1.p", "R1.a"), ("R1.b", "D1.a"), ("D1.k", "GND1.g"), ("V1.n", "GND1.g"))
    d.tstop, d.tstep = "100u", "10u"
    res = run_document(d); assert res.ok, res.message
    typed = {"1p": 1e-12, "10f": 1e-14, "1e-14": 1e-14}[Is]
    i_exp, _ = shockley(float(vs), float(r), typed, float(n), float(rs))
    assert res.stats("I(D1)")["final"] == pytest.approx(i_exp, rel=1e-4)


def src_doc(**p):
    d = Document()
    d.add_part("V", **p); d.add_part("R", R="1k"); d.add_part("GND")
    wire(d, ("V1.p", "R1.a"), ("R1.b", "GND1.g"), ("V1.n", "GND1.g"))
    return d


def test_pulse_unequal_rise_fall():
    # 0->5 V, td 1m, tr 1m, pw 2m, tf 3m, per 10m: rising 1-2 ms, high to 4 ms, falling 4-7 ms
    d = src_doc(mode="PULSE", v1=0, v2=5, td="1m", tr="1m", tf="3m", pw="2m", per="10m")
    d.tstop, d.tstep = "10m", "20u"
    r = run_document(d); assert r.ok, r.message
    s = r.get("V(R1)")
    for t, v in ((0.5e-3, 0), (1.5e-3, 2.5), (3e-3, 5), (4e-3 + 1.5e-3, 2.5), (7.5e-3, 0)):
        assert at(s, t) == pytest.approx(v, abs=2e-3), t
    assert at(s, 5e-3) == pytest.approx(5 * (1 - 1 / 3), abs=2e-3)    # a swapped tr/tf would give 5*(1-1/1)... off


def test_sin_offset_vs_amplitude():
    d = src_doc(mode="SIN", vo="2", va="1", freq="1k")
    d.tstop, d.tstep = "2m", "5u"
    r = run_document(d); assert r.ok, r.message
    s = r.get("V(R1)")
    for t in (0.25e-3, 0.5e-3, 0.75e-3, 1.25e-3):
        assert at(s, t) == pytest.approx(2 + math.sin(2 * math.pi * 1e3 * t), abs=2e-3)
    vals = [v for _, v in s]
    assert min(vals) == pytest.approx(1.0, abs=5e-3) and max(vals) == pytest.approx(3.0, abs=5e-3)


def test_dc_current_source_into_resistor():
    d = Document()
    d.add_part("I", dc="2m"); d.add_part("R", R="1k"); d.add_part("GND")
    wire(d, ("I1.p", "GND1.g"), ("I1.n", "R1.a"), ("R1.b", "GND1.g"))
    d.tstop, d.tstep = "1m", "100u"
    r = run_document(d); assert r.ok, r.message
    # SPICE: current flows p -> n through the source, so p=gnd, n=R.a pushes current INTO R: V = +I R
    assert r.stats("V(R1)")["final"] == pytest.approx(2.0, abs=1e-6)
    assert r.stats("I(R1)")["final"] == pytest.approx(2e-3, rel=1e-6)
