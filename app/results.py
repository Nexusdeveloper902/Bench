"""Results dock: the waveform viewer (signal picker, play / slider / time cursor, plot, readout, stats, message banner,
failure / blocked / running / not-run states, "Show details" with core findings and developer text) and the Problems
list. Reads RunResult only; no simulation logic. Design: docs/milestone/03-design.md (results frames)."""
import time

import matplotlib
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QProgressBar, QPushButton, QSlider, QStackedWidget, QStyledItemDelegate, QVBoxLayout,
                               QWidget)

from . import theme, widgets as W
from .components import format_si
from .model import value_at

matplotlib.rcParams.update({"font.family": [theme.FONT, "DejaVu Sans"], "font.size": 7})

SLIDER_MAX = 10000
LOOP_SECONDS = 4.0   # wall-clock time for one pass over the simulated window (blink example: ~1 Hz)
GROUPS = (("node", "Node voltages"), ("across", "Voltage across parts"), ("current", "Currents"))
QUANTITY = {"V": "Voltage", "A": "Current"}


def eng(m):
    """(scale, prefix) for showing magnitudes around m: 0.0047 -> (1e-3, 'm')."""
    for s, pre in ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p")):
        if m >= s * 0.9999: return s, pre
    return (1, "") if m == 0 else (1e-12, "p")


class PlayButton(QPushButton):
    """32 x 32 icon-only play / pause button. Its text ('Play' / 'Pause') is the accessible name, never painted."""
    def __init__(self):
        super().__init__("Play")
        self.setFixedSize(32, 32); self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        on = self.isEnabled()
        p.setPen(QPen(QColor(theme.PRIMARY if on else theme.DIVIDER), 1))
        p.setBrush(QColor(theme.PRIMARY_SOFT if on else theme.DISABLED_BG))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), theme.R_MD, theme.R_MD)
        pm = W.svg_pixmap("pause" if self.text() == "Pause" else "play", theme.PRIMARY if on else theme.TEXT_DISABLED, 16)
        p.drawPixmap(8, 8, pm)


def banner(kind, icon_name, icon_color):
    """Flat message box with an icon: -> (frame, layout of its text column)."""
    f = QFrame(); f.setObjectName("banner"); f.setProperty("kind", kind)
    row = QHBoxLayout(f); row.setContentsMargins(12, 6, 12, 6); row.setSpacing(8)
    ic = W.icon_label(icon_name, icon_color); row.addWidget(ic, 0, Qt.AlignTop)
    col = QVBoxLayout(); col.setSpacing(2); col.setContentsMargins(0, 0, 0, 0); row.addLayout(col, 1)
    f.icon = ic
    return f, col


class ResultsPanel(QWidget):
    cursor_moved = Signal()   # the time cursor moved: the canvas redraws its LED glow

    def __init__(self):
        super().__init__()
        self.setObjectName("results")
        self.result, self.want = None, None   # want: the signal the user picked; kept across reruns
        self.cur_t, self.cur_line, self.cur_dot, self.t_rng = None, None, None, (0.0, 1.0)
        self.bg, self._last = None, 0.0
        root = QVBoxLayout(self); root.setContentsMargins(16, 8, 16, 8); root.setSpacing(4)
        # header: title, signal picker, details link
        head = QHBoxLayout(); head.setSpacing(10)
        head.addWidget(W.label("Results", "panelhead"))
        self.probe = QComboBox(); self.probe.setMinimumWidth(90); self.probe.setMaximumWidth(240)
        self.probe.setPlaceholderText("Signal: none")
        self.probe.setToolTip("Signal to plot. Tip: click a wire on the canvas to plot its node voltage.")
        self.probe.currentTextChanged.connect(self._probe_changed)
        self.details_btn = QPushButton("Show details"); self.details_btn.setCheckable(True)
        self.details_btn.setProperty("link", True); self.details_btn.setCursor(Qt.PointingHandCursor)
        self.details_btn.toggled.connect(self._details_toggled)
        head.addWidget(self.probe, 1); head.addStretch(1); head.addWidget(self.details_btn)
        root.addLayout(head)
        self.pages = QStackedWidget(); root.addWidget(self.pages, 1)
        self._empty_page(); self._running_page(); self._ok_page(); self._fail_page(); self._details_page()
        self.timer = QTimer(self); self.timer.setInterval(40); self.timer.timeout.connect(self._on_timer)
        self.play_btn.clicked.connect(self.toggle_play)
        self.slider.valueChanged.connect(
            lambda v: self.set_cursor_time(self.t_rng[0] + (self.t_rng[1] - self.t_rng[0]) * v / SLIDER_MAX))
        self.set_view(None, "idle")

    # ---- pages ----
    def _empty_page(self):
        w = QWidget(); l = QVBoxLayout(w); l.setAlignment(Qt.AlignCenter)
        self.empty_title, self.empty_text = W.label("No results yet", "emptytitle"), W.label("", "hint", wrap=True)
        self.empty_title.setAlignment(Qt.AlignCenter); self.empty_text.setAlignment(Qt.AlignCenter)
        self.stale_banner, col = banner("warn", "alert", theme.WARN_FG)
        col.addWidget(W.label("Results out of date: re-run to refresh.", wrap=True))
        l.addWidget(self.stale_banner); l.addSpacing(6); l.addWidget(self.empty_title); l.addWidget(self.empty_text)
        self.pages.addWidget(w)

    def _running_page(self):
        w = QWidget(); l = QVBoxLayout(w); l.setAlignment(Qt.AlignCenter)
        t = W.label("Running...", "emptytitle"); t.setAlignment(Qt.AlignCenter)
        d = W.label("Simulating. Signals and the plot appear when the run finishes. Run, Re-run and Reset are "
                    "disabled until then.", "hint", wrap=True); d.setAlignment(Qt.AlignCenter)
        bar = QProgressBar(); bar.setRange(0, 0); bar.setTextVisible(False); bar.setFixedWidth(300)
        l.addWidget(t); l.addWidget(d); l.addSpacing(8); l.addWidget(bar, 0, Qt.AlignHCenter)
        self.pages.addWidget(w)

    def _ok_page(self):
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(0, 0, 0, 0); l.setSpacing(4)
        self.play_btn, self.slider, self.time_lbl = PlayButton(), QSlider(Qt.Horizontal), W.label("", "timelbl")
        self.slider.setRange(0, SLIDER_MAX); self.slider.setEnabled(False); self.play_btn.setEnabled(False)
        row = QHBoxLayout(); row.setSpacing(10)
        row.addWidget(self.play_btn); row.addWidget(self.slider, 1); row.addWidget(self.time_lbl)
        l.addLayout(row)
        self.fig = Figure(figsize=(5, 1.4), layout="constrained", facecolor=theme.PANEL); self.ax = self.fig.add_subplot()
        self.plotw = FigureCanvasQTAgg(self.fig); self.plotw.setMinimumSize(120, 70)
        self.plotw.mpl_connect("draw_event", self._on_draw)
        self.plotw.mpl_connect("button_press_event",
                               lambda e: e.inaxes is self.ax and e.xdata is not None and self.set_cursor_time(e.xdata))
        l.addWidget(self.plotw, 1)
        self.readout, self.stats = W.label("", "readout"), W.label("", "stats")
        l.addWidget(self.readout); l.addWidget(self.stats)
        self.ok_banner, col = banner("ok", "check", theme.STATE_COMPLETED)
        self.msg = W.label("", wrap=True); col.addWidget(self.msg); l.addWidget(self.ok_banner)
        self.pages.addWidget(w)

    def _fail_page(self):
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(0, 4, 0, 0); l.setSpacing(8)
        self.err_box, col = banner("err", "xcircle", theme.ERR_FG)
        self.err_title, self.err = W.label("", "bold"), W.label("", wrap=True)
        col.addWidget(self.err_title); col.addWidget(self.err)
        self.noplot = W.label("", "hint"); self.noplot.setAlignment(Qt.AlignCenter)
        l.addWidget(self.err_box); l.addWidget(self.noplot); l.addStretch(1)
        self.pages.addWidget(w)

    def _details_page(self):
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(0, 0, 0, 0); l.setSpacing(4)
        self.findings_head, self.findings = W.section("Core findings"), W.label("", "finding", wrap=True)
        self.details = QPlainTextEdit(); self.details.setReadOnly(True); self.details.setObjectName("devtext")
        self.details.setMinimumHeight(60)
        l.addWidget(self.findings_head); l.addWidget(self.findings); l.addSpacing(4)
        l.addWidget(W.section("Developer details")); l.addWidget(self.details, 1)
        self.pages.addWidget(w)

    # ---- state ----
    def set_view(self, r, state, stale=False):
        """Sync everything with RunResult `r` (None = no results). state: idle | running | completed | failed |
        blocked; stale: the circuit changed since the last run (results were dropped)."""
        self.result = r
        self.findings.setText("\n".join(f"{f['code']}: {f['message']}" for f in (r.findings if r else [])))
        self.findings_head.setText(f"CORE FINDINGS ({len(r.findings)})" if r and r.findings else "")
        self.findings_head.setVisible(bool(r and r.findings)); self.findings.setVisible(bool(r and r.findings))
        title = "Run blocked" if r and r.status == "blocked" else "Run failed"
        self.err_title.setText(title if r and not r.ok else ""); self.err.setText("" if not r or r.ok else r.message)
        self.noplot.setText("No plot: the run did not start." if r and r.status == "blocked" else
                            "No plot: nothing was simulated.")
        self.details.setPlainText(r.details if r else "")
        self.details_btn.setEnabled(r is not None)
        if r is None: self.details_btn.setChecked(False)
        ok = bool(r and r.ok)
        if ok:
            self.msg.setText(r.message)
            kind, ic = ("warn", ("alert", theme.WARN_FG)) if r.excluded else ("ok", ("check", theme.STATE_COMPLETED))
            self.ok_banner.setProperty("kind", kind)
            for x in (self.ok_banner, self.msg): theme.repolish(x)
            self.ok_banner.icon.setPixmap(W.svg_pixmap(*ic, 16))
        self.probe.blockSignals(True); self.probe.clear()
        first = -1
        for kind, title in GROUPS:
            sigs = [s for s in (r.signals if ok else []) if s["kind"] == kind]
            if not sigs: continue
            self.probe.addItem(title.upper())
            it = self.probe.model().item(self.probe.count() - 1)
            it.setEnabled(False); f = it.font(); f.setPixelSize(theme.CAPTION_PX); f.setWeight(QFont.DemiBold); it.setFont(f)
            for s in sigs:
                self.probe.addItem(s["name"])
                self.probe.setItemData(self.probe.count() - 1, ", ".join(s["pins"]), Qt.ToolTipRole)
                if first < 0 or s["name"] == self.want: first = self.probe.count() - 1
        self.probe.setCurrentIndex(first)
        self.probe.blockSignals(False)
        self.probe.setEnabled(ok)
        self.probe.setVisible(r is not None)
        self.details_btn.setVisible(r is not None)
        self._init_cursor(); self.plot(self.probe.currentText())
        if state == "running": page = 1
        elif r is None: page = 0
        else: page = 2 if ok else 3
        if r is not None and self.details_btn.isChecked(): page = 4
        self.empty_text.setText("Results were dropped because the circuit or the settings changed. Press Re-run "
                                "(Ctrl+F5) to simulate again." if stale else
                                "Press Run (F5) to simulate the circuit. Signals and the plot appear here.")
        self.empty_title.setVisible(not stale); self.stale_banner.setVisible(stale)
        self.pages.setCurrentIndex(page)
        self.details_btn.setEnabled(r is not None and state != "running")

    def _details_toggled(self, on):
        self.details_btn.setText("Hide details" if on else "Show details")
        if self.result is not None:
            self.pages.setCurrentIndex(4 if on else 2 if self.result.ok else 3)

    def _probe_changed(self, name):
        if self.result and self.result.signal(name): self.want = name
        self.plot(name)

    # ---- waveform viewer (reads RunResult only) ----
    def plot(self, name):
        self.ax.clear()
        r = self.result
        sig = r.signal(name) if r and r.ok else None
        self.cur_line = self.cur_dot = None
        self.ax.set_facecolor("#FFFFFF")
        for s in self.ax.spines.values(): s.set_color(theme.BORDER_STRONG); s.set_linewidth(0.8)
        self.ax.tick_params(colors=theme.TEXT_MUTED, labelsize=7, length=2, pad=2)
        if sig:
            ser = r.series[sig["vector"]]
            t, v = zip(*ser)
            self.ax.plot(t, v, color=theme.WIRE, lw=1.6)
            (ts, tp), (vs, vp) = eng(max(abs(t[0]), abs(t[-1]))), eng(max(map(abs, v)))
            self.ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _, s=ts: f"{x / s:.6g}"))
            self.ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _, s=vs: f"{x / s:.6g}"))
            self.ax.set_xlabel(f"time ({tp}s)"); self.ax.set_ylabel(f"{QUANTITY[sig['unit']]} ({vp}{sig['unit']})")
            self.ax.xaxis.label.set_visible(False); self.ax.yaxis.label.set_visible(False)   # the units sit inside the plot
            kw = dict(fontsize=7, color=theme.TEXT_MUTED, transform=self.ax.transAxes)
            self.ax.text(0.995, 0.04, f"{tp}s", ha="right", va="bottom", **kw)
            self.ax.text(0.005, 0.96, f"{vp}{sig['unit']}", ha="left", va="top", **kw)
            self.ax.set_title(sig["name"]); self.ax.title.set_visible(False)   # the picker names the signal
            self.ax.grid(True, color=theme.GRID, lw=0.8); self.ax.set_axisbelow(True)
            x = self.cur_t if self.cur_t is not None else t[-1]
            self.cur_line = self.ax.axvline(x, color=theme.OPEN_PIN, lw=1.2, animated=True)   # drawn by blit, not by a
            self.cur_dot, = self.ax.plot([x], [value_at(ser, x)], "o", color=theme.OPEN_PIN, ms=4, animated=True)  # full redraw
            s, u = r.stats(name), sig["unit"]
            self.stats.setText("   ".join(f"{k} {format_si(s[k], u, 6)}" for k in ("final", "min", "max")))
        else:
            self.stats.setText("")
            self.ax.set_xticks([]); self.ax.set_yticks([])
        self.bg = None; self._readout(); self.plotw.draw_idle()

    def view_data(self):
        """(signal dict, [(t, v)]) currently plotted - straight from RunResult.series - or None."""
        r, name = self.result, self.probe.currentText()
        sig = r.signal(name) if r and r.ok else None
        return (sig, r.series[sig["vector"]]) if sig else None

    # ---- time cursor / playback (reads RunResult.series only) ----
    def _init_cursor(self):
        """Called whenever the result changes: stop playback, cursor to end of run (or disabled if no result)."""
        self.timer.stop(); self.play_btn.setText("Play"); self.play_btn.update()
        ok = bool(self.result and self.result.ok and self.result.series)
        self.slider.setEnabled(ok); self.play_btn.setEnabled(ok)
        if ok:
            ser = next(iter(self.result.series.values()))
            self.t_rng = (ser[0][0], ser[-1][0]); self.cur_t = self.t_rng[1]
        else:
            self.cur_t = None
        self.slider.blockSignals(True); self.slider.setValue(SLIDER_MAX); self.slider.blockSignals(False)

    def set_cursor_time(self, t):
        if self.cur_t is None: return
        a, b = self.t_rng
        self.cur_t = max(a, min(b, t))
        v = round(SLIDER_MAX * (self.cur_t - a) / (b - a)) if b > a else SLIDER_MAX
        if self.slider.value() != v:
            self.slider.blockSignals(True); self.slider.setValue(v); self.slider.blockSignals(False)
        vd = self.view_data()
        if self.cur_line is not None:
            self.cur_line.set_xdata([self.cur_t, self.cur_t])
            if vd: self.cur_dot.set_data([self.cur_t], [value_at(vd[1], self.cur_t)])
            self._blit()
        self._readout(); self.cursor_moved.emit()

    def _on_draw(self, _=None):
        self.bg = self.plotw.copy_from_bbox(self.fig.bbox)
        for a in (self.cur_line, self.cur_dot):
            if a is not None: self.ax.draw_artist(a)

    def _blit(self):
        if self.bg is None or self.cur_line is None: return self.plotw.draw_idle()
        self.plotw.restore_region(self.bg)
        for a in (self.cur_line, self.cur_dot): self.ax.draw_artist(a)
        self.plotw.blit(self.fig.bbox)

    def toggle_play(self):
        if self.cur_t is None: return
        if self.timer.isActive(): self.timer.stop(); self.play_btn.setText("Play")
        else: self.play()
        self.play_btn.update()

    def play(self):
        self._last = time.perf_counter(); self.timer.start(); self.play_btn.setText("Pause"); self.play_btn.update()

    def _on_timer(self):
        now = time.perf_counter(); dt, self._last = min(now - self._last, 0.25), now
        self.tick(dt)

    def tick(self, dt=None):
        """Advance the cursor by dt wall-clock seconds (default: one timer interval); LOOP_SECONDS = one pass,
        wraps to the start. Driven by elapsed time, so slow frames skip ahead instead of stretching the blink."""
        if self.cur_t is None: return self.timer.stop()
        a, b = self.t_rng
        t = self.cur_t + (self.timer.interval() / 1000 if dt is None else dt) / LOOP_SECONDS * (b - a)
        self.set_cursor_time(a if t > b else t)

    def _readout(self):
        vd = self.view_data()
        if self.cur_t is None or not vd:
            self.time_lbl.setText("")
            return self.readout.setText("")
        sig, ser = vd
        s, p = eng(max(abs(self.t_rng[1]), abs(self.t_rng[0])))
        self.time_lbl.setText(f"{self.cur_t / s:.2f} / {self.t_rng[1] / s:.2f} {p}s")
        self.readout.setText(f"t = {format_si(self.cur_t, 's', 5)}    "
                             f"{sig['name']} = {format_si(value_at(ser, self.cur_t), sig['unit'], 5)}")


class ProblemRow(QStyledItemDelegate):
    """Problems row: severity icon, code (mono, coloured by severity) and the wrapped message, divider below."""
    def sizeHint(self, option, index):
        d, width = index.data(Qt.UserRole + 1), self.parent().viewport().width()
        f = QFont(theme.FONT); f.setPixelSize(theme.BODY_PX)
        h = QFontMetrics(f).boundingRect(0, 0, max(60, width - 52), 10_000, Qt.TextWordWrap, d["message"]).height()
        return QSize(width, 8 + 15 + 2 + h + 10)

    def paint(self, p, option, index):
        d = index.data(Qt.UserRole + 1)
        err = d["severity"] == "error"
        p.save(); p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect)
        p.drawPixmap(int(r.left() + 16), int(r.top() + 10), W.svg_pixmap("xcircle" if err else "alert",
                                                                       theme.ERR_FG if err else theme.WARN_FG, 16))
        mono = QFont(theme.MONO); mono.setPixelSize(theme.CAPTION_PX); mono.setWeight(QFont.Bold)
        p.setFont(mono); p.setPen(QColor(theme.ERR_FG if err else theme.WARN_FG))
        p.drawText(QRectF(r.left() + 40, r.top() + 8, r.width() - 52, 15), Qt.AlignLeft | Qt.AlignVCenter, d["code"])
        body = QFont(theme.FONT); body.setPixelSize(theme.BODY_PX)
        p.setFont(body); p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(r.left() + 40, r.top() + 25, r.width() - 52, r.height() - 25), Qt.TextWordWrap | Qt.AlignTop,
                   d["message"])
        p.setPen(QColor(theme.DIVIDER)); p.drawLine(int(r.left() + 12), int(r.bottom()), int(r.right() - 12), int(r.bottom()))
        p.restore()


class ProblemsPanel(QWidget):
    """Live pre-run diagnostics: title + count pill, rows (click selects the part), 'No problems' empty state."""
    part_clicked = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("problems")
        root = QVBoxLayout(self); root.setContentsMargins(0, 10, 0, 0); root.setSpacing(6)
        head = QHBoxLayout(); head.setContentsMargins(16, 0, 16, 0); head.setSpacing(10)
        self.title = W.label("Problems", "panelhead"); self.pill = W.label("0", "countpill")
        self.pill.setAlignment(Qt.AlignCenter); self.pill.setMinimumWidth(18)
        head.addWidget(self.title); head.addWidget(self.pill); head.addStretch(1)
        root.addLayout(head)
        self.list = QListWidget(); self.list.setObjectName("problemlist"); self.list.setItemDelegate(ProblemRow(self.list))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.list.setFocusPolicy(Qt.NoFocus)
        self.list.itemClicked.connect(lambda it: it.data(Qt.UserRole) and self.part_clicked.emit(it.data(Qt.UserRole)))
        self.empty = QWidget(); el = QVBoxLayout(self.empty); el.setAlignment(Qt.AlignCenter)
        ck = W.icon_label("check", theme.STATE_COMPLETED); el.addWidget(ck, 0, Qt.AlignHCenter)
        el.addWidget(W.label("No problems", "hint"), 0, Qt.AlignHCenter)
        self.stack = QStackedWidget(); self.stack.addWidget(self.list); self.stack.addWidget(self.empty)
        root.addWidget(self.stack, 1)

    def set_diagnostics(self, diags):
        self.list.clear()
        for dg in diags:
            it = QListWidgetItem(f"{dg['severity'].capitalize()}: {dg['message']}  [{dg['code']}]")
            it.setData(Qt.UserRole, dg["part"]); it.setData(Qt.UserRole + 1, dg); it.setToolTip(dg["message"])
            self.list.addItem(it)
        ne = sum(dg["severity"] == "error" for dg in diags)
        self.pill.setText(str(len(diags))); self.pill.setVisible(bool(diags))
        self.pill.setToolTip(f"{ne} error{'s' * (ne != 1)}, {len(diags) - ne} warning{'s' * (len(diags) - ne != 1)}")
        self.stack.setCurrentIndex(0 if diags else 1)

    def resizeEvent(self, e):
        super().resizeEvent(e); self.list.doItemsLayout()
