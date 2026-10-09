"""GUI: mixed undo/redo across every edit type, invariants after each step; stale results; pending edits at Run."""
import sys, random
sys.path.insert(0, ".")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QFormLayout
from app.window import MainWindow, pin_pos, wire_pts, part_rect
from app.tests.test_gui_workflow import show, click, place, select, wire, set_value, field, type_into, pick
from app.model import Document

app = QApplication.instance() or QApplication([])
bugs = []


def check(cond, msg):
    if not cond: bugs.append(msg); print("BUG:", msg)


def invariants(w, tag):
    doc = w.doc
    for a, b in doc.wires:
        for p in (a, b):
            n = p.partition(".")[0]
            check(n in doc.parts and p in doc.pins(n), f"{tag}: dangling wire {a}-{b}")
        check(a != b, f"{tag}: self wire {a}")
    check(len(set(map(frozenset, doc.wires))) == len(doc.wires), f"{tag}: duplicate wires {doc.wires}")
    check(w.sel is None or w.sel in doc.parts, f"{tag}: stale selection {w.sel}")
    check(w.sel_wire is None or w.sel_wire in doc.wires, f"{tag}: stale wire selection {w.sel_wire}")
    # inspector header names the selection
    # (disabled: peeking inspector wrappers triggers the shiboken segfault, see a3g) #    txt = w.form.itemAt(0).widget().text() if w.form.rowCount() else ""
    # (disabled: peeking inspector wrappers triggers the shiboken segfault, see a3g) #    if w.sel: check(f"<b>{w.sel}</b>" in txt, f"{tag}: inspector shows {txt!r} for sel {w.sel}")
    check(w.a_undo.isEnabled() == w.hist.can_undo() and w.a_redo.isEnabled() == w.hist.can_redo(),
          f"{tag}: undo/redo enabled state wrong")


w = show(MainWindow())
states = [w.doc.to_dict()]
def snap(tag):
    invariants(w, tag); states.append(w.doc.to_dict())

v = place(w, "V", 40, 120); snap("place V")
r = place(w, "R", 160, 40); snap("place R")
c = place(w, "C", 320, 120); snap("place C")
g = place(w, "GND", 220, 300); snap("place GND")
s = place(w, "SW", 400, 260); snap("place SW")
wire(w, "V1.p", "R1.a"); snap("wire1")
wire(w, "R1.b", "C1.a"); snap("wire2")
wire(w, "C1.b", "GND1.g"); snap("wire3")
wire(w, "V1.n", "GND1.g"); snap("wire4")
wire(w, "C1.a", "SW1.a"); snap("wire5")
select(w, "C1"); QTest.keyClick(w.canvas, Qt.Key_R); snap("rotate C")
set_value(w, "R1", "Resistance", "2k"); snap("param R")
select(w, "SW1"); cb = next(w.form.itemAt(i, QFormLayout.FieldRole).widget() for i in range(w.form.rowCount())
                           if isinstance(w.form.itemAt(i, QFormLayout.FieldRole) and w.form.itemAt(i, QFormLayout.FieldRole).widget(), QComboBox))
cb.setCurrentText("closed"); QTest.qWait(10); snap("choice SW")
select(w, "V1"); cb = next(w.form.itemAt(i, QFormLayout.FieldRole).widget() for i in range(w.form.rowCount())
                          if isinstance(w.form.itemAt(i, QFormLayout.FieldRole) and w.form.itemAt(i, QFormLayout.FieldRole).widget(), QComboBox))
cb.setCurrentText("PULSE"); QTest.qWait(10); snap("mode V")
# move SW
cpt = part_rect(w.doc.parts["SW1"]).center(); vp = w.canvas.viewport()
QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, w.canvas.mapFromScene(cpt))
QTest.mouseMove(vp, w.canvas.mapFromScene(cpt.x() + 80, cpt.y()))
QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, w.canvas.mapFromScene(cpt.x() + 80, cpt.y())); snap("move SW")
# right-click disconnect a pin
click(w, pin_pos(w.doc, "SW1.a"), Qt.RightButton); snap("disconnect SW1.a")
# delete a wire by selecting it
a, b = w.doc.wires[0]; pts = wire_pts(w.doc, a, b); mid = (pts[0] + pts[1]) / 2
click(w, mid); check(w.sel_wire == (a, b), f"wire select failed {w.sel_wire}")
QTest.keyClick(w.canvas, Qt.Key_Delete); snap("delete wire")
select(w, "SW1"); QTest.keyClick(w.canvas, Qt.Key_Delete); snap("delete SW")
final = w.doc.to_dict()
# select R1 then undo all the way, past empty
select(w, "R1")
n = 0
while w.hist.can_undo():
    w.undo(); n += 1; invariants(w, f"undo#{n}")
    exp = states[-1 - n]
    check(w.doc.to_dict()["components"] == exp["components"] and w.doc.to_dict()["connections"] == exp["connections"],
          f"undo#{n}: state mismatch vs recorded")
print("undo steps", n, "edits", len(states) - 1)
check(n == len(states) - 1, f"undo count {n} != edits {len(states)-1}")
check(not w.doc.parts, "doc not empty after undo-all")
w.undo(); w.undo(); invariants(w, "undo past empty")
k = 0
while w.hist.can_redo():
    w.redo(); k += 1; invariants(w, f"redo#{k}")
check(w.doc.to_dict()["components"] == final["components"] and w.doc.to_dict()["connections"] == final["connections"],
      "redo-all != final")
check(w.doc.to_dict()["layout"] == final["layout"], "redo-all layout != final")

# random fuzz of edits/undo/redo
random.seed(1)
for i in range(300):
    op = random.choice(["place", "wire", "rot", "del", "undo", "undo", "redo", "move"])
    parts = list(w.doc.parts)
    try:
        if op == "place":
            place(w, random.choice(["R", "C", "L", "V", "I", "D", "LED", "SW", "GND"]),
                  random.randrange(2, 40) * 20, random.randrange(2, 30) * 20)
        elif op == "wire" and parts:
            pins = [p for n in parts for p in w.doc.pins(n)]
            wire(w, random.choice(pins), random.choice(pins))
        elif op == "rot" and parts:
            w.sel = random.choice(parts); w.rotate_selected()
        elif op == "del" and parts:
            w.sel = random.choice(parts); w.delete_selected()
        elif op == "move" and parts:
            n_ = random.choice(parts); w.hist.begin(w.doc); w.doc.parts[n_]["x"] += 20; w.hist.end(w.doc); w.refresh()
        elif op == "undo": w.undo()
        elif op == "redo": w.redo()
    except Exception as e:
        check(False, f"fuzz {i} {op}: exception {type(e).__name__}: {e}")
    invariants(w, f"fuzz{i}:{op}")
    w.pending_pin = None

# ---- stale results ----
w2 = show(MainWindow()); w2._reset(Document.led_example()); w2.a_run.trigger()
check(w2.state == "completed", "led example run failed")
type_into(w2.tstop, "1m")
check(w2.result is None, f"STALE: stop time changed 5m->1m after run but waveform/result kept (state={w2.state}, "
      f"plotted t_end={w2.t_rng[1]})")
w2.a_run.trigger()
# pending inspector edit (typed, no Enter) then F5/Run
select(w2, "R1"); ed = field(w2, "Resistance"); ed.setFocus(); ed.selectAll(); QTest.keyClicks(ed, "1k")
w2.a_run.trigger()
ran_r = w2.doc.parts["R1"]["params"]["R"]
i_led = w2.result.stats("I(D1)")["final"] if w2.result and w2.result.ok else None
check(ran_r == "1k", f"PENDING EDIT: typed R=1k (no Enter) then Run -> simulated with R={ran_r}, I(D1)={i_led}")
w2.canvas.setFocus(); QTest.qWait(10)
print("after focus-out: R =", w2.doc.parts["R1"]["params"]["R"], "result", w2.result and w2.result.ok, w2.state)

# failed run, then fix: state should not stick in running/failed
w3 = show(MainWindow()); place(w3, "R", 100, 100); w3.a_run.trigger()
check(w3.state == "failed", f"bad circuit state {w3.state}")
w3.a_run.trigger(); w3.a_run.trigger()
check("running" not in w3.state_lbl.text(), "state label stuck in running")
# run twice rapidly on good circuit: identical
w2.a_run.trigger(); a = list(w2.result.series["v(n2)"]); w2.a_run.trigger(); b = list(w2.result.series["v(n2)"])
check(a == b, "rerun not identical")
# viewer: picked signal no longer exists after loading another circuit
pick(w2, "V(D1)"); w2._reset(Document.blink_example()); w2.a_run.trigger()
print("probe after load other:", w2.probe.currentText(), "want", w2.want)
w4 = show(MainWindow()); place(w4, "R", 100, 100)  # want refers to nothing
w2._reset(Document()); place(w2, "V", 40, 120); place(w2, "R", 160, 40); place(w2, "GND", 220, 300)
wire(w2, "V1.p", "R1.a"); wire(w2, "R1.b", "GND1.g"); wire(w2, "V1.n", "GND1.g")
w2.a_run.trigger(); print("probe now", w2.probe.currentText(), w2.ax.get_title())
check(w2.ax.get_title() == w2.probe.currentText(), "plot title != selected probe")
print("\n".join(["", f"{len(bugs)} problems"] + bugs[:40]))
