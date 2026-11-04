"""Where each value is printed, in the frame of the page as provided (for highlighting it in a viewer).

The OCR works on a transformed page (scaled, blank margins trimmed, turned upright, pages stacked vertically):
boxes are mapped back to the page as the caller sent it, before any rotation, in thousandths of its size.
"""

from dataclasses import dataclass

from .confidence import CharIndex, _alnum, _candidates, value_score
from .ocr import Line


@dataclass
class PageGeo:
    page: int  # 1-based, in input order
    y_start: float  # vertical range of this page's lines in the stacked coordinates
    y_end: float
    rotation: int  # degrees clockwise applied before the OCR (0, 90, 180, 270)
    width: float  # image given to the OCR, before rotation (= frame size for a PDF text layer)
    height: float
    offset: tuple[int, int]  # top-left of that image in the page frame
    frame_w: float
    frame_h: float


def _value_span(ln: Line, value) -> tuple[float, float]:
    """Horizontal span of the value inside its OCR line (proportional to characters), else the whole line."""
    if isinstance(value, str) and ", " not in value:
        idx = [i for i, ch in enumerate(ln.text) if _alnum(ch)]
        flat = "".join(_alnum(ln.text[i])[0] for i in idx)
        for c in _candidates(value):
            start = flat.find(c) if c else -1
            if start >= 0 and len(c) < len(flat):
                a, b = idx[start], idx[start + len(c) - 1] + 1
                n = len(ln.text)
                return ln.x0 + ln.w * a / n, ln.x0 + ln.w * b / n
    return ln.x0, ln.x1


def _to_frame(g: PageGeo, x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
    """Box in the OCR image (rotated, page-local) -> box in the page frame."""
    w, h = g.width, g.height
    if g.rotation == 90:
        x0, y0, x1, y1 = y0, h - x1, y1, h - x0
    elif g.rotation == 270:
        x0, y0, x1, y1 = w - y1, x0, w - y0, x1
    elif g.rotation == 180:
        x0, y0, x1, y1 = w - x1, h - y1, w - x0, h - y0
    ox, oy = g.offset
    return x0 + ox, y0 + oy, x1 + ox, y1 + oy


def field_positions(fields: dict, lines: list[Line], geo: list[PageGeo]) -> dict:
    """{field: {page, x, y, width, height}} in thousandths of the page, origin top-left, for the fields
    found as printed in the OCR lines, whatever their source (rules, MRZ, LLM). A value spread over several
    lines gets the box around all of them (on the page of its first line)."""
    idx = CharIndex(lines)
    out = {}
    for k, v in fields.items():
        if isinstance(v, str):
            # multi-line value (address): box the parts found, a part may be printed differently
            rows = sorted({i for part in v.split(", ") for i in (_find(part, idx) or [])})
        else:
            found = value_score(v, idx)
            rows = sorted(found[1]) if found else None
        if not rows:
            continue
        box = _box([lines[i] for i in rows], v, geo)
        if box:
            out[k] = box
    return out


MIN_CHARS = 3  # shorter values (sex, a category code...) are found at too many places to be located


def _find(value: str, idx: CharIndex) -> list[int] | None:
    """Line of a single-line value: outside the MRZ when printed there too (the visual zone is what a reader
    looks at), then the best read occurrence."""
    best = None
    for c in _candidates(value):
        if len(c) < MIN_CHARS:
            continue
        for i, (text, confs) in enumerate(idx.rows):
            start = text.find(c)
            while start != -1:
                key = (not idx.mrz[i], min(confs[start:start + len(c)]))
                if best is None or key > best[0]:
                    best = (key, i)
                start = text.find(c, start + 1)
    return [best[1]] if best else None


def _box(lines: list[Line], value, geo: list[PageGeo]) -> dict | None:
    g = next((p for p in geo if p.y_start <= lines[0].cy < p.y_end), None)
    if g is None or g.frame_w <= 0 or g.frame_h <= 0:
        return None
    boxes = []
    for ln in lines:
        if not g.y_start <= ln.cy < g.y_end:
            continue
        x0, x1 = _value_span(ln, value) if len(lines) == 1 else (ln.x0, ln.x1)
        boxes.append(_to_frame(g, x0, ln.y0 - g.y_start, x1, ln.y1 - g.y_start))
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)

    def t(v: float, size: float) -> int:
        return max(0, min(1000, round(v * 1000 / size)))

    px, py = t(x0, g.frame_w), t(y0, g.frame_h)
    return {"page": g.page, "x": px, "y": py, "width": t(x1, g.frame_w) - px, "height": t(y1, g.frame_h) - py}
