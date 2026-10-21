"""AC small-signal analysis end to end (G17, and the AC source of G13): Document -> run_document -> the real engine,
checked against analytic transfer functions; persistence of the analysis choice; live runs stay transient only; the
Bode view in the window (headless)."""
import json
import math

import pytest

from app.electrical import check
from app.model import AC_DEFAULTS, Document, ModelError
from app.sim import LiveSession, replay_document, run_document
from app.tests.test_gui_workflow import qapp, win  # noqa: F401
from app.window import LIVE_AC

FC = 1 / (2 * math.pi * 1e3 * 1e-6)   # 1 kΩ / 1 µF: 159.155 Hz


def rc(ac="1", **sweep):
    """V (AC stimulus `ac`) -> R 1k -> C 1u -> ground, in AC analysis."""
    d = Document()
    d.add_part("V", 40, 100, ac=ac); d.add_part("R", 180, 60, R="1k"); d.add_part("C", 320, 100, C="1u")
    d.add_part("GND", 200, 260)
    for a, b in (("V1.p", "R1.a"), ("R1.b", "C1.a"), ("C1.b", "GND1.g"), ("V1.n", "GND1.g")):
        d.connect(a, b)
    d.analysis, d.ac = "ac", {**AC_DEFAULTS, **sweep}
    return d


def db_deg(z):
    return 20 * math.log10(abs(z)), math.degrees(math.atan2(z.imag, z.real))


def test_rc_lowpass_matches_the_transfer_function_and_its_minus_3db_point():
    """H = 1/(1 + j f/fc) over 1 Hz..1 MHz (dec 20), and exactly -3.0103 dB / -45° at fc (a lin sweep starting there)."""
    r = run_document(rc())
    assert r.ok and r.analysis == "ac", r.message
    pts = r.get("V(C1)")
    assert len(pts) == 121 and pts[0][0] == pytest.approx(1) and pts[-1][0] == pytest.approx(1e6)
    for f, z in pts:
        x = f / FC
        assert abs(z) == pytest.approx(1 / math.sqrt(1 + x * x), rel=1e-9)
        assert db_deg(z)[1] == pytest.approx(-math.degrees(math.atan(x)), abs=1e-6)
    r = run_document(rc(sweep="lin", points="3", fstart=repr(FC), fstop=repr(2 * FC)))
    (f, z), *_ = r.get("V(C1)")
    assert f == pytest.approx(FC) and db_deg(z) == pytest.approx((-10 * math.log10(2), -45), abs=1e-6)
    # across-part signals are complex differences; the source current is the small-signal one: I = -V/Z
    (_, vr), (_, i) = r.get("V(R1)")[0], r.get("I(V1)")[0]
    assert abs(vr) == pytest.approx(1 / math.sqrt(2), rel=1e-9) and i == pytest.approx(-vr / 1e3, rel=1e-9)


def test_rlc_resonance_peaks_at_f0_with_unity_gain():
    """Series L 1 mH, C 1 µF, R 10 Ω, output across R: gain 1 at f0 = 1/(2π√LC) = 5032.9 Hz, below it everywhere else."""
    d = Document()
    d.add_part("V", 40, 100, ac="1"); d.add_part("L", 180, 60, L="1m"); d.add_part("C", 320, 60, C="1u")
    d.add_part("R", 460, 100, R="10"); d.add_part("GND", 200, 260)
    for a, b in (("V1.p", "L1.a"), ("L1.b", "C1.a"), ("C1.b", "R1.a"), ("R1.b", "GND1.g"), ("V1.n", "GND1.g")):
        d.connect(a, b)
    f0 = 1 / (2 * math.pi * math.sqrt(1e-3 * 1e-6))
    d.analysis, d.ac = "ac", {"sweep": "dec", "points": "200", "fstart": "100", "fstop": "1M"}
    pts = run_document(d).get("V(R1)")
    f, z = max(pts, key=lambda p: abs(p[1]))
    assert f / f0 == pytest.approx(1, abs=0.006) and abs(z) <= 1 + 1e-9
    d.ac = {"sweep": "lin", "points": "3", "fstart": repr(f0), "fstop": repr(3 * f0)}
    (f, z), *_ = run_document(d).get("V(R1)")
    assert abs(z) == pytest.approx(1, rel=1e-9) and db_deg(z)[1] == pytest.approx(0, abs=1e-6)


def test_the_ac_stimulus_phase_and_a_current_source():
    """I source AC 2 mA at 90° into 1 kΩ: 2 V at +90° at every frequency."""
    d = Document()
    d.add_part("I", 40, 100, ac="2m", acphase="90"); d.add_part("R", 180, 100, R="1k"); d.add_part("GND", 100, 260)
    for a, b in (("I1.p", "GND1.g"), ("I1.n", "R1.a"), ("R1.b", "GND1.g")):
        d.connect(a, b)
    d.analysis = "ac"
    r = run_document(d)
    assert r.ok, r.message
    assert all(abs(z) == pytest.approx(2, rel=1e-9) and db_deg(z)[1] == pytest.approx(90, abs=1e-6)
               for _, z in r.get("V(R1)"))


def test_transient_decks_are_unchanged_by_the_ac_stimulus():
    """The `ac` template is emitted only in AC decks: a transient deck with a non-zero AC magnitude is byte-identical."""
    d = rc(ac="1")
    ac_line = d.netlist()["lines"][0]
    d.analysis = "transient"
    assert d.netlist()["lines"][0] == "V1 n1 0 DC 5.0" and ac_line == "V1 n1 0 DC 5.0 AC 1.0 0.0"
    r = run_document(d)
    assert r.ok and r.analysis == "transient" and r.stats("V(C1)")["final"] == pytest.approx(5, abs=0.05)


def test_ac_result_notes_device_currents_and_a_missing_stimulus():
    r = run_document(rc(ac="0"))
    names = [s["name"] for s in r.signals]
    assert r.ok and "I(V1)" in names and "I(R1)" not in names and "I(C1)" not in names
    assert "AC magnitude" in r.message and "device currents" in r.message
    assert all(z == 0 for _, z in r.get("V(C1)"))


@pytest.mark.parametrize("sweep, msg", [
    ({"fstart": "1k", "fstop": "10"}, "stop frequency must be above"),
    ({"points": "2.5"}, "whole number"),
    ({"points": "0"}, "AC points: must be >= 1"),
    ({"fstart": "0"}, "AC fstart: must be > 0"),
    ({"points": "100000", "fstop": "1e300"}, "too many points"),
])
def test_bad_sweeps_block_the_run(sweep, msg):
    r = run_document(rc(**sweep))
    assert r.status == "blocked" and msg in r.message, r.message


def test_a_two_point_lin_sweep_is_refused_by_the_engine():
    """ngspice 47 returns a single point for every `lin 2` sweep (probed); the engine refuses it with the reason."""
    r = run_document(rc(sweep="lin", points="2", fstart="100", fstop="200"))
    assert r.status == "failed" and "use 3 or more" in r.message, r.message


def test_live_runs_and_replays_are_transient_only():
    d = rc()
    s = LiveSession(d)
    assert s.result.status == "blocked" and "transient only" in s.result.message and list(s) == []
    assert replay_document(d, []).status == "blocked"


def test_save_and_load_round_trip(tmp_path):
    d = rc(sweep="oct", points="5", fstart="10", fstop="20k")
    d.save(tmp_path / "ac.json")
    raw = json.loads((tmp_path / "ac.json").read_text())["simulation"]
    assert raw == {"analysis": "ac", "tstop": "5m", "tstep": "10u",
                   "ac": {"sweep": "oct", "points": "5", "fstart": "10", "fstop": "20k"}}
    e = Document.load(tmp_path / "ac.json")
    assert (e.analysis, e.ac, e.to_dict()) == (d.analysis, d.ac, d.to_dict())
    assert run_document(e).get("V(C1)")[0][0] == pytest.approx(10)
    # defaults are not written; a transient document keeps a non-default sweep for later
    d = rc(); assert "ac" not in d.to_dict()["simulation"]
    d.analysis, d.ac["points"] = "transient", "7"
    e = Document.from_dict(json.loads(json.dumps(d.to_dict())))
    assert (e.analysis, e.ac["points"]) == ("transient", "7")


def test_old_files_load_as_transient_and_bad_settings_are_refused():
    old = Document.led_example().to_dict()
    del old["simulation"]["analysis"]
    assert Document.from_dict(old).analysis == "transient"
    for sim, msg in (({"analysis": "noise"}, "unsupported analysis"), ({"ac": {"sweep": "log"}}, "unknown AC sweep"),
                     ({"ac": {"step": "1"}}, "unknown AC setting"), ({"ac": "dec"}, "wrong type")):
        bad = Document.led_example().to_dict()
        bad["simulation"].update(sim)
        with pytest.raises(ModelError, match=msg):
            Document.from_dict(bad)


def test_the_ac_section_key_is_for_sources_only():
    from app.catalog import get
    r = get("R")
    out = check({**r.sim["electrical"], "ac": "AC {R}"}, r.pins, r.params)
    assert any(d["field"] == "sim.electrical.ac" and "V or I source" in d["message"] for d in out), out
    v = get("V")
    out = check({**v.sim["electrical"], "ac": "AC {nope}"}, v.pins, v.params)
    assert any("unknown placeholder {nope}" in d["message"] for d in out), out


def test_window_bode_view_cursor_and_live_disabled(win):
    """Headless window: pick AC, set the sweep in the top bar, run; the result is a Bode plot (log frequency axis,
    magnitude in dB, phase on a second axis) whose cursor readout gives -3.01 dB at -45° at fc. Live is disabled in AC
    with a clear message; switching back to Transient restores the transient view."""
    w = win
    w._reset(rc())
    w.doc.analysis = "transient"; w.refresh()
    assert w.tran_box.isVisibleTo(w) and not w.ac_box.isVisibleTo(w)
    w.analysis.setCurrentIndex(1); w.analysis.activated.emit(1)
    assert w.doc.analysis == "ac" and w.ac_box.isVisibleTo(w) and not w.tran_box.isVisibleTo(w)
    assert not w.live_box.isVisibleTo(w) and not w.b_live.isEnabled() and w.b_live.toolTip() == LIVE_AC
    w.ac_points.setText("50"); w.ac_points.editingFinished.emit()
    w.ac_fstop.setText("100k"); w.ac_fstop.editingFinished.emit()
    assert w.doc.ac == {"sweep": "dec", "points": "50", "fstart": "1", "fstop": "100k"}
    w.ac_fstart.setText("abc"); w.ac_fstart.editingFinished.emit()
    assert not w.b_run.isEnabled() and w.ac_fstart.property("invalid")
    w.ac_fstart.setText("1"); w.ac_fstart.editingFinished.emit()
    w.a_run.trigger()
    assert w.result.ok and w.result.analysis == "ac", w.result.message
    w.probe.setCurrentText("V(C1)")
    assert w.ax.get_xscale() == "log" and w.results.ax_ph.get_visible() and w.ax.get_ylabel() == "Magnitude (dB)"
    assert w.results.ax_ph.get_ylabel() == "Phase (°)" and not w.play_btn.isEnabled()
    w.set_cursor_time(FC)
    assert "-3.01 dB" in w.results.readout.text() and "∠ -45.0°" in w.results.readout.text(), w.results.readout.text()
    w.slider.setValue(w.slider.maximum() // 2)   # log slider: mid-range is the geometric mean, √(1 · 100k)
    assert w.cur_t == pytest.approx(math.sqrt(1e5), rel=0.01)
    assert w.part_current("R1") is None and w.led_glow("R1") == 0
    w.live()
    assert w.session is None and w.status.text() == LIVE_AC
    w.analysis.setCurrentIndex(0); w.analysis.activated.emit(0)
    assert w.doc.analysis == "transient" and w.result is None and w.stale   # the analysis is a run setting
    w.a_rerun.trigger()
    assert w.result.ok and w.result.analysis == "transient" and w.ax.get_xscale() == "linear" and not w.results.ax_ph.get_visible()
    assert w.b_live.isEnabled()
