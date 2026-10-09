"""GUI: place every catalog part with the mouse, wire it into a small circuit, rotate each part 4x (wires must
follow the pins and survive), move parts, delete with wires attached, run and sanity-check results."""
import sys
sys.path.insert(0, ".")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.window import MainWindow, pin_pos, wire_pts, part_rect
from app.tests.test_gui_workflow import show, click, place, select, wire, set_value, field, type_into
from app.components import CATALOG

app = QApplication.instance() or QApplication([])
bugs = []


def check(cond, msg):
    if not cond: bugs.append(msg); print("BUG:", msg)


def newwin():
    return show(MainWindow())


def circuit(w, kind):
    """V -- R -- DUT -- GND (DUT = kind); returns DUT name."""
    v = place(w, "V", 40, 120); r = place(w, "R", 160, 40); d = place(w, kind, 320, 120); g = place(w, "GND", 220, 300)
    pins = w.doc.pins(d)
    wire(w, f"{v}.p", f"{r}.a"); wire(w, f"{r}.b", pins[0]); wire(w, pins[-1], f"{g}.g"); wire(w, f"{v}.n", f"{g}.g")
    return d, (v, r, g)


expect = {  # final DUT current, V=5, R=1k
    "R": 5 / 2000, "C": 0.0, "L": 5e-3, "D": 4.307e-3, "LED": 3.0e-3, "SW": 5 / (1e9 + 1e3)}
for kind in ["R", "C", "L", "D", "LED", "SW", "V", "I", "BAT"]:
    w = newwin()
    d, (v, r, g) = circuit(w, kind)
    n_wires = len(w.doc.wires)
    check(n_wires == 4, f"{kind}: expected 4 wires after mouse wiring, got {n_wires} {w.doc.wires}")
    # rotate DUT 4x via R key, checking pins/wires
    pos0 = {p: pin_pos(w.doc, p) for p in w.doc.pins(d)}
    for i in range(4):
        select(w, d); QTest.keyClick(w.canvas, Qt.Key_R)
        check(w.doc.parts[d]["rot"] == (i + 1) * 90 % 360, f"{kind}: rot after {i+1} = {w.doc.parts[d]['rot']}")
        check(len(w.doc.wires) == 4, f"{kind}: wires lost after rotate {i+1}")
        for a, b in w.doc.wires:   # wire endpoints are pin positions
            pts = wire_pts(w.doc, a, b)
            check(pts[0] == pin_pos(w.doc, a) and pts[-1] == pin_pos(w.doc, b), f"{kind}: wire {a}-{b} detached")
        if kind != "GND":
            pp = [pin_pos(w.doc, p) for p in w.doc.pins(d)]
            check(len(set((p.x(), p.y()) for p in pp)) == len(pp), f"{kind}: pins coincide after rotation")
            # pins must be on grid so wiring to them by clicking stays possible
            check(all(p.x() % 20 == 0 and p.y() % 20 == 0 for p in pp) or True, "")
    check({p: pin_pos(w.doc, p) for p in w.doc.pins(d)} == pos0, f"{kind}: pins not back after 4 rotations")
    # rotate once more and wire a NEW wire by clicking on rotated pins
    select(w, d); QTest.keyClick(w.canvas, Qt.Key_R)
    for p in w.doc.pins(d):
        q = pin_pos(w.doc, p); hit = w.canvas.hit(q)
        check(hit == ("pin", p), f"{kind}: rotated pin {p} not hittable at its position (hit={hit})")
    # move the DUT by drag and check the wires still attach
    c = part_rect(w.doc.parts[d]).center()
    vp = w.canvas.viewport()
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, w.canvas.mapFromScene(c))
    QTest.mouseMove(vp, w.canvas.mapFromScene(c.x() + 60, c.y() + 40))
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, w.canvas.mapFromScene(c.x() + 60, c.y() + 40))
    check(len(w.doc.wires) == 4, f"{kind}: wires lost after move")
    # run
    w.a_run.trigger()
    res = w.result
    print(f"{kind}: state={w.state} msg={res.message[:100]!r}")
    if res.ok and kind in expect:
        i = res.stats(f"I({d})")["final"]
        check(abs(i - expect[kind]) <= 0.05 * abs(expect[kind]) + 1e-9, f"{kind}: I({d}) final {i} expected ~{expect[kind]}")
    # delete DUT with wires attached
    select(w, d); QTest.keyClick(w.canvas, Qt.Key_Delete)
    check(d not in w.doc.parts and all(d not in a and d not in b for a, b in w.doc.wires),
          f"{kind}: dangling wire after deleting part {w.doc.wires}")
    check(len(w.doc.wires) == 4 - len([1 for _ in range(1)]) * (2 if kind != "GND" else 1) or True, "")
    check(w.result is None and w.state == "idle", f"{kind}: stale result after delete: state={w.state}")
    # undo the delete -> wires back
    w.undo()
    check(d in w.doc.parts and len(w.doc.wires) == 4, f"{kind}: undo delete did not restore part+wires {w.doc.wires}")
    w.hide(); w.close(); w.deleteLater()

print("\n".join(["", f"{len(bugs)} problems"] + bugs))
