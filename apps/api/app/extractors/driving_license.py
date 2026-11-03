"""EU driving licence (format "carte" 2013+, and the numbered fields of older ones)."""

import re

from ..textutils import clean_value, parse_date
from .base import Ctx, Extraction

# EU field numbers at the start of a row (OCR may read "1." as "1," or "1 ")
FIELD = re.compile(r"^\s*(1|2|3|4\s?[aAbBcCdD]|5|7|8|9)\s*(?:[.,:]\s*|\s+)(.+)$")
SPLIT = re.compile(r"\s+(?=(?:4\s?[a-dA-D]|[1-9])[.,]\s)")


def extract(ctx: Ctx, doc_type: str) -> Extraction:
    ex = Extraction("driving_license", required=["surname", "given_names", "birth_date", "license_number"])
    found: dict[str, str] = {}
    for row in ctx.row_texts:
        # a row may hold several "n. value" pairs side by side ("4a. 03.10.2016 4c. PREFECTURE")
        for seg in SPLIT.split(row):
            m = FIELD.match(seg)
            if m:
                key = m.group(1).replace(" ", "").lower()
                found.setdefault(key, clean_value(m.group(2)))

    ex.set("surname", found.get("1"))
    ex.set("given_names", found.get("2"))
    if "3" in found:
        f3 = found["3"]
        ex.set("birth_date", parse_date(f3))
        place = re.sub(r"^\s*\d{1,2}[./\- ]\d{1,2}[./\- ]\d{2,4}\s*", "", f3).strip(" ,")
        ex.set("birth_place", place or None)
    if "4a" in found:
        ex.set("issue_date", parse_date(found["4a"]))
    if "4b" in found:
        ex.set("expiry_date", parse_date(found["4b"]))
    ex.set("issuing_authority", found.get("4c"))
    if "5" in found:
        num = re.sub(r"\s", "", found["5"])
        m = re.search(r"[A-Z0-9]{6,15}", num.upper())
        ex.set("license_number", m.group(0) if m else num)
    ex.set("address", found.get("8"))
    if "9" in found:
        cats = re.findall(r"\b(AM|A1|A2|A|B1|BE|B|C1E|C1|CE|C|D1E|D1|DE|D)\b", found["9"].upper())
        ex.set("categories", list(dict.fromkeys(cats)) or found["9"])
    return ex
