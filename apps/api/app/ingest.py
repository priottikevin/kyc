"""Turn an uploaded file into page images (or native text for digital PDFs)."""

from dataclasses import dataclass, field

import cv2
import pymupdf as fitz
import numpy as np


class UnsupportedFile(ValueError):
    pass


@dataclass
class Page:
    image: np.ndarray | None = None  # BGR
    native_lines: list[tuple[str, tuple[float, float, float, float]]] = field(default_factory=list)
    # the page as provided (image after EXIF orientation / PDF page), in the unit of `image` or of `native_lines`:
    # positions are returned relative to it, whatever was trimmed, scaled or rotated for the OCR
    width: float = 0.0
    height: float = 0.0
    offset: tuple[int, int] = (0, 0)  # top-left corner of `image` in that frame (blank margins trimmed)

    @property
    def is_native(self) -> bool:
        return bool(self.native_lines)


def sniff(data: bytes) -> str:
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:2] in (b"II", b"MM"):
        return "tiff"
    if data[:2] == b"BM":
        return "bmp"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftyphevc"):
        return "heic"
    raise UnsupportedFile("Format non supporté (PDF, JPEG, PNG, WEBP, TIFF, BMP acceptés)")


def downscale(img: np.ndarray, max_side: int) -> np.ndarray:
    h, w = img.shape[:2]
    m = max(h, w)
    if m <= max_side:
        return img
    s = max_side / m
    return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)


def trim_margins(img: np.ndarray, thresh: int = 225, pad_ratio: float = 0.02) -> np.ndarray:
    """Crop blank (near-white) margins, e.g. a card scanned in the corner of an A4 page.

    Less pixels for the detector = faster OCR. Photos with a non-white background are left untouched.
    """
    return trim_margins_at(img, thresh, pad_ratio)[0]


def trim_margins_at(img: np.ndarray, thresh: int = 225, pad_ratio: float = 0.02) -> tuple[np.ndarray, tuple[int, int]]:
    """trim_margins, plus the (x, y) of the kept area in the original image."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    ink = (gray < thresh).astype(np.uint8)
    h, w = ink.shape
    # ignore scanner specks / thin borders: keep rows/cols with a meaningful amount of ink
    rows = np.where(ink.sum(axis=1) > max(3, 0.01 * w))[0]
    cols = np.where(ink.sum(axis=0) > max(3, 0.01 * h))[0]
    if rows.size == 0 or cols.size == 0:
        return img, (0, 0)
    pad = int(pad_ratio * max(h, w))
    y0, y1 = max(0, rows[0] - pad), min(h, rows[-1] + 1 + pad)
    x0, x1 = max(0, cols[0] - pad), min(w, cols[-1] + 1 + pad)
    if (y1 - y0) * (x1 - x0) > 0.8 * h * w:
        return img, (0, 0)
    return img[y0:y1, x0:x1].copy(), (int(x0), int(y0))


def load(data: bytes, max_pages: int, max_side: int) -> tuple[str, list[Page]]:
    kind = sniff(data)
    if kind == "heic":
        raise UnsupportedFile("HEIC non supporté : exporter en JPEG")
    if kind == "pdf":
        return kind, _load_pdf(data, max_pages, max_side)
    # imdecode applies EXIF orientation for JPEG
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise UnsupportedFile("Image illisible")
    return kind, [_image_page(downscale(img, max_side))]


def _image_page(img: np.ndarray) -> Page:
    trimmed, offset = trim_margins_at(img)
    return Page(image=trimmed, width=img.shape[1], height=img.shape[0], offset=offset)


def _load_pdf(data: bytes, max_pages: int, max_side: int) -> list[Page]:
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        raise UnsupportedFile(f"PDF illisible : {e}") from e
    pages: list[Page] = []
    with doc:
        for page in list(doc)[:max_pages]:
            lines = _native_lines(page)
            if sum(len(t) for t, _ in lines) >= 80:
                pages.append(Page(native_lines=lines, width=page.rect.width, height=page.rect.height))
                continue
            # Scanned page: render so the long side is ~max_side px
            r = page.rect
            zoom = min(max_side / max(r.width, r.height), 4.0)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)
            img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
            pages.append(_image_page(cv2.cvtColor(img, cv2.COLOR_RGB2BGR)))
    if not pages:
        raise UnsupportedFile("PDF vide")
    return pages


def _native_lines(page) -> list[tuple[str, tuple[float, float, float, float]]]:
    out = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            # keep spans separate so label/value split across spans stays usable
            for span in line.get("spans", []):
                t = span.get("text", "").strip()
                if t:
                    out.append((t, tuple(span["bbox"])))
    return out
