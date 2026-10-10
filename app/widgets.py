"""Small widgets shared by the library panel and the inspector (badge, toggle, tile, banner, table, doc links).
Qt only; no component knowledge: everything is passed in from a catalog Definition."""
import os
from html import escape

from PySide6.QtCore import QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import theme
from .catalog import DOC_EXTENSIONS
from .symbols import icon_pixmap


def label(text="", name=None, wrap=False):
    w = QLabel(text)
    if name: w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


def section(text):
    return label(text.upper(), "overline")


def rule():
    f = QFrame(); f.setObjectName("rule"); f.setFixedHeight(1)
    return f


def link(text, slot=None):
    b = QPushButton(text); b.setProperty("link", True); b.setCursor(Qt.PointingHandCursor); b.setFlat(True)
    if slot: b.clicked.connect(lambda _=False: slot())
    return b


class Badge(QLabel):
    """Readiness badge: dot + text on a tinted rounded rectangle (20 px high). `key` is a theme.READINESS key."""
    def __init__(self, key, parent=None):
        super().__init__(parent)
        text, self.fg, self.bg, tip = theme.READINESS[key]
        self.setText(text); self.setToolTip(tip); self.key = key
        f = QFont(theme.FONT); f.setPixelSize(theme.CAPTION_PX); f.setWeight(QFont.DemiBold); self.setFont(f)
        self.setFixedHeight(20)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 26, 20)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen); p.setBrush(QColor(self.bg)); p.drawRoundedRect(QRectF(self.rect()), theme.R_SM, theme.R_SM)
        p.setBrush(QColor(self.fg)); p.drawEllipse(QRectF(7, 7, 6, 6))
        p.setPen(QColor(self.fg)); p.setFont(self.font())
        p.drawText(QRectF(18, 0, self.width() - 18, 20), Qt.AlignVCenter | Qt.AlignLeft, self.text())


class PinState(QLabel):
    """Dot + word: filled green 'wired' or hollow red 'open'."""
    def __init__(self, wired, parent=None):
        super().__init__("wired" if wired else "open", parent)
        self.wired = wired
        f = QFont(theme.FONT); f.setPixelSize(theme.SMALL_PX); self.setFont(f)
        self.setMinimumWidth(70)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        c = QColor(theme.OK_PIN if self.wired else theme.OPEN_PIN)
        p.setPen(QPen(QColor(theme.OK_PIN_RING) if self.wired else c, 1.5))
        p.setBrush(c if self.wired else Qt.NoBrush)
        y = self.height() / 2
        p.drawEllipse(QRectF(1, y - 4.5, 9, 9))
        p.setPen(QColor(theme.STATE_COMPLETED if self.wired else theme.OPEN_PIN)); p.setFont(self.font())
        p.drawText(QRectF(18, 0, self.width() - 18, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text())


class Toggle(QAbstractButton):
    """36 x 20 switch (track off #B8C0CC, on primary)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True); self.setCursor(Qt.PointingHandCursor); self.setFixedSize(36, 20)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        p.setPen(Qt.NoPen); p.setBrush(QColor(theme.PRIMARY if on else theme.BORDER_STRONG))
        p.drawRoundedRect(QRectF(0, 0, 36, 20), 10, 10)
        p.setBrush(QColor("#FFFFFF")); p.drawEllipse(QRectF(18 if on else 2, 2, 16, 16))


def tile(defn=None, size=44, px=40, glyph="?"):
    """Rounded icon tile: the definition's icon (or rendered symbol), else a letter glyph on a dashed tile.
    `fallback` property + `.pix` tell tests and the stylesheet which one was used."""
    f = QFrame(); f.setObjectName("tile"); f.setFixedSize(size, size)
    lay = QVBoxLayout(f); lay.setContentsMargins(0, 0, 0, 0)
    pm = icon_pixmap(defn, px) if defn else None
    lab = QLabel(); lab.setAlignment(Qt.AlignCenter)
    if pm is not None:
        lab.setPixmap(pm)
    else:
        lab.setObjectName("glyph")
        lab.setText(glyph if defn is None else (defn.designator or glyph)[:2])
    f.setProperty("fallback", pm is None)
    f.pix = pm
    lay.addWidget(lab)
    return f


def banner(kind, text, wrap=True):
    """Flat message box. kind: info | warn | err. -> (frame, text label, layout)."""
    f = QFrame(); f.setObjectName("banner"); f.setProperty("kind", kind)
    lay = QVBoxLayout(f); lay.setContentsMargins(12, 8, 12, 8); lay.setSpacing(6)
    t = label(text, wrap=wrap); lay.addWidget(t)
    return f, t, lay


class Table(QWidget):
    """Header row + zebra-less rows with divider lines. `cols`: [(header, stretch, mono)]; `rows`: [[str | QWidget]].
    More than `limit` rows are folded behind a 'Show all N <what>' link."""
    def __init__(self, cols, rows, limit=None, what="rows", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        head = QFrame(); head.setObjectName("tablehead")
        hl = QHBoxLayout(head); hl.setContentsMargins(10, 4, 10, 4)
        for i, (h, st, _) in enumerate(cols): hl.addWidget(label(h.upper(), "th"), st)
        lay.addWidget(head)
        self.rows = []
        for r in rows:
            line = QWidget(); ll = QVBoxLayout(line); ll.setContentsMargins(0, 0, 0, 0); ll.setSpacing(0)
            row = QWidget(); rl = QHBoxLayout(row); rl.setContentsMargins(10, 5, 10, 5)
            for (h, st, mono), c in zip(cols, r):
                w = c if isinstance(c, QWidget) else label(str(c), "code" if mono else None)
                if isinstance(w, QLabel) and not mono: w.setStyleSheet("")
                rl.addWidget(w, st)
            ll.addWidget(row)
            sep = QFrame(); sep.setObjectName("rowline"); sep.setFixedHeight(1); ll.addWidget(sep)
            lay.addWidget(line); self.rows.append(line)
        self.more = None
        if limit and len(rows) > limit:
            for r in self.rows[limit:]: r.setVisible(False)
            self.more = link(f"Show all {len(rows)} {what}")
            self.more.clicked.connect(lambda _=False: ([r.setVisible(True) for r in self.rows], self.more.hide()))
            lay.addSpacing(6); lay.addWidget(self.more, 0, Qt.AlignLeft)


def doc_target(defn, d):
    """QUrl for a docs entry, or None when it must not be opened: only an http(s) URL, or a file of an allowed type
    whose real path (symlinks resolved) is inside the package directory."""
    if d.startswith(("http://", "https://")): return QUrl(d)
    base = os.path.realpath(defn.path)
    f = os.path.realpath(os.path.join(base, d))
    ok = os.path.commonpath([base, f]) == base and os.path.splitext(f)[1].lower() in DOC_EXTENSIONS
    return QUrl.fromLocalFile(f) if ok and os.path.isfile(f) else None


def doc_title(d):
    from urllib.parse import urlparse
    return urlparse(d).netloc or os.path.basename(d) if d.startswith(("http://", "https://")) else os.path.basename(d)


def docs_block(defn):
    """DOCS section widget for a definition, or None when it has no docs. Entries open with QDesktopServices."""
    if not defn.docs: return None
    w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(4)
    lay.addWidget(section("Docs"))
    n = 0
    for d in defn.docs:
        u = doc_target(defn, d)
        if u is None: continue   # not an http(s) URL or an allowed file inside the package: no link
        b = link("↗  " + doc_title(d)); b.setToolTip(d)
        b.clicked.connect(lambda _=False, u=u: QDesktopServices.openUrl(u))
        lay.addWidget(b); n += 1
    return w if n else None


def bullets(items):
    out = []
    for t in items:
        l = label(f"•  {escape(t)}", wrap=True); l.setTextFormat(Qt.RichText); out.append(l)
    return out
