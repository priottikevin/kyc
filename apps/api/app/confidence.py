"""Per-field confidence from the OCR's per-character recognition scores.

The score of a field is the lowest score among the characters it was read from: a single doubtful
character is enough to make the value doubtful, while a line average hides it ("9230O" averages 0.96).
Measured on the sample documents: lines whose characters all score >= 0.97 never disagreed between
the fast and the accurate model (0/379), below that 15-80% did. It is a "check this" signal, not a
probability: correct characters can score low too.
"""

import re

from .ocr import Line
from .textutils import strip_accents

THRESHOLD = 0.97
# checksum name -> fields it proves
CHECKED_FIELDS = {
    "siren_luhn": ("siren",),
    "siret_luhn": ("supplier_siret",),
    "iban": ("iban",),
    "totals_consistent": ("total_excl_tax", "total_vat", "total_incl_tax"),
}
VERIFIED_SOURCES = {"mrz", "mrz+ocr"}


def _alnum(s: str) -> str:
    return "".join(c for c in strip_accents(s).upper() if c.isalnum())


class CharIndex:
    """OCR lines flattened to alphanumeric characters with their scores, for substring lookups."""

    def __init__(self, lines: list[Line]):
        self.rows: list[tuple[str, list[float]]] = []
        self.mrz: list[bool] = [ln.text.count("<") >= 3 for ln in lines]
        for ln in lines:
            chars, confs = [], []
            scores = ln.chars if ln.chars and len(ln.chars) == len(ln.text) else [ln.conf] * len(ln.text)
            for ch, sc in zip(ln.text, scores):
                a = _alnum(ch)
                if a:
                    chars.append(a[0])
                    confs.append(sc)
            self.rows.append(("".join(chars), confs))

    def score(self, needle: str) -> tuple[float, int] | None:
        """(best score, line index) of `needle` (alphanumeric, uppercase) over its occurrences; None if not found."""
        if len(needle) < 1:
            return None
        best = None
        for i, (text, confs) in enumerate(self.rows):
            start = text.find(needle)
            while start != -1:
                s = min(confs[start:start + len(needle)])
                if best is None or s > best[0]:
                    best = (s, i)
                start = text.find(needle, start + 1)
        return best


def _candidates(v) -> list[str]:
    """Ways a normalised value may have been printed."""
    if isinstance(v, bool):
        return []
    if isinstance(v, float):
        return [_alnum(f"{v:.2f}"), _alnum(f"{v:.1f}"), _alnum(str(int(v)))] if v == int(v) else [_alnum(f"{v:.2f}")]
    if isinstance(v, int):
        return [str(v)]
    s = str(v)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        y, m, d = s.split("-")
        return [d + m + y, d + m + y[2:], y + m + d]
    if re.fullmatch(r"\d{4}-\d{2}", s):
        y, m = s.split("-")
        return [m + y, m + y[2:]]
    return [_alnum(s)]


def value_score(v, idx: CharIndex) -> tuple[float, set[int]] | None:
    """(score, indexes of the lines the value was read from), None when the value is not found as printed."""
    if isinstance(v, (list, tuple)):
        parts = [value_score(x, idx) for x in v]
    elif isinstance(v, dict):
        parts = [value_score(x, idx) for x in v.values()]
    elif isinstance(v, str) and ", " in v:  # multi-line value (address) joined by the extractor
        parts = [value_score(x, idx) for x in v.split(", ")]
    else:
        found = [s for c in _candidates(v) if c and (s := idx.score(c)) is not None]
        if not found:
            return None
        s, i = max(found)
        return s, {i}
    if not parts or any(p is None for p in parts):
        return None
    return min(p[0] for p in parts), set().union(*(p[1] for p in parts))


def field_confidence(fields: dict, sources: dict, checks: dict, lines: list[Line]) -> tuple[dict, dict]:
    """({field: {"score": 0..1 | None, "level": ...}}, {field: [Line, ...] the value was read from}).

    Levels: verified (checksum / MRZ), high (every character >= THRESHOLD), check (a doubtful character),
    unknown (value not found as printed: normalised or derived), llm.
    """
    proven = {f for name, fs in CHECKED_FIELDS.items() if checks.get(name) for f in fs}
    idx = CharIndex(lines)
    # a PDF text layer is exact: anything extracted from it is as printed
    native = bool(lines) and all(ln.chars is None and ln.conf == 1.0 for ln in lines)
    out, where = {}, {}
    for k, v in fields.items():
        src = sources.get(k, "")
        if src in VERIFIED_SOURCES or k in proven:
            out[k] = {"score": 1.0, "level": "verified"}
            continue
        if src == "llm":
            out[k] = {"score": None, "level": "llm"}
            continue
        found = value_score(v, idx)
        if found is None and native:
            out[k] = {"score": 1.0, "level": "high"}
            continue
        if found is None:
            s, level = None, "unknown"
        else:
            s, rows = found
            where[k] = [lines[i] for i in sorted(rows)]
            s = round(s, 3)
            level = "high" if s >= THRESHOLD else "check"
        out[k] = {"score": s, "level": level}
    return out, where


def uncertain(conf: dict) -> list[str]:
    return [k for k, c in conf.items() if c["level"] in ("check", "unknown")]

