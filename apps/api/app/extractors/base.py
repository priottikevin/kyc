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
        return self._locate(label, value_re, exclude, max_below)[0]

    def value_block(self, label: str, exclude: str | None = None, stop: str | None = None, max_lines: int = 4) -> str | None:
        """Multi-line value (addresses): first line as value_after, then left-aligned lines just below it."""
        first, ln = self._locate(label, None, exclude)
        if not first or ln is None:
            return first
        stop_re = re.compile(stop) if stop else None
        parts, cur = [first], ln
        for _ in range(max_lines - 1):
            nxt = [
                o for o in self.lines
                if o is not cur and o.cy > cur.cy + 0.5 * cur.h and o.y0 - cur.y1 < 0.8 * cur.h
                and abs(o.x0 - cur.x0) < 1.5 * cur.h
            ]
            if not nxt:
                break
            o = min(nxt, key=lambda o: o.y0)
            t = clean_value(o.text)
            if not t or (stop_re and stop_re.search(norm(t))):
                break
            parts.append(t)
            cur = o
        return ", ".join(parts)

    def address_blocks(self) -> list[list[str]]:
        """Postal blocks in reading order: the rows of a column above a "75011 PARIS" box, down to that box.

        Addresses are often printed without a label (recipient of a letter) or beside another column, so the
        column is what ties a name, its street and its postcode together.
        """
        blocks = []
        for last in self.lines:
            if not re.match(r"\d{5}\s+\S", norm(last.text)):
                continue
            rows, cur = [clean_value(last.text)], last
            for _ in range(3):
                above = [o for o in self.lines if o is not cur and o.cy < cur.cy - 0.5 * cur.h
                         and cur.y0 - o.y1 < 2.5 * cur.h and abs(o.x0 - last.x0) < 1.5 * last.h]
                if not above:
                    break
                cur = max(above, key=lambda o: o.y1)
                rows.insert(0, clean_value(cur.text))
            if len(rows) > 1:
                blocks.append(rows)
        return blocks

    def address_of(self, name: str) -> str | None:
        """Address printed under a name (regex on normalized text), "street, 75011 PARIS"."""
        for rows in self.address_blocks():
            for i, row in enumerate(rows[:-1]):
                if re.search(name, norm(row)):
                    return ", ".join(rows[i + 1:])
        return None

    def _locate(self, label: str, value_re: str | None = None, exclude: str | None = None,
                max_below: float = 2.6) -> tuple[str | None, Line | None]:
        """value_after + the box the value was read from (None when it shares the label's box)."""
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
                return r, None
            # 2) same row, to the right
            right = [o for o in self.lines if o is not ln and o.x0 >= ln.x1 - 5 and abs(o.cy - ln.cy) < 0.5 * max(ln.h, o.h)]
            for o in sorted(right, key=lambda o: o.x0)[:1]:
                r = ok(o.text)
                if r:
                    return r, o
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
                    return r, o
        return None, None
