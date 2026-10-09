"""Stress: repeated MainWindow.refresh() (what every edit/undo does) on a document with problems.
argv[1]: 'refresh' = call refresh only; 'undo' = undo/redo ping-pong of one rotate."""
import sys, gc
sys.path.insert(0, ".")
from PySide6.QtWidgets import QApplication
from app.window import MainWindow
from app.tests.test_gui_workflow import show
app = QApplication.instance() or QApplication([])
w = show(MainWindow())
for k in ["V", "R", "C", "SW", "L"]: w.place(k, 40 + 80 * len(w.doc.parts), 120)
w.sel = "R1"; w.rotate_selected()
for i in range(int(sys.argv[2]) if len(sys.argv) > 2 else 2000):
    if sys.argv[1] == "refresh": w.refresh()
    else: w.undo(); w.redo()
    if i % 100 == 0: print(i, flush=True)
print("no crash")
