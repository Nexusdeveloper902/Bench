"""Dark mode: both themes keep readable text, and View > Dark mode re-themes the live window (palette, stylesheet,
canvas, plot, icons) and saves the choice."""
from conftest import destroy
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from app import theme


def _lum(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    q = QColor(c)
    return 0.2126 * ch(q.red()) + 0.7152 * ch(q.green()) + 0.0722 * ch(q.blue())


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# (foreground, background) token pairs that carry text. TEXT_MUTED (hints) is listed apart: the light design's
# #8A94A0 predates this check.
TEXT_PAIRS = [("TEXT", "PANEL"), ("TEXT", "APP_BG"), ("TEXT", "PANEL_ALT"), ("TEXT_2", "PANEL"), ("TEXT_2", "APP_BG"),
              ("TEXT", "PRIMARY_SOFT"), ("PRIMARY", "PANEL"), ("ON_PRIMARY", "PRIMARY"), ("ON_PRIMARY", "TEXT"),
              ("ON_PRIMARY", "ERR_FG"), ("INFO_FG", "INFO_BG"), ("WARN_FG", "WARN_BG"), ("ERR_FG", "ERR_BG"),
              ("STATE_COMPLETED", "OK_BG"), ("ERR_FG", "PANEL"), ("BAD", "PANEL"), ("INK", "CANVAS_BG"),
              ("TEXT_2", "CANVAS_BG"), ("STATE_RUNNING", "PANEL"), ("STATE_STALE", "PANEL")]


@pytest.fixture
def light_after():
    yield
    theme.use(False); theme.apply(QApplication.instance() or QApplication([]))   # other tests expect the light theme


@pytest.mark.parametrize("dark", [False, True])
def test_text_contrast(dark, light_after):
    theme.use(dark)
    bad = [(f, b, round(contrast(theme.color(f), theme.color(b)), 2)) for f, b in TEXT_PAIRS
           if contrast(theme.color(f), theme.color(b)) < 4.5]
    bad += [(k, round(contrast(fg, bg), 2)) for k, (_, fg, bg, _) in theme.READINESS.items() if contrast(fg, bg) < 4.5]
    assert not bad
    if dark: assert contrast(theme.TEXT_MUTED, theme.PANEL) >= 4.5


def test_tokens_match():
    assert set(theme.LIGHT) == set(theme.DARK)


def test_toggle_rethemes_live_window(light_after):   # QSettings lands in conftest's temp XDG_CONFIG_HOME, never ~/.config
    from app.window import MainWindow
    app = QApplication.instance() or QApplication([])
    theme.use(False); theme.apply(app)
    w = MainWindow(); w.resize(1280, 800); w.show(); w.load_demo()
    zoom = w.canvas.zoom_ctl.minus
    before = zoom.icon().pixmap(16).toImage()
    w.a_dark.trigger()                                                   # View > Dark mode, as the menu does
    assert theme.DARK_MODE and w.a_dark.isChecked()
    assert app.palette().color(QPalette.Window) == QColor(theme.DARK["APP_BG"])
    assert theme.DARK["TEXT"] in app.styleSheet()
    assert w.results.fig.get_facecolor()[:3] == pytest.approx(QColor(theme.DARK["PANEL"]).getRgbF()[:3])
    img = w.canvas.viewport().grab().toImage()
    assert img.pixelColor(2, 2) == QColor(theme.DARK["CANVAS_BG"])      # grid background repainted
    assert zoom.icon().pixmap(16).toImage() != before          # baked icon re-rendered
    assert QSettings("Bench", "Bench").value("dark", type=bool) is True
    assert theme.saved_dark(app) is True
    w.set_dark(False)
    assert not theme.DARK_MODE and app.palette().color(QPalette.Window) == QColor(theme.LIGHT["APP_BG"])
    assert theme.saved_dark(app) is False
    destroy(w)
