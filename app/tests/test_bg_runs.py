"""Batch/AC/replay runs on a worker thread (app/window.py RunWorker): the GUI stays responsive, Stop abandons the run, a
Run during an abandoned one waits for the engine, New/Open/close are safe. `run_nowait` is the plain start; the autouse
fixture in conftest makes `run` wait for completion for every other test."""
import time

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest

from app.model import Document
from app.sim import run_document
from app.tests.test_gui_workflow import qapp, win  # noqa: F401
from conftest import wait_for


def slow_doc():
    """V1 PULSE 0-5 V, 1 ns edges every 4 us, into 4 x (R 100 + diode to ground): 120k edge-limited points, 2+ s of solver
    time (measured 2.4 s) with tstop/tstep inside the size limits (50k points)."""
    d = Document()
    d.add_part("V", 0, 0, mode="PULSE", v1=0, v2=5, td=0, tr=1e-9, tf=1e-9, pw=2e-6, per=4e-6); d.add_part("GND", 0, 0)
    prev = "V1.p"
    for i in range(1, 5):
        d.add_part("R", 0, 0, R=100); d.add_part("D", 0, 0)
        d.connect(prev, f"R{i}.a"); d.connect(f"R{i}.b", f"D{i}.a"); d.connect(f"D{i}.k", "GND1.g"); prev = f"R{i}.b"
    d.connect("V1.n", "GND1.g")
    d.tstop, d.tstep = "10m", "0.2u"
    return d


def led_current(w):
    return w.result.series[w.result.signal("I(D1)")["vector"]][-1][1]


def test_gui_processes_events_during_a_run_of_at_least_one_second(win):
    w = win; w._reset(slow_doc())
    ticks = []
    t = QTimer(w); t.setInterval(10); t.timeout.connect(lambda: ticks.append(w.busy)); t.start()
    t0 = time.monotonic(); w.run_nowait()
    assert w.state == "running" and w.b_run.text() == "Stop"
    assert wait_for(lambda: not w.busy, 60000); t.stop()
    took = time.monotonic() - t0
    assert took >= 1.0, f"the circuit must take >= 1 s to prove responsiveness (took {took:.2f} s)"
    assert sum(ticks) >= 20, f"only {sum(ticks)} GUI timer ticks ran while the solver was busy"
    assert w.state == "completed" and w.result.ok and w.result is not None and w.b_run.text() == "Run"
    assert w.ran_key == w._key() and not w.stale


def test_result_is_identical_to_the_synchronous_path(win):
    w = win; w._reset(Document.led_example()); w.run_nowait(); assert w.state == "running"
    assert wait_for(lambda: not w.busy)
    ref = run_document(Document.led_example())
    assert w.result.series == ref.series and w.result.message == ref.message
    assert abs(led_current(w) - 9.1772e-3) < 5e-8, led_current(w)


def test_stop_discards_the_result_and_leaves_the_state_consistent(win):
    w = win; w._reset(slow_doc()); w.run_nowait(); worker = w.run_worker
    assert w.busy
    w.b_run.click()   # the Run button is Stop while running
    assert not w.busy and w.result is None and w.state == "idle" and w.ran_key is None
    assert w.run_worker is worker          # the blocking call still holds the engine
    w.doc.add_part("R", 0, 0)             # the UI is usable at once: edit the circuit
    assert wait_for(lambda: w.run_worker is None, 60000)
    assert w.result is None and w.state == "idle" and not w.busy
    assert w.b_run.text() == "Run" and w.b_run.isEnabled()


def test_run_during_an_abandoned_run_waits_and_never_overlaps(win, monkeypatch):
    import app.window as window
    active, peak, calls = [0], [0], []
    real = window.run_document
    def counted(doc):
        active[0] += 1; peak[0] = max(peak[0], active[0]); calls.append(len(doc.parts))
        try: return real(doc)
        finally: active[0] -= 1
    monkeypatch.setattr(window, "run_document", counted)
    w = win; w._reset(slow_doc()); w.run_nowait(); first = w.run_worker
    w.cancel_run()
    w.doc.add_part("R", 0, 0)             # edited while abandoned: the queued run simulates the circuit as it is then
    w.run_nowait()
    assert w.busy and w.state == "running" and w.run_worker is first and len(calls) == 1
    assert "Finishing previous run" in w.status.text()
    assert wait_for(lambda: not w.busy and w.run_worker is None, 120000)
    assert peak[0] == 1 and len(calls) == 2 and calls[1] == calls[0] + 1
    assert w.state in ("completed", "failed", "blocked") and w.result is not None   # the second run's own result


def test_stop_while_waiting_drops_the_queued_run(win):
    w = win; w._reset(slow_doc()); w.run_nowait(); w.cancel_run(); w.run_nowait()
    w.cancel_run()
    assert not w.busy and w.queued is None and w.state == "idle"
    assert wait_for(lambda: w.run_worker is None, 60000) and w.result is None


def test_edit_during_a_run_marks_the_arriving_result_stale(win):
    w = win; w._reset(slow_doc()); w.run_nowait()
    w.edit_param("R1", "R", "220")
    assert wait_for(lambda: not w.busy, 60000)
    assert w.result is None and w.stale and w.state == "idle" and "out of date" in w.status.text()


def test_new_during_a_run_is_safe(win):
    w = win; w._reset(slow_doc()); w.run_nowait(); worker = w.run_worker
    w.new()   # New: the run is abandoned, the old circuit's result must never appear
    assert not w.busy and w.result is None and not w.doc.parts
    assert wait_for(lambda: w.run_worker is None, 60000)
    assert w.result is None and w.state == "idle"
    assert not worker.isRunning()


def test_closing_the_window_during_a_run_joins_the_worker(win):
    w = win; w._reset(slow_doc()); w.run_nowait(); worker = w.run_worker
    w.close()                              # closeEvent waits for the blocked call: no thread outlives the window
    assert not worker.isRunning() and w.run_worker is None and not w.busy
    import gc
    assert gc.isenabled()                  # the conftest leak guard also checks this after the test


def test_live_cannot_start_while_an_abandoned_run_holds_the_engine(win):
    w = win; w._reset(slow_doc()); w.run_nowait(); w.cancel_run()
    w.live()
    assert w.session is None and "Finishing previous run" in w.status.text()
    assert wait_for(lambda: w.run_worker is None, 60000)


def test_live_is_enabled_again_when_an_abandoned_run_frees_the_engine(win):
    w = win; w._reset(slow_doc()); w.run_nowait(); w.cancel_run()
    assert not w.b_live.isEnabled()
    assert wait_for(lambda: w.run_worker is None, 60000)
    assert w.b_live.isEnabled()            # no unrelated UI change needed to refresh it


def test_replay_runs_in_the_background_and_stop_keeps_the_live_result(win):
    from app.tests.test_live_ui import finished, start, until_t
    from app.catalog import CATALOG
    from app.inspector import slider_map
    w = win; start(w)
    lo, hi, log = CATALOG.get("LDR").param("lux").slider(10.0)
    until_t(w, 4e-3); w.inspector.live_sliders["lux"].setValue(slider_map(lo, hi, log)[1](300.0))
    assert wait_for(lambda: len(w.live_result.changes) == 1)
    finished(w); live = w.result
    w.replay_live_nowait()
    assert w.busy and w.state == "running"
    w.cancel_run()
    assert w.result is live and w.state == "completed"      # the live result is untouched by an abandoned replay
    assert wait_for(lambda: w.run_worker is None, 60000) and w.result is live
    w.replay_live_nowait(); assert wait_for(lambda: not w.busy, 60000)
    assert w.result is not live and w.status.text().startswith("REPLAY")


def test_a_queued_replay_is_stale_if_the_circuit_is_edited_while_it_waits(win):
    from app.tests.test_live_ui import finished, start, until_t
    from app.catalog import CATALOG
    from app.inspector import slider_map
    w = win; start(w)
    lo, hi, log = CATALOG.get("LDR").param("lux").slider(10.0)
    until_t(w, 4e-3); w.inspector.live_sliders["lux"].setValue(slider_map(lo, hi, log)[1](300.0))
    assert wait_for(lambda: len(w.live_result.changes) == 1)
    finished(w)
    w.replay_live_nowait(); w.cancel_run()
    w.replay_live_nowait()                 # queued behind the abandoned replay, for the circuit as it is now
    assert w.queued is not None
    w.doc.add_part("R", 0, 0)              # edited while it waits: the replay still simulates the live run's circuit
    assert wait_for(lambda: not w.busy and w.run_worker is None, 60000)
    assert w.result is None and w.stale and not w.status.text().startswith("REPLAY")
