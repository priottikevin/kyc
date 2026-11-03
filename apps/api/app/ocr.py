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
            self.get(s)(img)


def run_ocr(engine, img: np.ndarray) -> list[Line]:
    res = engine(img)
    if res.txts is None or res.boxes is None:
        return []
    out = []
    for box, txt, sc in zip(res.boxes, res.txts, res.scores):
        b = np.asarray(box)
        t = txt.strip()
        if t:
            out.append(Line(t, float(sc), float(b[:, 0].min()), float(b[:, 1].min()), float(b[:, 0].max()), float(b[:, 1].max())))
    return out


def ocr_page(engine, img: np.ndarray) -> list[Line]:
    """OCR with a cheap whole-page orientation check (photos taken sideways / upside down)."""
    import cv2

    lines = run_ocr(engine, img)
    vertical = sum(1 for l in lines if l.h > 1.5 * l.w and len(l.text) > 2)
    low = _score(lines)
    if lines and vertical <= len(lines) * 0.4 and np.mean([l.conf for l in lines]) > 0.8:
        return lines
    # Try alternatives only when the first pass looks wrong
    candidates = [(low, lines)]
    rots = [cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE] if vertical > len(lines) * 0.4 else [cv2.ROTATE_180]
    for r in rots:
        alt = run_ocr(engine, cv2.rotate(img, r))
        candidates.append((_score(alt), alt))
    return max(candidates, key=lambda c: c[0])[1]


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
