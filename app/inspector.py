"""Inspector panel: what is selected on the canvas, and its editable parameters. The form is generated from the
catalog Definition's param schema (number -> text field with unit and SI hint, choice -> dropdown, `when` fields appear
and vanish with their choice); pins, limitations, docs and readiness come from the Definition too. No component is
named here. Design: docs/milestone/03-design.md (inspector frames)."""
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from . import theme, widgets as W
from .components import ModelError, format_si

SI_HINT = "1k  4.7u  330m"


class FieldEdit(QLineEdit):
    """Line edit that mirrors its focus onto the surrounding field box (stylesheet property `focus`)."""
    def __init__(self, text, box):
        super().__init__(text); self.box = box

    def _ring(self, on):
        self.box.setProperty("focus", on); theme.repolish(self.box)

    def focusInEvent(self, e):
        super().focusInEvent(e); self._ring(True)

    def focusOutEvent(self, e):
        super().focusOutEvent(e); self._ring(False)


class Inspector(QWidget):
    param_edited = Signal(str, str, str)   # part, param key, text as typed (committed on Enter / focus loss)
    plot_requested = Signal(str)           # pin ref of the selected wire
    delete_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("panel"); self.setFixedWidth(theme.INSPECTOR_W)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.NoFrame)
        QVBoxLayout(self).addWidget(self.scroll); self.layout().setContentsMargins(0, 0, 0, 0)
        self.ctx = None
        self.view = "none"           # none | part | placeholder | wire
        self.edits, self.boxes, self.msgs, self.labels, self.banner = {}, {}, {}, {}, None
        self.buttons = {}

    # ---- entry points
    def show_selection(self, doc, sel, wire, run):
        """Render the canvas selection: a part name, a wire (pin, pin) or nothing. `run` = (analysis, stop, step) text."""
        self.ctx = (doc, sel, wire, run)
        self.edits, self.boxes, self.msgs, self.labels, self.buttons, self.banner = {}, {}, {}, {}, {}, None
        w = QWidget(); w.setObjectName("panel")
        lay = QVBoxLayout(w); lay.setContentsMargins(theme.LG, theme.LG, theme.LG, theme.LG); lay.setSpacing(theme.SM)
        if sel in doc.parts:
            self.view = "placeholder" if "placeholder" in doc.parts[sel] else "part"
            self._part(lay, doc, sel)
        elif wire in doc.wires:
            self.view = "wire"; self._wire(lay, doc, wire)
        else:
            self.view = "none"; self._nothing(lay, doc, run)
        lay.addStretch(1)
        self.scroll.setWidget(w)

    def refresh(self):
        """Re-render with the last context (e.g. after a choice changed which fields apply)."""
        if self.ctx: self.show_selection(*self.ctx)

    def field(self, label):
        """The edit widget of the parameter whose label starts with `label` (tests, scripting)."""
        return next(self.edits[k] for k, t in self.labels.items() if t.startswith(label))

    def message(self, key):
        return self.msgs[key].text() if key in self.msgs else ""

    # ---- nothing selected
    def _nothing(self, lay, doc, run):
        lay.addWidget(W.label("Inspector", "title"))
        ic = W.label("i", "emptyicon"); ic.setFixedSize(56, 56); ic.setAlignment(Qt.AlignCenter)
        lay.addSpacing(8); lay.addWidget(ic, 0, Qt.AlignHCenter)
        t = W.label("Nothing selected", "bold"); t.setAlignment(Qt.AlignCenter); lay.addWidget(t)
        h = W.label("Click a part or a wire to see and edit it here.", "muted", wrap=True)
        h.setAlignment(Qt.AlignCenter); lay.addWidget(h); lay.addSpacing(8)
        unm = set(doc.unmodeled())
        sim = [n for n in doc.parts if n not in unm]
        lay.addWidget(W.section("Circuit"))
        for k, v in (("Parts", len(doc.parts)), ("Wires", len(doc.wires)),
                     ("Simulated", f"{len(sim)} ({', '.join(sim)})" if sim else "0"),
                     ("Not simulated", f"{len(unm)} ({', '.join(unm)})" if unm else "0")):
            lay.addWidget(self._kv(k, str(v)))
        lay.addSpacing(8)
        lay.addWidget(W.section("Simulation"))
        for k, v in zip(("Analysis", "Stop time", "Time step"), run): lay.addWidget(self._kv(k, v))
        lay.addWidget(W.label("Edit stop time and time step in the top bar.", "hint", wrap=True))

    @staticmethod
    def _kv(k, v):
        r = QWidget(); l = QHBoxLayout(r); l.setContentsMargins(0, 2, 0, 2)
        a = W.label(k, "muted"); a.setFixedWidth(96); b = W.label(v, wrap=True)
        l.addWidget(a); l.addWidget(b, 1)
        return r

    # ---- wire selected
    @staticmethod
    def _pin_label(doc, ref):
        name, _, pin = ref.partition(".")
        c = doc.definition(name) if name in doc.parts else None
        p = next((p for p in c.pins if p.id == pin), None) if c else None
        return f"{name}  ·  pin {p.name if p else pin}"

    def _wire(self, lay, doc, wire):
        a, b = wire
        head = QHBoxLayout(); head.setSpacing(12)
        head.addWidget(W.tile(None, glyph="└┐"), 0, Qt.AlignTop)
        col = QVBoxLayout(); col.setSpacing(2)
        col.addWidget(W.label("Wire", "title")); col.addWidget(W.label(f"{a} to {b}", "code"))
        head.addLayout(col, 1); lay.addLayout(head); lay.addSpacing(4)
        net = doc.nets()[a]
        joined = sum(v == net for v in doc.nets().values())
        lay.addWidget(W.section("Connection"))
        for k, v in (("From", self._pin_label(doc, a)), ("To", self._pin_label(doc, b)),
                     ("Node", f"{net} (ground)  ·  {joined} pins joined" if net == "0" else
                      f"{net}  ·  {joined} pins joined")):
            lay.addWidget(self._kv(k, v))
        row = QHBoxLayout()
        plot, dele = QPushButton("Plot node"), QPushButton("Delete")
        plot.clicked.connect(lambda: self.plot_requested.emit(a)); dele.clicked.connect(self.delete_requested.emit)
        row.addWidget(plot); row.addWidget(dele); row.addStretch(1); lay.addLayout(row)
        self.buttons = {"plot": plot, "delete": dele}
        lay.addWidget(W.label(f"Plot node selects V({net}) in the results signal picker. Delete removes only this wire (Del).",
                              "hint", wrap=True))

    # ---- part selected
    def _header(self, lay, defn, title, meta, badge, glyph="?"):
        head = QHBoxLayout(); head.setSpacing(12)
        head.addWidget(W.tile(defn, glyph=glyph), 0, Qt.AlignTop)
        col = QVBoxLayout(); col.setSpacing(2)
        col.addWidget(W.label(title, "title", wrap=True)); col.addWidget(W.label(meta, "code"))
        col.addWidget(W.Badge(badge), 0, Qt.AlignLeft)
        head.addLayout(col, 1); lay.addLayout(head); lay.addSpacing(4)

    def _part(self, lay, doc, n):
        d, c = doc.parts[n], doc.definition(n)
        ph = d.get("placeholder")
        if c is None:
            self._header(lay, None, "Unknown component", f"{n}  ·  {d['kind']}  ·  v{d['version']}", "placeholder")
            f, t, _ = W.banner("warn", f"{ph['detail'][0].upper()}{ph['detail'][1:]}. Kept unchanged on save.")
            lay.addWidget(f); self.banner = f
            lay.addWidget(W.section("Parameters (read-only)"))
            for k, v in d["params"].items(): lay.addWidget(self._field_ro(k, str(v)))
            if not d["params"]: lay.addWidget(W.label("No parameters were saved.", "hint"))
            lay.addWidget(W.section("Pins (from connections)"))
            self._pins(lay, doc, n, None)
            dele = QPushButton("Delete"); dele.clicked.connect(self.delete_requested.emit)
            self.buttons = {"delete": dele}; lay.addWidget(dele, 0, Qt.AlignLeft)
            lay.addWidget(W.label("Move, rotate, wire and delete work. Parameters cannot be edited. No definition is "
                                  "substituted.", "hint", wrap=True))
            return
        self._header(lay, c, c.name, f"{n}  ·  {c.id}  ·  v{d['version']}", c.readiness)
        lay.addWidget(W.section("Parameters"))
        keys = c.fields(d["params"])
        if not keys:
            lay.addWidget(W.label("No configurable parameters.", "hint"))
        errs = []
        for k in keys:
            row, err = self._field(doc, n, c.param(k), d["params"][k])
            lay.addWidget(row)
            if err: errs.append(err)
        self.banner, _, _ = W.banner("err", f"Run is blocked until every value is valid. The Problems list shows "
                                            f"BAD-PARAM for {n}.")
        lay.addWidget(self.banner); self.banner.setVisible(bool(errs))
        if keys:
            lay.addWidget(W.label("Parameters are checked as you type. Changing a value marks results out of date.",
                                  "hint", wrap=True))
        lay.addWidget(W.section(f"Pins ({len(c.pins)})" if len(c.pins) > 5 else "Pins"))
        self._pins(lay, doc, n, c)
        if c.limitations:
            lay.addWidget(W.section("Limitations"))
            for b in W.bullets(c.limitations): lay.addWidget(b)
        docs = W.docs_block(c)
        if docs: lay.addWidget(docs)
        lay.addWidget(W.rule())
        lay.addWidget(W.label(f"type {c.id}   version {d['version']}", "code"))

    def _pins(self, lay, doc, n, c):
        wired = {e for w in doc.wires for e in w}
        names = {p.id: p.name for p in c.pins} if c else {}
        rows = []
        for ref in doc.pins(n):
            pid = ref.partition(".")[2]
            rows.append([names.get(pid, pid), pid, W.PinState(ref in wired)])
        if rows:
            lay.addWidget(W.Table([("Pin", 3, False), ("ID", 2, True), ("State", 2, False)], rows, 5, "pins"))
        else:
            lay.addWidget(W.label("No pins are wired.", "hint"))

    def _label_row(self, text, hint, tip=""):
        r = QWidget(); l = QHBoxLayout(r); l.setContentsMargins(0, 0, 0, 0)
        a = W.label(text); a.setObjectName("fieldlabel"); a.setStyleSheet(f"color: {theme.TEXT_2}; font-weight: 500;")
        l.addWidget(a); l.addStretch(1)
        if hint: l.addWidget(W.label(hint, "hint"))
        if tip: r.setToolTip(tip)
        return r

    def _field_ro(self, key, value):
        col = QWidget(); l = QVBoxLayout(col); l.setContentsMargins(0, 0, 0, 0); l.setSpacing(4)
        l.addWidget(self._label_row(key, ""))
        box = QFrame(); box.setObjectName("fieldbox"); box.setProperty("readonly", True)
        bl = QHBoxLayout(box); bl.setContentsMargins(0, 0, 0, 0)
        e = QLineEdit(value); e.setReadOnly(True); e.setEnabled(False); bl.addWidget(e)
        l.addWidget(box)
        self.edits[key] = e
        return col

    def _field(self, doc, n, m, value):
        """One param row -> (widget, error text or None). Registers self.edits/boxes/msgs under m.key."""
        col = QWidget(); l = QVBoxLayout(col); l.setContentsMargins(0, 0, 0, 0); l.setSpacing(4)
        self.labels[m.key] = m.label
        l.addWidget(self._label_row(m.label, SI_HINT if m.kind != "choice" else "", m.help))
        msg = W.label("", "hint", wrap=True); msg.setVisible(False); self.msgs[m.key] = msg
        err = None
        if m.kind == "choice":
            w = QComboBox(); w.setFixedHeight(theme.CONTROL_H); w.addItems(m.choices); w.setCurrentText(value)
            w.currentTextChanged.connect(lambda v, n=n, k=m.key: (self.param_edited.emit(n, k, v),
                                                                 QTimer.singleShot(0, self.refresh)))
            self.edits[m.key] = self.boxes[m.key] = w
            l.addWidget(w)
        else:
            box = QFrame(); box.setObjectName("fieldbox"); box.setFixedHeight(theme.CONTROL_H)
            bl = QHBoxLayout(box); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(0)
            e = FieldEdit(value, box); e.setPlaceholderText("e.g. 1k, 10u, 4.7n"); bl.addWidget(e, 1)
            if m.unit:
                u = W.label(m.unit, "unit"); u.setAlignment(Qt.AlignCenter); u.setMinimumWidth(43); bl.addWidget(u)
            e.editingFinished.connect(lambda n=n, k=m.key, e=e: self.param_edited.emit(n, k, e.text()))
            e.textEdited.connect(lambda text, n=n, m=m, e=e: self._live(n, m, e, text))
            self.edits[m.key], self.boxes[m.key] = e, box
            l.addWidget(box)
            try:
                doc.check_param(n, m.key)
            except ModelError as ex:
                err = str(ex); self._mark(m.key, err)
        l.addWidget(msg)
        return col, err

    def _mark(self, key, err):
        """Invalid state: red box + message under the field, or plain. Number fields only."""
        box, msg = self.boxes[key], self.msgs[key]
        box.setProperty("invalid", bool(err)); theme.repolish(box)
        msg.setObjectName("err" if err else "hint"); theme.repolish(msg)
        msg.setText(err or ""); msg.setVisible(bool(err))
        self.edits[key].setToolTip(err or "")
        if self.view == "part" and self.banner is not None:   # the 'run is blocked' banner follows the fields
            self.banner.setVisible(any(b.property("invalid") for b in self.boxes.values()))

    def _live(self, n, m, ed, text):
        """As-you-type check of one field (the document changes only when editing finishes)."""
        doc = self.ctx[0]
        try:
            v = doc.check_param(n, m.key, text)
        except ModelError as e:
            return self._mark(m.key, str(e))
        self._mark(m.key, None)
        msg = self.msgs[m.key]
        msg.setText(f"= {format_si(v, m.unit, 6)}"); msg.setVisible(True)
