"""Full user workflow in MainWindow (offscreen) using only mouse/key events and param-panel edits; real core numbers."""
import pytest
from PySide6.QtCore import Qt, QPointF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFormLayout
from app.window import MainWindow, pin_pos


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    w = MainWindow(); w.show(); w.canvas.setFocus(); return w


def click(w, pt):
    QTest.mouseClick(w.canvas.viewport(), Qt.LeftButton, Qt.NoModifier, w.canvas.mapFromScene(QPointF(*pt) if isinstance(pt, tuple) else pt))


def place(w, kind, x, y):
    w.tool_btns[kind].click(); click(w, (x + 5, y + 5))


def wire(w, a, b):
    click(w, pin_pos(w.doc, a)); click(w, pin_pos(w.doc, b))


def set_param(w, part, text):
    """Select the part by clicking it, then type into the first param field of the panel."""
    d = w.doc.parts[part]; click(w, (d["x"] + 30, d["y"] + 15))
    assert w.sel == part
    ed = w.form.itemAt(1, QFormLayout.FieldRole).widget()
    ed.setText(text); ed.editingFinished.emit()


def build(w, reverse=False, battery=True, last_wire=True):
    place(w, "BAT", 40, 100); place(w, "R", 180, 60); place(w, "LED", 320, 100); place(w, "GND", 440, 220)
    assert list(w.doc.parts) == ["V1", "R1", "D1", "GND1"]
    if battery:
        set_param(w, "V1", "5"); set_param(w, "R1", "330")
    a, k = ("D1.k", "D1.a") if reverse else ("D1.a", "D1.k")
    wire(w, "R1.b", a); wire(w, k, "GND1.g")
    if battery:
        wire(w, "V1.p", "R1.a")
        if last_wire: wire(w, "V1.n", "GND1.g")
    w.tstop.setText("1e-3"); w.tstop.textEdited.emit("1e-3")
    w.tstep.setText("1e-5"); w.tstep.textEdited.emit("1e-5")


def fin(w, n): return w.result.series[n][-1][1]


def test_led_workflow_numbers_undo_redo(win):
    w = win; build(w)
    assert len(w.doc.wires) == 4 and w.doc.parts["R1"]["params"]["R"] == "330"
    w.a_run.trigger()
    assert w.status.text().startswith("SUCCESS"), w.status.text()
    i, vd, vr, vb = fin(w, "@d1[id]"), fin(w, "v(D1)"), fin(w, "v(R1)"), fin(w, "v(V1)")
    assert vb == pytest.approx(5, abs=1e-6)
    assert i == pytest.approx((5 - vd) / 330, abs=1e-6)
    assert vr + vd == pytest.approx(5, abs=1e-6)
    assert 5e-3 < i < 15e-3
    # picker -> plot
    w.probe.setCurrentText("V(D1)")
    line = w.ax.lines[0]
    assert w.ax.get_title() == "V(D1)" and list(line.get_ydata()) == [v for _, v in w.result.series["v(D1)"]]
    s = w.result.stats("v(D1)")
    from app.components import format_si
    assert f"final {format_si(s['final'], 'V', 6)}" in w.stats.text() and f"max {format_si(s['max'], 'V', 6)}" in w.stats.text()
    assert not w.ax.lines == [] and w.findings.text() == ""
    # undo last wire -> stale at once, rerun reports dangling (core still solves it)
    QTest.keyClick(w.canvas, Qt.Key_Z, Qt.ControlModifier)
    assert len(w.doc.wires) == 3 and w.result is None and "out of date" in w.status.text() and not w.ax.lines
    w.a_run.trigger()
    assert "DANGLING" in w.findings.text()
    # redo -> stale again, run -> success again
    QTest.keyClick(w.canvas, Qt.Key_Y, Qt.ControlModifier)
    assert w.result is None and "out of date" in w.status.text()
    w.a_run.trigger()
    assert w.status.text().startswith("SUCCESS") and w.findings.text() == ""
    assert fin(w, "@d1[id]") == pytest.approx(i, abs=1e-12)


def test_stale_after_param_delete_connect(win):
    w = win; w.load_led_example(); w.a_run.trigger(); assert w.result.ok
    set_param(w, "R1", "470")
    assert w.result is None and "out of date" in w.status.text() and not w.ax.lines and w.stats.text() == ""
    w.a_run.trigger(); assert w.result.ok
    w.select("V1"); QTest.keyClick(w.canvas, Qt.Key_Delete)
    assert w.result is None and "out of date" in w.status.text()
    w.a_run.trigger(); w.undo()
    assert w.result is None
    w.a_run.trigger(); assert w.result.ok
    wire(w, "R1.a", "D1.a")   # new wire (shorts R1, still solvable)
    assert w.result is None
    # moving a part does not invalidate
    w.a_run.trigger(); assert w.result.ok
    d = w.doc.parts["D1"]
    QTest.mousePress(w.canvas.viewport(), Qt.LeftButton, Qt.NoModifier, w.canvas.mapFromScene(QPointF(d["x"] + 30, d["y"] + 15)))
    QTest.mouseMove(w.canvas.viewport(), w.canvas.mapFromScene(QPointF(d["x"] + 30, d["y"] + 55)))
    QTest.mouseRelease(w.canvas.viewport(), Qt.LeftButton, Qt.NoModifier, w.canvas.mapFromScene(QPointF(d["x"] + 30, d["y"] + 55)))
    assert w.result is not None and w.result.ok


def test_reversed_led_blocks(win):
    w = win; build(w, reverse=True); w.a_run.trigger()
    assert w.status.text().startswith("SUCCESS"), w.status.text()
    assert abs(fin(w, "@d1[id]")) < 1e-9 and fin(w, "v(D1)") == pytest.approx(-5, abs=1e-3)


def test_led_without_battery_fails_informatively(win):
    w = win; build(w, battery=False); w.a_run.trigger()
    assert w.status.text().startswith("BLOCKED") and len(w.status.text()) > 20
    assert not w.result.ok and w.result.message in w.status.text()


def test_findings_highlight_nets(win):
    w = win; w.load_led_example()
    click(w, pin_pos(w.doc, "D1.k")); w.canvas.setFocus()
    QTest.mouseClick(w.canvas.viewport(), Qt.RightButton, Qt.NoModifier, w.canvas.mapFromScene(pin_pos(w.doc, "D1.k")))
    w.a_run.trigger()
    assert "DANGLING" in w.findings.text()
    ring = [it for it in w.canvas.sc.items() if it.boundingRect().width() == 20 + 3 and it.pen().color().name() == "#e0a000"]
    assert len(ring) >= 1
