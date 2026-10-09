"""Segfault bisection: run a subset of GUI edit steps (real mouse/keyboard), then 5 undo-all/redo-all cycles.
usage: a3f_crash_bisect.py STEP[,STEP...]|all   -> prints 'survived' or the process segfaults (rc 139)."""
import sys
sys.path.insert(0, ".")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QFormLayout
from app.window import MainWindow, pin_pos, wire_pts, part_rect
from app.tests.test_gui_workflow import show, click, place, select, wire, set_value, type_into

app = QApplication.instance() or QApplication([])
w = show(MainWindow())


def combo(name, text):
    select(w, name)
    cb = next(w.form.itemAt(i, QFormLayout.FieldRole).widget() for i in range(w.form.rowCount())
              if w.form.itemAt(i, QFormLayout.FieldRole) and isinstance(w.form.itemAt(i, QFormLayout.FieldRole).widget(), QComboBox))
    cb.setCurrentText(text); QTest.qWait(10)


def move(name, dx):
    c = part_rect(w.doc.parts[name]).center(); vp = w.canvas.viewport()
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, w.canvas.mapFromScene(c))
    QTest.mouseMove(vp, w.canvas.mapFromScene(c.x() + dx, c.y()))
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, w.canvas.mapFromScene(c.x() + dx, c.y()))


def delwire():
    a, b = w.doc.wires[0]; pts = wire_pts(w.doc, a, b); click(w, (pts[0] + pts[1]) / 2)
    QTest.keyClick(w.canvas, Qt.Key_Delete)


STEPS = {
    "rotate": lambda: (select(w, "C1"), QTest.keyClick(w.canvas, Qt.Key_R)),
    "param": lambda: set_value(w, "R1", "Resistance", "2k"),
    "choice": lambda: combo("SW1", "closed"),
    "mode": lambda: combo("V1", "PULSE"),
    "move": lambda: move("SW1", 80),
    "rclick": lambda: click(w, pin_pos(w.doc, "SW1.a"), Qt.RightButton),
    "delwire": delwire,
    "delpart": lambda: (select(w, "SW1"), QTest.keyClick(w.canvas, Qt.Key_Delete)),
}
want = sys.argv[1].split(",") if len(sys.argv) > 1 else ["all"]
for k in ["V", "R", "C", "GND", "SW"]: place(w, k, {"V": 40, "R": 160, "C": 320, "GND": 220, "SW": 400}[k],
                                             {"V": 120, "R": 40, "C": 120, "GND": 300, "SW": 260}[k])
for a, b in (("V1.p", "R1.a"), ("R1.b", "C1.a"), ("C1.b", "GND1.g"), ("V1.n", "GND1.g"), ("C1.a", "SW1.a")):
    wire(w, a, b)
for name, fn in STEPS.items():
    if "all" in want or name in want: fn()
select(w, "R1")
for cyc in range(5):
    while w.hist.can_undo(): QTest.keyClick(w.canvas, Qt.Key_Z, Qt.ControlModifier)
    while w.hist.can_redo(): QTest.keyClick(w.canvas, Qt.Key_Y, Qt.ControlModifier)
print("survived")
