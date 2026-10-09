"""Window pinned to 1300x850 (as on a 1300x850 screen / maximized): inspector rows for C and PULSE V."""
import sys, os
sys.path.insert(0, ".")
from PySide6.QtWidgets import QApplication, QLineEdit
from PySide6.QtTest import QTest
from app.window import MainWindow
from app.model import Document
from app.tests.test_gui_workflow import show, select
out = "docs/review/attack/shots"; os.makedirs(out, exist_ok=True)
app = QApplication.instance() or QApplication([])
w = show(MainWindow()); w.setFixedSize(1300, 850)
d = Document(); d.add_part("C", 100, 100, C="10u"); d.add_part("V", 100, 250, mode="PULSE"); w._reset(d)
for n in ("C1", "V1"):
    select(w, n); QTest.qWait(150)
    print(n, "window min height hint:", w.minimumSizeHint().height(), "size", w.size().toTuple())
    for e in [x for x in w.findChildren(QLineEdit) if x.isVisible() and x.width() > 100]:
        print("   field", repr(e.text()), "h", e.height(), "visible h", e.visibleRegion().boundingRect().height(),
              "font h", e.fontMetrics().height())
    w.grab().save(f"{out}/fixed_{n}.png")
