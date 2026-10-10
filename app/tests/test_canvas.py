"""GUI tests driving real mouse/key events on the canvas (offscreen)."""
import copy, pytest
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtTest import QTest
from app.window import MainWindow, pin_pos, wire_pts


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(shown_window):
    return shown_window


def vp(w, scene_pt):
    if not isinstance(scene_pt, QPointF): scene_pt = QPointF(*scene_pt)
    return w.canvas.mapFromScene(scene_pt)


def click(w, pt, button=Qt.LeftButton):
    QTest.mouseClick(w.canvas.viewport(), button, Qt.NoModifier, vp(w, pt))


def drag(w, a, b):
    v = w.canvas.viewport()
    a, b = [(p.x(), p.y()) if isinstance(p, QPointF) else p for p in (a, b)]
    QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, vp(w, a))
    QTest.mouseMove(v, vp(w, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)))
    QTest.mouseMove(v, vp(w, b))
    QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, vp(w, b))


def place(w, kind, x, y):
    w.library.card(kind).click(); click(w, (x + 5, y + 5))   # snaps to grid


def test_place_drag_wire_delete_undo(win):
    w = win
    place(w, "BAT", 40, 100); place(w, "R", 180, 100)
    assert list(w.doc.parts) == ["V1", "R1"] and w.tool is None
    # drag R1 by its body
    x0, y0 = w.doc.parts["R1"]["x"], w.doc.parts["R1"]["y"]
    drag(w, (x0 + 30, y0 + 15), (x0 + 30 + 60, y0 + 15 + 40))
    assert (w.doc.parts["R1"]["x"], w.doc.parts["R1"]["y"]) == (x0 + 60, y0 + 40)
    assert w.sel == "R1"
    w.undo(); assert (w.doc.parts["R1"]["x"], w.doc.parts["R1"]["y"]) == (x0, y0)
    w.redo(); assert w.doc.parts["R1"]["x"] == x0 + 60
    # wire: click pin then pin
    click(w, pin_pos(w.doc, "V1.p")); click(w, pin_pos(w.doc, "R1.a"))
    assert w.doc.nets()["V1.p"] == w.doc.nets()["R1.a"] and w.doc.wires == [("V1.p", "R1.a")]
    # wire: drag pin to pin; wire follows when part moves
    drag(w, pin_pos(w.doc, "R1.b"), pin_pos(w.doc, "V1.n"))
    assert ("R1.b", "V1.n") in w.doc.wires
    n = w.doc.nets(); assert n["R1.b"] == n["V1.n"] != n["V1.p"]
    # select wire by clicking the middle of its first (right-angle routed) segment, Delete removes it
    a, b = wire_pts(w.doc, "V1.p", "R1.a")[:2]
    click(w, QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2))
    assert w.sel_wire == ("V1.p", "R1.a")
    QTest.keyClick(w.canvas, Qt.Key_Delete)
    assert ("V1.p", "R1.a") not in w.doc.wires and ("R1.b", "V1.n") in w.doc.wires
    w.undo(); assert ("V1.p", "R1.a") in w.doc.wires
    # select part + Delete removes part and its wires; undo restores
    snapshot = copy.deepcopy(w.doc.to_dict())
    p = w.doc.parts["R1"]; click(w, (p["x"] + 30, p["y"] + 15))
    QTest.keyClick(w.canvas, Qt.Key_Delete)
    assert "R1" not in w.doc.parts and w.doc.wires == []
    w.undo()
    assert w.doc.to_dict() == snapshot


def test_ctrl_z_shortcuts(win):
    w = win
    place(w, "R", 100, 100)
    QTest.keyClick(w.canvas, Qt.Key_Z, Qt.ControlModifier)
    assert w.doc.parts == {}
    QTest.keyClick(w.canvas, Qt.Key_Y, Qt.ControlModifier)
    assert "R1" in w.doc.parts
    QTest.keyClick(w.canvas, Qt.Key_Z, Qt.ControlModifier)
    QTest.keyClick(w.canvas, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert "R1" in w.doc.parts


def test_right_click_pin_disconnects_and_undo(win):
    w = win; w.load_led_example()
    before = copy.deepcopy(w.doc.to_dict())
    click(w, pin_pos(w.doc, "D1.k"), Qt.RightButton)
    assert not any("D1.k" in x for x in w.doc.wires)
    w.undo(); assert w.doc.to_dict() == before


def test_param_edit_undo_and_tstop_survives_selection(win):
    w = win; w.load_led_example()
    w.tstop.setText("2e-3"); w.tstop.textEdited.emit("2e-3")
    w.select("V1")
    edit = w.inspector.field("Voltage")   # BAT dc
    edit.setText("9"); edit.editingFinished.emit()
    assert w.doc.parts["V1"]["params"]["dc"] == "9"
    w.select("R1")
    assert w.doc.tstop == "2e-3" and w.tstop.text() == "2e-3"
    w.undo(); assert w.doc.parts["V1"]["params"]["dc"] == "5"
    w.select("D1"); assert not w.inspector.edits   # LED: "No configurable parameters."


def test_led_example_run_and_picker(win):
    w = win; w.load_led_example(); w.run()
    assert w.status.text().startswith("SUCCESS")
    items = [w.probe.itemText(i) for i in range(w.probe.count())]
    for need in ("V(n1)", "I(V1)", "I(R1)", "I(D1)", "V(D1)", "V(R1)"): assert need in items   # readable names
    assert w.probe.currentText() == "V(n1)" and "final" in w.stats.text()
    w.probe.setCurrentText("V(D1)")
    assert w.ax.get_ylabel() == "Voltage (V)" and 1.8 < w.result.stats("v(D1)")["final"] < 2.1
    w.probe.setCurrentText("I(D1)"); assert w.ax.get_ylabel() == "Current (mA)"   # ~9 mA: axis scaled to mA
    assert w.view_data()[1] is w.result.series["@d1[id]"]
    click(w, pin_pos(w.doc, "D1.k"), Qt.RightButton); w.run()
    assert "DANGLING" in w.findings.text()


def test_screenshot(win, tmp_path):
    w = win; w.load_led_example(); w.run(); w.select("R1")
    import os
    qapp = __import__("PySide6.QtWidgets", fromlist=["x"]).QApplication; qapp.processEvents()
    out = os.environ.get("SIM_SHOT") or str(tmp_path / "led.png")
    assert w.grab().save(out)
