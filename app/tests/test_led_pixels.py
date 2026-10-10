"""What is actually painted: grab() the canvas and read LED pixels (accessor values are not enough)."""
import pytest
from PySide6.QtWidgets import QApplication
from conftest import wait_for
from app.results import LOOP_SECONDS
from app.window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(shown_window):
    shown_window.load_blink_example(); shown_window.a_run.trigger()
    assert wait_for(shown_window.plotw.isVisible)
    return shown_window


def reddish(w):
    """Count of lit (red/orange) pixels in the LED's rect of the grabbed canvas."""
    QApplication.processEvents()
    cv = w.canvas; img = cv.grab().toImage()
    c = cv.viewport().mapTo(cv, cv.mapFromScene(350, 115))
    return sum(1 for x in range(c.x() - 12, c.x() + 12) for y in range(c.y() - 12, c.y() + 12)
               if img.pixelColor(x, y).red() > 200 and img.pixelColor(x, y).green() < 150)


def test_painted_pixels_differ_on_off(win):
    w = win
    w.set_cursor_time(2.5e-3); on = reddish(w)      # PULSE high 2-3 ms
    w.set_cursor_time(3.5e-3); off = reddish(w)     # low 3-4 ms
    assert on > 100 and off == 0


def test_play_ticks_toggle_painted_led(win):
    w = win; w.timer.stop(); w.set_cursor_time(w.t_rng[0]); states = []
    for _ in range(100):                            # one loop of timer-sized ticks
        w.tick(); states.append(reddish(w) > 50)
    toggles = sum(a != b for a, b in zip(states, states[1:]))
    assert toggles >= 7 and states.count(True) > 25 and states.count(False) > 25   # ~4 ON phases, each several frames


def test_tick_does_not_redraw_whole_plot(win):
    w = win; n = []; w.plotw.mpl_connect("draw_event", lambda e: n.append(1)); w.timer.stop()
    QApplication.processEvents(); n.clear()
    for _ in range(20): w.tick(); QApplication.processEvents()
    assert not n     # a full matplotlib redraw (~40 ms) per frame starves the 40 ms timer and the LED repaints


def test_autoplay_after_run_only_when_enabled(qapp):
    w = MainWindow(); w.show(); w.load_blink_example(); w.a_run.trigger()
    assert not w.timer.isActive()                   # default off: tests stay deterministic
    w.autoplay = True; w.a_run.trigger()
    assert w.timer.isActive() and w.play_btn.text() == "Pause"
    w.timer.stop()                                  # the frames are driven by tick(), not by the wall clock
    seen = set()
    for _ in range(100):                            # one loop (LOOP_SECONDS) in 100 frames
        w.tick(LOOP_SECONDS / 100); seen.add(reddish(w) > 50)
    assert seen == {True, False}                    # LED lit and dark over one loop
    w.timer.start()
    w.play_btn.click(); assert not w.timer.isActive()
    w.load_led_example(); w.a_run.trigger(); assert not w.timer.isActive()   # DC circuit: no autoplay
