"""Repeated undo-to-empty / redo-to-full cycles in a shown MainWindow (places + wires + rotate + param edit only,
all through MainWindow methods). Segfaults intermittently in MainWindow._problems -> QListWidget.clear()."""
import sys
sys.path.insert(0, ".")
from PySide6.QtWidgets import QApplication
from app.window import MainWindow
from app.tests.test_gui_workflow import show
app = QApplication.instance() or QApplication([])
w = show(MainWindow())
for k in ["V", "R", "C", "GND", "SW"]: w.place(k, 40 + 100 * len(w.doc.parts), 120)
for a, b in (("V1.p", "R1.a"), ("R1.b", "C1.a"), ("C1.b", "GND1.g"), ("V1.n", "GND1.g"), ("C1.a", "SW1.a")):
    with w.hist.change(w.doc): w.doc.connect(a, b)
    w.refresh()
w.sel = "C1"; w.rotate_selected(); w.edit_param("R1", "R", "2k")
for cyc in range(int(sys.argv[1]) if len(sys.argv) > 1 else 200):
    while w.hist.can_undo(): w.undo()
    while w.hist.can_redo(): w.redo()
    print(cyc, flush=True) if cyc % 20 == 0 else None
print("no crash")
