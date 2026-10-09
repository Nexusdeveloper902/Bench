"""Inspector value typed but not committed (no Enter) when the user presses Run (F5 / toolbar button)."""
import sys
sys.path.insert(0, ".")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton
from app.window import MainWindow
from app.model import Document
from app.tests.test_gui_workflow import show, select, field, click

app = QApplication.instance() or QApplication([])
for how in ("F5", "toolbar-click"):
    w = show(MainWindow()); w._reset(Document.led_example())
    select(w, "R1"); ed = field(w, "Resistance"); QTest.mouseClick(ed, Qt.LeftButton); QTest.qWait(20); print("focus before typing:", type(app.focusWidget()).__name__, app.focusWidget() is ed); ed.selectAll(); QTest.keyClicks(ed, "1k")
    if how == "F5":
        QTest.keyClick(ed, Qt.Key_F5)
    else:
        btn = next(b for b in w.findChildren(QToolButton) if b.defaultAction() is w.a_run)
        QTest.mouseClick(btn, Qt.LeftButton)
    print(how, "focus widget:", type(app.focusWidget()).__name__, "R in doc:", w.doc.parts["R1"]["params"]["R"],
          "| ran I(D1) =", round(w.result.stats("I(D1)")["final"] * 1e3, 3), "mA (330R -> 9.18 mA, 1k -> ~3 mA)",
          "| field shows:", ed.text())
    click(w, (900, 650))   # click empty canvas: focus leaves the field
    QTest.qWait(20)
    print("   after clicking canvas: R in doc:", w.doc.parts["R1"]["params"]["R"], "state:", w.state,
          "result:", None if w.result is None else "kept")
    w.hide(); w.close()
