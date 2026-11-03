"""Local OCR (RapidOCR / PP-OCRv6 on onnxruntime) + layout helpers."""

import logging
import threading
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Line:
    text: str
    conf: float
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


class OcrEngines:
    """Lazily-built RapidOCR engines keyed by model size ('tiny', 'small', 'medium')."""

    def __init__(self, threads: int = 0):
        self._engines: dict[str, object] = {}
        self._lock = threading.Lock()
        self._threads = threads

    def get(self, size: str):
        eng = self._engines.get(size)
        if eng is not None:
            return eng
        with self._lock:
            if size not in self._engines:
                self._engines[size] = self._build(size)
            return self._engines[size]

    def _build(self, size: str):
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import ModelType, OCRVersion

        mt = {"tiny": ModelType.TINY, "small": ModelType.SMALL, "medium": ModelType.MEDIUM}[size]
        det_mt = ModelType.TINY if size == "tiny" else ModelType.SMALL
        params = {
            "Global.log_level": "error",
            # per-line 180° classifier flips digits like 999 -> 666; page orientation is handled in ocr_page
            "Global.use_cls": False,
            "Det.ocr_version": OCRVersion.PPOCRV6,
            "Det.model_type": det_mt,
            "Det.lang_type": "fr",
            "Rec.ocr_version": OCRVersion.PPOCRV6,
            "Rec.model_type": mt,
            "Rec.lang_type": "fr",
        }
        if self._threads:
            params["EngineConfig.onnxruntime.intra_op_num_threads"] = self._threads
        log.info("Loading OCR engine %s", size)
        return RapidOCR(params=params)

    def warmup(self, sizes: list[str]) -> None:
        img = np.full((120, 400, 3), 255, np.uint8)
        import cv2

        cv2.putText(img, "WARMUP 123", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
        for s in sizes:
            self.get(s)(img, use_det=True, use_cls=False, use_rec=True)
            self.get(s)(img, use_det=False, use_cls=False, use_rec=True)  # targeted re-reads path


def run_ocr(engine, img: np.ndarray) -> list[Line]:
    res = engine(img, use_det=True, use_cls=False, use_rec=True)
    if res.txts is None or res.boxes is None:
        return []
    out = []
    for box, txt, sc in zip(res.boxes, res.txts, res.scores):
        b = np.asarray(box)
        t = txt.strip()
        if t:
            out.append(Line(t, float(sc), float(b[:, 0].min()), float(b[:, 1].min()), float(b[:, 0].max()), float(b[:, 1].max())))
    return out


def ocr_page(engine, img: np.ndarray) -> tuple[list[Line], np.ndarray]:
    """OCR with a cheap whole-page orientation check (photos taken sideways / upside down).

    Returns the lines and the image they refer to (rotated if needed).
    """
    import cv2

    lines = run_ocr(engine, img)
    vertical = sum(1 for l in lines if l.h > 1.5 * l.w and len(l.text) > 2)
    low = _score(lines)
    if lines and vertical <= len(lines) * 0.4 and np.mean([l.conf for l in lines]) > 0.8:
        return lines, img
    # Try alternatives only when the first pass looks wrong
    candidates = [(low, lines, img)]
    rots = [cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE] if vertical > len(lines) * 0.4 else [cv2.ROTATE_180]
    for r in rots:
        rimg = cv2.rotate(img, r)
        alt = run_ocr(engine, rimg)
        candidates.append((_score(alt), alt, rimg))
    best = max(candidates, key=lambda c: c[0])
    return best[1], best[2]


def reread_line(engine, img: np.ndarray, ln: Line, y_off: float = 0.0, others: list[Line] | None = None) -> Line | None:
    """Recognition only (no detection, ~20 ms) on one line's crop with another model.

    Tightly spaced lines (addresses) overlap their neighbours' boxes, so the crop stops halfway to the
    centre of the lines just above / below.
    """
    pad = 0.25 * ln.h
    top, bottom = ln.y0 - pad, ln.y1 + pad
    for o in others or []:
        if o is ln or o.x1 < ln.x0 or o.x0 > ln.x1:
            continue
        if o.cy < ln.cy and abs(o.cy - ln.cy) > 0.3 * ln.h:
            top = max(top, (o.cy + ln.cy) / 2)
        elif o.cy > ln.cy and abs(o.cy - ln.cy) > 0.3 * ln.h:
            bottom = min(bottom, (o.cy + ln.cy) / 2)
    h, w = img.shape[:2]
    y0, y1 = max(0, int(top - y_off)), min(h, int(bottom - y_off) + 1)
    x0, x1 = max(0, int(ln.x0 - pad)), min(w, int(ln.x1 + pad))
    if y1 - y0 < 4 or x1 - x0 < 4:
        return None
    res = engine(img[y0:y1, x0:x1], use_det=False, use_cls=False, use_rec=True)
    if not res.txts or not res.txts[0].strip():
        return None
    return Line(res.txts[0].strip(), float(res.scores[0]), ln.x0, ln.y0, ln.x1, ln.y1)


def _score(lines: list[Line]) -> float:
    return sum(l.conf * len(l.text) for l in lines if l.w >= l.h)


def native_to_lines(native: list[tuple[str, tuple[float, float, float, float]]]) -> list[Line]:
    return [Line(t, 1.0, *bbox) for t, bbox in native]


# ---------------------------------------------------------------- layout helpers


def group_rows(lines: list[Line]) -> list[list[Line]]:
    """Group boxes whose vertical centers overlap into rows, sorted top->bottom, left->right."""
    rows: list[list[Line]] = []
    for ln in sorted(lines, key=lambda l: l.cy):
        for row in rows:
            ref = row[0]
            tol = 0.5 * min(ref.h, ln.h)
            if abs(ref.cy - ln.cy) <= tol:
                row.append(ln)
                break
        else:
            rows.append([ln])
    for row in rows:
        row.sort(key=lambda l: l.x0)
    rows.sort(key=lambda r: min(l.y0 for l in r))
    return rows


def full_text(lines: list[Line]) -> str:
    return "\n".join(" ".join(l.text for l in row) for row in group_rows(lines))
