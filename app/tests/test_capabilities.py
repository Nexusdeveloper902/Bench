"""Capability pre-check: an unsimulated part never vanishes silently from a run. Wired -> Run refused ('blocked',
the core is never called); unwired -> excluded and named in the success message. Also R5 (case-only duplicate names)
and R6 (only core errors are caught). Real catalog, real engine, real files; no mocks except the R6 fault injection."""
import json
import pytest
from PySide6.QtWidgets import QApplication
from test_persistence_v2 import FUTURE, board, dump
from app import sim
from app.model import Document, ModelError
from app.sim import run_document
from app.window import MainWindow

I_LED = 9.1772e-3    # plan 02-plan.md experiment 5: LED example I(D1), reference value


def led_with_board(root, wired):
    board(root)
    d = Document.led_example()
    assert d.add_part("TESTBOARD", 600, 40) == "U1"
    if wired: d.connect("U1.3V3", "R1.a")
    return d


def codes(d):
    return [(x["code"], x["severity"]) for x in d.validate()]


def test_wired_unsupported_blocks_run(extra_root):
    d = led_with_board(extra_root, wired=True)
    assert ("NOT-SIMULATED", "error") in codes(d)
    with pytest.raises(ModelError, match="U1 .*wired"):      # the netlist boundary refuses too, never drops the part
        d.netlist()
    r = run_document(d)
    assert r.ok is False and r.status == "blocked" and r.state == "blocked"
    assert "NOT-SIMULATED" in r.message and "U1" in r.message and "Test TESTBOARD" in r.message
    assert "--- netlist" not in r.details                     # the core was never called
    assert r.series == {} and r.signals == [] and r.excluded == []


def test_unwired_unsupported_runs_rest(extra_root):
    d = led_with_board(extra_root, wired=False)
    assert ("NOT-SIMULATED", "warning") in codes(d) and not any(s == "error" for _, s in codes(d))
    r = run_document(d)
    assert r.ok and r.status == "success" and r.excluded == ["U1"]
    assert "not simulated: U1" in r.message
    assert r.stats("I(D1)")["final"] == pytest.approx(I_LED, rel=1e-3)
    ref = run_document(Document.led_example())               # same circuit without the board: identical waveforms
    assert r.series == ref.series and [s["name"] for s in r.signals] == [s["name"] for s in ref.signals]


def test_every_excluded_part_is_named(extra_root, tmp_path):
    """Unsupported definition + placeholder (missing definition), both unwired, one success message names both."""
    board(extra_root)
    t = json.loads(json.dumps(FUTURE)); t["connections"].remove(["U2.COM", "U2.NO"])
    d = Document.load(dump(tmp_path / "f.json", t))
    d.add_part("TESTBOARD", 600, 40)
    assert sorted(codes(d)) == sorted([("MISSING-DEFINITION", "warning"), ("NOT-SIMULATED", "warning")])
    r = run_document(d)
    assert r.ok and r.excluded == ["U2", "U1"] and r.message.endswith("not simulated: U2, U1")


def test_wired_placeholder_blocks_run(tmp_path):
    d = Document.load(dump(tmp_path / "f.json", FUTURE))     # U2 FUTURE_RELAY wired only to itself
    assert ("MISSING-DEFINITION", "error") in codes(d)
    r = run_document(d)
    assert r.status == "blocked" and "U2" in r.message and "FUTURE_RELAY" in r.message and r.series == {}
    d.disconnect("U2.COM")
    r = run_document(d)
    assert r.ok and r.excluded == ["U2"] and "not simulated: U2" in r.message
    assert r.stats("I(D1)")["final"] == pytest.approx(I_LED, rel=1e-3)


def test_incompatible_placeholder_is_reported_not_substituted(tmp_path):
    t = json.loads(json.dumps(FUTURE)); t["components"]["D1"]["version"] = 2    # LED is version 1
    d = Document.load(dump(tmp_path / "x.json", t))
    r = run_document(d)
    assert r.status == "blocked" and r.message.startswith("[INCOMPATIBLE-DEFINITION]") and "LED version 2" in r.message


def test_only_unsupported_parts_is_empty_error(extra_root):
    board(extra_root)
    d = Document(); d.add_part("TESTBOARD")
    out = d.validate()
    assert [(x["code"], x["severity"]) for x in out] == [("EMPTY", "error"), ("NOT-SIMULATED", "warning")]
    assert out[0]["message"] == "Nothing to simulate: place simulated components."
    r = run_document(d)
    assert r.status == "blocked" and "Nothing to simulate" in r.message
    assert Document().validate()[0]["message"] == "Circuit is empty: place some components."


def test_case_only_duplicate_names_reported_before_run():
    d = Document()
    d.add_part("R"); d.add_part("R"); d.add_part("V"); d.add_part("GND")
    d.rename_part("R1", "r2")                     # allowed by the name rules: "r2" is unused ("R2" is a different string)
    for a, b in (("V1.p", "r2.a"), ("r2.b", "R2.a"), ("R2.b", "GND1.g"), ("V1.n", "GND1.g")): d.connect(a, b)
    errs = [x for x in d.validate() if x["code"] == "DUP-NAME-CASE"]
    assert len(errs) == 1 and errs[0]["severity"] == "error" and errs[0]["part"] == "R2"
    assert errs[0]["message"] == "r2 and R2 differ only in case; SPICE names are case-insensitive."
    r = run_document(d)
    assert r.status == "blocked" and "[DUP-NAME-CASE]" in r.message and "--- netlist" not in r.details
    d.rename_part("r2", "R7")
    assert run_document(d).ok


def test_core_error_is_failed_not_blocked():
    d = Document()
    for k in ("V", "R", "C", "GND"): d.add_part(k)
    for a, b in (("V1.p", "R1.a"), ("R1.b", "C1.a"), ("V1.n", "GND1.g")): d.connect(a, b)   # C1.b floats
    r = run_document(d)
    assert r.status == "failed" and r.state == "failed" and "--- netlist" in r.details and r.excluded == []


def test_app_bug_propagates_not_labelled_sim_error(monkeypatch):
    def boom(*a): raise ZeroDivisionError("app bug")
    monkeypatch.setattr(sim, "_diff", boom)
    with pytest.raises(ZeroDivisionError):
        run_document(Document.led_example())


# ---------------------------------------------------------------- the window shows blocked and excluded
@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    w = MainWindow()
    yield w
    w.hide(); w.close(); w.deleteLater()


def test_window_blocked_run_has_no_plot(win, extra_root):
    win._reset(led_with_board(extra_root, wired=True))
    win.a_run.trigger()
    assert win.state == "blocked" and "blocked" in win.state_lbl.text().lower() and win.status.text().startswith("BLOCKED")
    assert "U1" in win.err.text() and win.err_title.text() == "Run blocked" and not win.ax.lines
    assert win.probe.count() == 0 and win.stats.text() == ""


def test_window_success_names_excluded_part(win, extra_root):
    win._reset(led_with_board(extra_root, wired=False))
    win.a_run.trigger()
    assert win.state == "completed" and win.status.text().startswith("SUCCESS") and "not simulated: U1" in win.status.text()


def test_open_with_placeholders_shows_dismissible_banner(win, tmp_path):
    """dialogs.png: a non-blocking banner above the canvas (never over a part), with Show in Problems and Dismiss."""
    win.show(); assert not win.open_banner.isVisible()
    win.open(str(dump(tmp_path / "f.json", FUTURE)))
    b = win.open_banner
    assert b.isVisible() and win.open_msg.text() == "1 part has no usable definition: U2 (FUTURE_RELAY v3)."
    assert b.property("kind") == "warn" and b.parentWidget() is win.canvas.parentWidget()
    assert b.geometry().bottom() < win.canvas.geometry().top()           # laid out above the canvas, not floating on it
    assert win.canvas.isEnabled() and win.doc.placeholders() == ["U2"]       # non-blocking: still editable
    win.open_show.click()
    assert win.sel == "U2"
    win.open_dismiss.click()
    assert not b.isVisible() and "U2 (FUTURE_RELAY v3)" in win.status.text()  # the status bar keeps the message
    win.open(str(dump(tmp_path / "f.json", FUTURE))); assert b.isVisible()
    win.new(); assert not b.isVisible()                                      # a new document clears it


def test_window_open_warns_about_placeholders(win, tmp_path):
    p = dump(tmp_path / "f.json", FUTURE)
    win.open(str(p))
    assert "1 part has no usable definition: U2 (FUTURE_RELAY v3)" in win.status.text()
    assert win.doc.placeholders() == ["U2"] and not win.dirty()
    win.select("U2")                                          # inspector and drawing cope with a placeholder
    win.a_run.trigger()
    assert win.state == "blocked" and "MISSING-DEFINITION" in win.status.text()
