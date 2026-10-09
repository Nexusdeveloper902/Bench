"""Part symbols as SVG items. A symbol file's user units are part-local coordinates (the same as pin `at` and `box`);
`currentColor` is replaced by the state colour before loading. A part without a usable symbol draws as a generic
labelled box. Qt only; all geometry comes from the catalog Definition."""
from functools import cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, QRectF
from PySide6.QtGui import QBrush, QColor, QFont, QPainterPath, QPen, QTransform
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtSvgWidgets import QGraphicsSvgItem

from .catalog import CATALOG


def part_transform(d):
    """Local (unrotated catalog coords) -> scene. Rotation is clockwise about the body centre, as in components."""
    x0, y0, w, h = CATALOG.get(d["kind"]).box
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


def add_generic_body(scene, transform, box, pins, title, color):
    """Rounded box with a stub and the name at each pin, and the title centred. -> items."""
    col, items = QColor(color), []
    x0, y0, w, h = box
    body = QPainterPath(); body.addRoundedRect(QRectF(x0, y0, w, h), 6, 6)
    items.append(scene.addPath(transform.map(body), QPen(col, 2), QBrush(QColor(255, 255, 255))))
    c = transform.map(QPointF(x0 + w / 2, y0 + h / 2))
    font = QFont(); font.setPointSize(7)
    for p in pins:
        q = transform.map(QPointF(*p.at))
        s = 1 if q.x() < c.x() else -1                       # label on the side facing the body centre
        t = scene.addSimpleText(p.name, font); t.setBrush(QBrush(col))
        t.setPos(q.x() + 5 if s > 0 else q.x() - 5 - t.boundingRect().width(), q.y() - t.boundingRect().height() / 2)
        items += [scene.addLine(q.x(), q.y(), q.x() + 4 * s, q.y(), QPen(col, 2)), t]
    t = scene.addSimpleText(title); t.setBrush(QBrush(col))
    t.setPos(c.x() - t.boundingRect().width() / 2, c.y() - t.boundingRect().height() / 2)
    return items + [t]


def add_symbol(scene, part, defn, color):
    """Add `part`'s symbol (rotated, coloured) to the scene -> items. Falls back to the generic body."""
    f = defn.symbol_for(part["params"])
    r = renderer(str(f), QColor(color).name()) if f else None
    if r is None:
        return add_generic_body(scene, part_transform(part), defn.box, defn.pins, defn.name, color)
    vb, ds = r.viewBoxF(), r.defaultSize()
    item = QGraphicsSvgItem(); item.setSharedRenderer(r)
    item.setTransform(QTransform().scale(vb.width() / ds.width(), vb.height() / ds.height())
                      * QTransform().translate(vb.x(), vb.y()) * part_transform(part))
    scene.addItem(item)
    return [item]
