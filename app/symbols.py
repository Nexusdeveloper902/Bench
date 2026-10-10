"""Part symbols as SVG items. A symbol file's user units are part-local coordinates (the same as pin `at` and `box`);
`currentColor` is replaced by the state colour before loading. A part without a usable symbol draws as a generic
labelled box. Qt only; all geometry comes from the catalog Definition."""
from functools import cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtSvgWidgets import QGraphicsSvgItem
from PySide6.QtWidgets import QGraphicsItem

from . import theme
from .catalog import Pin, geometry


def part_transform(d):
    """Local (unrotated catalog coords) -> scene. Rotation is clockwise about the body centre, as in components."""
    x0, y0, w, h = geometry(d)[0]
    cx, cy = x0 + w / 2, y0 + h / 2
    t = QTransform(); t.translate(d["x"] + cx, d["y"] + cy); t.rotate(d.get("rot", 0)); t.translate(-cx, -cy)
    return t


@cache   # ponytail: unbounded; items hold the renderer, so it must outlive them. A few files x a few colours.
def renderer(path, color):
    """QSvgRenderer of the SVG at `path` with currentColor -> `color` (hex str), or None if unreadable/invalid."""
    try:
        svg = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    r = QSvgRenderer(QByteArray(svg.replace("currentColor", color).encode()))
    return r if r.isValid() else None


def _font(px, weight=QFont.Normal, mono=False):
    f = QFont(theme.MONO if mono else theme.FONT); f.setPixelSize(px); f.setWeight(weight)
    return f


def _wrap(text, px, maxw):
    """Greedy word wrap of a title (breaking after a hyphen too) into lines no wider than maxw (a long word stays)."""
    fm, lines = QFontMetricsF(_font(px)), []
    for tok in text.replace("-", "- ").split(" "):
        sep = "" if not lines or lines[-1].endswith("-") else " "
        if lines and fm.horizontalAdvance(lines[-1] + sep + tok) <= maxw: lines[-1] += sep + tok
        else: lines.append(tok)
    return lines or [text]


def add_generic_body(scene, transform, box, pins, title, color, name=None, fill=None, dashed=False):
    """Rounded box with the pin name at each pin, and the title centred (above it the part `name`, in bold, when given).
    `fill` (a colour) and `dashed` restyle it (placeholders). -> items."""
    col, items = QColor(color), []
    x0, y0, w, h = box
    body = QPainterPath(); body.addRoundedRect(QRectF(x0, y0, w, h), 8, 8)
    pen = QPen(col, 2, Qt.DashLine if dashed else Qt.SolidLine)
    items.append(scene.addPath(transform.map(body), pen, QBrush(QColor(fill or theme.PANEL_ALT))))
    c = transform.map(QPointF(x0 + w / 2, y0 + h / 2))
    for p in pins:
        q = transform.map(QPointF(*p.at))
        s = 1 if q.x() < c.x() else -1                       # label on the side facing the body centre
        t = scene.addSimpleText(p.name, _font(theme.SMALL_PX)); t.setBrush(QBrush(col))
        t.setPos(q.x() + 9 if s > 0 else q.x() - 9 - t.boundingRect().width(), q.y() - t.boundingRect().height() / 2)
        items.append(t)
    lines = _wrap(title, theme.CAPTION_PX, w * 0.5) if h >= 100 else [title]   # a tall board wraps its name
    fm = QFontMetricsF(_font(theme.CAPTION_PX)); lh = fm.height()
    top = c.y() - lh * len(lines) / 2 + (lh / 2 if name else 0)
    for i, line in enumerate(lines):
        t = scene.addSimpleText(line, _font(theme.CAPTION_PX)); t.setBrush(QBrush(col))
        t.setPos(c.x() - t.boundingRect().width() / 2, top + i * lh); items.append(t)
    if name:
        n = scene.addSimpleText(name, _font(theme.SMALL_PX, QFont.DemiBold)); n.setBrush(QBrush(col))
        n.setPos(c.x() - n.boundingRect().width() / 2, top - n.boundingRect().height()); items.append(n)
    return items


def is_generic(defn, params):
    """True when the part draws as the generic labelled box (no usable symbol file)."""
    f = defn.symbol_for(params)
    return not f or renderer(str(f), "#000000") is None


def add_symbol(scene, part, defn, color, name=None):
    """Add `part`'s symbol (rotated, coloured) to the scene -> items. Falls back to the generic body, which shows
    the part `name` (if given) above the definition name."""
    f = defn.symbol_for(part["params"])
    r = renderer(str(f), QColor(color).name()) if f else None
    if r is None:
        return add_generic_body(scene, part_transform(part), defn.box, defn.pins, defn.name, color, name)
    vb, ds = r.viewBoxF(), r.defaultSize()
    item = QGraphicsSvgItem(); item.setSharedRenderer(r)
    item.setTransform(QTransform().scale(vb.width() / ds.width(), vb.height() / ds.height())
                      * QTransform().translate(vb.x(), vb.y()) * part_transform(part))
    scene.addItem(item)
    return [item]


def add_placeholder(scene, part, color, name=None):
    """Dashed purple box with a big '?' for a part whose definition is missing or incompatible; its saved type and
    version sit at the bottom. `color` replaces the purple while hovered/selected."""
    box, pos = geometry(part)
    ph = QColor(theme.READINESS["placeholder"][1])
    col = ph if QColor(color) == QColor(theme.INK) else QColor(color)
    fill = QColor(theme.READINESS["placeholder"][2]); fill.setAlpha(140)
    items = add_generic_body(scene, part_transform(part), box, [Pin(p, p, at) for p, at in pos.items()], "", col,
                             fill=fill, dashed=True)
    t, c = part_transform(part), part_transform(part).map(QPointF(box[0] + box[2] / 2, box[1] + box[3] / 2))
    q = scene.addSimpleText("?", _font(32, QFont.Bold)); q.setBrush(QBrush(col))
    q.setPos(c.x() - q.boundingRect().width() / 2, c.y() - q.boundingRect().height() / 2 - 4)
    r = t.mapRect(QRectF(*box))
    k = scene.addSimpleText(f"{part['kind']}  v{part['version']}", _font(theme.CAPTION_PX)); k.setBrush(QBrush(col))
    k.setPos(r.left() + 4, r.bottom() - k.boundingRect().height() - 3)
    return items + [q, k]


class Badge(QGraphicsItem):
    """Readiness pill on the canvas (dot + text on a tinted rounded rectangle), with a tooltip. `key` is a
    theme.READINESS key; `at` is the pill's bottom-left corner in scene coordinates."""
    def __init__(self, key, at, tip):
        super().__init__()
        self.text, self.fg, self.bg, _ = theme.READINESS[key]
        self.font = _font(theme.CAPTION_PX, QFont.DemiBold)
        self.w = QFontMetricsF(self.font).horizontalAdvance(self.text) + 26
        self.setPos(at.x(), at.y() - 20); self.setToolTip(tip); self.key = key

    def boundingRect(self):
        return QRectF(0, 0, self.w, 20)

    def paint(self, p, *_):
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen); p.setBrush(QColor(self.bg)); p.drawRoundedRect(self.boundingRect(), theme.R_SM, theme.R_SM)
        p.setBrush(QColor(self.fg)); p.drawEllipse(QRectF(7, 7, 6, 6))
        p.setPen(QColor(self.fg)); p.setFont(self.font)
        p.drawText(QRectF(18, 0, self.w - 18, 20), Qt.AlignVCenter | Qt.AlignLeft, self.text)


def add_badge(scene, key, at, tip):
    """Add a readiness badge whose bottom-left corner is `at`; hovering it shows `tip`. -> the item."""
    b = Badge(key, at, tip); scene.addItem(b)
    return b


def icon_pixmap(defn, px=40, color="#1F2933"):
    """Library icon of a definition: its `icon` SVG, else its (first) rendered `symbol`, else None (the caller draws a
    letter glyph). Fitted into px x px keeping the aspect ratio, rendered at 2x for crispness."""
    sym = defn.symbol
    f = defn.icon or (next((x for x in sym["files"].values() if x), None) if isinstance(sym, dict) else sym)
    r = renderer(str(f), color) if f else None
    if r is None:
        return None
    vb, k = r.viewBoxF(), 2
    s = min(px / vb.width(), px / vb.height())
    w, h = vb.width() * s, vb.height() * s
    pm = QPixmap(px * k, px * k); pm.fill(Qt.transparent); pm.setDevicePixelRatio(k)
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF((px - w) / 2, (px - h) / 2, w, h))
    p.end()
    return pm
