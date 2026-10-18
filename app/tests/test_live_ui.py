"""Live-run UI (task 9.5b) on a real MainWindow and real ngspice: Live / Pause / Stop, the inspector slider (range from
package data, G31), streamed plot with change ticks, cleanup on close, errors, edits and replay. Engine contract:
docs/plans/ngspice-library/LIVE.md; UI notes: HANDOFF.md "Task 9.5b"."""
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from app.catalog import CATALOG
from app.inspector import slider_map
from app.model import Document, value_at
from app.sim import run_document
from app.tests.test_gui_workflow import qapp, select, show, win  # noqa: F401
from app.tests.test_live import STIMULI, ldr_out
from app.window import MainWindow
from conftest import wait_for

TSTOP = 20e-3


def ldr_doc(kind="LDR"):
    """V1 5 V -> sensor a; sensor b -> R 10k -> ground. Laid out for the screenshot; output net is the sensor's b."""
    d = Document()
    v, s, r, g = d.add_part("V", 60, 120, dc="5"), d.add_part(kind, 220, 60), d.add_part("R", 380, 160, R="10k"), \
        d.add_part("GND", 240, 300)
    for a, b in ((f"{v}.p", f"{s}.a"), (f"{s}.b", f"{r}.a"), (f"{r}.b", f"{g}.g"), (f"{v}.n", f"{g}.g")):
        d.connect(a, b)
    d.tstop, d.tstep = "20m", "10u"
    return d, s


def start(w, kind="LDR", pace="Pace: 0.01×"):
    """Load the divider, pick the pace, press Live, plot the output, select the sensor (its sliders appear)."""
    d, s = ldr_doc(kind)
    w._reset(d)
    w.pace.setCurrentText(pace)
    QTest.mouseClick(w.b_live, Qt.LeftButton)
    assert w.session is not None and w.state == "live", w.result and w.result.message
    w.probe.setCurrentText(f"V({d.nets()[s + '.b']})")
    select(w, s)
    return s


def until_t(w, t, timeout=20000):
    assert wait_for(lambda: w.live_t >= t or w.session is None, timeout), w.live_t


def finished(w, timeout=30000):
    assert wait_for(lambda: w.session is None, timeout)


def test_moving_the_lux_slider_steps_the_plotted_output_after_the_change(win):
    w = win
    s = start(w)
    sl = w.inspector.live_sliders["lux"]
    lo, hi, log = CATALOG.get("LDR").param("lux").slider(10.0)
    to_val, to_pos = slider_map(lo, hi, log)
    assert sl.value() == to_pos(10.0)
    until_t(w, 5e-3)
    sl.setValue(to_pos(1000.0))                                   # what a drag ends on
    finished(w)
    r = w.result
    assert r.status == "success" and w.state == "completed" and w.session is None
    assert len(r.changes) == 1
    tc, part, key, lux = r.changes[0]
    assert (part, key) == (s, "lux") and lux == to_val(to_pos(1000.0)) and 5e-3 <= tc < 15e-3
    sig, ser = w.view_data()                                      # the plotted series, unmodified
    line = w.ax.lines[0].get_xydata()
    assert len(line) == len(ser) and line[-1][0] == pytest.approx(TSTOP)
    before = [v for t, v in ser if t <= tc]
    after = [v for t, v in ser if t > tc + 1e-4]
    assert max(abs(v - ldr_out(10)) for v in before) < 1e-4
    assert max(abs(v - ldr_out(lux)) for v in after) < 1e-4 and ldr_out(lux) - ldr_out(10) > 1.0
    ticks = [l for l in w.ax.lines[1:] if list(l.get_xdata()) == [tc, tc]]
    assert ticks, "the change is marked on the plot's time axis"
    w.set_cursor_time(TSTOP / 2)                                  # batch scrubbing still works on the finished run
    assert w.cur_t == pytest.approx(TSTOP / 2) and "t = 10 ms" in w.readout.text()


def test_the_plot_grows_while_the_run_streams(win):
    w = win
    start(w)
    until_t(w, 4e-3)
    assert wait_for(lambda: len(w.ax.lines) and len(w.ax.lines[0].get_xdata()) > 50)
    n = len(w.view_data()[1])
    assert w.ax.get_xlim() == pytest.approx((0, TSTOP))           # fixed axis: the line grows into it
    assert w.slider.isEnabled() and "real time" in w.results.live_lbl.text()
    until_t(w, 12e-3)
    assert wait_for(lambda: len(w.view_data()[1]) > n)
    w.stop_live(); finished(w)


def test_pause_holds_sim_time_and_resume_finishes_the_run(win):
    w = win
    start(w)
    until_t(w, 3e-3)
    QTest.mouseClick(w.results.live_pause, Qt.LeftButton)
    assert w.paused and w.state == "paused" and w.results.live_pause.text() == "Resume"
    QTest.qWait(200)                                              # the chunk in progress may still land
    t = w.live_t
    QTest.qWait(400)
    assert w.live_t == t < TSTOP
    QTest.mouseClick(w.results.live_pause, Qt.LeftButton)
    assert not w.paused and w.state == "live"
    finished(w)
    assert w.result.status == "success" and w.result.get("V(n2)")[-1][0] == pytest.approx(TSTOP)


def test_stop_keeps_the_points_so_far_and_a_batch_run_works_after(win):
    w = win
    start(w)
    until_t(w, 3e-3)
    QTest.mouseClick(w.results.live_stop, Qt.LeftButton)
    finished(w)
    r = w.result
    assert r.status == "cancelled" and w.state == "cancelled" and w.state_lbl.text() == "Stopped"
    assert 3e-3 <= r.get("V(n2)")[-1][0] < TSTOP and w.b_rerun.isEnabled() and w.b_live.text() == "Live"
    w.rerun()
    assert w.result.status == "success" and w.result.get("V(n2)")[-1][0] == pytest.approx(TSTOP)


def test_batch_run_is_disabled_during_a_live_run(win):
    w = win
    start(w)
    until_t(w, 1e-3)
    assert not any(b.isEnabled() for b in (w.b_run, w.b_rerun, w.b_reset))
    assert not w.a_run.isEnabled() and not w.a_rerun.isEnabled() and not w.a_reset.isEnabled()
    assert not w.tstop.isEnabled() and not w.pace.isEnabled()
    assert w.b_live.text() == "Stop" and w.b_live.isEnabled()
    live = w.result
    w.run(); w.reset()                                            # direct calls are refused too
    assert w.result is live and w.session is not None
    QTest.mouseClick(w.b_live, Qt.LeftButton)                     # the Live button stops it
    finished(w)
    assert w.b_run.isEnabled() is False and w.b_rerun.isEnabled()  # completed-like state: Re-run, as after a batch run


def test_closing_the_window_mid_run_leaves_no_worker_or_engine_running(qapp):
    w = show(MainWindow())
    d = start(w) and w.doc
    until_t(w, 2e-3)
    w.pause_live()                                                # worst case: the worker waits in a paused run
    QTest.qWait(100)
    worker, session = w.worker, w.session
    w.hide(); w.close()
    assert worker.isFinished() and w.worker is None and w.session is None and session.state == "cancelled"
    w.deleteLater()
    assert run_document(d).ok                                     # the engine is free
    w2 = show(MainWindow()); w2._reset(ldr_doc()[0]); w2.run()
    assert w2.result.ok
    w2.hide(); w2.close(); w2.deleteLater()


def test_an_engine_error_mid_run_is_shown_like_a_batch_failure(win):
    w = win
    s = start(w)
    until_t(w, 2e-3)
    w.live_set(s, "lux", 1e-300)                                  # accepted by the param (> 0), breaks the law in ngspice
    finished(w)
    r = w.result
    assert r.status == "failed" and w.state == "failed" and "SolverError" in r.message
    assert w.err_title.text() == "Run failed" and "SolverError" in w.err.text() and w.status.text().startswith("FAILED")
    assert w.results.pages.currentIndex() == 3 and not w.results.live_row.isVisible()
    w.run()
    assert w.result.ok                                            # the engine recovered


def test_a_refused_value_is_reported_and_the_run_goes_on(win):
    w = win
    s = start(w)
    w.live_set(s, "lux", 1e9)                                     # over the package max (G31)
    assert wait_for(lambda: "refused" in w.status.text())
    assert "must be <= 100000" in w.status.text() and w.session is not None
    w.stop_live(); finished(w)


def test_editing_the_circuit_stops_the_live_run(win):
    w = win
    s = start(w)
    until_t(w, 2e-3)
    w.edit_param(s, "r10", "20k")
    assert w.result is None and w.stale and "Live run stopped" in w.status.text()
    finished(w)
    assert w.session is None and w.state == "idle" and w.b_rerun.isEnabled() and not w.inspector.live_sliders


def test_sliders_and_switches_cover_the_selected_parts_live_params(win):
    w = win
    d = Document()
    v, sw, r, g = d.add_part("V", 60, 120, dc="5"), d.add_part("SW", 220, 60), d.add_part("R", 380, 160, R="1k"), \
        d.add_part("GND", 240, 300)
    for a, b in ((f"{v}.p", f"{sw}.a"), (f"{sw}.b", f"{r}.a"), (f"{r}.b", f"{g}.g"), (f"{v}.n", f"{g}.g")):
        d.connect(a, b)
    d.tstop, d.tstep = "20m", "10u"
    w._reset(d); w.pace.setCurrentText("Pace: 0.01×"); w.live()
    select(w, sw)
    combo = w.inspector.live_sliders["state"]
    assert combo.currentText() == "open"
    until_t(w, 4e-3)
    combo.setCurrentText("closed")
    select(w, r)
    assert w.inspector.live_sliders["R"].toolTip() == "10 Ω to 100 kΩ, log scale"   # no package max: x/÷100 around 1k
    finished(w)
    tc = w.result.changes[0][0]
    i = w.result.get(f"I({r})")
    assert value_at(i, tc - 1e-3) < 1e-6 and value_at(i, tc + 1e-3) == pytest.approx(5e-3, rel=1e-3)


def test_replay_reruns_the_change_log_as_a_batch_run_that_matches(win):
    w = win
    s = start(w)
    lo, hi, log = CATALOG.get("LDR").param("lux").slider(10.0)
    to_val, to_pos = slider_map(lo, hi, log)
    until_t(w, 4e-3)
    w.inspector.live_sliders["lux"].setValue(to_pos(300.0))
    until_t(w, 10e-3)
    w.inspector.live_sliders["lux"].setValue(to_pos(3.0))
    finished(w)
    live = w.result
    assert len(live.changes) == 2 and w.results.live_replay.isVisible() and w.results.live_replay.isEnabled()
    QTest.mouseClick(w.results.live_replay, Qt.LeftButton)
    rep = w.result
    assert rep is not live and rep.status == "success" and w.state == "completed" and rep.changes == live.changes
    a, b = live.get("V(n2)"), rep.get("V(n2)")
    assert max(abs(v - value_at(b, t)) for t, v in a if t > 1e-5) < 1e-4
    assert w.status.text().startswith("REPLAY") and "2 changes" in w.status.text()


# ---------------------------------------------------------------- slider ranges are package data (G31)
@pytest.mark.parametrize("kind", sorted(STIMULI))
def test_every_stimulus_has_a_finite_slider_range_from_its_package(kind):
    p = CATALOG.get(kind).param(STIMULI[kind])
    v = p.parse(p.default)
    lo, hi, log = p.slider(v)
    assert p.max is not None and hi == p.max and lo <= v <= hi and lo < hi
    assert lo == (p.min if p.min is not None and (p.min > 0 or not log) else p.max / 1e6)


def test_log_scale_and_slider_mapping():
    lux = CATALOG.get("LDR").param("lux")
    assert lux.scale == "log" and lux.slider(10.0) == (0.1, 1e5, True)
    assert CATALOG.get("FSR").param("force").slider(1.0) == (1e-4, 100, True)
    assert CATALOG.get("R").param("R").slider(1e3) == (10, 1e5, True)       # Ω: log by unit, no package max
    assert CATALOG.get("V").param("dc").slider(5.0) == (-10, 10, False)     # linear, ±2|v|
    to_val, to_pos = slider_map(0.1, 1e5, True)
    assert to_val(0) == 0.1 and to_val(1000) == 1e5 and to_val(to_pos(10.0)) == pytest.approx(10.0, rel=0.015) and to_pos(1e5) == 1000


def test_a_bad_scale_is_a_definition_error(tmp_path):
    from app import catalog
    from conftest import write_def
    try:
        write_def(tmp_path, mutate=lambda d: d["params"][0].update(scale="cubic"))
        assert any(p["code"] == "DEF-PARAM" and "scale" in p["message"] for p in catalog.CATALOG.problems)
        write_def(tmp_path, mutate=lambda d: d["params"][0].update(scale="log", min=-1))
        assert any("log scale needs min >= 0" in p["message"] for p in catalog.CATALOG.problems)
    finally:
        catalog.reload()
