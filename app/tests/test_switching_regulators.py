"""Averaged buck and boost converters (G21): BUCK_AVG, BUCK_AVG_CTL, BOOST_AVG, BOOST_AVG_CTL.

Every check goes through the real path (Document -> netlist -> app.sim -> benchcore -> ngspice) and compares with the
analytic CCM formulas of Erickson and Maksimovic, Fundamentals of Power Electronics, chapters 2, 7 and 8:
- DC conversion ratio and conduction-loss efficiency (transient run settled to steady state);
- a load-current step against the linear ODE (buck) or the averaged ODE integrated by RK4 (boost);
- the AC small-signal transfer functions: line-to-output and control-to-output, including the boost RHP zero."""
import math

import numpy as np
import pytest

from app.catalog import CATALOG
from app.model import Document
from app.sim import run_document

IDS = ["BUCK_AVG", "BUCK_AVG_CTL", "BOOST_AVG", "BOOST_AVG_CTL"]


def build(kind, ctl=False, vin="12", D="0.5", L="100u", RL="1u", C="100u", R="1", load=None, vac="0", dac="0"):
    """V1 (dc vin, ac vac) -> converter U1 -> C1 and a load (R1, or the PWL current source I1 when `load` is its pts) to ground."""
    d = Document()
    d.add_part("V", 0, 0, dc=vin, ac=vac)
    d.add_part(f"{kind}_AVG" + ("_CTL" if ctl else ""), 100, 0, D=D, L=L, RL=RL)
    d.add_part("C", 200, 0, C=C)
    d.add_part("ISRC_PWL" if load else "R", 300, 0, **({"pts": load} if load else {"R": R}))
    d.add_part("GND", 0, 200)
    load_pins = ("I1.p", "I1.n") if load else ("R1.a", "R1.b")
    for a, b in (("V1.p", "U1.vin"), ("V1.n", "GND1.g"), ("U1.gnd", "GND1.g"), ("U1.out", "C1.a"), ("C1.b", "GND1.g"),
                 (load_pins[0], "C1.a"), (load_pins[1], "GND1.g")):
        d.connect(a, b)
    if ctl:
        d.add_part("V", 0, 100, dc="0", ac=dac)
        d.connect("V2.p", "U1.ctl"); d.connect("V2.n", "GND1.g")
    return d


def transient(d, tstop, tstep):
    d.tstop, d.tstep = tstop, tstep
    r = run_document(d)
    assert r.ok and r.analysis == "transient", r.message
    return r


def ac(d, fstart="10", fstop="100k", points="20"):
    d.analysis, d.ac = "ac", {"sweep": "dec", "points": points, "fstart": fstart, "fstop": fstop}
    r = run_document(d)
    assert r.ok and r.analysis == "ac", r.message
    return r


@pytest.mark.parametrize("pid", IDS)
def test_packages_load_clean_and_state_their_limits(pid):
    c = CATALOG.get(pid)
    sec = c.sim["electrical"]
    assert sec["live"] == [] and sec["verification"] == "analytic" and sec["_status"] == "full"
    text = " ".join(c.limitations)
    assert "DCM" in text and "ripple" in text and "CCM" in text


@pytest.mark.parametrize("kind, D, ratio", [("BUCK", "0.25", 0.25), ("BUCK", "0.8", 0.8), ("BOOST", "0.25", 1 / 0.75), ("BOOST", "0.6", 2.5)])
@pytest.mark.parametrize("ctl", [False, True])
def test_dc_conversion_ratio(kind, D, ratio, ctl):
    """Vout = D Vin (buck), Vin / (1 - D) (boost); RL is 1 uOhm so the loss is negligible."""
    r = transient(build(kind, ctl, D=D, R="5", C="10u"), "3m", "2u")
    assert r.stats("V(C1)")["final"] == pytest.approx(12 * ratio, rel=2e-4)


@pytest.mark.parametrize("kind", ["BUCK", "BOOST"])
def test_conduction_loss_efficiency(kind):
    """With RL = 0.5 ohm: buck Vout = D Vin R/(R+RL) and efficiency R/(R+RL); boost efficiency 1/(1 + RL/(D'^2 R))."""
    D, RL, R, vin = 0.5, 0.5, 10.0, 12.0
    r = transient(build(kind, D=str(D), RL=str(RL), R=str(R)), "6m", "4u")
    vout, iin = r.stats("V(C1)")["final"], -r.get("I(V1)")[-1][1]
    if kind == "BUCK":
        eta, v_expect = R / (R + RL), D * vin * R / (R + RL)
    else:
        dp = 1 - D
        eta = 1 / (1 + RL / (dp * dp * R))
        v_expect = vin / dp * eta
    assert vout == pytest.approx(v_expect, rel=2e-4)
    assert (vout * vout / R) / (vin * iin) == pytest.approx(eta, rel=2e-4)


def reference(kind, D, vin, L, C, RL, i1, i2, t0, tstop):
    """The averaged equations integrated by fixed-step RK4 (h = 0.1 us, far below the 1.6 kHz LC period) from rest (what the
    converter does from a cold start), with the load current i1 until t0 and i2 after: buck L iL' = D Vin - RL iL - v;
    boost L iL' = Vin - RL iL - D' v and C v' = D' iL - Iload. -> (t, v) arrays."""
    k = D if kind == "BUCK" else 1 - D
    src = D * vin if kind == "BUCK" else vin
    def f(il, v, iload):
        return (src - RL * il - k * v if kind == "BOOST" else src - RL * il - v) / L, ((il if kind == "BUCK" else k * il) - iload) / C
    h, n0, n = 1e-7, round(t0 / 1e-7), round(tstop / 1e-7)
    il = v = 0.0
    ts, vs = [0.0], [0.0]
    for i in range(n):
        iload = i1 if i < n0 else i2   # the step lands on a step boundary
        a = f(il, v, iload)
        b = f(il + h / 2 * a[0], v + h / 2 * a[1], iload)
        c = f(il + h / 2 * b[0], v + h / 2 * b[1], iload)
        d = f(il + h * c[0], v + h * c[1], iload)
        il += h / 6 * (a[0] + 2 * b[0] + 2 * c[0] + d[0])
        v += h / 6 * (a[1] + 2 * b[1] + 2 * c[1] + d[1])
        if i % 10 == 9: ts.append((i + 1) * h); vs.append(v)
    return np.array(ts), np.array(vs)


@pytest.mark.parametrize("kind, vin, D, RL, i1, i2, rise", [("BUCK", 12.0, 0.5, 0.5, 1.0, 3.0, 2.0), ("BOOST", 6.0, 0.5, 0.2, 0.5, 1.0, 1.0)])
def test_load_step_matches_the_averaged_equations(kind, vin, D, RL, i1, i2, rise):
    """Load current i1 -> i2 at 4 ms into an LC (L 100 uH, C 100 uF), from a cold start, against the averaged ODE integrated
    independently. The buck also lands on its closed-form steady states D Vin - RL I before and after the step."""
    L, C, t0, tstop = 100e-6, 100e-6, 4e-3, 7e-3
    pts = f"0 {i1}, {t0} {i1}, {t0 + 1e-9} {i2}"
    r = transient(build(kind, D=str(D), RL=str(RL), L="100u", C="100u", load=pts, vin=str(vin)), "7m", "2u")
    ref_t, ref_v = reference(kind, D, vin, L, C, RL, i1, i2, t0, tstop)
    ts, vs = (np.array(x) for x in zip(*r.get("V(C1)")))
    assert np.max(np.abs(vs - np.interp(ts, ref_t, ref_v))) < 0.01 * rise
    if kind == "BUCK":
        assert vs[ts < t0][-1] == pytest.approx(D * vin - RL * i1, abs=5e-3)
        assert vs[-1] == pytest.approx(D * vin - RL * i2, abs=5e-3)
    seg = vs[ts > t0]
    assert abs(seg.min() - seg.max()) > 0.2 * rise   # the step really moves the output (undershoot), so the match is not trivial


def gain(r, name):
    return [(f, z) for f, z in r.get(name)]


@pytest.mark.parametrize("ctl", [False, True])
def test_buck_small_signal_lc_double_pole(ctl):
    """Line-to-output D/(1 + sL/R + s^2 LC) and control-to-output Vin/(1 + sL/R + s^2 LC), 10 Hz..100 kHz (R = 10 ohm)."""
    L, C, R, D, vin = 100e-6, 100e-6, 10.0, 0.4, 12.0
    d = build("BUCK", ctl, D=str(D), R=str(R), vac="0" if ctl else "1", dac="1" if ctl else "0")
    pts = gain(ac(d), "V(C1)")
    peak = 0.0
    for f, z in pts:
        s = 2j * math.pi * f
        den = 1 + s * L / R + s * s * L * C
        assert z == pytest.approx((vin if ctl else D) / den, rel=2e-4)
        peak = max(peak, abs(z))
    f0 = 1 / (2 * math.pi * math.sqrt(L * C))
    fpk, zpk = max(pts, key=lambda p: abs(p[1]))
    assert fpk == pytest.approx(f0, rel=0.13) and abs(zpk) > 2 * (vin if ctl else D) * 0.9   # Q = R sqrt(C/L) = 10


@pytest.mark.parametrize("ctl", [False, True])
def test_boost_small_signal_rhp_zero(ctl):
    """Line-to-output (1/D')/(1 + s L/(D'^2 R) + s^2 L C/D'^2); control-to-output (Vg/D'^2)(1 - s L/(D'^2 R)) over the same
    denominator, whose numerator is the right-half-plane zero at fz = D'^2 R/(2 pi L)."""
    L, C, R, D, vin = 100e-6, 100e-6, 10.0, 0.5, 6.0
    dp = 1 - D
    d = build("BOOST", ctl, D=str(D), R=str(R), vin=str(vin), vac="0" if ctl else "1", dac="1" if ctl else "0")
    pts = gain(ac(d, fstart="10", fstop="50k"), "V(C1)")
    fz = dp * dp * R / (2 * math.pi * L)
    for f, z in pts:
        s = 2j * math.pi * f
        den = 1 + s * L / (dp * dp * R) + s * s * L * C / (dp * dp)
        num = (vin / dp ** 2) * (1 - s * L / (dp * dp * R)) if ctl else 1 / dp
        assert z == pytest.approx(num / den, rel=2e-4)
    if ctl:
        # the zero's signature: the phase of the numerator alone lags by atan(f/fz) (a left-half zero would lead)
        fk, zk = min(pts, key=lambda p: abs(p[0] - fz))
        s = 2j * math.pi * fk
        den = 1 + s * L / (dp * dp * R) + s * s * L * C / (dp * dp)
        assert np.angle(zk * den) == pytest.approx(-math.atan(fk / fz), abs=1e-3)


def test_dcm_is_not_modelled_a_negative_load_current_flows_back_into_the_source():
    """The documented limitation, pinned: with the load current reversed the averaged inductor current goes negative and the
    converter returns power to the source, as a synchronous converter would; a diode converter would be in DCM instead."""
    r = transient(build("BUCK", D="0.5", RL="1", load="0 -1, 1m -1"), "4m", "2u")
    assert r.get("I(V1)")[-1][1] == pytest.approx(0.5, rel=1e-3)   # V1 absorbs D * 1 A (ngspice: positive = into the source)
