"""Component library panel: search, category navigation, 'Simulated only' toggle, cards, details view, catalog-problems
banner. Everything comes from the catalog (`CATALOG.search` / `.categories` / `.problems`); no component is named here.
Design: docs/milestone/03-design.md (library frames, rules R1/R3/R4)."""
from html import escape
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QGuiApplication
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from . import theme, widgets as W
from .catalog import CATALOG

MIME = "application/x-sim-component"


class Elided(QLabel):
    """One-line label that elides its text with an ellipsis to whatever width the layout gives it (full text in the
    tooltip when cut). `natural`: asks for its full text width (chips) but can shrink; else it takes what it is given."""
    def __init__(self, text, name=None, natural=False):
        super().__init__(); self.full, self.natural = text, natural
        self.setTextFormat(Qt.PlainText)
        if name: self.setObjectName(name)
        self.setSizePolicy(QSizePolicy.Preferred if natural else QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(28 if natural else 10)
        self._elide()

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.full) + 22, 20) if self.natural else super().sizeHint()

    def _elide(self):
        t = self.fontMetrics().elidedText(self.full, Qt.ElideRight, max(self.contentsRect().width(), 10))
        self.setText(t); self.setToolTip(self.full if t != self.full else "")

    def resizeEvent(self, e):
        super().resizeEvent(e); self._elide()

    def showEvent(self, e):   # the stylesheet font is applied at first polish: measure again with it
        super().showEvent(e); self._elide()


class Card(QFrame):
    """One catalog entry: icon tile, name, one-line description, readiness badge, up to 2 tag chips.
    Left click arms placement, right click / Shift+Enter opens the details view, drag carries MIME = id."""
    clicked, details = Signal(str), Signal(str)

    def __init__(self, defn):
        super().__init__()
        self.defn, self._press = defn, None
        self.setObjectName("card"); self.setFixedHeight(theme.CARD_H); self.setFocusPolicy(Qt.TabFocus)   # keyboard focus ring only; clicking does not leave a focus border
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        self.setToolTip(f"Click to place {defn.name}. Right-click or Shift+Enter for details.")
        self.setProperty("selected", False); self.setProperty("armed", False)
        row = QHBoxLayout(self); row.setContentsMargins(9, 9, 9, 9); row.setSpacing(10)
        self.tile = W.tile(defn); row.addWidget(self.tile, 0, Qt.AlignTop)
        col = QVBoxLayout(); col.setSpacing(2); col.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout(); top.setContentsMargins(0, 0, 0, 0)
        self.name = W.label(defn.name, "cardname"); top.addWidget(self.name, 1)
        self.pill = W.label("Placing", "pill"); self.pill.setVisible(False); top.addWidget(self.pill)
        col.addLayout(top)
        self.desc = Elided(defn.description.split("\n")[0], "muted")
        col.addWidget(self.desc)
        badges = QHBoxLayout(); badges.setContentsMargins(0, 2, 0, 0); badges.setSpacing(8)
        self.badge = W.Badge(defn.readiness); badges.addWidget(self.badge)
        self.chips = [Elided(t, "chip", natural=True) for t in defn.tags[:2]]   # elided when the card is narrow
        for c in self.chips: badges.addWidget(c)
        badges.addStretch(1); col.addLayout(badges)
        row.addLayout(col, 1)

    def set_state(self, selected, armed):
        for k, v in (("selected", selected), ("armed", armed)): self.setProperty(k, v)
        self.pill.setVisible(armed); theme.repolish(self)

    def click(self):
        self.clicked.emit(self.defn.id)

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton: return self.details.emit(self.defn.id)
        self._press = e.position().toPoint() if e.button() == Qt.LeftButton else None

    def mouseMoveEvent(self, e):
        if self._press is None or not e.buttons() & Qt.LeftButton: return
        if (e.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance(): return
        self._press = None
        m = QMimeData(); m.setData(MIME, self.defn.id.encode())
        d = QDrag(self); d.setMimeData(m); d.setPixmap(self.grab()); d.setHotSpot(QPoint(20, 20)); d.exec(Qt.CopyAction)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._press is not None and self.rect().contains(e.position().toPoint()):
            self.click()
        self._press = None

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            return self.details.emit(self.defn.id) if e.modifiers() & Qt.ShiftModifier else self.click()
        if e.key() == Qt.Key_Space: return self.click()
        super().keyPressEvent(e)


class NavItem(QPushButton):
    """Category row: name left, count right. Zero-count rows stay clickable (they show the empty-category state)."""
    def __init__(self, name, key):
        super().__init__()
        self.key = key
        self.setObjectName("nav"); self.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout(self); row.setContentsMargins(9, 0, 12, 0)
        self.name, self.count = QLabel(name), QLabel("0")
        self.name.setTextFormat(Qt.PlainText)
        self.count.setObjectName("hint")
        for l in (self.name, self.count): l.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.name, 1); row.addWidget(self.count)
        self.setProperty("selected", False)

    def set_state(self, selected, n):
        self.setProperty("selected", selected); self.count.setText(str(n))
        muted = "" if n else f"color: {theme.TEXT_DISABLED};"
        self.name.setStyleSheet(muted + (f"color: {theme.PRIMARY}; font-weight: 600;" if selected else ""))
        self.count.setStyleSheet(muted + (f"color: {theme.PRIMARY};" if selected else ""))
        theme.repolish(self)

    def on_theme(self):   # the inline colours above were the old theme's
        self.set_state(self.property("selected"), int(self.count.text()))


class SearchField(QLineEdit):
    """Search box: Esc clears the text."""
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.text(): return self.clear()
        super().keyPressEvent(e)


class LibraryPanel(QWidget):
    place_requested = Signal(str)    # a card was clicked (arm) or the details 'Place' button pressed

    def __init__(self):
        super().__init__()
        self.setObjectName("panel"); self.setFixedWidth(theme.LIBRARY_W)
        self.category, self.armed, self.selected = None, None, None
        self.cards, self.navs = {}, {}
        self.stack = QStackedWidget(); QVBoxLayout(self).addWidget(self.stack)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.list_page, self.detail_page = QWidget(), QScrollArea()
        self.stack.addWidget(self.list_page); self.stack.addWidget(self.detail_page)
        self._build_list()
        self.reload()

    # ---- list view
    def _build_list(self):
        lay = QVBoxLayout(self.list_page); lay.setContentsMargins(12, 12, 12, 0); lay.setSpacing(8)
        lay.addWidget(W.label("Components", "title"))
        self.banner = self._build_banner(); lay.addWidget(self.banner)
        self.search = SearchField(); self.search.setObjectName("search"); self.search.setPlaceholderText("Search components")
        self.search.setClearButtonEnabled(True); self.search.setFixedHeight(theme.CONTROL_H)
        a = self.search.addAction(self._search_icon(), QLineEdit.LeadingPosition)
        W.themed(a, lambda: a.setIcon(self._search_icon()))
        self.search.textChanged.connect(self.refilter)
        lay.addWidget(self.search)
        self.nav_box = QVBoxLayout(); self.nav_box.setSpacing(0); lay.addLayout(self.nav_box)
        lay.addWidget(W.rule())
        tr = QHBoxLayout(); tr.setContentsMargins(0, 2, 0, 2)
        tr.addWidget(QLabel("Simulated only"), 1)
        self.toggle = W.Toggle(); self.toggle.toggled.connect(self.refilter); tr.addWidget(self.toggle)
        lay.addLayout(tr)
        lay.addWidget(W.rule())
        self.empty = self._build_empty(); lay.addWidget(self.empty)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.NoFrame)
        self.card_host = QWidget(); self.card_lay = QVBoxLayout(self.card_host)
        self.card_lay.setContentsMargins(0, 4, 0, 12); self.card_lay.setSpacing(8); self.card_lay.addStretch(1)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.scroll.setWidget(self.card_host)
        lay.addWidget(self.scroll, 1)

    @staticmethod
    def _search_icon():
        from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
        pm = QPixmap(32, 32); pm.fill(Qt.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing); p.setPen(QPen(QColor(theme.TEXT_MUTED), 3))
        p.drawEllipse(5, 5, 14, 14); p.drawLine(17, 17, 26, 26); p.end()
        pm.setDevicePixelRatio(2)
        return QIcon(pm)

    def _build_banner(self):
        f, self.banner_title, lay = W.banner("err", "", wrap=False)
        f.layout().removeWidget(self.banner_title); self.banner_title.deleteLater()
        self.banner_toggle = QPushButton(); self.banner_toggle.setCursor(Qt.PointingHandCursor)
        self.banner_toggle.clicked.connect(lambda: self._expand_banner(not self.banner_body.isVisible()))
        lay.addWidget(self.banner_toggle)
        self.banner_body = QWidget(); self.banner_lay = QVBoxLayout(self.banner_body)
        self.banner_lay.setContentsMargins(0, 0, 0, 0); self.banner_lay.setSpacing(6)
        lay.addWidget(self.banner_body); self.banner_body.setVisible(False)
        self.banner_title = self.banner_toggle
        return f

    def _build_empty(self):
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(8, 24, 8, 8); lay.setSpacing(8)
        icon = W.label("⌕", "emptyicon"); icon.setFixedSize(56, 56); icon.setAlignment(Qt.AlignCenter)
        lay.addWidget(icon, 0, Qt.AlignHCenter)
        self.empty_title = W.label("", "bold", wrap=True); self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_text = W.label("", "muted", wrap=True); self.empty_text.setAlignment(Qt.AlignCenter)
        self.empty_btn = QPushButton(); self.empty_btn.clicked.connect(self._empty_action)
        for x in (self.empty_title, self.empty_text): lay.addWidget(x)
        lay.addWidget(self.empty_btn, 0, Qt.AlignHCenter); lay.addStretch(1)
        w.setVisible(False)
        return w

    def _empty_action(self):
        if self.search.text(): self.search.clear()
        else: self.toggle.setChecked(False)

    # ---- catalog problems banner
    def _rel(self, f):
        """File path relative to the catalog root that holds it (absolute if none does)."""
        return next((str(Path(f).relative_to(r)) for r in CATALOG.roots if Path(f).is_relative_to(r)), f)

    def _expand_banner(self, on):
        self.banner_body.setVisible(on)
        self.banner_toggle.setText(f"{self._banner_head}   {'⌃' if on else '⌄'}")

    def _fill_banner(self):
        probs = CATALOG.problems
        errs = [p for p in probs if p["severity"] == "error"]
        self.banner.setVisible(bool(probs))
        while self.banner_lay.count():
            w = self.banner_lay.takeAt(0).widget()
            if w: w.hide(); w.setParent(None); w.deleteLater()
        if not probs: return
        n = len({p["file"] for p in errs})
        self.problem_count = n if errs else len({p["file"] for p in probs})
        self._banner_head = (f"{n} definition{'s' * (n != 1)} could not be loaded" if errs else
                             f"{self.problem_count} definition warning{'s' * (self.problem_count != 1)}")
        self.banner.setProperty("kind", "err" if errs else "warn"); theme.repolish(self.banner)
        for p in probs:
            head = W.label(f"<b>{escape(self._rel(p['file']))}</b> · {p['code']}", "code")
            head.setTextFormat(Qt.RichText)
            head.setTextFormat(Qt.RichText)
            msg = W.label(f"{p['message']}" + (f" [{p['field']}]" if p["field"] else ""), wrap=True)
            msg.setTextFormat(Qt.PlainText)
            self.banner_lay.addWidget(head); self.banner_lay.addWidget(msg)
        copy = W.link("Copy all diagnostics", self._copy_problems); self.banner_lay.addWidget(copy)
        self._expand_banner(False)

    def _copy_problems(self):
        QGuiApplication.clipboard().setText("\n".join(
            f"{p['severity']} {p['code']} {p['file']} [{p['field']}] {p['message']}" for p in CATALOG.problems))

    # ---- filtering
    def reload(self):
        """Rebuild from the (possibly reloaded) catalog."""
        self.category = self.category if self.category in CATALOG.categories() else None
        while self.nav_box.count():
            w = self.nav_box.takeAt(0).widget()
            if w: w.hide(); w.setParent(None); w.deleteLater()
        self.navs = {}
        for key, name in [(None, "All")] + [(c, c) for c in CATALOG.categories()]:
            n = NavItem(name, key); n.clicked.connect(lambda _=False, k=key: self.set_category(k))
            self.nav_box.addWidget(n); self.navs[key] = n
        self._fill_banner()
        self.refilter()

    def set_category(self, key):
        self.category = key; self.refilter()

    def query(self):
        return self.search.text().strip()

    def results(self):
        return CATALOG.search(self.search.text(), self.category, self.toggle.isChecked())

    def refilter(self, *_):
        text, sim = self.search.text(), self.toggle.isChecked()
        for key, nav in self.navs.items():
            nav.set_state(key == self.category, len(CATALOG.search(text, key, sim)))
        res = self.results()
        while self.card_lay.count() > 1:   # hide() first: a card added this event-loop turn still has Qt's queued
            w = self.card_lay.takeAt(0).widget()   # 'show if not hidden', which would open it as a window of its own
            if w: w.hide(); w.setParent(None); w.deleteLater()
        self.cards = {}
        for d in res:
            c = Card(d); c.clicked.connect(self.place_requested.emit); c.details.connect(self.show_details)
            self.card_lay.insertWidget(self.card_lay.count() - 1, c); self.cards[d.id] = c
        self._card_states()
        self.empty.setVisible(not res); self.scroll.setVisible(bool(res))
        if not res: self._fill_empty(text.strip(), sim)

    def _fill_empty(self, q, sim):
        where = f" in {self.category}" if self.category else ""
        if q:
            self.empty_title.setText(f"No components match '{q}'{where}")
            self.empty_text.setText("Search matches name, alias, tag, keyword and description.")
            self.empty_btn.setText("Clear search")
        elif sim:
            hidden = len(CATALOG.search("", self.category, False))
            self.empty_title.setText(f"No simulated components{where}")
            self.empty_text.setText(f"{hidden} interface-only component{'s are' if hidden != 1 else ' is'} hidden by "
                                    "'Simulated only'.")
            self.empty_btn.setText("Show all")
        else:
            self.empty_title.setText(f"No components{where}"); self.empty_text.setText("Nothing in the catalog yet.")
            self.empty_btn.setText("Show all")
            self.empty_btn.setVisible(False); return
        self.empty_btn.setVisible(True)

    def _card_states(self):
        for k, c in self.cards.items(): c.set_state(k == self.selected, k == self.armed)

    # ---- armed / selected (R1: independent of the canvas selection and the inspector)
    def set_armed(self, id):
        self.armed = id
        if id: self.selected = None
        self._card_states()

    def card(self, id):
        return self.cards.get(id)

    # ---- details view
    def show_details(self, id):
        d = CATALOG.defs.get(id)
        if d is None: return
        self.selected = id; self._card_states()
        self.detail_page.setWidgetResizable(True); self.detail_page.setFrameShape(QFrame.NoFrame)
        self.detail_page.setWidget(self._details_widget(d))
        self.stack.setCurrentWidget(self.detail_page)

    def back(self):
        self.stack.setCurrentWidget(self.list_page)

    def in_details(self):
        return self.stack.currentWidget() is self.detail_page

    def _details_widget(self, d):
        w = QWidget(); w.setObjectName("panel"); lay = QVBoxLayout(w); lay.setContentsMargins(12, 12, 12, 12); lay.setSpacing(8)
        back = W.link("‹  All components", self.back); back.setObjectName("back"); lay.addWidget(back)
        head = QHBoxLayout(); head.setSpacing(12); head.addWidget(W.tile(d), 0, Qt.AlignTop)
        col = QVBoxLayout(); col.setSpacing(2)
        col.addWidget(W.label(d.name, "title", wrap=True)); col.addWidget(W.label(d.id, "code"))
        col.addWidget(W.Badge(d.readiness), 0, Qt.AlignLeft); head.addLayout(col, 1); lay.addLayout(head)
        if d.description: lay.addWidget(W.label(d.description, "muted", wrap=True))
        lay.addWidget(W.section(f"Pins ({len(d.pins)})"))
        if d.pins:
            lay.addWidget(W.Table([("Pin", 1, False), ("ID", 1, True)], [[p.name, p.id] for p in d.pins], 5, "pins"))
        else:
            lay.addWidget(W.label("No pins.", "hint"))
        lay.addWidget(W.section("Parameters"))
        if d.params:
            rows = [[p.label, p.default, p.unit or "-"] for p in d.params]
            lay.addWidget(W.Table([("Name", 3, False), ("Default", 2, True), ("Unit", 1, False)], rows))
        else:
            lay.addWidget(W.label("No configurable parameters.", "hint"))
        if d.limitations:
            lay.addWidget(W.section("Limitations"))
            for b in W.bullets(d.limitations): lay.addWidget(b)
        docs = W.docs_block(d)
        if docs: lay.addWidget(docs)
        lay.addWidget(W.rule())
        lay.addWidget(W.label(f"id {d.id}   version {d.version}", "code"))
        lay.addStretch(1)
        place = QPushButton("Place"); place.setProperty("primary", True); place.setFixedHeight(theme.CONTROL_H)
        place.clicked.connect(lambda _=False, i=d.id: (self.back(), i != self.armed and self.place_requested.emit(i)))
        lay.addWidget(place)
        return w
