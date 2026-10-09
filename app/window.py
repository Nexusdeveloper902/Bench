"""Thin Qt layer over app.model / app.sim / app.history. All logic lives there; handlers just call it.

Everything per component (pins, body box, symbol, params, units, value label, current/glow display) comes from the
app.catalog Definition of its kind; symbols are SVG files drawn by app.symbols. No per-kind code here."""
import os
import time
import traceback
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui import QPen, QBrush, QColor, QPainterPath, QKeySequence, QAction, QFont, QRadialGradient, \
    QPolygonF
from PySide6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QGraphicsScene, QGraphicsView,
                               QPushButton, QLabel, QLineEdit, QFormLayout, QComboBox, QFileDialog, QToolBar,
                               QSplitter, QSlider, QScrollArea, QListWidget, QListWidgetItem, QPlainTextEdit, QMessageBox)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
from .model import Document, ModelError, RUN_PARAMS, led_level, value_at
from .catalog import CATALOG, summary, terminal_offset
from .components import format_si
from .symbols import add_symbol, part_transform
from .sim import run_document, RunResult
from .history import History

GRID = 20
SLIDER_MAX = 10000
LOOP_SECONDS = 4.0   # wall-clock time for one pass over the simulated window (blink example: ~1 Hz)
LED_OFF, LED_ON = (90, 90, 90), (255, 70, 30)
HINT = "1 Place: pick a part, click the canvas.   2 Connect: click a pin, then another pin (or drag pin to pin).   " \
       "3 Configure: select a part, edit values (1k, 10u, 4.7n ...).   4 Run (F5).   " \
       "R rotates, drag moves, Delete removes, Ctrl+Z undoes. Click a wire to plot its node."
INK, SEL, HOVER, WIRE = QColor("#222"), QColor("#e07000"), QColor("#2a7fff"), QColor("#1a5fb4")
OK_PIN, OPEN_PIN, BAD = QColor("#2fa82f"), QColor("#d02020"), QColor("#b00020")
STATE_COLORS = {"idle": "#666", "running": "#1a5fb4", "completed": "#1b6e1b", "failed": "#b00020"}
GROUPS = (("node", "Node voltages"), ("across", "Voltage across parts"), ("current", "Currents"))
QUANTITY = {"V": "Voltage", "A": "Current"}


# ---------------------------------------------------------------- geometry (catalog driven)
def _box(kind):
    x0, y0, w, h = CATALOG.get(kind).box
    return x0, y0, w, h, x0 + w / 2, y0 + h / 2


def pin_pos(doc, ref):
    name, _, pin = ref.partition(".")
    d = doc.parts[name]
    ox, oy = terminal_offset(d["kind"], pin, d.get("rot", 0))
    return QPointF(d["x"] + ox, d["y"] + oy)


def part_rect(d):
    x0, y0, w, h, *_ = _box(d["kind"])
    return part_transform(d).mapRect(QRectF(x0, y0, w, h))


def exits_horizontally(doc, ref):
    name, _, pin = ref.partition(".")
    d = doc.parts[name]
    *_, cx, cy = _box(d["kind"])
    px, py = CATALOG.get(d["kind"]).pos[pin]
    return (abs(px - cx) >= abs(py - cy)) == (d.get("rot", 0) % 180 == 0)


def _crossings(rects, pts):
    segs = [QRectF(a, b).normalized().adjusted(-.5, -.5, .5, .5) for a, b in zip(pts, pts[1:])]
    return sum(s.intersects(r) for s in segs for r in rects)


def wire_pts(doc, a, b):
    """Right-angle route with one corner: the corner that crosses fewer part bodies, else leave the first pin along
    its own axis. ponytail: two candidate routes only, no real router; wires may still cross parts in dense layouts."""
    p, q = pin_pos(doc, a), pin_pos(doc, b)
    if p.x() == q.x() or p.y() == q.y():
        return [p, q]
    h, v = [p, QPointF(q.x(), p.y()), q], [p, QPointF(p.x(), q.y()), q]
    rects = [part_rect(d).adjusted(2, 2, -2, -2) for d in doc.parts.values()]
    ch, cv = _crossings(rects, h), _crossings(rects, v)
    return h if ch < cv or (ch == cv and exits_horizontally(doc, a)) else v


def seg_dist(p, a, b):
    ab, ap = b - a, p - a
    L = ab.x() ** 2 + ab.y() ** 2
    t = 0 if L == 0 else max(0, min(1, (ap.x() * ab.x() + ap.y() * ab.y()) / L))
    q = a + ab * t
    return ((p.x() - q.x()) ** 2 + (p.y() - q.y()) ** 2) ** 0.5


def eng(m):
    """(scale, prefix) for showing magnitudes around m: 0.0047 -> (1e-3, 'm')."""
    for s, pre in ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p")):
        if m >= s * 0.9999: return s, pre
    return (1, "") if m == 0 else (1e-12, "p")


# ---------------------------------------------------------------- canvas
class Canvas(QGraphicsView):
    def __init__(self, win):
        self.sc = QGraphicsScene()
        super().__init__(self.sc)
        self.win = win
        self.drag = None        # (part, dx, dy) while moving a part
        self.wiring = False     # pressed on a pin, may drag to another
        self.hover = None       # ("pin", ref) | ("wire", wire)
        self.cursor = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSceneRect(0, 0, 1000, 700)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.sc.setBackgroundBrush(QBrush(Qt.white))

    # ---- hit tests ----
    def hit(self, pt):
        doc = self.win.doc
        for n in doc.parts:
            for r in doc.pins(n):
                if (pin_pos(doc, r) - pt).manhattanLength() < 10: return ("pin", r)
        for n, d in doc.parts.items():
            if part_rect(d).contains(pt): return ("part", n)
        for w in doc.wires:
            pts = wire_pts(doc, *w)
            if any(seg_dist(pt, pts[i], pts[i + 1]) < 5 for i in range(len(pts) - 1)): return ("wire", w)
        return None

    def led_glow(self, name):
        return self.win.led_glow(name)

    def pt(self, e):
        return self.mapToScene(e.position().toPoint())

    # ---- events ----
    def mousePressEvent(self, e):
        self.setFocus()
        w, doc = self.win, self.win.doc
        pt = self.cursor = self.pt(e)
        h = self.hit(pt)
        if h and h[0] == "pin":
            ref = h[1]
            if e.button() == Qt.RightButton:
                with w.hist.change(doc): doc.disconnect(ref)
            elif w.pending_pin and w.pending_pin != ref:
                with w.hist.change(doc): doc.connect(w.pending_pin, ref)
                w.pending_pin = None
            else:
                w.pending_pin = ref; self.wiring = True
            return w.refresh()
        w.pending_pin = None
        if e.button() != Qt.LeftButton:
            return w.refresh()
        if w.tool and not h:
            x, y = round(pt.x() / GRID) * GRID, round(pt.y() / GRID) * GRID
            w.select(w.place(w.tool, x, y)); w.set_tool(None)
            return
        if h and h[0] == "part":
            d = doc.parts[h[1]]
            self.drag = (h[1], pt.x() - d["x"], pt.y() - d["y"]); w.hist.begin(doc)
            return w.select(h[1])
        if h:
            return w.select_wire(h[1])
        w.select(None)

    def mouseMoveEvent(self, e):
        w, doc = self.win, self.win.doc
        pt = self.cursor = self.pt(e)
        if self.drag:
            n, dx, dy = self.drag
            doc.parts[n]["x"] = round((pt.x() - dx) / GRID) * GRID
            doc.parts[n]["y"] = round((pt.y() - dy) / GRID) * GRID
            return w.draw()
        h = self.hit(pt)
        self.hover = h if h and h[0] in ("pin", "wire") else None
        w.draw()

    def mouseReleaseEvent(self, e):
        w, doc = self.win, self.win.doc
        if self.drag:
            self.drag = None
            if w.hist.end(doc): w.set_status("Moved part")
            return w.refresh()
        if self.wiring:
            self.wiring = False
            h = self.hit(self.pt(e))
            if h and h[0] == "pin" and h[1] != w.pending_pin:
                with w.hist.change(doc): doc.connect(w.pending_pin, h[1])
                w.pending_pin = None
            w.refresh()

    def leaveEvent(self, e):
        self.hover = None; self.win.draw()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.win.delete_selected()
        elif e.key() == Qt.Key_Escape:
            self.win.pending_pin = None; self.win.set_tool(None); self.win.refresh()
        elif e.key() == Qt.Key_R and not e.modifiers() & ~Qt.KeypadModifier:   # fallback if the shortcut is inactive
            self.win.rotate_selected()
        elif e.matches(QKeySequence.Undo):   # fallback when the window-level shortcut is inactive
            self.win.undo()
        elif e.matches(QKeySequence.Redo) or (e.key() == Qt.Key_Y and e.modifiers() & Qt.ControlModifier):
            self.win.redo()
        else:
            super().keyPressEvent(e)


# ---------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.doc, self.result, self.sel, self.sel_wire = Document(), None, None, None
        self.ran_key = None     # circuit signature of the last run; a mismatch means the result is stale
        self.tool, self.pending_pin = None, None
        self.state = "idle"     # idle | running | completed | failed (no pause: the core runs synchronously)
        self.want = None        # observable the user picked; kept across reruns
        self.hist = History()
        self.clean = self.doc.to_dict()
        self.canvas = Canvas(self)

        def act(text, fn, *keys, tip=None):
            a = QAction(text, self); a.triggered.connect(lambda _=False: fn())
            if keys: a.setShortcuts([QKeySequence(k) for k in keys])
            if tip: a.setToolTip(tip)
            return a
        self.a_new, self.a_open = act("New", self.new, "Ctrl+N"), act("Open...", self.open, "Ctrl+O")
        self.a_save, self.a_saveas = act("Save", self.save, "Ctrl+S"), act("Save As...", self.save_as, "Ctrl+Shift+S")
        self.a_led = act("Load LED example", self.load_led_example)
        self.a_blink = act("Load blinking LED example", self.load_blink_example)
        self.a_undo = act("Undo", self.undo, "Ctrl+Z")
        self.a_redo = act("Redo", self.redo, "Ctrl+Shift+Z", "Ctrl+Y")
        self.a_rotate = act("Rotate", self.rotate_selected, "R", tip="Rotate the selected part 90° (R)")
        self.a_delete = act("Delete", self.delete_selected, tip="Delete the selected part or wire (Del)")
        self.a_run = act("Run", self.run, "F5", tip="Run the transient simulation (F5)")
        self.a_rerun = act("Re-run", self.rerun, "Ctrl+F5", tip="Clear results and run again (Ctrl+F5)")
        self.a_reset = act("Reset", self.reset, tip="Clear the results (the circuit is kept)")
        fm, em, sm = (self.menuBar().addMenu(t) for t in ("File", "Edit", "Simulation"))
        for a in (self.a_new, self.a_open, self.a_save, self.a_saveas, None, self.a_led, self.a_blink):
            fm.addSeparator() if a is None else fm.addAction(a)
        for a in (self.a_undo, self.a_redo, self.a_rotate, self.a_delete): em.addAction(a)
        for a in (self.a_run, self.a_rerun, self.a_reset): sm.addAction(a)
        # toolbars: file/edit | simulation controls, then the parts palette
        tb = QToolBar("File"); self.addToolBar(tb)
        for a in (self.a_new, self.a_open, self.a_save, self.a_undo, self.a_redo): tb.addAction(a)
        st = QToolBar("Simulation"); self.addToolBar(st)
        self.analysis = QLabel(" Analysis: <b>Transient</b> ")   # the only analysis the core supports
        self.analysis.setToolTip("Analysis type. The simulation core supports transient analysis only.")
        st.addWidget(self.analysis)
        self.tstop, self.tstep = QLineEdit(), QLineEdit()
        for lab, ed, tip in (("Stop time", self.tstop, "Simulated time span, e.g. 5m = 5 ms"),
                             ("Time step", self.tstep, "Maximum solver step / output resolution, e.g. 10u = 10 µs")):
            ed.setFixedWidth(80); ed.setProperty("help", tip); ed.setToolTip(tip)
            ed.textEdited.connect(self.cfg_changed); ed.editingFinished.connect(self.cfg_changed)
            st.addWidget(QLabel(f" {lab} ")); st.addWidget(ed)
        st.addSeparator()
        for a in (self.a_run, self.a_rerun, self.a_reset): st.addAction(a)
        self.state_lbl = QLabel(); self.state_lbl.setMinimumWidth(110); st.addWidget(self.state_lbl)
        self.addToolBarBreak()
        pb = QToolBar("Parts"); self.addToolBar(pb)
        pb.addWidget(QLabel("Place: "))
        self.tool_btns = {}
        for k, c in CATALOG.defs.items():
            b = QPushButton(c.name); b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=k: self.set_tool(None if self.tool == k else k))
            pb.addWidget(b); self.tool_btns[k] = b
        pb.addSeparator(); pb.addAction(self.a_rotate); pb.addAction(self.a_delete)
        # inspector
        self.form = QFormLayout()
        self.param_msg = QLabel(""); self.param_msg.setWordWrap(True)
        self.problems = QListWidget(); self.problems.setMaximumHeight(90)
        self.problems.itemClicked.connect(lambda it: it.data(Qt.UserRole) and self.select(it.data(Qt.UserRole)))
        top = QWidget(); tl = QVBoxLayout(top); tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(QLabel("<b>Inspector</b>")); tl.addLayout(self.form); tl.addWidget(self.param_msg)
        self.problems_lbl = QLabel("<b>Problems</b>")
        tl.addWidget(self.problems_lbl); tl.addWidget(self.problems); tl.addStretch(1)
        # waveform viewer
        self.probe, self.stats = QComboBox(), QLabel("")
        self.probe.setToolTip("Signal to plot. Tip: click a wire on the canvas to plot its node voltage.")
        self.findings = QLabel(""); self.findings.setWordWrap(True)
        self.findings.setStyleSheet("color: #a05000")
        self.probe.currentTextChanged.connect(self._probe_changed)
        self.cur_t, self.cur_line, self.t_rng = None, None, (0.0, 1.0)
        self.autoplay, self.bg, self._last = False, None, 0.0   # autoplay: start looping after a Run of a time-varying source
        self.play_btn, self.slider, self.readout = QPushButton("Play"), QSlider(Qt.Horizontal), QLabel("")
        self.slider.setRange(0, SLIDER_MAX); self.slider.setEnabled(False); self.play_btn.setEnabled(False)
        self.timer = QTimer(self); self.timer.setInterval(40); self.timer.timeout.connect(self._on_timer)
        self.play_btn.clicked.connect(self.toggle_play)
        self.slider.valueChanged.connect(lambda v: self.set_cursor_time(self.t_rng[0] + (self.t_rng[1] - self.t_rng[0]) * v / SLIDER_MAX))
        self.fig = Figure(figsize=(5, 3), layout="constrained"); self.ax = self.fig.add_subplot()
        self.plotw = FigureCanvasQTAgg(self.fig); self.plotw.setMinimumSize(300, 160)
        self.plotw.mpl_connect("draw_event", self._on_draw)
        self.plotw.mpl_connect("button_press_event",
                               lambda e: e.inaxes is self.ax and e.xdata is not None and self.set_cursor_time(e.xdata))
        self.err = QLabel(""); self.err.setWordWrap(True); self.err.setStyleSheet("color: #b00020; font-weight: bold")
        self.details_btn = QPushButton("Show details"); self.details_btn.setCheckable(True)
        self.details = QPlainTextEdit(); self.details.setReadOnly(True); self.details.setVisible(False)
        self.details.setMinimumHeight(120)
        self.details_btn.toggled.connect(lambda on: (self.details.setVisible(on),
                                                     self.details_btn.setText("Hide details" if on else "Show details")))
        bot = QWidget(); rl = QVBoxLayout(bot); rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("<b>Waveform</b>  signal:")); rl.addWidget(self.probe); rl.addWidget(self.plotw, 1)
        pr = QHBoxLayout(); pr.addWidget(self.play_btn); pr.addWidget(self.slider, 1); rl.addLayout(pr)
        rl.addWidget(self.readout); rl.addWidget(self.stats); rl.addWidget(self.err); rl.addWidget(self.findings)
        dl = QHBoxLayout(); dl.addWidget(self.details_btn); dl.addStretch(1); rl.addLayout(dl); rl.addWidget(self.details)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(top); scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setMinimumHeight(140)   # long forms (PULSE: 7 fields) scroll instead of squeezing the fields
        right = QSplitter(Qt.Vertical); right.addWidget(scroll); right.addWidget(bot); right.setStretchFactor(1, 1)
        right.setMinimumWidth(420); right.setSizes([250, 600])
        left = QWidget(); ll = QVBoxLayout(left)
        self.hint = QLabel(HINT); self.hint.setWordWrap(True)
        ll.addWidget(self.hint); ll.addWidget(self.canvas, 1)
        sp = QSplitter(); sp.addWidget(left); sp.addWidget(right); sp.setStretchFactor(0, 1)
        self.setCentralWidget(sp)
        self.status = QLabel("Ready.")
        self.statusBar().addWidget(self.status, 1)
        self.setStyleSheet("QLineEdit { border: 1px solid #666; border-radius: 3px; padding: 2px 4px; }")
        self.resize(1280, 800)
        self._show_result()
        self.refresh()

    # --- file path lives on the document (Document.save/load set it) ---
    @property
    def path(self): return self.doc.path

    @path.setter
    def path(self, p): self.doc.path = p

    # --- actions (thin) ---
    def place(self, kind, x, y):
        with self.hist.change(self.doc):
            name = self.doc.add_part(kind, x, y)
        self.refresh(); return name

    def set_tool(self, k):
        self.tool = k
        for kk, b in self.tool_btns.items(): b.setChecked(kk == k)
        if k: self.set_status(f"Click the canvas to place a {CATALOG.get(k).name}")

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
        doc = self.doc
        with self.hist.change(doc):
            if self.sel in doc.parts: doc.remove_part(self.sel)
            elif self.sel_wire in doc.wires: doc.remove_wire(self.sel_wire)
        self.sel = self.sel_wire = None; self.refresh()

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
        self.doc, self.result, self.sel, self.sel_wire = doc, None, None, None
        self.pending_pin = None; self.hist.clear(); self.clean = doc.to_dict()
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
        self._reset(doc); self.set_status(f"Opened {p}")

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
        e.accept() if self.maybe_save() else e.ignore()

    def _title(self):
        self.setWindowTitle(f"Sim - {os.path.basename(self.path) if self.path else 'untitled'}"
                            f"{' *' if self.dirty() else ''}")

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
                RUN_PARAMS[k].parse(ed.text(), "Run"); mark(ed, None)
            except ModelError as e:
                mark(ed, str(e))
        self._check_stale(); self._title()

    def _key(self):
        d = self.doc
        return repr(({n: (p["kind"], p["params"]) for n, p in d.parts.items()}, sorted(map(sorted, d.wires)),
                     d.tstop, d.tstep))

    def _check_stale(self):
        if self.result is not None and self._key() != self.ran_key:
            self.result = None; self.state = "idle"; self._show_result()
            self.set_status("results out of date - press Run", True)

    def edit_param(self, n, k, v):
        if n in self.doc.parts:
            with self.hist.change(self.doc): self.doc.set_param(n, k, v)
            self._check_stale(); self._problems(); self.draw(); self._title()
            self.a_undo.setEnabled(self.hist.can_undo()); self.a_redo.setEnabled(self.hist.can_redo())

    def _live(self, ed, n, k, text):
        """As-you-type check of one inspector field (no document change until editing finishes)."""
        try:
            v = self.doc.check_param(n, k, text)
        except ModelError as e:
            mark(ed, str(e)); self.param_msg.setText(str(e)); self.param_msg.setStyleSheet("color: #b00020"); return
        mark(ed, None)
        self.param_msg.setText(f"{self.doc.definition(n).param(k).label} = "
                               f"{format_si(v, self.doc.definition(n).param(k).unit, 6)}")
        self.param_msg.setStyleSheet("color: #555")

    # --- run controls ---
    def set_state(self, s):
        self.state = s
        self.state_lbl.setText(f"  ● {s}")
        self.state_lbl.setStyleSheet(f"color: {STATE_COLORS[s]}; font-weight: bold")
        self.a_reset.setEnabled(self.result is not None)

    def run(self):
        fw = self.focusWidget()
        if isinstance(fw, QLineEdit): fw.editingFinished.emit()   # commit a typed-but-not-Entered inspector value
        self.cfg_changed()
        self.set_state("running"); self.state_lbl.repaint()
        try:
            r = run_document(self.doc)   # never raises by contract; a raise is an app bug and is shown, not hidden
        except Exception as e:
            r = RunResult(False, "failed", f"Internal error: {type(e).__name__}: {e}", details=traceback.format_exc())
        self.result, self.ran_key = r, self._key()
        self.set_status(("SUCCESS: " if r.ok else "FAILED: ") + r.message, not r.ok)
        self._show_result()
        self.draw()
        if self.autoplay and r.ok and any(p["params"].get(k) == v for p in self.doc.parts.values()
                                          for k, v in CATALOG.get(p["kind"]).animate_when):
            self.play()

    def reset(self):
        """Drop the results (circuit, settings and the picked signal are kept)."""
        self.result = None; self._show_result(); self.draw(); self.set_status("Results cleared")

    def rerun(self):
        self.reset(); self.run()

    def _show_result(self):
        """Sync the viewer, findings, error box and state with self.result (None = idle)."""
        r = self.result
        self.set_state("idle" if r is None else r.state)
        self.findings.setText("\n".join(f"Finding [{f['severity']}] {f['code']}: {f['message']}"
                                        for f in (r.findings if r else [])))
        self.err.setText(f"Simulation failed: {r.message}" if r and not r.ok else "")
        self.err.setVisible(bool(r and not r.ok))
        self.details.setPlainText(r.details if r else "")
        self.details_btn.setEnabled(r is not None)
        if r is None: self.details_btn.setChecked(False)
        self.probe.blockSignals(True); self.probe.clear()
        first = -1
        for kind, title in GROUPS:
            sigs = [s for s in (r.signals if r and r.ok else []) if s["kind"] == kind]
            if not sigs: continue
            self.probe.addItem(f"--- {title} ---")
            self.probe.model().item(self.probe.count() - 1).setEnabled(False)
            for s in sigs:
                self.probe.addItem(s["name"])
                self.probe.setItemData(self.probe.count() - 1, ", ".join(s["pins"]), Qt.ToolTipRole)
                if first < 0 or s["name"] == self.want: first = self.probe.count() - 1
        self.probe.setCurrentIndex(first)
        self.probe.blockSignals(False)
        self._init_cursor(); self.plot(self.probe.currentText())

    def _probe_changed(self, name):
        if self.result and self.result.signal(name): self.want = name
        self.plot(name)

    # --- waveform viewer (reads RunResult only) ---
    def plot(self, name):
        self.ax.clear()
        r = self.result
        sig = r.signal(name) if r and r.ok else None
        self.cur_line = None
        if sig:
            ser = r.series[sig["vector"]]
            t, v = zip(*ser)
            self.ax.plot(t, v, color=WIRE.name(), lw=1.6)
            (ts, tp), (vs, vp) = eng(max(abs(t[0]), abs(t[-1]))), eng(max(map(abs, v)))
            self.ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _, s=ts: f"{x / s:.6g}"))
            self.ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _, s=vs: f"{x / s:.6g}"))
            self.ax.set_xlabel(f"time ({tp}s)"); self.ax.set_ylabel(f"{QUANTITY[sig['unit']]} ({vp}{sig['unit']})")
            self.ax.set_title(sig["name"]); self.ax.grid(True, alpha=.3)
            self.cur_line = self.ax.axvline(self.cur_t if self.cur_t is not None else t[-1], color="#d02020", lw=1.2,
                                            animated=True)   # drawn by blit, not by a full redraw per frame
            s, u = r.stats(name), sig["unit"]
            self.stats.setText("   ".join(f"{k} {format_si(s[k], u, 6)}" for k in ("final", "min", "max")))
        else:
            self.stats.setText("")
            msg, col = (("Simulation failed - see the message below", "#b00020") if r and not r.ok else
                        ("Press Run (F5) to simulate", "#888") if r is None else ("", "#888"))
            self.ax.text(0.5, 0.5, msg, ha="center", va="center", color=col, transform=self.ax.transAxes)
            self.ax.set_xticks([]); self.ax.set_yticks([])
        self.bg = None; self._readout(); self.plotw.draw_idle()

    def view_data(self):
        """(signal dict, [(t, v)]) currently plotted - straight from RunResult.series - or None."""
        r, name = self.result, self.probe.currentText()
        sig = r.signal(name) if r and r.ok else None
        return (sig, r.series[sig["vector"]]) if sig else None

    # --- time cursor / playback (reads RunResult.series only) ---
    def _init_cursor(self):
        """Called whenever result changes: stop playback, cursor to end of run (or disabled if no result)."""
        self.timer.stop(); self.play_btn.setText("Play")
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
        if self.cur_line is not None:
            self.cur_line.set_xdata([self.cur_t, self.cur_t]); self._blit()
        self._readout(); self.draw()
        self.canvas.viewport().repaint()   # paint this frame now; a busy loop must not coalesce LED frames away

    def _on_draw(self, _=None):
        self.bg = self.plotw.copy_from_bbox(self.fig.bbox)
        if self.cur_line is not None: self.ax.draw_artist(self.cur_line)

    def _blit(self):
        if self.bg is None or self.cur_line is None: return self.plotw.draw_idle()
        self.plotw.restore_region(self.bg); self.ax.draw_artist(self.cur_line); self.plotw.blit(self.fig.bbox)

    def toggle_play(self):
        if self.cur_t is None: return
        if self.timer.isActive(): self.timer.stop(); self.play_btn.setText("Play")
        else: self.play()

    def play(self):
        self._last = time.perf_counter(); self.timer.start(); self.play_btn.setText("Pause")

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
            return self.readout.setText("")
        sig, ser = vd
        self.readout.setText(f"cursor  t = {format_si(self.cur_t, 's', 5)}    "
                             f"{sig['name']} = {format_si(value_at(ser, self.cur_t), sig['unit'], 5)}")

    def part_current(self, name):
        """Core current series I(part) at the cursor time, or None."""
        r = self.result
        sig = r.signal(f"I({name})") if r and r.ok else None
        return None if not sig or self.cur_t is None else value_at(r.series[sig["vector"]], self.cur_t)

    def led_glow(self, name):
        i = self.part_current(name)
        return 0.0 if i is None else led_level(i)

    def set_status(self, msg, bad=False):
        self.status.setText(msg); self.status.setStyleSheet("color: #b00020" if bad else "")

    def load_demo(self):
        d = Document()
        d.add_part("V", 40, 100); d.add_part("R", 160, 60); d.add_part("C", 300, 140); d.add_part("GND", 200, 260)
        for a, b in (("V1.p", "R1.a"), ("R1.b", "C1.a"), ("C1.b", "GND1.g"), ("V1.n", "GND1.g")):
            d.connect(a, b)
        self._reset(d)

    # --- drawing ---
    def draw(self):
        doc, sc, cv = self.doc, self.canvas.sc, self.canvas
        sc.clear()
        wired = {}
        for w in doc.wires:
            for p in w: wired[p] = wired.get(p, 0) + 1
        for w in doc.wires:
            pts = wire_pts(doc, *w)
            col, wd = (SEL, 4) if w == self.sel_wire else (HOVER, 4) if cv.hover == ("wire", w) else (WIRE, 2)
            path = QPainterPath(pts[0])
            for q in pts[1:]: path.lineTo(q)
            sc.addPath(path, QPen(col, wd, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        nets = doc.nets()
        flag = {p for f in (self.result.findings if self.result else []) for p in nets
                if nets[p] in f.get("nodes", [])}   # pins on nets named by core findings
        for p in flag:
            q = pin_pos(doc, p)
            sc.addEllipse(q.x() - 10, q.y() - 10, 20, 20, QPen(QColor("#e0a000"), 3))
        font = QFont(); font.setPointSize(9)
        for n, d in doc.parts.items():
            c, sel = CATALOG.get(d["kind"]), n == self.sel
            col, r = (SEL if sel else INK), part_rect(d)
            if sel:
                sc.addRect(r.adjusted(-6, -6, 6, 6), QPen(SEL, 1.5, Qt.DashLine), QBrush(QColor(255, 230, 190, 140)))
            cur = self.part_current(n) if c.show_current else None
            if c.indicator == "glow" and cur is not None:
                self._glow(d, n)
            add_symbol(sc, d, c, col)
            lines = [f"{n} {summary(d['kind'], d['params'])}".strip()]
            if cur is not None: lines.append(format_si(cur, "A", 3))
            for i, text in enumerate(lines):
                t = sc.addSimpleText(text, font); t.setBrush(QBrush(col if i == 0 else BAD))
                bw, bh = t.boundingRect().width(), t.boundingRect().height()
                if r.height() > r.width():   # vertical part: labels to the right
                    t.setPos(r.right() + 8, r.center().y() - bh * len(lines) / 2 + i * bh)
                else:
                    t.setPos(r.center().x() - bw / 2, r.bottom() + 8 + i * bh)
            for ref in doc.pins(n):
                p = pin_pos(doc, ref)
                hov, pend = cv.hover == ("pin", ref), ref == self.pending_pin
                if pend: rad, pen, br = 6, QPen(HOVER, 2), QBrush(HOVER)
                elif ref in wired: rad, pen, br = 4.5, QPen(QColor("#1b6e1b"), 1), QBrush(OK_PIN)
                else: rad, pen, br = 5, QPen(OPEN_PIN, 2), QBrush(Qt.white)   # unconnected: hollow red
                if hov: rad += 2
                sc.addEllipse(p.x() - rad, p.y() - rad, 2 * rad, 2 * rad, pen, br)
                if wired.get(ref, 0) >= 2:   # junction: 3+ conductors meet at this pin
                    sc.addEllipse(p.x() - 4, p.y() - 4, 8, 8, QPen(Qt.NoPen), QBrush(WIRE))
        if self.pending_pin in {p for n in doc.parts for p in doc.pins(n)} and cv.cursor is not None:
            a = pin_pos(doc, self.pending_pin)
            sc.addLine(a.x(), a.y(), cv.cursor.x(), cv.cursor.y(), QPen(HOVER, 1, Qt.DashLine))

    def _glow(self, d, n):
        sc, g = self.canvas.sc, self.led_glow(n)
        x0, y0, w, h, cx, cy = _box(d["kind"])
        t = part_transform(d); ctr = t.map(QPointF(cx, cy))
        if g > 0:
            gr = QRadialGradient(ctr, 55); a = int(255 * min(1, 0.4 + g))
            gr.setColorAt(0, QColor(255, 140, 60, a)); gr.setColorAt(0.4, QColor(255, 60, 20, a // 2))
            gr.setColorAt(1, QColor(255, 60, 20, 0))
            sc.addEllipse(ctr.x() - 55, ctr.y() - 55, 110, 110, QPen(Qt.NoPen), QBrush(gr))
        rgb = [int(a + (b - a) * g) for a, b in zip(LED_OFF, LED_ON)]
        sc.addPolygon(t.map(QPolygonF([QPointF(cx - 10, cy - 10), QPointF(cx - 10, cy + 10), QPointF(cx + 10, cy)])),
                      QPen(Qt.NoPen), QBrush(QColor(*rgb)))

    def _problems(self):
        """Live pre-run diagnostics from Document.validate() (the same checks Run applies)."""
        self.problems.clear()
        diags = self.doc.validate() if self.doc.parts else []
        for dg in diags:
            it = QListWidgetItem(f"{dg['severity'].capitalize()}: {dg['message']}  [{dg['code']}]")
            it.setForeground(QBrush(BAD if dg["severity"] == "error" else QColor("#a05000")))
            it.setData(Qt.UserRole, dg["part"]); it.setToolTip(dg["message"])
            self.problems.addItem(it)
        ne = sum(dg["severity"] == "error" for dg in diags)
        self.problems_lbl.setText(f"<b>Problems</b> ({ne} error{'s' * (ne != 1)}, {len(diags) - ne} warnings)"
                                  if diags else "<b>Problems</b> (none)")

    def refresh(self):
        doc = self.doc
        self._check_stale()
        self.draw()
        for ed, v in ((self.tstop, doc.tstop), (self.tstep, doc.tstep)):
            if ed.text() != v and ed.text() != self._si(v) and not ed.hasFocus(): ed.setText(self._si(v))
        self.cfg_changed()
        has = self.sel in doc.parts
        self.a_delete.setEnabled(has or self.sel_wire in doc.wires); self.a_rotate.setEnabled(has)
        self.a_undo.setEnabled(self.hist.can_undo()); self.a_redo.setEnabled(self.hist.can_redo())
        self.param_msg.setText("")
        while self.form.rowCount(): self.form.removeRow(0)
        if has:
            self._params()
        elif self.sel_wire in doc.wires:
            self.form.addRow(QLabel(f"<b>Wire</b> {self.sel_wire[0]} - {self.sel_wire[1]}"))
        else:
            self.form.addRow(QLabel("Select a part to edit its values."))
        self._problems()

    def _params(self):
        """Inspector rows from catalog Param metadata: label, unit, choice -> combo box, number -> SI text field."""
        n = self.sel
        d = self.doc.parts[n]
        c, p = CATALOG.get(d["kind"]), d["params"]
        self.form.addRow(QLabel(f"<b>{n}</b> ({c.name})"))
        keys = c.fields(p)
        if not keys:
            self.form.addRow(QLabel("no parameters")); return
        errs = []
        for k in keys:
            m = c.param(k)
            if m.kind == "choice":
                w = QComboBox(); w.setMinimumSize(110, 26); w.addItems(m.choices); w.setCurrentText(p[k])
                w.currentTextChanged.connect(lambda v, n=n, k=k: (self.edit_param(n, k, v), QTimer.singleShot(0, self.refresh)))
            else:
                w = QLineEdit(p[k]); w.setPlaceholderText("e.g. 1k, 10u, 4.7n"); w.setMinimumSize(110, 26)
                w.editingFinished.connect(lambda n=n, k=k, w=w: self.edit_param(n, k, w.text()))
                w.textEdited.connect(lambda text, n=n, k=k, w=w: self._live(w, n, k, text))
                try:
                    self.doc.check_param(n, k)
                except ModelError as e:
                    mark(w, str(e)); errs.append(str(e))
            self.form.addRow(f"{m.label} ({m.unit})" if m.unit else m.label, w)
        if errs:
            self.param_msg.setText("\n".join(errs)); self.param_msg.setStyleSheet("color: #b00020")


def mark(ed, err):
    """Inline validation state of a text field: red border + tooltip message, or plain."""
    ed.setStyleSheet("border: 2px solid #d02020; background: #fff0f0; color: #222" if err else "")
    ed.setToolTip(err or ed.property("help") or "")
