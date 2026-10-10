"""Real-window workflows (offscreen QTest): build circuits with mouse/keyboard, configure through the inspector,
run the REAL core, read the waveform viewer's data and compare with analytic results. No mocks of the simulator;
only modal dialogs (file pickers, message boxes) are monkeypatched because they cannot be clicked offscreen."""
import math
import pytest
from PySide6.QtCore import Qt, QPointF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QLineEdit, QComboBox
from app.window import MainWindow, pin_pos, wire_pts, part_rect

VF, R, C = 5.0, 1e3, 10e-6
TAU = R * C                 # 10 ms
# Tolerance: 0.5 mV (0.01 % of 5 V) over EVERY plotted point. Measured error with tstep = 50 us = tau/200 is ~4 uV
# (trapezoidal integration), so 0.5 mV is >100x margin for platform/ngspice-version jitter, while a 1 % error in
# tau (wrong R, C or SI-suffix parsing) gives ~18 mV and fails.
TOL = 5e-4


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def show(w):
    """Show and activate: window shortcuts (R, Ctrl+Z, F5) only fire in the active window."""
    w.show(); w.activateWindow(); QTest.qWaitForWindowActive(w); w.canvas.setFocus()
    return w


@pytest.fixture
def win(qapp):
    w = show(MainWindow())
    yield w
    w.hide(); w.close(); w.deleteLater()   # hidden: no unsaved-changes prompt; gone: cannot steal later shortcuts


# ---- helpers: real input events only ----
def click(w, pt, button=Qt.LeftButton):
    pt = pt if isinstance(pt, QPointF) else QPointF(*pt)
    QTest.mouseClick(w.canvas.viewport(), button, Qt.NoModifier, w.canvas.mapFromScene(pt))


def place(w, kind, x, y):
    w.library.card(kind).click(); click(w, (x + 5, y + 5))
    return next(reversed(w.doc.parts))


def select(w, name):
    click(w, part_rect(w.doc.parts[name]).center()); assert w.sel == name


def wire(w, a, b):
    click(w, pin_pos(w.doc, a)); click(w, pin_pos(w.doc, b))


def field(w, label):
    """Inspector widget whose row label starts with `label` (e.g. 'Resistance')."""
    try:
        return w.inspector.field(label)
    except StopIteration:
        raise AssertionError(f"no inspector field {label}") from None


def type_into(ed, text):
    ed.setFocus(); ed.selectAll(); QTest.keyClicks(ed, text); QTest.keyClick(ed, Qt.Key_Return)


def set_value(w, part, label, text):
    select(w, part); type_into(field(w, label), text)


def pick(w, name):
    i = w.probe.findText(name); assert i >= 0, name
    w.probe.setCurrentIndex(i)


def build_rc(w):
    """5 V -- R -- C -- GND, placed and wired with the mouse, values typed with SI suffixes."""
    place(w, "V", 40, 120); place(w, "R", 160, 40); place(w, "C", 320, 120); place(w, "GND", 220, 280)
    select(w, "C1"); QTest.keyClick(w.canvas, Qt.Key_R)            # rotate the capacitor upright
    assert w.doc.parts["C1"]["rot"] == 90
    for a, b in (("V1.p", "R1.a"), ("R1.b", "C1.a"), ("C1.b", "GND1.g"), ("V1.n", "GND1.g")):
        wire(w, a, b)
    set_value(w, "V1", "Voltage", "5")
    set_value(w, "R1", "Resistance", "1k")
    set_value(w, "C1", "Capacitance", "10u")
    for ed, text in ((w.tstop, "50m"), (w.tstep, "50u")): type_into(ed, text)
    assert w.doc.parts["R1"]["params"]["R"] == "1k" and w.doc.parts["C1"]["params"]["C"] == "10u"
    assert (w.doc.tstop, w.doc.tstep) == ("50m", "50u")


def viewer_xy(w):
    line = w.ax.lines[0]
    return list(line.get_xdata()), list(line.get_ydata())


def check_rc(w):
    t, v = viewer_xy(w)
    assert len(t) > 500 and t[-1] == pytest.approx(50e-3, rel=1e-3)
    err = max(abs(y - VF * (1 - math.exp(-x / TAU))) for x, y in zip(t, v))
    assert err < TOL, err
    i = min(range(len(t)), key=lambda k: abs(t[k] - TAU))
    assert v[i] / VF == pytest.approx(1 - math.exp(-t[i] / TAU), abs=TOL / VF)   # 63.2 % at t = tau


# ---- workflows ----
def test_rc_built_with_mouse_matches_analytic(win):
    w = win; build_rc(w)
    assert not [p for p in w.doc.validate() if p["severity"] == "error"] and w.problems.count() == 0
    w.a_run.trigger()
    assert w.state == "completed" and w.status.text().startswith("SUCCESS"), w.status.text()
    pick(w, "V(C1)")
    assert w.ax.get_title() == "V(C1)" and w.ax.get_xlabel() == "time (ms)" and w.ax.get_ylabel() == "Voltage (V)"
    sig, ser = w.view_data()
    assert sig["name"] == "V(C1)" and viewer_xy(w) == ([p[0] for p in ser], [p[1] for p in ser])
    check_rc(w)
    # cursor readout: click the plot at t = tau
    w.set_cursor_time(TAU)
    assert "t = 10 ms" in w.readout.text() and "V(C1) = 3.16" in w.readout.text()
    assert w.stats.text().startswith("final 4.966")   # 5(1-e^-5) = 4.9663


def test_save_new_window_load_rerun_identical(win, qapp, tmp_path, monkeypatch):
    w = win; build_rc(w); w.a_run.trigger(); pick(w, "V(C1)")
    first = viewer_xy(w)
    path = str(tmp_path / "rc")                                     # no extension: Save As adds .json
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (path, "")))
    assert w.dirty() and w.windowTitle().endswith("*")
    w.a_saveas.trigger()
    assert w.path == path + ".json" and not w.dirty() and "Saved" in w.status.text()
    w2 = show(MainWindow())
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (path + ".json", "")))
    w2.a_open.trigger()
    assert w2.doc.to_dict() == w.doc.to_dict() and w2.doc.parts["C1"]["rot"] == 90 and not w2.dirty()
    w2.a_run.trigger(); pick(w2, "V(C1)")
    assert viewer_xy(w2) == first                                   # bit-identical waveform after reload
    check_rc(w2)
    w2.hide(); w2.close(); w2.deleteLater(); show(w)


def test_run_reset_rerun_is_deterministic(win):
    w = win; build_rc(w); w.a_run.trigger(); pick(w, "V(C1)")
    first = viewer_xy(w)
    w.a_reset.trigger()
    assert w.result is None and w.state == "idle" and not w.ax.lines and w.probe.count() == 0
    assert "idle" in w.state_lbl.text().lower() and not w.slider.isEnabled()
    w.a_run.trigger()
    assert w.probe.currentText() == "V(C1)" and viewer_xy(w) == first   # pick survives reset, same numbers
    w.a_rerun.trigger()
    assert w.state == "completed" and viewer_xy(w) == first


def test_undo_redo_rotate_and_param_edit(win):
    w = win; place(w, "R", 100, 100)
    before = pin_pos(w.doc, "R1.a")
    select(w, "R1"); QTest.keyClick(w.canvas, Qt.Key_R)
    d = w.doc.parts["R1"]
    assert d["rot"] == 90 and pin_pos(w.doc, "R1.a") == QPointF(d["x"] + 30, d["y"] - 15) != before
    w.a_rotate.trigger(); assert w.doc.parts["R1"]["rot"] == 180
    w.undo(); assert w.doc.parts["R1"]["rot"] == 90
    w.undo(); assert w.doc.parts["R1"]["rot"] == 0 and pin_pos(w.doc, "R1.a") == before
    w.redo(); assert w.doc.parts["R1"]["rot"] == 90
    set_value(w, "R1", "Resistance", "2.2k")
    assert w.doc.parts["R1"]["params"]["R"] == "2.2k"
    QTest.keyClick(w.canvas, Qt.Key_Z, Qt.ControlModifier)
    assert w.doc.parts["R1"]["params"]["R"] == "1000" and w.doc.parts["R1"]["rot"] == 90
    QTest.keyClick(w.canvas, Qt.Key_Y, Qt.ControlModifier)
    assert w.doc.parts["R1"]["params"]["R"] == "2.2k"
    select(w, "R1"); assert field(w, "Resistance").text() == "2.2k"   # inspector shows the restored value


def test_inline_validation_and_units(win):
    w = win; place(w, "C", 100, 100); select(w, "C1")
    ed = field(w, "Capacitance")
    ed.setFocus(); ed.selectAll(); QTest.keyClicks(ed, "4.7n")
    assert "4.7 nF" in w.inspector.message("C") and not w.inspector.boxes["C"].property("invalid")
    ed.selectAll(); QTest.keyClicks(ed, "abc")
    assert "not a number" in w.inspector.message("C") and w.inspector.boxes["C"].property("invalid")
    QTest.keyClick(ed, Qt.Key_Return)                                # stored as typed, flagged everywhere
    assert w.doc.parts["C1"]["params"]["C"] == "abc"
    assert any("BAD-PARAM" in w.problems.item(i).text() for i in range(w.problems.count()))
    select(w, "C1"); assert w.inspector.boxes["C"].property("invalid")
    type_into(w.tstop, "-1"); assert w.tstop.property("invalid")


def test_no_ground_shows_diagnostics_and_details(win):
    w = win
    place(w, "V", 40, 120); place(w, "R", 160, 40)
    wire(w, "V1.p", "R1.a"); wire(w, "R1.b", "V1.n")
    texts = [w.problems.item(i).text() for i in range(w.problems.count())]
    assert any("NO-GROUND" in t for t in texts)                       # shown before running
    w.a_run.trigger()
    assert w.state == "blocked" and "blocked" in w.state_lbl.text().lower() and w.status.text().startswith("BLOCKED")
    assert w.err.isVisible() and "No ground" in w.err.text() and not w.ax.lines and w.stats.text() == ""
    w.details_btn.click()
    assert w.details.isVisible() and "ValidationError" in w.details.toPlainText()
    assert "NO-GROUND" in w.details.toPlainText()


def test_core_error_is_surfaced_with_details(win):
    """A capacitor whose far side goes nowhere passes the app checks (warning only) but the core rejects the
    floating node. The core's message and the netlist sent must reach the user, not be swallowed."""
    w = win
    place(w, "V", 40, 120); place(w, "R", 160, 40); place(w, "C", 300, 120); place(w, "GND", 200, 280)
    for a, b in (("V1.p", "R1.a"), ("R1.b", "C1.a"), ("V1.n", "GND1.g")): wire(w, a, b)
    w.a_run.trigger()
    assert w.state == "failed" and "NO-DC-PATH" in w.err.text()
    det = w.details.toPlainText()
    assert "InvalidCircuitError" in det and "netlist sent to the core" in det and "C1" in det


def test_click_wire_plots_its_node(win):
    w = win; build_rc(w); w.a_run.trigger()
    a, b = wire_pts(w.doc, "R1.b", "C1.a")[:2]
    click(w, QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2))
    node = w.doc.nets()["C1.a"]
    assert w.sel_wire == ("R1.b", "C1.a") and w.probe.currentText() == f"V({node})"
    check_rc(w)                                                        # node voltage == capacitor voltage here


def test_switch_toggle_in_inspector_changes_real_current(win):
    w = win
    place(w, "BAT", 40, 120); place(w, "SW", 160, 40); place(w, "R", 300, 120); place(w, "GND", 200, 280)
    for a, b in (("V1.p", "SW1.a"), ("SW1.b", "R1.a"), ("R1.b", "GND1.g"), ("V1.n", "GND1.g")): wire(w, a, b)
    select(w, "SW1"); combo = field(w, "State")
    assert isinstance(combo, QComboBox) and combo.currentText() == "open"
    combo.setCurrentText("closed"); QApplication.processEvents()
    assert w.doc.parts["SW1"]["params"]["state"] == "closed"
    w.a_run.trigger(); pick(w, "I(R1)")
    assert w.result.stats("I(R1)")["final"] == pytest.approx(5 / (1000 + 0.01), rel=1e-6)
    assert w.ax.get_ylabel() == "Current (mA)"
    select(w, "SW1"); field(w, "State").setCurrentText("open"); QApplication.processEvents()
    assert w.result is None                                            # stale -> cleared
    w.a_run.trigger()
    assert abs(w.result.stats("I(R1)")["final"]) < 1e-8               # 5 V / 1 GOhm
    w.undo(); assert w.doc.parts["SW1"]["params"]["state"] == "closed"


def test_unsaved_prompt_and_save_error(win, tmp_path, monkeypatch):
    w = win; place(w, "R", 100, 100)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(1) or QMessageBox.Cancel))
    w.a_new.trigger(); assert asked and "R1" in w.doc.parts             # cancelled: nothing lost
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    w.a_new.trigger(); assert w.doc.parts == {}
    place(w, "R", 100, 100)
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: errors.append(a[2])))
    bad = str(tmp_path / "missing-dir" / "x.json")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (bad, "")))
    assert w.save() is False
    assert errors and "cannot save" in errors[0] and w.status.text().startswith("FAILED") and w.dirty()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(tmp_path / "nope.json"), "")))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    w.a_open.trigger()
    assert "cannot open" in errors[-1] and "R1" in w.doc.parts       # failed open keeps the current circuit
