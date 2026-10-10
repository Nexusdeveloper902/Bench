"""Real-engine analytic checks: Document (catalog, SI input) -> app.sim -> benchcore -> libngspice -> series,
compared with closed-form circuit theory. No mocks.

Tolerances:
- Linear resistive DC (divider, switch): ngspice solves the linear MNA system to ~machine precision at every
  transient point (abstol 1e-12 A, vntol 1 uV); 1e-6 V / rel 1e-6 is far above that and far below any wiring or
  value mistake (a wrong resistor shifts the result by >= percent level).
- RC transient: trapezoidal integration with tmax = tstep = tau/100 gives local errors ~1e-5 V; 1 mV on a 5 V step
  (0.02 %) is the existing app tolerance and still fails for a 0.1 % error in R or C.
"""
import math
import pytest
from app.model import Document
from app.sim import run_document


def wire(d, *pairs):
    for a, b in pairs: d.connect(a, b)


def at(series, t):
    for (t0, v0), (t1, v1) in zip(series, series[1:]):
        if t0 <= t <= t1:
            return v1 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    raise AssertionError(f"t={t} outside series")


@pytest.mark.parametrize("vin,r1,r2", [("12", "1k", "3k"), ("5 V", "10k", "4.7kΩ"), ("3.3", "1meg", "2.2M")])
def test_voltage_divider(vin, r1, r2):
    d = Document()
    d.add_part("V", dc=vin); d.add_part("R", R=r1); d.add_part("R", R=r2); d.add_part("GND")
    wire(d, ("V1.p", "R1.a"), ("R1.b", "R2.a"), ("R2.b", "GND1.g"), ("V1.n", "GND1.g"))
    d.tstop, d.tstep = "1m", "100u"
    res = run_document(d)
    assert res.ok, res.message
    v, a, b = (d.check_param(n, k) for n, k in (("V1", "dc"), ("R1", "R"), ("R2", "R")))
    vout = v * b / (a + b)
    assert res.stats("V(R2)")["final"] == pytest.approx(vout, abs=1e-6)
    assert res.stats("V(R1)")["final"] == pytest.approx(v - vout, abs=1e-6)
    assert res.stats("I(R1)")["final"] == pytest.approx(v / (a + b), rel=1e-6)
    # every point, not just the last: a DC circuit must be flat
    assert max(abs(x - vout) for _, x in res.get("V(R2)")) < 1e-6


@pytest.mark.parametrize("r,c,tau", [("1k", "1u", 1e-3), ("2.2k", "470n", 2.2e3 * 470e-9)])
def test_rc_charge_matches_exponential(r, c, tau):
    vf = 5.0
    d = Document()
    d.add_part("V", dc="5"); d.add_part("R", R=r); d.add_part("C", C=c); d.add_part("GND")
    wire(d, ("V1.p", "R1.a"), ("R1.b", "C1.a"), ("C1.b", "GND1.g"), ("V1.n", "GND1.g"))
    d.tstop, d.tstep = repr(6 * tau), repr(tau / 100)
    res = run_document(d)
    assert res.ok, res.message
    s = res.get("V(C1)")                                    # readable name -> derived across-part voltage
    for k in (0.1, 0.5, 1, 2, 3, 5):
        assert at(s, k * tau) == pytest.approx(vf * (1 - math.exp(-k)), abs=1e-3), k
    assert at(s, tau) / vf == pytest.approx(1 - math.exp(-1), abs=2e-4)
    # capacitor current = (Vf - Vc)/R at the same instants
    i = res.get("I(C1)"); rv = d.check_param("R1", "R")
    for k in (0.5, 1, 2):
        assert at(i, k * tau) == pytest.approx(vf * math.exp(-k) / rv, abs=1e-3 / rv), k


def switch_circuit(state):
    d = Document()
    d.add_part("V", dc="5"); d.add_part("SW", state=state); d.add_part("R", R="1k"); d.add_part("GND")
    wire(d, ("V1.p", "SW1.a"), ("SW1.b", "R1.a"), ("R1.b", "GND1.g"), ("V1.n", "GND1.g"))
    d.tstop, d.tstep = "1m", "100u"
    return d


def test_switch_closed_and_open():
    closed, opened = run_document(switch_circuit("closed")), run_document(switch_circuit("open"))
    assert closed.ok and opened.ok, (closed.message, opened.message)
    ron, roff, r = 0.01, 1e9, 1e3                            # catalog defaults
    assert closed.stats("I(R1)")["final"] == pytest.approx(5 / (r + ron), rel=1e-6)
    assert closed.stats("V(SW1)")["final"] == pytest.approx(5 * ron / (r + ron), rel=1e-6)
    assert opened.stats("I(SW1)")["final"] == pytest.approx(5 / (r + roff), rel=1e-6)
    assert opened.stats("V(R1)")["final"] == pytest.approx(5 * r / (r + roff), rel=1e-6)
    assert not any(f["code"] == "NO-DC-PATH" for f in opened.findings)   # resistor-backed: no false error


def test_switch_toggle_rerun_is_deterministic():
    d = switch_circuit("open")
    a = run_document(d)
    d.set_param("SW1", "state", "closed"); b = run_document(d)
    d.set_param("SW1", "state", "open"); c = run_document(d)
    assert a.ok and b.ok and c.ok
    assert a.series == c.series and a.series != b.series
