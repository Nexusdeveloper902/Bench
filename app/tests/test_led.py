"""LED + resistor + battery through the REAL core. Kirchhoff checks use the series' own values."""
import pytest
from app.model import Document
from app.sim import run_document


def led(v="5", r="330", rev=False, float_led=False):
    d = Document()
    d.add_part("BAT", dc=v); d.add_part("R", R=r); d.add_part("LED"); d.add_part("GND")
    a, k = ("k", "a") if rev else ("a", "k")
    wires = [("V1.p", "R1.a"), ("V1.n", "GND1.g"), ("R1.b", f"D1.{a}")]
    if not float_led:
        wires.append((f"D1.{k}", "GND1.g"))
    for w in wires: d.connect(*w)
    d.tstop, d.tstep = "1e-3", "1e-5"
    return d


def fin(res, n): return res.series[n][-1][1]


@pytest.mark.parametrize("v,r,lo,hi", [("5", "330", 5e-3, 15e-3), ("9", "470", 5e-3, 20e-3)])
def test_led_circuit_kvl(v, r, lo, hi):
    res = run_document(led(v, r))
    assert res.ok, res.message
    vb, vr, vl, i = float(v), fin(res, "v(R1)"), fin(res, "v(D1)"), fin(res, "@d1[id]")
    assert 1.7 < vl < 2.2 and lo < i < hi
    assert fin(res, "v(V1)") == pytest.approx(vb, abs=1e-6)
    assert vb == pytest.approx(vr + vl, abs=1e-6)               # KVL
    assert i * float(r) == pytest.approx(vr, rel=1e-4)          # Ohm on the core's own values
    assert i == pytest.approx((vb - vl) / float(r), rel=1e-4)
    assert fin(res, "@r1[i]") == pytest.approx(i, rel=1e-4)
    assert res.stats("@d1[id]")["max"] >= i - 1e-12


def test_reverse_led_blocks():
    res = run_document(led(rev=True))
    assert res.ok, res.message
    assert abs(fin(res, "@d1[id]")) < 1e-9
    assert fin(res, "v(D1)") == pytest.approx(-5.0, abs=1e-3)


def test_derived_voltages_are_subtractions():
    d = led(); res = run_document(d)
    nets = d.nets()
    n = lambda pin: res.series[f"v({nets[pin]})"] if nets[pin] != "0" else None
    for name, a, b in (("v(R1)", "R1.a", "R1.b"), ("v(D1)", "D1.a", "D1.k"), ("v(V1)", "V1.p", "V1.n")):
        sa, sb = n(a), n(b)
        for j, (t, v) in enumerate(res.series[name]):
            exp = (sa[j][1] if sa else 0.0) - (sb[j][1] if sb else 0.0)
            assert v == pytest.approx(exp, abs=1e-12)
    assert {"v(R1)", "v(D1)", "v(V1)"} <= set(res.observables)


def test_led_save_load_roundtrip(tmp_path):
    d = led(); p = tmp_path / "led.json"; d.save(p)
    d2 = Document.load(p)
    assert d2.parts == d.parts and d2.netlist() == d.netlist()
    nl = d2.netlist()
    assert nl["models"] == [".model dled D(Is=1e-20 N=1.8 Rs=5)"]
    assert "V1 n1 0 DC 5.0" in nl["lines"]
    assert "D1 n2 0 dled" in nl["lines"]
    assert d2.add_part("LED") == "D2" and d2.add_part("BAT") == "V2"


def test_led_and_plain_diode_coexist():
    d = led(); d.add_part("D", 0, 0)   # D2 (plain diode) must not collide with LED's D1
    assert list(d.parts) == ["V1", "R1", "D1", "GND1", "D2"]
    assert sorted(d.netlist()["models"]) == sorted([".model dled D(Is=1e-20 N=1.8 Rs=5)",
                                                 ".model dmod_d2 D(Is=1e-14 N=1.0 Rs=0.0)"])   # ngspice defaults


def test_floating_led_fails_cleanly():
    d = led(float_led=True)
    d.disconnect("D1.a")                       # LED fully unconnected: core must reject
    res = run_document(d)
    assert not res.ok and res.status == "failed" and res.message and not res.series


def test_dangling_cathode_is_flagged():
    res = run_document(led(float_led=True))    # core solves it but reports an open end
    assert res.ok and any("n3" in f["message"] and "D1" in f["devices"] for f in res.findings)
