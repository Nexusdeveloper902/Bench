"""GUI: open malformed files via File > Open (dialogs patched), NaN layout, viewer with 1 point / constant signal,
huge run (UI blocked time + memory)."""
import sys, os, json, tempfile, time, resource
sys.path.insert(0, ".")
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from app.window import MainWindow
from app.model import Document
from app.tests.test_gui_workflow import show, select

app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
shown = []
QMessageBox.critical = staticmethod(lambda *a, **k: shown.append(a[2]))
hooked = []
sys.excepthook = lambda t, v, tb: hooked.append(f"{t.__name__}: {v}")
base = Document.led_example().to_dict()

w = show(MainWindow())
for label, text in [("conn ints", json.dumps({**base, "connections": [[1, 2]]})),
                    ("deep nesting", "[" * 100000 + "]" * 100000),
                    ("NaN x", json.dumps({**base, "layout": {**base["layout"], "R1": {"x": float("nan"), "y": 0, "rotation": 0}}})),
                    ("x=1e308", json.dumps({**base, "layout": {**base["layout"], "R1": {"x": 1e308, "y": 0, "rotation": 0}}}))]:
    p = os.path.join(tmp, label.replace(" ", "_") + ".json"); open(p, "w").write(text)
    shown.clear(); hooked.clear()
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (p, ""))
    w.a_open.trigger(); app.processEvents()
    print(f"[{label}] error dialog={shown[:1]} uncaught={hooked[:1]} title={w.windowTitle()!r} parts={list(w.doc.parts)}")
    if "R1" in w.doc.parts and w.path == p:
        try:
            w.a_run.trigger(); print("   run:", w.state, w.result.message[:60])
            w.canvas.hit(w.canvas.mapToScene(10, 10)); w.draw()
            out = os.path.join(tmp, "resaved.json"); w.doc.save(out)
            print("   resaved contains NaN/Infinity (invalid strict JSON):", any(s in open(out).read() for s in ("NaN", "Infinity")))
        except Exception as e:
            print("   EXC", type(e).__name__, e)

# viewer: 1 point / constant signal
w._reset(Document.led_example()); w.doc.tstop, w.doc.tstep = "1e-9", "1e-9"; w.refresh()
w.a_run.trigger(); print("tstop=tstep run:", w.state, w.result.message, "points", len(w.view_data()[1]) if w.view_data() else None,
                         "ylabel", w.ax.get_ylabel())
w.set_cursor_time(0); print("   readout:", w.readout.text())
# GND-only signal / zero signal: plot V(D1) when everything is 0 (V=0)
w._reset(Document.led_example()); w.doc.parts["V1"]["params"]["dc"] = "0"; w.a_run.trigger()
w.probe.setCurrentText("V(n1)"); print("zero signal ylabel:", w.ax.get_ylabel(), "| stats:", w.stats.text(), "| ylim", w.ax.get_ylim())

# huge run: blocked UI time and memory
w._reset(Document.led_example()); w.doc.tstop, w.doc.tstep = "50", "10u"; w.refresh()
r0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
t = time.time(); w.a_run.trigger(); app.processEvents(); dt = time.time() - t
print(f"5e6-point run: UI blocked {dt:.1f}s, state={w.state}, peak RSS +{(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - r0) / 1024:.0f} MB")
t = time.time(); w.probe.setCurrentIndex(w.probe.count() - 1); app.processEvents(); w.plotw.draw(); print(f"   switch signal + redraw {time.time() - t:.1f}s")
