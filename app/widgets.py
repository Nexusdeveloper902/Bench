"""Small widgets shared by the library panel and the inspector (badge, toggle, tile, banner, table, doc links).
Qt only; no component knowledge: everything is passed in from a catalog Definition."""
import os
from html import escape

from functools import cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import theme
from .catalog import DOC_EXTENSIONS, SCHEME_RE, WEB_URL
from .symbols import icon_pixmap


# 24 x 24 line icons (stroke 1.5 in the design, round caps); the shapes are plain SVG path data.
ICONS = {
    "new": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
    "open": '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    "save": '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>'
            '<polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>',
    "undo": '<polyline points="9 14 4 9 9 4"/><path d="M20 20v-7a4 4 0 0 0-4-4H4"/>',
    "redo": '<polyline points="15 14 20 9 15 4"/><path d="M4 20v-7a4 4 0 0 1 4-4h12"/>',
    "rotate": '<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.13-9.36L23 10"/>',
    "trash": '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    "minus": '<line x1="5" y1="12" x2="19" y2="12"/>',
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "fit": '<path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "alert": '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>'
             '<line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "xcircle": '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>',
    "down": '<polyline points="6 9 12 15 18 9"/>',
    "up": '<polyline points="6 15 12 9 18 15"/>',
    "right": '<polyline points="9 6 15 12 9 18"/>',
    "play": '<polygon points="6 3 20 12 6 21 6 3" fill="currentColor"/>',
    "pause": '<rect x="6" y="4" width="4" height="16" fill="currentColor"/><rect x="14" y="4" width="4" height="16" fill="currentColor"/>',
    "pulse": '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    "hand": '<path d="M18 11V6a2 2 0 0 0-4 0v1M14 10V4a2 2 0 0 0-4 0v6M10 10.5V6a2 2 0 0 0-4 0v8"/>'
            '<path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"/>',
}


@cache
def svg_pixmap(name, color, px, stroke=1.5):
    """ICONS[name] drawn in `color` into a px x px pixmap (device ratio 2 for crispness)."""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="{stroke * 24 / px:g}" stroke-linecap="round" stroke-linejoin="round">'
           f'{ICONS[name].replace("currentColor", color)}</svg>')
    r, k = QSvgRenderer(QByteArray(svg.encode())), 2
    pm = QPixmap(px * k, px * k); pm.fill(Qt.transparent); pm.setDevicePixelRatio(k)
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing); r.render(p, QRectF(0, 0, px, px)); p.end()
    return pm


def icon(name, color=theme.TEXT_2, px=20, disabled=theme.TEXT_DISABLED):
    """QIcon of ICONS[name]: `color` normally, `disabled` when the button is disabled."""
    i = QIcon()
    i.addPixmap(svg_pixmap(name, color, px), QIcon.Normal); i.addPixmap(svg_pixmap(name, disabled, px), QIcon.Disabled)
    return i


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
    if d.startswith(WEB_URL): return QUrl(d)
    if SCHEME_RE.match(d): return None      # file:, javascript:, ftp: ... are never opened
    base = os.path.realpath(defn.path)
    f = os.path.realpath(os.path.join(base, d))
    ok = os.path.commonpath([base, f]) == base and os.path.splitext(f)[1].lower() in DOC_EXTENSIONS
    return QUrl.fromLocalFile(f) if ok and os.path.isfile(f) else None


def doc_title(d):
    from urllib.parse import urlparse
    return urlparse(d).netloc or os.path.basename(d) if d.startswith(WEB_URL) else os.path.basename(d)


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


class StateBadge(QWidget):
    """Run-state pill (24 px high): coloured outline, dot (hollow ring while running) and bold text. `key` is a
    theme.STATES key."""
    def __init__(self, parent=None):
        super().__init__(parent)
        f = QFont(theme.FONT); f.setPixelSize(theme.SMALL_PX); f.setWeight(QFont.DemiBold); self.setFont(f)
        self.key = "idle"; self.setFixedHeight(24); self.set_state("idle")

    def text(self):
        return theme.STATES[self.key][0]

    def set_state(self, key):
        self.key = key; self.setFixedWidth(self.fontMetrics().horizontalAdvance(self.text()) + 40); self.update()

    def paintEvent(self, _):
        text, col, hollow = theme.STATES[self.key]
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        c = QColor(col)
        p.setPen(QPen(c, 1.5)); p.setBrush(QColor(theme.PANEL)); p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 11, 11)
        if hollow: p.setBrush(Qt.NoBrush); p.setPen(QPen(c, 1.5)); p.drawEllipse(QRectF(10, 8, 8, 8))
        else: p.setPen(Qt.NoPen); p.setBrush(c); p.drawEllipse(QRectF(10, 8, 8, 8))
        p.setPen(c); p.setFont(self.font())
        p.drawText(QRectF(24, 0, self.width() - 24, 24), Qt.AlignVCenter | Qt.AlignLeft, text)


def icon_label(name, color, px=16):
    """QLabel showing ICONS[name] (message and problem rows)."""
    l = QLabel(); l.setPixmap(svg_pixmap(name, color, px)); l.setFixedSize(px, px)
    l.setStyleSheet("background: transparent; border: 0"); return l


def vline(h=20):
    f = QFrame(); f.setObjectName("vline"); f.setFixedHeight(h); return f
