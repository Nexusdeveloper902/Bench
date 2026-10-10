"""Q_2N2222 (app/library/electrical/q_2n2222) through the REAL path: Document -> netlist -> app.sim -> benchcore -> ngspice.

Circuit: Vcc - Rb - base, Vcc - Rc - collector, emitter - ground, each on its own net so the pin ORDER in the
`Q.. c b e` line is actually checked (a test with every pin on ground cannot catch a swapped order).

Oracle numbers (Vcc 5, Rb 430k, Rc 1k, the emitted card: Bf=255.9, Vaf=74.03, Rb=10, Rc=1): ngspice gives
Ic = 1.61136 mA, Vc = 3.38864 V, Vb = 0.65744 V, Ib = 10.099 uA. The checks below do not trust those numbers alone:
KVL on Rc and Rb is recomputed from the simulated currents. Tolerance 1 % (measured KVL residual < 1e-6 relative; the
1 % covers ngspice-version differences of the BJT model, a wrong card or swapped pins is off by >50 %)."""
import pytest
from app.tests.test_gui_workflow import qapp, win   # noqa: F401  (fixtures)
from app.model import Document
from app.sim import run_document

CARD = (".model q2n2222 NPN(Is=14.34f Xti=3 Eg=1.11 Vaf=74.03 Bf=255.9 Ne=1.307 Ise=14.34f Ikf=.2847 Xtb=1.5 Br=6.092 "
        "Nc=2 Isc=0 Ikr=0 Rc=1 Cjc=7.306p Mjc=.3416 Vjc=.75 Fc=.5 Cje=22.01p Mje=.377 Vje=.75 Tr=46.91n Tf=411.1p "
        "Itf=.6 Vtf=1.7 Xtf=3 Rb=10)")


def ce_doc(rb="430k", rc="1k", vcc="5", base_to_ground=False, wire_base=True):
    d = Document()
    d.add_part("V", dc=vcc); d.add_part("R", R=rb); d.add_part("R", R=rc); d.add_part("Q_2N2222"); d.add_part("GND")
    wires = [("V1.p", "R1.a"), ("V1.p", "R2.a"), ("R2.b", "Q1.c"), ("Q1.e", "GND1.g"), ("V1.n", "GND1.g")]
    if base_to_ground: wires.append(("Q1.b", "GND1.g"))
    elif wire_base: wires.append(("R1.b", "Q1.b"))
    for a, b in wires: d.connect(a, b)
    d.tstop, d.tstep = "100u", "10u"
    return d


def final(res, name):
    return res.stats(name)["final"]


def test_golden_netlist_pin_order_model_and_save():
    nl = ce_doc().netlist()
    assert nl["lines"] == ["V1 n1 0 DC 5.0", "R1 n1 n2 430000.0", "R2 n1 n3 1000.0", "Q1 n3 n2 0 q2n2222"]
    assert nl["models"] == [CARD]
    assert ".save all @q1[ic]" in nl["saves"] and "@q1[ic]" in nl["observables"]
    d = ce_doc(); n = d.nets()                   # collector = Rc side, base = Rb side, emitter = ground, all distinct
    assert (n["Q1.c"], n["Q1.b"], n["Q1.e"]) == ("n3", "n2", "0") and len({n["Q1.c"], n["Q1.b"], n["Q1.e"]}) == 3


def test_common_emitter_operating_point_matches_kvl_and_oracle():
    d = ce_doc(); res = run_document(d)
    assert res.ok, res.message
    ic, ib = final(res, "I(Q1)"), final(res, "I(R1)")
    vc, vb = final(res, "V(n3)"), final(res, "V(n2)")
    assert vc == pytest.approx(3.3886, rel=1e-2) and ic == pytest.approx(1.6114e-3, rel=1e-2)
    assert vc == pytest.approx(5 - ic * 1e3, rel=1e-2)                  # KVL on Rc
    assert ib == pytest.approx((5 - vb) / 430e3, rel=1e-2)               # KVL on Rb
    assert 100 < ic / ib < 255.9 and 0.6 < vb < 0.72    # forward-active: beta 160 (< Bf: Ise recombination), Vbe ~ 0.66
    assert final(res, "I(R2)") == pytest.approx(ic, rel=1e-3)           # Rc carries the collector current (Ib is tiny)


def test_collector_current_signal_is_attributed_to_collector_pin():
    res = run_document(ce_doc())
    sig = next(s for s in res.signals if s["name"] == "I(Q1)")
    assert (sig["part"], sig["pins"], sig["vector"]) == ("Q1", ["Q1.c"], "@q1[ic]")
    assert final(res, "I(Q1)") > 0                                       # into the collector for NPN forward-active


def test_saturation_vce_well_below_0p3():
    res = run_document(ce_doc(rb="10k"))
    assert res.ok, res.message
    vce = final(res, "V(n3)")
    assert 0 < vce < 0.2 and final(res, "I(Q1)") == pytest.approx((5 - vce) / 1e3, rel=0.01)
    assert final(res, "I(Q1)") < 255.9 * final(res, "I(R1)")             # saturated: Ic < beta Ib


def test_cutoff_with_base_grounded():
    res = run_document(ce_doc(base_to_ground=True))
    assert res.ok, res.message
    assert abs(final(res, "I(Q1)")) < 1e-9 and final(res, "V(n3)") == pytest.approx(5.0, abs=1e-5)


def test_persistence_roundtrip_keeps_part_pins_wires(tmp_path):
    d = ce_doc(); p = tmp_path / "ce.json"; d.save(p)
    d2 = Document.load(p)
    assert d2.parts["Q1"]["kind"] == "Q_2N2222" and "placeholder" not in d2.parts["Q1"]
    assert d2.pins("Q1") == ["Q1.b", "Q1.c", "Q1.e"] and d2.wires == d.wires and d2.netlist() == d.netlist()
    assert run_document(d2).series == run_document(d).series


def test_load_without_catalog_entry_gives_placeholder_with_pins_bce(tmp_path, monkeypatch):
    p = tmp_path / "ce.json"; ce_doc().save(p)
    from app.catalog import CATALOG
    monkeypatch.setattr(CATALOG, "defs", {k: v for k, v in CATALOG.defs.items() if k != "Q_2N2222"})
    d = Document.load(p)
    ph = d.parts["Q1"]["placeholder"]
    assert ph["reason"] == "missing" and ph["pins"] == ["b", "c", "e"] and d.parts["Q1"]["kind"] == "Q_2N2222"
    assert d.wired("Q1")
    res = run_document(d)                                                # wired placeholder: blocked, never simulated
    assert not res.ok and "MISSING-DEFINITION" in res.message


def test_unwired_base_is_diagnosed_not_crashed():
    d = ce_doc(wire_base=False)
    diags = [x for x in d.validate() if x["part"] == "Q1"]
    assert [(x["code"], x["severity"], x["pins"]) for x in diags] == [("UNCONNECTED-PIN", "warning", ["Q1.b"])]
    assert diags[0]["message"] == "Q1.b is not connected."
    assert run_document(d).ok                                             # a floating base is a warning, not a block


def test_unwired_transistor_is_an_error_naming_the_part():
    d = Document(); d.add_part("V"); d.add_part("R"); d.add_part("Q_2N2222"); d.add_part("GND")
    for a, b in (("V1.p", "R1.a"), ("R1.b", "GND1.g"), ("V1.n", "GND1.g")): d.connect(a, b)
    res = run_document(d)
    assert not res.ok and "UNCONNECTED-PART" in res.message and "Q1 is not connected to anything" in res.message


def test_wrong_wiring_all_pins_on_one_net_is_clear_not_a_crash():
    d = ce_doc()
    d.wires = [w for w in d.wires if "Q1.c" not in w and "Q1.e" not in w]
    d.connect("Q1.c", "R2.b"); d.connect("Q1.e", "Q1.c"); d.connect("Q1.b", "Q1.c")   # shorted C-B-E, off ground
    res = run_document(d)
    assert isinstance(res.ok, bool) and res.message and "Traceback" not in res.message
    if not res.ok: assert res.message.startswith("[")                    # coded message, not a raw exception


# ---------------------------------------------------------------- GUI: place, wire the three pins, run
def test_gui_place_wire_and_run_common_emitter(win):
    from PySide6.QtWidgets import QApplication
    from app.tests.test_gui_workflow import place, wire, set_value
    w = win
    place(w, "V", 40, 200); place(w, "R", 160, 40); place(w, "R", 160, 140); place(w, "Q_2N2222", 340, 220)
    place(w, "GND", 200, 400)
    assert w.doc.parts["Q1"]["kind"] == "Q_2N2222"
    for a, b in (("V1.p", "R1.a"), ("V1.p", "R2.a"), ("R1.b", "Q1.b"), ("R2.b", "Q1.c"), ("Q1.e", "GND1.g"),
                 ("V1.n", "GND1.g")):
        wire(w, a, b)
    set_value(w, "R1", "Resistance", "430k"); set_value(w, "R2", "Resistance", "1k")
    n = w.doc.nets()
    assert len({n["Q1.b"], n["Q1.c"], n["Q1.e"]}) == 3 and n["Q1.e"] == "0"
    assert w.problems.count() == 0
    w.a_run.trigger(); QApplication.processEvents()
    assert w.state == "completed", w.status.text()
    assert w.result.stats("I(Q1)")["final"] == pytest.approx(1.6114e-3, rel=1e-2)
    assert w.result.stats(f"V({n['Q1.c']})")["final"] == pytest.approx(3.3886, rel=1e-2)
