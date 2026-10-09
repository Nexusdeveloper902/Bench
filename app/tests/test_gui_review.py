"""GUI regressions from docs/review/attack-findings.md (B1, B2, B4, B7-B9) plus viewer axis values and the real
solver-failure path. Real MainWindow, real engine; only modal dialogs are monkeypatched."""
import json, math
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFormLayout, QLabel, QMessageBox, QPushButton, QLineEdit, QComboBox
from app.model import Document, value_at
from app.components import format_si
from app.tests.test_gui_workflow import qapp, show, win, select, field, type_into, click, pick  # noqa: F401


def typing(w, text):
    """Click into the Resistance field and type without Enter (what a user does before pressing F5)."""
    QTest.qWait(20); ed = field(w, "Resistance")
    QTest.mouseClick(ed, Qt.LeftButton); ed.selectAll(); QTest.keyClicks(ed, text)
    assert w.focusWidget() is ed
    return ed


def test_pending_inspector_edit_is_committed_before_run(win):
    """B1: typed '1k' in Resistance, no Enter, F5. Run must simulate R = 1k (I = (5-1.97)/1k ~ 3 mA, not 9.2 mA)."""
    w = win; w._reset(Document.led_example()); select(w, "R1")
    ed = typing(w, "1k")
    assert w.doc.parts["R1"]["params"]["R"] == "330"            # not committed yet
    QTest.keyClick(ed, Qt.Key_F5)
    assert w.doc.parts["R1"]["params"]["R"] == "1k"
    i = w.result.stats("I(D1)")["final"]
    assert 2.5e-3 < i < 3.5e-3, i


def test_pending_invalid_edit_blocks_run_with_message(win):
    w = win; w._reset(Document.led_example()); select(w, "R1")
    ed = typing(w, "abc")
    w.a_run.trigger()
    assert w.state == "blocked" and "R1" in w.result.message


@pytest.mark.parametrize("which,text", [("tstop", "1m"), ("tstep", "20u")])
def test_run_setting_edit_marks_result_stale(win, which, text):
    """B2: editing Stop time / Time step after a run invalidates the waveform like a parameter edit."""
    w = win; w._reset(Document.led_example()); w.a_run.trigger()
    assert w.state == "completed" and w.view_data()
    ed = getattr(w, which); type_into(ed, text)
    assert w.state == "idle" and w.result is None and w.view_data() is None
    assert "out of date" in w.status.text()
    w.a_run.trigger(); assert w.state == "completed"


def test_displaying_si_run_settings_does_not_stale_result(win):
    w = win; w._reset(Document.led_example()); w.a_run.trigger()
    w.refresh(); w.cfg_changed()                                 # showing '5 ms' for '5e-3' is not an edit
    assert w.state == "completed"


MALFORMED = ['{"format": "sim-circuit", "version": 1, "components": {}, "connections": [[1, 2]], "simulation": '
             '{"tstop": "1m", "tstep": "1u"}}', "[" * 100000, "{", "", '{"version": 1}']


@pytest.mark.parametrize("content", MALFORMED)
def test_open_malformed_shows_open_failed_dialog(win, tmp_path, monkeypatch, content):
    shown = []
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: shown.append(a[1:3])))
    p = tmp_path / "bad.json"; p.write_text(content)
    before = win.doc
    win.open(str(p))
    assert len(shown) == 1 and shown[0][0] == "Open failed" and shown[0][1], shown
    assert win.doc is before


def test_run_fields_show_si_and_examples_roundtrip(win):
    w = win; w._reset(Document.led_example())
    assert (w.tstop.text(), w.tstep.text()) == ("5 ms", "10 µs")
    assert (w.doc.tstop, w.doc.tstep) == ("5e-3", "10e-6") or not w.dirty()
    assert not w.dirty()                                          # displaying SI text is not an edit
    w.a_run.trigger(); assert w.state == "completed"
    w.load_blink_example(); assert (w.tstop.text(), w.tstep.text()) == ("8.5 ms", "20 µs")


def test_inspector_fields_readable_at_1300x850(win):
    """B7: a PULSE source's 7 fields must each be a usable size and the Play row must not overlap the plot."""
    w = win; w.resize(1300, 850)
    w._reset(Document.blink_example()); select(w, "V1"); QTest.qWait(50)
    eds = [w.form.itemAt(i, QFormLayout.FieldRole).widget() for i in range(w.form.rowCount())
           if w.form.itemAt(i, QFormLayout.FieldRole)]
    assert len([e for e in eds if isinstance(e, QLineEdit)]) == 7
    for e in [e for e in eds if not isinstance(e, QLabel)]:
        assert e.width() >= 100 and e.height() >= 20, (type(e).__name__, e.size())
    assert w.minimumSizeHint().height() <= 850
    pb, pw = w.play_btn.mapTo(w, w.play_btn.rect().topLeft()), w.plotw.mapTo(w, w.plotw.rect().topLeft())
    assert pw.y() + w.plotw.height() <= pb.y(), (pw, w.plotw.size(), pb)


def test_no_duplicate_rotate_delete_in_inspector(win):
    """B8: Rotate/Delete live in the toolbar and Edit menu only."""
    texts = {b.text() for b in win.centralWidget().findChildren(QPushButton)}
    assert not {"Rotate (R)", "Delete selected"} & texts
    assert win.a_rotate.text() == "Rotate" and win.a_delete.text() == "Delete"   # still reachable: toolbar + Edit menu


@pytest.mark.parametrize("t,label", [(0.0, "0 s"), (1e-3, "1 ms"), (2.5e-3, "2.5 ms"), (4.9e-3, "4.9 ms")])
def test_time_axis_ticks_and_cursor_readout(win, t, label):
    """Tick text is t / scale in the unit of the axis label: a x10 scale error shows up here."""
    w = win; w._reset(Document.led_example()); w.a_run.trigger(); pick(w, "V(R1)")
    scale = {"time (ms)": 1e-3}[w.ax.get_xlabel()]
    fmt = w.ax.xaxis.get_major_formatter()
    for tick in (0.0, 1e-3, 2e-3, 5e-3):
        assert fmt(tick, 0) == f"{tick / 1e-3:g}"
    ticks = [x for x in w.ax.get_xticks() if 0 <= x <= 5e-3 + 1e-12]
    assert 5e-3 in [round(x, 9) for x in ticks]
    w.set_cursor_time(t)
    assert w.readout.text().startswith("cursor  t = ")
    if t >= 1e-3:                                                 # (t = 0 clamps to the first sample, step/100)
        assert f"t = {label} " in w.readout.text()
        v = value_at(w.view_data()[1], t)
        assert f"V(R1) = {format_si(v, 'V', 5)}" in w.readout.text()


def test_real_solver_failure_shows_failed_with_details(win):
    """A real ngspice 'timestep too small' (cross-coupled B-source latch, injected through Document.netlist because
    the app's own parts cannot oscillate/latch) -> state failed, message and developer details, no plot."""
    w = win; w._reset(Document.led_example())
    nl = dict(w.doc.netlist(), lines=["B1 o1 0 V=5*(1-u(V(o2)-2.5))", "B2 o2 0 V=5*(1-u(V(o1)-2.5))",
                                      "C1 o1 0 1n", "C2 o2 0 1n"], models=[], saves=[], observables=["v(o1)"],
              derived={}, signals=[])
    w.doc.netlist = lambda: nl
    w.doc.tstop, w.doc.tstep = "10u", "10n"
    w.a_run.trigger()
    assert w.state == "failed" and not w.result.ok
    assert "Timestep too small" in w.result.message and w.err.text().startswith("Simulation failed")
    assert w.view_data() is None and w.details_btn.isEnabled()
    assert "ConvergenceError" in w.details.toPlainText() and "B-CYCLE" in w.details.toPlainText()
    assert "FAILED" in w.status.text()
