"""Normalisation, dates and generic entity extraction (email, phone, IBAN, SIRET...)."""

import re
import unicodedata
from datetime import date

MONTHS = {
    "JAN": 1, "JANV": 1, "JANVIER": 1, "JANUARY": 1,
    "FEV": 2, "FEVR": 2, "FEVRIER": 2, "FEB": 2, "FEBRUARY": 2,
    "MAR": 3, "MARS": 3, "MARCH": 3,
    "AVR": 4, "AVRIL": 4, "APR": 4, "APRIL": 4,
    "MAI": 5, "MAY": 5,
    "JUIN": 6, "JUN": 6, "JUNE": 6,
    "JUIL": 7, "JUILLET": 7, "JUL": 7, "JULY": 7,
    "AOU": 8, "AOUT": 8, "AUG": 8, "AUGUST": 8,
    "SEP": 9, "SEPT": 9, "SEPTEMBRE": 9, "SEPTEMBER": 9,
    "OCT": 10, "OCTOBRE": 10, "OCTOBER": 10,
    "NOV": 11, "NOVEMBRE": 11, "NOVEMBER": 11,
    "DEC": 12, "DECEMBRE": 12, "DECEMBER": 12,
}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm(s: str) -> str:
    """Uppercase, no accents, unified apostrophes - for keyword matching."""
    s = strip_accents(s).upper()
    return s.replace("’", "'").replace("`", "'")


def clean_value(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip(" :;,.-_/|")
    return s


DATE_NUM = re.compile(r"\b(\d{1,2})\s*[./\- ]\s*(\d{1,2})\s*[./\- ]\s*(\d{4}|\d{2})\b")
DATE_TXT = re.compile(r"\b(\d{1,2})\s*([A-Za-zÀ-ÿ]{3,9})\.?\s*(\d{4})\b")
# OCR often drops separators on ID cards: "12091980", "28 072032"
DATE_LOOSE = re.compile(r"(?<!\d)(\d{2})\s?[./\-]?\s?(\d{2})\s?[./\-]?\s?(\d{4})(?!\d)")
# regex for a label-bound date value (used to skip label noise such as "7 Date Ofbith")
DATE_VALUE = r"(?<!\d)\d{1,2}\s?[./\- ]?\s?(?:\d{1,2}|[A-Za-zÀ-ÿ]{3,9}\.?)\s?[./\- ]?\s?(?:\d{4}|\d{2})(?!\d)"


def _mk(d: int, m: int, y: int) -> str | None:
    if y < 100:
        y += 2000 if y <= (date.today().year % 100) + 10 else 1900
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def parse_date(s: str) -> str | None:
    """Return ISO date from French/European formats, or None."""
    m = DATE_NUM.search(s)
    if m:
        r = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if r:
            return r
    m = DATE_TXT.search(s)
    if m:
        mon = MONTHS.get(norm(m.group(2)).rstrip("."))
        if mon:
            return _mk(int(m.group(1)), mon, int(m.group(3)))
    m = DATE_LOOSE.search(s)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def find_dates(s: str) -> list[str]:
    out = []
    for m in DATE_NUM.finditer(s):
        r = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if r:
            out.append(r)
    return out


EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
PHONE = re.compile(r"(?:(?:\+|00)\d{2,3}[\s.\-]?(?:\(0\)\s?)?\d(?:[\s.\-]?\d{2}){4}|\b0[1-9](?:[\s.\-]?\d{2}){4})\b")
URL = re.compile(r"\b(?:https?://)?(?:www\.)[\w\-]+(?:\.[\w\-]+)+(?:/[\w\-./?=&%#]*)?|\bhttps?://[\w\-]+(?:\.[\w\-]+)+(?:/[\w\-./?=&%#]*)?", re.I)
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,3})?\b")
SIRET = re.compile(r"\b\d{3}\s?\d{3}\s?\d{3}(?:\s?\d{5})?\b")
PLATE_SIV = re.compile(r"\b([A-HJ-NP-TV-Z]{2})[\s\-]?(\d{3})[\s\-]?([A-HJ-NP-TV-Z]{2})\b")
PLATE_FNI = re.compile(r"\b(\d{1,4})\s?([A-Z]{1,3})\s?(\d{2}|2A|2B|97\d)\b")
VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
POSTCODE_CITY = re.compile(r"\b(\d{5})\s+([A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ\-' ]{1,40})")


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def iban_ok(iban: str) -> bool:
    s = iban.replace(" ", "").upper()
    if not 15 <= len(s) <= 34:
        return False
    s = s[4:] + s[:4]
    num = "".join(str(int(c, 36)) for c in s)
    return int(num) % 97 == 1


def find_plate(text: str) -> str | None:
    m = PLATE_SIV.search(text.upper())
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def format_phone(p: str) -> str:
    return re.sub(r"[\s.\-]+", " ", p).strip()


def entities(text: str) -> dict:
    """Generic, validated entities found anywhere in the text."""
    up = text.upper()
    out: dict = {}
    emails = sorted({e.lower() for e in EMAIL.findall(text)})
    if emails:
        out["emails"] = emails
    phones = list(dict.fromkeys(format_phone(p) for p in PHONE.findall(text)))
    if phones:
        out["phones"] = phones
    urls = [u for u in dict.fromkeys(URL.findall(text)) if "@" not in u]
    if urls:
        out["websites"] = urls
    ibans = [i.replace(" ", "") for i in IBAN.findall(up) if iban_ok(i)]
    if ibans:
        out["ibans"] = list(dict.fromkeys(ibans))
    sir = []
    for m in SIRET.findall(text):
        d = m.replace(" ", "")
        if len(d) in (9, 14) and luhn_ok(d):
            sir.append(d)
    if sir:
        out["siren_siret"] = list(dict.fromkeys(sir))
    plates = list(dict.fromkeys(f"{a}-{b}-{c}" for a, b, c in PLATE_SIV.findall(up)))
    if plates:
        out["plates"] = plates
    vins = [v for v in dict.fromkeys(VIN.findall(up)) if re.search(r"\d", v) and re.search(r"[A-Z]", v)]
    if vins:
        out["vins"] = vins
    dates = list(dict.fromkeys(find_dates(text)))
    if dates:
        out["dates"] = dates
    return out
