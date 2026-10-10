"""LED glow + time cursor/playback on the real core (offscreen)."""
import pytest
from PySide6.QtWidgets import QApplication
from app.model import led_level, Document
from app.window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(shown_window):
    return shown_window


def test_dc_led_glows_at_end_matches_mapping(win):
    w = win; w.load_led_example(); assert w.canvas.led_glow("D1") == 0  # not run yet
    w.a_run.trigger()
    i = w.result.series["@d1[id]"][-1][1]
    assert 5e-3 < i < 15e-3
    assert w.canvas.led_glow("D1") == pytest.approx(led_level(i)) and w.canvas.led_glow("D1") > 0.5
    assert "t =" in w.readout.text()
    w.doc.set_param("R1", "R", "470"); w.refresh()          # stale -> cleared
    assert w.result is None and w.canvas.led_glow("D1") == 0 and not w.slider.isEnabled()


def test_reversed_led_dark(win):
    w = win;
    d = Document(); d.add_part("BAT"); d.add_part("R", R=330); d.add_part("LED"); d.add_part("GND")
    for a, b in (("V1.p", "R1.a"), ("R1.b", "D1.k"), ("D1.a", "GND1.g"), ("V1.n", "GND1.g")): d.connect(a, b)
    w._reset(d); w.a_run.trigger(); assert w.result.ok
    assert w.canvas.led_glow("D1") == 0


def _times(w):
    s = w.result.series["v(V1)"]
    return next(t for t, v in s if v > 4.5), next(t for t, v in s if v < 0.5 and t > 2e-3)


def test_blink_cursor_scrubs_on_off(win):
    w = win; w.load_blink_example(); w.a_run.trigger(); assert w.result.ok
    ts = [t for t, _ in w.result.series["v(V1)"]]
    assert max(b - a for a, b in zip(ts, ts[1:])) <= 2e-3 / 50   # pulse edges resolved
    assert w.canvas.led_glow("D1") > 0.5                           # run ends in the ON phase
    t_on, t_off = _times(w)
    for t, on in ((t_off, False), (t_on, True)):
        w.slider.setValue(round(10000 * (t - ts[0]) / (ts[-1] - ts[0])))
        assert abs(w.cur_t - t) < 2e-6
        assert (w.canvas.led_glow("D1") > 0.5) if on else (w.canvas.led_glow("D1") < 0.05)
        assert w.cur_line.get_xdata()[0] == pytest.approx(w.cur_t)
    assert "V(" in w.readout.text()


def test_play_pause_advances_and_loops(win):
    w = win; w.load_blink_example(); w.a_run.trigger()
    w.slider.setValue(0); assert not w.timer.isActive()
    w.play_btn.click(); assert w.timer.isActive() and w.play_btn.text() == "Pause"
    w.tick(); a = w.cur_t; w.tick(); assert w.cur_t > a > w.t_rng[0]
    w.slider.setValue(10000); w.tick(); assert w.cur_t == w.t_rng[0]   # loops
    w.play_btn.click(); assert not w.timer.isActive() and w.play_btn.text() == "Play"
