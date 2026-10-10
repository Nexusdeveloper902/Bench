"""Circuit canvas: a QGraphicsView over the document. Geometry helpers (pin and wire positions), grid, zoom (25-400 %,
view transform only), pan, drop from the library, hit testing (pin radius is constant in screen pixels), the drawing of
parts, wires and pins in their hover / selected / pending states, and the overlays (zoom control, part toolbar, empty
hint, tooltip). Everything per component comes from the catalog Definition through app.symbols; no kind is named here.
Design: docs/milestone/03-design.md (canvas frames)."""
import math
from functools import reduce

from PySide6.QtCore import QLineF, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen, QPolygonF,
                           QRadialGradient, QTransform)
from PySide6.QtWidgets import (QFrame, QGraphicsScene, QGraphicsView, QGridLayout, QHBoxLayout, QLabel, QMenu,
                               QToolButton, QVBoxLayout)

from . import theme, widgets as W
from .catalog import CATALOG, geometry, pin_offset, summary
from .components import format_si
from .library import MIME
from .symbols import add_badge, add_placeholder, add_symbol, is_generic, part_transform

GRID = 20
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.25, 4.0, 1.25
PIN_HIT = 10          # pin hit radius in SCREEN pixels (divided by the zoom for the scene)
MARGIN = 200          # scene rect = the parts' bounds + this margin, and at least the visible area from the origin


# ---------------------------------------------------------------- geometry (catalog driven)
def _box(part):
    x0, y0, w, h = geometry(part)[0]
    return x0, y0, w, h, x0 + w / 2, y0 + h / 2


def pin_pos(doc, ref):
    name, _, pin = ref.partition(".")
    d = doc.parts[name]
    ox, oy = pin_offset(d, pin)
    return QPointF(d["x"] + ox, d["y"] + oy)


def part_rect(d):
    x0, y0, w, h, *_ = _box(d)
    return part_transform(d).mapRect(QRectF(x0, y0, w, h))


def exits_horizontally(doc, ref):
    name, _, pin = ref.partition(".")
    d = doc.parts[name]
    *_, cx, cy = _box(d)
    px, py = geometry(d)[1][pin]
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


def snap(v):
    return round(v / GRID) * GRID


def _font(px, weight=QFont.Normal):
    f = QFont(theme.FONT); f.setPixelSize(px); f.setWeight(weight)
    return f


# ---------------------------------------------------------------- overlay widgets (children of the viewport)
class ZoomControl(QFrame):
    """168 x 32 pill: minus | percent | plus | fit. The view owns the zoom; this only shows and requests."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("floating"); self.setFixedSize(168, 32)
        lay = QHBoxLayout(self); lay.setContentsMargins(1, 1, 1, 1); lay.setSpacing(0)

        def btn(name, tip):
            b = W.set_icon(QToolButton(), name, "TEXT", 16); b.setIconSize(QSize(16, 16))
            b.setToolTip(tip); b.setFixedHeight(30); b.setCursor(Qt.PointingHandCursor); return b
        self.minus, self.plus, self.fit = btn("minus", "Zoom out  Ctrl -"), btn("plus", "Zoom in  Ctrl +"), \
            btn("fit", "Fit all parts  Ctrl 0")
        self.pct = W.label("100%", "zpct"); self.pct.setAlignment(Qt.AlignCenter); self.pct.setFixedWidth(56)
        lay.addWidget(self.minus, 1); lay.addWidget(W.vline(18)); lay.addWidget(self.pct); lay.addWidget(W.vline(18))
        lay.addWidget(self.plus, 1); lay.addWidget(W.vline(18)); lay.addWidget(self.fit, 1)

    def set_zoom(self, z):
        self.pct.setText(f"{round(z * 100)}%")


class PartToolbar(QFrame):
    """76 x 32 floating Rotate / Delete buttons above the selected part."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("floating"); self.setFixedSize(76, 32)
        lay = QHBoxLayout(self); lay.setContentsMargins(1, 1, 1, 1); lay.setSpacing(0)
        self.rotate, self.delete = QToolButton(), QToolButton()
        for b, name, col, tip in ((self.rotate, "rotate", "TEXT_2", "Rotate 90°  R"),
                                  (self.delete, "trash", "BAD", "Delete  Del")):
            W.set_icon(b, name, col, 16); b.setIconSize(QSize(16, 16)); b.setToolTip(tip)
            b.setFixedHeight(30); b.setCursor(Qt.PointingHandCursor); lay.addWidget(b, 1)
        lay.insertWidget(1, W.vline(18))


class HintCard(QFrame):
    """Empty-canvas hint (replaces the old text strip). Does not take mouse events: clicks reach the canvas."""
    STEPS = (("Place", "Pick a component in the library, then click here."),
             ("Connect", "Click a pin, then another pin (or drag pin to pin)."),
             ("Configure", "Select a part and edit its values: 1k, 10u, 4.7n."),
             ("Run", "Press Run or F5. Click a wire to plot its node."))

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("hintcard"); self.setAttribute(Qt.WA_TransparentForMouseEvents); self.setFixedWidth(372)
        g = QGridLayout(self); g.setContentsMargins(20, 18, 20, 14); g.setHorizontalSpacing(10); g.setVerticalSpacing(10)
        t = W.label("Build a circuit", "title"); t.setAlignment(Qt.AlignCenter)
        g.addWidget(t, 0, 0, 1, 2); g.setRowMinimumHeight(0, 28)
        for i, (head, text) in enumerate(self.STEPS):
            n = W.label(str(i + 1), "stepnum"); n.setFixedSize(22, 22); n.setAlignment(Qt.AlignCenter)
            col = QVBoxLayout(); col.setSpacing(0); col.setContentsMargins(0, 0, 0, 0)
            col.addWidget(W.label(head, "steptitle")); col.addWidget(W.label(text, "muted", wrap=True))
            g.addWidget(n, i + 1, 0, Qt.AlignTop); g.addLayout(col, i + 1, 1)
        k = W.label("R rotate   Del delete   Esc cancel   Ctrl+wheel zoom   Space+drag pan", "kbd", wrap=True)
        k.setAlignment(Qt.AlignCenter); g.addWidget(k, 5, 0, 1, 2)


class Tip(QLabel):
    """Dark tooltip pill that follows the cursor (pin names, 'Click to place ...'). Transparent for the mouse."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("tip"); self.setAttribute(Qt.WA_TransparentForMouseEvents); self.hide()

    def show_at(self, pos, text):
        self.setText(text); self.adjustSize()
        p = self.parentWidget().rect()
        x, y = pos.x() + 14, pos.y() + 18
        self.move(min(x, max(0, p.width() - self.width() - 4)), min(y, max(0, p.height() - self.height() - 4)))
        self.show(); self.raise_()


# ---------------------------------------------------------------- the view
class Canvas(QGraphicsView):
    zoom_changed = Signal(float)

    def __init__(self, win):
        self.sc = QGraphicsScene()
        super().__init__(self.sc)
        self.win = win
        self.drag = None        # (part, dx, dy) while moving a part
        self.wiring = False     # pressed on a pin, may drag to another
        self.hover = None       # ("pin", ref) | ("wire", wire) | ("part", name)
        self.cursor = None      # last mouse position in scene coordinates (rubber band, placement ghost)
        self.pan = None         # last viewport position while panning (middle button, or Space + left button)
        self.space = False
        self._rect = QRectF(0, 0, 1000, 700)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFrameShape(QFrame.NoFrame)
        self.setSceneRect(self._rect)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setTransformationAnchor(QGraphicsView.NoAnchor); self.setResizeAnchor(QGraphicsView.NoAnchor)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setAcceptDrops(True); self.viewport().setAcceptDrops(True)
        vp = self.viewport()
        self.zoom_ctl, self.part_bar, self.hint, self.tip = ZoomControl(vp), PartToolbar(vp), HintCard(vp), Tip(vp)
        self.zoom_ctl.minus.clicked.connect(lambda: self.zoom_by(1 / ZOOM_STEP))
        self.zoom_ctl.plus.clicked.connect(lambda: self.zoom_by(ZOOM_STEP))
        self.zoom_ctl.fit.clicked.connect(self.fit)
        self.part_bar.rotate.clicked.connect(lambda: (win.rotate_selected(), self.setFocus()))
        self.part_bar.delete.clicked.connect(lambda: (win.delete_selected(), self.setFocus()))
        self.part_bar.hide()
        self._place_overlays()

    # ---- zoom / pan / fit (view transform only: part positions never change) ----
    @property
    def zoom(self):
        return self.transform().m11()

    def set_zoom(self, z, anchor=None):
        """Zoom to z (clamped to 25-400 %, 100 % when close to it), keeping the scene point under `anchor` (viewport
        position; default the view centre) fixed."""
        z = max(ZOOM_MIN, min(ZOOM_MAX, z)); z = 1.0 if abs(z - 1) < 0.02 else z
        anchor = anchor if anchor is not None else self.viewport().rect().center()
        p = self.mapToScene(anchor)
        self.setTransform(QTransform.fromScale(z, z))
        d = self.mapFromScene(p) - anchor
        hb, vb = self.horizontalScrollBar(), self.verticalScrollBar()
        hb.setValue(hb.value() + d.x()); vb.setValue(vb.value() + d.y())
        self._zoomed()

    def zoom_by(self, factor, anchor=None):
        self.set_zoom(self.zoom * factor, anchor)

    def fit(self):
        """Zoom (at most 100 %) and scroll so every part is visible; no parts: 100 % at the origin."""
        rects = [part_rect(d) for d in self.win.doc.parts.values()]
        if not rects:
            self.setTransform(QTransform()); self.horizontalScrollBar().setValue(0); self.verticalScrollBar().setValue(0)
            return self._zoomed()
        r = reduce(QRectF.united, rects).adjusted(-40, -40, 40, 40)
        vp = self.viewport().size()
        z = max(ZOOM_MIN, min(1.0, vp.width() / r.width(), vp.height() / r.height()))
        self.setTransform(QTransform.fromScale(z, z)); self.centerOn(r.center())
        self._zoomed()

    def _zoomed(self):
        self._update_rect(); self.zoom_ctl.set_zoom(self.zoom); self._place_overlays(); self.viewport().update()
        self.zoom_changed.emit(self.zoom)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.zoom_by(1.2 ** (e.angleDelta().y() / 120), e.position().toPoint()); e.accept()
        else:
            super().wheelEvent(e)

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy); self._place_overlays()

    def resizeEvent(self, e):
        super().resizeEvent(e); self._update_rect(); self._place_overlays()

    def refresh_cursor(self):
        c = Qt.ClosedHandCursor if self.pan is not None else Qt.OpenHandCursor if self.space else \
            Qt.CrossCursor if self.win.tool else Qt.ArrowCursor
        self.viewport().setCursor(c)

    def _set_scene_rect(self, r):
        """Resize the scene rect without moving the picture under the mouse (the scroll position is compensated)."""
        if r == self._rect: return
        before = self.mapToScene(0, 0)
        self._rect = r; self.setSceneRect(r)
        d = (before - self.mapToScene(0, 0)) * self.zoom
        hb, vb = self.horizontalScrollBar(), self.verticalScrollBar()
        hb.setValue(hb.value() + round(d.x())); vb.setValue(vb.value() + round(d.y()))

    def _update_rect(self):
        """Scene rect: the parts' bounds plus a margin, and at least the visible area from the origin (so a small
        circuit does not scroll). While a part is dragged it only grows."""
        vp, z = self.viewport().size(), self.zoom
        r = QRectF(0, 0, vp.width() / z, vp.height() / z)
        rects = [part_rect(d) for d in self.win.doc.parts.values()]
        if rects: r = r.united(reduce(QRectF.united, rects).adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN))
        self._set_scene_rect(r.united(self._rect) if self.drag else r)

    def _place_overlays(self):
        vp, w = self.viewport().rect(), self.win
        self.zoom_ctl.move(12, vp.height() - self.zoom_ctl.height() - 12)
        self.hint.adjustSize(); self.hint.move((vp.width() - self.hint.width()) // 2,
                                               max(8, (vp.height() - self.hint.height()) // 2))
        self.hint.setVisible(not w.doc.parts)
        sel = w.sel if w.sel in w.doc.parts else None
        if sel is None:
            return self.part_bar.hide()
        r = self.mapFromScene(part_rect(w.doc.parts[sel]).adjusted(-6, -6, 6, 6)).boundingRect()
        x = max(4, min(r.center().x() - self.part_bar.width() // 2, vp.width() - self.part_bar.width() - 4))
        y = r.top() - 30 - self.part_bar.height()
        self.part_bar.move(x, y if y >= 4 else r.bottom() + 30)
        self.part_bar.show(); self.part_bar.raise_()

    # ---- grid ----
    def drawBackground(self, p, rect):
        p.fillRect(rect, QColor(theme.CANVAS_BG))
        step = GRID if self.zoom * GRID >= 8 else GRID * 5      # 25 % zoom: 100 unit lines, not a haze
        pen = QPen(QColor(theme.GRID), 1); pen.setCosmetic(True)
        p.setPen(pen); p.setRenderHint(QPainter.Antialiasing, False)
        x0, y0 = math.floor(rect.left() / step) * step, math.floor(rect.top() / step) * step
        p.drawLines([QLineF(x, rect.top(), x, rect.bottom()) for x in range(x0, math.ceil(rect.right()) + 1, step)] +
                    [QLineF(rect.left(), y, rect.right(), y) for y in range(y0, math.ceil(rect.bottom()) + 1, step)])

    # ---- hit tests ----
    def hit(self, pt):
        doc, z = self.win.doc, self.zoom
        for n in doc.parts:
            for r in doc.pins(n):
                if (pin_pos(doc, r) - pt).manhattanLength() < PIN_HIT / z: return ("pin", r)
        for n, d in doc.parts.items():
            if part_rect(d).contains(pt): return ("part", n)
        for w in doc.wires:
            pts = wire_pts(doc, *w)
            if any(seg_dist(pt, pts[i], pts[i + 1]) < 5 / z for i in range(len(pts) - 1)): return ("wire", w)
        return None

    def led_glow(self, name):
        return self.win.led_glow(name)

    def pt(self, e):
        return self.mapToScene(e.position().toPoint())

    def pin_title(self, ref):
        """'name (id)' of a pin, e.g. 'Terminal b (b)'-style text for the hover tooltip."""
        doc = self.win.doc
        n, _, pin = ref.partition(".")
        c = doc.definition(n)
        p = next((p for p in c.pins if p.id == pin), None) if c else None
        return pin if p is None or p.name == pin else f"{p.name} ({pin})"

    def part_menu(self, name):
        """Context menu of a part: Rotate, Delete, Disconnect all (removes only its wires)."""
        w, m = self.win, QMenu(self)
        m.addAction("Rotate", lambda: (w.select(name), w.rotate_selected()))
        m.addAction("Delete", lambda: (w.select(name), w.delete_selected()))
        d = m.addAction("Disconnect all", lambda: w.disconnect_all(name))
        d.setEnabled(w.doc.wired(name))
        return m

    # ---- events ----
    def mousePressEvent(self, e):
        self.setFocus()
        w, doc = self.win, self.win.doc
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and self.space):
            self.pan = e.position().toPoint(); return self.refresh_cursor()
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
        if e.button() == Qt.RightButton and h and h[0] == "part":
            w.select(h[1]); self.part_menu(h[1]).popup(e.globalPosition().toPoint()); return
        if e.button() != Qt.LeftButton:
            return w.refresh()
        if w.tool and not h:
            w.select(w.place(w.tool, snap(pt.x()), snap(pt.y()))); w.set_tool(None)
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
        if self.pan is not None:
            p = e.position().toPoint(); d = p - self.pan; self.pan = p
            hb, vb = self.horizontalScrollBar(), self.verticalScrollBar()
            hb.setValue(hb.value() - d.x()); vb.setValue(vb.value() - d.y())
            return
        pt = self.cursor = self.pt(e)
        if self.drag:
            n, dx, dy = self.drag
            doc.parts[n]["x"] = snap(pt.x() - dx)
            doc.parts[n]["y"] = snap(pt.y() - dy)
            return w.draw()
        h = self.hit(pt)
        self.hover = h if h and h[0] in ("pin", "wire", "part") else None
        tip = (self.pin_title(h[1]) if h and h[0] == "pin" else
               "Click: select wire, plot its node" if h and h[0] == "wire" else None)
        if w.tool and not (h and h[0] == "pin"): tip = f"Click to place {CATALOG.get(w.tool).name}     Esc to cancel"
        if tip: self.tip.show_at(e.position().toPoint(), tip)
        else: self.tip.hide()
        w.draw()

    def mouseReleaseEvent(self, e):
        w, doc = self.win, self.win.doc
        if self.pan is not None:
            self.pan = None; return self.refresh_cursor()
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
        self.hover = None; self.cursor = None; self.tip.hide(); self.win.draw()

    def focusOutEvent(self, e):
        self.space = False; self.refresh_cursor(); super().focusOutEvent(e)

    def keyPressEvent(self, e):
        w, ctrl = self.win, bool(e.modifiers() & Qt.ControlModifier)
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            w.delete_selected()
        elif e.key() == Qt.Key_Escape:   # cancels in order: armed placement, pending wire, selection
            if w.tool: w.set_tool(None); w.set_status("Ready.")
            elif w.pending_pin: w.pending_pin = None
            else: w.sel = w.sel_wire = None
            w.refresh()
        elif e.key() == Qt.Key_Space and not ctrl:
            if not e.isAutoRepeat(): self.space = True; self.refresh_cursor()
        elif e.key() == Qt.Key_R and not e.modifiers() & ~Qt.KeypadModifier:   # fallback if the shortcut is inactive
            w.rotate_selected()
        elif ctrl and e.key() in (Qt.Key_Equal, Qt.Key_Plus):   # fallbacks if the window shortcuts are inactive
            self.zoom_by(ZOOM_STEP)
        elif ctrl and e.key() == Qt.Key_Minus:
            self.zoom_by(1 / ZOOM_STEP)
        elif ctrl and e.key() == Qt.Key_0:
            self.fit()
        elif e.matches(QKeySequence.Undo):
            w.undo()
        elif e.matches(QKeySequence.Redo) or (e.key() == Qt.Key_Y and ctrl):
            w.redo()
        else:
            super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self.space = False; self.refresh_cursor()
        super().keyReleaseEvent(e)

    # ---- drop from the library (mime = component id) ----
    def dragEnterEvent(self, e):
        e.acceptProposedAction() if e.mimeData().hasFormat(MIME) else super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        e.acceptProposedAction() if e.mimeData().hasFormat(MIME) else super().dragMoveEvent(e)

    def dropEvent(self, e):
        if not e.mimeData().hasFormat(MIME): return super().dropEvent(e)
        kind, pt = bytes(e.mimeData().data(MIME)).decode(), self.mapToScene(e.position().toPoint())
        if kind not in CATALOG.defs: return e.ignore()
        w = self.win
        w.select(w.place(kind, snap(pt.x()), snap(pt.y()))); w.set_tool(None); e.acceptProposedAction()
        self.setFocus()

    # ---- drawing ----
    def draw(self):
        w, doc, sc = self.win, self.win.doc, self.sc
        sc.clear()
        wired = {}
        for wr in doc.wires:
            for p in wr: wired[p] = wired.get(p, 0) + 1
        for wr in doc.wires:
            pts, selw = wire_pts(doc, *wr), wr == w.sel_wire
            col, wd = (theme.SEL, 3) if selw else (theme.HOVER, 3) if self.hover == ("wire", wr) else (theme.WIRE, 2)
            path = QPainterPath(pts[0])
            for q in pts[1:]: path.lineTo(q)
            sc.addPath(path, QPen(QColor(col), wd, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        nets = doc.nets()
        flag = {p for f in (w.result.findings if w.result else []) for p in nets
                if nets[p] in f.get("nodes", [])}   # pins on nets named by core findings
        for p in flag:
            q = pin_pos(doc, p)
            sc.addEllipse(q.x() - 10, q.y() - 10, 20, 20, QPen(QColor(theme.FINDING), 3))
        for n, d in doc.parts.items():
            self._part(n, d, wired)
        if w.sel_wire in doc.wires:   # end handles of the selected wire, over the pins
            pts = wire_pts(doc, *w.sel_wire)
            for q in (pts[0], pts[-1]):
                sc.addEllipse(q.x() - 5, q.y() - 5, 10, 10, QPen(QColor(theme.SEL), 2), QBrush(QColor(theme.CANVAS_BG)))
        if w.pending_pin in {p for n in doc.parts for p in doc.pins(n)} and self.cursor is not None:
            a = pin_pos(doc, w.pending_pin)
            sc.addLine(a.x(), a.y(), self.cursor.x(), self.cursor.y(), QPen(QColor(theme.HOVER), 1.5, Qt.DashLine))
        if w.tool and self.cursor is not None and w.tool in CATALOG.defs:
            self._ghost(w.tool)
        self._update_rect()
        self._place_overlays()

    def _part(self, n, d, wired):
        w, doc, sc = self.win, self.win.doc, self.sc
        c, sel = doc.definition(n), n == w.sel
        hov = self.hover == ("part", n) and not self.drag
        col = theme.SEL if sel else theme.HOVER if hov else theme.INK
        r = part_rect(d)
        if sel:
            sc.addRect(r.adjusted(-6, -6, 6, 6), QPen(QColor(theme.SEL), 1.5, Qt.DashLine),
                       QBrush(self._alpha(theme.SEL_FILL, 140)))
        if self.drag and self.drag[0] == n:   # dragging: faint box and dashed snap guides through the part origin
            sc.addRect(r.adjusted(-6, -6, 6, 6), QPen(QColor(theme.HOVER), 1.5, Qt.DashLine),
                       QBrush(self._alpha(theme.HOVER, 30)))
            v = self.mapToScene(self.viewport().rect()).boundingRect()
            g = QPen(self._alpha(theme.HOVER, 160), 1, Qt.DashLine)
            sc.addLine(r.left(), v.top(), r.left(), v.bottom(), g); sc.addLine(v.left(), r.top(), v.right(), r.top(), g)
        cur = w.part_current(n) if c and c.show_current else None
        if c and c.indicator == "glow" and cur is not None:
            self._glow(d, n)
        generic = c is not None and is_generic(c, d["params"])
        if c: add_symbol(sc, d, c, col, n)
        else: add_placeholder(sc, d, col, n)
        if c is None or generic or c.readiness != "simulated":   # readiness badge; its tooltip says why
            at = QPointF(r.left(), r.bottom() + 30) if c is None else QPointF(r.left(), r.top() - (8 if generic else 30))
            tip = d["placeholder"]["detail"] if c is None else (c.limitations[0] if c.limitations else "Not simulated")
            add_badge(sc, "placeholder" if c is None else c.readiness, at, tip)
        self._labels(n, d, c, r, cur, sel, named=not generic)   # a generic body prints the name inside (F5: values stay)
        for ref in doc.pins(n):
            self._pin(ref, wired)

    @staticmethod
    def _alpha(color, a):
        c = QColor(color); c.setAlpha(a); return c

    def _labels(self, n, d, c, r, cur, sel, named=True):
        sc = self.sc
        name = sc.addSimpleText(n, _font(theme.SMALL_PX, QFont.DemiBold)); name.setBrush(QBrush(QColor(theme.SEL if sel else theme.INK)))
        rows = [summary(d["kind"], d["params"]) if c else ""]
        if cur is not None: rows.append(format_si(cur, "A", 3))
        texts = []
        for s in filter(None, rows):
            t = sc.addSimpleText(s, _font(theme.SMALL_PX)); t.setBrush(QBrush(QColor(theme.TEXT_2))); texts.append(t)
        nh = name.boundingRect().height()
        if not named: sc.removeItem(name)   # only the value and current rows remain
        if r.height() > r.width():   # vertical part: labels to the right
            y = r.center().y() - nh
            name.setPos(r.right() + 8, y)
            for i, t in enumerate(texts): t.setPos(r.right() + 8, r.center().y() + i * nh)
        else:                        # name above the body, value (and current) below, both left-aligned
            name.setPos(r.left(), r.top() - nh - 8)
            for i, t in enumerate(texts): t.setPos(r.left(), r.bottom() + 4 + i * nh)

    def _pin(self, ref, wired):
        w, doc, sc = self.win, self.win.doc, self.sc
        p = pin_pos(doc, ref)
        hov, pend = self.hover == ("pin", ref), ref == w.pending_pin
        if hov or pend:   # halo + blue ring
            sc.addEllipse(p.x() - 9, p.y() - 9, 18, 18, QPen(Qt.NoPen), QBrush(self._alpha(theme.HOVER, 50)))
            pen, br = QPen(QColor(theme.HOVER), 2), QBrush(QColor(theme.CANVAS_BG))
        elif ref in wired: pen, br = QPen(QColor(theme.OK_PIN_RING), 1.5), QBrush(QColor(theme.OK_PIN))
        else: pen, br = QPen(QColor(theme.OPEN_PIN), 1.5), QBrush(QColor(theme.CANVAS_BG))   # unconnected: hollow red
        sc.addEllipse(p.x() - 4.5, p.y() - 4.5, 9, 9, pen, br)
        if wired.get(ref, 0) >= 2:   # junction: 3+ conductors meet at this pin
            sc.addEllipse(p.x() - 4, p.y() - 4, 8, 8, QPen(Qt.NoPen), QBrush(QColor(theme.WIRE)))

    def _ghost(self, kind):
        """Faint copy of the armed part under the cursor, snapped to the grid."""
        c, sc = CATALOG.get(kind), self.sc
        g = {"kind": kind, "version": c.version, "x": snap(self.cursor.x()), "y": snap(self.cursor.y()), "rot": 0,
             "params": c.defaults()}
        for it in add_symbol(sc, g, c, theme.TEXT_2): it.setOpacity(0.55)
        r = part_rect(g)
        sc.addRect(r.adjusted(-6, -6, 6, 6), QPen(self._alpha(theme.SEL, 200), 1.5, Qt.DashLine),
                   QBrush(self._alpha(theme.SEL_FILL, 90)))
        for pin in geometry(g)[1]:
            q = QPointF(g["x"], g["y"]) + QPointF(*pin_offset(g, pin))
            sc.addEllipse(q.x() - 3.5, q.y() - 3.5, 7, 7, QPen(Qt.NoPen), QBrush(QColor(theme.SEL)))

    def _glow(self, d, n):
        sc, g = self.sc, self.led_glow(n)
        x0, y0, w, h, cx, cy = _box(d)
        t = part_transform(d); ctr = t.map(QPointF(cx, cy))
        if g > 0:
            gr = QRadialGradient(ctr, 55); a = int(255 * min(1, 0.4 + g))
            gr.setColorAt(0, QColor(255, 140, 60, a)); gr.setColorAt(0.4, QColor(255, 60, 20, a // 2))
            gr.setColorAt(1, QColor(255, 60, 20, 0))
            sc.addEllipse(ctr.x() - 55, ctr.y() - 55, 110, 110, QPen(Qt.NoPen), QBrush(gr))
        off, on = QColor(theme.LED_OFF), QColor(theme.LED_ON)
        rgb = [int(a + (b - a) * g) for a, b in zip((off.red(), off.green(), off.blue()), (on.red(), on.green(), on.blue()))]
        sc.addPolygon(t.map(QPolygonF([QPointF(cx - 10, cy - 10), QPointF(cx - 10, cy + 10), QPointF(cx + 10, cy)])),
                      QPen(Qt.NoPen), QBrush(QColor(*rgb)))
