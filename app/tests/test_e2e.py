"""End-to-end tests against the REAL benchcore (no mocks)."""
import math
import pytest
from app.model import Document
from app.sim import run_document

VS, R, C = 5.0, 1000.0, 1e-6
TAU = R * C


def rc(r="1000", c="1e-6", ic="0", dc="5", tstop="5e-3"):
    d = Document()
    v, rr, cc, g = d.add_part("V", 0, 0), d.add_part("R", 1, 0), d.add_part("C", 2, 0), d.add_part("GND", 3, 0)
    for a, b in (("V1.p", "R1.a"), ("R1.b", "C1.a"), ("C1.b", "GND1.g"), ("V1.n", "GND1.g")):
        d.connect(a, b)
    d.set_param("V1", "dc", dc); d.set_param("R1", "R", r)
    d.set_param("C1", "C", c); d.set_param("C1", "ic", ic)
    d.tstop = tstop
    return d


def at(series, t):
    """Linear interpolation of [(t, v)]."""
    for (t0, v0), (t1, v1) in zip(series, series[1:]):
        if t0 <= t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0) if t1 > t0 else v1
    raise AssertionError(f"t={t} outside series")


def cap_node(doc):
    return f"v({doc.nets()['C1.a']})"


def check_charge(res, node, tau, vs=VS, tol=1e-3):
    s = res.series[node]
    for k in (0.1, 0.5, 1, 2, 3, 5):
        t = k * tau
        assert abs(at(s, t) - vs * (1 - math.exp(-k))) < tol, k
    assert abs(at(s, tau) / vs - 0.632) < 1e-3
    assert abs(res.stats(node)["final"] - vs * (1 - math.exp(-s[-1][0] / tau))) < tol


def test_rc_charge():
    d = rc()
    res = run_document(d)
    assert res.ok and res.status == "success", res.message
    check_charge(res, cap_node(d), TAU)
    assert abs(res.stats(cap_node(d))["final"] - 5.0) < 0.04   # 5 tau = 99.3%


def test_parameter_change_changes_tau():
    d = rc()
    check_charge(run_document(d), cap_node(d), TAU)
    d.set_param("R1", "R", "2000")
    d.tstop = "1e-2"
    res = run_document(d)
    assert res.ok, res.message
    check_charge(res, cap_node(d), 2 * TAU)
    # at the OLD tau the cap must now be well short of 63.2%
    assert at(res.series[cap_node(d)], TAU) < 5 * (1 - math.exp(-0.5)) + 1e-3


def test_initial_condition_discharge():
    # no source: V=0 so C discharges from ic through R
    d = rc(ic="2", dc="0")
    res = run_document(d)
    assert res.ok, res.message
    s = res.series[cap_node(d)]
    for k in (0.01, 0.5, 1, 3):
        assert abs(at(s, k * TAU) - 2 * math.exp(-k)) < 1e-3, k
    assert abs(at(s, TAU) / 2 - 0.368) < 1e-3


def test_ic_charge_towards_source():
    d = rc(ic="2")   # 2 V -> 5 V
    res = run_document(d)
    s = res.series[cap_node(d)]
    for k in (0.5, 1, 2):
        assert abs(at(s, k * TAU) - (5 + (2 - 5) * math.exp(-k))) < 1e-3


def test_resistive_divider_dc():
    d = Document()
    d.add_part("V"); d.add_part("R"); d.add_part("R"); d.add_part("GND")
    for a, b in (("V1.p", "R1.a"), ("R1.b", "R2.a"), ("R2.b", "GND1.g"), ("V1.n", "GND1.g")):
        d.connect(a, b)
    d.set_param("V1", "dc", "12"); d.set_param("R1", "R", "1000"); d.set_param("R2", "R", "3000")
    d.tstop, d.tstep = "1e-3", "1e-4"
    res = run_document(d)
    assert res.ok, res.message
    mid = f"v({d.nets()['R1.b']})"
    assert abs(res.stats(mid)["final"] - 9.0) < 1e-6
    assert abs(res.stats("@r1[i]")["final"] - 3e-3) < 1e-9


def test_save_load_roundtrip(tmp_path):
    d = rc()
    p = tmp_path / "x.json"
    d.save(p)
    d2 = Document.load(p)
    assert d2.to_dict() == d.to_dict()
    a, b = run_document(d), run_document(d2)
    assert a.ok and b.ok and a.series == b.series


def test_load_bad_file(tmp_path):
    from app.model import ModelError
    p = tmp_path / "bad.json"; p.write_text("{not json")
    with pytest.raises(ModelError):
        Document.load(p)


# ---------------- failure cases ----------------
def fail(d, *needles):
    res = run_document(d)
    assert res.ok is False and res.status == "blocked"   # pre-check refusals (plan A1); core errors are "failed"
    assert res.message and any(n in res.message for n in needles), res.message
    return res


def test_no_ground():
    d = rc()
    d.remove_part("GND1")
    fail(d, "NO-GROUND", "NO-DC-PATH")


def test_floating_pin():
    d = rc()
    d.disconnect("C1.b"); d.disconnect("V1.n")   # leaves both unconnected to ground
    d.connect("V1.n", "C1.b")
    res = run_document(d)
    assert not res.ok and res.message
    d = rc(); d.disconnect("R1.b")               # dangling: cap node loses its only connection to R
    res = run_document(d)
    assert not res.ok and res.message


def test_dangling_wire_part():
    d = rc(); d.add_part("R")   # R2 entirely unconnected
    res = run_document(d)
    assert not res.ok and res.message


@pytest.mark.parametrize("bad", ["0", "-5", "-1e-9"])
def test_bad_resistance(bad):
    fail(rc(r=bad), "R", "> 0")


@pytest.mark.parametrize("bad,needle", [("abc", "not a number"), ("1x", "not a number"),
                                        ("5 V", "does not fit"), ("", "not a number"), ("nan", "finite")])
def test_non_numeric(bad, needle):
    res = fail(rc(r=bad), needle)
    assert "R1" in res.message


@pytest.mark.parametrize("bad", ["0", "-1e-3", "abc"])
def test_bad_tstop(bad):
    fail(rc(tstop=bad), "tstop")


def test_tstep_gt_tstop():
    d = rc(); d.tstep = "1"
    fail(d, "tstep")


def test_nonexistent_observable_surfaces_error():
    import benchcore
    d = rc()
    s = benchcore.Simulation("t")
    nl = d.netlist()
    s.build_circuit(nl["lines"]); s.configure(**d.config())
    s.observe("v(nope)")
    with pytest.raises(benchcore.SimError) as e:
        s.run(); s.observable("v(nope)")
    assert "nope" in str(e.value)


# ---------------- GUI (offscreen) ----------------
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def build_window(qapp, r="1000", tstop="5e-3"):
    from app.window import MainWindow
    w = MainWindow()
    for k, x, y in (("V", 40, 100), ("R", 160, 60), ("C", 300, 140), ("GND", 200, 260)):
        w.place(k, x, y)
    for a, b in (("V1.p", "R1.a"), ("R1.b", "C1.a"), ("C1.b", "GND1.g"), ("V1.n", "GND1.g")):
        w.doc.connect(a, b)
    w.doc.set_param("V1", "dc", "5"); w.doc.set_param("R1", "R", r)
    w.doc.set_param("C1", "C", "1e-6"); w.doc.set_param("C1", "ic", "0")
    w.tstop.setText(tstop); w.tstop.editingFinished.emit()
    return w


def test_gui_workflow(qapp):
    w = build_window(qapp)
    w.run()
    assert w.status.text().startswith("SUCCESS"), w.status.text()
    assert w.result.ok
    node = cap_node(w.doc)
    shown = "V" + node[1:]                    # picker shows readable names: v(n2) -> V(n2)
    w.probe.setCurrentText(shown)
    assert w.probe.currentText() == shown
    final = w.result.stats(node)["final"]
    assert abs(final - 5 * (1 - math.exp(-5))) < 1e-3
    from app.components import format_si
    assert f"final {format_si(final, 'V', 6)}" in w.stats.text()
    check_charge(w.result, node, TAU)


def test_gui_edit_through_param_panel(qapp):
    """Select a part, type in the real QLineEdit, finish editing, rerun."""
    w = build_window(qapp, tstop="1e-2")
    w.select("R1")
    edit = next(iter(w.inspector.edits.values()))
    edit.setText("2000"); edit.editingFinished.emit()
    w.run()
    assert w.status.text().startswith("SUCCESS")
    check_charge(w.result, cap_node(w.doc), 2 * TAU)


@pytest.mark.parametrize("r,needle", [("abc", "not a number"), ("1x", "not a number"), ("-5", "> 0"), ("0", "> 0")])
def test_gui_bad_value_shows_failed(qapp, r, needle):
    w = build_window(qapp, r=r)
    w.run()
    assert w.status.text().startswith("BLOCKED") and needle in w.status.text()
    assert not w.result.ok and w.stats.text() == ""


def test_gui_failures(qapp):
    w = build_window(qapp)
    w.select("GND1"); w.delete_selected()
    w.run()
    assert w.status.text().startswith("BLOCKED") and ("NO-GROUND" in w.status.text() or "NO-DC-PATH" in w.status.text())
    w = build_window(qapp, tstop="0")
    w.run()
    assert w.status.text().startswith("BLOCKED") and "tstop" in w.status.text()
    # recovery: fix and rerun succeeds
    w.tstop.setText("5e-3"); w.run()
    assert w.status.text().startswith("SUCCESS")


def test_gui_save_load(qapp, tmp_path, monkeypatch):
    w = build_window(qapp)
    w.path = str(tmp_path / "s.json"); w.save()
    w2 = build_window(qapp); w2.new()
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (w.path, "")))
    w2.open()
    assert w2.doc.to_dict() == w.doc.to_dict()
    w.run(); w2.run()
    assert w.result.series == w2.result.series
