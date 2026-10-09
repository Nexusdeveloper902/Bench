"""Diode and LED through the REAL path (Document -> app.sim -> simcore -> libngspice) vs an independent Shockley
oracle solved in the test by bisection, using exactly the model card the app emits.

Oracle: ngspice's DC diode (no breakdown, BV = inf) is I = Is (exp(Vj / (N Vt)) - 1) + GMIN Vj with series Rs,
Vt = k T / q at TNOM = 27 C (300.15 K), GMIN = 1e-12 S across the junction (ngspice default). KVL around
V - R - D: Vs = Vj + I (R + Rs).

Tolerances: the circuit is DC, so transient integration plays no role; the operating point is Newton-converged to
reltol 1e-3 *per iteration step*, but ngspice keeps iterating while timesteps proceed, and the final points are
converged far tighter. Measured relative current error vs the oracle: 5e-8 (N=1, Rs=0), 5e-8 (N=1.75, Rs=0.57),
1.3e-6 (N=1.5, Rs=2), LED 2.1e-7; reverse 2e-7. Tolerance rel 1e-4 (~75x the worst). Sensitivity check: a 2 %
change of N moves Vj by ~14 mV and the current by >1 % in these circuits, i.e. 100x the tolerance
(test_diode_tolerance_detects_model_change).
"""
import math
import pytest
from app.model import Document, led_level, I_OFF
from app.components import LED_MODEL
from app.sim import run_document

VT = 1.380649e-23 * 300.15 / 1.602176634e-19     # 25.865 mV
GMIN = 1e-12


def shockley(vs, r, Is, n, rs, gmin=GMIN):
    """-> (I, Vj) for Vs - R - diode(Is, N, Rs) - ground, solved by bisection on the junction voltage."""
    f = lambda vj: vs - vj - (Is * math.expm1(vj / (n * VT)) + gmin * vj) * (r + rs)   # decreasing in vj
    lo, hi = min(vs, 0.0) - 1, max(vs, 0.0) + 1
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) > 0 else (lo, mid)
    vj = (lo + hi) / 2
    return Is * math.expm1(vj / (n * VT)) + gmin * vj, vj


def diode_doc(vs, r, Is="1e-14", n="1", rs="0", kind="D"):
    d = Document()
    d.add_part("V", dc=vs); d.add_part("R", R=r)
    d.add_part(kind, **({"Is": Is, "N": n, "Rs": rs} if kind == "D" else {}))
    d.add_part("GND")
    for a, b in (("V1.p", "R1.a"), ("R1.b", "D1.a"), ("D1.k", "GND1.g"), ("V1.n", "GND1.g")): d.connect(a, b)
    d.tstop, d.tstep = "100u", "10u"
    return d


def final(res, name):
    return res.stats(name)["final"]


@pytest.mark.parametrize("vs,r,Is,n,rs", [
    ("5", "1k", "1e-14", "1", "0"),            # catalog default diode
    ("5", "1k", "2.52n", "1.752", "0.568"),    # 1N4148-like card
    ("1", "100", "1p", "1.5", "2"),            # Rs matters: 3.4 mV of the 0.83 V drop
    ("12", "470", "10f", "1.2", "0.5"),
])
def test_diode_forward_matches_shockley(vs, r, Is, n, rs):
    d = diode_doc(vs, r, Is, n, rs)
    nl = d.netlist()
    # the oracle uses the numbers actually sent to the core, read back from the emitted card
    card = nl["models"][0]
    vals = dict(kv.split("=") for kv in card[card.index("(") + 1:-1].split())
    Is_, n_, rs_ = (float(vals[k]) for k in ("Is", "N", "Rs"))
    vsf, rf = d.check_param("V1", "dc"), d.check_param("R1", "R")
    i_exp, vj = shockley(vsf, rf, Is_, n_, rs_)

    res = run_document(d)
    assert res.ok, res.message
    i, vd = final(res, "I(D1)"), final(res, "V(D1)")
    assert i == pytest.approx(i_exp, rel=1e-4)
    assert vd == pytest.approx(vj + i_exp * rs_, abs=1e-5)          # terminal drop = junction + I Rs
    assert final(res, "I(R1)") == pytest.approx(i, rel=1e-9)         # series loop: same current (KCL)
    assert vd + final(res, "V(R1)") == pytest.approx(vsf, abs=1e-9)  # KVL


@pytest.mark.parametrize("Is", ["1e-14", "1e-9"])
def test_diode_reverse_leakage_and_blocking(Is):
    """Reverse biased by 5 V: I = -Is - GMIN*5. With Is = 1e-14 the GMIN shunt (5 pA) dominates, so the
    reported current is ngspice's convergence conductance, not physics; Is = 1e-9 A shows -Is directly."""
    res = run_document(diode_doc("-5", "1k", Is))
    assert res.ok, res.message
    i, _ = shockley(-5.0, 1e3, float(Is), 1.0, 0.0)
    assert i == pytest.approx(-float(Is) - GMIN * 5, rel=1e-6)       # oracle sanity
    assert final(res, "I(D1)") == pytest.approx(i, rel=1e-4)
    assert final(res, "V(D1)") == pytest.approx(-5.0, abs=1e-5)       # all of the supply across the diode
    assert abs(final(res, "I(D1)")) < 1e-8                            # blocks: < 10 nA vs 4.3 mA forward


def test_diode_tolerance_detects_model_change():
    """Simulate with N=1.02 / Is doubled while the oracle keeps the nominal card: the error must be far above 1e-4."""
    i_nom, _ = shockley(5.0, 1e3, 1e-14, 1.0, 0.0)
    for Is, n in (("1e-14", "1.02"), ("2e-14", "1")):
        i = final(run_document(diode_doc("5", "1k", Is, n)), "I(D1)")
        assert abs(i / i_nom - 1) > 30 * 1e-4, (Is, n, i, i_nom)


# ---------------------------------------------------------------- LED (existing example, real app path)
LED = dict(zip(("Is", "N", "Rs"), (1e-20, 1.8, 5.0)))


def test_led_card_is_what_the_oracle_assumes():
    assert LED_MODEL == ".model dled D(Is=1e-20 N=1.8 Rs=5)"
    assert Document.led_example().netlist()["models"] == [LED_MODEL]


def test_led_example_current_is_self_consistent():
    """Document.led_example (5 V battery, 330 ohm, red LED): I = (Vs - Vf)/R with Vf solved from the emitted card."""
    d = Document.led_example(); d.tstop, d.tstep = "1m", "10u"
    res = run_document(d)
    assert res.ok, res.message
    i_exp, vj = shockley(5.0, 330.0, LED["Is"], LED["N"], LED["Rs"])
    i, vf = final(res, "I(D1)"), final(res, "V(D1)")
    assert i == pytest.approx(i_exp, rel=1e-4)                       # 9.177 mA
    assert vf == pytest.approx(vj + i_exp * LED["Rs"], abs=1e-5)     # 1.9715 V
    assert i == pytest.approx((5.0 - vf) / 330.0, rel=1e-6)           # (Vs - Vf)/R on the core's own Vf
    assert led_level(i) > 0.5                                         # visibly lit


def test_led_lit_threshold():
    """The GUI's 'lit' threshold is I_OFF = 0.1 mA. The supply voltage giving exactly I_OFF is
    Vs* = I_OFF (R + Rs) + N Vt ln(1 + I_OFF/Is) = 1.7486 V for R = 330. 3 % below: dark; 3 % above: lit.
    3 % is ~50 mV, i.e. ~2x the LED's 46.6 mV/decade slope, so the margin is a real I-V statement, not noise."""
    vstar = I_OFF * 335 + LED["N"] * VT * math.log1p(I_OFF / LED["Is"])
    assert shockley(vstar, 330, LED["Is"], LED["N"], LED["Rs"])[0] == pytest.approx(I_OFF, rel=1e-6)
    for k, lit in ((0.97, False), (1.03, True)):
        d = Document.led_example(); d.set_param("V1", "dc", repr(vstar * k)); d.tstop, d.tstep = "1m", "10u"
        res = run_document(d)
        assert res.ok, res.message
        i = final(res, "I(D1)")
        assert i == pytest.approx(shockley(vstar * k, 330, LED["Is"], LED["N"], LED["Rs"])[0], rel=1e-4)
        assert (led_level(i) > 0) is lit, (k, i)
