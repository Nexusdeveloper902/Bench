"""Screenshots at 1300x850 (run under xvfb-run -a): inspector clipping, duplicated Rotate/Delete, LED example fields.
Writes PNGs to argv[1] (default docs/review/attack/shots)."""
import sys, os
sys.path.insert(0, ".")
from PySide6.QtWidgets import QApplication, QLineEdit, QToolButton, QPushButton
from PySide6.QtTest import QTest
from app.window import MainWindow
from app.model import Document
from app.tests.test_gui_workflow import show, select, field

out = sys.argv[1] if len(sys.argv) > 1 else "docs/review/attack/shots"
os.makedirs(out, exist_ok=True)
app = QApplication.instance() or QApplication([])
w = show(MainWindow()); w.resize(1300, 850); QTest.qWait(200)
w._reset(Document.led_example()); QTest.qWait(100)
print("LED example fields: stop time =", repr(w.tstop.text()), "time step =", repr(w.tstep.text()))
w.grab().save(f"{out}/led_example.png")
d = Document(); d.add_part("C", 100, 100, C="10u"); d.add_part("V", 100, 250, mode="PULSE"); w._reset(d)
select(w, "C1"); QTest.qWait(100)
ed = field(w, "Capacitance")
fm = ed.fontMetrics()
print(f"C field: text={ed.text()!r} width={ed.width()} text px={fm.horizontalAdvance(ed.text())} "
      f"contentsRect={ed.contentsRect().width()}")
w.grab().save(f"{out}/inspector_C.png")
ed.grab().save(f"{out}/inspector_C_field.png")
for e in [x for x in w.findChildren(QLineEdit) if x.isVisible()]:
    print("  visible lineedit", repr(e.text()), "w", e.width(), "h", e.height(), "text px", e.fontMetrics().horizontalAdvance(e.text()),
          "font h", e.fontMetrics().height())
select(w, "V1"); QTest.qWait(100); w.grab().save(f"{out}/inspector_V_pulse.png")
names = [b.text() or (b.defaultAction() and b.defaultAction().text()) for b in w.findChildren(QToolButton) if b.isVisible()]
names += [b.text() for b in w.findChildren(QPushButton) if b.isVisible()]
print("visible buttons:", names)
