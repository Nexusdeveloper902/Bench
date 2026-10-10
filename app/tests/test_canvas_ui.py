"""T9: canvas navigation, overlays, placement / editing by mouse and keys, badges, placeholders, and the simulation
controls (Run / Re-run / Reset states, blocked run) on the real window, real catalog and real engine. Input goes
through QTest at real viewport positions (mapFromScene)."""
import pytest
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDropEvent, QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsSimpleTextItem, QGraphicsPathItem

from app.canvas import PIN_HIT, ZOOM_MAX, ZOOM_MIN
from app.catalog import CATALOG
from app.library import MIME
from app.model import Document
from app.window import MainWindow, part_rect, pin_pos

ESP = "ESP32_DEVKITC"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    w = MainWindow(); w.show(); w.activateWindow(); QTest.qWaitForWindowActive(w); w.canvas.setFocus()   # shortcuts need it
    yield w
    w.hide(); w.close(); w.deleteLater()


def vp(w, pt):
    return w.canvas.mapFromScene(pt if isinstance(pt, QPointF) else QPointF(*pt))


def click(w, pt, button=Qt.LeftButton, off=QPoint(0, 0)):
    QTest.mouseClick(w.canvas.viewport(), button, Qt.NoModifier, vp(w, pt) + off)


def drag(w, a, b, button=Qt.LeftButton):
    a, b = [(p.x(), p.y()) if isinstance(p, QPointF) else p for p in (a, b)]
    v = w.canvas.viewport()
    QTest.mousePress(v, button, Qt.NoModifier, vp(w, a))
    QTest.mouseMove(v, vp(w, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)))
    QTest.mouseMove(v, vp(w, b))
    QTest.mouseRelease(v, button, Qt.NoModifier, vp(w, b))


def place(w, kind, x, y):
    w.library.card(kind).click(); click(w, (x + 5, y + 5))
    return next(reversed(w.doc.parts))


def hover(w, pt):
    """A mouse move over scene point `pt`, delivered straight to the viewport (QTest.mouseMove can be swallowed when an
    older test window left the cursor at the same global position)."""
    p = QPointF(vp(w, pt)); v = w.canvas.viewport()
    QApplication.sendEvent(v, QMouseEvent(QEvent.MouseMove, p, QPointF(v.mapToGlobal(p.toPoint())), Qt.NoButton,
                                          Qt.NoButton, Qt.NoModifier))


def drop(w, comp_id, pt):
    """What Qt delivers for a drag from a library card: enter, move, drop (mime = component id)."""
    m = QMimeData(); m.setData(MIME, comp_id.encode())
    for t in (QDropEvent.Type.DragEnter, QDropEvent.Type.DragMove, QDropEvent.Type.Drop):
        QApplication.sendEvent(w.canvas.viewport(), QDropEvent(QPointF(vp(w, pt)), Qt.CopyAction, m, Qt.LeftButton,
                                                                Qt.NoModifier, t))


def in_view(w, rect):
    return w.canvas.viewport().rect().contains(w.canvas.mapFromScene(rect).boundingRect())


# ---------------------------------------------------------------- zoom, fit, pan
def test_zoom_keeps_hit_testing(win):
    """At 200 % a real click on two pins creates the wire; the pin hit radius stays PIN_HIT screen pixels."""
    w = win
    a, b = place(w, "R", 60, 60), place(w, "R", 180, 60)
    w.canvas.set_zoom(2.0, QPoint(0, 0))
    assert w.canvas.zoom == 2.0 and w.canvas.zoom_ctl.pct.text() == "200%"
    assert w.doc.parts[a]["x"] == 60 and w.doc.parts[b]["x"] == 180            # zoom is a view transform only
    p, q = pin_pos(w.doc, f"{a}.b"), pin_pos(w.doc, f"{b}.a")
    assert in_view(w, part_rect(w.doc.parts[a])) and in_view(w, part_rect(w.doc.parts[b]))
    click(w, p, off=QPoint(PIN_HIT + 2, 0))                                    # just outside: no wiring started
    assert w.pending_pin is None
    click(w, p, off=QPoint(PIN_HIT - 4, 0))                                    # just inside: starts a wire
    assert w.pending_pin == f"{a}.b"
    click(w, q)
    assert w.doc.wires == [(f"{a}.b", f"{b}.a")] and w.pending_pin is None


def test_zoom_range_keys_wheel_and_anchor(win):
    w = win; c = w.canvas
    place(w, "R", 100, 100); place(w, "R", 1500, 1000)                         # a big enough scene to scroll in
    for _ in range(30): QTest.keyClick(c, Qt.Key_Equal, Qt.ControlModifier)
    assert c.zoom == ZOOM_MAX and c.zoom_ctl.pct.text() == "400%"
    for _ in range(40): QTest.keyClick(c, Qt.Key_Minus, Qt.ControlModifier)
    assert c.zoom == ZOOM_MIN and c.zoom_ctl.pct.text() == "25%"
    c.zoom_ctl.plus.click(); c.zoom_ctl.plus.click(); assert c.zoom > ZOOM_MIN
    c.set_zoom(1.0)
    anchor = QPoint(300, 200); before = c.mapToScene(anchor)                   # Ctrl+wheel zooms around the cursor
    ev = QWheelEvent(QPointF(anchor), QPointF(c.viewport().mapToGlobal(anchor)), QPoint(0, 0), QPoint(0, 120),
                     Qt.NoButton, Qt.ControlModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(c.viewport(), ev)
    assert c.zoom > 1.1
    after = c.mapToScene(anchor)
    assert abs(after.x() - before.x()) < 1.5 and abs(after.y() - before.y()) < 1.5


def test_fit_shows_all_parts(win):
    w = win; c = w.canvas
    a, b = place(w, "R", 60, 60), place(w, "LED", 1200, 700)
    c.set_zoom(4.0)
    assert not (in_view(w, part_rect(w.doc.parts[a])) and in_view(w, part_rect(w.doc.parts[b])))
    QTest.keyClick(c, Qt.Key_0, Qt.ControlModifier)
    assert ZOOM_MIN <= c.zoom < 1.0
    assert in_view(w, part_rect(w.doc.parts[a])) and in_view(w, part_rect(w.doc.parts[b]))
    c.set_zoom(3.0); c.zoom_ctl.fit.click()
    assert in_view(w, part_rect(w.doc.parts[a])) and in_view(w, part_rect(w.doc.parts[b]))


@pytest.mark.parametrize("how", ["middle", "space"])
def test_pan_moves_view_not_parts(win, how):
    w = win; c = w.canvas
    a, b = place(w, "R", 60, 60), place(w, "R", 900, 500)
    c.set_zoom(2.0)
    parts = {n: (d["x"], d["y"]) for n, d in w.doc.parts.items()}
    hb, vb = c.horizontalScrollBar(), c.verticalScrollBar()
    assert hb.maximum() > 100 and vb.maximum() > 100
    h0, v0 = hb.value(), vb.value()
    v = c.viewport(); start, end = QPoint(300, 300), QPoint(200, 240)          # empty canvas: drag left/up
    if how == "space":
        QTest.keyPress(c, Qt.Key_Space)
        QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, start)
    else:
        QTest.mousePress(v, Qt.MiddleButton, Qt.NoModifier, start)
    assert v.cursor().shape() == Qt.ClosedHandCursor
    QTest.mouseMove(v, QPoint(250, 270)); QTest.mouseMove(v, end)
    if how == "space":
        QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, end); QTest.keyRelease(c, Qt.Key_Space)
    else:
        QTest.mouseRelease(v, Qt.MiddleButton, Qt.NoModifier, end)
    assert (hb.value() - h0, vb.value() - v0) == (100, 60)                     # content followed the mouse
    assert {n: (d["x"], d["y"]) for n, d in w.doc.parts.items()} == parts       # no part moved, nothing selected
    assert w.sel == b                                                           # (b was placed last; panning never selects)
    assert v.cursor().shape() == Qt.ArrowCursor


def test_scene_rect_grows_with_the_parts(win):
    w = win
    a = place(w, "R", 60, 60)
    far = place(w, "R", 2400, 1600)
    r = part_rect(w.doc.parts[far])
    assert w.canvas.sceneRect().contains(r.adjusted(-200, -200, 200, 200))
    w.select(far); w.delete_selected()
    assert not w.canvas.sceneRect().contains(r)                                # shrinks again


# ---------------------------------------------------------------- place, move, rotate, delete
def test_place_move_rotate_delete_by_mouse_and_keys(win):
    w = win
    n = place(w, "R", 100, 100)
    assert w.sel == n and w.tool is None and (w.doc.parts[n]["x"], w.doc.parts[n]["y"]) == (100, 100)
    x0, y0 = 100, 100
    drag(w, (x0 + 30, y0 + 15), (x0 + 30 + 60, y0 + 15 + 40))                  # move, snapped to the 20 px grid
    assert (w.doc.parts[n]["x"], w.doc.parts[n]["y"]) == (160, 140)
    QTest.keyClick(w.canvas, Qt.Key_R)
    assert w.doc.parts[n]["rot"] == 90
    w.canvas.part_bar.rotate.click(); assert w.doc.parts[n]["rot"] == 180      # the floating affordance does the same
    QTest.keyClick(w.canvas, Qt.Key_Delete)
    assert n not in w.doc.parts and "Deleted" in w.status.text()
    QTest.keyClick(w.canvas, Qt.Key_Z, Qt.ControlModifier)
    assert n in w.doc.parts and w.doc.parts[n]["rot"] == 180
    w.select(n); assert w.canvas.part_bar.isVisible()
    w.canvas.part_bar.delete.click(); assert n not in w.doc.parts and not w.canvas.part_bar.isVisible()


def test_part_toolbar_follows_selection_and_esc_order(win):
    w = win
    n = place(w, "R", 100, 120)
    assert w.canvas.part_bar.isVisible()
    r = w.canvas.mapFromScene(part_rect(w.doc.parts[n])).boundingRect()
    assert w.canvas.part_bar.geometry().bottom() < r.top()                     # above the part
    w.library.card("C").click(); assert w.tool == "C"
    QTest.keyClick(w.canvas, Qt.Key_Escape)
    assert w.tool is None and w.sel == n                                       # 1st Esc: armed placement only
    click(w, pin_pos(w.doc, f"{n}.a")); assert w.pending_pin == f"{n}.a"
    QTest.keyClick(w.canvas, Qt.Key_Escape)
    assert w.pending_pin is None and w.sel == n                                # 2nd: pending wire
    QTest.keyClick(w.canvas, Qt.Key_Escape)
    assert w.sel is None and not w.canvas.part_bar.isVisible()                 # last: selection


def test_empty_hint_and_zoom_control_overlay(win):
    w = win; c = w.canvas
    assert c.hint.isVisible() and c.zoom_ctl.isVisible() and c.zoom_ctl.pct.text() == "100%"
    assert w.results.parentWidget().height() == 248 and c.width() >= 670 and c.height() >= 470   # the design's space
    assert c.zoom_ctl.geometry().left() == 12 and c.zoom_ctl.geometry().bottom() == c.viewport().height() - 12 - 1
    assert w.canvas.hint.testAttribute(Qt.WA_TransparentForMouseEvents)        # clicks pass through to the canvas
    place(w, "R", 100, 100)
    assert not c.hint.isVisible()
    w._reset(Document()); assert c.hint.isVisible()


def test_drop_from_library(win):
    w = win
    drop(w, "LED", (203, 141))
    n = next(iter(w.doc.parts))
    assert w.doc.parts[n]["kind"] == "LED" and (w.doc.parts[n]["x"], w.doc.parts[n]["y"]) == (200, 140) and w.sel == n
    drop(w, "NOPE", (203, 241))                                                # unknown id: ignored
    assert len(w.doc.parts) == 1


def test_armed_ghost_and_pin_tooltip(win):
    w = win
    w.library.card("R").click()
    hover(w, (203, 143))
    assert w.canvas.tip.isVisible() and "Click to place Resistor" in w.canvas.tip.text() and "Esc" in w.canvas.tip.text()
    assert any(i.opacity() < 1 for i in w.canvas.sc.items())                   # the ghost follows the cursor
    w.set_tool(None); n = place(w, "R", 100, 100)
    hover(w, pin_pos(w.doc, f"{n}.a"))
    assert w.canvas.tip.isVisible() and w.canvas.tip.text() == "a"
    hover(w, (500, 400))
    assert not w.canvas.tip.isVisible()


def test_disconnect_all(win):
    w = win; w.load_led_example()
    assert w.doc.wired("R1") and len(w.doc.wires) == 4
    m = w.canvas.part_menu("R1")
    assert [a.text() for a in m.actions()] == ["Rotate", "Delete", "Disconnect all"]
    next(a for a in m.actions() if a.text() == "Disconnect all").trigger()
    assert not w.doc.wired("R1") and "R1" in w.doc.parts and len(w.doc.wires) == 2    # only its wires went
    assert not next(a for a in w.canvas.part_menu("R1").actions() if a.text() == "Disconnect all").isEnabled()
    QTest.keyClick(w.canvas, Qt.Key_Z, Qt.ControlModifier); assert len(w.doc.wires) == 4
    click(w, part_rect(w.doc.parts["D1"]).center(), Qt.RightButton)            # right-click selects and opens the menu
    assert w.sel == "D1"
    if (pop := QApplication.activePopupWidget()) is not None: pop.close()


# ---------------------------------------------------------------- badges and placeholders
def test_badge_on_interface_only_part(win):
    w = win
    esp = CATALOG.get(ESP); assert esp.readiness == "interface" and esp.limitations
    place(w, ESP, 100, 40)
    tips = {i.toolTip() for i in w.canvas.sc.items() if i.toolTip()}
    assert tips == {esp.limitations[0]}
    badge = next(i for i in w.canvas.sc.items() if i.toolTip())
    assert badge.text == "Interface only" and badge.key == "interface"
    place(w, "R", 400, 100)
    assert len([i for i in w.canvas.sc.items() if i.toolTip()]) == 1           # a simulated part gets none


def placeholder_doc(wired=True):
    d = Document.led_example().to_dict()
    d["components"]["X1"] = {"type": "FUTURE_RELAY", "version": 3, "params": {"coil": "5"}}
    d["layout"]["X1"] = {"x": 300, "y": 300, "rotation": 0}
    if wired: d["connections"] += [["X1.COM", "R1.b"], ["X1.NO", "D1.a"]]
    return Document.from_dict(d)


def test_placeholder_drawn_and_movable(win):
    w = win; w._reset(placeholder_doc(wired=False))
    w.draw()
    dashed = [i for i in w.canvas.sc.items() if isinstance(i, QGraphicsPathItem) and i.pen().style() == Qt.DashLine]
    texts = {i.text() for i in w.canvas.sc.items() if isinstance(i, QGraphicsSimpleTextItem)}
    assert dashed and "?" in texts and "FUTURE_RELAY  v3" in texts
    badge = next(i for i in w.canvas.sc.items() if i.toolTip())
    assert badge.text == "Placeholder" and "not in the catalog" in badge.toolTip()
    x, y = w.doc.parts["X1"]["x"], w.doc.parts["X1"]["y"]
    drag(w, part_rect(w.doc.parts["X1"]).center(), part_rect(w.doc.parts["X1"]).center() + QPointF(80, 40))
    assert (w.doc.parts["X1"]["x"], w.doc.parts["X1"]["y"]) == (x + 80, y + 40)    # moves, snapped
    assert w.doc.parts["X1"]["kind"] == "FUTURE_RELAY" and w.doc.parts["X1"]["params"] == {"coil": "5"}
    w.select("X1"); QTest.keyClick(w.canvas, Qt.Key_R); assert w.doc.parts["X1"]["rot"] == 90
    w.undo(); w.undo(); assert (w.doc.parts["X1"]["x"], w.doc.parts["X1"]["y"]) == (x, y)


def test_placeholder_pins_wire_by_mouse(win):
    w = win; w._reset(placeholder_doc(wired=False))
    w.doc.parts["X1"]["placeholder"]["pins"] = ["COM", "NO"]                   # the pins a saved file would reference
    click(w, pin_pos(w.doc, "X1.COM")); click(w, pin_pos(w.doc, "R1.b"))
    assert ("X1.COM", "R1.b") in w.doc.wires


# ---------------------------------------------------------------- simulation controls
def buttons(w):
    return tuple(b.isEnabled() for b in (w.b_run, w.b_rerun, w.b_reset))


def test_run_controls_follow_the_state_table(win):
    w = win; w.load_led_example()
    assert w.state_lbl.text() == "Idle" and buttons(w) == (True, False, False)           # idle, valid
    assert w.b_run.property("primary") and w.b_run.text() == "Run"
    w.tstop.setFocus(); w.tstop.selectAll(); QTest.keyClicks(w.tstop, "5xyz")
    assert w.tstop.property("invalid") and buttons(w) == (False, False, False)           # invalid field
    assert w.notice.isVisible() and w.notice.property("kind") == "err"
    w.tstop.selectAll(); QTest.keyClicks(w.tstop, "5m"); QTest.keyClick(w.tstop, Qt.Key_Return)
    assert not w.tstop.property("invalid") and buttons(w) == (True, False, False) and not w.notice.isVisible()
    w.b_run.click()                                                                       # the button runs
    assert w.state == "completed" and w.state_lbl.text() == "Completed" and buttons(w) == (False, True, True)
    assert w.results.pages.currentWidget().isVisible() and w.plotw.isVisible() and w.probe.isVisible()
    w.set_state("running")
    assert buttons(w) == (False, False, False) and not w.tstop.isEnabled() and w.state_lbl.text() == "Running"
    w.set_state("completed")
    w.doc.set_param("R1", "R", "470"); w.refresh()                                        # stale: results dropped
    assert w.state == "idle" and w.state_lbl.text() == "Out of date" and buttons(w) == (False, True, True)
    assert w.notice.isVisible() and "out of date" in w.notice.text().lower() and w.results.stale_banner.isVisible()
    w.b_rerun.click()
    assert w.state == "completed" and buttons(w) == (False, True, True)
    w.b_reset.click()
    assert w.state == "idle" and w.result is None and w.state_lbl.text() == "Idle" and buttons(w) == (True, False, False)


def test_run_blocked_by_wired_unsupported_shows_failure_without_plot(win):
    """A4/A1: the pre-check refuses the run: state `blocked` (not `failed`), no plot, the message names the part."""
    w = win; w.load_led_example()
    u1 = place(w, ESP, 480, 40)
    click(w, pin_pos(w.doc, "R1.b")); click(w, pin_pos(w.doc, f"{u1}.3V3"))
    assert ("R1.b", f"{u1}.3V3") in w.doc.wires or (f"{u1}.3V3", "R1.b") in w.doc.wires
    w.b_run.click()
    assert w.state == "blocked" and w.result.status == "blocked" and not w.result.ok
    assert w.state_lbl.text() == "Blocked" and buttons(w) == (False, False, False)
    assert w.err_title.text() == "Run blocked" and "U1" in w.err.text() and "U1" in w.result.message
    assert not w.plotw.isVisible() and not w.ax.lines and w.probe.count() == 0 and w.stats.text() == ""
    assert w.results.noplot.isVisible() and "did not start" in w.results.noplot.text()
    assert w.notice.isVisible() and "U1" in w.notice.text()
    assert w.problems.count() >= 1 and any("NOT-SIMULATED" in w.problems.item(i).text() for i in range(w.problems.count()))
    w.canvas.part_menu(u1).actions()[2].trigger()                                        # fix it: Disconnect all
    assert w.state == "idle" and w.state_lbl.text() == "Out of date" and buttons(w) == (False, True, True)
    w.b_rerun.click()
    assert w.state == "completed" and "not simulated: U1" in w.result.message
    assert "not simulated: U1" in w.results.msg.text() and w.results.ok_banner.property("kind") == "warn"
    assert w.result.series["@d1[id]"][-1][1] == pytest.approx(9.1772e-3, rel=1e-3)


def test_results_panel_states_and_details(win):
    w = win; r = w.results
    assert r.pages.currentIndex() == 0 and "No results yet" in r.empty_title.text() and not w.probe.isVisible()
    w.load_led_example(); w.a_run.trigger()
    assert r.pages.currentIndex() == 2 and w.probe.isVisible() and w.details_btn.text() == "Show details"
    assert "ok, 511 points" in r.msg.text() and r.ok_banner.property("kind") == "ok"
    assert r.time_lbl.text() == "5.00 / 5.00 ms"
    w.set_cursor_time(2.5e-3)
    assert r.time_lbl.text() == "2.50 / 5.00 ms" and w.readout.text().startswith("t = 2.5 ms")
    assert w.cur_line.get_xdata()[0] == pytest.approx(2.5e-3)
    w.details_btn.click()
    assert r.pages.currentIndex() == 4 and w.details.isVisible() and w.details_btn.text() == "Hide details"
    w.details_btn.click(); assert r.pages.currentIndex() == 2
    w.set_state("running"); r.set_view(None, "running"); assert r.pages.currentIndex() == 1
    w.a_reset.trigger(); assert r.pages.currentIndex() == 0


def test_problems_rows_show_code_message_and_select_part(win):
    w = win; w.load_led_example()
    place(w, ESP, 480, 40)
    assert w.problems.count() == 1 and w.problems_panel.pill.text() == "1"
    it = w.problems.item(0)
    assert it.data(Qt.UserRole + 1)["code"] == "NOT-SIMULATED" and it.data(Qt.UserRole + 1)["severity"] == "warning"
    assert w.problems.itemDelegate().sizeHint(None, w.problems.indexFromItem(it)).height() > 40
    w.select(None); it = w.problems.item(0)
    QTest.mouseClick(w.problems.viewport(), Qt.LeftButton, Qt.NoModifier, w.problems.visualItemRect(it).center())
    assert w.sel == "U1"
    w._reset(Document()); assert w.problems.count() == 0 and w.problems_panel.stack.currentWidget() is w.problems_panel.empty
