"""Minimal repro search for the segfault in MainWindow._problems (QListWidget.clear) during redo."""
import sys
sys.path.insert(0, ".")
from PySide6.QtWidgets import QApplication
from app.window import MainWindow
from app.tests.test_gui_workflow import show, place

app = QApplication.instance() or QApplication([])
mode = sys.argv[1] if len(sys.argv) > 1 else "gui"
w = show(MainWindow()) if mode == "gui" else MainWindow()
for k in ["V", "R", "C", "GND", "SW"]:
    place(w, k, 40 + 80 * len(w.doc.parts), 120) if mode == "gui" else w.place(k, 40 + 80 * len(w.doc.parts), 120)
n = 0
while w.hist.can_undo(): w.undo(); n += 1
print("undone", n, flush=True)
while w.hist.can_redo(): w.redo(); print("redo ok", len(w.doc.parts), flush=True)
print("no crash")
