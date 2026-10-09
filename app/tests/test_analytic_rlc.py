"""Second-order and inductor dynamics through the REAL path: Document -> app.sim.run_document -> simcore ->
libngspice, compared with closed-form circuit theory. No mocks.

Circuits are the step response of a 5 V DC source switched on at t=0 (the core always runs `.tran ... uic`, so all
capacitor voltages / inductor currents start at their IC=0 and the source is 5 V from the first step).

Numerics behind the tolerances (ngspice defaults: trapezoidal integration, reltol 1e-3, vntol 1 uV, abstol 1 pA;
the app sets tmax = tstep, so the solver step is <= tstep):
- RLC with tstep = 1 us = T_d/200 (underdamped) and <= tau_fast/5 (overdamped). Measured max |Vc - analytic| over
  the whole run: 0.96 mV underdamped (on an 8.0 V peak), 0.11 mV overdamped. Tolerances 3 mV / 0.5 mV keep ~3-5x
  margin, yet a 2 % change of R, L or C moves the waveform by 37-119 mV (see the discrimination tests below).
- RL with tstep = tau/100: measured max |i - analytic| = 0.15 uA on 50 mA (3e-6 relative); tolerance 2 uA.
- LC energy: trapezoidal integration is energy-preserving for the lossless LC; measured drift 4.3e-6 relative over
  ~5 periods; tolerance 1e-4. Its phase, however, drifts (frequency warping), see the test.
"""
import math
import pytest
from app.model import Document, value_at
from app.sim import run_document

V = 5.0


def build(parts, wires, tstop, tstep):
    d = Document()
    for kind, params in parts: d.add_part(kind, **params)
    for a, b in wires: d.connect(a, b)
    d.tstop, d.tstep = tstop, tstep
    return d


def run(d):
    res = run_document(d)
    assert res.ok, res.message + "\n" + res.details
    return res


# ---------------------------------------------------------------- series RLC
def rlc(R, L, C, tstop="3m", tstep="1u"):
    return build([("V", {"dc": "5"}), ("R", {"R": R}), ("L", {"L": L}), ("C", {"C": C}), ("GND", {})],
                 [("V1.p", "R1.a"), ("R1.b", "L1.a"), ("L1.b", "C1.a"), ("C1.b", "GND1.g"), ("V1.n", "GND1.g")],
                 tstop, tstep)


def vc_rlc(t, R, L, C):
    """Closed-form capacitor voltage of a series RLC driven by a V step from rest."""
    a, w0 = R / (2 * L), 1 / math.sqrt(L * C)
    if a < w0:
        wd = math.sqrt(w0 * w0 - a * a)
        return V * (1 - math.exp(-a * t) * (math.cos(wd * t) + a / wd * math.sin(wd * t)))
    r = math.sqrt(a * a - w0 * w0); s1, s2 = -a + r, -a - r
    return V * (1 - (s2 * math.exp(s1 * t) - s1 * math.exp(s2 * t)) / (s2 - s1))


def i_rlc_under(t, R, L, C):
    a = R / (2 * L); wd = math.sqrt(1 / (L * C) - a * a)
    return V / (L * wd) * math.exp(-a * t) * math.sin(wd * t)


def max_err(series, f):
    return max(abs(v - f(t)) for t, v in series)


UNDER = (10.0, 1e-3, 1e-6)    # alpha = 5000/s, w0 = 31623 rad/s, zeta = 0.158, T_d = 201 us
OVER = (200.0, 1e-3, 1e-6)    # alpha = 1e5/s: poles -5132/s and -194868/s, zeta = 3.16


def test_rlc_underdamped_matches_closed_form():
    R, L, C = UNDER
    res = run(rlc("10", "1m", "1u"))
    vc, i = res.get("V(C1)"), res.get("I(L1)")
    a = R / (2 * L); wd = math.sqrt(1 / (L * C) - a * a)

    # whole waveform vs closed form (3 mV, see module doc)
    assert max_err(vc, lambda t: vc_rlc(t, *UNDER)) < 3e-3
    # loop current i = V/(L wd) e^{-at} sin(wd t), 0.16 A peak: measured max error 31 uA -> tolerance 0.3 mA
    assert max_err(i, lambda t: i_rlc_under(t, *UNDER)) < 3e-4

    # first peak: t_p = pi/wd, Vp = V(1 + exp(-alpha pi/wd)). Peak is taken on the solver grid (step <= 1 us),
    # so time is good to one step; value error ~ curvature * step^2 << 2 mV.
    tp, vp = max(vc, key=lambda p: p[1])
    assert tp == pytest.approx(math.pi / wd, abs=1e-6)
    assert vp == pytest.approx(V * (1 + math.exp(-a * math.pi / wd)), abs=2e-3)
    assert (vp - V) / V == pytest.approx(0.6047, abs=1e-3)              # 60.5 % overshoot for zeta = 0.158

    # damped frequency from interpolated zero crossings of (Vc - V): half period = pi/wd.
    # Linear interpolation over a 1 us step: crossing error << 0.1 us; 5 half periods averaged -> 0.1 % tolerance.
    xs = [t0 + (t1 - t0) * (V - v0) / (v1 - v0) for (t0, v0), (t1, v1) in zip(vc, vc[1:]) if (v0 - V) * (v1 - V) < 0]
    assert len(xs) >= 6
    half = (xs[5] - xs[0]) / 5
    assert half == pytest.approx(math.pi / wd, rel=1e-3)

    # decay envelope: successive extrema of (Vc - V) shrink by exp(-alpha pi/wd) = 0.605 per half period
    ext = [max(((t, v) for t, v in vc if lo < t < hi), key=lambda p: abs(p[1] - V)) for lo, hi in zip(xs, xs[1:])]
    # (extrema are exactly pi/wd apart: dVc/dt ~ i ~ e^{-at} sin(wd t); sampled values are off by ~1e-4 rel)
    for (_, v0), (_, v1) in zip(ext[:4], ext[1:5]):
        assert abs(v1 - V) / abs(v0 - V) == pytest.approx(math.exp(-a * math.pi / wd), rel=2e-3)

    # 2 % settling: last time |Vc - V| > 0.1 V, simulated vs the closed form on the same grid (within 2 steps);
    # also bounded by the envelope V*sqrt(1+(a/wd)^2)*exp(-a t) = 0.1 V -> t = 0.785 ms
    last = lambda f: max(t for t, v in vc if abs(f(t, v) - V) > 0.02 * V)
    ts_sim, ts_ana = last(lambda t, v: v), last(lambda t, v: vc_rlc(t, *UNDER))
    assert ts_sim == pytest.approx(ts_ana, abs=2e-6)
    assert ts_sim <= math.log(V * math.sqrt(1 + (a / wd) ** 2) / (0.02 * V)) / a

    # limits: final value V (envelope at 3 ms = 1.5 uV), current -> 0, current starts from zero:
    # first point is t1 = 10 ns where i = V t1 / L exactly to first order (inductor blocks the step)
    assert vc[-1][1] == pytest.approx(V, abs=1e-4) and abs(i[-1][1]) < 1e-4
    t1, i1 = i[0]
    assert t1 < 1e-7 and i1 == pytest.approx(V * t1 / L, rel=1e-2)
    assert vc[0][1] == pytest.approx(0, abs=1e-6)


def test_rlc_overdamped_matches_closed_form():
    R, L, C = OVER
    res = run(rlc("200", "1m", "1u"))
    vc, i = res.get("V(C1)"), res.get("I(L1)")
    assert max_err(vc, lambda t: vc_rlc(t, *OVER)) < 5e-4
    # overdamped: monotonic rise, never above the final value (no overshoot), current never reverses
    assert all(b[1] >= a[1] - 1e-9 for a, b in zip(vc, vc[1:]))
    assert max(v for _, v in vc) <= V + 1e-6
    assert min(x for _, x in i) >= -1e-9
    # current peak of the overdamped loop: t* = ln(s2/s1)/(s1-s2), i* = V/(L(s1-s2)) (e^{s1 t*} - e^{s2 t*})
    a = R / (2 * L); r = math.sqrt(a * a - 1 / (L * C)); s1, s2 = -a + r, -a - r
    ts = math.log(s2 / s1) / (s1 - s2)
    tpk, ipk = max(i, key=lambda p: p[1])
    assert tpk == pytest.approx(ts, abs=1e-6)                       # one solver step
    assert ipk == pytest.approx(V / (L * (s1 - s2)) * (math.exp(s1 * ts) - math.exp(s2 * ts)), rel=1e-3)
    assert vc[-1][1] == pytest.approx(V, abs=1e-3)                    # slow pole: 5*e^{-15.4} = 1 uV left at 3 ms
    t1, i1 = i[0]
    assert i1 == pytest.approx(V * t1 / L, rel=1e-2)


# ---------------------------------------------------------------- inductor
def rl(L="10m"):
    return build([("V", {"dc": "5"}), ("R", {"R": "100"}), ("L", {"L": L}), ("GND", {})],
                 [("V1.p", "R1.a"), ("R1.b", "L1.a"), ("L1.b", "GND1.g"), ("V1.n", "GND1.g")], "600u", "1u")


def test_rl_current_rise():
    res = run(rl())
    i = res.get("I(L1)")
    tau = 10e-3 / 100
    f = lambda t: V / 100 * (1 - math.exp(-t / tau))
    assert max_err(i, f) < 2e-6
    assert value_at(i, tau) / (V / 100) == pytest.approx(1 - math.exp(-1), abs=1e-4)
    assert i[-1][1] == pytest.approx(0.05 * (1 - math.exp(-6)), rel=1e-4)   # 6 tau: 0.25 % short of V/R
    vl = res.get("V(L1)")
    assert max_err(vl, lambda t: V * math.exp(-t / tau)) < 1e-3     # L di/dt = V e^{-t/tau}


def test_lc_energy_is_conserved():
    """C charged to 5 V (IC) discharging into L: (C v^2 + L i^2)/2 stays at C V^2/2; amplitude and period correct."""
    d = build([("C", {"C": "1u", "ic": "5"}), ("L", {"L": "1m"}), ("GND", {})],
              [("C1.a", "L1.a"), ("C1.b", "GND1.g"), ("L1.b", "GND1.g")], "1m", "1u")
    res = run(d)
    v, i = res.get("V(C1)"), res.get("I(L1)")
    e0 = 0.5 * 1e-6 * V ** 2
    for (t, x), (_, y) in zip(v, i):
        assert 0.5 * 1e-6 * x * x + 0.5 * 1e-3 * y * y == pytest.approx(e0, rel=1e-4), t
    assert max(abs(y) for _, y in i) == pytest.approx(V * math.sqrt(1e-6 / 1e-3), rel=1e-3)   # I = V sqrt(C/L)
    # waveform: trapezoidal integration keeps amplitude but warps frequency by ~(w0 h)^2/12, a phase lag growing
    # as w0^3 h^2 t / 12 = 2.6 mrad at 1 ms -> ~13 mV on 5 V (measured 12.4 mV). 25 mV tolerance; a 2 % L or C error
    # shifts the phase by ~1 % * w0 t = 0.3 rad -> > 1 V.
    w0 = 1 / math.sqrt(1e-9)
    assert max_err(v, lambda t: V * math.cos(w0 * t)) < 25e-3


# ---------------------------------------------------------------- discrimination (mutation sanity)
# The checks above must FAIL if the simulated circuit differs from the oracle by a few percent. Here the document
# is perturbed by 2 % while the oracle keeps the nominal values: the error must exceed the tolerance by a wide
# margin (measured: R +2 % -> 38 mV, L +2 % -> 115 mV, C +2 % -> 119 mV underdamped; C +2 % -> 37 mV overdamped; RC R +2 % -> ~36 mV;
# RL L +2 % -> 364 uA). If these ever pass the tolerance, the tolerances above have become too loose to mean anything.
@pytest.mark.parametrize("R,L,C,nominal,tol", [
    ("10.2", "1m", "1u", UNDER, 3e-3),
    ("10", "1.02m", "1u", UNDER, 3e-3),
    ("10", "1m", "1.02u", UNDER, 3e-3),
    ("200", "1m", "1.02u", OVER, 5e-4),
    ("204", "1m", "1u", OVER, 5e-4),
])
def test_rlc_tolerance_detects_2_percent_error(R, L, C, nominal, tol):
    err = max_err(run(rlc(R, L, C)).get("V(C1)"), lambda t: vc_rlc(t, *nominal))
    assert err > 5 * tol, err


def test_rc_tolerance_detects_2_percent_error():
    """Same idea for the RC charge check in test_core_analytic (1 mV at 0.1..5 tau)."""
    tau = 1e-3
    for r, must_fail in (("1k", False), ("1.02k", True), ("980", True)):
        d = build([("V", {"dc": "5"}), ("R", {"R": r}), ("C", {"C": "1u"}), ("GND", {})],
                  [("V1.p", "R1.a"), ("R1.b", "C1.a"), ("C1.b", "GND1.g"), ("V1.n", "GND1.g")], "6m", "10u")
        s = run(d).get("V(C1)")
        err = max(abs(value_at(s, k * tau) - V * (1 - math.exp(-k))) for k in (0.1, 0.5, 1, 2, 3, 5))
        assert (err > 10 * 1e-3) if must_fail else (err < 1e-3), (r, err)


def test_rl_tolerance_detects_2_percent_error():
    i = run(rl("10.2m")).get("I(L1)")
    assert max_err(i, lambda t: V / 100 * (1 - math.exp(-t / 1e-4))) > 50 * 2e-6
