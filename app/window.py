"""Thin Qt layer over app.model / app.sim / app.history. All logic lives there; handlers just call it.

The window composes the top bar (file actions, undo/redo, run settings, Run / Re-run / Reset, run-state badge), the
library panel, the canvas (app.canvas), the inspector, the results dock (app.results) and the status bar. Everything
per component (pins, body box, symbol, params, units, value label, current/glow display) comes from the app.catalog
Definition of its kind; symbols are SVG files drawn by app.symbols. No per-kind code here."""
import gc
import math
import os
import traceback
from PySide6.QtCore import Qt, QSize, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu,
                               QMessageBox, QPushButton, QSizePolicy, QSplitter, QToolButton, QVBoxLayout, QWidget)
from .model import Document, ModelError, LIVE_INDEFINITE, LIVE_STOP_TIME, RUN_PARAMS, led_level, value_at
from .catalog import CATALOG
from .components import format_si
from .sim import run_document, RunResult, LiveSession, live_params, trim
from .history import History
from .canvas import Canvas, pin_pos, wire_pts, part_rect   # re-exported: tests and scripts import them from here
from .inspector import Inspector
from .library import LibraryPanel
from .results import FIT_SECONDS, LIVE_FOR, ProblemsPanel, ResultsPanel, banner
from . import theme, widgets as W


def logo_pixmap(px=28):
    """The app logo: a primary-coloured rounded square with an on-primary pulse."""
    pm = QPixmap(px * 2, px * 2); pm.fill(Qt.transparent); pm.setDevicePixelRatio(2)
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing); p.setPen(Qt.NoPen); p.setBrush(QColor(theme.PRIMARY))
    p.drawRoundedRect(0, 0, px, px, theme.R_MD, theme.R_MD)
    p.drawPixmap((px - 20) // 2, (px - 20) // 2, W.svg_pixmap("pulse", theme.ON_PRIMARY, 20)); p.end()
    return pm


class LiveWorker(QThread):
    """Iterates one LiveSession off the Qt thread (LIVE.md threading rule 1). Each chunk reaches the window as a queued
    signal; QThread.finished follows the last one. Core errors end the iteration inside the session (never raise)."""
    chunk = Signal(object)

    def __init__(self, session):
        super().__init__(); self.session, self.error = session, None

    def run(self):
        try:
            for c in self.session: self.chunk.emit(c)
        except Exception:   # an app bug: shown as a failed run, never hidden
            self.error = traceback.format_exc()


# ---------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        theme.apply(QApplication.instance())
        self.doc, self.result, self.sel, self.sel_wire = Document(), None, None, None
        self.ran_key = None     # circuit signature of the last run; a mismatch means the result is stale
        self.stale = False      # the results were dropped because the circuit or the settings changed since the run
        self.tool, self.pending_pin = None, None
        self.state = "idle"     # idle | running | completed | failed | blocked (no pause: the core runs synchronously)
        self.invalid = {}       # run-setting key -> message, for the fields that do not parse
        self.autoplay = False   # start looping after a Run of a time-varying source
        # live run (task 9.5b): the session and its worker exist from start until the worker has finished
        self.session, self.worker, self.paused, self.last_session, self.live_result = None, None, False, None, None
        self.live_vals, self.live_pending, self.live_t, self.live_rtf = {}, {}, 0.0, 0.0
        self.live_invalid = None   # why the live duration field does not parse (blocks Live only, not Run)
        self.live_flush = QTimer(self); self.live_flush.setSingleShot(True); self.live_flush.setInterval(50)
        self.live_flush.timeout.connect(self._live_flush)      # slider drags: at most one change per part per 50 ms
        self.live_redraw = QTimer(self); self.live_redraw.setSingleShot(True); self.live_redraw.setInterval(50)
        self.live_redraw.timeout.connect(self._live_redraw)    # chunks: at most one plot redraw per 50 ms
        # While the worker runs, Python's cyclic gc runs only here, on the GUI thread: gc triggered on the worker thread
        # would finalize Qt wrappers there, and PySide deletes Python-owned non-widget QObjects on the collecting
        # thread (probed), racing the GUI thread's event delivery (segfault in notify_helper).
        self.gc_timer, self.gc_ticks = QTimer(self), 0
        self.gc_timer.setInterval(200); self.gc_timer.timeout.connect(self._collect)
        self.hist = History()
        self.clean = self.doc.to_dict()
        self.canvas = Canvas(self)

        def act(text, fn, *keys, tip=None, icon=None):
            a = QAction(text, self); a.triggered.connect(lambda _=False: fn())
            if keys: a.setShortcuts([QKeySequence(k) for k in keys])
            if tip: a.setToolTip(tip)
            if icon: W.set_icon(a, icon, "TEXT_2", 18)
            self.addAction(a)   # shortcuts work with or without a visible menu
            return a
        self.a_new, self.a_open = act("New", self.new, "Ctrl+N", tip="New circuit (Ctrl+N)", icon="new"), \
            act("Open...", self.open, "Ctrl+O", tip="Open a circuit (Ctrl+O)", icon="open")
        self.a_save, self.a_saveas = act("Save", self.save, "Ctrl+S", tip="Save (Ctrl+S)", icon="save"), \
            act("Save As...", self.save_as, "Ctrl+Shift+S")
        self.a_led = act("Load LED example", self.load_led_example)
        self.a_blink = act("Load blinking LED example", self.load_blink_example)
        self.a_undo = act("Undo", self.undo, "Ctrl+Z", tip="Undo (Ctrl+Z)", icon="undo")
        self.a_redo = act("Redo", self.redo, "Ctrl+Shift+Z", "Ctrl+Y", tip="Redo (Ctrl+Y)", icon="redo")
        self.a_rotate = act("Rotate", self.rotate_selected, "R", tip="Rotate the selected part 90° (R)")
        self.a_delete = act("Delete", self.delete_selected, tip="Delete the selected part or wire (Del)")
        self.a_run = act("Run", self.run, "F5", tip="Run the transient simulation (F5)")
        self.a_rerun = act("Re-run", self.rerun, "Ctrl+F5", tip="Clear results and run again (Ctrl+F5)")
        self.a_reset = act("Reset", self.reset, tip="Clear the results (the circuit is kept)")
        self.a_live = act("Live", self.live, "F6", tip="Live run (F6): the transient runs while you move sliders on "
                          "sensors, pots, switches and sources; F6 again stops it")
        self.a_pause = act("Pause live run", self.pause_live, "F7", tip="Pause or resume the live run (F7)")
        self.a_gear = act("Gear integration", self.set_gear, tip="Integrate with Gear instead of the trapezoidal rule: "
                          "damps the alternating-sample ringing sharp edges excite on high-Q nodes (crystals, LC tanks), "
                          "and real high-Q ringing slightly too")
        self.a_gear.setCheckable(True)
        self.a_zoom_in = act("Zoom in", lambda: self.canvas.zoom_by(1.25), "Ctrl+=", "Ctrl++")
        self.a_zoom_out = act("Zoom out", lambda: self.canvas.zoom_by(1 / 1.25), "Ctrl+-")
        self.a_fit = act("Fit all parts", self.canvas.fit, "Ctrl+0")
        self.a_dark = act("Dark mode", lambda: self.set_dark(self.a_dark.isChecked()), tip="Switch between the light and dark theme")
        W.set_icon(self.a_dark, "moon"); self.a_dark.setCheckable(True); self.a_dark.setChecked(theme.DARK_MODE)
        # library (left) and inspector (right) panels
        self.library, self.inspector = LibraryPanel(), Inspector()
        self.library.place_requested.connect(self._toggle_tool)
        self.inspector.param_edited.connect(self.edit_param)
        self.inspector.plot_requested.connect(self.probe_pin)
        self.inspector.delete_requested.connect(self.delete_selected)
        self.inspector.live_edited.connect(self.live_set)
        # results dock: waveform viewer | Problems
        self.results, self.problems_panel = ResultsPanel(), ProblemsPanel()
        self.problems_panel.part_clicked.connect(self.select)
        self.results.cursor_moved.connect(self._cursor_moved)
        for n in ("probe", "stats", "readout", "findings", "err", "err_title", "details", "details_btn", "play_btn",
                  "slider", "plotw", "fig", "ax", "timer"):
            setattr(self, n, getattr(self.results, n))      # the viewer's widgets, reachable from the window
        self.problems, self.problems_lbl = self.problems_panel.list, self.problems_panel.title
        self.pace = self.results.pace
        self.results.live_pause.clicked.connect(self.pause_live)
        self.results.live_stop.clicked.connect(self.stop_live)
        self.results.live_replay.clicked.connect(self.replay_live)
        dock = QWidget(); dock.setObjectName("results"); dock.setFixedHeight(theme.DOCK_H)
        dl = QHBoxLayout(dock); dl.setContentsMargins(0, 0, 0, 0); dl.setSpacing(0)
        sep = QFrame(); sep.setObjectName("vsep")
        self.problems_panel.setMinimumWidth(150); self.problems_panel.setMaximumWidth(theme.PROBLEMS_W)   # narrows below 1280
        self.results.setMinimumWidth(240)
        dl.addWidget(self.results, 1); dl.addWidget(sep); dl.addWidget(self.problems_panel)
        rule = QFrame(); rule.setObjectName("rule")
        centre = QWidget(); cl = QVBoxLayout(centre); cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(0)
        # "opened with placeholders" banner (dialogs.png): in the layout above the canvas, so it never covers a part
        self.open_banner, col = banner("warn", "alert", "WARN_FG"); self.open_banner.hide()
        self.open_msg = W.label("", wrap=True); self.open_show = W.link("Show in Problems", self._show_placeholder)
        self.open_dismiss = W.link("Dismiss", self.open_banner.hide)
        col.addWidget(self.open_msg)
        for b in (self.open_show, self.open_dismiss): self.open_banner.layout().addWidget(b, 0, Qt.AlignTop)
        cl.addWidget(self.open_banner); cl.addWidget(self.canvas, 1); cl.addWidget(rule); cl.addWidget(dock)
        sp = QSplitter(); sp.addWidget(self.library); sp.addWidget(centre); sp.addWidget(self.inspector)
        sp.setStretchFactor(1, 1); sp.setChildrenCollapsible(False)
        sp.setSizes([theme.LIBRARY_W, 1280 - theme.LIBRARY_W - theme.INSPECTOR_W, theme.INSPECTOR_W])
        root = QWidget(); rl = QVBoxLayout(root); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(0)
        rl.addWidget(self._topbar()); rl.addWidget(sp, 1)
        self.setCentralWidget(root)
        self.notice = QLabel(root); self.notice.setObjectName("notice"); self.notice.hide()   # under the top bar
        self.status = QLabel("Ready."); self.status.setContentsMargins(8, 0, 0, 0)
        self.counts = QLabel(""); self.counts.setObjectName("hint"); self.counts.setContentsMargins(0, 0, 8, 0)
        self.statusBar().setSizeGripEnabled(False); self.statusBar().setFixedHeight(theme.STATUS_H)
        self.statusBar().addWidget(self.status, 1); self.statusBar().addPermanentWidget(self.counts)
        self.resize(1280, 800)
        self._show_result()
        self.refresh()

    # --- top bar ---
    def _topbar(self):
        bar = QFrame(); bar.setObjectName("topbar"); bar.setFixedHeight(theme.TOPBAR_H)
        bar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)   # narrow windows compact it (see resizeEvent)
        lay = QHBoxLayout(bar); lay.setContentsMargins(16, 0, 16, 0); lay.setSpacing(8)
        app = QToolButton(); app.setObjectName("appbtn"); app.setText(" Bench"); W.themed(app, lambda: app.setIcon(QPixmap(logo_pixmap())))
        app.setIconSize(QSize(28, 28)); app.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        app.setPopupMode(QToolButton.InstantPopup); app.setToolTip("Menu: file, edit, simulation, view")
        menu = QMenu(app)
        for title, acts in (("File", (self.a_new, self.a_open, self.a_save, self.a_saveas, None, self.a_led, self.a_blink)),
                            ("Edit", (self.a_undo, self.a_redo, self.a_rotate, self.a_delete)),
                            ("Simulation", (self.a_run, self.a_rerun, self.a_reset, None, self.a_live, self.a_pause,
                                            None, self.a_gear)),
                            ("View", (self.a_zoom_in, self.a_zoom_out, self.a_fit, None, self.a_dark))):
            sub = menu.addMenu(title)
            for a in acts: sub.addSeparator() if a is None else sub.addAction(a)
        app.setMenu(menu); self.app_menu = menu
        lay.addWidget(app); lay.addSpacing(8)

        def ibtn(a):
            b = QToolButton(); b.setObjectName("iconbtn"); b.setDefaultAction(a); b.setToolButtonStyle(Qt.ToolButtonIconOnly)
            b.setIconSize(QSize(18, 18)); b.setFixedSize(32, 32); return b
        for group in ((self.a_new, self.a_open, self.a_save), (self.a_undo, self.a_redo)):
            g = QHBoxLayout(); g.setSpacing(3); g.setContentsMargins(0, 0, 0, 0)
            for a in group: g.addWidget(ibtn(a))
            lay.addLayout(g); lay.addWidget(W.vline(24))
        self.file_lbl = W.label("", "filename"); self.file_lbl.setTextFormat(Qt.RichText)
        self.file_lbl.setMinimumWidth(40); self.file_lbl.setMaximumWidth(260)
        lay.addWidget(self.file_lbl, 1); lay.addSpacing(8)
        self.analysis = W.label("Transient", "chipbox"); self.analysis.setFixedHeight(28)   # the only analysis the core supports
        self.analysis.setToolTip("Analysis type. The simulation core supports transient analysis only.")
        lay.addWidget(self.analysis)
        self.tstop, self.tstep = QLineEdit(), QLineEdit()
        self.field_labels = []
        for lab, ed, tip in (("Stop time", self.tstop, "Simulated time span, e.g. 5m = 5 ms"),
                             ("Time step", self.tstep, "Maximum solver step / output resolution, e.g. 10u = 10 µs")):
            ed.setObjectName("topfield"); ed.setFixedSize(84, 28); ed.setProperty("help", tip); ed.setToolTip(tip)
            ed.textEdited.connect(self.cfg_changed); ed.editingFinished.connect(self.cfg_changed)
            fl = W.label(lab, "fieldlabel"); self.field_labels.append(fl)
            lay.addWidget(fl); lay.addWidget(ed)
        # live-run duration (task 9.7; saved with the circuit): a time, "Stop time" or "Indefinite"
        self.live_for = QComboBox(); self.live_for.setObjectName("topcombo"); self.live_for.setEditable(True)
        self.live_for.setInsertPolicy(QComboBox.NoInsert); self.live_for.addItems(LIVE_FOR)
        self.live_for.setProperty("help", "Live run duration, in simulated time: type a time (e.g. 2, 500m), or pick "
                                  "Stop time (the batch stop time) or Indefinite (until you press Stop; the plot then "
                                  "scrolls over the last stop time of the run)")
        self.live_for.setToolTip(self.live_for.property("help"))
        self.live_for.lineEdit().textEdited.connect(self.live_for_changed)
        self.live_for.lineEdit().editingFinished.connect(self.live_for_changed)
        self.live_for.activated.connect(self.live_for_changed)
        for sig in (self.live_for.lineEdit().editingFinished, self.live_for.activated):   # show the start of the text
            sig.connect(lambda *_: self.live_for.lineEdit().setCursorPosition(0))
        self.live_for_lbl = W.label("Live for", "fieldlabel"); self.field_labels.append(self.live_for_lbl)
        lay.addSpacing(8)
        self.b_run, self.b_rerun, self.b_reset = (QPushButton(t) for t in ("Run", "Re-run", "Reset"))
        for b, a, prim in ((self.b_run, self.a_run, True), (self.b_rerun, self.a_rerun, True), (self.b_reset, self.a_reset, False)):
            b.setObjectName("simbtn"); b.setProperty("primary", prim); b.setFixedSize(96, 32); b.setToolTip(a.toolTip())
            b.clicked.connect(lambda _=False, a=a: a.trigger())
            if prim:
                W.set_icon(b, "play", "ON_PRIMARY", 16); b.setIconSize(QSize(16, 16))
            lay.addWidget(b)
        self.b_live = QPushButton("Live"); self.b_live.setObjectName("simbtn"); self.b_live.setProperty("primary", False)
        self.b_live.setFixedSize(96, 32); self.b_live.setToolTip(self.a_live.toolTip()); self.b_live.clicked.connect(self.live)
        W.set_icon(self.b_live, "pulse", "TEXT_2", 16); self.b_live.setIconSize(QSize(16, 16))
        lay.addSpacing(4); lay.addWidget(self.live_for_lbl); lay.addWidget(self.live_for)   # read with Live: "Indefinite | Live"
        lay.addWidget(self.b_live)
        self.state_lbl = W.StateBadge(); lay.addWidget(self.state_lbl)
        self.dark_sep = W.vline(24); lay.addWidget(self.dark_sep)
        db = QToolButton(); db.setObjectName("iconbtn"); db.setDefaultAction(self.a_dark); db.setToolButtonStyle(Qt.ToolButtonIconOnly)
        db.setIconSize(QSize(18, 18)); db.setFixedSize(32, 32); lay.addWidget(db); self.dark_btn = db   # visible toggle; also under menu > View
        return bar

    def _sync_controls(self):
        """Enable and label the run controls per the Run / Re-run / Reset table (design R2) and show the notice."""
        s, live = self.state, self.session is not None
        running = s == "running" or live          # batch Run, Re-run, Reset and the run settings wait for a live run
        stale = self.stale and self.result is None and s == "idle"
        valid = not self.invalid
        run = valid and s == "idle" and not stale and not live
        rerun = valid and (s in ("completed", "failed", "cancelled") or stale) and not live
        reset = valid and (s in ("completed", "failed", "cancelled") or stale) and not live
        for b, on in ((self.b_run, run), (self.b_rerun, rerun), (self.b_reset, reset)): b.setEnabled(on)
        for w in (self.tstop, self.tstep, self.live_for, *self.field_labels, self.a_gear): w.setEnabled(not running)
        self.a_gear.setChecked(self.doc.method == "gear")
        self.state_lbl.set_state("stale" if stale else s)
        self.a_run.setEnabled(not running); self.a_rerun.setEnabled(not running)   # F5 / menu always work (R2: the primary action)
        self.b_live.setText("Stop" if live else "Live")
        self.b_live.setEnabled(live or (valid and not self.live_invalid and s != "running"))
        W.set_icon(self.b_live, "stop" if live else "pulse", "TEXT_2", 16); self.a_live.setEnabled(s != "running")
        self.a_pause.setEnabled(live); self.a_pause.setText("Resume live run" if self.paused else "Pause live run")
        r = self.results
        r.pace.setEnabled(not live)
        r.live_row.setVisible(live); r.ok_banner.setVisible(not live)
        r.live_pause.setText("Resume" if self.paused else "Pause")
        W.set_icon(r.live_pause, "play" if self.paused else "pause", "TEXT_2", 14)
        res = self.result   # a finished live run with changes can be replayed as a batch run
        r.live_replay.setVisible(not live and res is not None and res is self.live_result and bool(res.changes)
                                 and self.last_session is not None)
        self.a_reset.setEnabled((self.result is not None or stale) and not live)
        msg = next(iter(self.invalid.values()), None)
        # A failed/blocked run is shown in the results area only (design: results-running-blocked): a banner floating over
        # the canvas covered part badges (t9-blocked.png).
        notice = ("err", msg) if msg else ("warn", "Results out of date. Re-run to refresh.") if stale else None
        if notice:
            self.notice.setProperty("kind", notice[0]); theme.repolish(self.notice)
            c = self.canvas.mapTo(self.centralWidget(), self.canvas.rect().topLeft()).x()   # keep it over the canvas
            self.notice.setWordWrap(True); self.notice.setFixedWidth(min(520, self.canvas.width() - 24))
            self.notice.setText(notice[1]); self.notice.setToolTip(notice[1]); self.notice.adjustSize()
            self.notice.move(c + 12, theme.TOPBAR_H + 8)
            self.notice.raise_()
        self.notice.setVisible(bool(notice))

    def resizeEvent(self, e):
        compact = self.width() < 1520   # below the full top bar's width: no analysis chip, narrower fields and buttons
        for fl in self.field_labels: fl.setHidden(self.width() < 1150)   # narrower still: the fields keep their tooltips
        self.live_for_lbl.setHidden(compact)   # the live-duration field keeps its tooltip
        self.dark_btn.setHidden(self.width() < 1150); self.dark_sep.setHidden(self.width() < 1150)   # also View > Dark mode
        self.file_lbl.setMinimumWidth(24 if self.width() < 1150 else 40)
        if self.live_for.property("compact") != (self.width() < 1150):   # narrowest: the field shrinks too
            self.live_for.setProperty("compact", self.width() < 1150); theme.repolish(self.live_for)
        if compact != self.analysis.isHidden():
            self.analysis.setHidden(compact)
            for x in (self.tstop, self.tstep, self.b_run, self.b_rerun, self.b_reset, self.b_live):
                x.setProperty("compact", compact); theme.repolish(x)
        super().resizeEvent(e); self._sync_controls()

    # --- file path lives on the document (Document.save/load set it) ---
    @property
    def path(self): return self.doc.path

    @path.setter
    def path(self, p): self.doc.path = p

    # --- the viewer lives in app.results; the window forwards what scripts and tests use ---
    cur_t = property(lambda self: self.results.cur_t)
    t_rng = property(lambda self: self.results.t_rng)
    cur_line = property(lambda self: self.results.cur_line)

    def plot(self, name): self.results.plot(name)
    def view_data(self): return self.results.view_data()
    def set_cursor_time(self, t): self.results.set_cursor_time(t)
    def toggle_play(self): self.results.toggle_play()
    def play(self): self.results.play()
    def tick(self, dt=None): self.results.tick(dt)

    def _cursor_moved(self):
        self.draw()
        self.canvas.viewport().repaint()   # paint this frame now; a busy loop must not coalesce LED frames away

    # --- actions (thin) ---
    def place(self, kind, x, y):
        with self.hist.change(self.doc):
            name = self.doc.add_part(kind, x, y)
        self.refresh(); return name

    def set_tool(self, k):
        self.tool = k
        self.library.set_armed(k)
        self.canvas.refresh_cursor()
        if k: self.set_status(f"Placing {CATALOG.get(k).name}: click the canvas. Esc cancels.")
        else: self.canvas.tip.hide()
        self.draw()

    def _toggle_tool(self, k):
        if self.tool == k: self.set_tool(None); self.set_status("Ready.")
        else: self.set_tool(k)

    def select(self, name):
        self.sel, self.sel_wire = name, None; self.refresh()

    def select_wire(self, wire):
        self.sel, self.sel_wire = None, tuple(wire); self.refresh()
        self.probe_pin(wire[0])

    def probe_pin(self, pin):
        """Plot the node voltage of the net this pin is on (click-a-wire-to-plot)."""
        r = self.result
        if not (r and r.ok): return
        sig = next((s for s in r.signals if s["kind"] == "node" and pin in s["pins"]), None)
        if sig: self.probe.setCurrentText(sig["name"])
        else: self.set_status(f"{pin} is on the ground net (0 V reference)")

    def delete_selected(self):
        doc, what = self.doc, self.sel if self.sel in self.doc.parts else "wire"
        with self.hist.change(doc):
            if self.sel in doc.parts: doc.remove_part(self.sel)
            elif self.sel_wire in doc.wires: doc.remove_wire(self.sel_wire)
            else: what = None
        self.sel = self.sel_wire = None
        if what: self.set_status(f"Deleted {what}")
        self.refresh()

    def disconnect_all(self, name):
        """Remove every wire touching the part (the part stays)."""
        with self.hist.change(self.doc):
            for p in self.doc.pins(name): self.doc.disconnect(p)
        self.set_status(f"Disconnected {name}"); self.refresh()

    def rotate_selected(self):
        if self.sel not in self.doc.parts: return self.set_status("Select a part to rotate")
        with self.hist.change(self.doc): self.doc.rotate(self.sel, 90)
        self.set_status(f"Rotated {self.sel} to {self.doc.parts[self.sel]['rot']}°"); self.refresh()

    def _history_step(self, fn, what):
        if fn(self.doc):
            if self.sel not in self.doc.parts: self.sel = None
            if self.sel_wire not in self.doc.wires: self.sel_wire = None
            self.pending_pin = None; self.set_status(what); self.refresh()

    def undo(self): self._history_step(self.hist.undo, "Undo")
    def redo(self): self._history_step(self.hist.redo, "Redo")

    # --- documents / files ---
    def _reset(self, doc):
        self.end_live()
        self.doc, self.result, self.sel, self.sel_wire, self.stale = doc, None, None, None, False
        self.pending_pin = None; self.hist.clear(); self.clean = doc.to_dict(); self.open_banner.hide()
        self._show_result(); self.refresh()

    def dirty(self):
        return self.doc.to_dict() != self.clean

    def maybe_save(self):
        """True if it is OK to drop the current document (saved, discarded or unchanged). A hidden window has no
        user to ask (scripts/tests), so it never prompts."""
        if not self.dirty() or not self.isVisible(): return True
        b = QMessageBox.question(self, "Unsaved changes",
                                 f"Save changes to {os.path.basename(self.path or 'untitled')}?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        return self.save() if b == QMessageBox.Save else b == QMessageBox.Discard

    def error(self, title, msg):
        self.set_status(f"FAILED: {msg}", True)
        QMessageBox.critical(self, title, msg)

    def new(self):
        if self.maybe_save(): self._reset(Document()); self.set_status("New simulation")

    def load_led_example(self):
        if self.maybe_save(): self._reset(Document.led_example()); self.set_status("LED example loaded. Press Run.")

    def load_blink_example(self):
        if self.maybe_save():
            self._reset(Document.blink_example())
            self.set_status("Blinking LED example loaded. Press Run (it plays automatically).")

    def open(self, p=None):
        if not self.maybe_save(): return
        p = p or QFileDialog.getOpenFileName(self, "Open", os.path.dirname(self.path or ""), "Simulation (*.json)")[0]
        if not p: return
        try:
            doc = Document.load(p)
        except ModelError as e:
            return self.error("Open failed", str(e))
        self._reset(doc)
        ph = doc.placeholders()
        if ph:
            what = (f"{len(ph)} part{'s have' if len(ph) > 1 else ' has'} no usable definition: "
                    + ", ".join(f"{n} ({doc.parts[n]['kind']} v{doc.parts[n]['version']})" for n in ph))
            self.set_status(f"Opened {p}. {what}", True)
            self.open_msg.setText(what + "."); self.open_banner.show()
        else:
            self.set_status(f"Opened {p}")

    def _show_placeholder(self):
        if ph := self.doc.placeholders(): self.select(ph[0])

    def save(self):
        return self._write(self.path) if self.path else self.save_as()

    def save_as(self):
        p = QFileDialog.getSaveFileName(self, "Save As", self.path or "circuit.json", "Simulation (*.json)")[0]
        if not p: return False
        return self._write(p if os.path.splitext(p)[1] else p + ".json")

    def _write(self, p):
        self.cfg_changed()
        try:
            self.doc.save(p)
        except ModelError as e:
            self.error("Save failed", str(e)); return False
        self.clean = self.doc.to_dict(); self.set_status(f"Saved {p}"); self._title()
        return True

    def closeEvent(self, e):
        if not self.maybe_save(): return e.ignore()
        self.end_live()
        e.accept()

    def _title(self):
        name, dirty = os.path.basename(self.path) if self.path else "untitled", self.dirty()
        self.setWindowTitle(f"Bench - {name}{' *' if dirty else ''}")
        self.file_lbl.setText(f"{name}{f' <span style=\"color:{theme.SEL}\">●</span>' if dirty else ''}")
        self.file_lbl.setToolTip(self.path or "Not saved yet")

    # --- simulation settings / params ---
    @staticmethod
    def _si(text):
        """Run-setting text as shown: '5e-3' -> '5 ms' (unparseable text is shown as typed)."""
        try: return format_si(RUN_PARAMS["tstop"].parse(text, "Run"), "s")
        except ModelError: return text

    def cfg_changed(self, *_):
        d = self.doc
        for ed, k in (self.tstop, "tstop"), (self.tstep, "tstep"):   # keep the stored text if the field just shows it
            if ed.text() != self._si(getattr(d, k)): setattr(d, k, ed.text())
        for ed, k in ((self.tstop, "tstop"), (self.tstep, "tstep")):
            try:
                RUN_PARAMS[k].parse(ed.text(), "Run"); mark(ed, None); self.invalid.pop(k, None)
            except ModelError as e:
                mark(ed, str(e)); self.invalid[k] = str(e)
        self._check_stale(); self._title(); self._sync_controls()

    def _live_for_text(self):
        """The live duration as the field shows it: "Stop time", "Indefinite", or the time ('2 s')."""
        t = self.doc.live_tstop.strip()
        return "Stop time" if t.lower() in LIVE_STOP_TIME else "Indefinite" if t.lower() in LIVE_INDEFINITE else self._si(t)

    def live_for_changed(self, *_):
        """The live duration field changed: store it (as typed; "Stop time" = unset) and validate it."""
        text = self.live_for.currentText().strip()
        if text != self._live_for_text():
            self.doc.live_tstop = "" if text.lower() in LIVE_STOP_TIME else text
        try:
            self.doc.live_duration(); self.live_invalid = None
        except ModelError as e:
            self.live_invalid = str(e)
        mark(self.live_for, self.live_invalid); self._title(); self._sync_controls()

    def set_gear(self):
        """Simulation > Gear integration: a run setting, saved with the circuit (GAPS G30)."""
        self.doc.method = "gear" if self.a_gear.isChecked() else "trap"
        self._check_stale(); self._title(); self._sync_controls()

    def _key(self):
        d = self.doc
        return repr(({n: (p["kind"], p["params"]) for n, p in d.parts.items()}, sorted(map(sorted, d.wires)),
                     d.tstop, d.tstep, d.method))

    def _check_stale(self):
        if self.result is not None and self._key() != self.ran_key:
            if self.session:   # an edit stops a live run: the circuit it simulates no longer exists
                self.session.cancel()
            self.result, self.state, self.stale = None, "idle", True
            self._show_result()
            self.set_status(("Live run stopped: the circuit changed. " if self.session else "")
                            + "results out of date - press Re-run", True)

    def edit_param(self, n, k, v):
        if n in self.doc.parts:
            with self.hist.change(self.doc): self.doc.set_param(n, k, v)
            self._check_stale(); self._problems(); self.draw(); self._title()
            self.a_undo.setEnabled(self.hist.can_undo()); self.a_redo.setEnabled(self.hist.can_redo())

    # --- run controls ---
    def set_state(self, s):
        self.state = s
        self._sync_controls()

    def run(self):
        if self.session: return   # batch runs wait for a live run (the engine is claimed; the controls are disabled)
        fw = self.focusWidget()
        if isinstance(fw, QLineEdit): fw.editingFinished.emit()   # commit a typed-but-not-Entered inspector value
        self.cfg_changed()
        self.stale = False
        self.set_state("running"); self.results.set_view(None, "running"); self.repaint()
        try:
            r = run_document(self.doc)   # never raises by contract; a raise is an app bug and is shown, not hidden
        except Exception as e:
            r = RunResult(False, "failed", f"Internal error: {type(e).__name__}: {e}", details=traceback.format_exc())
        self.result, self.ran_key = r, self._key()
        self.set_status(("SUCCESS: " if r.ok else "BLOCKED: " if r.status == "blocked" else "FAILED: ") + r.message,
                        not r.ok)
        self._show_result()
        self.draw()
        if self.autoplay and r.ok and any(self.doc.parts[n]["params"].get(k) == v for n in self.doc.parts
                                          if self.doc.definition(n) for k, v in self.doc.definition(n).animate_when):
            self.play()

    # --- live runs (task 9.5b; engine contract: docs/plans/ngspice-library/LIVE.md) ---
    def _pace(self):
        """Sim seconds per wall second for the chosen pace, or None (as fast as possible)."""
        r, d = self.pace.currentData(), self.doc.live_duration()
        return (RUN_PARAMS["tstop"].parse(self.doc.tstop, "Run") if math.isinf(d) else d) / FIT_SECONDS if r == "fit" else r

    def live(self):
        """Start a live run, or stop the one in progress (the Live button toggles)."""
        if self.session: return self.stop_live()
        if self.state == "running": return
        fw = self.focusWidget()
        if isinstance(fw, QLineEdit): fw.editingFinished.emit()   # commit a typed-but-not-Entered inspector value
        self.cfg_changed(); self.live_for_changed()
        if self.invalid: return
        if self.live_invalid: return self.set_status(f"Live run: {self.live_invalid}", True)
        self.stale, self.last_session = False, None
        try:
            s = LiveSession(self.doc, realtime=self._pace())
        except Exception as e:   # never raises by contract; a raise is an app bug and is shown, not hidden
            s = None; r = RunResult(False, "failed", f"Internal error: {type(e).__name__}: {e}", details=traceback.format_exc())
        if s is None or s.result.status != "running":   # blocked by the pre-check, or refused by the core
            self.result, self.ran_key = (r if s is None else s.result), self._key()
            self.set_status(("BLOCKED: " if self.result.status == "blocked" else "FAILED: ") + self.result.message, True)
            return self._show_result()
        f = s.result   # the window's own copy grows on the Qt thread from chunk payloads (rule 4)
        self.live_result = RunResult(True, "running", "live run", {k: [] for k in f.series}, f.findings, f.diagnostics,
                                     f.signals, f.details, f.excluded)
        self.live_vals = {(p["part"], p["key"]): self.doc.definition(p["part"]).param(p["key"]).parse(
            self.doc.parts[p["part"]]["params"][p["key"]], p["part"])
            for p in sorted(live_params(self.doc), key=lambda p: not p["stimulus"])}
        self.session, self.paused, self.live_pending, self.live_t, self.live_rtf = s, False, {}, 0.0, 0.0
        self.result, self.ran_key = self.live_result, self._key()
        self.live_span, self.live_cap, self.live_total = s.window, s.cap, s.tstop
        self.results.xlim = (0.0, s.window)
        self.worker = LiveWorker(s)
        self.worker.chunk.connect(self._live_chunk); self.worker.finished.connect(self._live_done)
        gc.disable(); self.gc_timer.start()   # gc on the GUI thread only until _live_done
        self.worker.start()
        self._show_result(); self._live_label(); self.refresh()
        pace = self.pace.currentText().removeprefix("Pace: ")
        self.set_status(f"Live run started (pace {pace}). Select a sensor, pot, switch or source to change it.")

    def pause_live(self):
        if not self.session: return
        self.paused = not self.paused
        self.session.pause() if self.paused else self.session.resume()
        self.set_state("paused" if self.paused else "live"); self._live_label()
        self.set_status("Live run paused: changes wait for Resume." if self.paused else "Live run resumed.")

    def stop_live(self):
        """Stop at the next chunk boundary (at once when paused); the worker then finishes and _live_done tidies up."""
        if self.session:
            self.session.cancel(); self.set_status("Stopping the live run...")

    def end_live(self):
        """Stop a live run and wait for its worker (window close, New/Open): never leave the worker blocked in a
        paused run or holding the engine (LIVE.md rule 3). Waits for at most the chunk in progress."""
        if self.worker:
            self.session.cancel(); self.worker.wait(); self._live_done()

    def _collect(self):
        """GUI-thread gc during a live run: the young generations every 200 ms, everything every 10 s (~25 ms)."""
        self.gc_ticks += 1
        gc.collect(2 if self.gc_ticks % 50 == 0 else 1)

    def live_set(self, part, key, value):
        """A live control moved: coalesce (last value per param wins) and hand it to the session at most every 50 ms."""
        if not self.session: return
        self.live_vals[(part, key)] = value; self.live_pending[(part, key)] = value
        if not self.live_flush.isActive(): self.live_flush.start()

    def _live_flush(self):
        pending, self.live_pending = self.live_pending, {}
        for (part, key), v in pending.items():
            if not self.session: return
            try:
                self.session.set_stimulus(part, v, key)
            except ModelError as e:   # refused value, or the run just ended: say so, the run goes on
                self.set_status(f"Live change refused: {e}", True)

    def _live_chunk(self, c):
        r = self.live_result
        if r is not self.result: return   # dropped meanwhile (an edit made it stale): the worker is stopping
        for n, pts in c["series"].items(): r.series[n].extend(pts)
        trim(r.series, c["t"] - self.live_span, self.live_cap)   # the scrolling window (a no-op within the span)
        r.changes += c["changes"]
        self.live_t, self.live_rtf = c["t"], c["rtf"]
        if not self.live_redraw.isActive(): self.live_redraw.start()

    def _live_redraw(self):
        if self.result is self.live_result and self.result is not None:
            t, w = self.live_t, self.live_span   # the axis scrolls once the run passes its window
            self.results.xlim = (max(0.0, t - w), max(w, t))
            self.results.grow(); self._live_label(); self.draw()

    def _live_label(self):
        total = "∞" if math.isinf(self.live_total) else format_si(self.live_total, 's', 3)   # ∞: indefinite
        what = "paused" if self.paused else f"{self.live_rtf:.3g}× real time" if self.live_rtf else "starting"
        self.results.live_lbl.setText(f"t = {format_si(self.live_t, 's', 4)} / {total}   {what}")

    def _live_done(self):
        """The worker finished (completed, stopped, failed, or dropped by an edit): release the session and show
        the outcome. The streamed result stays for scrubbing and replay; a failure is shown like a batch failure."""
        if self.session is None: return   # end_live already did it
        s, w = self.session, self.worker
        self.session = self.worker = None; self.paused = False
        self.gc_timer.stop(); gc.enable()
        self.live_flush.stop(); self.live_redraw.stop(); self.live_pending = {}
        w.wait()                          # finished is queued from the thread's last moments: let it exit first
        s.close()                         # idempotent; the worker is done, so this never waits
        self.last_session, self.results.xlim = s, None
        r, f = self.live_result, s.result
        if r is self.result and r is not None:
            if w.error:
                r.ok, r.status, r.message, r.details = False, "failed", "Internal error in the live run", w.error
            else:
                r.ok, r.status, r.message, r.details = f.status in ("success", "cancelled"), f.status, f.message, f.details
            r.changes = s.changes
            self.set_status({"success": "LIVE: ", "cancelled": "LIVE STOPPED: "}.get(r.status, "FAILED: ") + r.message,
                            not r.ok)
            self._show_result(); self.draw()
        self.refresh()

    def replay_live(self):
        """Re-run the last live run's recorded changes as one batch run (deterministic) and show it as the result."""
        s = self.last_session
        if s is None or self.session: return
        self.set_state("running"); self.results.set_view(None, "running"); self.repaint()
        r = s.replay()
        if r.status == "blocked":   # too long for one batch run: keep the live result, say why
            self._show_result(); return self.set_status(f"REPLAY BLOCKED: {r.message}", True)
        self.result, self.ran_key = r, self._key()
        self.set_status(("REPLAY: " if r.ok else "FAILED: ") + r.message + (f" ({len(r.changes)} changes)" if r.ok else ""),
                        not r.ok)
        self._show_result(); self.draw()

    def reset(self):
        """Drop the results (circuit, settings and the picked signal are kept)."""
        if self.session: return
        self.result, self.stale = None, False; self._show_result(); self.draw(); self.set_status("Results cleared")

    def rerun(self):
        self.reset(); self.run()

    def _show_result(self):
        """Sync the viewer, state badge and controls with self.result (None = idle, or stale after an edit)."""
        r = self.result
        s = ("idle" if r is None else ("paused" if self.paused else "live") if self.session and r is self.live_result
             else r.state)
        self.results.set_view(r, s, self.stale)
        self.set_state(s)

    def part_current(self, name):
        """Core current series I(part) at the cursor time, or None."""
        r = self.result
        sig = r.signal(f"I({name})") if r and r.ok else None
        return None if not sig or self.cur_t is None else value_at(r.series[sig["vector"]], self.cur_t)

    def led_glow(self, name):
        i = self.part_current(name)
        return 0.0 if i is None else led_level(i)

    def set_status(self, msg, bad=False):
        self.status.setText(msg); self.status.setProperty("bad", bad); theme.repolish(self.status)

    def set_dark(self, dark):
        """View > Dark mode: switch the theme live (no restart) and remember the choice."""
        self.a_dark.setChecked(dark)
        if dark == theme.DARK_MODE: return
        theme.use(dark); theme.apply(QApplication.instance()); theme.save_dark(dark)
        W.retheme(self)

    def on_theme(self):   # after the children's hooks (W.retheme): redraw what reads theme colours while drawing
        self._title(); self.refresh(); self.canvas.viewport().update()

    def load_demo(self):
        d = Document()
        d.add_part("V", 40, 100); d.add_part("R", 160, 60); d.add_part("C", 300, 140); d.add_part("GND", 200, 260)
        for a, b in (("V1.p", "R1.a"), ("R1.b", "C1.a"), ("C1.b", "GND1.g"), ("V1.n", "GND1.g")):
            d.connect(a, b)
        self._reset(d)

    def draw(self):
        self.canvas.draw()

    def _problems(self):
        """Live pre-run diagnostics from Document.validate() (the same checks Run applies)."""
        self.problems_panel.set_diagnostics(self.doc.validate() if self.doc.parts else [])

    def refresh(self):
        doc = self.doc
        self._check_stale()
        self.draw()
        for ed, v in ((self.tstop, doc.tstop), (self.tstep, doc.tstep)):
            if ed.text() != v and ed.text() != self._si(v) and not ed.hasFocus(): ed.setText(self._si(v))
        self.cfg_changed()
        if self.live_for.currentText() != self._live_for_text() and not self.live_for.hasFocus():
            self.live_for.setEditText(self._live_for_text()); self.live_for.lineEdit().setCursorPosition(0)
        self.live_for_changed()
        has = self.sel in doc.parts
        self.a_delete.setEnabled(has or self.sel_wire in doc.wires); self.a_rotate.setEnabled(has)
        self.a_undo.setEnabled(self.hist.can_undo()); self.a_redo.setEnabled(self.hist.can_redo())
        live = {k: v for (p, k), v in self.live_vals.items() if p == self.sel} if self.session else None
        self.inspector.show_selection(doc, self.sel, self.sel_wire, ("Transient", self._si(doc.tstop), self._si(doc.tstep)),
                                      live)
        self._problems()
        n, m = len(doc.parts), len(doc.wires)
        self.counts.setText(f"{n} part{'s' * (n != 1)}   {m} wire{'s' * (m != 1)}   grid 20 px")


def mark(ed, err):
    """Inline validation state of a text field: red border (stylesheet property `invalid`) + tooltip message."""
    ed.setProperty("invalid", bool(err)); theme.repolish(ed)
    ed.setToolTip(err or ed.property("help") or "")
