"""Save -> reload -> simulate through the REAL path. A reloaded document must produce bit-identical waveforms to
the original (same netlist text -> same deterministic ngspice run), and still match the analytic answer.
Exact equality is the right tolerance: the file stores parameters as the typed text, so nothing is rounded."""
import json
import math
import pytest
from app.model import Document, ModelError
from app.sim import run_document


def rlc_diode_doc():
    """Underdamped series RLC (10 ohm, 1 mH, 1 uF) plus a diode/LED branch, rotated parts, SI-text values."""
    d = Document()
    d.add_part("V", 40, 100, dc="5 V"); d.add_part("R", 160, 60, rot=90, R="10Ω"); d.add_part("L", 280, 60, L="1m")
    d.add_part("C", 400, 100, rot=270, C="1µ"); d.add_part("R", 160, 200, R="1k")
    d.add_part("D", 280, 200, Is="2.52n", N="1.752", Rs="0.568"); d.add_part("GND", 440, 260)
    for a, b in (("V1.p", "R1.a"), ("R1.b", "L1.a"), ("L1.b", "C1.a"), ("C1.b", "GND1.g"), ("V1.n", "GND1.g"),
                 ("V1.p", "R2.a"), ("R2.b", "D1.a"), ("D1.k", "GND1.g")):
        d.connect(a, b)
    d.tstop, d.tstep = "2m", "1u"
    return d


def vc_under(t, R=10.0, L=1e-3, C=1e-6, V=5.0):
    a = R / (2 * L); wd = math.sqrt(1 / (L * C) - a * a)
    return V * (1 - math.exp(-a * t) * (math.cos(wd * t) + a / wd * math.sin(wd * t)))


def test_save_reload_simulates_identically_and_analytically(tmp_path):
    d = rlc_diode_doc(); p = tmp_path / "rlc.json"
    a = run_document(d)
    d.save(p)
    d2 = Document.load(p)                       # fresh document, nothing shared with d
    assert d2 is not d and d2.netlist() == d.netlist()
    b = run_document(d2)
    assert a.ok and b.ok, (a.message, b.message)
    assert a.series.keys() == b.series.keys() and a.series == b.series          # bit-for-bit, every vector
    assert [s["name"] for s in a.signals] == [s["name"] for s in b.signals]
    # and the reloaded circuit is still the right physics (3 mV: see test_analytic_rlc)
    assert max(abs(v - vc_under(t)) for t, v in b.get("V(C1)")) < 3e-3
    assert b.stats("I(D1)")["final"] == pytest.approx(4.3468e-3, rel=1e-3)     # 1N4148-like card at 5 V / 1k
    # layout + typed text survive too
    assert d2.parts["R1"]["rot"] == 90 and d2.parts["C1"]["params"]["C"] == "1µ" and d2.tstop == "2m"


def test_reload_then_edit_then_resave(tmp_path):
    """Continue editing after reload: change C, save again, reload: the new tau shows in the simulation."""
    p = tmp_path / "rlc.json"
    rlc_diode_doc().save(p)
    d = Document.load(p); d.set_param("C1", "C", "4u"); d.save()
    r = run_document(Document.load(p))
    assert r.ok, r.message
    assert max(abs(v - vc_under(t, C=4e-6)) for t, v in r.get("V(C1)")) < 3e-3


def test_old_unversioned_file_loads_and_matches_v1(tmp_path):
    """A v0 file (pre-versioning layout) of an RC circuit simulates exactly like the same circuit built today."""
    v0 = {"parts": {"V1": {"kind": "V", "x": 40, "y": 100, "params": {"mode": "DC", "dc": "5"}},
                    "R1": {"kind": "R", "x": 180, "y": 60, "params": {"R": "1000"}},
                    "C1": {"kind": "C", "x": 320, "y": 100, "params": {"C": "1e-6"}},
                    "GND1": {"kind": "GND", "x": 440, "y": 220, "params": {}}},
          "wires": [["V1.p", "R1.a"], ["R1.b", "C1.a"], ["C1.b", "GND1.g"], ["V1.n", "GND1.g"]],
          "tstop": "5e-3", "tstep": "1e-5"}
    p = tmp_path / "old.json"; p.write_text(json.dumps(v0))
    old = run_document(Document.load(p))
    d = Document()
    d.add_part("V", dc="5"); d.add_part("R", R="1000"); d.add_part("C", C="1e-6"); d.add_part("GND")
    for a, b in v0["wires"]: d.connect(a, b)
    d.tstop, d.tstep = "5e-3", "1e-5"
    new = run_document(d)
    assert old.ok and new.ok and old.series == new.series
    s = old.get("V(C1)")
    assert max(abs(v - 5 * (1 - math.exp(-t / 1e-3))) for t, v in s) < 1e-3


@pytest.mark.parametrize("content", [b'{"format": "sim-circuit", "version": 1, "compo', b"\x89PNG\r\n\x1a\n\x00\xff",
                                     b""])
def test_corrupt_file_errors_clearly(tmp_path, content):
    p = tmp_path / "bad.json"; p.write_bytes(content)
    with pytest.raises(ModelError, match=r"cannot open .*bad\.json"):
        Document.load(p)


def test_bad_value_in_file_fails_at_run_with_named_param(tmp_path):
    """A hand-edited file with an unparsable value loads (text is stored as typed) but the run fails cleanly,
    naming the part and parameter, before anything reaches the core."""
    t = rlc_diode_doc().to_dict(); t["components"]["C1"]["params"]["C"] = "lots"
    p = tmp_path / "edited.json"; p.write_text(json.dumps(t))
    r = run_document(Document.load(p))
    assert not r.ok and r.message.startswith("[BAD-PARAM]") and "C1 C" in r.message
