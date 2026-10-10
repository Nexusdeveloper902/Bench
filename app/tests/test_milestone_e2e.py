"""T10 (plan 02-plan.md section 10): milestone end-to-end checks and the D17 regressions (audit mutations M04, M07,
M12, M16, M17, M18, plus the RC step response). Every test drives MainWindow through QTest mouse/key events or its
public actions and widgets, with the real catalog, real files in tmp_path and the real engine. No mocks."""
import json
import math

import pytest
from PySide6.QtCore import Qt, QPointF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsSimpleTextItem, QLabel

from conftest import write_def
from test_analytic_diode import final, shockley   # the Shockley oracle shared with the diode tests
from app import catalog, widgets
from app.catalog import BUILTIN_ROOT, CATALOG
from app.model import value_at
from app.symbols import Badge
from app.window import MainWindow, pin_pos

ESP = "ESP32_DEVKITC"
I_LED = 9.1772e-3   # plan experiment 5: LED example I(D1) at 5 V / 330 ohm

# plan 02-plan.md section 8: a v2 file with a part whose definition does not exist (FUTURE_RELAY is not in the catalog)
SECTION8 = {"format": "sim-circuit", "version": 2,
            "components": {"V1": {"type": "BAT", "version": 1, "params": {"dc": "5"}},
                           "R1": {"type": "R", "version": 1, "params": {"R": "330"}},
                           "D1": {"type": "LED", "version": 1, "params": {}},
                           "GND1": {"type": "GND", "version": 1, "params": {}},
                           "U1": {"type": "ESP32_DEVKITC", "version": 1, "params": {}},
                           "U2": {"type": "FUTURE_RELAY", "version": 3, "params": {"coil": "5"}}},
            "connections": [["V1.p", "R1.a"], ["R1.b", "D1.a"], ["D1.k", "GND1.g"], ["V1.n", "GND1.g"], ["U2.COM", "U2.NO"]],
            "simulation": {"analysis": "transient", "tstop": "5m", "tstep": "10u"},
            "layout": {"V1": {"x": 40, "y": 100, "rotation": 0}, "U1": {"x": 600, "y": 40, "rotation": 0},
                       "U2": {"x": 600, "y": 500, "rotation": 90}}}
U2_RECORD = {"type": "FUTURE_RELAY", "version": 3, "params": {"coil": "5"}}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    w = MainWindow(); w.show(); w.activateWindow(); QTest.qWaitForWindowActive(w); w.canvas.setFocus()
    yield w
    w.hide(); w.close(); w.deleteLater()


# ---------------------------------------------------------------- input helpers (mouse, keys, widgets)
def vp(w, pt):
    return w.canvas.mapFromScene(pt if isinstance(pt, QPointF) else QPointF(*pt))


def click(w, pt):
    QTest.mouseClick(w.canvas.viewport(), Qt.LeftButton, Qt.NoModifier, vp(w, pt))


def place(w, kind, x, y):
    """Library card click (arms the tool), then a canvas click at the scene point. -> the new part's name."""
    w.library.card(kind).click(); click(w, (x + 5, y + 5))
    return next(reversed(w.doc.parts))


def select(w, name):
    d = w.doc.parts[name]; click(w, (d["x"] + 30, d["y"] + 15))
    assert w.sel == name


def wire(w, a, b):
    n = len(w.doc.wires)
    click(w, pin_pos(w.doc, a)); click(w, pin_pos(w.doc, b))
    assert len(w.doc.wires) == n + 1, (a, b)


def set_param(w, part, label, text):
    """Select the part on the canvas, then type into its inspector field and commit with Return."""
    select(w, part)
    ed = w.inspector.field(label)
    ed.setFocus(); ed.selectAll(); QTest.keyClicks(ed, text); QTest.keyClick(ed, Qt.Key_Return)


def set_run_times(w, tstop, tstep):
    for ed, text in ((w.tstop, tstop), (w.tstep, tstep)):
        ed.setFocus(); ed.selectAll(); QTest.keyClicks(ed, text); QTest.keyClick(ed, Qt.Key_Return)


def press_run(w):
    """Click the Run button, or Re-run when the results are stale (that is what the toolbar offers then)."""
    btn = w.b_run if w.b_run.isEnabled() else w.b_rerun
    assert btn.isEnabled(), "no run control is enabled"
    QTest.mouseClick(btn, Qt.LeftButton)


def type_search(w, text):
    s = w.library.search
    s.clear(); s.setFocus(); QTest.keyClicks(s, text)


def ids(w):
    return list(w.library.cards)


def click_widget(wd):
    QTest.mouseClick(wd, Qt.LeftButton, Qt.NoModifier, wd.rect().center())


def cross(series, level, t0=0.0, rising=True):
    """Time of the first linear-interpolated crossing of `level` at or after t0 (the waveform has no t = 0 sample)."""
    for (ta, va), (tb, vb) in zip(series, series[1:]):
        if ta < t0 or va == vb or (va - level) * (vb - level) > 0: continue
        if (vb > va) == rising:
            return ta + (level - va) * (tb - ta) / (vb - va)
    raise AssertionError(f"no {'rising' if rising else 'falling'} crossing of {level} after {t0}")


# ---------------------------------------------------------------- 1-2: discovery, search and filter
def test_1_esp32_is_listed_under_microcontrollers(win):
    lib = win.library
    card = lib.card(ESP)
    assert card.name.text() == "ESP32-DevKitC V4" and card.badge.text() == "Interface only"
    assert "Microcontrollers" in [n.name.text() for n in lib.navs.values()]
    click_widget(lib.navs["Microcontrollers"])
    shown = set(ids(win))
    assert shown == {d for d, c in CATALOG.defs.items() if c.category == "Microcontrollers"} == {ESP, "ARDUINO_UNO_R3"}
    assert lib.navs["Microcontrollers"].property("selected") and ESP in lib.cards


def test_2_search_and_category_filter_through_the_ui(win):
    lib = win.library
    type_search(win, "diode")
    assert set(ids(win)) == {"D", "D_1N4148", "LED"}
    click_widget(lib.navs["Electrical"])                       # the category narrows the same query
    assert set(ids(win)) == {"D", "D_1N4148", "LED"} and lib.navs["Electrical"].property("selected")
    click_widget(lib.navs["Sensors"])                          # no sensor matches "diode": empty state says why
    assert ids(win) == [] and lib.empty.isVisible() and "diode" in lib.empty_title.text()
    type_search(win, "temperature")                            # the sensor is found inside its category
    assert ids(win) == ["DHT22"]
    click_widget(lib.navs[None])                               # All: still the same query
    assert ids(win) == ["DHT22"] and lib.navs[None].property("selected")
    type_search(win, "esp")                                    # alias-free name search across categories
    assert ids(win) == [ESP]
    type_search(win, "zzz")
    assert ids(win) == [] and lib.empty.isVisible()


# ---------------------------------------------------------------- 3: extensibility, no code
def test_3_new_definition_is_placed_wired_and_simulated(extra_root, qapp):
    src = BUILTIN_ROOT / "electrical" / "d_1n4148"
    dst = extra_root / "test_diode"; dst.mkdir()
    text = (src / "component.toml").read_text(encoding="utf-8")
    assert 'id = "D_1N4148"' in text
    (dst / "component.toml").write_text(text.replace('id = "D_1N4148"', 'id = "TEST_DIODE"'), encoding="utf-8")
    (dst / "symbol.svg").write_bytes((src / "symbol.svg").read_bytes())
    catalog.reload([BUILTIN_ROOT, extra_root])
    w = MainWindow(); w.show()
    try:
        card = w.library.card("TEST_DIODE")
        assert card is not None and card.name.text() == "1N4148 diode" and card.badge.text() == "Simulated"
        place(w, "V", 40, 100); place(w, "R", 180, 60); place(w, "TEST_DIODE", 320, 100); place(w, "GND", 440, 220)
        assert list(w.doc.parts) == ["V1", "R1", "D1", "GND1"]
        assert w.doc.parts["V1"]["params"]["dc"] == "5" and w.doc.parts["R1"]["params"]["R"] == "1000"   # 5 V / 1 kOhm
        for a, b in (("V1.p", "R1.a"), ("R1.b", "D1.a"), ("D1.k", "GND1.g"), ("V1.n", "GND1.g")): wire(w, a, b)
        set_run_times(w, "100u", "10u")
        press_run(w)
        assert w.result.ok and w.status.text().startswith("SUCCESS"), w.status.text()
        i_ref, _ = shockley(5.0, 1e3, 2.52e-9, 1.752, 0.568)      # bisection, the 1N4148 card's own numbers
        assert final(w.result, "I(D1)") == pytest.approx(i_ref, rel=2e-3)
    finally:
        w.hide(); w.close(); w.deleteLater()


# ---------------------------------------------------------------- 4: unsupported part, inspect, save, reopen
def test_4_esp32_inspect_save_and_reopen_in_a_new_window(win, tmp_path):
    w = win
    assert place(w, ESP, 40, 40) == "U1"
    select(w, "U1")
    QTest.keyClick(w.canvas, Qt.Key_R)                           # rotate 90 degrees, so the layout is not all zeros
    assert w.doc.parts["U1"]["rot"] == 90
    table = w.inspector.findChildren(widgets.Table)[0]
    assert len(table.rows) == 38 and w.inspector.findChildren(widgets.Badge)[0].text() == "Interface only"
    out = tmp_path / "esp.json"
    w.path = str(out)
    w.a_save.trigger()
    assert w.status.text() == f"Saved {out}" and not w.dirty()
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert rec["components"]["U1"] == {"type": ESP, "version": 1, "params": {}}
    assert rec["layout"]["U1"] == {"x": 40, "y": 40, "rotation": 90}
    w2 = MainWindow(); w2.show()
    try:
        w2.open(str(out))
        assert w2.status.text().startswith("Opened") and w2.doc.placeholders() == []
        assert w2.doc.parts == w.doc.parts                       # type, version, params and layout all equal
        assert w2.doc.definition("U1").id == ESP and w2.doc.definition("U1").readiness == "interface"
        w2.select("U1")
        assert len(w2.inspector.findChildren(widgets.Table)[0].rows) == 38
    finally:
        w2.hide(); w2.close(); w2.deleteLater()


# ---------------------------------------------------------------- 5: capability pre-check, real engine
def test_5_wired_esp32_is_blocked_then_runs_when_unwired(win):
    w = win
    w.load_led_example()
    assert place(w, ESP, 480, 20) == "U1"
    wire(w, "U1.3V3", "R1.a")
    press_run(w)
    r = w.result
    assert r.status == "blocked" and w.state == "blocked" and w.status.text().startswith("BLOCKED")
    assert "NOT-SIMULATED" in r.message and "U1" in r.message and "ESP32" in r.message
    assert r.series == {} and r.signals == [] and not w.ax.lines            # no plot, no signals, no false success
    assert w.err_title.text() == "Run blocked" and "U1" in w.err.text()
    click(w, (330, 40))                                                    # the wire runs along y = 40
    assert w.sel_wire == ("U1.3V3", "R1.a")
    QTest.keyClick(w.canvas, Qt.Key_Delete)
    assert ("U1.3V3", "R1.a") not in w.doc.wires and len(w.doc.wires) == 4
    press_run(w)
    assert w.result.ok and w.status.text().startswith("SUCCESS")
    assert "not simulated: U1" in w.status.text() and w.result.excluded == ["U1"]
    assert final(w.result, "I(D1)") == pytest.approx(I_LED, rel=1e-3)


# ---------------------------------------------------------------- 6: invalid definitions are reported, the rest works
def test_6_invalid_definitions_are_listed_and_the_rest_still_runs(extra_root, qapp):
    write_def(extra_root, "BROKEN", mutate=lambda d: d["pins"].append({"id": "a", "at": [1, 1]}), reload=False)
    bad = extra_root / "garbled"; bad.mkdir()
    (bad / "component.toml").write_text("format = 1\nid = \n", encoding="utf-8")
    catalog.reload([BUILTIN_ROOT, extra_root])
    w = MainWindow(); w.show()
    try:                                                                   # the app starts
        lib = w.library
        assert lib.banner.isVisible() and lib.problem_count == 2
        assert lib.banner_toggle.text().startswith("2 definitions could not be loaded")
        click_widget(lib.banner_toggle)
        body = " ".join(l.text() for l in lib.banner_body.findChildren(QLabel))
        assert "broken/component.toml" in body and "DEF-PIN" in body and "garbled/component.toml" in body
        assert "DEF-PARSE" in body
        assert "BROKEN" not in lib.cards and "garbled" not in lib.cards and ESP in lib.cards and "R" in lib.cards
        w.load_led_example(); press_run(w)                                 # the good parts simulate as before
        assert w.result.ok and final(w.result, "I(D1)") == pytest.approx(I_LED, rel=1e-3)
    finally:
        w.hide(); w.close(); w.deleteLater()


# ---------------------------------------------------------------- 7: missing definition, placeholder kept
def test_7_missing_definition_file_opens_as_placeholder_and_saves_unchanged(win, tmp_path):
    w = win
    src = tmp_path / "section8.json"
    src.write_text(json.dumps(SECTION8, indent=1), encoding="utf-8")
    w.open(str(src))
    assert w.doc.placeholders() == ["U2"] and w.doc.parts["U2"]["placeholder"]["reason"] == "missing"
    assert "1 part has no usable definition: U2 (FUTURE_RELAY v3)" in w.status.text()
    assert any(w.problems.item(i).data(Qt.UserRole) == "U2" and "MISSING-DEFINITION" in w.problems.item(i).text()
               for i in range(w.problems.count()))
    scene = w.canvas.sc.items()
    assert any(isinstance(i, QGraphicsSimpleTextItem) and i.text() == "FUTURE_RELAY  v3" for i in scene)  # drawn
    assert any(isinstance(i, Badge) and i.key == "placeholder" for i in scene)
    w.select("U2")
    assert w.inspector.view == "placeholder" and "Kept unchanged" in " ".join(l.text() for l in w.inspector.findChildren(QLabel))
    out = tmp_path / "saved.json"
    w.path = str(out)
    w.a_save.trigger()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert json.dumps(saved["components"]["U2"]) == json.dumps(U2_RECORD)   # the record, byte for byte
    assert ["U2.COM", "U2.NO"] in saved["connections"]
    press_run(w)                                                           # wired to itself: blocked, named
    assert w.result.status == "blocked" and "MISSING-DEFINITION" in w.status.text() and "U2" in w.status.text()


# ---------------------------------------------------------------- 8: D17 regressions as numbers
def test_m04_two_grounds_are_both_node_zero(win):
    w = win
    place(w, "V", 40, 100); place(w, "R", 180, 60); place(w, "LED", 320, 100)
    place(w, "GND", 440, 220); place(w, "GND", 440, 300)
    assert list(w.doc.parts) == ["V1", "R1", "D1", "GND1", "GND2"]
    set_param(w, "R1", "Resistance", "330")
    wire(w, "V1.p", "R1.a"); wire(w, "R1.b", "D1.a"); wire(w, "D1.k", "GND1.g"); wire(w, "V1.n", "GND2.g")
    nets = w.doc.nets()
    assert nets["GND1.g"] == nets["GND2.g"] == nets["D1.k"] == nets["V1.n"] == "0"
    assert "V1 n1 0 DC 5.0" in w.doc.netlist()["lines"]                  # the source returns to node 0 through GND2
    press_run(w)
    assert w.result.ok, w.result.message
    i_ref, _ = shockley(5.0, 330.0, 1e-20, 1.8, 5.0)
    assert final(w.result, "I(D1)") == pytest.approx(i_ref, rel=1e-4)


def test_m07_capacitor_initial_voltage_is_saved_emitted_and_applied(win, tmp_path):
    w = win
    place(w, "V", 40, 100); place(w, "R", 180, 60); place(w, "C", 320, 100); place(w, "GND", 440, 220)
    set_param(w, "C1", "Initial voltage", "2")
    assert w.doc.parts["C1"]["params"]["ic"] == "2"
    for a, b in (("V1.p", "R1.a"), ("R1.b", "C1.a"), ("C1.b", "GND1.g"), ("V1.n", "GND1.g")): wire(w, a, b)
    out = tmp_path / "c.json"
    w.path = str(out); w.a_save.trigger()
    assert json.loads(out.read_text(encoding="utf-8"))["components"]["C1"]["params"]["ic"] == "2"
    assert "C1 n2 0 1e-06 IC=2.0" in w.doc.netlist()["lines"]
    set_run_times(w, "5m", "10u")
    press_run(w)
    assert w.result.ok, w.result.message
    t0, v0 = w.result.get("V(C1)")[0]                                    # first output sample, 1e-7 s
    assert t0 <= 1e-6 and v0 == pytest.approx(2.0, abs=1e-3)


def test_m12_diode_series_resistance_changes_the_current(win):
    w = win
    place(w, "V", 40, 100); place(w, "R", 180, 60); place(w, "D", 320, 100); place(w, "GND", 440, 220)
    set_param(w, "D1", "Series resistance", "100")
    assert w.doc.parts["D1"]["params"]["Rs"] == "100"
    for a, b in (("V1.p", "R1.a"), ("R1.b", "D1.a"), ("D1.k", "GND1.g"), ("V1.n", "GND1.g")): wire(w, a, b)
    press_run(w)
    assert w.result.ok, w.result.message
    i = final(w.result, "I(D1)")
    i_rs, _ = shockley(5.0, 1e3, 1e-14, 1.0, 100.0)
    i_no_rs, _ = shockley(5.0, 1e3, 1e-14, 1.0, 0.0)
    assert i == pytest.approx(i_rs, rel=1e-4)
    assert abs(i - i_no_rs) / i_no_rs > 0.01                             # Rs is not ignored: 100 ohm moves it


def test_m16_pulse_rise_and_fall_are_independent(win):
    w = win
    place(w, "V", 40, 100); place(w, "R", 180, 60); place(w, "GND", 440, 220)
    select(w, "V1")
    w.inspector.edits["mode"].setCurrentText("PULSE"); QApplication.processEvents()
    set_param(w, "V1", "Rise time", "100u")
    set_param(w, "V1", "Fall time", "400u")
    set_param(w, "V1", "Pulse width", "0.5m")
    set_param(w, "V1", "Period", "2m")
    for a, b in (("V1.p", "R1.a"), ("R1.b", "GND1.g"), ("V1.n", "GND1.g")): wire(w, a, b)
    set_run_times(w, "1.2m", "10u")
    press_run(w)
    assert w.result.ok, w.result.message
    s, step = w.result.get("V(V1)"), 10e-6
    rise = cross(s, 4.5) - cross(s, 0.5, rising=True)                   # 10 % to 90 % of 0..5 V
    fall = cross(s, 0.5, t0=0.6e-3, rising=False) - cross(s, 4.5, t0=0.6e-3, rising=False)
    assert abs(rise - 0.8 * 100e-6) <= 2e-6                             # 80 us, +-2 us (interpolated crossings)
    assert abs(fall - 0.8 * 400e-6) <= 2e-6                             # 320 us, +-2 us


def test_m17_sine_amplitude_is_one_volt(win):
    w = win
    place(w, "V", 40, 100); place(w, "R", 180, 60); place(w, "GND", 440, 220)
    select(w, "V1")
    w.inspector.edits["mode"].setCurrentText("SIN"); QApplication.processEvents()
    assert list(w.inspector.edits) == ["mode", "vo", "va", "freq"]
    set_param(w, "V1", "Amplitude", "1")
    set_param(w, "V1", "Frequency", "1k")
    for a, b in (("V1.p", "R1.a"), ("R1.b", "GND1.g"), ("V1.n", "GND1.g")): wire(w, a, b)
    set_run_times(w, "2m", "10u")
    press_run(w)
    assert w.result.ok, w.result.message
    v = [x for _, x in w.result.get("V(V1)")]
    assert max(v) == pytest.approx(1.0, rel=2e-3) and min(v) == pytest.approx(-1.0, rel=2e-3)


def test_m18_inductor_initial_current_is_applied(win):
    w = win
    place(w, "V", 40, 100); place(w, "R", 180, 60); place(w, "L", 320, 100); place(w, "GND", 440, 220)
    set_param(w, "L1", "Inductance", "1")                              # 1 H: di/dt is ~4 A/s, so t = 0 is stable
    set_param(w, "L1", "Initial current", "1m")
    assert w.doc.parts["L1"]["params"]["ic"] == "1m"
    for a, b in (("V1.p", "R1.a"), ("R1.b", "L1.a"), ("L1.b", "GND1.g"), ("V1.n", "GND1.g")): wire(w, a, b)
    set_run_times(w, "1m", "1u")
    press_run(w)
    assert w.result.ok, w.result.message
    t0, i0 = w.result.get("I(L1)")[0]
    assert t0 <= 1e-7 and i0 == pytest.approx(1e-3, rel=1e-3)


def test_rc_step_response_at_one_time_constant(win):
    w = win
    place(w, "V", 40, 100); place(w, "R", 180, 60); place(w, "C", 320, 100); place(w, "GND", 440, 220)
    set_param(w, "R1", "Resistance", "1k")
    set_param(w, "C1", "Capacitance", "1u")
    for a, b in (("V1.p", "R1.a"), ("R1.b", "C1.a"), ("C1.b", "GND1.g"), ("V1.n", "GND1.g")): wire(w, a, b)
    set_run_times(w, "5m", "10u")
    press_run(w)
    assert w.result.ok, w.result.message
    v_tau = value_at(w.result.get("V(C1)"), 1e-3)                       # tau = R C = 1 ms
    assert v_tau == pytest.approx(5 * (1 - math.exp(-1)), rel=5e-3)      # 3.1606 V
