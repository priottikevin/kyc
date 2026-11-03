import re
from dataclasses import dataclass, field
from functools import cached_property

from ..ocr import Line, group_rows
from ..textutils import clean_value, norm


@dataclass
class Extraction:
    document_type: str
    fields: dict = field(default_factory=dict)
    field_sources: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    llm_preferred: bool = False  # layout too variable for rules to be trusted alone

    def set(self, key: str, value, source: str = "ocr", overwrite: bool = False) -> None:
        if value in (None, "", []):
            return
        if key in self.fields and not overwrite:
            return
        self.fields[key] = value
        self.field_sources[key] = source

    @property
    def missing(self) -> list[str]:
        return [k for k in self.required if not self.fields.get(k)]

    @property
    def checks_ok(self) -> bool:
        return all(self.checks.values())

    @property
    def complete(self) -> bool:
        return not self.missing and self.checks_ok

    def rank(self) -> tuple:
        return (self.complete, self.checks_ok, -len(self.missing), len(self.fields))


class Ctx:
    """OCR lines + layout helpers shared by extractors."""

    def __init__(self, lines: list[Line]):
        self.lines = lines

    @cached_property
    def rows(self) -> list[list[Line]]:
        return group_rows(self.lines)

    @cached_property
    def row_texts(self) -> list[str]:
        return [" ".join(l.text for l in r) for r in self.rows]

    @cached_property
    def text(self) -> str:
        return "\n".join(self.row_texts)

    @cached_property
    def ntext(self) -> str:
        return norm(self.text)

    def value_after(self, label: str, value_re: str | None = None, exclude: str | None = None, max_below: float = 2.6) -> str | None:
        """Find the value associated with a label: same box after the label, same row to the right, or just below.

        label/exclude are regexes applied on accent-stripped uppercase text.
        """
        lab = re.compile(label)
        vre = re.compile(value_re) if value_re else None
        exc = re.compile(exclude) if exclude else None

        def ok(v: str) -> str | None:
            v = clean_value(v)
            if not v:
                return None
            if exc and exc.search(norm(v)):
                return None
            if vre:
                m = vre.search(v)
                return m.group(0) if m else None
            return v

        for ln in self.lines:
            n = norm(ln.text)
            m = lab.search(n)
            if not m:
                continue
            # 1) value in the same box, after the label
            end = m.end() + len(re.match(r"[A-Z.']*", n[m.end():]).group(0))  # finish the word
            rest = ln.text[end:] if len(n) == len(ln.text) else n[end:]
            if re.match(r"\s*/", rest):  # bilingual label "Nom / Surname": no value in this box
                rest = ""
            rest = re.sub(r"^\s*\(\d+\)", "", rest)  # passport field numbers "(1)"
            r = ok(rest)
            if r and len(rest.strip(" :")) > 1:
                return r
            # 2) same row, to the right
            right = [o for o in self.lines if o is not ln and o.x0 >= ln.x1 - 5 and abs(o.cy - ln.cy) < 0.5 * max(ln.h, o.h)]
            for o in sorted(right, key=lambda o: o.x0)[:1]:
                r = ok(o.text)
                if r:
                    return r
            # 3) below, horizontally overlapping, nearest first
            below = [
                o for o in self.lines
                # tilted photos: the value box can start above the label's center, compare centers instead
                if o is not ln and o.cy > ln.cy + 0.25 * ln.h and o.y0 > ln.y0 + 0.25 * ln.h
                and o.y0 - ln.y1 < max_below * ln.h
                and o.x0 < ln.x1 and o.x1 > ln.x0 - ln.h
            ]
            for o in sorted(below, key=lambda o: (o.y0, abs(o.x0 - ln.x0))):
                r = ok(o.text)
                if r:
                    return r
        return None
