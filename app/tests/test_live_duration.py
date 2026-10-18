"""Live-run duration and indefinite live runs (task 9.7), real ngspice: the duration sets the live tstop, an
indefinite run goes on until stopped with bounded memory (a scrolling window, LiveSession.cap), a change lands
exactly, replay runs to the stop time (refused past the batch limits), the setting is saved, the UI field and the
scrolling plot. Contract: docs/plans/ngspice-library/LIVE.md "Indefinite runs"."""
import json
import math

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from app.model import Document, ModelError
from app.sim import LiveSession, trim
from app.tests.test_gui_workflow import qapp, win  # noqa: F401
from app.tests.test_live import Worker, divider, ldr_out
from app.tests.test_live_ui import finished, ldr_doc, until_t
from conftest import wait_for


def rss_mb():
    with open("/proc/self/status") as f:
        return next(int(l.split()[1]) for l in f if l.startswith("VmRSS")) / 1024


def run_to_end(s):
    for _ in s: pass
    return s.result


# ---------------------------------------------------------------- engine side, through LiveSession
def test_the_live_duration_controls_the_live_tstop():
    c, _, _, out = divider(tstop="2m")
    assert c.d.live_tstop == "" and LiveSession(c.d).close() is None   # default: the batch stop time
    s = LiveSession(c.d)
    assert s.tstop == 2e-3 and s.window == 2e-3 and run_to_end(s).get(out)[-1][0] == pytest.approx(2e-3)
    c.d.live_tstop = "1m"                         # shorter than the stop time: every point is kept
    r = run_to_end(LiveSession(c.d)).get(out)
    assert r[0][0] < 1e-6 and r[-1][0] == pytest.approx(1e-3)
    c.d.live_tstop = "5m"                         # longer: runs to 5 ms and keeps the last stop time (2 ms) of it
    s = LiveSession(c.d)
    r = run_to_end(s).get(out)
    assert s.result.status == "success" and r[-1][0] == pytest.approx(5e-3) and 3e-3 - 1e-9 <= r[0][0] < 3.1e-3
    c.d.live_tstop = "soon"
    s = LiveSession(c.d)
    assert s.result.status == "blocked" and "Live run duration" in s.result.message and list(s) == []


def test_an_indefinite_run_goes_past_the_stop_time_until_stopped():
    c, _, _, out = divider(tstop="2m")
    c.d.live_tstop = "Indefinite"
    s = LiveSession(c.d)
    assert s.indefinite and math.isinf(s.tstop) and s.window == 2e-3
    n = 0
    for chunk in s:
        n += 1
        assert not chunk["done"]
        if chunk["t"] >= 25 * 2e-3:               # 25x the stop time (and 1250 chunks) later: still running
            s.cancel()
    assert s.state == "cancelled" and s.result.status == "cancelled" and n >= 1250
    pts = s.result.get(out)
    assert pts[-1][0] >= 50e-3 and pts[-1][0] - pts[0][0] <= 2e-3 + 1e-9   # only the window is kept
    assert all(v == pytest.approx(ldr_out(10), abs=1e-9) for _, v in pts)


def test_memory_stays_bounded_over_a_long_fast_indefinite_run():
    """Max pace, 8000 chunks of 50 points: the retained samples stay under the cap (and the window) and RSS stays
    flat. Without the window it would keep 400k points x 4 series (~150 MB of tuples) and ngspice its own copy."""
    c, _, _, out = divider(tstop="2m")
    c.d.live_tstop = "indefinite"
    s = LiveSession(c.d, chunk=5e-4)
    assert s.cap == 100_000
    base, most, total = None, 0, 0
    for k, chunk in enumerate(s, 1):
        total += len(chunk["time"])
        most = max(most, max(len(v) for v in s.result.series.values()))
        if k == 1000:
            base = rss_mb()
        if k == 8000:
            s.cancel()
    grew = rss_mb() - base
    assert total > 390_000 and most <= s.cap and most < 300, (total, most)
    assert grew < 15, f"RSS grew {grew:.1f} MB over 7000 chunks"


def test_trim_keeps_the_window_and_the_cap():
    ser = {"a": [(t / 10, t) for t in range(100)], "b": [(t / 10, -t) for t in range(100)]}
    trim(ser, 5.0, 1000)
    assert ser["a"][0] == (5.0, 50) and ser["b"][0] == (5.0, -50) and len(ser["a"]) == 50
    trim(ser, 0.0, 10)
    assert len(ser["a"]) == len(ser["b"]) == 10 and ser["a"][-1] == (9.9, 99)


def test_a_change_during_an_indefinite_run_lands_exactly_and_replays_to_the_stop_time():
    c, ldr, _, out = divider(tstop="2m")
    c.d.live_tstop = "indefinite"
    s = LiveSession(c.d, chunk=1e-3)
    for _ in range(30):                           # 30 ms: 15x past the stop time
        t = next(s)["t"]
    assert t == pytest.approx(30e-3)
    s.set_stimulus(ldr, 100)
    c2 = next(s)
    assert c2["changes"] == [(t, ldr, "lux", 100.0)]
    s.cancel()
    assert list(s) == [] and s.result.status == "cancelled"
    pts = s.result.get(out)
    assert all(v == pytest.approx(ldr_out(10) if tt <= t else ldr_out(100), abs=1e-9) for tt, v in pts)
    assert any(tt <= t for tt, _ in pts) and any(tt > t for tt, _ in pts)
    rep = s.replay()                              # a batch run of 31 ms, the time the run reached
    assert rep.ok and rep.changes == s.changes and rep.get(out)[-1][0] == pytest.approx(31e-3)
    for tt, v in rep.get(out):
        assert v == pytest.approx(ldr_out(10) if tt <= t else ldr_out(100), abs=1e-9), tt


def test_replay_of_an_indefinite_run_past_the_batch_limit_is_refused():
    c, ldr, _, _ = divider(tstop="2m", tstep="10u")
    c.d.live_tstop = "indefinite"
    s = LiveSession(c.d, chunk=0.02)
    for k, chunk in enumerate(s):
        if k == 1: s.set_stimulus(ldr, 50)
        if chunk["t"] >= 1.1:                     # 1.1 s at 10 us: 110k points, over MAX_POINTS
            s.cancel()
    r = s.replay()
    assert r.status == "blocked" and r.message.startswith("Replay refused: the live run lasted 1.1") \
        and "100,000" in r.message and r.changes == s.changes


# ---------------------------------------------------------------- persistence
def test_the_live_duration_is_saved_and_read_back(tmp_path):
    d, _ = ldr_doc()
    assert "live_tstop" not in d.to_dict()["simulation"]          # the default writes nothing
    for text in ("indefinite", "30", "2m"):
        d.live_tstop = text
        p = tmp_path / "c.json"
        d.save(str(p))
        assert json.loads(p.read_text())["simulation"]["live_tstop"] == text
        back = Document.load(str(p))
        assert back.live_tstop == text and back.to_dict() == d.to_dict()
    assert Document.load(str(p)).live_duration() == 2e-3


def test_an_old_document_without_the_setting_loads_with_the_stop_time(tmp_path):
    d, _ = ldr_doc()
    old = d.to_dict()
    assert "live_tstop" not in old["simulation"]
    p = tmp_path / "old.json"
    p.write_text(json.dumps(old))
    back = Document.load(str(p))
    assert back.live_tstop == "" and back.live_duration() == 20e-3
    bad = {**old, "simulation": {**old["simulation"], "live_tstop": [1]}}
    with pytest.raises(ModelError, match="live_tstop"):
        Document.from_dict(bad)


# ---------------------------------------------------------------- UI
def test_the_live_for_field_sets_and_validates_the_duration(win):
    w = win
    d, _ = ldr_doc()
    w._reset(d)
    assert w.live_for.currentText() == "Stop time" and w.live_for.isEnabled()
    le = w.live_for.lineEdit()
    le.selectAll(); QTest.keyClicks(le, "abc"); le.editingFinished.emit()
    assert w.doc.live_tstop == "abc" and w.live_for.property("invalid") and not w.b_live.isEnabled()
    assert w.b_run.isEnabled(), "a bad live duration does not block a batch run"
    w.live()
    assert w.session is None and "Live run duration" in w.status.text()
    le.selectAll(); QTest.keyClicks(le, "50m"); le.editingFinished.emit()
    assert w.doc.live_tstop == "50m" and not w.live_for.property("invalid") and w.b_live.isEnabled() and w.dirty()
    w.live_for.setCurrentIndex(1); w.live_for.activated.emit(1)            # pick "Indefinite"
    assert w.doc.live_tstop == "Indefinite" and math.isinf(w.doc.live_duration())
    w.live_for.setCurrentIndex(0); w.live_for.activated.emit(0)            # back to "Stop time": unset
    assert w.doc.live_tstop == "" and "live_tstop" not in w.doc.to_dict()["simulation"]


def test_an_indefinite_live_run_scrolls_and_its_window_can_be_scrubbed_after_stop(win):
    w = win
    d, s = ldr_doc()                               # stop time 20 ms: the scrolling window
    d.live_tstop = "indefinite"
    w._reset(d)
    assert w.live_for.currentText() == "Indefinite"
    w.pace.setCurrentText("Pace: 1×")
    QTest.mouseClick(w.b_live, Qt.LeftButton)
    assert w.session is not None and not w.live_for.isEnabled()
    w.probe.setCurrentText(f"V({d.nets()[s + '.b']})")
    until_t(w, 70e-3)
    assert wait_for(lambda: w.results.xlim[0] > 40e-3, 5000)                 # the axis scrolled
    lo, hi = w.results.xlim
    assert hi - lo == pytest.approx(20e-3) and w.ax.get_xlim() == pytest.approx((lo, hi))
    assert "/ ∞" in w.results.live_lbl.text() and w.session is not None
    QTest.mouseClick(w.results.live_stop, Qt.LeftButton)
    finished(w)
    r = w.result
    assert w.state == "cancelled" and r.ok
    sig, ser = w.view_data()
    assert ser[-1][0] >= 70e-3 and ser[-1][0] - ser[0][0] <= 20e-3 + 1e-9    # the retained window
    assert w.t_rng == (ser[0][0], ser[-1][0])
    mid = (ser[0][0] + ser[-1][0]) / 2
    w.set_cursor_time(mid)
    assert w.cur_t == pytest.approx(mid) and "V(" in w.readout.text()
    w.set_cursor_time(0.0)                                                  # clamps to the window
    assert w.cur_t == ser[0][0]
